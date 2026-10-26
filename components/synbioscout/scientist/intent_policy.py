"""Explicit task admission and context isolation; no candidate-name blacklist.

Legacy records keep their original behavior and are never relabeled as an
unguided discovery. Fresh retrieval may introduce any molecule, including one
that appeared in an unrelated historical project.
"""
from __future__ import annotations


def is_disease_exploration(brief):
    return brief.task_intent == "disease_exploration"


def validate_intent_brief(brief):
    if brief.task_intent == "candidate_assessment" and not brief.existing_candidates:
        raise ValueError("candidate_assessment requires explicitly supplied existing_candidates")
    if not is_disease_exploration(brief):
        return
    # Neutral disease/population/endpoint descriptions remain ordinary task
    # inputs. Previous experimental or candidate-specific guidance is explicit
    # incompatible input, not something silently stripped after admission.
    forbidden = ("existing_candidates", "background_package_id", "target_hints", "assumptions",
                 "experiment_ids", "target_packages", "continue_saved_hypotheses",
                 "allowed_precursors", "engineering_allowed", "experimental_capabilities",
                 "delivery_context", "conditions")
    conflicts = [field for field in forbidden if getattr(brief, field)]
    # The schema's historical chassis defaults are never exploration context.
    # Supplying a different engineered chassis is intentional guidance and must
    # use a guided task instead of being silently accepted as exploration.
    for field in ("chassis_name", "chassis_id", "reference_chassis"):
        if getattr(brief, field) and getattr(brief, field) != type(brief).model_fields[field].default:
            conflicts.append(field)
    if conflicts:
        raise ValueError("disease_exploration excludes guided inputs: " + ", ".join(conflicts))


def admit_run_inputs(brief, parent_run_id=""):
    """Call before parent loading or run creation, including non-HTTP entrypoints."""
    validate_intent_brief(brief)
    if is_disease_exploration(brief) and parent_run_id:
        raise ValueError("disease_exploration requires a fresh run without parent_run_id")


def validate_exploration_start(run, *, allow_planning_step=False):
    """Reject injected saved artifacts before constructing any provider request."""
    admit_run_inputs(run.brief, run.parent_run_id)
    if not is_disease_exploration(run.brief):
        return
    conflicts = [field for field in ("sources", "evidence", "targets", "candidates", "target_packages",
                                     "questions") if getattr(run, field)]
    if run.steps and not (allow_planning_step and len(run.steps) == 1 and
                         run.steps[0].step_id == "plan" and run.steps[0].status == "running"):
        conflicts.append("steps")
    if run.co_scientist is not None:
        conflicts.append("co_scientist")
    if conflicts:
        raise ValueError("disease_exploration excludes preloaded research artifacts: " + ", ".join(conflicts))


def task_intent_receipt(brief):
    """Record task intent alongside candidate-origin and retrieval provenance."""
    declared = brief.task_intent
    effective = declared
    if declared == "legacy":
        effective = "candidate_assessment" if brief.existing_candidates else "guided_unspecified"
    return {"declared_intent": declared, "effective_intent": effective,
            "intent_basis": "explicit_user_selection" if declared != "legacy" else "legacy_compatible_guided_inputs",
            "context_admission": "current_run_public_queries_only" if is_disease_exploration(brief)
                                 else "guided_context_with_provenance",
            "historical_memory_allowed": not is_disease_exploration(brief),
            "saved_source_reuse_allowed": not is_disease_exploration(brief),
            "candidate_background_allowed": not is_disease_exploration(brief),
            "discovery_claim": "Candidate origins are recorded with the retrieval and generation history of each run"}

