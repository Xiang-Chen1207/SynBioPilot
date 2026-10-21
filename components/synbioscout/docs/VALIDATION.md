# Verification and evaluation scope

## Software checks

The portable checks use synthetic data, mocked providers and local API clients.
They cover input parsing, evidence spans, chemical identity, version-bound
reviews, budgets, exports and submission recovery. Run the available tests from
the component directory with its development dependencies installed:

```sh
python -m pytest tests/test_demos.py tests/test_english_ui.py tests/test_generation_context_digest.py tests/test_candidate_assessment_support_scope.py
node --test tests/test_demo_ui.js tests/test_intake_ui.js
```

Some other tests consume historical source packages distributed separately.
In particular, `test_collaboration_ui.js` requires
`docs/serial_optimization/fixtures/collaboration_states.json`, and
`test_delivery_r3_ui.js` requires
`docs/nac_asd_iteration/DELIVERY_R3_SYNTHETIC_PUBLIC.json`. These fixtures are
absent from this bundle. Their input requirements are visible in the test files.
API/service tests use Unix `fcntl` locking and should run on Linux, macOS or WSL;
the symlink test also needs symlink support. The demo API's isolated-preview test
requires the separately distributed `scripts/preview_hdac2_workbench.py`, and the
historical-run observation test requires its original `run.json`.
The standalone release's `VALIDATION.json` and `scripts/verify_release.py` are
not included in this bundle; use the commands and checked-in tests above for
bundle verification.

## Component version and execution records

| Record | Scope and recorded result | Availability |
|---|---|---|
| 20 October 2026 engineering checks | Historical standalone notes report 1,714 Python checks and 113 JavaScript contracts | Historical counts; retain with that version rather than treating them as current bundle counts |
| 21 October 2026 English-interface checks | Historical standalone notes report 1,721 Python checks and 117 JavaScript contracts | Historical counts; the current bundle includes test source |
| Archived component ASD–NAC assessment, 17 October 2026 | Partial run; extraction stopped at a per-call output cap; candidate conclusion review was not reached | The baseline notes report 12,538 output UTF-8 bytes against a 12,000-byte cap, 309.984 seconds, two model calls and NAC CID 12035, identity revision 1. Original run receipts are required to verify the record. |

The archived assessment predates compact-v2 dispatch; its partial status belongs
to that component execution record. The formal project uses autonomous discovery
and automatic screening, with SFN and NAC automatically classified as D1 by
ChassisGate. See [project workflow and records](../../../docs/result-provenance.md).
Software checks, component execution records and project research outputs each
retain their own versions, inputs and stage statuses.

## Research evaluation

Assess each result through its task intent, candidate origin, current identity,
source passages, study system, endpoint and conclusion-review status. Source
review and software completion are reported separately. `hold`, `needs_evidence`,
skipped stages and failed calls remain visible with their next actions.

Disease-exploration quality, live input parsing and clinical applicability each
require a defined evaluation dataset. Strain production, release, exposure and
nasal PK/PD use measurements specific to the strain and experimental conditions.
