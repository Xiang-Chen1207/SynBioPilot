# Setup and external resources

Use editable installation from a checkout; adapters locate files relative to the
checkout. Source `scripts/runtime.sh` before installation so caches and temporary
files remain inside `.runtime/`. Keep each model in a separate environment.

| Component | Dependencies / external inputs | Configuration |
|---|---|---|
| Controller | Python 3.11+, numpy, pandas, pyarrow | `run.python` |
| DrugCLIP | Official DrugCLIP, pocket-preprocessing repository, Uni-Core, six folds | `drugclip.python/repo/screen_repo/checkpoint_dir` |
| SPRINT | Official panspecies-dti/ultrafast, retrieval checkpoint, SaProt, Foldseek | `sprint.python/repo/checkpoint/saprot_checkpoint/foldseek` |
| PSICHIC | Official graph implementation and matching Torch/PyG extensions, model.pt/degree.pt/config.json, ESM cache | `psichic.python/repo/weights/torch_home` |
| PharmacoNet | Official pmnet, Open Babel, pmnet.tar | `pharmaconet.python/repo/checkpoint` |
| ADMET-AI | ADMET-AI and its matching inference dependencies | `admet.python` |
| ChassisGate | `environment/chassisgate.txt`, iBT721.xml, public-source access and an LLM backend | `gate.python/models_dir/backend` |

The `environment/*.txt` files preserve integration dependency specifications.
Select upstream versions compatible with your CUDA/PyTorch platform and
checkpoints. The adapters load these environments for model inference.

Official code: [DrugCLIP](https://github.com/THU-ATOM/DrugCLIP),
[pocket preprocessing](https://github.com/THU-ATOM/DrugCLIP_screen_pipeline),
[Uni-Core](https://github.com/dptech-corp/Uni-Core),
[SPRINT](https://github.com/abhinadduri/panspecies-dti),
[PSICHIC](https://github.com/huankoh/PSICHIC),
[PharmacoNet](https://github.com/SeonghwanSeo/PharmacoNet),
[ADMET-AI](https://github.com/swansonk14/admet_ai).

DrugCLIP needs `fold_0.pt` through `fold_5.pt`; SPRINT needs its trained retrieval
checkpoint and `SaProt_650M_AF2.pt`; PSICHIC needs its full matching weight/config
directory; PharmacoNet needs `pmnet.tar`. Use the official providers' download
instructions. Compatible existing embeddings can be referenced through
`embedding_caches`, including their ID mappings. Weights and caches are not bundled.

## HDAC2 and the library

The prepared PDB, FASTA and positioned reference ligand from the existing HDAC2
run are in `examples/hdac2/`; their structure source is
[PDB 4LXZ](https://www.rcsb.org/structure/4LXZ).
Changing target requires all three matching inputs and a new output directory.
Model-specific sequence/pocket limitations still apply. This prepared screening
receptor is not the molecular-dynamics topology or AD4Zn receptor.

Set `library.molecules` to your full standardized library. The default template
maps `canonical_isomeric_smiles` and `full_inchikey`; the simple CSV preparation
command instead emits `smiles` and `inchikey`, so update these column mappings.

## Chassis and model access

Place the WCFS1 iBT721 SBML file in `resources/gems/iBT721.xml`, or set an external
`models_dir`. Obtain it from the [official MODEL1507180045 archive](https://www.ebi.ac.uk/biostudies/studies/MODEL1507180045).
This reference represents WCFS1; L168 production assessment requires
strain-and-condition-specific evidence. Keep `chassis_id=lplantarum`,
`chassis_name=Lactiplantibacillus plantarum WCFS1` and `kegg_organism_code=lpl`
consistent with that input.

The example uses an API-compatible backend. Set `gate.base_url`, `gate.model`
and `gate.api_key_env` for your service, then provide the named environment
variable through your shell or secret manager. No credentials belong in JSON.
Set the template's service/model names to an available backend and record its
version with each run.

Existing `codex` and `file_queue` backends remain supported. For file_queue,
run `python3 run.py --config configs/hdac2.local.json bridge` on the authorized
sidecar host with the same shared output directory. Record backend and model changes with the run, since they can change extracted
literature evidence.

## SynBioScout

Install it in a separate environment to avoid colliding with its bundled legacy
`target2lead` package:

```bash
cd components/synbioscout
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/start_scientist.py --no-browser
```

Open `http://127.0.0.1:8765`. Configure its normal backend authentication and
explicitly enable remote processing before live research. This application has
its own settings and budgets; the screening Gate API settings do not configure it.
Disease exploration identifies candidates from the disease question. The optional
candidate-assessment mode evaluates existing inputs with their recorded origin. Source packages are explicit, and the optional NAC source
package and real research histories are not included in this code distribution.

Review/export the research result, prepare the selected target files and use the
root SynBioPilot configuration to start the Wiki screening workflow. The older
compute-handoff feature inside SynBioScout retains its original compatibility
behavior and is not used by the root workflow.
