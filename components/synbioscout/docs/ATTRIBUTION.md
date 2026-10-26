# Attribution and dependencies

SUSTech-MED assembled the research workflow, application contracts, budget and review controls, natural-language confirmation flow, interface and exports. The repository's existing authorship history is preserved. Individual author/role names for the official Attributions Form should be confirmed by the team rather than inferred from a Git login.

## Software dependencies

`pyproject.toml` declares Python dependencies: Pydantic, requests, jsonschema, Biopython, RDKit, NumPy, pandas, SciPy, Matplotlib, FastAPI and Uvicorn. Development checks use pytest, httpx and Ruff; frontend tests use Node's built-in facilities. Optional PaperQA and ToolUniverse integrations have their own installation requirements.

Dependencies are not bundled or relicensed in this snapshot. Their upstream distributions and notices remain authoritative. The release environment inventory records installed versions for reproducibility, not a legal audit of all transitive packages.

## External models and data

- Uses externally provided language-model backends and Codex/API services.
- Literature records and quotations belong to their authors and publishers. PubChem, Europe PMC and the other databases have their own data and access terms.
- DrugCLIP, SPRINT, PSICHIC, PharmacoNet, ADMET, docking and MD tools are separate upstream systems. Their weights, libraries, environments and results are not in this package.
- ChassisDeliver-150 review materials carry redistribution restrictions and are excluded, as are bundled vendor code and GEM resources.

## Excluded research inputs

The historical `nac_asd_v1` package and `final_gold_source_scenario.json` contain real paper excerpts and project input. They are omitted pending a source-by-source redistribution review. The release's growth/concentration examples and portable test fixtures are explicitly synthetic.

The original tests that require omitted historical documents remain as test source for engineering provenance, but are outside the portable test selection. The distributed files comprise software, public examples and test source; research runs and local private assets remain external.

## License

The standalone release recorded a deferred license decision. This distribution's
[LICENSE_STATUS.md](../LICENSE_STATUS.md) identifies the enclosing SynBioPilot MIT
license as superseding that notice for team-owned code. Third-party terms and
the redistribution status of excluded resources remain separate.

