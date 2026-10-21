from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

class SemanticSource(str, Enum):
    DENOVO = "denovo"; BIOTRANSFORM = "biotransform"; NON_NATIVE = "non_native"

class PrecursorAvailable(str, Enum):
    AVAILABLE_IN_MEDIUM = "available_in_medium"
    AVAILABLE_BY_SIMPLE_FEEDING = "available_by_simple_feeding"
    REQUIRES_CUSTOM_SYNTHESIS = "requires_custom_synthesis"
    UNKNOWN = "unknown"

class ProvenanceStrength(str, Enum):
    EXACT_STRAIN = "exact_strain"; NEAR_RELATIVE = "near_relative"
    GENUS_OR_BROADER = "genus_or_broader"; INFERRED = "inferred"


class LabelStatus(str, Enum):
    ADJUDICABLE = "adjudicable"
    LITERATURE_SILENT_OR_INSUFFICIENT = "literature_silent_or_insufficient"

class RelationType(str, Enum):
    PRODUCES = "produces"; TRANSFORMS = "transforms"
    SUBSTRATE_OF = "substrate_of"; NOT_REPORTED = "not_reported"

class Decision(str, Enum):
    DELIVER = "deliver"; REJECT = "reject"; ABSTAIN = "abstain"

class EvidenceObject(BaseModel):
    organism: str
    enzyme_or_pathway: Optional[str] = None
    relation_type: RelationType
    precursor_available: PrecursorAvailable
    provenance_strength: ProvenanceStrength
    citation_pmid: Optional[str] = None
    support_score: float = Field(ge=0.0, le=1.0)
    actual_producer: Optional[str] = None  # metadata only

class Candidate(BaseModel):
    id: str; name: str; smiles: Optional[str] = None
    inchikey: Optional[str] = None  # used to map to a GEM metabolite (genome/FBA channels)

class Chassis(BaseModel):
    id: str; name: str

class Judgment(BaseModel):
    candidate_id: str
    chassis_id: str
    evidence: list[EvidenceObject] = []
    semantic_source: SemanticSource
    precursor_available: PrecursorAvailable
    engineering_required: bool = False
    raw_confidence: float = Field(ge=0.0, le=1.0)
    engineerable_tier2: bool = False

class Result(BaseModel):
    candidate_id: str
    candidate_name: str
    chassis_id: str
    p_raw: float
    p_cal: Optional[float] = None
    decision: Decision
    semantic_source: SemanticSource
    precursor_available: PrecursorAvailable
    deliverable_now: int
    trace: dict = {}
