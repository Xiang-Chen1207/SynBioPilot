"""Derived product views over persisted research; never rewrite historical findings."""
from __future__ import annotations

from collections import Counter

from .evidence import classify_legacy_role, decide_candidates
from .models import ResearchRun, SourceRecord
from .research_public import (
    public_literature_source,
    public_research_flow,
    public_source_view,
    research_observation_counts,
)
from .tool_public import (
    public_budget,
    public_budget_failure,
    public_candidate_identity,
    public_entity_reference,
    public_reuse_seed,
    public_tool_event,
    public_tool_plan,
    public_tool_result,
    public_tool_source,
    public_tool_task,
    public_tool_warning,
)

ROLE_LABELS = {
    "therapeutic_candidate": "药效研究候选",
    "symptom_management": "症状管理候选",
    "biomarker": "标志物线索",
    "risk_factor": "风险相关分子",
    "metabolic_context": "代谢通路线索",
    "compound_class": "分子类别",
    "unknown": "角色待确认",
}
THERAPEUTIC_ROLES = {"therapeutic_candidate", "symptom_management"}


def presented_run(run: ResearchRun) -> ResearchRun:
    """Annotate old records on a deep copy, keeping source/prediction provenance intact."""
    view = run.model_copy(deep=True)
    view.warnings = [public_tool_warning(w) for w in view.warnings]
    view.sources = [SourceRecord.model_validate(public_source_view(s)) for s in view.sources]
    accepted = {e.evidence_id for e in view.evidence if e.validated_span}
    normalized = []
    for candidate in view.candidates:
        if candidate.role_status == "unclassified":
            candidate = classify_legacy_role(candidate, view.evidence, view.sources)
        elif candidate.role_status == "model_reviewed" and (
            not candidate.role_evidence_ids or
            not set(candidate.role_evidence_ids).issubset(accepted.intersection(candidate.evidence_ids))
        ):
            candidate.role = "unknown"
            candidate.role_status = "needs_review"
            candidate.role_reason = "角色审查未关联到该分子的有效证据；不作为治疗分类。"
        normalized.append(candidate)
    view.candidates = normalized
    decide_candidates(view.candidates, view.evidence)
    if view.co_scientist:
        co = view.co_scientist
        checker = None
        if co.research_flow:
            from types import SimpleNamespace

            from .research_flow import ResearchFlow
            checker = object.__new__(ResearchFlow)
            checker.engine, checker.data = SimpleNamespace(run=view, state=co), co.research_flow
            if not checker.verify()["valid"]:
                co.research_flow.limitations = list(dict.fromkeys(co.research_flow.limitations + [
                    "研究记录一致性或范围校验未通过；公开支持状态已保守降级，原记录未修改。"
                ]))
                for claim in co.research_flow.claims:
                    if claim.state == "supported":
                        claim.state = "unknown"
                for review in co.research_flow.claim_reviews:
                    if review.verdict == "supported":
                        review.verdict = "unknown"
        for h in co.hypotheses:
            reviews = [r for r in co.reviews if (r.hypothesis_id, r.version) == (h.hypothesis_id, h.version)]
            reviewed_scope = checker.reviewed_evidence_ids(h) if checker else {"evidence_ids": []}
            effective_refs = set(h.evidence_ids + reviewed_scope["evidence_ids"])
            # The formal receipt can cite a verified negative source observation
            # without treating it as clinical support. The main gate below still
            # downgrades observation-only cards to needs_evidence.
            effective_refs.update(a["evidence_id"] for a in reviewed_scope.get("associations", [])
                if a.get("verdict") == "supported" and a.get("relation_schema_version") == 2
                and a.get("assessment_scope") == "source_observation"
                and a.get("claim_relation") == "supports" and a.get("relation_basis") == "exact_source_report_only")
            if h.status == "reviewed" and (len(reviews) != 1 or reviews[0].verdict != "retain"
                or not accepted.intersection(effective_refs).intersection(reviews[0].valid_evidence_ids)):
                h.status = "proposed" if effective_refs else "needs_evidence"
                h.inference_gaps = list(dict.fromkeys(h.inference_gaps + ["没有对应版本的有效独立上下文审查，生成器自填状态不作为已审查依据"]))
            if co.research_flow and h.status == "reviewed" and not checker.main_reviewed(h):
                h.status = "needs_evidence"
                h.inference_gaps.append(checker.main_review_gap(h) or "当前版本逐主张适用性仍需补证")
        for contract in co.contracts:
            hs = [h for h in co.hypotheses if (h.hypothesis_id, h.version) == (contract.hypothesis_id, contract.version)]
            if co.research_flow and (not hs or hs[0].status != "reviewed"):
                contract.status = "needs_evidence"
    return view


def result_document(run: ResearchRun) -> dict:
    from .candidate_assessments import (
        assessment_cards,
        public_evaluation_records,
        public_evaluations,
    )
    evaluations = public_evaluations(run)
    view = presented_run(run)
    result = view.model_dump(mode="json")
    # Validate original content before source/identity presentation sanitization.
    # Persisted drafts and arbitrary model fields are never final public findings.
    result["candidate_evaluations"] = evaluations
    result["candidate_evaluation_records"] = public_evaluation_records(run, evaluations)
    from .intent_policy import task_intent_receipt
    result["task_intent_receipt"] = task_intent_receipt(view.brief)
    from .tool_public import public_failure_records
    result["failure_records"] = public_failure_records(view.co_scientist.model_dump()) if view.co_scientist else []
    for candidate in result["candidates"]:
        candidate.pop("identity_assessment_fingerprint", None)
        candidate.update(public_candidate_identity(candidate))
    if view.co_scientist:
        co = result["co_scientist"]
        co["seed"] = public_reuse_seed(co.get("seed"))
        co["research_flow"] = public_research_flow(co.get("research_flow"))
        tool_source_ids = {s.get("source_id") for t in co["tool_calls"]
                           for s in (t.get("sources") if isinstance(t.get("sources"), list) else [])
                           if isinstance(s, dict)}
        result["sources"] = [public_tool_source(s) if s["source_id"] in tool_source_ids else
                             public_literature_source(s) if s["evidence_scope"] in {"abstract", "full_text"} else s
                             for s in result["sources"]]
        co["tool_calls"] = [public_tool_result(t) for t in co["tool_calls"]]
        co["tool_plans"] = [public_tool_plan(p) for p in co["tool_plans"]]
        co["events"] = [public_tool_event(e) for e in co["events"]]
        co["budget"] = public_budget(co.get("budget"))
        if "entity_references" in co:
            co["entity_references"] = [public_entity_reference(row) for row in co["entity_references"][:100] if isinstance(row, dict)]
        for field in ("budget_failures", "optional_skips"):
            if field in co:
                co[field] = [public_budget_failure(row) for row in co[field][:100] if isinstance(row, dict)]
        for agent in co["agents"]:
            agent["task"] = public_tool_task(agent["task"])
    notes = ["研究线索、计算预测与实测结果分别记录，供研究规划使用。",
             "原文与语义审查对应所列来源、研究体系、条件和终点；实验验证状态单独记录。"]
    if not view.brief.phenotype:
        notes.append("尚未指定目标表型；目前检索覆盖疾病总体，需进一步明确研究终点。")
    if any(c.role_status == "legacy_inferred" for c in view.candidates):
        notes.append("历史角色标签来源为原记录的明确文本线索；当前审查状态为待复核。")
    if any(not t.related_cells for t in view.targets):
        notes.append("部分靶点的靶细胞信息待补充。")
    if any(c.role in THERAPEUTIC_ROLES and c.identity_status != "resolved" for c in view.candidates):
        notes.append("部分干预线索的单一化学实体待解析；小分子筛选入口使用已解析的单一结构。")
    result["summary"] = {
        "target_count": len(view.targets), "candidate_count": len(view.candidates),
        "evidence_count": len(view.evidence), "source_count": len(view.sources),
        "paper_count": sum(s.evidence_scope in ("abstract", "full_text") for s in view.sources),
        "therapeutic_candidate_count": sum(c.role in THERAPEUTIC_ROLES for c in view.candidates),
        "reviewed_therapeutic_count": sum(c.role in THERAPEUTIC_ROLES and
                                          c.role_status == "model_reviewed" for c in view.candidates),
        "resolved_therapeutic_count": sum(c.role in THERAPEUTIC_ROLES and
                                          c.identity_status == "resolved" for c in view.candidates),
        "other_molecule_count": sum(c.role not in THERAPEUTIC_ROLES for c in view.candidates),
        "ready_package_count": sum(p.status == "ready" for p in view.target_packages),
        "role_counts": dict(Counter(c.role for c in view.candidates)),
        "decision_counts": dict(Counter(c.decision for c in view.candidates)),
        "scope_notes": notes,
    }
    if view.co_scientist and view.co_scientist.research_flow:
        result["summary"].update(research_observation_counts(result))
        result["summary"]["scope_notes"].append("论文分组保留跨数据库和版本的原快照；独立研究按研究标识去重，证据强度按设计、条件与审查结果评价。")
        result["summary"]["scope_notes"].append("接受引文、正式假说版本审查、工具观察及入库来源分别计数；查询计划包含复用与未执行项。")
    from .evidence_digest import build_evidence_digest
    result["evidence_digest"] = build_evidence_digest(view)
    result["candidate_assessments"] = assessment_cards(view, evaluations)
    result["summary"]["independent_assessment_count"] = len(evaluations)
    for key, allowed in (("candidate_assessment_status", {"completed_review", "needs_review"}),
                         ("hypothesis_expansion_status", {"started", "not_run"})):
        value = run.usage.get(key)
        result["summary"][key] = value if isinstance(value, str) and value in allowed else "not_recorded"
    if result["summary"]["candidate_assessment_status"] == "completed_review":
        specified = [row for row in result["candidate_assessments"] if row["origin"] == "user_supplied"]
        if not specified or any(row["assessment_status"] != "completed_review" for row in specified):
            result["summary"]["candidate_assessment_status"] = "needs_review"
    result["summary"]["specified_candidate_count"] = sum(row["origin"] == "user_supplied" for row in result["candidate_assessments"])
    result["summary"]["scope_notes"].append("独立候选评估完成与科学支持程度分别记录；缺少当前有效审查收据时标为未审，不从角色、引文或草稿补造结论。")
    result["presentation"] = {"version": "0.3", "derived_view": True,
                              "original_record_unchanged": True, "role_labels": ROLE_LABELS}
    return result

