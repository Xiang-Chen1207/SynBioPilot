# Architecture

```text
English browser UI / HTTP API / CLI
                  |
       Natural-language draft + user confirmation
                  |
       Job service + project/run store
                  |
       Research engine + shared budget ledger
                  |
       Sources -> extraction -> semantic review
                  |
       Candidate identity + independent conclusion review
                  |
       Versioned findings, events and exports
```

## Components

| Code | Responsibility |
|---|---|
| `scientist/web/` | Research intake, evidence views, role activity, target and experiment panels. |
| `intake.py`, `api.py`, `cli.py` | Input grounding, revision/confirmation checks and application entry points. |
| `runtime.py`, `store.py` | Task ownership, persistence and recoverable project/run state. |
| `research.py`, `coscientist.py` | Research orchestration and task-specific stage selection. |
| `compact_synthesis.py`, `evidence.py` | Strict extraction format, deterministic reference expansion and source-span validation. |
| `identity.py`, `identity_prepare.py` | Candidate identity resolution, preparation and revision tracking. |
| `assessment_review.py`, `candidate_assessments.py` | Candidate findings and independent conclusion-review binding. |
| `budgets.py`, `provider_receipts.py` | Shared call/time/UTF-8 byte accounting and safe diagnostics. |
| `reports.py`, `exports.py`, `presentation.py` | Consistent human-readable and machine-readable outputs. |
| `target2lead/`, `integration.py` | Explicit handoff to separately configured downstream computation. |

## Research modes

Standard research and Co-Scientist research share evidence and identity contracts. In the Co-Scientist workbench, Supervisor, Generation, Proximity, Reflection, Ranking, Evolution and Meta-review are roles in one orchestration system. Proximity uses rule-based grouping, while model-backed roles operate through the configured backend.

Candidate-assessment mode prioritizes evidence extraction, an independent semantic review, canonical identity and an independent review of the candidate conclusion. Hypothesis generation and its later stages are optional extensions when the remaining budget admits them.

The new extraction request uses compact v2: a quotation and its scope are written once, and findings refer to explicit typed evidence keys. Expansion deterministically resolves the typed evidence keys to their recorded text. Later input size includes the expanded content. Old saved records remain readable.

## Tools and interoperability

The lightweight catalog has eight operations across seven services: Europe PMC, Open Targets (two operations), UniProt, PubChem, ChEMBL, Reactome and QuickGO. Source IDs, canonical chemical identifiers, typed JSON, CSV, Markdown and HTTP APIs connect the stages.

The experiment panel analyzes supplied tabular measurements. Downstream Target2Lead adapters support a separate computational workflow; the AI Scientist source package contains their integration code but no bundled model weights, molecule libraries or MD assets.

## Scientific record integrity

Evidence preserves source, continuous span, entity, study system, condition, endpoint and direction. Reviews bind to the current assessment and identity revisions. New relevant evidence can invalidate a previous conclusion and require review. Shared ambiguous aliases retain unresolved identity status.

Fixed per-call caps, global budgets, future-stage reservations and in-flight reservations are separate. Public receipts distinguish received from accepted output. The historical fields named `max_input_tokens` and `max_output_tokens` use UTF-8 byte accounting in this implementation; provider token usage and billing are separate measures.

