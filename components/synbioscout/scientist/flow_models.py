"""Public, bounded research receipts. No prompts, raw provider objects or full texts."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceSnapshot(Receipt):
    snapshot_id: str
    source_id: str
    provider: str
    title: str
    url: str
    retrieved_at: str
    scope: str
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""
    version: str = ""
    year: str = ""
    publication_status: str = "unknown"
    authors: list[str] = Field(default_factory=list)
    journal: str = ""
    text_sha256: str
    origin: Literal["retrieved", "saved", "library"] = "retrieved"


class PublicationGroup(Receipt):
    paper_id: str
    kind: Literal["paper", "database", "local_document"] = "paper"
    aliases: list[str] = Field(default_factory=list)
    snapshots: list[SourceSnapshot] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ClaimRecord(Receipt):
    claim_id: str
    hypothesis_id: str
    version: int
    kind: Literal["main", "disease_phenotype", "mechanism_direction", "intervention", "chassis_supply", "delivery_exposure"]
    text: str
    condition: str
    endpoint: str
    state: Literal["pending", "supported", "contested", "context_only", "unknown"] = "pending"
    relation_schema_version: Literal[1, 2] = 1
    assessment_scope: Literal["inference", "source_observation"] = "inference"
    evidence_ids: list[str] = Field(default_factory=list)
    query_ids: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class QueryReceipt(Receipt):
    query_id: str
    stage: Literal["landscape", "supplement", "novelty", "feedback", "full_text", "reuse"]
    purpose: str
    query: str
    provider: str = "europe_pmc"
    date_from: str = ""
    date_to: str = ""
    claim_ids: list[str] = Field(default_factory=list)
    hypothesis_id: str = ""
    version: int | None = None
    status: str = "planned"
    stop_reason: str = ""
    retrieved_at: str = ""
    source_ids: list[str] = Field(default_factory=list)
    before_dedup: int = 0
    after_dedup: int = 0
    total_hits: int | None = None
    coverage: str = "unknown"
    facet: str = ""
    query_origin: str = ""
    sort: str = ""
    pagination: str = ""
    hit_range_start: int | None = None
    hit_range_end: int | None = None
    has_more: bool | None = None
    selection_reason: str = ""
    coverage_gaps: list[str] = Field(default_factory=list)
    scope: str = "abstract"
    call_ids: list[str] = Field(default_factory=list)
    request_count: int = 0
    cache_hits: int = 0
    reused_from: str = ""


class ClaimEvidenceRelation(Receipt):
    """v2 relation request; only verified source-report projections can override polarity."""
    schema_version: Literal[2] = 2
    claim_id: str
    hypothesis_id: str
    version: int = Field(ge=1)
    evidence_id: str
    source_id: str
    entity_id: str
    relation: Literal["supports", "opposes", "context"]
    study_system: str
    condition: str
    endpoint: str


class ClaimReviewDraft(Receipt):
    relation_schema_version: Literal[1, 2] = 1
    claim_scope: Literal["inference", "source_observation"] = "inference"
    evidence_relations: list[ClaimEvidenceRelation] = Field(default_factory=list)
    claim_id: str
    hypothesis_id: str
    version: int
    verdict: Literal["supported", "contested", "context_only", "unknown"]
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str
    gaps: list[str] = Field(default_factory=list)


class EvidenceLink(Receipt):
    claim_id: str
    hypothesis_id: str
    version: int
    evidence_id: str
    source_id: str
    paper_id: str
    snapshot_id: str
    query_ids: list[str] = Field(default_factory=list)
    polarity: str
    scope: str
    study_system: str
    condition: str
    endpoint: str
    strength: str = "source_claim_review_not_experimental_validation"
    span_start: int
    span_end: int
    span_sha256: str
    location: str
    # Present only after deterministic v2 validation; original polarity is unchanged.
    claim_relation: ClaimEvidenceRelation | None = None


class ClaimReview(ClaimReviewDraft):
    review_id: str
    valid_evidence_ids: list[str] = Field(default_factory=list)
    rejected_evidence_ids: list[str] = Field(default_factory=list)
    rejected_relation_ids: list[str] = Field(default_factory=list)


class PriorWork(Receipt):
    hypothesis_id: str
    version: int
    source_id: str
    mechanism_overlap: str
    object_overlap: str
    strain_overlap: str
    condition_overlap: str
    endpoint_overlap: str
    unconfirmed_difference: str


class NoveltyReceipt(Receipt):
    novelty_id: str
    hypothesis_id: str
    version: int
    status: str = "insufficient_search"
    query_ids: list[str] = Field(default_factory=list)
    closest_source_ids: list[str] = Field(default_factory=list)
    overlap: list[str] = Field(default_factory=list)
    prior_work: list[PriorWork] = Field(default_factory=list)
    unconfirmed_difference: str = ""
    limitations: list[str] = Field(default_factory=list)


class RevisionReceipt(Receipt):
    revision_id: str
    hypothesis_id: str
    from_version: int
    to_version: int
    field: str
    before_sha256: str
    after_sha256: str
    claim_ids: list[str] = Field(default_factory=list)
    review_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    query_ids: list[str] = Field(default_factory=list)
    reason: str


class ReferenceCoverage(Receipt):
    """Declared reference identity versus current-run acquisition and citation use."""
    reference_id: str
    source_id: str
    identifiers: list[str] = Field(default_factory=list)
    title: str = ""
    url: str = ""
    package_id: str = ""
    package_version: str = ""
    package_manifest_sha256: str = ""
    declared_evidence_level: str = "unknown"
    available_in_reference: bool = False
    source_scope: str = "unknown"
    original_source_scope: str = "unknown"
    local_excerpt_only: bool = False
    selection_status: Literal["selected", "not_selected"] = "not_selected"
    access_status: Literal["available", "no_access"] = "no_access"
    citation_status: Literal["accepted", "unreviewed_or_unaccepted"] = "unreviewed_or_unaccepted"
    selected_source_ids: list[str] = Field(default_factory=list)
    accepted_evidence_ids: list[str] = Field(default_factory=list)
    scope_note: str = "Source reading scope, citation acceptance and claim-review status are recorded separately."


class ResearchFlowState(Receipt):
    protocol_version: str = "bounded-literature-1"
    activation: str = "research_mode=co_scientist"
    executor: str = "CoScientistEngine/ResearchFlow"
    stage_limits: dict[str, int] = Field(default_factory=dict)
    stage_usage: dict[str, int] = Field(default_factory=dict)
    library_status: list[dict[str, str | int]] = Field(default_factory=list)
    publications: list[PublicationGroup] = Field(default_factory=list)
    reference_coverage: list[ReferenceCoverage] = Field(default_factory=list)
    claims: list[ClaimRecord] = Field(default_factory=list)
    queries: list[QueryReceipt] = Field(default_factory=list)
    links: list[EvidenceLink] = Field(default_factory=list)
    claim_reviews: list[ClaimReview] = Field(default_factory=list)
    novelty: list[NoveltyReceipt] = Field(default_factory=list)
    revisions: list[RevisionReceipt] = Field(default_factory=list)
    checklist: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=lambda: [
        "检索范围为 Europe PMC 中本次记录的查询、时间窗口和分页。",
        "独立研究按原始研究标识去重，缓存访问沿用原来源记录。",
        "查新报告列出实际检索窗口、相关工作、待确认差异和执行状态。",
        "主张审查由共享后端的分离上下文执行，保存逐条主张与来源的审查记录。",
    ])

