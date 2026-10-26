"""Formal KEGG-bridged Genome and FBA mechanistic evidence paths."""
from __future__ import annotations

import re

import cobra

from .gem import GemProvider, candidate_metabolite, map_metabolite
from .kegg import KEGG_ORGANISM_CODES, KeggClient
from .mechanistic_models import (
    FBAPathResult,
    GemGenomeTrace,
    GenomePathResult,
    KeggGenomeTrace,
    KeggReactionTrace,
    PrecursorRescueTrace,
    ReactionTrace,
)
from .schema import Candidate, Chassis


def assess_genome_path(
    candidate: Candidate,
    chassis: Chassis,
    *,
    provider: GemProvider,
    kegg: KeggClient,
    organism_code: str | None = None,
) -> GenomePathResult:
    model = provider.load(chassis.id)
    gem_trace = _assess_gem_genome(model, candidate, chassis.id)
    org = organism_code or KEGG_ORGANISM_CODES.get(chassis.id)
    compound_id, mapping_method = kegg.resolve_candidate(candidate, model=model)
    if compound_id is None:
        kegg_trace = KeggGenomeTrace(status="unmapped", organism_code=org)
    else:
        context = kegg.compound_context(compound_id, organism_code=org)
        kegg_trace = KeggGenomeTrace(
            status=context.status,
            compound_id=compound_id,
            mapping_method=mapping_method,
            organism_code=org,
            producing_reaction_ids=[r.reaction_id for r in context.producing_reactions],
            producing_reactions=[
                KeggReactionTrace(
                    reaction_id=r.reaction_id,
                    equation=r.equation,
                    direction=r.direction,
                    precursor_compound_ids=r.precursor_compound_ids,
                    ko_ids=r.ko_ids,
                    ec_numbers=r.ec_numbers,
                )
                for r in context.producing_reactions
            ],
            candidate_kos=context.candidate_kos,
            present_kos=context.present_kos,
            missing_kos=context.missing_kos,
            supported_reaction_ids=context.supported_reaction_ids,
            ec_numbers=sorted({
                ec for reaction in context.producing_reactions for ec in reaction.ec_numbers
            }),
            reaction_coverage=context.reaction_coverage,
            direct_precursor_ids=context.precursor_compound_ids,
            error=context.error,
        )

    gem_positive = gem_trace.gene_associated_producer_count > 0
    gem_negative = (
        gem_trace.mapping_status == "mapped"
        and gem_trace.directional_producer_count == 0
    )
    kegg_positive = bool(kegg_trace.supported_reaction_ids)
    kegg_negative = (
        kegg_trace.status == "ok"
        and bool(kegg_trace.candidate_kos)
        and not kegg_trace.supported_reaction_ids
    )
    conflict = None
    if gem_positive and kegg_negative:
        conflict = "gem_gpr_positive_kegg_ko_missing"
    elif kegg_positive and gem_negative:
        conflict = "kegg_ko_positive_gem_reaction_missing"

    if gem_positive or kegg_positive:
        state = "supports_production"
        interpretation = "At least one genomic proxy supports a target-producing catalytic capability."
    elif kegg_negative and not gem_positive:
        state = "assessed_negative"
        interpretation = (
            "KEGG enumerates target-producing reactions but finds none of their candidate KOs "
            "in the chassis; the GEM provides no conflicting gene-associated producer."
        )
    else:
        state = "unknown"
        interpretation = "Available GEM/KEGG evidence is insufficient for a genomic capability judgment."
    return GenomePathResult(
        state=state,
        gem=gem_trace,
        kegg=kegg_trace,
        conflict=conflict,
        interpretation=interpretation,
    )


def assess_fba_path(
    candidate: Candidate,
    chassis: Chassis,
    *,
    provider: GemProvider,
    kegg: KeggClient,
    organism_code: str | None = None,
    flux_threshold: float = 1e-6,
    minimum_growth_fraction: float = 0.1,
    uptake_bound: float = 10.0,
) -> FBAPathResult:
    model = provider.load(chassis.id)
    if model is None:
        return FBAPathResult(
            state="unknown",
            mapping_status="model_unavailable",
            flux_threshold=flux_threshold,
            interpretation="No GEM was available for this chassis.",
        )
    target = candidate_metabolite(model, candidate)
    if target is None:
        return FBAPathResult(
            state="unknown",
            model_id=getattr(model, "id", chassis.id),
            mapping_status="metabolite_not_in_model",
            flux_threshold=flux_threshold,
            interpretation="The target metabolite could not be mapped into the GEM.",
        )

    compound_id, _ = kegg.resolve_candidate(candidate, model=model)
    context = (
        kegg.compound_context(
            compound_id,
            organism_code=organism_code or KEGG_ORGANISM_CODES.get(chassis.id),
        )
        if compound_id else None
    )
    growth_optimum = _growth_optimum(model)
    minimum_growth = (
        growth_optimum * minimum_growth_fraction
        if growth_optimum > flux_threshold and minimum_growth_fraction > 0 else None
    )
    default_flux, growth_applied = _maximum_target_flux(
        model, target, minimum_growth=minimum_growth
    )
    common = dict(
        model_id=getattr(model, "id", chassis.id),
        target_metabolite_id=target.id,
        mapping_status="mapped",
        kegg_compound_id=compound_id,
        default_medium_max_flux=default_flux,
        growth_optimum=growth_optimum,
        minimum_growth_flux=minimum_growth,
        growth_constraint_applied=growth_applied,
        flux_threshold=flux_threshold,
    )
    if default_flux > flux_threshold:
        return FBAPathResult(
            state="supports_production",
            candidate_class="denovo",
            tested_precursor_ids=context.precursor_compound_ids if context else [],
            interpretation="The target carries demand flux on the default medium.",
            **common,
        )

    precursor_ids = context.precursor_compound_ids if context and context.status == "ok" else []
    rescues: list[PrecursorRescueTrace] = []
    for precursor_id in precursor_ids:
        precursor = map_metabolite(model, kegg=precursor_id)
        if precursor is None:
            continue
        exchanges = _boundary_reactions_for(model, precursor)
        if not exchanges:
            continue
        flux, _ = _maximum_target_flux(
            model,
            target,
            minimum_growth=minimum_growth,
            opened_exchanges=exchanges,
            uptake_bound=uptake_bound,
        )
        if flux > flux_threshold:
            rescues.append(PrecursorRescueTrace(
                precursor_kegg_id=precursor_id,
                metabolite_id=precursor.id,
                exchange_reaction_ids=sorted(r.id for r in exchanges),
                target_flux=flux,
            ))
    if rescues:
        return FBAPathResult(
            state="supports_production",
            candidate_class="biotransform_candidate",
            tested_precursor_ids=precursor_ids,
            rescues=rescues,
            interpretation="Specific KEGG direct precursor uptake rescues target production.",
            **common,
        )
    return FBAPathResult(
        state="assessed_negative",
        candidate_class="mechanistic_negative",
        tested_precursor_ids=precursor_ids,
        rescues=[],
        interpretation=(
            "The mapped target has no default-medium flux and no tested KEGG direct precursor rescue."
        ),
        **common,
    )


def _assess_gem_genome(model, candidate: Candidate, chassis_id: str) -> GemGenomeTrace:
    if model is None:
        return GemGenomeTrace(mapping_status="model_unavailable")
    metabolite = candidate_metabolite(model, candidate)
    if metabolite is None:
        return GemGenomeTrace(
            mapping_status="metabolite_not_in_model",
            model_id=getattr(model, "id", chassis_id),
        )
    directional = []
    excluded_transport = []
    gene_associated = []
    for reaction in metabolite.reactions:
        if reaction.boundary:
            continue
        coefficient = reaction.metabolites.get(metabolite, 0.0)
        produces = (
            coefficient > 0 and reaction.upper_bound > 0
        ) or (
            coefficient < 0 and reaction.lower_bound < 0
        )
        if not produces:
            continue
        if _transports_same_metabolite(reaction, metabolite):
            excluded_transport.append(reaction)
            continue
        directional.append(reaction)
        if reaction.gene_reaction_rule:
            gene_associated.append(reaction)
    return GemGenomeTrace(
        mapping_status="mapped",
        model_id=getattr(model, "id", chassis_id),
        metabolite_id=metabolite.id,
        metabolite_name=metabolite.name,
        directional_producer_count=len(directional),
        excluded_transport_count=len(excluded_transport),
        gene_associated_producer_count=len(gene_associated),
        producers=[_reaction_trace(reaction) for reaction in gene_associated],
    )


def _reaction_trace(reaction) -> ReactionTrace:
    annotation = reaction.annotation or {}
    raw_ec = annotation.get("ec-code") or annotation.get("ec") or []
    ecs = [raw_ec] if isinstance(raw_ec, str) else [str(value) for value in raw_ec]
    return ReactionTrace(
        reaction_id=reaction.id,
        reaction_name=reaction.name or "",
        equation=reaction.reaction,
        gene_reaction_rule=reaction.gene_reaction_rule or "",
        gene_ids=sorted(gene.id for gene in reaction.genes),
        ec_codes=sorted(ecs),
    )


def _transports_same_metabolite(reaction, target) -> bool:
    target_base = re.sub(r"_[a-z][0-9]*$", "", target.id.casefold())
    return any(
        metabolite is not target
        and re.sub(r"_[a-z][0-9]*$", "", metabolite.id.casefold()) == target_base
        for metabolite in reaction.metabolites
    )


def _growth_optimum(model) -> float:
    try:
        solution = model.optimize()
        if solution.status == "optimal" and solution.objective_value:
            return max(0.0, float(solution.objective_value))
    except Exception:
        pass
    return 0.0


def _maximum_target_flux(
    model,
    target,
    *,
    minimum_growth: float | None,
    opened_exchanges=(),
    uptake_bound: float = 10.0,
) -> tuple[float, bool]:
    with model:
        for reaction in model.boundary:
            if target in reaction.metabolites:
                reaction.lower_bound = max(0.0, reaction.lower_bound)
        for reaction in opened_exchanges:
            reaction.lower_bound = min(reaction.lower_bound, -abs(uptake_bound))
        growth_applied = False
        if minimum_growth is not None:
            constraint = model.problem.Constraint(
                model.objective.expression,
                lb=minimum_growth,
                name="minimum_growth__deliverability",
            )
            model.add_cons_vars(constraint)
            growth_applied = True
        demand = cobra.Reaction("DM_probe__deliverability_v1")
        demand.add_metabolites({target: -1.0})
        demand.bounds = (0.0, 1000.0)
        model.add_reactions([demand])
        model.objective = demand
        try:
            solution = model.optimize()
        except Exception:
            return 0.0, growth_applied
        if solution.status == "optimal" and solution.objective_value:
            return float(solution.objective_value), growth_applied
        return 0.0, growth_applied


def _boundary_reactions_for(model, metabolite):
    return [reaction for reaction in model.boundary if metabolite in reaction.metabolites]
