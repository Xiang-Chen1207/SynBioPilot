"""Derived candidate assessment cards; exact entities and exact review versions only.

No file/network access and no mutation of historical records. These summaries keep
specified candidates visible even when chemical identity or scientific support is
unresolved. They never turn workflow completion into a scientific conclusion.
"""
from __future__ import annotations

import re
import unicodedata
from types import SimpleNamespace

from .identity import declared_aliases, declared_candidate_names
from .source_scope import source_scope_view
from .tool_public import _fields, _strings

EVALUATION_FIELDS = {"schema_version", "assessment_id", "version", "candidate_id", "entity_id",
    "identity_revision", "identity_fingerprint", "task_fingerprint", "conclusion_fingerprint", "evidence_fingerprint",
    "status", "review_id", "review_operation_id", "conclusion", "scientific_status"}
EVALUATION_LISTS = ("supporting_evidence_ids", "opposing_evidence_ids", "unknowns", "next_questions")


def public_evaluations(run):
    """Revalidate original records before projecting a bounded public conclusion."""
    from .assessment_review import public_validated_evaluations
    rows = []
    for value in public_validated_evaluations(run)[:100]:
        row = _fields(value, EVALUATION_FIELDS, 1200)
        required = {"assessment_id", "version", "candidate_id", "entity_id", "identity_revision", "review_id", "review_operation_id",
                    "conclusion", "scientific_status", "identity_fingerprint", "task_fingerprint",
                    "conclusion_fingerprint", "evidence_fingerprint"}
        if not required.issubset(row) or not row["conclusion"]:
            continue
        for key in EVALUATION_LISTS:
            row[key] = _strings(value.get(key), 1200, 100)
        row["findings"] = [{**_fields(f, {"finding_id", "statement", "applicability"}, 1200),
            "evidence_ids": _strings(f.get("evidence_ids"), 300, 100),
            "scope_limitations": _strings(f.get("scope_limitations"), 1200, 100),
            "scope": _fields(f.get("scope"), {"study_system", "condition", "endpoint"}, 2000)}
            for f in value.get("findings", [])[:100] if isinstance(f, dict)]
        rows.append(row)
    return rows


def public_evaluation_records(run, evaluations=None):
    """Audit links retain failed/missing review status without publishing drafts."""
    evaluations = public_evaluations(run) if evaluations is None else evaluations
    accepted = {(r["assessment_id"], r["version"]): r for r in evaluations}
    rows = []
    for value in getattr(run, "candidate_evaluations", [])[:100]:
        value = value.model_dump() if hasattr(value, "model_dump") else value
        if not isinstance(value, dict):
            continue
        row = _fields(value, EVALUATION_FIELDS - {"review_id", "conclusion", "scientific_status"}, 1200)
        final = accepted.get((row.get("assessment_id"), row.get("version")))
        row["recorded_status"] = row.get("status", "unknown")
        row["status"] = "completed_review" if final else "unreviewed"
        row["final"] = final
        row["validation_errors"] = _strings(value.get("validation_errors"), 1200, 100)
        row["reviews"] = []
        for review in value.get("reviews", [])[:100]:
            if not isinstance(review, dict):
                continue
            projected = _fields(review, EVALUATION_FIELDS - {"status", "conclusion", "scientific_status"}
                                | {"verdict", "reason"}, 1200)
            for key in ("supporting_evidence_ids", "opposing_evidence_ids"):
                projected[key] = _strings(review.get(key), 300, 100)
            projected["item_reviews"] = [{**_fields(item, {"finding_id", "verdict", "reason", "applicability"}, 1200),
                "evidence_ids": _strings(item.get("evidence_ids"), 300, 100)}
                for item in review.get("item_reviews", [])[:100] if isinstance(item, dict)]
            row["reviews"].append(projected)
        rows.append(row)
    return rows


def exact_name(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def candidate_names(candidate):
    return list(dict.fromkeys([candidate.name, *candidate.aliases, *declared_aliases(candidate.name),
                              *declared_candidate_names(candidate)]))


def matching_candidates(candidates, names):
    """Resolve each explicitly declared name independently; abstain on collisions."""
    result = []
    for name in names:
        matches = [c for c in candidates if exact_name(name) in {exact_name(n) for n in candidate_names(c)}]
        if len(matches) == 1 and all(matches[0] is not old for old in result):
            result.append(matches[0])
    return result


def names_in_quote(candidate, quote):
    text = exact_name(quote)
    return any(re.search(r"(?<![\w-])" + re.escape(exact_name(name)) + r"(?![\w-])", text)
               for name in candidate_names(candidate) if name.strip())


def candidate_version_supported(h, associations, evidence_lookup, checker):
    """A supported general claim cannot supply a named entity's missing support.

    Associations have already passed exact entity, version and source/span checks.
    Recheck applicability on the candidate-bound supporting record by itself: a
    different generic reference in the same review must not lend it applicability.
    """
    if not checker or not checker.main_reviewed(h):
        return False
    claims = {c.claim_id: c for c in checker.claims_for(h)}
    for association in associations:
        if ((association["hypothesis_id"], association["version"]) != (h.hypothesis_id, h.version)
                or association["verdict"] != "supported" or association["polarity"] != "supports"):
            continue
        claim = claims.get(association["claim_id"])
        evidence = evidence_lookup.get(association["evidence_id"])
        if (claim and evidence and evidence.polarity == "supports"
                and checker.scope_supports(h, claim, [evidence])):
            return True
    return False


def scoped_gap_view(candidate, cards, contracts, checker):
    """Label old gap text by its recorded scope, without declaring it resolved."""
    claims = [c for h in cards for c in checker.claims_for(h)] if checker else []
    claim_rows = [{"claim_id":c.claim_id,"hypothesis_id":c.hypothesis_id,"version":c.version,
        "kind":c.kind,"state":c.state,"assessment_scope":c.assessment_scope,
        "relation_schema_version":c.relation_schema_version, "evidence_ids":list(c.evidence_ids),"gaps":list(c.gaps)} for c in claims]
    notes = []
    records = [("candidate_record", "", None, gap) for gap in candidate.uncertainties]
    records += [("hypothesis_record", h.hypothesis_id, h.version, gap) for h in cards for gap in h.inference_gaps]
    records += [("contract_record", c.hypothesis_id, c.version, gap) for c in contracts for gap in c.remaining_unknowns]
    for origin, hid, version, text in records:
        scoped = [c for c in claims if text in c.gaps and (not hid or (c.hypothesis_id,c.version) == (hid,version))]
        notes.append({"origin":origin,"hypothesis_id":hid,"version":version,"text":text,
            "claim_ids":[c.claim_id for c in scoped],
            "scope_status":"linked_current_claim_gap" if scoped else "recorded_scope_unconfirmed"})
    unknowns = []
    for claim in claims:
        for text in claim.gaps:
            state_note = ("原文观察之外仍待验证" if claim.state == "supported" and claim.assessment_scope == "source_observation"
                else "原缺口文字未重裁定" if claim.state == "supported" else "当前缺口")
            unknowns.append(f"[{claim.hypothesis_id} v{claim.version} / {claim.kind} / {claim.state} / {state_note}] {text}")
    for note in notes:
        if not note["claim_ids"]:
            origin = (note["hypothesis_id"] + " v" + str(note["version"])) if note["hypothesis_id"] else "候选原记录"
            unknowns.append(f"[{origin} / 原记录未定范围，未重新裁定] {note['text']}")
    if checker:
        for h in cards:
            gap = checker.main_review_gap(h)
            if gap:
                unknowns.append(f"[{h.hypothesis_id} v{h.version} / main] {gap}")
    return {"unknowns":list(dict.fromkeys(unknowns)),"scoped_claim_gaps":claim_rows,"recorded_gap_notes":notes}


def assessment_cards(run, evaluations=None):
    """Public, explicit fields; raw provider metadata and full source text stay out."""
    candidates = run.candidates
    evaluations = public_evaluations(run) if evaluations is None else evaluations
    expansion = run.usage.get("hypothesis_expansion_status")
    expansion = expansion if isinstance(expansion, str) and expansion in {"started", "not_run"} else "not_recorded"
    specified = matching_candidates(candidates, run.brief.existing_candidates)
    co = run.co_scientist
    checker = None
    if co and co.research_flow:
        from .research_flow import ResearchFlow
        checker = object.__new__(ResearchFlow)
        checker.engine, checker.data = SimpleNamespace(run=run, state=co), co.research_flow
    latest = {}
    for h in co.hypotheses if co else ():
        if h.hypothesis_id not in latest or h.version > latest[h.hypothesis_id].version:
            latest[h.hypothesis_id] = h
    source_lookup = {s.source_id: s for s in run.sources}
    evidence_lookup = {e.evidence_id: e for e in run.evidence if e.validated_span}
    rows = []
    for candidate in candidates:
        valid = [r for r in evaluations if r.get("candidate_id") == candidate.candidate_id
                 and r.get("entity_id") == candidate.entity_id and r.get("identity_revision") == candidate.identity_revision]
        evaluation = max(valid, key=lambda r: r["version"]) if valid else None
        cards = [h for h in latest.values() if any(c is candidate for c in matching_candidates(candidates, h.molecule_names))]
        associations = [a for h in cards for a in checker.reviewed_evidence_ids(h)["associations"]] if checker else []
        # General disease or mechanism citations remain with their claim. Only
        # explicit candidate intervention/main evidence is attached to this entity.
        associations = [a for a in associations if a["claim_kind"] in {"main", "intervention"}
                        and names_in_quote(candidate, evidence_lookup[a["evidence_id"]].quote)]
        evaluation_ids = [*evaluation["supporting_evidence_ids"], *evaluation["opposing_evidence_ids"]] if evaluation else []
        ids = list(dict.fromkeys([*candidate.evidence_ids, *(a["evidence_id"] for a in associations), *evaluation_ids]))
        records = []
        for eid in ids:
            e = evidence_lookup.get(eid)
            source = source_lookup.get(e.source_id) if e else None
            if not e or not source:
                continue
            records.append({"evidence_id": eid, "source_id": e.source_id, "polarity": e.polarity,
                "study_system": e.study_system, "condition": e.condition, "endpoint": e.endpoint,
                "source_scope": source_scope_view(source)["current_scope"], "title": source.title, "url": source.url,
                "limitations": list(e.limitations),
                "association": "independent_candidate_review" if eid in evaluation_ids else
                               "same_version_claim_review" if any(a["evidence_id"] == eid for a in associations)
                               else "recorded_candidate_citation"})
        reviews = [r for r in co.reviews if any((r.hypothesis_id, r.version) == (h.hypothesis_id, h.version)
                   for h in cards)] if co else []
        contracts = [c for c in co.contracts if any((c.hypothesis_id, c.version) == (h.hypothesis_id, h.version)
                     for h in cards)] if co else []
        gap_view = scoped_gap_view(candidate, cards, contracts, checker)
        gaps = gap_view["unknowns"]
        if candidate.identity_status != "resolved":
            gaps.append("化学身份待核对：需要唯一化学形式与结构记录")
        rows.append({"candidate_id": candidate.candidate_id, "entity_id": candidate.entity_id or candidate.candidate_id,
            "name": candidate.name, "origin": "user_supplied" if any(c is candidate for c in specified) else "research_record",
            "identity_status": candidate.identity_status, "identity_revision": candidate.identity_revision,
            "decision": candidate.decision, "population_requested": run.brief.population or "未指定",
            "endpoint_requested": run.brief.phenotype or "未指定", "delivery_context": run.brief.delivery_context or "未指定",
            "workflow_status": run.status.value, "scientific_status": "needs_evidence" if not cards or not checker
                or any(not candidate_version_supported(h, associations, evidence_lookup, checker) for h in cards)
                else "source_claim_supported_pending_validation",
            "evidence_ids": [r["evidence_id"] for r in records if r["polarity"] != "opposes"],
            "opposing_evidence_ids": [r["evidence_id"] for r in records if r["polarity"] in {"opposes", "mixed"}],
            "evidence_scope": records, "review_associations": associations,
            "observation_support": [a for a in associations if a.get("relation_basis") == "exact_source_report_only"
                and a.get("claim_relation") == "supports" and a["verdict"] == "supported"],
            "hypothesis_versions": [{"hypothesis_id": h.hypothesis_id, "version": h.version,
                "review_state": checker.main_review_state(h) if checker else "unreviewed"} for h in cards],
            "unknowns": list(dict.fromkeys(gaps)),
            "scoped_claim_gaps":gap_view["scoped_claim_gaps"], "recorded_gap_notes":gap_view["recorded_gap_notes"],
            "next_questions": list(dict.fromkeys([*(h.validation_question for h in cards),
                *(q for r in reviews for q in r.required_evidence), *(c.minimal_validation for c in contracts)])),
            "status_note": "候选结论包含评估状态、证据来源和适用条件。"})
        row = rows[-1]
        row["assessment_status"] = "completed_review" if evaluation else "unreviewed"
        row["hypothesis_expansion_status"] = expansion
        row["validated_evaluation"] = evaluation
        if evaluation:
            row["scientific_status"] = evaluation["scientific_status"]
            row["unknowns"] = list(dict.fromkeys([*evaluation["unknowns"], *row["unknowns"]]))
            row["next_questions"] = list(dict.fromkeys([*evaluation["next_questions"], *row["next_questions"]]))
    # Never fabricate an entity if generation did not return the specified name.
    for name in run.brief.existing_candidates:
        if not matching_candidates(candidates, [name]):
            rows.append({"candidate_id": "", "entity_id": "", "name": name, "origin": "user_supplied",
                "identity_status": "unresolved", "identity_revision": 0, "decision": "hold",
                "population_requested": run.brief.population or "未指定", "endpoint_requested": run.brief.phenotype or "未指定",
                "delivery_context": run.brief.delivery_context or "未指定", "workflow_status": run.status.value,
                "scientific_status": "needs_evidence", "assessment_status": "unreviewed", "validated_evaluation": None,
                "hypothesis_expansion_status": expansion,
                "evidence_ids": [], "opposing_evidence_ids": [],
                "evidence_scope": [], "review_associations": [], "observation_support": [], "hypothesis_versions": [],
                "unknowns": ["候选名称的唯一化学身份关联待确定"],
                "next_questions": ["核对评估输入的唯一化学身份并形成可追溯记录"],
                "status_note": "候选结论包含评估状态、证据来源和适用条件。"})
    return rows

