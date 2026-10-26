"""Versioned, inspectable Co-Scientist artifacts, not private reasoning traces."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .flow_models import ResearchFlowState


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProblemAnchor(Contract):
    disease: str
    phenotype: str
    species: str
    chassis_name: str
    population: str = ""
    delivery_context: str = ""
    research_goal: str = ""
    condition: str = "未指定培养与供给条件"
    success_criterion: str = "待团队明确可测量研究终点"
    non_goals: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class AgentState(Contract):
    agent_id: str
    role: str
    execution: Literal["llm", "rule_based", "orchestrator"] = "llm"
    status: Literal["queued", "running", "completed", "partial", "failed", "skipped"] = "queued"
    task: str = ""
    updated_at: str = ""
    invocations: int = 0
    tool_calls: int = 0


class AgentEvent(Contract):
    event_id: str
    sequence: int
    agent_id: str
    kind: str
    status: str
    title: str
    summary: str = ""
    created_at: str
    artifact_ids: list[str] = Field(default_factory=list)
    # Public observation metadata only; never prompts or private reasoning traces.
    input_summary: str = ""
    input_artifact_ids: list[str] = Field(default_factory=list)
    operation_id: str = ""
    operation_status: str = ""
    provider_failure: dict[str, Any] = Field(default_factory=dict)
    budget_failure: dict[str, Any] = Field(default_factory=dict)


class ChassisSnapshot(Contract):
    status: Literal["direct_evidence", "indirect", "unknown", "adverse"] = "unknown"
    strain_match: Literal["exact", "other_strain", "unknown"] = "unknown"
    mode: Literal["native_synthesis", "precursor_conversion", "exogenous", "unknown"] = "unknown"
    evidence_ids: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=lambda: ["尚无该菌株、条件下的产物身份/定量及暴露验证"])


class NoveltyAssessment(Contract):
    status: Literal["known_application", "potential_gap", "insufficient_search"] = (
        "insufficient_search"
    )
    closest_source_ids: list[str] = Field(default_factory=list)
    difference: str = ""
    search_scope: str = ""


class HypothesisCard(Contract):
    hypothesis_id: str
    version: int = Field(default=1, ge=1)
    parent_version: int | None = None
    title: str
    claim: str
    molecule_names: list[str] = Field(default_factory=list)
    target_symbols: list[str] = Field(default_factory=list)
    mechanism: str = ""
    chassis_name: str
    condition: str
    endpoint: str
    evidence_ids: list[str] = Field(default_factory=list)
    opposing_evidence_ids: list[str] = Field(default_factory=list)
    inference_gaps: list[str] = Field(default_factory=list)
    prediction: str
    validation_question: str
    origin: Literal["user_supplied", "literature", "synthesis"] = "synthesis"
    status: Literal["proposed", "reviewed", "revised", "needs_evidence", "withheld"] = "proposed"
    chassis: ChassisSnapshot = Field(default_factory=ChassisSnapshot)
    novelty: NoveltyAssessment = Field(default_factory=NoveltyAssessment)
    changes: list[str] = Field(default_factory=list)


class HypothesisReview(Contract):
    hypothesis_id: str
    version: int
    verdict: Literal["retain", "revise", "withhold"]
    reason: str
    objections: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    valid_evidence_ids: list[str] = Field(default_factory=list)


class PairwiseReview(Contract):
    comparison_id: str
    left_id: str
    left_version: int
    right_id: str
    right_version: int
    outcome: Literal["left", "right", "tie", "insufficient", "incomparable"]
    reason: str
    criterion: str
    evidence_ids: list[str] = Field(default_factory=list)


class ParameterNeed(Contract):
    variable: str
    unit: str
    why_needed: str
    value: None = None
    data_class: Literal["measurement_required"] = "measurement_required"
    source_ids: list[str] = Field(default_factory=list)


class ResearchContract(Contract):
    hypothesis_id: str
    version: int
    status: Literal["human_review_needed", "needs_evidence", "awaiting_measurement"]
    question: str
    judgment: str
    evidence_ids: list[str] = Field(default_factory=list)
    opposing_evidence_ids: list[str] = Field(default_factory=list)
    minimal_validation: str
    resource_requirements: list[str] = Field(default_factory=list)
    remaining_unknowns: list[str] = Field(default_factory=list)
    parameter_needs: list[ParameterNeed] = Field(default_factory=list)


class CoScientistState(Contract):
    protocol_version: str = "0.3.0"
    seed: dict[str, Any] = Field(default_factory=dict)
    anchor: ProblemAnchor | None = None
    agents: list[AgentState] = Field(default_factory=list)
    events: list[AgentEvent] = Field(default_factory=list)
    hypotheses: list[HypothesisCard] = Field(default_factory=list)
    reviews: list[HypothesisReview] = Field(default_factory=list)
    comparisons: list[PairwiseReview] = Field(default_factory=list)
    contracts: list[ResearchContract] = Field(default_factory=list)
    clusters: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_plans: list[dict[str, Any]] = Field(default_factory=list)
    novelty_searches: list[dict[str, Any]] = Field(default_factory=list)
    selected_versions: list[dict[str, Any]] = Field(default_factory=list)
    # Current entity annotations are separate from versioned scientific claims/gaps.
    entity_references: list[dict[str, Any]] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    budget_failures: list[dict[str, Any]] = Field(default_factory=list)
    optional_skips: list[dict[str, Any]] = Field(default_factory=list)
    research_flow: ResearchFlowState | None = None
    limitations: list[str] = Field(
        default_factory=lambda: [
            "多角色通过共享后端的分离上下文完成生成、反思与审查，后端配置随运行保存。",
            "研究优先级依据证据适用性、可检验性和资源匹配进行比较。",
            "底盘快照汇总研究线索；正式分类、FBA 结果与 PKPD 参数来源分别记录。",
        ]
    )

