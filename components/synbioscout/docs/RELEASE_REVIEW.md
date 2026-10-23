> Standalone component version 0.3.1 release history. Current bundle checks are documented in [VALIDATION.md](VALIDATION.md).

# Version 0.3.1 review

## Changes

- The README now introduces the software and its usage. Detailed validation history and submission administration have dedicated documents.
- Interface explanations and next actions are more concise. Actual review, failure, budget and synthetic-data states retain their meaning.
- New settings leave the backend disabled, model name empty and compute configuration unselected. Existing local settings are preserved by the launcher.
- The example brief uses disease exploration with empty candidate, target, chassis and source-package fields.
- Entry examples illustrate two task types using migraine and caffeine. They fill the text box only, and users can replace them before parsing.

## Candidate-selection audit

Candidate inputs start empty. The intake prompt preserves candidate-assessment inputs; disease exploration identifies candidates through current research sources. Candidate-assessment admission accepts any candidate name in the input list. Disease exploration rejects candidate-specific background inputs and starts with its own searches.

New offline tests cover confirmed API submission for caffeine, resveratrol, NAC and SFN; disease-only input; explicit candidate exclusions; blank UI fields; and first-start configuration. These tests evaluate software contracts using fixed parser responses; live-model biomedical evaluation uses its own datasets.

NAC/SFN mentions remain where they carry information: historical validation records, regression fixtures and instructions preventing implicit candidate selection. Legacy raw-schema defaults remain compatible; the interactive intake and the packaged example explicitly set unspecified strain and candidate fields to empty values.

## Preserved controls

Research prompts, evidence/identity checks, scientific review gates, budgets, idempotent confirmation and private-data boundaries are unchanged. Unreviewed, rejected, partial and failed records keep their original status. The standalone release recorded a deferred license decision; the current distribution's [license status](../LICENSE_STATUS.md) describes the applicable team-code license.

