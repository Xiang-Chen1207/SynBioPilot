# Sources and third-party materials

The root MIT license applies to SUSTech-MED's own integration, research-assistant,
ChassisGate and analysis contributions. It does not grant additional rights to
external dependencies, model weights, datasets, figures or literature passages.

- SynBioScout source was obtained from the team's `software/ai-scientist` directory
  in [IGEM2026_Model_1](https://github.com/Xiang-Chen1207/IGEM2026_Model_1).
  The component's attribution document describes its inspirations and dependencies.
- Four-model and Gate adapters come from the local Target2Lead implementation,
  derived from `model1/scripts`. ChassisGate is the team's packaged implementation;
  its dataset-specific notice is preserved. CHASSISDELIVER-150 is not bundled.
- Molecular-analysis scripts come from the team's SFN_HDAC2 project. The
  [project repository](https://github.com/alpsxu258/SFN_HDAC2) may require access.
- HDAC2 prepared examples derive from [RCSB PDB 4LXZ](https://www.rcsb.org/structure/4LXZ).
  They are scientific input examples, not new measurements.
- DrugCLIP, Uni-Core, SPRINT, SaProt, Foldseek, PSICHIC, PharmacoNet, ADMET-AI,
  RDKit, COBRApy, MDAnalysis, Meeko, AutoDock/Vina, AmberTools and their dependencies
  are used through their original distributions and licenses.
- Natural-product database exports, iBT721 and other GEMs, source packages,
  checkpoints and trajectories are external. Obtain them under their providers'
  terms. No right to redistribute these assets is inferred from this code license.

The Wiki's publication figures and full text have not been copied into this
repository. The workflow documentation links to the Wiki and records the
implementation decisions necessary to use the source code correctly.
