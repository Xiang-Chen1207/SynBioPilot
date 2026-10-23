# Wiki workflow and implementation mapping

Source: [SynBioPilot Wiki](https://2026.igem.wiki/sustech-med/model-synbiopilot),
read on 21 October 2026 (Asia/Shanghai). The project workflow below incorporates the maintainer clarification of 22 October 2026.

| Wiki section | Repository implementation | Scope |
|---|---|---|
| AI Scientist | `components/synbioscout/scientist/` | Autonomous project discovery; optional candidate-assessment input mode; identity, source passage and review binding; separate model-backed reviews; optional budget-limited hypothesis cycle; English workbench |
| Eight-source library | `synbiopilot/library.py` | Import supplied CSV exports; canonical isomeric SMILES deduplication; source-record mapping. Source database download/access is external. |
| Four independent Top1000 lists | `target2lead/adapters/`; `synbiopilot/scoring.py:candidates` | DrugCLIP, SPRINT, PharmacoNet, PSICHIC; same library IDs; successful finite scores only |
| Equations (1)–(5) | `valid_scores`, `admet8`, `composite` | Full valid reference pool, average tied ranks, exact weights, 50:50 final score, missing values pending |
| Literature + KEGG/GEM + FBA | `vendor/chassisgate/deliverability/integrated.py`, `mechanistic_paths.py`, `fusion.py` | Existing team implementation with external GEM and LLM/service dependencies |
| A–D follow-up vs deliverable-now | `synbiopilot/scoring.py:stratify` | Separate route-follow-up and deliverable-now lists; technical/missing inputs E2; unresolved E stays unknown |
| Focused molecular analysis | `molecular/` | Actual project AD4Zn panel and SAHA 05/06 and SFN 08/09 H-bond/endpoint analysis scripts; requires separately supplied scientific input assets |

## Module handoffs

1. Merge the eight natural-product database exports and deduplicate by structure to form the complete screening library.
2. Score the same library independently with DrugCLIP, SPRINT, PharmacoNet and PSICHIC.
3. Select each model's Top1000; merge the lists and deduplicate candidates.
4. Calculate percentiles from each model's full valid score pool and evaluate the union with ADMET8.
5. Combine four-model consensus and ADMET8 with the 50:50 rule below.
6. Automatically classify complete-score candidates through ChassisGate.
7. Prepare target-specific docking or trajectory inputs for the selected molecular study.

SynBioScout exports evidence-linked candidates, targets and research contracts
for review. The root screening entry point consumes explicit target files and
library inputs. Molecular studies use their own module inputs and controls.
The NAC candidate route and HDAC2 target route are complementary outputs;
their relationship is recorded with the supporting evidence for each route.

## Project discovery and classification

The project uses autonomous disease-driven discovery and automatic screening
over the same complete library for all four models. NAC and HDAC2 arise from
the disease research route; SFN emerges from the automatic screening route.
ChassisGate automatically assigns **D1 to both SFN and NAC**.

[Project workflow and records](result-provenance.md) describes the library,
score, classification and trajectory records associated with these results.

## Exact scoring rules

For model m, N is the count of `status=success` and finite `raw_score` records in
the full input library. With descending average rank r, `P=100*(N-r)/(N-1)`.
All ties receive their average rank; ordinal Top1000 selection breaks boundary
ties by ascending compound ID. A one-record reference pool is assigned 100 as a
documented degenerate-input convention; an empty valid pool is an error.

`C=(P_drugclip+P_sprint+P_pharmaconet+P_psichic)/4` and `F=0.5*C+0.5*A`.
Both C and F require four finite percentiles. A requires all eight valid endpoint
values and successful ADMET status. Solubility is clipped as specified in the
Wiki; probability inputs must be in [0,1], with out-of-range values recorded as invalid.

The eight source databases are COCONUT, LOTUS, NP-MRD, NPASS, NPAtlas,
StreptomeDB, CMNPD and CyanoMetDB. Record their snapshot versions, access conditions and structure counts with each
library release. Supply the database exports as external inputs.

## Chassis reference and route classes

| Setting | Definition |
|---|---|
| Reference strain / GEM | *Lactiplantibacillus plantarum* WCFS1 / iBT721 |
| Route follow-up | Complete-score A1/A2/B1/B2/B3/C1/C2/D1/D2 candidates |
| Deliverable-now policy | A1/A2/B1/B2; C/D classes represent engineering follow-up routes |
| Unknown classes | E/E1/E2 retain unknown status; execution/missing-input cases use E2 |
| L168 applicability | Requires L168-specific production evidence under the stated conditions |
