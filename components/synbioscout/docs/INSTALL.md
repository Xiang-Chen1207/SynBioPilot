# Installation

## Requirements

- Python 3.11 or newer; macOS or Linux. Use WSL rather than native Windows for the launcher.
- A Python virtual environment and enough disk space for the scientific dependencies listed in `pyproject.toml`.
- Node.js for the frontend contract tests; the frontend itself needs no npm build.
- For live research only: an authorized supported model backend and network access to the selected literature/database services.

The install specification uses dependency ranges. This source bundle does not include the standalone release validation JSON or a universal lockfile. Source the root SynBioPilot runtime script before installation to keep caches in the checkout.

## Install from this directory

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/start_scientist.py --check
.venv/bin/python scripts/start_scientist.py --no-browser
```

The UI is at `http://127.0.0.1:8765`; API documentation is at `/docs`. The server binds to loopback. Stop it with Ctrl-C. Only one service should own a given workspace.

## Configure the model

The launcher creates `scientist.local.json` on first start and preserves existing settings. New installations use `llm.backend: "none"`, an empty model name and `allow_remote_llm_data: false`. Choose a backend and a model available to your account before invoking parsing or research.

For the Codex backend, set `llm.backend` to `codex`, `llm.model` to your supported model ID and `llm.executable` to your installed executable. For Responses or an OpenAI-compatible endpoint, use the corresponding backend settings. Authenticate through the provider's normal mechanism; store API keys in the environment variable named by `api_key_env`. Set `allow_remote_llm_data` to `true` when you are ready to send research context.

Keep the initial settings for browsing, drafts from research settings and the offline example. The interface explains when model parsing needs configuration.

Review `allow_remote_llm_data` before live use: selected research context may be transmitted to the model provider. Keep credentials and personal health information out of research inputs.

## Optional resources

`target2lead_config` starts empty. To use Compute handoff, configure a compatible Target2Lead installation with its required tools, structures, assets and licenses, then select that configuration. `configs/keap1.json` documents the integration format; its external resources are installed separately.

Leave `background_package_id` empty for ordinary research. To reuse local literature, install a package with appropriate source permissions and enter its ID explicitly. The historical team-specific paper-excerpt package is excluded from this distribution.

## Troubleshooting

| Symptom | Action |
|---|---|
| `.venv` not found | Create it in this package root with Python 3.11+. |
| Missing module | Install the declared dependencies into that environment. |
| Port already occupied | Use an unused `--port`; do not terminate another team's service. |
| Model unavailable | Check your configured backend, installed executable and account access. |
| Requested source package missing | Install an authorized package or clear the package selection. |
| Partial run | Inspect the stage and review status; distinguish a budget stop from a scientific conclusion. |

Do not disable TLS checks to work around a service error. Keep safe error categories and request IDs for diagnosis.

