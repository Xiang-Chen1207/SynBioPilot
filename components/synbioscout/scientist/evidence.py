"""Exact-span evidence validation and conservative, versioned candidate decisions."""
from __future__ import annotations

import re

from .models import CandidateRecord, EvidenceRecord, SourceRecord
from .store import digest

THERAPEUTIC_ROLES = frozenset({"therapeutic_candidate", "symptom_management"})
CONTEXT_ROLES = frozenset({"biomarker", "risk_factor", "metabolic_context", "compound_class"})

# These are explicit role descriptions, not drug/disease name lookups. A historical
# classification is only a display hint; it does not constitute reviewed intervention evidence.
_ROLE_PATTERNS = {
    "compound_class": (
        r"\b(?:class|family|group) of (?:compounds|molecules|metabolites)\b|"
        r"\bcompound (?:class|family)\b|分子(?:类别|类群)|一类(?:化合物|分子|代谢物)|"
        r"非单一(?:化合物|分子)|不是单一(?:化合物|分子)"
    ),
    "risk_factor": (
        r"\brisk (?:factor|associated metabolite|associations?)\b|\b(?:increased|higher|elevated) "
        r"(?:disease |clinical )?risk\b|风险因素|危险因素|风险相关(?:分子|代谢物)|"
        r"增加.{0,8}风险|升高.{0,8}风险|(?:候选|潜在)?风险关联"
    ),
    "biomarker": (
        r"\b(?:biomarker|diagnostic marker|prognostic marker)s?\b|"
        r"生物标志物|生物标记物|诊断标志物|预后标志物|预测标志物"
    ),
    "metabolic_context": (
        r"\b(?:metabolic|metabolite|metabolomic|microbiome|microbial) "
        r"(?:association|correlation|context|signature|profile)\b|"
        r"\bmetabolite.{0,30}(?:associated|correlated)\b|"
        r"代谢(?:关联|背景|特征)|代谢物.{0,20}(?:相关|关联)|相关.{0,8}代谢物"
    ),
    "symptom_management": (
        r"\bsymptom(?:atic)? (?:management|treatment|relief)\b|"
        r"\b(?:manage|reduce|treat|improve) (?:associated )?symptoms?\b|"
        r"症状管理|症状终点|对症(?:治疗|干预)|改善.{0,10}相关症状|"
        r"(?:targets?|针对).{0,12}(?:comorbid |associated )?symptoms?\b|"
        r"(?:仅|主要).{0,6}(?:改善|缓解).{0,12}症状"
    ),
    "therapeutic_candidate": (
        r"\btherapeutic candidate\b|\bcandidate (?:therapy|treatment|intervention)\b|"
        r"\b(?:treatment|therapeutic|intervention) (?:trial|benefit|effect)\b|"
        r"\b(?:randomized|randomised|controlled) .{0,20}(?:trial|intervention)\b|"
        r"治疗候选|候选(?:药物|疗法|干预)|干预(?:研究|线索)|治疗研究|"
        r"疗效.{0,8}(?:报道|报告)|"
        r"随机.{0,12}(?:试验|研究)|(?:治疗|干预).{0,15}(?:改善|有效|获益|疗效)"
    ),
}


def _described_roles(text):
    text = normalized(text).casefold()
    roles = {role for role, pattern in _ROLE_PATTERNS.items() if re.search(pattern, text)}
    # A trial description cannot turn a sentence explicitly denying therapeutic identity
    # into a therapy classification. Such text may still support a non-therapeutic role.
    if re.search(r"\bnot (?:a |an )?(?:therapeutic candidate|treatment|therapy)\b|"
                 r"(?:不是|非|不能视为|不能作为).{0,8}(?:治疗候选|治疗药物|治疗分子|候选药物)", text):
        roles.discard("therapeutic_candidate")
    return roles


def classify_legacy_role(candidate: CandidateRecord, evidence: list[EvidenceRecord],
                         sources: list[SourceRecord] | None = None) -> CandidateRecord:
    """Annotate an immutable display copy; never validate or persist a historical role.

    Both the candidate rationale and its attached accepted claim/quotation must explicitly
    describe the same role. Full source documents are not scanned for unrelated role words.
    Ambiguous and missing descriptions stay unknown; names alone are never sufficient.
    """
    result = candidate.model_copy(deep=True)
    if candidate.role != "unknown" or candidate.role_status == "model_reviewed":
        return result
    lookup = {e.evidence_id: e for e in evidence if e.validated_span and len(normalized(e.quote)) >= 15}
    source_lookup = {s.source_id: s for s in sources} if sources is not None else None
    records = [lookup[eid] for eid in candidate.evidence_ids if eid in lookup]
    if source_lookup is not None:
        records = [e for e in records if e.source_id in source_lookup and
                   normalized(e.quote) in normalized(source_lookup[e.source_id].text)]
    described = _described_roles(candidate.rationale)
    related = {}
    for record in records:
        for role in _described_roles(record.claim + " " + record.quote):
            related.setdefault(role, []).append(record.evidence_id)
    common = described.intersection(related)
    # Explicit risk/marker descriptions narrow the otherwise generic metabolic context.
    if common in ({"risk_factor", "metabolic_context"}, {"biomarker", "metabolic_context"}):
        common.discard("metabolic_context")
    # Symptom management is a narrower description than intervention generally. However,
    # any simultaneous non-therapeutic description requires a new explicit role review.
    if common == {"symptom_management", "therapeutic_candidate"}:
        common = {"symptom_management"}
    if len(common) != 1:
        if described or related:
            result.role_status = "needs_review"
            result.role_reason = "历史记录的角色描述不足或存在歧义；未进行独立角色审查"
        return result
    result.role = next(iter(common))
    result.role_evidence_ids = list(dict.fromkeys(related[result.role]))
    result.role_status = "legacy_inferred"
    result.role_reason = "展示标签来源为历史候选说明与关联证据中的明确角色词；角色审查待完成"
    return result


def normalized(text):
    return re.sub(r"\s+", " ", text).strip()


def validate_evidence(items, sources: list[SourceRecord]):
    lookup = {s.source_id: s for s in sources}
    accepted, rejected = [], []
    for item in items:
        payload = dict(item)
        payload.setdefault("evidence_id", "ev_" + digest(item)[:16])
        payload["validated_span"] = False
        try:
            record = EvidenceRecord.model_validate(payload)
            source = lookup.get(record.source_id)
            quote = normalized(record.quote)
            if source is None:
                raise ValueError("unknown source ID")
            if len(quote) < 15 or quote not in normalized(source.text):
                raise ValueError("quote is absent from the provided source or too short")
            if not record.location.strip():
                raise ValueError("source location is missing")
            record.validated_span = True
            accepted.append(record)
        except ValueError as exc:
            rejected.append({"input": item, "reason": str(exc)})
    # An exact quotation verifies provenance; it does NOT itself prove entailment or causality.
    unique = {r.evidence_id: r for r in accepted}
    return list(unique.values()), rejected


def candidate_assessment_fingerprint(candidate: CandidateRecord, evidence: list[EvidenceRecord]):
    """Reviewed scientific inputs, excluding chemical identity and raw observations."""
    attached = {e.evidence_id: e for e in evidence if e.validated_span and e.evidence_id in candidate.evidence_ids}
    return digest({"accepted_evidence": [attached[key].model_dump() for key in sorted(attached)],
                   "admet": {key: candidate.admet.get(key) for key in ("status", "admet_status", "selection_pass")},
                   "gate": {key: candidate.gate.get(key) for key in ("gate_status", "gate_class")}})


def decide_candidates(candidates: list[CandidateRecord], evidence: list[EvidenceRecord],
                      *, scientific_reassessment: bool = False):
    """Apply decision rules, preserving identity-only holds across derived views.

    Only a caller that completed a fresh independent candidate assessment may
    set scientific_reassessment. Finalization and display callers use defaults.
    """
    lookup = {e.evidence_id: e for e in evidence}
    for candidate in candidates:
        records = [lookup[e] for e in candidate.evidence_ids if e in lookup]
        reasons = []
        if candidate.identity_status != "resolved":
            candidate.decision = "hold"
            reasons.append("Chemical identity needs resolution")
        elif "literature" in candidate.routes and not records:
            candidate.decision = "hold"
            reasons.append("Literature claim review did not pass; retain draft for audit, not recommendation")
        elif records and any(e.polarity == "opposes" for e in records) and not any(e.polarity == "supports" for e in records):
            candidate.decision = "hold"
            reasons.append("Attached evidence opposes the recorded design endpoint within its source conditions")
        elif candidate.admet.get("status") == "failed" or candidate.admet.get("admet_status") == "failed":
            candidate.decision = "hold"
            reasons.append("ADMET execution failed; biological interpretation awaits a valid result")
        elif candidate.admet.get("selection_pass") is False:
            candidate.decision = "hold"
            reasons.append("ADMET does not satisfy the explicitly configured selection bounds")
        elif candidate.gate.get("gate_status") == "failed":
            candidate.decision = "hold"
            reasons.append("Gate execution failed; chassis interpretation awaits a valid result")
        elif candidate.role in CONTEXT_ROLES:
            candidate.decision = "hold"
            reasons.append(f"Role {candidate.role} belongs to the context-molecule view")
        elif candidate.gate.get("gate_status") == "success" and candidate.gate.get("gate_class") in ("A1", "A2", "B1", "B2"):
            has_support = any(e.polarity == "supports" for e in records)
            opposed = any(e.polarity in ("opposes", "mixed") for e in records)
            reviewed_role = candidate.role in THERAPEUTIC_ROLES and \
                candidate.role_status == "model_reviewed" and any(
                    e.validated_span and e.polarity == "supports" and
                    e.evidence_id in candidate.role_evidence_ids for e in records
                )
            candidate.decision = "prioritize" if reviewed_role and has_support and not opposed else "exploratory"
            reasons.append("ChassisGate supports its configured native/biotransformation policy")
            reasons.append("Functional interpretation still requires review and experiments")
        elif candidate.gate.get("gate_class") and candidate.gate.get("gate_class") != "E":
            candidate.decision = "hold"
            reasons.append("Current chassis gate excludes this candidate under its policy")
        else:
            candidate.decision = "exploratory"
            reasons.append("ChassisGate is pending or evidence insufficient")
        if not records:
            reasons.append("No validated biomedical evidence attached; computational exploration only")
        elif any(e.polarity in ("opposes", "mixed") for e in records):
            reasons.append("Conflicting/opposing findings require interpretation")
        if candidate.role == "unknown" or candidate.role_status != "model_reviewed":
            reasons.append("Molecule-role review is pending; research priority awaits the role assessment")
        if candidate.identity_assessment_pending:
            if scientific_reassessment:
                candidate.identity_assessment_pending = False
            elif (not candidate.identity_assessment_fingerprint or
                    candidate.identity_assessment_fingerprint == candidate_assessment_fingerprint(candidate, evidence)):
                ranks = {"hold": 0, "exploratory": 1, "prioritize": 2}
                previous = candidate.identity_priority_before_update or "hold"
                if ranks[candidate.decision] > ranks[previous]:
                    candidate.decision = previous
                reasons.append("Chemical identity updated; priority retained pending new reviewed evidence or assessment")
            else:
                # A new accepted evidence/compute assessment may be interpreted
                # normally. Unreviewed tool observations cannot release this guard.
                candidate.identity_assessment_pending = False
        candidate.decision_reasons = reasons
    return candidates

