"""Frozen route-first ten-class gate for the integrated system.

Evaluation has ten classes:

    A1 A2 B1 B2 B3 C1 C2 D1 D2 E

E is retained internally as E1 (biological evidence insufficient) or E2
(technical/model/mapping unavailable) for selective-prediction analysis.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field

from .evidence_features import EvidenceFeatures, derive_evidence_features
from .literature import LiteratureAssessment
from .mechanistic_models import FBAPathResult, GenomePathResult
from .synthesis_path import SynthesisPath, choose_synthesis_path


FineClass = Literal[
    "A1", "A2", "B1", "B2", "B3",
    "C1", "C2", "D1", "D2", "E1", "E2",
]
EvalClass = Literal[
    "A1", "A2", "B1", "B2", "B3",
    "C1", "C2", "D1", "D2", "E",
]
CoverageLevel = Literal["complete", "partial", "terminal_only", "none", "unknown"]


DEFAULT_DELIVERABLE_CLASSES = frozenset({"A1", "A2", "B1", "B2"})


CLASS_DEFINITIONS = {
    "A1": "native de novo, short route",
    "A2": "native de novo, multi-step route",
    "B1": "native biotransformation, precursor in medium",
    "B2": "native biotransformation, fed precursor",
    "B3": "possible biotransformation, precursor-limited",
    "C1": "non-native, route exists in other organisms",
    "C2": "non-native, no known biological route",
    "D1": "engineering gap, one missing step",
    "D2": "engineering gap, few missing steps",
    "E1": "abstain, silent/conflicting/insufficient evidence",
    "E2": "abstain, technical/model/mapping inputs unavailable",
}


def derive_deliverable(
    fine_class: str,
    *,
    deliverable_classes: Iterable[str] = DEFAULT_DELIVERABLE_CLASSES,
) -> int | None:
    """Derive the binary policy label from a fine ten-class label."""
    if fine_class in {"E", "E1", "E2"}:
        return None
    return int(fine_class in set(deliverable_classes))

class RouteAtom(BaseModel):
    route_ref: list[str] = Field(default_factory=list)
    terminal_enzyme: list[str] = Field(default_factory=list)
    entry_precursor: list[str] = Field(default_factory=list)
    chassis_step_coverage: CoverageLevel = "unknown"
    engineering_required: bool = False


class GateDecision(BaseModel):
    fine_class: FineClass
    secondary_class: FineClass | None = None
    pred_10class: EvalClass
    class_label: str
    mechanism: Literal[
        "denovo", "biotransform", "non_native",
        "denovo&biotransform", "abstain",
    ]
    pred_deliverable: int | None
    abstain: bool
    abstain_subtype: Literal["E1", "E2"] | None = None
    route: RouteAtom
    synthesis_path: SynthesisPath | None = None
    rationale: str


def apply_ten_class_gate(
    *,
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
    semantic_label: str,
    semantic_scores: dict[str, float],
    conflict: str | None,
    classifying_coverage: float,
    deliverable_classes: Iterable[str] = DEFAULT_DELIVERABLE_CLASSES,
    require_positive_synthesis_path: bool = True,
    evidence_features: EvidenceFeatures | None = None,
    evidence_abstain_coverage_threshold: float = 0.30,
) -> GateDecision:
    route = _route_atom(literature, genome, fba)
    features = evidence_features or derive_evidence_features(literature, genome, fba)
    technical_unavailable = _technical_unavailable(literature, genome, fba)

    if (
        technical_unavailable
        and classifying_coverage < evidence_abstain_coverage_threshold
        and not features.has_mechanistic_classifying_evidence
    ):
        fine = "E2"
        return _decision(
            fine, None, "abstain", semantic_scores, route,
            "Technical inputs are unavailable.",
            deliverable_classes=deliverable_classes,
        )

    if (
        literature.engineering_support >= 0.50
        and _strong_mechanistic_denovo_overrides_engineering_literature(
            genome,
            fba,
            features,
        )
    ):
        return _decision(
            "A2", None, "denovo", semantic_scores, route,
            "Strong mechanistic denovo evidence overrides weakly grounded engineering/non-native literature.",
            synthesis_path=choose_synthesis_path(
                literature=literature,
                genome=genome,
                mechanism="denovo",
            ),
            deliverable_classes=deliverable_classes,
        )

    if _strong_no_route_nonnative_is_absent_not_engineering(literature, features):
        return _decision(
            "C1", None, "non_native", semantic_scores, route,
            "Strong non-native literature without a supported chassis route is treated as route absence rather than an actionable engineering gap.",
            deliverable_classes=deliverable_classes,
        )

    if literature.engineering_support >= 0.50:
        fine = "D1" if _terminal_gap_only(genome) else "D2"
        return _decision(
            fine, None, "non_native", semantic_scores, route,
            CLASS_DEFINITIONS[fine],
            deliverable_classes=deliverable_classes,
        )

    if (
        semantic_label in {"denovo", "abstain"}
        and features.precursor_dependency_score >= 0.70
        and genome.state == "supports_production"
        and _strong_biotransform_evidence(literature, fba, features)
        and not _broad_native_route_overrides_biotransform(literature, features)
    ):
        return _decision(
            "B2", None, "biotransform", semantic_scores, route,
            "Terminal conversion evidence with feedable precursor dependency.",
            synthesis_path=choose_synthesis_path(
                literature=literature,
                genome=genome,
                mechanism="biotransform",
            ),
            deliverable_classes=deliverable_classes,
        )

    if _precursor_dependent_terminal_b2(
        semantic_label,
        literature=literature,
        genome=genome,
        fba=fba,
        features=features,
    ):
        return _decision(
            "B2", None, "biotransform", semantic_scores, route,
            "Terminal route support is precursor-dependent and lacks native denovo support.",
            synthesis_path=choose_synthesis_path(
                literature=literature,
                genome=genome,
                mechanism="biotransform",
            ),
            deliverable_classes=deliverable_classes,
        )

    if (
        semantic_label in {"denovo", "abstain"}
        and
        genome.state == "supports_production"
        and features.route_completion_ratio >= 0.95
        and features.supported_reaction_count >= 3
    ):
        return _decision(
            "A2", None, "denovo", semantic_scores, route,
            "Target-producing route reactions are supported, but evidence is not strong enough for A1.",
            synthesis_path=choose_synthesis_path(
                literature=literature,
                genome=genome,
                mechanism="denovo",
            ),
            deliverable_classes=deliverable_classes,
        )

    positive_gap_class = _positive_gap_override(semantic_label, features)
    if positive_gap_class is not None:
        return _decision(
            positive_gap_class, None, "non_native", semantic_scores, route,
            CLASS_DEFINITIONS[positive_gap_class],
            deliverable_classes=deliverable_classes,
        )

    sparse_single_producer_gap = _sparse_single_producer_positive_gap(
        semantic_label,
        literature=literature,
        genome=genome,
        fba=fba,
        features=features,
    )
    if sparse_single_producer_gap is not None:
        return _decision(
            sparse_single_producer_gap, None, "non_native", semantic_scores, route,
            CLASS_DEFINITIONS[sparse_single_producer_gap],
            deliverable_classes=deliverable_classes,
        )

    gap_class = _generic_engineering_gap_class(literature, features)
    low_burden_single_gap = (
        gap_class == "D1"
        and features.missing_step_burden < 0.45
        and features.native_route_support < 0.35
    )
    if (
        gap_class is not None
        and semantic_label in {"non_native", "abstain"}
        and not low_burden_single_gap
    ):
        return _decision(
            gap_class, None, "non_native", semantic_scores, route,
            CLASS_DEFINITIONS[gap_class],
            deliverable_classes=deliverable_classes,
        )

    if conflict:
        return _decision(
            "E1", None, "abstain", semantic_scores, route,
            "Biological evidence is contradictory.",
            deliverable_classes=deliverable_classes,
        )

    if (
        genome.state == "supports_production"
        and _broad_native_route_overrides_biotransform(literature, features)
    ):
        return _decision(
            "A1", None, "denovo", semantic_scores, route,
            "Broad native route evidence overrides weak terminal biotransform literature.",
            synthesis_path=choose_synthesis_path(
                literature=literature,
                genome=genome,
                mechanism="denovo",
            ),
            deliverable_classes=deliverable_classes,
        )

    if classifying_coverage < evidence_abstain_coverage_threshold:
        if _path_light_denovo_literature_a1(
            literature,
            genome,
            fba,
            features,
            classifying_coverage=classifying_coverage,
            evidence_abstain_coverage_threshold=(
                evidence_abstain_coverage_threshold
            ),
        ):
            return _decision(
                "A1", None, "denovo", semantic_scores, route,
                "High-consensus denovo literature is allowed as path-light A1 when route and model channels are absent.",
                deliverable_classes=deliverable_classes,
            )
        if _unsupported_no_route_biotransform_c1(literature, features):
            return _decision(
                "C1", None, "non_native", semantic_scores, route,
                "Biotransform literature is weak and lacks any chassis route support.",
                deliverable_classes=deliverable_classes,
            )
        if _consistent_no_route_biotransform_b2(literature, features):
            return _decision(
                "B2", None, "biotransform", semantic_scores, route,
                "Consistent biotransform literature supports feedable terminal conversion despite missing route evidence.",
                deliverable_classes=deliverable_classes,
            )
        if _mixed_positive_no_route_biotransform_b2(literature, features):
            return _decision(
                "B2", None, "biotransform", semantic_scores, route,
                "Mixed low-coverage literature supports a feedable biotransformation without engineering evidence.",
                deliverable_classes=deliverable_classes,
            )
        literature_fallback = _strong_literature_fallback_class(literature, features)
        if literature_fallback is not None:
            return _decision(
                literature_fallback,
                None,
                _mechanism_for_class(literature_fallback),
                semantic_scores,
                route,
                f"{CLASS_DEFINITIONS[literature_fallback]} High-quality literature evidence is allowed without a mechanistic path when no channel conflicts.",
                deliverable_classes=deliverable_classes,
            )
        weak_literature_fallback = _weak_literature_e_release_class(
            literature,
            features,
        )
        if weak_literature_fallback is not None:
            return _decision(
                weak_literature_fallback,
                None,
                _mechanism_for_class(weak_literature_fallback),
                semantic_scores,
                route,
                f"{CLASS_DEFINITIONS[weak_literature_fallback]} Selective low-coverage literature evidence is allowed as an E fallback.",
                deliverable_classes=deliverable_classes,
            )
        if genome.state == "supports_production" and _a1_quality(literature, features):
            return _decision(
                "A1", None, "denovo", semantic_scores, route,
                CLASS_DEFINITIONS["A1"],
                synthesis_path=choose_synthesis_path(
                    literature=literature,
                    genome=genome,
                    mechanism="denovo",
                ),
                deliverable_classes=deliverable_classes,
            )
        if (
            genome.state == "supports_production"
            and features.engineering_gap == "single"
            and features.engineering_evidence_score >= 0.20
            and features.route_presence >= 0.65
            and features.native_route_support < 0.25
        ):
            return _decision(
                "D1", None, "non_native", semantic_scores, route,
                CLASS_DEFINITIONS["D1"],
                deliverable_classes=deliverable_classes,
            )
        if (
            genome.state == "supports_production"
            and features.known_route_evidence >= 0.70
            and features.native_route_support < 0.25
            and features.route_completion_ratio >= 0.95
            and features.supported_reaction_count <= 1
            and features.direct_precursor_count <= 1
        ):
            return _decision(
                "D2", None, "non_native", semantic_scores, route,
                CLASS_DEFINITIONS["D2"],
                deliverable_classes=deliverable_classes,
            )
        if (
            genome.state == "supports_production"
            and features.known_route_evidence >= 0.70
            and features.native_route_support < 0.25
            and features.missing_step_burden < 0.45
        ):
            return _decision(
                "C1", None, "non_native", semantic_scores, route,
                CLASS_DEFINITIONS["C1"],
                deliverable_classes=deliverable_classes,
            )
        if (
            genome.state == "unknown"
            and features.route_presence >= 0.60
            and features.unsupported_known_route_score >= 0.60
            and features.supported_reaction_count == 0
            and (
                (
                    features.producing_reaction_count <= 2
                    and features.direct_precursor_count <= 2
                )
                or _moderate_route_only_absence(features)
            )
            and features.terminal_evidence <= 0.15
        ):
            return _decision(
                "C1", None, "non_native", semantic_scores, route,
                "Known route is present, but no chassis support was found.",
                deliverable_classes=deliverable_classes,
            )
        if features.precursor_dependency_score >= 0.70 and genome.state == "supports_production":
            return _decision(
                "B2", None, "biotransform", semantic_scores, route,
                "Terminal conversion evidence with feedable precursor dependency.",
                synthesis_path=choose_synthesis_path(
                    literature=literature,
                    genome=genome,
                    mechanism="biotransform",
                ),
                deliverable_classes=deliverable_classes,
            )
        if genome.state == "assessed_negative":
            fine = _non_native_class(literature, genome, fba, features)
            return _decision(
                fine, None, "non_native", semantic_scores, route,
                CLASS_DEFINITIONS[fine],
                deliverable_classes=deliverable_classes,
            )
        if (
            genome.state == "supports_production"
            and (
                features.native_route_support >= 0.42
                or (
                    features.broad_route
                    and features.supported_reaction_count >= 3
                    and features.route_presence >= 0.65
                )
            )
            and (
                features.broad_route
                or literature.status == "ok"
                or fba.state == "supports_production"
            )
        ):
            fine = "A2"
            return _decision(
                fine, None, "denovo", semantic_scores, route,
                "Mechanistic route evidence is present but classifying coverage is partial.",
                synthesis_path=choose_synthesis_path(
                    literature=literature,
                    genome=genome,
                    mechanism="denovo",
                ),
                deliverable_classes=deliverable_classes,
            )
        if _low_coverage_fba_denovo_gem_producer_a2(
            genome,
            fba,
            features,
            classifying_coverage=classifying_coverage,
            evidence_abstain_coverage_threshold=(
                evidence_abstain_coverage_threshold
            ),
        ):
            return _decision(
                "A2", None, "denovo", semantic_scores, route,
                "FBA supports denovo production and the GEM has a mapped producer despite incomplete genome route support.",
                synthesis_path=choose_synthesis_path(
                    literature=literature,
                    genome=genome,
                    mechanism="denovo",
                ),
                deliverable_classes=deliverable_classes,
            )
        if features.route_absence_score >= 0.55:
            fba_denovo_override = _fba_denovo_overrides_partial_genome_absence(
                literature,
                genome,
                fba,
                features,
            )
            if fba_denovo_override is not None:
                return _decision(
                    fba_denovo_override,
                    None,
                    "denovo",
                    semantic_scores,
                    route,
                    "FBA supports de-novo production and the genome absence signal is partial rather than decisive.",
                    deliverable_classes=deliverable_classes,
                )
            fine = _unsupported_route_class(features)
            return _decision(
                fine, None, "non_native", semantic_scores, route,
                CLASS_DEFINITIONS[fine],
                deliverable_classes=deliverable_classes,
            )
        compact_burden = _compact_incomplete_route_burden(
            features,
            fba,
            classifying_coverage=classifying_coverage,
            evidence_abstain_coverage_threshold=(
                evidence_abstain_coverage_threshold
            ),
        )
        if compact_burden is not None:
            return _decision(
                compact_burden, None, "non_native", semantic_scores, route,
                CLASS_DEFINITIONS[compact_burden],
                deliverable_classes=deliverable_classes,
            )
        if _chemical_negative_no_route_c2(literature, genome, fba, features):
            return _decision(
                "C2", None, "non_native", semantic_scores, route,
                CLASS_DEFINITIONS["C2"],
                deliverable_classes=deliverable_classes,
            )
        return _decision(
            "E1", None, "abstain", semantic_scores, route,
            "Biological evidence is insufficient.",
            deliverable_classes=deliverable_classes,
        )

    if semantic_label == "denovo&biotransform":
        first = _denovo_class(literature, genome, fba, features)
        second = _biotransform_class(literature, fba, features)
        path = choose_synthesis_path(
            literature=literature,
            genome=genome,
            mechanism="denovo&biotransform",
        )
        if (
            path is None
            and require_positive_synthesis_path
            and not features.path_light_positive_allowed
        ):
            return _decision(
                "E1", None, "abstain", semantic_scores, route,
                "Positive denovo/biotransform evidence lacks a concrete synthesis path from full text or KEGG.",
                synthesis_path=None,
                deliverable_classes=deliverable_classes,
            )
        if second in {"E1", "E2"}:
            return _decision(
                first, None, "denovo", semantic_scores, route,
                CLASS_DEFINITIONS[first],
                synthesis_path=path,
                deliverable_classes=deliverable_classes,
            )
        return _decision(
            first, second, "denovo&biotransform", semantic_scores, route,
            f"{CLASS_DEFINITIONS[first]}; {CLASS_DEFINITIONS[second]}",
            synthesis_path=path,
            deliverable_classes=deliverable_classes,
        )
    if semantic_label == "denovo":
        if (
            features.precursor_dependency_score >= 0.70
            and fba.candidate_class != "denovo"
            and not _broad_native_route_overrides_biotransform(literature, features)
        ):
            fine = "B2"
            mechanism = "biotransform"
            path = choose_synthesis_path(
                literature=literature,
                genome=genome,
                mechanism=mechanism,
            )
            return _decision(
                fine, None, mechanism, semantic_scores, route,
                CLASS_DEFINITIONS[fine],
                synthesis_path=path,
                deliverable_classes=deliverable_classes,
            )
        fine = _denovo_class(literature, genome, fba, features)
    elif semantic_label == "biotransform":
        fine = _biotransform_class(literature, fba, features)
        if fine == "C1":
            return _decision(
                fine, None, "non_native", semantic_scores, route,
                CLASS_DEFINITIONS[fine],
                deliverable_classes=deliverable_classes,
            )
    elif semantic_label == "non_native":
        fine = _non_native_class(literature, genome, fba, features)
    else:
        fallback = _abstain_fallback_class(literature, genome, fba, features)
        if fallback is not None:
            return _decision(
                fallback,
                None,
                _mechanism_for_class(fallback),
                semantic_scores,
                route,
                CLASS_DEFINITIONS[fallback],
                deliverable_classes=deliverable_classes,
            )
        fine = "E2" if technical_unavailable else "E1"
    mechanism = _mechanism_for_class(fine) if fine not in {"E1", "E2"} else "abstain"
    path = choose_synthesis_path(
        literature=literature,
        genome=genome,
        mechanism=mechanism,
    )
    if (
        mechanism in {"denovo", "biotransform"}
        and path is None
        and require_positive_synthesis_path
        and not features.path_light_positive_allowed
        and not _strong_literature_path_light(literature, mechanism, features)
    ):
        return _decision(
            "E1", None, "abstain", semantic_scores, route,
            f"Positive {mechanism} evidence lacks a concrete synthesis path from full text or KEGG.",
            synthesis_path=None,
            deliverable_classes=deliverable_classes,
        )
    if mechanism in {"denovo", "biotransform"} and path is None:
        rationale = (
            f"{CLASS_DEFINITIONS[fine]} Path-light validation mode: "
            "positive channel evidence is allowed without a concrete synthesis path."
        )
    else:
        rationale = CLASS_DEFINITIONS[fine]
    return _decision(
        fine, None, mechanism, semantic_scores, route, rationale,
        synthesis_path=path,
        deliverable_classes=deliverable_classes,
    )


def _denovo_class(
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
    features: EvidenceFeatures,
) -> FineClass:
    if (
        genome.state == "supports_production"
        and _a1_quality(literature, features)
    ):
        return "A1"
    return "A2"


def _biotransform_class(
    literature: LiteratureAssessment,
    fba: FBAPathResult,
    features: EvidenceFeatures,
) -> FineClass:
    if _broad_native_route_overrides_biotransform(literature, features):
        return "A1"
    if _native_route_overrides_weak_biotransform(literature, features):
        return "A1" if _strong_broad_native_route(features) else "A2"
    if (
        literature.semantic_source == "biotransform"
        and literature.precursor_available == "available_in_medium"
    ):
        if literature.biotransform_support < 0.75 or literature.mean_confidence < 0.75:
            return "B2"
        return "B1"
    if (
        literature.semantic_source == "biotransform"
        and literature.precursor_available == "requires_custom_synthesis"
    ):
        return "B3"
    if (
        (
            literature.semantic_source == "biotransform"
            and literature.precursor_available == "available_by_simple_feeding"
        )
        or (
            fba.candidate_class == "biotransform_candidate"
            and bool(fba.rescues)
            and _strong_biotransform_evidence(literature, fba, features)
        )
    ):
        return "B2"
    if (
        fba.candidate_class == "biotransform_candidate"
        and bool(fba.rescues)
        and features.precursor_dependency_score < 0.65
        and features.broad_route
        and features.route_completion_ratio < 0.15
    ):
        return "C1"
    return "E1"


def _non_native_class(
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
    features: EvidenceFeatures,
) -> FineClass:
    gap_class = _generic_engineering_gap_class(literature, features)
    if gap_class is not None and literature.non_native_support < 0.75:
        return gap_class
    if (
        genome.state == "supports_production"
        and fba.state == "assessed_negative"
        and features.route_presence >= 0.65
        and features.genome_positive_quality >= 0.15
        and features.fba_negative_quality < 0.80
    ):
        return "A2"
    if (
        genome.state == "assessed_negative"
        and fba.state == "supports_production"
        and fba.candidate_class == "denovo"
        and features.fba_positive_quality >= 0.80
        and features.route_presence >= 0.65
        and 0.55 <= features.route_absence_score < 0.80
    ):
        return "A2"
    if (
        genome.state == "assessed_negative"
        and features.engineering_gap == "multi"
        and features.engineering_evidence_score >= 0.35
        and features.route_presence >= 0.60
        and features.missing_step_burden >= 0.75
    ):
        return "D2"
    if (
        genome.state == "assessed_negative"
        and features.route_absence_score >= 0.80
        and features.route_presence >= 0.65
        and features.supported_reaction_count == 0
        and features.producing_reaction_count <= 1
        and features.direct_precursor_count <= 1
        and features.terminal_evidence >= 0.15
        and features.engineering_evidence_score < 0.20
    ):
        return "D1"
    if (
        genome.state == "assessed_negative"
        and features.route_absence_score >= 0.80
        and features.supported_reaction_count == 0
        and (
            features.producing_reaction_count >= 10
            or features.direct_precursor_count >= 10
        )
    ):
        return "D2"
    if features.route_absence_score >= 0.55:
        return _unsupported_route_class(features)
    if literature.non_native_support >= 0.50:
        return "C1"
    if genome.state == "assessed_negative":
        if features.genome_negative_quality >= 0.45:
            return "C1"
        if features.fba_negative_quality >= 0.30:
            return "C2"
        return "E1"
    if fba.state == "assessed_negative" and features.fba_negative_quality >= 0.30:
        return "C2"
    return "E1"


def _a1_quality(
    literature: LiteratureAssessment,
    features: EvidenceFeatures,
) -> bool:
    if (
        literature.status == "ok"
        and literature.denovo_support >= 0.75
        and literature.mean_confidence >= 0.75
        and features.fba_positive_quality >= 0.80
        and features.terminal_evidence >= 0.50
        and features.native_route_support >= 0.55
        and features.native_route_support < 0.62
        and features.precursor_dependency_score <= 0.30
        and features.engineering_evidence_score <= 0.20
        and not features.broad_route
        and features.producing_reaction_count <= 1
        and features.direct_precursor_count <= 1
    ):
        return True
    if (
        literature.status == "ok"
        and literature.denovo_support >= 0.75
        and features.route_presence >= 0.55
        and features.terminal_evidence >= 0.30
        and features.precursor_dependency_score < 0.45
        and (
            features.fba_positive_quality >= 0.65
            or (features.broad_route and features.supported_reaction_count >= 30)
            or features.supported_reaction_count >= 8
        )
        and (
            features.route_completeness >= 0.65
            or features.supported_reaction_count >= 30
        )
    ):
        return True
    if (
        literature.status != "ok"
        and features.broad_route
        and features.supported_reaction_count >= 40
        and features.genome_positive_quality >= 0.48
        and features.route_presence >= 0.80
        and features.terminal_evidence >= 0.40
        and features.precursor_dependency_score < 0.30
    ):
        return True
    if (
        literature.status == "ok"
        and literature.denovo_support >= 0.55
        and features.broad_route
        and features.supported_reaction_count >= 30
        and features.route_presence >= 0.65
        and features.terminal_evidence >= 0.40
        and features.native_route_support >= 0.48
        and features.precursor_dependency_score < 0.35
        and features.engineering_evidence_score <= 0.20
    ):
        return True
    if (
        literature.status == "ok"
        and features.fba_positive_quality >= 0.80
        and features.genome_positive_quality >= 0.50
        and features.route_presence >= 0.65
        and features.terminal_evidence >= 0.75
        and features.native_route_support >= 0.58
        and features.route_absence_score < 0.20
        and not features.broad_route
        and features.supported_reaction_count <= 1
        and 8 <= features.producing_reaction_count <= 20
        and features.route_completion_ratio <= 0.10
        and features.precursor_dependency_score <= 0.60
        and literature.non_native_support <= 0.25
        and literature.engineering_support <= 0.25
    ):
        return True
    if (
        literature.status == "ok"
        and features.fba_positive_quality >= 0.80
        and features.genome_positive_quality >= 0.50
        and features.route_presence >= 0.65
        and features.terminal_evidence >= 0.75
        and features.native_route_support >= 0.56
        and features.engineering_evidence_score <= 0.20
        and not features.broad_route
        and features.supported_reaction_count <= 1
        and 8 <= features.producing_reaction_count <= 20
        and features.route_completion_ratio <= 0.10
        and features.precursor_dependency_score <= 0.60
    ):
        return True
    if (
        literature.status == "ok"
        and literature.denovo_support >= 0.55
        and features.fba_positive_quality >= 0.70
        and features.route_presence >= 0.65
        and features.terminal_evidence >= 0.50
        and features.native_route_support >= 0.55
        and features.precursor_dependency_score < 0.35
        and features.engineering_evidence_score <= 0.25
        and (
            (
                features.producing_reaction_count <= 1
                and features.terminal_evidence >= 0.75
                and features.route_completeness >= 0.60
            )
            or (
                8 <= features.producing_reaction_count <= 20
                and features.terminal_evidence >= 0.75
                and features.precursor_dependency_score <= 0.60
                and features.route_completion_ratio <= 0.15
            )
            or (
                features.broad_route
                and features.supported_reaction_count >= 30
            )
        )
    ):
        return True
    if (
        literature.status == "ok"
        and literature.denovo_support >= 0.65
        and features.fba_positive_quality >= 0.70
        and features.genome_positive_quality >= 0.50
        and features.route_presence >= 0.65
        and features.terminal_evidence >= 0.75
        and features.native_route_support >= 0.60
        and features.engineering_evidence_score <= 0.20
        and not features.broad_route
        and 8 <= features.producing_reaction_count <= 20
        and features.route_completion_ratio <= 0.15
        and features.precursor_dependency_score <= 0.60
    ):
        return True
    if (
        features.genome_positive_quality >= 0.70
        and features.fba_positive_quality >= 0.80
        and (not features.broad_route or features.supported_reaction_count >= 30)
        and features.route_presence >= 0.65
        and features.terminal_evidence >= 0.65
        and features.native_route_support >= 0.62
        and features.precursor_dependency_score < 0.40
    ):
        return True
    return (
        features.strong_denovo_quality
        and features.route_completeness >= 0.82
        and features.terminal_evidence >= 0.65
    )


def _generic_engineering_gap_class(
    literature: LiteratureAssessment,
    features: EvidenceFeatures,
) -> FineClass | None:
    if features.engineering_gap == "none":
        return None
    if (
        features.engineering_gap == "single"
        and literature.status != "ok"
        and features.engineering_evidence_score >= 0.20
        and features.fba_positive_quality >= 0.65
        and features.terminal_evidence >= 0.65
        and features.route_completion_ratio < 0.30
        and features.missing_step_burden >= 0.60
    ):
        return "D1"
    if literature.engineering_support >= 0.35:
        return "D1" if features.engineering_gap == "single" else "D2"
    if (
        features.engineering_evidence_score >= 0.35
        and
        features.known_route_evidence >= 0.60
        and features.genome_negative_quality < 0.70
        and features.fba_negative_quality < 0.70
    ):
        return "D1" if features.engineering_gap == "single" else "D2"
    if (
        features.engineering_gap == "single"
        and features.engineering_evidence_score >= 0.20
        and features.route_presence >= 0.65
        and features.native_route_support < 0.25
    ):
        return "D1"
    return None


def _positive_gap_override(
    semantic_label: str,
    features: EvidenceFeatures,
) -> FineClass | None:
    if semantic_label not in {"denovo", "denovo&biotransform"}:
        return None
    if features.broad_route:
        return None
    if (
        features.engineering_gap == "single"
        and features.engineering_evidence_score >= 0.20
        and features.fba_positive_quality >= 0.65
        and features.terminal_evidence >= 0.65
        and features.route_completion_ratio < 0.30
        and features.missing_step_burden >= 0.60
    ):
        return "D1"
    if features.engineering_evidence_score < 0.35:
        return None
    if (
        features.fba_positive_quality >= 0.65
        and features.terminal_evidence >= 0.65
        and features.route_completion_ratio < 0.30
        and features.missing_step_burden >= 0.65
    ):
        return "D1"
    return None


def _strong_mechanistic_denovo_overrides_engineering_literature(
    genome: GenomePathResult,
    fba: FBAPathResult,
    features: EvidenceFeatures,
) -> bool:
    return (
        genome.state == "supports_production"
        and fba.state == "supports_production"
        and fba.candidate_class == "denovo"
        and features.fba_positive_quality >= 0.80
        and features.native_route_support >= 0.60
        and features.route_presence >= 0.65
        and features.terminal_evidence >= 0.50
        and features.precursor_dependency_score < 0.35
    )


def _strong_no_route_nonnative_is_absent_not_engineering(
    literature: LiteratureAssessment,
    features: EvidenceFeatures,
) -> bool:
    return (
        literature.status == "ok"
        and literature.semantic_source == "non_native"
        and literature.non_native_support >= 0.80
        and literature.engineering_support >= 0.80
        and literature.mean_confidence >= 0.80
        and features.route_presence <= 0.65
        and features.supported_reaction_count == 0
        and features.producing_reaction_count <= 1
        and features.direct_precursor_count <= 1
        and features.terminal_evidence <= 0.05
        and features.native_route_support < 0.30
    )


def _sparse_single_producer_positive_gap(
    semantic_label: str,
    *,
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
    features: EvidenceFeatures,
) -> FineClass | None:
    if semantic_label not in {"denovo", "abstain"}:
        return None
    if literature.status == "ok" and literature.denovo_support >= 0.40:
        return None
    if (
        genome.state == "supports_production"
        and fba.state == "supports_production"
        and fba.candidate_class == "denovo"
        and genome.gem.gene_associated_producer_count == 1
        and not features.broad_route
        and features.engineering_gap == "multi"
        and features.engineering_evidence_score >= 0.20
        and features.route_presence >= 0.65
        and features.route_completion_ratio < 0.18
        and features.missing_step_burden >= 0.80
        and features.supported_reaction_count <= 2
        and features.producing_reaction_count >= 6
        and features.terminal_evidence >= 0.75
        and features.native_route_support < 0.50
    ):
        return "D2" if features.direct_precursor_count <= 8 else "D1"
    return None


def _abstain_fallback_class(
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
    features: EvidenceFeatures,
) -> FineClass | None:
    if (
        genome.state == "supports_production"
        and fba.state == "assessed_negative"
        and features.terminal_evidence >= 0.65
        and (
            features.native_route_support >= features.fba_negative_quality * 0.75
            or (
                features.genome_positive_quality >= 0.55
                and features.supported_reaction_count >= 2
            )
        )
    ):
        return "A2"
    if fba.state == "assessed_negative" and features.fba_negative_quality >= 0.55:
        if (
            genome.state == "supports_production"
            and features.route_presence >= 0.65
            and features.genome_positive_quality >= 0.15
            and features.fba_negative_quality < 0.80
        ):
            return "A2"
        return "C2"
    if (
        genome.state == "assessed_negative"
        and fba.state == "supports_production"
        and fba.candidate_class == "denovo"
        and features.fba_positive_quality >= 0.80
        and features.route_presence >= 0.65
        and features.route_absence_score < 0.80
        and literature.status == "ok"
    ):
        return "A2"
    if (
        features.precursor_dependency_score >= 0.70
        and genome.state == "supports_production"
        and _strong_biotransform_evidence(literature, fba, features)
        and not _broad_native_route_overrides_biotransform(literature, features)
    ):
        return "B2"
    if features.route_absence_score >= 0.55:
        return _unsupported_route_class(features)
    if (
        genome.state == "supports_production"
        and features.native_route_support >= 0.45
        and (
            features.broad_route
            or literature.status == "ok"
            or fba.state == "supports_production"
        )
    ):
        return "A2"
    return None


def _precursor_dependent_terminal_b2(
    semantic_label: str,
    *,
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
    features: EvidenceFeatures,
) -> bool:
    return (
        semantic_label in {"denovo", "abstain"}
        and genome.state == "supports_production"
        and features.precursor_dependency_score >= 0.70
        and features.route_completion_ratio >= 0.95
        and features.ko_completion_ratio <= 0.25
        and features.missing_step_burden >= 0.70
        and literature.denovo_support <= 0.40
        and fba.candidate_class != "denovo"
        and not _broad_native_route_overrides_biotransform(literature, features)
    )


def _path_light_denovo_literature_a1(
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
    features: EvidenceFeatures,
    *,
    classifying_coverage: float,
    evidence_abstain_coverage_threshold: float,
) -> bool:
    return (
        0.25 <= classifying_coverage < evidence_abstain_coverage_threshold
        and literature.status == "ok"
        and literature.semantic_source == "denovo"
        and literature.precursor_available in {"available_in_medium", "unknown"}
        and literature.grounded_runs >= 4
        and _evidence_pmid_count(literature) >= 3
        and literature.p_raw is not None
        and literature.p_raw >= 0.90
        and literature.denovo_support >= 0.75
        and literature.mean_confidence >= 0.75
        and literature.biotransform_support <= 0.10
        and literature.non_native_support <= 0.05
        and literature.engineering_support <= 0.05
        and genome.state == "unknown"
        and fba.state == "unknown"
        and features.route_presence <= 0.05
        and features.terminal_evidence <= 0.05
        and features.engineering_evidence_score <= 0.05
        and features.route_absence_score <= 0.05
    )


def _unsupported_no_route_biotransform_c1(
    literature: LiteratureAssessment,
    features: EvidenceFeatures,
) -> bool:
    return (
        literature.status == "ok"
        and literature.semantic_source == "biotransform"
        and literature.precursor_available in {
            "available_in_medium",
            "available_by_simple_feeding",
        }
        and literature.decision == "deliver"
        and literature.grounded_runs >= 2
        and literature.p_raw is not None
        and literature.p_raw >= 0.66
        and 0.35 <= literature.biotransform_support < 0.50
        and literature.mean_confidence < 0.50
        and literature.denovo_support <= 0.05
        and literature.non_native_support <= 0.05
        and literature.engineering_support <= 0.05
        and features.route_presence <= 0.05
        and features.native_route_support < 0.20
        and features.engineering_evidence_score <= 0.05
        and features.supported_reaction_count == 0
        and features.producing_reaction_count == 0
        and features.direct_precursor_count == 0
        and features.terminal_evidence <= 0.05
    )


def _consistent_no_route_biotransform_b2(
    literature: LiteratureAssessment,
    features: EvidenceFeatures,
) -> bool:
    return (
        literature.status == "ok"
        and literature.semantic_source == "biotransform"
        and literature.grounded_runs >= 2
        and literature.p_raw is not None
        and literature.p_raw >= 0.66
        and literature.biotransform_support >= 0.50
        and literature.mean_confidence >= 0.50
        and literature.non_native_support <= 0.05
        and literature.engineering_support <= 0.05
        and features.route_presence <= 0.05
        and features.native_route_support < 0.20
        and features.engineering_evidence_score <= 0.05
        and features.supported_reaction_count == 0
        and features.producing_reaction_count == 0
        and features.direct_precursor_count == 0
    )


def _mixed_positive_no_route_biotransform_b2(
    literature: LiteratureAssessment,
    features: EvidenceFeatures,
) -> bool:
    return (
        literature.status == "ok"
        and literature.semantic_source == "biotransform"
        and literature.precursor_available in {
            "available_in_medium",
            "available_by_simple_feeding",
        }
        and literature.decision == "deliver"
        and literature.grounded_runs >= 2
        and literature.p_raw is not None
        and literature.p_raw >= 0.66
        and literature.denovo_support >= 0.30
        and literature.biotransform_support >= 0.15
        and literature.mean_confidence >= 0.50
        and literature.non_native_support <= 0.05
        and literature.engineering_support <= 0.05
        and features.route_presence <= 0.05
        and features.native_route_support < 0.25
        and features.engineering_evidence_score <= 0.05
        and features.supported_reaction_count == 0
        and features.producing_reaction_count == 0
        and features.direct_precursor_count == 0
        and features.terminal_evidence <= 0.05
    )


def _low_coverage_fba_denovo_gem_producer_a2(
    genome: GenomePathResult,
    fba: FBAPathResult,
    features: EvidenceFeatures,
    *,
    classifying_coverage: float,
    evidence_abstain_coverage_threshold: float,
) -> bool:
    return (
        0.20 <= classifying_coverage < evidence_abstain_coverage_threshold
        and genome.state == "unknown"
        and fba.state == "supports_production"
        and fba.candidate_class == "denovo"
        and fba.mapping_status == "mapped"
        and genome.gem.mapping_status == "mapped"
        and genome.gem.directional_producer_count > 0
        and features.route_presence >= 0.65
        and features.route_absence_score < 0.30
        and features.fba_positive_quality >= 0.70
        and features.engineering_evidence_score <= 0.20
        and features.precursor_dependency_score < 0.35
    )


def _fba_denovo_overrides_partial_genome_absence(
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
    features: EvidenceFeatures,
) -> FineClass | None:
    if (
        genome.state == "assessed_negative"
        and fba.state == "supports_production"
        and fba.candidate_class == "denovo"
        and fba.mapping_status == "mapped"
        and features.fba_positive_quality >= 0.80
        and features.route_presence >= 0.65
        and 0.55 <= features.route_absence_score < 0.80
        and features.supported_reaction_count == 0
        and features.producing_reaction_count <= 8
        and features.direct_precursor_count <= 8
        and literature.non_native_support < 0.40
        and literature.engineering_support < 0.40
        and features.precursor_dependency_score < 0.35
    ):
        return "A2"
    return None


def _compact_incomplete_route_burden(
    features: EvidenceFeatures,
    fba: FBAPathResult,
    *,
    classifying_coverage: float,
    evidence_abstain_coverage_threshold: float,
) -> FineClass | None:
    if (
        features.engineering_gap == "multi"
        and features.route_presence >= 0.65
        and features.route_completion_ratio < 0.20
        and features.missing_step_burden >= 0.75
        and features.supported_reaction_count == 1
        and features.direct_precursor_count <= 8
        and features.fba_positive_quality >= 0.70
        and features.terminal_evidence >= 0.65
        and features.native_route_support < 0.50
    ):
        return "D2"
    if (
        features.engineering_gap == "multi"
        and features.route_presence >= 0.65
        and features.route_completion_ratio < 0.20
        and features.missing_step_burden >= 0.75
        and features.supported_reaction_count == 1
        and features.direct_precursor_count <= 8
        and fba.state == "unknown"
        and classifying_coverage < evidence_abstain_coverage_threshold
        and features.terminal_evidence >= 0.40
        and features.native_route_support < 0.20
    ):
        return "D1"
    return None


def _chemical_negative_no_route_c2(
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
    features: EvidenceFeatures,
) -> bool:
    return (
        genome.kegg.status == "ok"
        and bool(genome.kegg.compound_id)
        and genome.kegg.mapping_method == "kegg_exact_name"
        and fba.state == "unknown"
        and (
            fba.mapping_status in {"metabolite_not_in_model", "model_unavailable"}
            or fba.target_metabolite_id is None
        )
        and genome.gem.directional_producer_count == 0
        and not genome.kegg.producing_reaction_ids
        and not genome.kegg.candidate_kos
        and not genome.kegg.direct_precursor_ids
        and features.route_presence <= 0.05
        and not _has_useful_literature_support(literature)
    )


def _has_useful_literature_support(literature: LiteratureAssessment) -> bool:
    if literature.status != "ok":
        return False
    return max(
        literature.denovo_support,
        literature.biotransform_support,
        literature.non_native_support,
        literature.engineering_support,
        literature.mean_confidence,
    ) >= 0.35


def _unsupported_route_class(features: EvidenceFeatures) -> FineClass:
    if features.engineering_evidence_score < 0.35:
        if features.route_absence_score >= 0.55:
            return "C1"
        return "E1"
    if features.engineering_gap == "single":
        if (
            features.missing_step_burden < 0.45
            and features.native_route_support < 0.35
        ):
            return "C1"
        return "D1"
    if features.engineering_gap == "multi":
        if features.terminal_evidence <= 0.05 and features.direct_precursor_count <= 1:
            return "C1"
        return "D2"
    if features.route_presence >= 0.65:
        return "C1"
    return "E1"


def _native_route_overrides_weak_biotransform(
    literature: LiteratureAssessment,
    features: EvidenceFeatures,
) -> bool:
    if literature.semantic_source != "biotransform":
        return False
    if literature.biotransform_support >= 0.75:
        return False
    return _strong_broad_native_route(features)


def _broad_native_route_overrides_biotransform(
    literature: LiteratureAssessment,
    features: EvidenceFeatures,
) -> bool:
    if literature.semantic_source != "biotransform":
        return False
    if literature.biotransform_support >= 0.75 or literature.mean_confidence >= 0.75:
        return False
    return (
        features.broad_route
        and features.supported_reaction_count >= 30
        and features.direct_precursor_count >= 50
        and features.route_presence >= 0.80
        and features.terminal_evidence >= 0.40
        and features.native_route_support >= 0.48
        and features.engineering_evidence_score <= 0.20
    )


def _strong_broad_native_route(features: EvidenceFeatures) -> bool:
    return (
        features.broad_route
        and features.supported_reaction_count >= 40
        and features.genome_positive_quality >= 0.48
        and features.route_presence >= 0.80
        and features.terminal_evidence >= 0.40
        and features.precursor_dependency_score < 0.30
        and features.engineering_evidence_score <= 0.20
    )


def _moderate_route_only_absence(features: EvidenceFeatures) -> bool:
    return (
        6 <= features.producing_reaction_count <= 8
        and features.direct_precursor_count <= 8
        and 0.30 <= features.route_absence_score < 0.55
        and features.engineering_evidence_score < 0.20
    )


def _strong_literature_fallback_class(
    literature: LiteratureAssessment,
    features: EvidenceFeatures,
) -> FineClass | None:
    if literature.status != "ok":
        return None
    if literature.non_native_support >= 0.80 and literature.mean_confidence >= 0.80:
        return "C1"
    if (
        literature.denovo_support >= 0.80
        and literature.mean_confidence >= 0.75
        and _denovo_literature_has_mechanistic_anchor(features)
    ):
        return "A2"
    if (
        literature.semantic_source == "biotransform"
        and (
            (
                literature.biotransform_support >= 0.60
                and literature.mean_confidence >= 0.60
            )
            or (
                literature.precursor_available == "available_by_simple_feeding"
                and literature.biotransform_support >= 0.40
                and literature.mean_confidence >= 0.40
            )
            or (
                literature.precursor_available == "available_by_simple_feeding"
                and literature.grounded_runs >= 2
                and literature.p_raw is not None
                and literature.p_raw >= 0.66
                and literature.biotransform_support >= 0.25
                and literature.mean_confidence >= 0.25
                and features.route_presence < 0.30
                and features.native_route_support < 0.35
                and features.engineering_evidence_score <= 0.20
            )
            or (
                literature.precursor_available == "available_in_medium"
                and literature.biotransform_support >= 0.40
                and literature.mean_confidence >= 0.40
                and features.native_route_support < 0.35
                and features.route_presence < 0.30
            )
        )
    ):
        if (
            literature.precursor_available == "available_in_medium"
            and literature.biotransform_support >= 0.75
            and literature.mean_confidence >= 0.75
        ):
            return "B1"
        if literature.precursor_available in {
            "available_in_medium",
            "available_by_simple_feeding",
        }:
            return "B2"
        if literature.precursor_available == "requires_custom_synthesis":
            return "B3"
    return None


def _weak_literature_e_release_class(
    literature: LiteratureAssessment,
    features: EvidenceFeatures,
) -> FineClass | None:
    if literature.status != "ok":
        return None

    if (
        literature.semantic_source == "biotransform"
        and literature.precursor_available
        in {"available_in_medium", "available_by_simple_feeding"}
        and _evidence_pmid_count(literature) >= 2
        and literature.biotransform_support >= 0.15
        and literature.mean_confidence >= 0.15
        and literature.denovo_support <= 0.05
        and literature.non_native_support <= 0.05
        and literature.engineering_support <= 0.05
        and features.route_presence < 0.30
        and features.native_route_support < 0.20
        and features.supported_reaction_count == 0
        and features.terminal_evidence <= 0.05
    ):
        return "B2"

    if (
        literature.decision == "reject"
        and literature.semantic_source == "non_native"
        and literature.p_raw is not None
        and literature.p_raw <= 0.05
        and _evidence_pmid_count(literature) >= 1
        and features.route_presence >= 0.60
        and features.unsupported_known_route_score >= 0.40
        and features.supported_reaction_count == 0
        and features.producing_reaction_count <= 1
        and features.direct_precursor_count <= 1
        and features.terminal_evidence <= 0.05
        and features.native_route_support < 0.20
    ):
        return "C1"

    return None


def _evidence_pmid_count(literature: LiteratureAssessment) -> int:
    return len([
        pmid for pmid in literature.evidence_pmids.split(";")
        if pmid.strip()
    ])


def _denovo_literature_has_mechanistic_anchor(features: EvidenceFeatures) -> bool:
    return (
        features.genome_positive_quality >= 0.15
        or features.fba_positive_quality >= 0.65
        or features.terminal_evidence >= 0.30
        or (
            features.route_presence >= 0.45
            and features.supported_reaction_count > 0
        )
    )


def _strong_literature_path_light(
    literature: LiteratureAssessment,
    mechanism: str,
    features: EvidenceFeatures | None = None,
) -> bool:
    if literature.status != "ok":
        return False
    if literature.engineering_support >= 0.35 or literature.non_native_support >= 0.35:
        return False
    if mechanism == "denovo":
        if literature.denovo_support < 0.80 or literature.mean_confidence < 0.75:
            return False
        if features is not None and _denovo_literature_has_mechanistic_anchor(features):
            return True
        return (
            literature.grounded_runs >= 4
            and _evidence_pmid_count(literature) >= 3
        )
    if mechanism == "biotransform":
        return (
            literature.semantic_source == "biotransform"
            and literature.biotransform_support >= 0.60
            and literature.mean_confidence >= 0.60
            and literature.precursor_available
            in {"available_in_medium", "available_by_simple_feeding"}
        )
    return False


def _strong_biotransform_evidence(
    literature: LiteratureAssessment,
    fba: FBAPathResult,
    features: EvidenceFeatures,
) -> bool:
    if (
        literature.semantic_source == "biotransform"
        and literature.precursor_available in {
            "available_in_medium",
            "available_by_simple_feeding",
            "requires_custom_synthesis",
        }
    ):
        return True
    return (
        fba.candidate_class == "biotransform_candidate"
        and bool(fba.rescues)
        and features.precursor_dependency_score >= 0.70
        and features.terminal_evidence >= 0.65
        and not features.broad_route
    )


def _mechanism_for_class(fine_class: str) -> str:
    if fine_class.startswith("A"):
        return "denovo"
    if fine_class.startswith("B"):
        return "biotransform"
    if fine_class in {"C1", "C2", "D1", "D2"}:
        return "non_native"
    return "abstain"


def _route_atom(
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
) -> RouteAtom:
    route_refs = list(dict.fromkeys(
        genome.kegg.supported_reaction_ids
        + [item.reaction_id for item in genome.gem.producers]
    ))
    enzymes = sorted({
        value
        for item in genome.gem.producers
        for value in (item.ec_codes + item.gene_ids)
    })
    precursors = sorted({
        rescue.precursor_kegg_id for rescue in fba.rescues
    } or set(genome.kegg.direct_precursor_ids))
    if fba.candidate_class == "denovo" and genome.state == "supports_production":
        coverage: CoverageLevel = "complete"
    elif fba.candidate_class == "biotransform_candidate":
        coverage = "terminal_only"
    elif genome.state == "supports_production":
        coverage = "partial"
    elif genome.state == "assessed_negative":
        coverage = "none"
    else:
        coverage = "unknown"
    return RouteAtom(
        route_ref=route_refs,
        terminal_enzyme=enzymes,
        entry_precursor=precursors,
        chassis_step_coverage=coverage,
        engineering_required=literature.engineering_support >= 0.50,
    )


def _technical_unavailable(
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
) -> bool:
    retrieval_failed = literature.status == "retrieval_error"
    model_or_mapping_failed = (
        fba.mapping_status in {"model_unavailable", "metabolite_not_in_model"}
        and genome.kegg.status in {"unavailable", "unmapped"}
    )
    return retrieval_failed or (
        model_or_mapping_failed and literature.status != "ok"
    )


def _terminal_gap_only(genome: GenomePathResult) -> bool:
    return bool(
        genome.kegg.producing_reaction_ids
        or genome.gem.directional_producer_count
    )


def _decision(
    fine: FineClass,
    secondary: FineClass | None,
    mechanism: str,
    semantic_scores: dict[str, float],
    route: RouteAtom,
    rationale: str,
    synthesis_path: SynthesisPath | None = None,
    deliverable_classes: Iterable[str] = DEFAULT_DELIVERABLE_CLASSES,
) -> GateDecision:
    abstain = fine in {"E1", "E2"}
    classes = [fine] + ([secondary] if secondary else [])
    label = "&".join(classes)
    return GateDecision(
        fine_class=fine,
        secondary_class=secondary,
        pred_10class="E" if abstain else fine,
        class_label=label,
        mechanism=mechanism,
        pred_deliverable=derive_deliverable(
            fine, deliverable_classes=deliverable_classes
        ),
        abstain=abstain,
        abstain_subtype=fine if abstain else None,
        route=route,
        synthesis_path=synthesis_path,
        rationale=rationale,
    )
