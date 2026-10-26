"""Persisted contracts shared by research, target preparation and integrations."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .assessment_models import CandidateEvaluation
from .co_models import CoScientistState


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResearchBudget(StrictModel):
    max_targets: int = Field(default=2, ge=0, le=10)
    literature_max_candidates: int = Field(default=12, ge=1, le=100)
    max_papers: int = Field(default=12, ge=1, le=100)
    max_searches: int = Field(default=8, ge=1, le=40)
    max_llm_calls: int = Field(default=10, ge=1, le=60)
    max_md_jobs_total: int = Field(default=20, ge=0, le=200)
    timeout_seconds: int = Field(default=1800, ge=30, le=14400)
    max_hypotheses: int = Field(default=6, ge=1, le=6)
    max_http_requests: int = Field(default=32, ge=1, le=100)
    max_tool_calls: int = Field(default=12, ge=0, le=32)
    max_input_tokens: int = Field(default=300000, ge=1000, le=2000000)
    max_output_tokens: int = Field(default=96000, ge=1000, le=300000)


class ResearchBrief(StrictModel):
    disease: str = Field(min_length=1, max_length=250)
    task_intent: Literal["legacy", "disease_exploration", "candidate_assessment"] = "legacy"
    research_mode: Literal["standard", "co_scientist"] = "standard"
    continue_saved_hypotheses: bool = False
    # Opt-in, repository-local, versioned knowledge. Never inferred from disease.
    background_package_id: str = Field(default="", max_length=80, pattern=r"^(?:[a-zA-Z0-9][a-zA-Z0-9_-]{0,79})?$")
    existing_candidates: list[str] = Field(default_factory=list, max_length=10)
    phenotype: str = Field(default="", max_length=1000)
    population: str = Field(default="", max_length=1000)
    delivery_context: str = Field(default="", max_length=1000)
    conditions: str = Field(default="", max_length=1000)
    success_criterion: str = Field(default="", max_length=1000)
    research_goal: str = Field(
        default="科研用途：识别靶点、候选分子及关联证据", max_length=1000
    )
    species: str = "Homo sapiens"
    chassis_name: str = "Lactiplantibacillus plantarum WCFS1"
    chassis_id: str = "lplantarum"
    reference_chassis: str = "Lactiplantibacillus plantarum WCFS1"
    allowed_precursors: list[str] = Field(default_factory=list)
    engineering_allowed: bool = False
    experimental_capabilities: list[str] = Field(default_factory=list)
    target_hints: list[str] = Field(default_factory=list)
    target_packages: list[dict[str, Any]] = Field(default_factory=list)
    experiment_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    budget: ResearchBudget = Field(default_factory=ResearchBudget)

    @model_validator(mode="after")
    def background_mode(self):
        from .intent_policy import validate_intent_brief
        validate_intent_brief(self)
        if self.background_package_id and self.research_mode != "co_scientist":
            raise ValueError("An explicit background package requires co_scientist mode")
        return self


class SourceRecord(StrictModel):
    source_id: str
    provider: str
    title: str
    url: str
    retrieved_at: str
    text: str = ""
    pmid: str = ""
    doi: str = ""
    pmcid: str = ""
    evidence_scope: Literal["abstract", "full_text", "database", "local_document"] = "abstract"
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRecord(StrictModel):
    evidence_id: str
    claim: str
    source_id: str
    quote: str
    location: str
    polarity: Literal["supports", "opposes", "mixed", "context"]
    study_system: str = "unknown"
    condition: str = "unknown"
    endpoint: str = "unknown"
    limitations: list[str] = Field(default_factory=list)
    validated_span: bool = False


class TargetCard(StrictModel):
    target_id: str
    gene_symbol: str
    species: str = "Homo sapiens"
    uniprot_id: str = ""
    ensembl_id: str = ""
    desired_direction: Literal["inhibit", "activate", "modulate", "unknown"] = "unknown"
    rationale: str = ""
    related_cells: list[str] = Field(default_factory=list)
    pathways: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    assessment: dict[str, Any] = Field(default_factory=dict)
    structure_status: str = "not_prepared"
    package_id: str = ""


CandidateRole = Literal[
    "therapeutic_candidate", "symptom_management", "biomarker", "risk_factor",
    "metabolic_context", "compound_class", "unknown",
]


class CandidateNameDeclaration(StrictModel):
    """An explicit user label, separate from provider-confirmed synonyms."""
    label: str = Field(min_length=1, max_length=500)
    full_name: str = Field(min_length=1, max_length=500)
    abbreviation: str = Field(default="", max_length=12)
    source: Literal["brief.existing_candidates"] = "brief.existing_candidates"
    source_index: int = Field(ge=0, le=9)
    run_id: str = Field(min_length=1, max_length=100)


class CandidateRecord(StrictModel):
    candidate_id: str
    name: str
    smiles: str = ""
    inchikey: str = ""
    pubchem_cid: str = ""
    original_ids: list[str] = Field(default_factory=list)
    # A stable entity reference survives replacement of the provisional chemical ID.
    entity_id: str = ""
    aliases: list[str] = Field(default_factory=list)
    user_name_declarations: list[CandidateNameDeclaration] = Field(default_factory=list, max_length=10)
    identity_revision: int = Field(default=0, ge=0)
    identity_source_ids: list[str] = Field(default_factory=list)
    identity_form: dict[str, Any] = Field(default_factory=dict)
    identity_history: list[dict[str, Any]] = Field(default_factory=list)
    identity_assessment_pending: bool = False
    identity_priority_before_update: Literal["", "prioritize", "exploratory", "hold"] = ""
    # Internal equality check over accepted evidence and decision-relevant Gate/ADMET inputs.
    identity_assessment_fingerprint: str = ""
    routes: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    target_relations: dict[str, str] = Field(default_factory=dict)
    rationale: str = ""
    role: CandidateRole = "unknown"
    role_reason: str = ""
    role_evidence_ids: list[str] = Field(default_factory=list)
    role_status: Literal[
        "unclassified", "model_reviewed", "legacy_inferred", "needs_review"
    ] = "unclassified"
    uncertainties: list[str] = Field(default_factory=list)
    identity_status: str = "unresolved"
    decision: Literal["prioritize", "exploratory", "hold"] = "exploratory"
    decision_reasons: list[str] = Field(default_factory=list)
    admet: dict[str, Any] = Field(default_factory=dict)
    gate: dict[str, Any] = Field(default_factory=dict)
    scores: list[dict[str, Any]] = Field(default_factory=list)


class TargetPackageRequest(StrictModel):
    uniprot_id: str = Field(min_length=1)
    pdb_id: str = ""
    protein_chain: str = ""
    ligand_id: str = ""
    ligand_chain: str = ""
    ligand_residue: str = ""
    model_number: int = Field(default=1, ge=1)
    sequence_scope: Literal["canonical", "structure_domain"] = "canonical"
    species: str = "Homo sapiens"
    target_id: str = ""
    allow_mutations: bool = False


class TargetPackage(StrictModel):
    package_id: str
    status: Literal["ready", "needs_review", "blocked"]
    protein_pdb: str = ""
    sequence_fasta: str = ""
    reference_ligand_sdf: str = ""
    chain: str = ""
    pocket_center: list[float] = Field(default_factory=list)
    model_readiness: dict[str, dict[str, Any]] = Field(default_factory=dict)
    checks: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanStep(StrictModel):
    step_id: str
    question: str
    capability: str
    status: str = "queued"
    reason: str = ""
    artifact: str = ""


class ResearchRun(StrictModel):
    run_id: str
    project_id: str
    created_at: str
    updated_at: str
    status: ExecutionStatus = ExecutionStatus.QUEUED
    brief: ResearchBrief
    steps: list[PlanStep] = Field(default_factory=list)
    sources: list[SourceRecord] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    targets: list[TargetCard] = Field(default_factory=list)
    candidates: list[CandidateRecord] = Field(default_factory=list)
    candidate_evaluations: list[CandidateEvaluation] = Field(default_factory=list)
    target_packages: list[TargetPackage] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    parent_run_id: str = ""
    report_path: str = ""
    co_scientist: CoScientistState | None = None


class LLMSettings(StrictModel):
    backend: Literal["codex", "responses", "compatible", "none"] = "codex"
    model: str = ""
    base_url: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    executable: str = "codex"
    timeout_seconds: int = Field(default=900, ge=10, le=1800)


class ScientistSettings(StrictModel):
    workspace: str = "runs/scientist"
    llm: LLMSettings = Field(default_factory=LLMSettings)
    target2lead_config: str = ""
    http_timeout_seconds: int = Field(default=30, ge=5, le=120)
    http_retries: int = Field(default=2, ge=0, le=5)
    allow_remote_llm_data: bool = True


class ExperimentSpec(StrictModel):
    name: str = Field(min_length=1)
    kind: Literal["growth", "concentration", "activity", "dose_response"]
    endpoint: str
    unit: str
    candidate_id: str = ""
    research_run_id: str = ""
    control_group: str = ""
    chassis_name: str = ""
    condition: str = ""
    time_unit: str = "h"
    dose_unit: str = "uM"
    qc_passed: bool = False
    qc_notes: str = ""
    detection_limit: float | None = Field(default=None, ge=0, allow_inf_nan=False)

