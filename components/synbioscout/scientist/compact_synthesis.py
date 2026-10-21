"""Versioned lossless projection of model-selected findings, never a review.

The v2 marker lives on assessment proposals so old synthesis contracts remain
readable. Every expanded statement and scope is a copy of one explicit key;
conclusions, unknowns, next questions and the choice of findings remain model
output. The original transport is accounted separately from local expansion.
"""
from __future__ import annotations

import json

from pydantic import ConfigDict, Field

from .assessment_models import CompactCandidateAssessmentProposal
from .models import StrictModel
from .research import CandidateDraft, EvidenceDraft, SynthesisDraft, TargetDraft
from .research_flow import source_observation_statement

MAX_EXPANSION_FACTOR = 3


class CompactSynthesisDraft(StrictModel):
    # Keep the stage identifier used by orchestration/receipts stable; the
    # actual new-call schema contains only v2, unlike the historical reader.
    model_config = ConfigDict(extra="forbid", title="SynthesisDraft")
    evidence: list[EvidenceDraft]
    targets: list[TargetDraft]
    candidates: list[CandidateDraft]
    unresolved_questions: list[str]
    assessment_proposals: list[CompactCandidateAssessmentProposal] = Field(default_factory=list)


def _unique(values, label):
    if any(not value for value in values) or len(values) != len(set(values)):
        raise ValueError("Invalid or repeated " + label)


def expand_synthesis_draft(raw):
    """Return legacy-shaped scientific data plus numeric-only expansion receipt."""
    wire_bytes = len(json.dumps(raw, ensure_ascii=False).encode())
    proposals = raw.get("assessment_proposals", [])
    versions = [item.get("schema_version", 1) for item in proposals]
    if any(type(version) is not int or version not in {1, 2} for version in versions):
        raise ValueError("Invalid assessment wire version")
    if len(set(versions)) > 1:
        raise ValueError("Mixed assessment wire versions")
    version = versions[0] if versions else 1
    draft = SynthesisDraft.model_validate(raw)
    if version == 2:
        _unique([e.key for e in draft.evidence], "evidence keys")
        _unique([c.name for c in draft.candidates], "candidate names")
        _unique([p.candidate_name for p in draft.assessment_proposals], "assessment candidate names")
        evidence = {e.key: e for e in draft.evidence}
        candidates = {c.name: c for c in draft.candidates}
        for item in [*draft.targets, *draft.candidates]:
            _unique(item.evidence_keys, "attached evidence keys")
            if not set(item.evidence_keys).issubset(evidence):
                raise ValueError("Foreign attached evidence key")
        for candidate in draft.candidates:
            _unique(candidate.role_evidence_keys, "role evidence keys")
            if not set(candidate.role_evidence_keys).issubset(candidate.evidence_keys):
                raise ValueError("Role evidence is not attached to candidate")
        expanded, used = [], set()
        for proposal in draft.assessment_proposals:
            if proposal.candidate_name not in candidates:
                raise ValueError("Assessment candidate is absent from synthesis")
            _unique([f.finding_key for f in proposal.findings], "finding keys")
            findings = []
            for finding in proposal.findings:
                key = finding.evidence_key
                if key in used or key not in evidence or key not in candidates[proposal.candidate_name].evidence_keys:
                    raise ValueError("Missing, repeated or foreign finding reference")
                used.add(key)
                item = evidence[key]
                findings.append({"finding_key": finding.finding_key, "evidence_keys": [key],
                    "statement": source_observation_statement(item.source_id, item.quote),
                    "scope": {k: getattr(item, k) for k in ("study_system", "condition", "endpoint")}})
            expanded.append({**proposal.model_dump(exclude={"schema_version", "findings"}), "findings": findings})
        draft = SynthesisDraft.model_validate({**draft.model_dump(), "assessment_proposals": expanded})
    expanded_bytes = len(json.dumps(draft.model_dump(mode="json"), ensure_ascii=False).encode())
    if version == 2 and expanded_bytes > MAX_EXPANSION_FACTOR * wire_bytes:
        raise ValueError("Compact expansion exceeds its reserved growth bound")
    return draft, {"schema_version": version, "wire_bytes": wire_bytes, "expanded_bytes": expanded_bytes,
        "growth_bytes": max(0, expanded_bytes - wire_bytes), "evidence_count": len(draft.evidence),
        "finding_count": sum(len(p.findings) for p in draft.assessment_proposals)}

