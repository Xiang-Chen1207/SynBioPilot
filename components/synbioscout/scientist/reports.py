"""Portable reports based on actual records, never fabricated benchmark scores."""
from __future__ import annotations

import json

from .models import ResearchRun
from .tool_public import (
    public_failure_records,
    public_tool_warning,
)


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def source_scope_label(source):
    from .source_scope import source_scope_view
    scope = source_scope_view(source)
    names = {"abstract": "摘要", "full_text": "全文", "database": "数据库记录",
             "local_document": "本地记录", "unknown": "范围未记录材料"}
    if scope["local_excerpt"]:
        title = "原" + names[scope["original_scope"]] + "的已存连续摘录"
        window = scope["original_window"]
        if window:
            title += f" · 原文字符窗口 [{window['start']}, {window['end']})"
        else:
            title += " · 原文窗口未核实"
        title += " · " + {"reused": "复用材料 · 非本轮全文读取",
                           "newly_acquired": "本轮新获取摘录 · 未认定阅读全文",
                           "unknown": "获取方式未记录 · 非本轮全文读取证明"}[scope["acquisition"]]
        return title
    title = "仅摘要" if scope["current_scope"] == "abstract" else names[scope["current_scope"]]
    if scope["fresh_fulltext"]:
        return title + " · 本轮新获取全文"
    if scope["acquisition"] == "reused":
        return title + " · 复用已存材料"
    if scope["acquisition"] == "newly_acquired":
        return title + (" · 本轮获取，完整全文范围待核对" if scope["current_scope"] == "full_text" else " · 本轮新获取")
    return title + (" · 本轮新全文读取未确认" if scope["current_scope"] == "full_text" else "")


def render_report(run: ResearchRun):
    from .candidate_assessments import (
        assessment_cards,
        public_evaluation_records,
        public_evaluations,
    )
    evaluations = public_evaluations(run)
    evaluation_records = public_evaluation_records(run, evaluations)
    if run.co_scientist:
        from .presentation import presented_run
        run = presented_run(run)
    from .intent_policy import task_intent_receipt
    from .presentation import ROLE_LABELS
    intent = task_intent_receipt(run.brief)
    lines = [f"# SynBioScout 研究报告：{run.brief.disease}", "",
             f"项目 `{run.project_id}` / 研究 `{run.run_id}` / 状态 `{run.status.value}`", "",
             f"任务意图：{intent['declared_intent']}；有效上下文模式：{intent['effective_intent']}；来源：{intent['intent_basis']}。", "",
             "候选来源与任务意图分别记录；旧记录使用兼容的有背景任务模式。", "",
             f"研究表型：{run.brief.phenotype or '待明确'}", "",
             f"研究人群：{run.brief.population or '待明确'}；递送语境：{run.brief.delivery_context or '待明确'}。", "",
             f"研究目标：{run.brief.research_goal or '探索性分子与靶点发现'}。", "",
             f"实验菌株：{run.brief.chassis_name}；参考菌株：{run.brief.reference_chassis}。", "",
             "本报告记录文献研究与计算假说；模型匹配、底盘支持和实际功能分别验证。", "",
             "## 研究计划", ""]
    lines += [f"- {s.question}：`{s.status}` {s.reason}" for s in run.steps]
    expansion = run.usage.get("hypothesis_expansion_status")
    if isinstance(expansion, str) and expansion in {"started", "not_run"}:
        lines += ["", "假说扩展执行状态：" + ("未执行（候选核心评估与可选假说链分开记录）" if expansion == "not_run" else "已启动（最终状态仍以实际版本审查与合同为准）"), ""]
    lines += ["", "## 靶点与结构包", "",
              "| 蛋白 | UniProt | 期望方向 | 结构状态 | 未解决问题 |",
              "| --- | --- | --- | --- | --- |"]
    for target in run.targets:
        lines.append("| " + " | ".join(cell(v) for v in [target.gene_symbol, target.uniprot_id,
            target.desired_direction, target.structure_status, "; ".join(target.uncertainties)]) + " |")
    for package in run.target_packages:
        lines += ["", f"靶点包 `{package.package_id}`：`{package.status}`；" +
                  cell(package.checks)]
    lines += ["", "## 双路线候选", "",
              "| 候选 | 角色 | 来源 | 身份 | 决策 | 理由 |",
              "| --- | --- | --- | --- | --- | --- |"]
    for candidate in run.candidates:
        lines.append("| " + " | ".join(cell(v) for v in [candidate.name,
            ROLE_LABELS[candidate.role] + " / " + candidate.role_status,
            ", ".join(candidate.routes), candidate.identity_status, candidate.decision,
            "; ".join(candidate.decision_reasons)]) + " |")
    for candidate in run.candidates:
        evidence_label = ("草稿假说：其引用未通过审查，不作为药效依据。" if not candidate.evidence_ids else
                          "关联证据：" + ", ".join(candidate.evidence_ids) + "；仍需领域复核。")
        lines += ["", f"### {candidate.name}", "",
                  "角色：" + ROLE_LABELS[candidate.role] + "；判定状态：" + candidate.role_status,
                  "", "角色依据：" + (candidate.role_reason or "尚未独立审查"),
                  "", evidence_label, "", "原候选草稿依据（非独立结论审查）：" + candidate.rationale,
                  "", f"结构：`{candidate.smiles or '待核对'}`；InChIKey：`{candidate.inchikey or '未知'}`",
                  "", "与靶点关系：" + cell(candidate.target_relations),
                  "", "底盘评估：" + cell(candidate.gate or "尚未执行 ChassisGate"),
                  "", "ADMET：" + cell(candidate.admet or "尚未执行"),
                  "", "未知项：" + "; ".join(candidate.uncertainties)]
    cards = assessment_cards(run, evaluations)
    specified = [row for row in cards if row["origin"] == "user_supplied"]
    if specified:
        lines += ["", "## 输入候选评估", "", "候选结论包含评估状态、证据来源和适用条件；hold/needs_evidence 状态保留在对应候选中。", ""]
        for row in specified:
            lines += ["### " + cell(row["name"]), "",
                "人群/目标终点：" + cell(row["population_requested"]) + " / " + cell(row["endpoint_requested"]), "",
                "身份/科学状态：" + row["identity_status"] + " / " + row["scientific_status"], "",
                "独立候选结论审查：" + ("已完成" if row["assessment_status"] == "completed_review" else "待审查（当前有效收据待生成）"), "",
                "关联依据：" + ", ".join(row["evidence_ids"]), "",
                "反证或混合结果：" + ", ".join(row["opposing_evidence_ids"]), "",
                "未知项：" + "; ".join(row["unknowns"]), "",
                "下一步问题：" + "; ".join(row["next_questions"]), ""]
            for scope in row["evidence_scope"]:
                lines.append("- " + scope["evidence_id"] + "：" + scope["study_system"] + " / " + scope["condition"] + " / " + scope["endpoint"])
    if evaluations:
        lines += ["", "## 独立审查的候选结论", "",
            "以下结论按记录的人群、实验体系、条件和终点呈现。假说扩展状态见执行记录。", ""]
        for row in evaluations:
            ref = f"assessment:{row['assessment_id']}:v{row['version']}"
            lines += ["### " + cell(ref), "",
                "候选/实体/身份版本：" + cell(row["candidate_id"]) + " / " + cell(row["entity_id"]) + " / " + str(row["identity_revision"]), "",
                "评估状态/科学状态：completed_review / " + row["scientific_status"], "",
                "独立审查：assessment_review:" + cell(row["review_id"]), "",
                "实际独立审查动作：" + cell(row.get("review_operation_id", "未记录")), "",
                "限定结论：" + cell(row.get("conclusion", "[非公开字段已省略]")), "",
                "任务指纹：" + row["task_fingerprint"], "", "结论指纹：" + row["conclusion_fingerprint"], "",
                "证据指纹：" + row["evidence_fingerprint"], "",
                "支持/背景引用：" + ", ".join(row["supporting_evidence_ids"]), "",
                "反对/混合引用：" + ", ".join(row["opposing_evidence_ids"]), ""]
            for finding in row["findings"]:
                scope = finding["scope"]
                lines += ["- " + cell(finding.get("finding_id", "")) + "：" + cell(finding.get("statement", "")) +
                    "；范围 " + " / ".join(cell(scope.get(k, "")) for k in ("study_system", "condition", "endpoint")) +
                    "；适用层级 " + cell(finding.get("applicability", "未记录")) +
                    "；引用 " + ", ".join(finding["evidence_ids"]) +
                    "；范围限制 " + "; ".join(finding["scope_limitations"])]
            lines += ["", "仍未知：" + "; ".join(row["unknowns"]), "",
                "下一步问题：" + "; ".join(row["next_questions"]), ""]
    if evaluation_records:
        lines += ["## 独立候选评估审查记录", "", "以下记录分别显示草稿与结论审查状态，并按当前实体、版本、任务与证据核验。", "",
            "```json", json.dumps(evaluation_records, ensure_ascii=False, indent=2, allow_nan=False), "```", ""]
    observations = [row for row in cards if row.get("observation_support")]
    if observations:
        lines += ["", "## 已审窄来源观察", "",
            "支持范围为所引原文观察及其条件；整体假说结论和临床适用性分别评估，混合/反对证据保留原极性。", ""]
        for row in observations:
            lines += ["### " + cell(row["name"]), "", "候选科学状态：" + row["scientific_status"], ""]
            for item in row["observation_support"]:
                lines.append("- " + cell(item["claim_id"]) + " / v" + str(item["version"]) +
                    " / 引文 " + cell(item["evidence_id"]) + " / 原证据极性 " + cell(item["polarity"]) +
                    " / 窄观察关系 " + cell(item["claim_relation"]) + " / 范围 " + cell(item["assessment_scope"]))
    from .evidence_digest import build_evidence_digest
    evidence_digest = build_evidence_digest(run)
    if evidence_digest["publications"] or evidence_digest["reference_coverage"]:
        lines += ["", "## 证据分层与参考覆盖", "", "按规范出版物标识聚合同一论文；每条引文保留原极性、研究系统、条件和终点。", ""]
        for publication in evidence_digest["publications"]:
            lines.append("- " + publication["publication_id"] + " / 来源类型：" + publication["source_design"] + " / " + ", ".join(publication["identifiers"]))
            for item in publication["accepted_citations"]:
                lines.append("  - " + item["evidence_id"] + " / " + item["polarity"] + " / " + item["study_system"] + " / " + item["condition"] + " / " + item["endpoint"])
        for reference in evidence_digest["reference_coverage"]:
            lines.append("- 参考来源 " + reference["source_id"] + " / " + reference["selection_status"] + " / " + reference["access_status"] + " / " + reference["citation_status"] + " / 已接受引文：" + ", ".join(reference["accepted_evidence_ids"]))
        lines += ["", "引用状态按已读取和已审查的范围记录；整篇来源的覆盖情况另见来源记录。", ""]
    lookup = {s.source_id: s for s in run.sources}
    lines += ["", "## 原文证据", "",
              "原文片段校验对应具体来源；自动判读按所列研究体系、条件和终点供领域复核。", ""]
    for record in run.evidence:
        source = lookup.get(record.source_id)
        label = f"[{cell(source.title)}]({source.url})" if source else record.source_id
        lines += [f"### {record.evidence_id}", "", record.claim,
                  "", f"方向 `{record.polarity}` / 体系：{record.study_system} / 终点：{record.endpoint}",
                  "", f"来源：{label}；范围：{source_scope_label(source)}；位置：{record.location}",
                  "", "> " + record.quote.replace("\n", "\n> "),
                  "", "限制：" + "; ".join(record.limitations)]
    lines += ["", "## 下一步问题", ""] + ["- " + q for q in run.questions]
    lines += ["", "## 执行记录与待解决项", ""]
    lines += ["- " + public_tool_warning(msg) for msg in dict.fromkeys(run.warnings + run.errors)]
    lines += ["", "用量：`" + cell(run.usage) + "`", "",
              "各计算与实验模块的结果按实际执行状态记录。", ""]
    if run.co_scientist:
        receipts = public_failure_records(run.co_scientist)
        if receipts:
            lines += ["## 执行失败与可选跳过收据", "",
                "输入/输出额度以 UTF-8 字节计量；提供方计费另行记录。同一动作的相同镜像合并显示，缺少动作 ID 的历史关联及未知字段保留未记录状态。", ""]
            for record in receipts:
                title = "模型调用失败收据" if record.get("provider_failure") else (
                    "可选阶段跳过" if "optional_skip" in record["record_types"] else "预算或停止记录")
                lines += ["### " + title + " · " + cell(record.get("stage", "阶段未记录")), ""]
                lines += ["```json", json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False), "```", ""]
    if run.co_scientist:
        from .coscientist import render_contracts
        state = run.co_scientist
        lines += ["## Co-Scientist 假说与版本", "",
                  "Proximity 使用规则分组；Generation 采用两个分离上下文，协作记录展示已保存的操作与产物。", ""]
        for h in state.hypotheses:
            lines += [f"### {cell(h.title)} / {h.hypothesis_id} v{h.version}", "", h.claim, "",
                      f"状态：{h.status}；来源类型：{h.origin}；菌株：{h.chassis_name}。", "",
                      "条件/终点：" + h.condition + " / " + h.endpoint, "",
                      "支持：" + ", ".join(h.evidence_ids), "", "反证：" + ", ".join(h.opposing_evidence_ids),
                      "", "底盘：" + h.chassis.status + "; " + "; ".join(h.chassis.gaps),
                      "", "预测：" + h.prediction, "", "验证问题：" + h.validation_question,
                      "", "版本变化：" + "; ".join(h.changes), ""]
        lines += ["## 成对评审", ""]
        for c in state.comparisons:
            lines += [f"- {c.left_id} v{c.left_version} / {c.right_id} v{c.right_version}: {c.outcome}；{c.reason}"]
        lines += ["", render_contracts(run)]
        from .research_public import render_research_flow
        lines += [render_research_flow(run.co_scientist.research_flow)]
    return "\n".join(lines)

