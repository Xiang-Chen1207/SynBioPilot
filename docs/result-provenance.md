# Project workflow and records

The project workflow and result summary below reflect the maintainer's
clarification on 22 October 2026. The implementation guides describe the
corresponding inputs, processing rules and output formats.

## Autonomous discovery and automatic screening

1. AI Scientist starts from the ASD disease question and autonomously identifies
   NAC as a candidate molecule and HDAC2 as a screening target.
2. The eight natural-product database exports are merged and structurally
   deduplicated into the complete screening library. DrugCLIP, SPRINT,
   PharmacoNet and PSICHIC independently score this same library.
3. Each model contributes its Top1000. Their union is deduplicated, scored with
   full-pool percentiles and ADMET8, and ranked by the common 50:50 formula.
   SFN emerges from this automatic screening and ranking workflow.
4. ChassisGate integrates the configured literature, KEGG/GEM and FBA evidence
   and automatically assigns **D1 to both SFN and NAC**.

Library preparation, model scoring, ranking and Gate classification retain
their own input and output records. A model's `chunk_size` controls processing
batch size; the library manifest defines the screening population.

## Records associated with each result

| Result | Records and interpretation |
|---|---|
| Disease-driven discovery | Research question, run ID, retrieved sources, candidate/target origins and review records link NAC and HDAC2 to the autonomous research run. |
| Complete screening library | Database snapshots, structure-deduplication map and library manifest define the common input population. |
| SFN screening result | Four-model raw scores, full percentile reference pools, Top1000 lists, ADMET output and composite-ranking tables record SFN's progress through the automatic workflow. |
| SFN and NAC route classes | ChassisGate output records the automatic D1 assignments alongside the assessed chassis, evidence and run configuration. D1 is an engineering follow-up route under the existing classification rubric. |
| Molecular analysis | SAHA runs 05/06 and SFN pose A runs 08/09 supply the configured trajectory analyses. Required provenance includes each trajectory's starting pose, topology, production inputs and velocity seed. |

The [workflow map](wiki-workflow.md) gives the scoring and classification rules.
The [molecular guide](../molecular/README.md) lists the analysis inputs and commands.
Research result files are maintained with their runs; this code repository
provides the implementation and input/output contracts.

## Software examples and execution records

The [synthetic example](../examples/synthetic/README.md) uses labelled artificial
inputs for software checks. SynBioScout also offers supplied-candidate assessment
and continuation modes, with explicit task-intent and origin fields per run.
Those modes and the [component execution records](../components/synbioscout/docs/VALIDATION.md)
have their own inputs and stage statuses. The project result summary above
describes the formal autonomous discovery and automatic screening workflow.
