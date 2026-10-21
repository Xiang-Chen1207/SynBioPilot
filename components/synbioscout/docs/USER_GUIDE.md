# User guide

## Create a study

1. Enter your research question. “Explore migraine” and “Assess caffeine for migraine” are editable examples of the two task types; clicking one fills the text box only.
2. Select **Understand request**. The configured model extracts a draft; this does not start research.
3. Check the disease, symptoms, population, candidate, task intent and any follow-up questions. Unknown details stay unspecified. Use the research settings form to edit these inputs directly.
4. Review source-package selection, task scope and budgets. Confirm the current draft, then start the study once.
5. If submission status is uncertain, inspect its receipt before trying again.

The interface is English. Original user text, quotations and scientific records keep their original language. Check the parsed fields before confirming, especially for multilingual or ambiguous requests.

## Read results

The workbench shows role activity, tool calls, source records, evidence, identities and reviews. A run-level status and a candidate-review status answer different questions: an optional stage may stop after a useful core evaluation, or a run may stop before any conclusion has been reviewed.

Open the source excerpt for an evidence item and inspect its scope. Check that the conclusion refers to the same candidate identity, version and evidence set as the review. A `hold` or `needs_evidence` result records the remaining evidence gaps.

Exports include Markdown, JSON, CSV and ZIP. The ZIP carries the report and structured tables. Exported records contain public research fields rather than private model prompts, answers or credentials.

## API sequence

Consult the running `/docs` schema for full field definitions.

| Action | Route |
|---|---|
| Create a draft | `POST /api/intake/drafts` |
| Edit its current revision | `PATCH /api/intake/drafts/{draft_id}` |
| Create the confirmed project | `POST /api/projects` |
| Start one run | `POST /api/projects/{project_id}/runs` |
| Recover submission status | `GET /api/intake/submissions/{submission_id}` |
| Read results | `GET /api/projects/{project_id}/runs/{run_id}/results` |
| Export | The run's `/export?format=json`, `markdown`, `csv` or `zip` |

Use IDs and revisions returned by the API. Reuse the same submission identifier for recovery; do not invent a new one just because a response was delayed. Candidate assessment can use `prepare_targets:false`.

## Offline example

Run `scripts/offline_example.py` to inspect API catalogs and analyze the artificial growth table. The output is clearly marked SYNTHETIC. This example demonstrates software behavior without a provider account, literature retrieval, GPU job or biological experiment.

## Intended use

The software supports research planning and evidence review. Humans choose the scientific question, check the sources and decide whether further experiments are justified. Clinical treatment, strain production and delivery performance require their own evidence.

