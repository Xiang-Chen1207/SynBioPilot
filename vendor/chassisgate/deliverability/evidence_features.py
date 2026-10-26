"""Generic evidence-quality features for fusion and ten-class gating.

The features in this module are derived only from channel outputs: literature
status/supports, GEM/KEGG traces, and FBA traces.  Candidate names, chassis
names, pair ids, and gold labels are intentionally not inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .literature import LiteratureAssessment
from .mechanistic_models import FBAPathResult, GenomePathResult


EngineeringGap = Literal["none", "single", "multi"]


@dataclass(frozen=True)
class EvidenceFeatures:
    literature_quality: float
    genome_positive_quality: float
    genome_negative_quality: float
    fba_positive_quality: float
    fba_negative_quality: float
    route_presence: float
    route_completion_ratio: float
    ko_completion_ratio: float
    missing_step_burden: float
    supported_reaction_count: int
    producing_reaction_count: int
    direct_precursor_count: int
    broad_route: bool
    route_completeness: float
    terminal_evidence: float
    precursor_dependency_score: float
    native_route_support: float
    unsupported_known_route_score: float
    engineering_evidence_score: float
    route_absence_score: float
    known_route_evidence: float
    engineering_gap: EngineeringGap

    @property
    def has_mechanistic_classifying_evidence(self) -> bool:
        return max(
            self.genome_positive_quality,
            self.genome_negative_quality,
            self.fba_positive_quality,
            self.fba_negative_quality,
            self.route_presence,
            self.unsupported_known_route_score,
        ) >= 0.25

    @property
    def path_light_positive_allowed(self) -> bool:
        if self.fba_positive_quality >= 0.65 and self.genome_positive_quality >= 0.20:
            return True
        return (
            self.native_route_support >= 0.55
            and (
                self.fba_positive_quality >= 0.65
                or self.literature_quality >= 0.70
                or self.supported_reaction_count >= 2
            )
        )

    @property
    def strong_denovo_quality(self) -> bool:
        return (
            self.native_route_support >= 0.70
            and self.terminal_evidence >= 0.30
            and self.precursor_dependency_score < 0.65
        )


def derive_evidence_features(
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
) -> EvidenceFeatures:
    """Summarize channel quality without identity-based rules."""
    literature_quality = _literature_quality(literature)
    genome_positive_quality = _genome_positive_quality(genome)
    genome_negative_quality = _genome_negative_quality(genome)
    fba_positive_quality = _fba_positive_quality(fba)
    fba_negative_quality = _fba_negative_quality(fba)
    route_presence = _route_presence(genome, literature)
    route_completion_ratio = _route_completion_ratio(genome)
    ko_completion_ratio = _ko_completion_ratio(genome)
    missing_step_burden = 1.0 - ko_completion_ratio
    supported_reaction_count = len(genome.kegg.supported_reaction_ids)
    producing_reaction_count = len(genome.kegg.producing_reaction_ids)
    direct_precursor_count = len(genome.kegg.direct_precursor_ids)
    broad_route = producing_reaction_count >= 50 or direct_precursor_count >= 40
    terminal_evidence = _terminal_evidence(genome, fba)
    precursor_dependency_score = _precursor_dependency_score(
        literature,
        genome,
        fba,
        terminal_evidence=terminal_evidence,
        route_completion_ratio=route_completion_ratio,
        missing_step_burden=missing_step_burden,
        broad_route=broad_route,
    )
    route_completeness = _route_completeness(
        genome,
        literature_quality=literature_quality,
        genome_positive_quality=genome_positive_quality,
        fba_positive_quality=fba_positive_quality,
        route_completion_ratio=route_completion_ratio,
        ko_completion_ratio=ko_completion_ratio,
        broad_route=broad_route,
    )
    native_route_support = _native_route_support(
        literature_quality=literature_quality,
        genome_positive_quality=genome_positive_quality,
        fba_positive_quality=fba_positive_quality,
        route_completeness=route_completeness,
        terminal_evidence=terminal_evidence,
        route_completion_ratio=route_completion_ratio,
        broad_route=broad_route,
    )
    unsupported_known_route_score = _unsupported_known_route_score(
        genome,
        route_presence=route_presence,
        route_completion_ratio=route_completion_ratio,
        ko_completion_ratio=ko_completion_ratio,
        genome_positive_quality=genome_positive_quality,
        fba_positive_quality=fba_positive_quality,
        literature_quality=literature_quality,
    )
    engineering_evidence_score = _engineering_evidence_score(
        literature,
        genome,
        fba,
        terminal_evidence=terminal_evidence,
        route_completion_ratio=route_completion_ratio,
        missing_step_burden=missing_step_burden,
        broad_route=broad_route,
    )
    route_absence_score = _route_absence_score(
        genome,
        route_presence=route_presence,
        unsupported_known_route_score=unsupported_known_route_score,
        engineering_evidence_score=engineering_evidence_score,
    )
    known_route_evidence = _known_route_evidence(genome, literature)
    engineering_gap = _engineering_gap(
        literature,
        genome,
        fba,
        route_completion_ratio=route_completion_ratio,
        missing_step_burden=missing_step_burden,
        broad_route=broad_route,
        engineering_evidence_score=engineering_evidence_score,
    )
    return EvidenceFeatures(
        literature_quality=literature_quality,
        genome_positive_quality=genome_positive_quality,
        genome_negative_quality=genome_negative_quality,
        fba_positive_quality=fba_positive_quality,
        fba_negative_quality=fba_negative_quality,
        route_presence=route_presence,
        route_completion_ratio=route_completion_ratio,
        ko_completion_ratio=ko_completion_ratio,
        missing_step_burden=missing_step_burden,
        supported_reaction_count=supported_reaction_count,
        producing_reaction_count=producing_reaction_count,
        direct_precursor_count=direct_precursor_count,
        broad_route=broad_route,
        route_completeness=route_completeness,
        terminal_evidence=terminal_evidence,
        precursor_dependency_score=precursor_dependency_score,
        native_route_support=native_route_support,
        unsupported_known_route_score=unsupported_known_route_score,
        engineering_evidence_score=engineering_evidence_score,
        route_absence_score=route_absence_score,
        known_route_evidence=known_route_evidence,
        engineering_gap=engineering_gap,
    )


def _literature_quality(literature: LiteratureAssessment) -> float:
    if literature.status != "ok":
        return 0.0
    support = max(
        literature.denovo_support,
        literature.biotransform_support,
        literature.non_native_support,
        literature.engineering_support,
        literature.mean_confidence,
    )
    grounding = min(1.0, literature.grounded_runs / max(1, literature.retrieved_count))
    return _clamp(0.35 + 0.45 * support + 0.20 * grounding)


def _genome_positive_quality(genome: GenomePathResult) -> float:
    if genome.state != "supports_production":
        return 0.0
    supported = len(genome.kegg.supported_reaction_ids)
    gene_producers = genome.gem.gene_associated_producer_count
    directional = genome.gem.directional_producer_count
    coverage = _route_completion_ratio(genome)
    quality = 0.20
    if supported:
        quality += min(0.25, 0.05 * supported)
    if gene_producers:
        quality += 0.25
    elif directional:
        quality += 0.10
    quality += 0.20 * coverage
    if genome.conflict:
        quality -= 0.10
    return _clamp(quality)


def _genome_negative_quality(genome: GenomePathResult) -> float:
    if genome.state != "assessed_negative":
        return 0.0
    quality = 0.35
    if genome.kegg.status == "ok" and genome.kegg.candidate_kos:
        quality += 0.25
    if genome.gem.mapping_status == "mapped":
        quality += 0.20
    if genome.gem.directional_producer_count == 0:
        quality += 0.10
    if genome.kegg.producing_reaction_ids:
        quality += 0.10
    return _clamp(quality)


def _fba_positive_quality(fba: FBAPathResult) -> float:
    if fba.state != "supports_production":
        return 0.0
    if fba.mapping_status != "mapped":
        return 0.25
    flux = fba.default_medium_max_flux or 0.0
    threshold = max(fba.flux_threshold, 1e-12)
    if fba.candidate_class == "denovo":
        flux_bonus = min(0.20, max(0.0, flux / (100.0 * threshold)) * 0.20)
        return _clamp(0.70 + flux_bonus)
    if fba.candidate_class == "biotransform_candidate":
        rescue_bonus = min(0.20, 0.05 * len(fba.rescues))
        return _clamp(0.60 + rescue_bonus)
    return 0.35


def _fba_negative_quality(fba: FBAPathResult) -> float:
    if fba.state != "assessed_negative":
        return 0.0
    if fba.mapping_status != "mapped":
        return 0.15
    quality = 0.30
    if fba.tested_precursor_ids:
        quality += 0.20
    if fba.growth_constraint_applied:
        quality += 0.10
    if fba.default_medium_max_flux is not None:
        quality += 0.10
    if not fba.target_import_blocked:
        quality -= 0.10
    return _clamp(quality)


def _route_presence(
    genome: GenomePathResult,
    literature: LiteratureAssessment,
) -> float:
    score = 0.0
    if genome.kegg.producing_reaction_ids:
        score += 0.45
    if genome.kegg.producing_reactions:
        score += 0.20
    if genome.kegg.ec_numbers or genome.kegg.candidate_kos:
        score += 0.20
    if literature.synthesis_path:
        score += 0.15
    return _clamp(score)


def _route_completion_ratio(genome: GenomePathResult) -> float:
    if genome.kegg.reaction_coverage is not None:
        return _clamp(genome.kegg.reaction_coverage)
    producing = len(genome.kegg.producing_reaction_ids)
    if producing:
        return _clamp(len(genome.kegg.supported_reaction_ids) / producing)
    return 0.0


def _ko_completion_ratio(genome: GenomePathResult) -> float:
    total = len(genome.kegg.present_kos) + len(genome.kegg.missing_kos)
    if total:
        return _clamp(len(genome.kegg.present_kos) / total)
    if genome.kegg.supported_reaction_ids:
        return 0.50
    return 0.0


def _route_completeness(
    genome: GenomePathResult,
    *,
    literature_quality: float,
    genome_positive_quality: float,
    fba_positive_quality: float,
    route_completion_ratio: float,
    ko_completion_ratio: float,
    broad_route: bool,
) -> float:
    coverage = route_completion_ratio
    if broad_route and genome.kegg.supported_reaction_ids:
        coverage = max(
            coverage,
            min(0.70, 0.30 + 0.01 * len(genome.kegg.supported_reaction_ids)),
        )
    score = (
        0.30 * coverage
        + 0.15 * ko_completion_ratio
        + 0.25 * genome_positive_quality
        + 0.20 * fba_positive_quality
        + 0.10 * literature_quality
    )
    return _clamp(score)


def _terminal_evidence(genome: GenomePathResult, fba: FBAPathResult) -> float:
    score = 0.0
    if genome.gem.gene_associated_producer_count:
        score += 0.35
    if genome.kegg.supported_reaction_ids:
        score += 0.30
    if genome.kegg.ec_numbers:
        score += 0.15
    if fba.candidate_class == "biotransform_candidate" and fba.rescues:
        score += 0.20
    return _clamp(score)


def _precursor_dependency_score(
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
    *,
    terminal_evidence: float,
    route_completion_ratio: float,
    missing_step_burden: float,
    broad_route: bool,
) -> float:
    if fba.candidate_class == "biotransform_candidate" and fba.rescues:
        if literature.precursor_available == "available_by_simple_feeding":
            return 0.95
        if literature.precursor_available == "requires_custom_synthesis":
            return 0.85
        if broad_route and literature.status != "ok":
            return 0.45
        return 0.80
    if literature.precursor_available == "available_by_simple_feeding":
        return 0.90
    if literature.precursor_available == "requires_custom_synthesis":
        return 0.80
    base = 0.20 if literature.precursor_available == "available_in_medium" else 0.0
    if broad_route:
        return base
    if (
        literature.status == "ok"
        and
        genome.kegg.direct_precursor_ids
        and terminal_evidence >= 0.40
        and route_completion_ratio >= 0.50
        and missing_step_burden >= 0.45
    ):
        base = max(base, 0.75)
    elif (
        genome.kegg.direct_precursor_ids
        and terminal_evidence >= 0.55
        and route_completion_ratio < 0.55
    ):
        base = max(base, 0.55)
    return _clamp(base)


def _native_route_support(
    *,
    literature_quality: float,
    genome_positive_quality: float,
    fba_positive_quality: float,
    route_completeness: float,
    terminal_evidence: float,
    route_completion_ratio: float,
    broad_route: bool,
) -> float:
    score = (
        0.25 * literature_quality
        + 0.25 * genome_positive_quality
        + 0.25 * fba_positive_quality
        + 0.20 * route_completeness
        + 0.05 * terminal_evidence
    )
    if broad_route and route_completion_ratio > 0:
        score += 0.10
    return _clamp(score)


def _unsupported_known_route_score(
    genome: GenomePathResult,
    *,
    route_presence: float,
    route_completion_ratio: float,
    ko_completion_ratio: float,
    genome_positive_quality: float,
    fba_positive_quality: float,
    literature_quality: float,
) -> float:
    if route_presence <= 0:
        return 0.0
    positive = max(genome_positive_quality, fba_positive_quality, literature_quality)
    incomplete = max(0.0, 1.0 - max(route_completion_ratio, ko_completion_ratio))
    score = 0.55 * route_presence + 0.35 * incomplete - 0.45 * positive
    if genome.state == "assessed_negative":
        score += 0.20
    elif genome.state == "unknown":
        score -= 0.25
    if genome.kegg.producing_reaction_ids and not genome.kegg.supported_reaction_ids:
        score += 0.15
    return _clamp(score)


def _engineering_evidence_score(
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
    *,
    terminal_evidence: float,
    route_completion_ratio: float,
    missing_step_burden: float,
    broad_route: bool,
) -> float:
    score = 0.0
    if literature.engineering_support > 0:
        score += 0.70 * literature.engineering_support
    if fba.candidate_class == "biotransform_candidate" and fba.rescues:
        score += 0.25
    if broad_route:
        return _clamp(score)
    if (
        genome.state == "supports_production"
        and terminal_evidence >= 0.40
        and route_completion_ratio > 0
        and missing_step_burden >= 0.55
    ):
        score += 0.20
    if (
        genome.state == "assessed_negative"
        and genome.kegg.supported_reaction_ids
        and terminal_evidence >= 0.35
    ):
        score += 0.25
    return _clamp(score)


def _route_absence_score(
    genome: GenomePathResult,
    *,
    route_presence: float,
    unsupported_known_route_score: float,
    engineering_evidence_score: float,
) -> float:
    if route_presence <= 0:
        return 0.0
    if genome.state == "assessed_negative":
        return _clamp(max(unsupported_known_route_score, 0.55))
    if genome.state == "unknown" and engineering_evidence_score < 0.35:
        return _clamp(unsupported_known_route_score * 0.55)
    return _clamp(unsupported_known_route_score * 0.35)


def _known_route_evidence(
    genome: GenomePathResult,
    literature: LiteratureAssessment,
) -> float:
    score = 0.0
    if genome.kegg.producing_reaction_ids:
        score += 0.35
    if genome.kegg.producing_reactions:
        score += 0.20
    if genome.kegg.candidate_kos or genome.kegg.ec_numbers:
        score += 0.20
    if literature.synthesis_path:
        score += 0.25
    return _clamp(score)


def _engineering_gap(
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
    *,
    route_completion_ratio: float,
    missing_step_burden: float,
    broad_route: bool,
    engineering_evidence_score: float,
) -> EngineeringGap:
    supported = len(genome.kegg.supported_reaction_ids)
    producing = len(genome.kegg.producing_reaction_ids)
    missing = len(genome.kegg.missing_kos)
    if fba.candidate_class == "biotransform_candidate" and fba.rescues and not broad_route:
        return "single"
    if broad_route:
        return "none"
    if supported >= 3 and route_completion_ratio >= 0.95:
        return "none"
    if engineering_evidence_score < 0.20 and literature.engineering_support < 0.35:
        return "none"
    if supported and missing:
        if supported <= 3 and missing <= 4:
            return "single"
        if route_completion_ratio >= 0.45 and missing_step_burden <= 0.60:
            return "single"
        return "multi"
    if producing and missing and not supported:
        return "single" if producing == 1 and missing <= 2 else "multi"
    if producing and not supported and genome.kegg.status == "ok":
        return "multi"
    return "none"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
