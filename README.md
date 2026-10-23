# SynBioPilot

**An AI Scientist for chassis-aware therapeutic discovery — SUSTech-MED, iGEM 2026.**

[中文说明](README_zh.md) · [Team Wiki](https://2026.igem.wiki/sustech-med/model-synbiopilot) · [Workflow & implementation](docs/wiki-workflow.md) · [Setup](docs/setup.md)

SynBioPilot connects disease-driven research with molecular screening and
chassis-aware biosynthetic route assessment. Its four modules organize
evidence-linked candidates and targets, combine four-model screening with
ADMET evaluation, classify candidate–chassis routes through ChassisGate,
and support focused docking and molecular dynamics analysis.

The repository provides the SynBioScout research application, a screening
and postprocessing entry point, and molecular-analysis scripts. Each module
documents its inputs, configuration and outputs.

## Workflow

| Module | Main operation | Output |
|---|---|---|
| AI Scientist / SynBioScout | Autonomous disease-driven discovery of candidates and targets | Evidence-linked candidates, targets and research plans |
| Target-Guided Virtual Screening | Independent Top1000 selection, candidate union, percentile scoring and ADMET8 integration | Composite-ranked candidates |
| ChassisGate | Literature, KEGG/GEM and growth-constrained FBA evidence integrated through a biological rubric | Chassis-specific route classes and follow-up lists |
| Molecular Docking & Dynamics Analysis | Target-specific docking and analysis of the configured SAHA/SFN trajectories | Pose, interaction and trajectory metrics |

Use the root `run.py` entry point for the Top1000/ADMET8 screening workflow.
Launch SynBioScout and molecular-analysis scripts through their module guides.
Each molecular study uses its own target, control and trajectory inputs.

In the SUSTech-MED project, AI Scientist autonomously identifies NAC as a
candidate and HDAC2 as a screening target from the ASD research question.
The four models automatically screen the same complete, structure-deduplicated
library; SFN emerges from their screening and the common ranking workflow.
ChassisGate automatically classifies both SFN and NAC as **D1**. See
[project workflow and records](docs/result-provenance.md) for the project result
summary and the corresponding input/output records.

For other research tasks, the application includes an optional candidate-assessment
mode. Each run records its task intent and candidate origins; the project workflow
above uses autonomous discovery and automatic screening.

## Quick start: offline example

Python 3.11+ is required for the controller. From a checkout:

```bash
source scripts/runtime.sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python run.py --config configs/demo.json postprocess
```

This offline example uses eight **synthetic** input records and Top3 selection
to demonstrate candidate merging, composite scoring and route stratification.
Expected synthetic outputs: six union candidates, four with complete scores,
two pending, three route-follow-up candidates and one deliverable-now candidate.

Read `runs/synthetic_demo/summary.json`, `composite/all_candidates.csv`,
`composite/pending_candidates.csv` and `stratification/route_followup.csv`.
For another run, change `run.output` to a new directory. Existing results are not
silently overwritten.

## Use the four modules

| Module | Implementation | Entry point |
|---|---|---|
| AI Scientist / SynBioScout | `components/synbioscout/scientist/`, including local web UI | [Application README](components/synbioscout/README.md) |
| Four-model screening and ADMET8 | `target2lead/adapters/`, `synbiopilot/scoring.py` | `run.py --config configs/hdac2.json plan` |
| ChassisGate | `vendor/chassisgate/deliverability/` and Gate adapter | `run.py … run --until stratify` |
| Docking and molecular analysis | `molecular/docking/`, `molecular/dynamics/` | [Molecular inputs and commands](molecular/README.md) |

For real inference, first provision the separate model environments, official
checkpoints, molecular library and GEM specified in [setup](docs/setup.md).
Copy `configs/hdac2.json` to `configs/hdac2.local.json` and edit resource paths:

```bash
python3 run.py --config configs/hdac2.local.json plan
python3 run.py --config configs/hdac2.local.json run --until composite
python3 run.py --config configs/hdac2.local.json run --until stratify --resume
```

Run these compute commands on a provisioned node. `postprocess` consumes existing
score, ADMET and Gate CSVs; [data contracts](docs/data-contracts.md) explain the fields.

The Wiki profile uses Top1000 per model, average ranks for ties, percentiles over
each model's full valid score pool, equal four-model weights, and the exact eight
ADMET endpoint weights. Missing model scores or invalid ADMET endpoints remain
pending. All candidates use the same scoring formula and input-validity rules.

## Inputs and methods

Model environments, checkpoints, molecular libraries and the WCFS1 / iBT721
metabolic reference are described in [setup](docs/setup.md). Target-specific
receptors, topologies and trajectories are listed in the [molecular guide](molecular/README.md).
The [workflow map](docs/wiki-workflow.md) defines scoring and route classes;
[project workflow and records](docs/result-provenance.md) links the project
results to the relevant library, score, classification and trajectory records.

## Layout

```text
synbiopilot/            Wiki-aligned orchestration, scoring, library preparation
target2lead/            Reused four-model, ADMET and ChassisGate adapters
components/synbioscout/ AI Scientist source, web interface, examples and upstream tests
vendor/chassisgate/     Team's biological evidence integration and fixed rubric
molecular/             Target-specific docking, H-bond and endpoint-analysis code
configs/               Real-resource template and offline synthetic example
examples/              Prepared HDAC2 inputs and explicitly synthetic predictions
environment/           Separate model dependency lists
docs/                  Setup, input/output contracts, Wiki mapping and attribution
```

MIT covers team-owned code. External models, data and other third-party materials
retain their own terms; see [third-party notices](THIRD_PARTY_NOTICES.md).
