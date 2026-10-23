# Focused molecular analysis

The molecular-analysis module evaluates R-SFN–HDAC2 interactions alongside
SAHA controls using an AD4Zn docking panel and analysis of SAHA runs 05/06 and SFN pose A runs
08/09. Outputs include pose geometry, contacts, hydrogen-bond
statistics and endpoint interaction-energy estimates.

## AD4Zn docking panel

`docking/run_docking.py` prepares the nine neutral parent compounds and a separate
panobinostat +1 sensitivity, performs SAHA control searches, then runs the panel.
The common protocol uses seeds 20261021/20261022/20261023, exhaustiveness 32,
20 modes and energy range 4. The SAHA criterion requires at least two first-ranked
poses below 2 Å, all at most 3 Å, and nearest N/O/S–Zn distances in 1.7–2.8 Å.
Failure stops panel interpretation.

Required external asset layout, under `SYNBIOPILOT_MOLECULAR_ASSETS`:

```text
preparation/interaction_panel/docking_ad4zn_ZN/
  receptorH.pdb  receptor.pdbqt  receptor_tz.pdbqt  receptor.gpf
preparation/interaction_panel/ligands/SAH.sdf
input/ligands/SFN_R.sdf
```

Provide the official `AD4Zn.dat` through `SYNBIOPILOT_AD4ZN_DAT`, and install RDKit,
Meeko, numpy, pandas, AutoGrid4 and Vina with AD4 map scoring. The two JSON chemical
input tables are included under `docking/inputs/`. Starting conformer identity,
stereochemistry and Zn/receptor preparation must match the study.

```bash
export SYNBIOPILOT_MOLECULAR_ASSETS=/data/SFN_HDAC2
export SYNBIOPILOT_MOLECULAR_OUTPUT=/data/runs/new_hdac2_docking
export SYNBIOPILOT_AD4ZN_DAT=/data/autodock/AD4Zn.dat
python3 molecular/docking/run_docking.py
python3 molecular/docking/analyze_docking.py
```

Analysis also needs openpyxl. It exports contact, geometry, pose-consistency and
score tables. The optional 3D viewer refers to the original project's 3Dmol assets;
the numerical tables do not depend on the viewer.

## Four-run MD analysis

The trajectory analyses use SAHA runs 05/06 and SFN pose A runs 08/09.
The scripts consume the associated production trajectories and system files.
Run provenance includes the starting pose, topology, production inputs and
velocity seed; see [project workflow and records](../docs/result-provenance.md).

Required external files include:

```text
preparation/interaction_panel/models/zn_1264/{hdac2_saha,hdac2_sfn_pose_a}/
  system.prmtop  system.gro
md/INTERACTION_PANEL_20261010/production_zn_1264_200ns/replicas/{05,06,08,09}/production/md.xtc
```

Use a new MD-analysis output directory and an environment with MDAnalysis, ParmEd,
AmberTools Sander, numpy and pandas. Source `scripts/runtime.sh` before execution.

```bash
export SYNBIOPILOT_MOLECULAR_OUTPUT=/data/runs/new_hdac2_md_analysis
python3 molecular/dynamics/analyze_hbonds.py
python3 molecular/dynamics/calculate_endpoint_energy.py
```

Hydrogen bonds use topology-bonded hydrogens, donor–acceptor distance ≤3.5 Å,
angle ≥150°, periodic boundaries and unique directed pairs per frame.
The endpoint code uses 1 ns snapshots and the existing hybrid OBC2 GB + explicit
ligand–receptor C4 cross term. This endpoint interaction-energy estimate omits
entropy, reorganization and standard-state terms; dedicated reference-set calibration of metal-site accuracy
has status: pending. Atom indices, residue offsets,
trajectory spacing and group selection must match the archived systems.

`dynamics/plot_comparison.py` regenerates the four-run RMSD/RMSF/Zn plots from
precomputed `results/publication_results_25runs_20261012/data/hdac2/*/metrics.csv`
and its corresponding `tables/HDAC2_{windows,RMSF,Zn_geometry}.csv` files beneath
the external asset root. It can also read newly calculated `energy/*` outputs.
It reads these precomputed metrics tables and requires matplotlib and openpyxl.

## Input availability and interpretation

Production trajectories, starting-system lineage and original velocity seeds
are external inputs. The 200 ns analyses require the exact run-to-topology and
starting-pose mapping; the corresponding original provenance records have status: pending. The older
5/80 ns restrained branches represent different simulations.

AD4Zn scores describe docking; N/O/S–Zn distances describe geometry; repeated
searches describe numerical pose consistency. Trajectory metrics describe the
specified simulated systems. Experimental binding, coordination and inhibition
require their corresponding experimental measurements.
