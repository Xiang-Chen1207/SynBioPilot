"""Versioned, independently reviewed candidate conclusions (not efficacy scores).

These contracts define their own reviewed content, separately from CandidateRecord.rationale and the
quote/role review. A final is a copy of an explicit model proposal, only after
the separate conclusion review and deterministic binding checks succeed.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Text = Annotated[str, Field(min_length=1, max_length=1200)]
Identifier = Annotated[str, Field(min_length=1, max_length=300)]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ScientificStatus = Literal["limited_source_findings", "mixed_evidence", "no_supported_benefit", "needs_evidence"]
Verdict = Literal["accept", "revise", "withhold"]
Applicability = Literal["source_scope_only", "population_uncertain", "nonhuman_or_unknown"]


class AssessmentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssessmentScope(AssessmentModel):
    study_system: str = Field(min_length=1, max_length=2000)
    condition: str = Field(min_length=1, max_length=2000)
    endpoint: str = Field(min_length=1, max_length=2000)


class CandidateFindingProposal(AssessmentModel):
    finding_key: Identifier
    statement: Text
    evidence_keys: list[Identifier] = Field(min_length=1, max_length=100)
    scope: AssessmentScope


class CandidateAssessmentProposal(AssessmentModel):
    candidate_name: str = Field(min_length=1, max_length=300)
    conclusion: Text
    scientific_status: ScientificStatus
    findings: list[CandidateFindingProposal] = Field(min_length=1, max_length=100)
    unknowns: list[Text] = Field(min_length=1, max_length=100)
    next_questions: list[Text] = Field(min_length=1, max_length=100)


class CompactFindingProposal(AssessmentModel):
    finding_key: Identifier
    evidence_key: Identifier
    projection: Literal["source_observation"]


class CompactCandidateAssessmentProposal(AssessmentModel):
    schema_version: Literal[2]
    candidate_name: str = Field(min_length=1, max_length=300)
    conclusion: Text
    scientific_status: ScientificStatus
    findings: list[CompactFindingProposal] = Field(min_length=1, max_length=100)
    unknowns: list[Text] = Field(min_length=1, max_length=100)
    next_questions: list[Text] = Field(min_length=1, max_length=100)


class CandidateAssessmentFinding(AssessmentModel):
    finding_id: Identifier
    statement: Text
    evidence_ids: list[Identifier] = Field(max_length=100)
    scope: AssessmentScope
    applicability: Applicability = "source_scope_only"
    scope_limitations: list[Text] = Field(default_factory=list, max_length=100)


class CandidateAssessmentContent(AssessmentModel):
    conclusion: Text
    scientific_status: ScientificStatus
    findings: list[CandidateAssessmentFinding] = Field(min_length=1, max_length=100)
    supporting_evidence_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    opposing_evidence_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    unknowns: list[Text] = Field(min_length=1, max_length=100)
    next_questions: list[Text] = Field(min_length=1, max_length=100)


class CandidateFindingReview(AssessmentModel):
    finding_id: Identifier
    verdict: Verdict
    evidence_ids: list[Identifier] = Field(max_length=100)
    applicability: Applicability
    reason: Text


class CandidateAssessmentReview(AssessmentModel):
    review_id: Identifier
    assessment_id: Identifier
    version: int = Field(ge=1)
    candidate_id: Identifier
    entity_id: Identifier
    identity_revision: int = Field(ge=0)
    identity_fingerprint: Fingerprint
    task_fingerprint: Fingerprint
    conclusion_fingerprint: Fingerprint
    evidence_fingerprint: Fingerprint
    verdict: Verdict
    reason: Text
    item_reviews: list[CandidateFindingReview] = Field(max_length=100)
    supporting_evidence_ids: list[Identifier] = Field(max_length=100)
    opposing_evidence_ids: list[Identifier] = Field(max_length=100)


class CandidateAssessmentReviewDraft(AssessmentModel):
    reviews: list[CandidateAssessmentReview] = Field(max_length=100)


class CandidateEvaluation(AssessmentModel):
    schema_version: Literal[1] = 1
    assessment_id: Identifier
    version: int = Field(default=1, ge=1)
    candidate_id: Identifier
    entity_id: Identifier
    identity_revision: int = Field(default=0, ge=0)
    identity_fingerprint: str = ""
    task_fingerprint: str = ""
    conclusion_fingerprint: str = ""
    evidence_fingerprint: str = ""
    # This snapshot, not later unrelated evidence, defines what was reviewed.
    reviewed_evidence_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    draft: CandidateAssessmentContent
    reviews: list[CandidateAssessmentReview] = Field(default_factory=list, max_length=100)
    # Set by the executor, never accepted from the review model's response.
    review_operation_id: str = Field(default="", max_length=300)
    status: Literal["draft", "ready_for_review", "completed_review", "needs_review"] = "draft"
    validation_errors: list[Text] = Field(default_factory=list, max_length=100)
    final: CandidateAssessmentContent | None = None


# Descriptive compatibility name for callers describing the third review stage.
CandidateConclusionReviewDraft = CandidateAssessmentReviewDraft

