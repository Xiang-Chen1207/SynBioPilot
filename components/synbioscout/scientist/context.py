"""Compact decision inputs; the complete audit trail remains in local receipts."""
from __future__ import annotations

import re

from .intent_policy import is_disease_exploration, task_intent_receipt, validate_intent_brief
from .source_scope import source_scope_view


def accepted_evidence_index(evidence, *, linked_evidence_ids=(), claim_applicability=()):
    """Keep global acceptance, current-version linkage and explicit claim review separate.

    Caller supplies the run's accepted evidence collection and rows for only the
    current hypothesis versions. Study scope is copied verbatim, never promoted
    from an animal/observational result to human intervention applicability.
    Large quotations and claim prose belong in the selected evidence windows.
    """
    linked = {eid for eid in linked_evidence_ids if isinstance(eid, str)}
    result = []
    for item in evidence:
        row = item.model_dump() if hasattr(item, "model_dump") else item
        if not isinstance(row, dict) or row.get("validated_span") is not True:
            continue
        if not isinstance(row.get("evidence_id"), str) or not isinstance(row.get("source_id"), str):
            continue
        entry = {key: row[key] if isinstance(row.get(key), str) else "unknown" for key in (
            "evidence_id", "source_id", "study_system", "condition", "endpoint", "polarity")}
        entry.update(global_status="accepted", version_linkage="linked" if row["evidence_id"] in linked else "not_linked",
                     claim_applicability="not_reviewed_for_current_claim")
        reviews = []
        for association in claim_applicability if row["evidence_id"] in linked else ():
            if (not isinstance(association, dict) or association.get("evidence_id") != row["evidence_id"]
                    or association.get("source_id", row["source_id"]) != row["source_id"]
                    or not all(isinstance(association.get(k), str) and association[k] for k in
                               ("claim_id", "hypothesis_id", "review_id"))
                    or type(association.get("version")) is not int or association["version"] < 1
                    or not isinstance(association.get("verdict"), str)
                    or association["verdict"] not in {"supported", "contested", "context_only", "unknown"}):
                continue
            review = {key: association[key] for key in ("claim_id", "hypothesis_id", "version", "review_id", "verdict")}
            for key, allowed in (("claim_relation", {"supports", "opposes", "context", "unassessed"}),
                                 ("relation_basis", {"exact_source_report_only", "legacy_global_polarity"}),
                                 ("assessment_scope", {"inference", "source_observation"})):
                if isinstance(association.get(key), str) and association[key] in allowed:
                    review[key] = association[key]
            if type(association.get("relation_schema_version")) is int and association["relation_schema_version"] in {1, 2}:
                review["relation_schema_version"] = association["relation_schema_version"]
            if review not in reviews:
                reviews.append(review)
        if reviews:
            entry.update(claim_applicability="reviewed_for_explicit_claims", claim_reviews=reviews)
        result.append(entry)
    return result


def background_windows(text):
    """Quoted task constraints, not a generated scientific summary.

    Long user documents remain local. Preserve explicit constraint/assay lines
    with adjacent context, and signal omitted material rather than infer it.
    This selector never runs on literature evidence or accepted quotations.
    """
    rule = re.compile(r"不|未|未知|限制|仅|需|必须|条件|菌株|终点|递送|暴露|禁止|避免|"
                      r"\b(?:cannot|unknown|must|only|strain|endpoint|condition|delivery|exposure|"
                      r"exclude|avoid|without|no|not|qPCR)\b", re.IGNORECASE)
    lines = text.splitlines(keepends=True)
    starts, offset = [], 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    spans = [(0, min(240, len(text))), (max(0, len(text) - 240), len(text))]
    for i, line in enumerate(lines):
        if rule.search(line):
            spans.append((starts[max(0, i - 1)],
                          starts[i + 2] if i + 2 < len(lines) else len(text)))
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return [{"start": a, "end": b, "text": text[a:b]} for a, b in merged]


def task_context(brief, *, include_documents=False):
    validate_intent_brief(brief)
    if is_disease_exploration(brief):
        fields = {"disease", "population", "phenotype", "species", "research_goal", "success_criterion"}
        return {**brief.model_dump(include=fields), "task_intent": task_intent_receipt(brief),
                "budget": brief.budget.model_dump(include={"max_targets", "literature_max_candidates", "max_hypotheses"}),
                "assumptions": [], "background_documents": []}
    fields = ("disease", "phenotype", "population", "delivery_context", "research_goal",
              "species", "chassis_name", "conditions", "success_criterion",
              "existing_candidates", "allowed_precursors", "engineering_allowed",
              "experimental_capabilities", "target_hints")
    result = {**brief.model_dump(include=set(fields)), "task_intent": task_intent_receipt(brief), "budget": brief.budget.model_dump(
        include={"max_targets", "literature_max_candidates", "max_hypotheses"}),
        "assumptions": [], "background_documents": []}
    for index, value in enumerate(brief.assumptions):
        # Audit hashes/pointers do not help the model decide a scientific claim.
        if re.fullmatch(r"[\w.-]*(?:sha256|hash)\s*=\s*[a-fA-F0-9]{32,128}", value.strip()):
            continue
        if include_documents or len(value.encode()) <= 1500:
            result["assumptions"].append(value)
        else:
            result["background_documents"].append({"reference": f"brief.assumptions[{index}]",
                "scope": "unverified_user_supplied_background_not_accepted_evidence",
                "original_characters": len(value), "windows": background_windows(value),
                "coverage": "constraint_and_boundary_windows_only; omitted material remains unknown"})
    return result


def source_context(source, evidence=(), excerpt_chars=1800, *, include_prefix=False):
    """Keep continuous original windows, never concatenate text into a quotation."""
    scope = source_scope_view(source)
    if source.evidence_scope == "database" and source.provider == "pubchem" and not any(
        item.source_id == source.source_id for item in evidence
    ):
        # Current entity fields already contain the verified selected structure
        # and aliases. A large synonym JSON is an identity audit artifact, not
        # clinical evidence or useful repeated scientific model context.
        return {"source_id": source.source_id, "title": source.title, "provider": source.provider,
                "url": source.url, "retrieved_at": source.retrieved_at,
                "scope": "database_identity_metadata", "identity_only": True,
                "source_scope": scope, "acquisition_origin": scope["acquisition_origin"],
                "windows": [], "text_truncated": bool(source.text)}
    spans = []
    if include_prefix:
        spans.append((0, min(len(source.text), excerpt_chars)))
    for item in evidence:
        if item.source_id == source.source_id and item.quote:
            start = source.text.find(item.quote)
            if start >= 0:
                spans.append((max(0, start - 180), min(len(source.text), start + len(item.quote) + 180)))
    if not spans:
        spans = [(0, min(len(source.text), excerpt_chars))]
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    windows = [{"start": a, "end": b, "text": source.text[a:b]} for a, b in merged]
    if scope["original_window"]:
        base = scope["original_window"]["start"]
        for window in windows:
            window.update(original_start=base + window["start"], original_end=base + window["end"])
    return {"source_id": source.source_id, "title": source.title, "provider": source.provider,
            "url": source.url, "pmid": source.pmid, "doi": source.doi,
            "scope": scope["current_scope"], "source_scope": scope,
            "original_scope": scope["original_scope"],
            "acquisition_origin": scope["acquisition_origin"],
            "original_excerpt_start": scope["original_window"]["start"] if scope["original_window"] else None,
            "year": source.metadata.get("year", ""),
            "publication_types": source.metadata.get("publication_types", {}),
            "windows": windows,
            "text_truncated": sum(b-a for a,b in merged) < len(source.text)}

