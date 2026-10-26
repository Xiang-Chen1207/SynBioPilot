"""End-to-end orchestration of Literature, Genome, FBA and deterministic fusion."""
from __future__ import annotations

from pydantic import BaseModel

from .extract import LLMClient
from .fusion import FusionConfig, FusionResult, fuse_evidence
from .gem import GemProvider
from .kegg import KEGG_ORGANISM_CODES, KeggClient
from .literature import LiteratureAssessment, assess_literature
from .mechanistic_models import FBAPathResult, GenomePathResult
from .mechanistic_paths import assess_fba_path, assess_genome_path
from .schema import Candidate, Chassis


class IntegratedAssessment(BaseModel):
    pair_id: str
    candidate_id: str
    candidate_name: str
    chassis_id: str
    chassis_name: str
    literature: dict
    genome: GenomePathResult
    fba: FBAPathResult
    fusion: FusionResult


def assess_integrated(
    candidate: Candidate,
    chassis: Chassis,
    *,
    literature_client: LLMClient,
    provider: GemProvider,
    kegg: KeggClient,
    pair_id: str | None = None,
    literature_result: LiteratureAssessment | None = None,
    literature_k: int = 3,
    literature_offline: bool = False,
    literature_cache_dir=None,
    literature_blocked_pmids: set[str] | None = None,
    organism_code: str | None = None,
    flux_threshold: float = 1e-6,
    minimum_growth_fraction: float = 0.1,
    uptake_bound: float = 10.0,
    fusion_config: FusionConfig = FusionConfig(),
) -> IntegratedAssessment:
    pid = pair_id or f"{candidate.id}__{chassis.id}"
    literature = literature_result or assess_literature(
        candidate,
        chassis,
        literature_client,
        pair_id=pid,
        k=literature_k,
        offline=literature_offline,
        cache_dir=literature_cache_dir,
        blocked_pmids=literature_blocked_pmids,
    )
    org = organism_code or KEGG_ORGANISM_CODES.get(chassis.id)
    genome = assess_genome_path(
        candidate, chassis, provider=provider, kegg=kegg, organism_code=org
    )
    fba = assess_fba_path(
        candidate,
        chassis,
        provider=provider,
        kegg=kegg,
        organism_code=org,
        flux_threshold=flux_threshold,
        minimum_growth_fraction=minimum_growth_fraction,
        uptake_bound=uptake_bound,
    )
    fusion = fuse_evidence(
        pid,
        literature,
        genome,
        fba,
        config=fusion_config,
    )
    return IntegratedAssessment(
        pair_id=pid,
        candidate_id=candidate.id,
        candidate_name=candidate.name,
        chassis_id=chassis.id,
        chassis_name=chassis.name,
        literature=literature.to_dict(),
        genome=genome,
        fba=fba,
        fusion=fusion,
    )
