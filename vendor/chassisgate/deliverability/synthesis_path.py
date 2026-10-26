"""Concrete synthesis-path contracts and conservative builders.

The final ten-class gate may only attach a positive de-novo/biotransformation
class when a concrete path is available from one of two sources:

1. a structured full-text literature extraction;
2. KEGG reaction context.

If neither source yields a path, the gate should abstain rather than emit a
positive A/B class.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .literature import LiteratureAssessment
from .mechanistic_models import GenomePathResult


PathSource = Literal["literature_fulltext", "kegg"]
PathMechanism = Literal["denovo", "biotransform", "denovo&biotransform"]
PathStatus = Literal["curated", "candidate_core", "candidate_context", "overbroad"]

MAX_KEGG_PATH_STEPS = 6

_CURRENCY_COMPOUNDS = {
    "C00001",  # water
    "C00002", "C00008",  # ATP / ADP
    "C00003", "C00004",  # NAD / NADH
    "C00005", "C00006",  # NADPH / NADP
    "C00009", "C00013",  # phosphate / diphosphate
    "C00010",  # CoA
    "C00011",  # CO2
    "C00014",  # ammonia
    "C00080",  # proton
}


class SynthesisStep(BaseModel):
    step_index: int
    reaction_id: str = ""
    reaction_name: str = ""
    equation: str = ""
    substrates: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    enzyme_ids: list[str] = Field(default_factory=list)
    ec_numbers: list[str] = Field(default_factory=list)
    ko_ids: list[str] = Field(default_factory=list)
    gene_ids: list[str] = Field(default_factory=list)
    native_status: Literal[
        "native_supported", "native_unverified", "heterologous", "unknown"
    ] = "unknown"
    evidence_source: PathSource
    evidence_refs: list[str] = Field(default_factory=list)
    note: str = ""


class SynthesisPath(BaseModel):
    source: PathSource
    mechanism: PathMechanism
    path_status: PathStatus = "candidate_context"
    path_issue: str = ""
    target_compound_id: str | None = None
    entry_precursors: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    steps: list[SynthesisStep] = Field(default_factory=list)
    confidence_note: str = ""

    @property
    def available(self) -> bool:
        return bool(self.steps)


def choose_synthesis_path(
    *,
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    mechanism: str,
) -> SynthesisPath | None:
    """Prefer full-text literature paths, then fall back to KEGG.

    ``mechanism`` may be a dual label. The builder packages explicit literature
    path extractions or KEGG reaction records collected by the genome path.
    Every step retains its source record.
    """
    if mechanism not in {"denovo", "biotransform", "denovo&biotransform"}:
        return None
    literature_path = _literature_path(literature, mechanism)
    if literature_path is not None:
        return literature_path
    return _kegg_path(genome, mechanism)


def _literature_path(
    literature: LiteratureAssessment, mechanism: str
) -> SynthesisPath | None:
    raw = literature.synthesis_path or {}
    if not isinstance(raw, dict):
        return None
    steps_raw = raw.get("steps") or []
    if not steps_raw:
        return None
    refs = _split_refs(raw.get("evidence_refs") or literature.evidence_pmids)
    steps: list[SynthesisStep] = []
    for index, item in enumerate(steps_raw, 1):
        if not isinstance(item, dict):
            continue
        steps.append(SynthesisStep(
            step_index=int(item.get("step_index") or index),
            reaction_id=str(item.get("reaction_id") or ""),
            reaction_name=str(item.get("reaction_name") or item.get("name") or ""),
            equation=str(item.get("equation") or ""),
            substrates=_list(item.get("substrates")),
            products=_list(item.get("products")),
            enzyme_ids=_list(item.get("enzyme_ids") or item.get("enzymes")),
            ec_numbers=_list(item.get("ec_numbers") or item.get("ec")),
            ko_ids=_list(item.get("ko_ids") or item.get("ko")),
            gene_ids=_list(item.get("gene_ids") or item.get("genes")),
            native_status=_native_status(item.get("native_status")),
            evidence_source="literature_fulltext",
            evidence_refs=_split_refs(item.get("evidence_refs")) or refs,
            note=str(item.get("note") or ""),
        ))
    if not steps:
        return None
    return SynthesisPath(
        source="literature_fulltext",
        mechanism=_mechanism(mechanism),
        path_status="curated",  # Compatibility label for automatically structured full-text steps.
        target_compound_id=raw.get("target_compound_id"),
        entry_precursors=_list(raw.get("entry_precursors")),
        evidence_refs=refs,
        steps=steps,
        confidence_note=str(
            raw.get("confidence_note")
            or "Structured pathway extracted from literature full text."
        ),
    )


def _kegg_path(genome: GenomePathResult, mechanism: str) -> SynthesisPath | None:
    candidate_reactions = [
        reaction
        for reaction in genome.kegg.producing_reactions
        if (
            reaction.reaction_id in genome.kegg.supported_reaction_ids
            or not genome.kegg.supported_reaction_ids
        )
    ]
    reactions = _ranked_core_reactions(candidate_reactions, genome)
    if not reactions:
        return None
    status, issue = _kegg_path_status(candidate_reactions, genome)
    steps = [
        SynthesisStep(
            step_index=index,
            reaction_id=reaction.reaction_id,
            equation=reaction.equation,
            substrates=_direct_precursors(reaction, genome.kegg.compound_id),
            products=[genome.kegg.compound_id] if genome.kegg.compound_id else [],
            enzyme_ids=reaction.ko_ids,
            ec_numbers=reaction.ec_numbers,
            ko_ids=reaction.ko_ids,
            gene_ids=_gene_ids_for_reaction(reaction, genome),
            native_status=(
                "native_supported"
                if (
                    reaction.reaction_id in genome.kegg.supported_reaction_ids
                    or _gene_ids_for_reaction(reaction, genome)
                )
                else "native_unverified"
            ),
            evidence_source="kegg",
            evidence_refs=[reaction.reaction_id],
            note=(
                "Selected KEGG target-producing reaction; substrates list keeps "
                "non-currency direct precursors while equation keeps the full "
                "KEGG stoichiometry."
            ),
        )
        for index, reaction in enumerate(reactions, 1)
    ]
    return SynthesisPath(
        source="kegg",
        mechanism=_mechanism(mechanism),
        path_status=status,
        path_issue=issue,
        target_compound_id=genome.kegg.compound_id,
        entry_precursors=sorted({
            precursor
            for reaction in reactions
            for precursor in _direct_precursors(reaction, genome.kegg.compound_id)
        } or set(genome.kegg.direct_precursor_ids)),
        evidence_refs=[reaction.reaction_id for reaction in reactions],
        steps=steps,
        confidence_note=(
            "Ranked KEGG-derived core reaction context. This is auditable "
            "biochemical evidence to construct candidate chassis routes."
        ),
    )


def _ranked_core_reactions(reactions, genome: GenomePathResult):
    ranked = sorted(
        reactions,
        key=lambda reaction: _reaction_rank(reaction, genome),
    )
    return ranked[:MAX_KEGG_PATH_STEPS]


def _reaction_rank(reaction, genome: GenomePathResult) -> tuple:
    present_kos = set(genome.kegg.present_kos)
    supported = reaction.reaction_id in set(genome.kegg.supported_reaction_ids)
    direct = _direct_precursors(reaction, genome.kegg.compound_id)
    gene_ids = _gene_ids_for_reaction(reaction, genome)
    return (
        0 if supported else 1,
        0 if (set(reaction.ko_ids) & present_kos) else 1,
        0 if gene_ids else 1,
        0 if reaction.ec_numbers else 1,
        abs(len(direct) - 1),
        len(direct),
        reaction.reaction_id,
    )


def _kegg_path_status(reactions, genome: GenomePathResult) -> tuple[PathStatus, str]:
    if len(reactions) > MAX_KEGG_PATH_STEPS:
        return (
            "overbroad",
            f"ranked_top_{MAX_KEGG_PATH_STEPS}_of_{len(reactions)}_target_producing_reactions",
        )
    if genome.kegg.supported_reaction_ids:
        return "candidate_core", ""
    return "candidate_context", "no_chassis_supported_kegg_reaction"


def _direct_precursors(reaction, target: str | None) -> list[str]:
    direct = [
        item for item in reaction.precursor_compound_ids
        if item != target and item not in _CURRENCY_COMPOUNDS
    ]
    if direct:
        return list(dict.fromkeys(direct))
    return list(dict.fromkeys(
        item for item in reaction.precursor_compound_ids if item != target
    ))


def _gene_ids_for_reaction(reaction, genome: GenomePathResult) -> list[str]:
    genes: list[str] = []
    reaction_ecs = set(reaction.ec_numbers)
    for producer in genome.gem.producers:
        if producer.reaction_id == reaction.reaction_id:
            genes.extend(producer.gene_ids)
        elif reaction_ecs and reaction_ecs & set(producer.ec_codes):
            genes.extend(producer.gene_ids)
    return sorted(set(genes))


def _mechanism(value: str) -> PathMechanism:
    if value == "denovo&biotransform":
        return "denovo&biotransform"
    if value == "biotransform":
        return "biotransform"
    return "denovo"


def _list(value) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(";") if part.strip()]
    return [str(item) for item in value if str(item)]


def _split_refs(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [part.strip() for part in str(value).split(";") if part.strip()]


def _native_status(value) -> str:
    normalized = str(value or "native_unverified").strip().lower()
    aliases = {
        "native": "native_supported",
        "supported": "native_supported",
        "native_supported": "native_supported",
        "unverified": "native_unverified",
        "native_unverified": "native_unverified",
        "heterologous": "heterologous",
        "engineered": "heterologous",
        "unknown": "unknown",
    }
    return aliases.get(normalized, "unknown")
