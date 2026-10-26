"""Auditable output contracts for the KEGG + Genome + FBA paths."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


PathState = Literal["supports_production", "assessed_negative", "unknown"]


class ReactionTrace(BaseModel):
    reaction_id: str
    reaction_name: str = ""
    equation: str = ""
    gene_reaction_rule: str = ""
    gene_ids: list[str] = Field(default_factory=list)
    ec_codes: list[str] = Field(default_factory=list)


class KeggReactionTrace(BaseModel):
    reaction_id: str
    equation: str = ""
    direction: str = "unknown"
    precursor_compound_ids: list[str] = Field(default_factory=list)
    ko_ids: list[str] = Field(default_factory=list)
    ec_numbers: list[str] = Field(default_factory=list)


class GemGenomeTrace(BaseModel):
    mapping_status: Literal["mapped", "metabolite_not_in_model", "model_unavailable"]
    model_id: str | None = None
    metabolite_id: str | None = None
    metabolite_name: str | None = None
    directional_producer_count: int = 0
    excluded_transport_count: int = 0
    gene_associated_producer_count: int = 0
    producers: list[ReactionTrace] = Field(default_factory=list)


class KeggGenomeTrace(BaseModel):
    status: Literal["ok", "unavailable", "unmapped"]
    compound_id: str | None = None
    mapping_method: str | None = None
    organism_code: str | None = None
    producing_reaction_ids: list[str] = Field(default_factory=list)
    producing_reactions: list[KeggReactionTrace] = Field(default_factory=list)
    candidate_kos: list[str] = Field(default_factory=list)
    present_kos: list[str] = Field(default_factory=list)
    missing_kos: list[str] = Field(default_factory=list)
    supported_reaction_ids: list[str] = Field(default_factory=list)
    ec_numbers: list[str] = Field(default_factory=list)
    reaction_coverage: float | None = None
    direct_precursor_ids: list[str] = Field(default_factory=list)
    error: str | None = None


class GenomePathResult(BaseModel):
    state: PathState
    gem: GemGenomeTrace
    kegg: KeggGenomeTrace
    conflict: str | None = None
    interpretation: str
    limitation: str = (
        "KO/GPR evidence records pathway structure; expression, enzyme activity, precursor supply and yield have separate evidence fields."
    )


class PrecursorRescueTrace(BaseModel):
    precursor_kegg_id: str
    metabolite_id: str
    exchange_reaction_ids: list[str] = Field(default_factory=list)
    target_flux: float


class FBAPathResult(BaseModel):
    state: PathState
    candidate_class: Literal["denovo", "biotransform_candidate", "mechanistic_negative"] | None = None
    model_id: str | None = None
    target_metabolite_id: str | None = None
    mapping_status: Literal["mapped", "metabolite_not_in_model", "model_unavailable"]
    kegg_compound_id: str | None = None
    default_medium_max_flux: float | None = None
    growth_optimum: float | None = None
    minimum_growth_flux: float | None = None
    growth_constraint_applied: bool = False
    flux_threshold: float = 1e-6
    target_import_blocked: bool = True
    tested_precursor_ids: list[str] = Field(default_factory=list)
    rescues: list[PrecursorRescueTrace] = Field(default_factory=list)
    interpretation: str
    limitation: str = (
        "FBA reports steady-state flux feasibility under configured constraints; expression, kinetics, toxicity, secretion and measured yield are evaluated through their corresponding evidence."
    )
