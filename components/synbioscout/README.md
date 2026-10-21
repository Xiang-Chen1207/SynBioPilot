# SynBioScout AI Scientist

SynBioScout helps research teams turn a question into a structured study, examine source evidence and review candidate molecules. Describe a disease, symptom or candidate in your own words, check the task draft, then follow the research in an English-language workbench.

## Features

- Natural-language entry with editable fields and confirmation before research starts.
- Disease exploration or assessment of candidates supplied by the user.
- Literature retrieval, traceable quotations and chemical-identity checks.
- Separate evidence and candidate-conclusion reviews, linked to the current record version.
- Role activity, budget receipts and recoverable submissions.
- Markdown, JSON, CSV and ZIP exports, plus an explicit Target2Lead compute handoff.

Candidate fields start empty. NAC, SFN and other molecules are assessed when supplied by the user or returned by the current research sources. Source packages are selected explicitly.

## Install and start

Use Python 3.11+ on macOS or Linux, or WSL on Windows.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/start_scientist.py --no-browser
```

Open `http://127.0.0.1:8765`. The first launch creates local settings with model access disabled. Set your backend and model, configure its normal authentication and enable remote data processing before using model-backed actions. See [installation and configuration](docs/INSTALL.md).

## Try an offline example

```sh
.venv/bin/python scripts/offline_example.py --output /path/to/new-empty-demo-directory
```

The example exercises the API and analyzes the included artificial growth measurements. Its files are labelled SYNTHETIC.

## Documentation

- [User guide and API routes](docs/USER_GUIDE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Verification results and evaluation scope](docs/VALIDATION.md)
- [Dependencies and attribution](docs/ATTRIBUTION.md)
- [Release changes and candidate-selection audit](docs/RELEASE_REVIEW.md)
- [Team Software page](https://2026.igem.wiki/sustech-med/software)

Version **0.3.1** · 21 October 2026. License selection is recorded in [LICENSE_STATUS.md](LICENSE_STATUS.md).

This component is embedded in SynBioPilot. Release-check notes identify the
standalone version and recorded verification scope.
For the Wiki Top1000/ADMET8 workflow use [the root entry point](../../README.md).

## Development

Run the portable checks with an installed Node executable:

```sh
SYNBIOSCOUT_TEST_NODE=/path/to/node .venv/bin/python -m pytest tests
```

Include the version, reproduction steps and safe diagnostic metadata when reporting an issue. Keep credentials, private inference files and patient data out of shared reports. Research use requires human review of sources and conclusions.

