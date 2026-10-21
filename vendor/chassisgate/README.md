# ChassisGate implementation subset

This directory contains the team-owned deliverability modules used by
SynBioPilot. The full benchmark artifact, its figures and its dataset are not
bundled. The original citation and dataset-license notice are retained.

The entry point is `deliverability.integrated.assess_integrated`. It combines
PMID-grounded literature, GEM/KEGG route evidence and growth-constrained FBA,
then applies the fixed fusion/rubric logic. In this subset the genome and FBA
route implementations are in `mechanistic_paths.py`.

The root adapter `target2lead/adapters/gate.py` calls this implementation for
complete-score candidates and retains the per-pair trace. See
[setup](../../docs/setup.md) for dependencies, GEM files and LLM configuration,
and [data contracts](../../docs/data-contracts.md) for the distinction between
route follow-up and conservative deliverable-now decisions.

No benchmark performance is re-estimated by packaging these modules.
