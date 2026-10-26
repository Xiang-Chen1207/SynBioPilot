"""Deterministic v1 fusion for Literature + Genome + FBA.

This module emits a transparent, pre-calibration deliverability probability for a
single molecule--chassis pair. Probability calibration is fold-local by design
(see the paper, "Fold-Local Calibration"): the calibration map is fitted on the
training molecules of each leave-one-molecule-out (LOMO) fold. The per-pair
call produces raw scores; calibration is applied downstream at the
scoring stage by ``deliverability.calibrate`` /
``scripts/score_calibration.py``, which consumes the ``p_raw`` produced here.
Channel priors and selection thresholds are the frozen defaults documented in
``docs/RUBRIC_AND_THRESHOLDS.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from .abstain import decide
from .gate import (
    DEFAULT_DELIVERABLE_CLASSES,
    GateDecision,
    apply_ten_class_gate,
    derive_deliverable,
)
from .evidence_features import EvidenceFeatures, derive_evidence_features
from .literature import LiteratureAssessment
from .mechanistic_models import FBAPathResult, GenomePathResult


SemanticLabel = Literal[
    "denovo",
    "biotransform",
    "non_native",
    "denovo&biotransform",
    "abstain",
]


@dataclass(frozen=True)
class FusionConfig:
    literature_weight: float = 0.35
    genome_weight: float = 0.35
    fba_weight: float = 0.30
    single_label_threshold: float = 0.45
    dual_label_floor: float = 0.45
    dual_label_max_gap: float = 0.18
    conflict_gap: float = 0.15
    minimum_classifying_coverage: float = 0.20
    evidence_abstain_coverage_threshold: float = 0.30
    deliverable_classes: tuple[str, ...] = tuple(sorted(DEFAULT_DELIVERABLE_CLASSES))
    probability_tau_low: float = 0.25
    probability_tau_high: float = 0.75
    enable_probability_abstain: bool = False
    require_positive_synthesis_path: bool = True

    def __post_init__(self) -> None:
        for name in (
            "probability_tau_low",
            "probability_tau_high",
            "evidence_abstain_coverage_threshold",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"{name} must lie in [0, 1]; got {value!r}."
                )
        if self.probability_tau_low > self.probability_tau_high:
            raise ValueError(
                "probability_tau_low must be <= probability_tau_high; got "
                f"{self.probability_tau_low!r} > {self.probability_tau_high!r}."
            )


class ClassScores(BaseModel):
    denovo: float = Field(ge=0.0, le=1.0)
    biotransform: float = Field(ge=0.0, le=1.0)
    non_native: float = Field(ge=0.0, le=1.0)


class FusionResult(BaseModel):
    pair_id: str
    label: SemanticLabel
    class_scores: ClassScores
    evidence_coverage: float = Field(ge=0.0, le=1.0)
    classifying_coverage: float = Field(ge=0.0, le=1.0)
    conflict: str | None = None
    precursor_factor: float = Field(ge=0.0, le=1.0)
    p_raw: float = Field(ge=0.0, le=1.0)
    p_cal: float = Field(ge=0.0, le=1.0)
    probability_decision: str
    channel_contributions: dict
    evidence_features: dict = Field(default_factory=dict)
    interpretation: str
    gate: GateDecision
    provisional: bool = True


def fuse_evidence(
    pair_id: str,
    literature: LiteratureAssessment,
    genome: GenomePathResult,
    fba: FBAPathResult,
    *,
    config: FusionConfig = FusionConfig(),
) -> FusionResult:
    """Fuse three paths into independent semantic scores and deliverability.

    Semantic scores are independent evidence supports, so they need not sum to
    one. This permits a meaningful ``denovo&biotransform`` output.
    """
    channels: list[tuple[str, float, dict[str, float]]] = []
    classifying_coverage = 0.0
    features = derive_evidence_features(literature, genome, fba)

    if literature.status == "ok":
        literature_vector = {
            "denovo": literature.denovo_support,
            "biotransform": literature.biotransform_support,
            "non_native": literature.non_native_support,
        }
        literature_signal = max(literature_vector.values())
        literature_weight = config.literature_weight * features.literature_quality
        channels.append((
            "literature",
            literature_weight,
            literature_vector,
        ))
        if literature_signal > 0:
            classifying_coverage += literature_weight

    genome_vector = _genome_vector(genome, features)
    if genome_vector is not None:
        genome_quality = max(
            features.genome_positive_quality,
            features.genome_negative_quality,
            features.native_route_support,
            features.route_absence_score,
            features.route_presence * 0.45,
        )
        genome_weight = config.genome_weight * genome_quality
        channels.append(("genome", genome_weight, genome_vector))
        classifying_coverage += genome_weight

    fba_vector = _fba_vector(fba, features)
    if fba_vector is not None:
        fba_quality = max(
            features.fba_positive_quality,
            features.fba_negative_quality,
        )
        fba_weight = config.fba_weight * fba_quality
        channels.append(("fba", fba_weight, fba_vector))
        classifying_coverage += fba_weight

    coverage = sum(weight for _, weight, _ in channels)
    scores = _weighted_scores(channels, coverage)
    label, conflict = _choose_label(
        scores,
        classifying_coverage=classifying_coverage,
        config=config,
    )
    precursor_factor = _precursor_factor(literature, fba)
    hard_conflict = (
        conflict if conflict == "production_vs_non_native_conflict" else None
    )
    gate = apply_ten_class_gate(
        literature=literature,
        genome=genome,
        fba=fba,
        semantic_label=label,
        semantic_scores=scores,
        conflict=hard_conflict,
        classifying_coverage=classifying_coverage,
        deliverable_classes=config.deliverable_classes,
        require_positive_synthesis_path=config.require_positive_synthesis_path,
        evidence_features=features,
        evidence_abstain_coverage_threshold=(
            config.evidence_abstain_coverage_threshold
        ),
    )
    p_raw = _raw_deliverability_probability(
        scores,
        gate=gate,
        precursor_factor=precursor_factor,
        deliverable_classes=config.deliverable_classes,
    )
    # p_cal exposes the calibrated-probability slot in the output contract.
    # Inside a single per-pair call there is no held-out fold to fit against, so
    # p_cal equals the pre-calibration p_raw here. The fold-local calibration map
    # (fitted on each LOMO fold's training molecules) is applied downstream by
    # the scorer, which recomputes calibrated probabilities from these p_raw
    # values; see deliverability.calibrate and scripts/score_calibration.py.
    p_cal = p_raw
    probability_decision = decide(
        p_cal, config.probability_tau_low, config.probability_tau_high
    ).value
    if (
        config.enable_probability_abstain
        and not gate.abstain
        and probability_decision == "abstain"
    ):
        gate = gate.model_copy(update={
            "pred_deliverable": None,
            "abstain": True,
            "abstain_subtype": "E1",
            "pred_10class": "E",
            "mechanism": "abstain",
            "rationale": (
                f"{gate.rationale} Probability {p_cal:.3f} falls inside "
                "the selective-prediction abstention band."
            ),
        })
    label = gate.mechanism
    conflict = (
        conflict or genome.conflict or gate.abstain_subtype
        if gate.abstain else genome.conflict
    )
    return FusionResult(
        pair_id=pair_id,
        label=label,
        class_scores=ClassScores(**scores),
        evidence_coverage=min(1.0, coverage),
        classifying_coverage=min(1.0, classifying_coverage),
        conflict=conflict or genome.conflict,
        precursor_factor=precursor_factor,
        p_raw=p_raw,
        p_cal=p_cal,
        probability_decision=probability_decision,
        channel_contributions={
            name: {"weight": weight, **vector}
            for name, weight, vector in channels
        },
        evidence_features=features.__dict__,
        interpretation=gate.rationale,
        gate=gate,
    )


def _genome_vector(
    result: GenomePathResult,
    features: EvidenceFeatures,
) -> dict[str, float] | None:
    if result.state == "supports_production":
        denovo = 0.35 + 0.45 * features.native_route_support
        if features.precursor_dependency_score >= 0.65:
            denovo *= 0.65
        if features.unsupported_known_route_score >= 0.45 and not features.broad_route:
            denovo *= 0.70
        biotransform = (
            0.30
            + 0.30 * features.terminal_evidence
            + 0.30 * features.precursor_dependency_score
        )
        if features.terminal_evidence >= 0.65 and features.route_completeness < 0.55:
            biotransform = max(biotransform, 0.70)
        return {
            "denovo": round(min(0.90, denovo), 6),
            "biotransform": round(min(0.80, biotransform), 6),
            "non_native": 0.0,
        }
    if (
        result.state == "unknown"
        and features.route_presence >= 0.45
        and features.route_absence_score >= 0.45
        and features.engineering_evidence_score >= 0.35
    ):
        return {
            "denovo": 0.0,
            "biotransform": 0.0,
            "non_native": round(min(0.70, 0.35 + 0.35 * features.route_absence_score), 6),
        }
    if result.state == "assessed_negative":
        support = 0.45 + 0.40 * features.genome_negative_quality
        return {
            "denovo": 0.0,
            "biotransform": 0.0,
            "non_native": round(min(0.90, support), 6),
        }
    return None


def _fba_vector(
    result: FBAPathResult,
    features: EvidenceFeatures,
) -> dict[str, float] | None:
    if result.candidate_class == "denovo":
        return {
            "denovo": round(0.65 + 0.30 * features.fba_positive_quality, 6),
            "biotransform": 0.0,
            "non_native": 0.0,
        }
    if result.candidate_class == "biotransform_candidate":
        if features.precursor_dependency_score < 0.65:
            support = 0.35 + 0.25 * features.precursor_dependency_score
        else:
            support = 0.55 + 0.30 * features.fba_positive_quality
        return {
            "denovo": 0.0,
            "biotransform": round(min(0.80, support), 6),
            "non_native": 0.0,
        }
    if result.state == "assessed_negative":
        return {
            "denovo": 0.0,
            "biotransform": 0.0,
            "non_native": round(0.35 + 0.45 * features.fba_negative_quality, 6),
        }
    return None


def _weighted_scores(channels, coverage: float) -> dict[str, float]:
    if coverage <= 0:
        return {"denovo": 0.0, "biotransform": 0.0, "non_native": 0.0}
    return {
        label: round(
            sum(weight * vector[label] for _, weight, vector in channels) / coverage,
            6,
        )
        for label in ("denovo", "biotransform", "non_native")
    }


def _choose_label(
    scores: dict[str, float],
    *,
    classifying_coverage: float,
    config: FusionConfig,
) -> tuple[SemanticLabel, str | None]:
    if classifying_coverage < config.minimum_classifying_coverage:
        return "abstain", "insufficient_classifying_evidence"

    denovo = scores["denovo"]
    biotransform = scores["biotransform"]
    non_native = scores["non_native"]
    if (
        denovo >= config.dual_label_floor
        and biotransform >= config.dual_label_floor
        and abs(denovo - biotransform) <= config.dual_label_max_gap
        and non_native < max(denovo, biotransform)
    ):
        return "denovo&biotransform", None

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    (top_label, top_score), (second_label, second_score) = ranked[:2]
    if top_score < config.single_label_threshold:
        return "abstain", "no_class_above_threshold"
    if (
        "non_native" in {top_label, second_label}
        and second_score >= config.dual_label_floor
        and top_score - second_score <= config.conflict_gap
    ):
        return "abstain", "production_vs_non_native_conflict"
    return top_label, None


def _precursor_factor(
    literature: LiteratureAssessment,
    fba: FBAPathResult,
) -> float:
    if fba.candidate_class == "biotransform_candidate" and fba.rescues:
        return 0.90
    return {
        "available_in_medium": 1.00,
        "available_by_simple_feeding": 0.90,
        "requires_custom_synthesis": 0.25,
        "unknown": 0.60,
    }.get(literature.precursor_available, 0.60)


def _raw_deliverability_probability(
    scores: dict[str, float],
    *,
    gate: GateDecision,
    precursor_factor: float,
    deliverable_classes: tuple[str, ...],
) -> float:
    """Fold semantic supports into a policy-specific continuous probability."""
    if gate.fine_class in {"E1", "E2"}:
        # 0.5 encodes no-information / abstention-neutral: with no resolved
        # deliverable class we deliberately sit at the midpoint rather than
        # leaning toward deliver or reject.
        return 0.5

    deliverable_set = set(deliverable_classes)
    deliverable_support = 0.0
    non_deliverable_support = 0.0
    seen: set[str] = set()

    for fine in (gate.fine_class, gate.secondary_class):
        if fine is None or fine in seen:
            continue
        seen.add(fine)
        support = _fine_class_support(fine, scores, precursor_factor)
        if derive_deliverable(fine, deliverable_classes=deliverable_set):
            deliverable_support = max(deliverable_support, support)
        else:
            non_deliverable_support = max(non_deliverable_support, support)

    total = deliverable_support + non_deliverable_support
    if total <= 0:
        # No supporting mass on either side: fall back to the same
        # no-information / abstention-neutral midpoint as above.
        return 0.5
    return round(deliverable_support / total, 6)


def _fine_class_support(
    fine_class: str,
    scores: dict[str, float],
    precursor_factor: float,
) -> float:
    if fine_class.startswith("A"):
        return scores["denovo"]
    if fine_class in {"B1", "B2"}:
        return scores["biotransform"]
    if fine_class == "B3":
        return scores["biotransform"] * precursor_factor
    if fine_class.startswith("C"):
        return scores["non_native"]
    if fine_class.startswith("D"):
        return max(scores["non_native"], 1.0 - max(scores["denovo"], scores["biotransform"]))
    return 0.0
