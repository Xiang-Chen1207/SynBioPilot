"""Dock and simulate selected compounds, preserving real failures per compound."""
from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import statistics
import subprocess


MDP_ROOT = Path(__file__).resolve().parents[2] / "resources" / "mdp"
TOOLS = {
    "gmx": "gmx", "vina": "vina", "obabel": "obabel", "acpype": "acpype",
    "prepare_receptor": "mk_prepare_receptor.py",
    "prepare_ligand": "mk_prepare_ligand.py", "export_ligand": "mk_export.py",
}
FIELDS = ["compound_id", "smiles", "name", "status", "error", "workdir",
          "docking_affinity_kcal_mol", "production_ns", "backbone_rmsd_mean_nm",
          "ligand_rmsd_mean_nm"]
AMINO_ACIDS = set("ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL HID HIE HIP CYX ASH GLH LYN".split())


def _tool(settings, key):
    value = settings.get("tools", {}).get(key, TOOLS[key])
    return list(value) if isinstance(value, list) else shlex.split(value)


def _command(args, cwd, log, env, input_text=None):
    with (cwd / log).open("w") as stream:
        process = subprocess.run([str(a) for a in args], cwd=cwd, env=env,
                                 input=input_text, text=True,
                                 stdout=stream, stderr=subprocess.STDOUT)
    if process.returncode:
        tail = (cwd / log).read_text(errors="replace")[-3500:]
        raise RuntimeError(f"Command exited {process.returncode}; see {cwd / log}: {tail}")


def _environment(job):
    environment = os.environ.copy()
    runtime = job / ".runtime"
    for key, child in {"TMPDIR": "tmp", "TMP": "tmp", "TEMP": "tmp",
                       "XDG_CACHE_HOME": "cache", "PYTHONPYCACHEPREFIX": "pycache",
                       "NUMBA_CACHE_DIR": "numba"}.items():
        path = runtime / child
        path.mkdir(parents=True, exist_ok=True)
        environment[key] = str(path)
    environment["GMX_MAXBACKUP"] = "-1"
    return environment


def _protein_only(source, destination):
    """Remove crystallographic water, but never silently delete cofactors/metals."""
    lines = source.read_text().splitlines()
    retained = []
    for line in lines:
        if line.startswith(("ATOM  ", "HETATM")):
            residue = line[17:20].strip()
            if residue in {"HOH", "WAT", "DOD"}:
                continue
            if residue not in AMINO_ACIDS:
                raise ValueError(f"Unsupported protein residue/cofactor {residue!r}; provide md.prepared_systems for this compound")
            retained.append(line)
        elif line.startswith("TER"):
            retained.append(line)
    if not any(line.startswith(("ATOM  ", "HETATM")) for line in retained):
        raise ValueError("Target contains no supported protein atoms")
    destination.write_text("\n".join(retained) + "\nEND\n")


def _ligand_sdf(smiles, destination, seed):
    from rdkit import Chem
    from rdkit.Chem import AllChem

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None or len(Chem.GetMolFrags(molecule)) != 1:
        raise ValueError("MD requires one valid, connected ligand SMILES")
    allowed = {1, 6, 7, 8, 9, 15, 16, 17, 35, 53}
    if any(atom.GetAtomicNum() not in allowed for atom in molecule.GetAtoms()):
        raise ValueError("Generic GAFF2 preparation supports organic ligands only; provide a prepared system for this chemistry")
    charge = Chem.GetFormalCharge(molecule)
    molecule = Chem.AddHs(molecule)
    parameters = AllChem.ETKDGv3()
    parameters.randomSeed = int(seed)
    if AllChem.EmbedMolecule(molecule, parameters) != 0:
        raise ValueError("RDKit could not generate ligand coordinates")
    if AllChem.MMFFHasAllMoleculeParams(molecule):
        AllChem.MMFFOptimizeMolecule(molecule, maxIters=1000)
    else:
        AllChem.UFFOptimizeMolecule(molecule, maxIters=1000)
    with Chem.SDWriter(str(destination)) as writer:
        writer.write(molecule)
    return charge


def _combine_gro(protein, ligand, destination):
    first, second = protein.read_text().splitlines(), ligand.read_text().splitlines()
    nfirst, nsecond = int(first[1]), int(second[1])
    atoms = first[2:2 + nfirst] + second[2:2 + nsecond]
    # GRO atom serials wrap at 100000; coordinates remain in the docking frame.
    atoms = [line[:15] + f"{(index + 1) % 100000:5d}" + line[20:]
             for index, line in enumerate(atoms)]
    destination.write_text("Protein and docked ligand\n" + str(len(atoms)) + "\n"
                           + "\n".join(atoms) + "\n" + first[2 + nfirst] + "\n")


def _add_ligand_topology(directory, ligand_itp):
    content = ligand_itp.read_text()
    molecule_section = re.search(r"^\s*\[\s*moleculetype\s*\]", content, re.M)
    if molecule_section is None:
        raise ValueError("ACPYPE output has no moleculetype")
    prefix, molecule = content[:molecule_section.start()], content[molecule_section.start():]
    lines = molecule.splitlines()[1:]
    molecule_name = next(line.split()[0] for line in lines
                         if line.strip() and not line.lstrip().startswith(";"))
    (directory / "ligand_atomtypes.itp").write_text(prefix)
    (directory / "ligand.itp").write_text(molecule)
    # ACPYPE ligand position restraints may be included under POSRES_LIG.
    for include in re.findall(r'^\s*#include\s+"([^\"]+)"', molecule, re.M):
        relative = Path(include)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("ACPYPE topology contains a nonlocal include")
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ligand_itp.parent / relative, target)
    topology = directory / "topol.top"
    top = topology.read_text()
    forcefield = re.search(r'^\s*#include\s+"[^\"]*forcefield\.itp"[^\n]*', top, re.M)
    if forcefield is None:
        raise ValueError("Protein topology has no forcefield include")
    insertion = '\n#include "ligand_atomtypes.itp"\n#include "ligand.itp"'
    top = top[:forcefield.end()] + insertion + top[forcefield.end():]
    topology.write_text(top.rstrip() + f"\n{molecule_name}    1\n")


def _fresh_system(row, target, settings, job, env):
    def call(key, args, log, input_text=None):
        _command(_tool(settings, key) + args, job, log, env, input_text)

    _protein_only(target / "protein.pdb", job / "protein.pdb")
    charge = _ligand_sdf(row["smiles"], job / "ligand.sdf", settings.get("seed", 2026))
    call("prepare_receptor", ["-i", "protein.pdb", "-o", "receptor", "-p"], "receptor.log")
    call("prepare_ligand", ["-i", "ligand.sdf", "-o", "ligand.pdbqt"], "ligand_prepare.log")
    geometry = json.loads((target / "target.json").read_text())
    center, box = geometry["pocket_center"], geometry["box_size"]
    if len(center) != 3 or len(box) != 3 or not all(math.isfinite(float(v)) for v in center + box) or any(float(v) <= 0 for v in box):
        raise ValueError("Docking pocket_center/box_size must be three finite Angstrom values; box sizes must be positive")
    args = ["--receptor", "receptor.pdbqt", "--ligand", "ligand.pdbqt", "--out", "docked.pdbqt",
            "--exhaustiveness", str(settings.get("exhaustiveness", 8)), "--num_modes", "1",
            "--cpu", str(settings.get("threads", 4)), "--seed", str(settings.get("seed", 2026))]
    for axis, value, width in zip("xyz", center, box):
        args += [f"--center_{axis}", str(value), f"--size_{axis}", str(width)]
    call("vina", args, "docking.log")
    affinity = re.search(r"REMARK VINA RESULT:\s*([-+\d.eE]+)", (job / "docked.pdbqt").read_text())
    if affinity is None:
        raise ValueError("Vina produced no scored pose")
    call("export_ligand", ["docked.pdbqt", "-s", "docked.sdf"], "pose_export.log")
    call("obabel", ["-isdf", "docked.sdf", "-omol2", "-O", "ligand.mol2", "-h"], "mol2.log")
    call("acpype", ["-i", "ligand.mol2", "-b", "LIG", "-n", str(charge), "-a", "gaff2", "-c", "bcc"], "acpype.log")
    call("gmx", ["pdb2gmx", "-f", "protein.pdb", "-o", "protein.gro", "-p", "topol.top",
                 "-ff", settings.get("forcefield", "amber99sb-ildn"), "-water", settings.get("water", "tip3p"), "-ignh"], "pdb2gmx.log")
    _add_ligand_topology(job, job / "LIG.acpype" / "LIG_GMX.itp")
    ligand_gro = job / "LIG.acpype" / "LIG_GMX.gro"
    residue_names = {line[5:10].strip() for line in ligand_gro.read_text().splitlines()[2:-1]}
    if len(residue_names) != 1:
        raise ValueError("ACPYPE ligand must occupy one residue")
    _combine_gro(job / "protein.gro", ligand_gro, job / "complex.gro")
    call("gmx", ["editconf", "-f", "complex.gro", "-o", "boxed.gro", "-c", "-d", str(settings.get("box_padding_nm", 1.0)), "-bt", "dodecahedron"], "box.log")
    call("gmx", ["solvate", "-cp", "boxed.gro", "-cs", "spc216.gro", "-o", "solvated.gro", "-p", "topol.top"], "solvate.log")
    shutil.copy2(MDP_ROOT / "em.mdp", job / "ions.mdp")
    call("gmx", ["grompp", "-f", "ions.mdp", "-c", "solvated.gro", "-p", "topol.top", "-o", "ions.tpr"], "ions_grompp.log")
    call("gmx", ["genion", "-s", "ions.tpr", "-o", "system.gro", "-p", "topol.top", "-pname", "NA", "-nname", "CL", "-neutral", "-conc", str(settings.get("salt_molar", 0.15))], "ions.log", "SOL\n")
    return job / "system.gro", job / "topol.top", residue_names.pop(), float(affinity.group(1))


def _prepared_system(prepared, job):
    source = Path(prepared["directory"]).resolve()
    destination = job / "prepared"
    if source == job or job in source.parents or source in job.parents:
        raise ValueError("Prepared system directory must be outside its MD job directory")
    shutil.copytree(source, destination, dirs_exist_ok=True)
    def local(key, default):
        relative = Path(prepared.get(key, default))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Prepared {key} must be relative to directory")
        result = destination / relative
        if not result.is_file():
            raise FileNotFoundError(result)
        return result
    return local("gro", "system.gro"), local("top", "topol.top"), prepared["ligand_resname"], None


def _mdp(stage, settings, destination):
    values = {}
    for line in (MDP_ROOT / f"{stage}.mdp").read_text().splitlines():
        line = line.split(";", 1)[0]
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    temperature = str(settings.get("temperature_k", 300))
    if stage != "em":
        duration = float(settings.get("production_ns", 5)) * 1000 if stage == "production" else float(settings.get(f"{stage}_ps", 100 if stage == "nvt" else 1000))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("MD stage durations must be finite and positive")
        values["nsteps"] = str(round(duration / float(values["dt"])))
        values["ref-t"] = temperature
        if stage == "nvt":
            values["gen-temp"] = temperature
            values["gen-seed"] = str(settings.get("seed", 2026))
    destination.write_text("\n".join(f"{key} = {value}" for key, value in values.items()) + "\n")
    return int(values["nsteps"])


def _rmsd_mean(path):
    values = [float(line.split()[1]) for line in path.read_text().splitlines()
              if line.strip() and not line.lstrip().startswith(("#", "@"))]
    if not values or not all(math.isfinite(v) for v in values):
        raise ValueError(f"No finite RMSD samples in {path}")
    return statistics.mean(values)


def _simulate(settings, job, coords, topology, ligand_resname, env):
    gmx = _tool(settings, "gmx")
    checkpoint = None
    for stage in ("em", "nvt", "npt", "production"):
        required_steps = _mdp(stage, settings, job / f"{stage}.mdp")
        args = gmx + ["grompp", "-f", job / f"{stage}.mdp", "-c", coords, "-p", topology, "-o", job / f"{stage}.tpr", "-po", job / f"{stage}_resolved.mdp"]
        if checkpoint:
            args += ["-t", checkpoint]
        # Local includes resolve beside the supplied topology, also for prepared systems.
        _command(args, topology.parent, f"{stage}_grompp.log", env)
        _command(gmx + ["mdrun", "-deffnm", stage, "-ntmpi", "1", "-ntomp", str(settings.get("threads", 4))] + list(settings.get("mdrun_args", [])), job, f"{stage}_console.log", env)
        content = (job / f"{stage}.log").read_text(errors="replace")
        if re.search(r"LINCS WARNING|Fatal error:|\bNaN\b", content, re.I):
            raise RuntimeError(f"Numerical instability in {stage}.log")
        if stage == "em" and not re.search(r"converged to Fmax\s*<", content):
            raise RuntimeError("Energy minimization did not converge to the requested force tolerance")
        if stage != "em":
            steps = re.findall(r"^\s*Step\s+Time\s*\n\s*(\d+)\s+[-+\d.eE]+", content, re.M)
            if not steps or int(steps[-1]) < required_steps:
                raise RuntimeError(f"{stage} stopped before its configured {required_steps} integration steps")
        coords = job / f"{stage}.gro"
        checkpoint = job / f"{stage}.cpt" if stage != "em" else None
    if not re.fullmatch(r"[A-Za-z0-9_+-]+", ligand_resname):
        raise ValueError("ligand_resname must be a single GROMACS residue name")
    _command(gmx + ["trjconv", "-s", "production.tpr", "-f", "production.xtc", "-o", "centered.xtc", "-pbc", "mol", "-center"], job, "center.log", env, "Protein\nSystem\n")
    _command(gmx + ["select", "-s", "production.tpr", "-select", f'group "Backbone"; (resname "{ligand_resname}") and not name "H*"', "-on", "rmsd.ndx"], job, "index.log", env)
    for kind, group in (("backbone", "0"), ("ligand", "1")):
        _command(gmx + ["rms", "-s", "production.tpr", "-f", "centered.xtc", "-n", "rmsd.ndx", "-o", f"{kind}_rmsd.xvg", "-tu", "ns"], job, f"{kind}_rmsd.log", env, f"0\n{group}\n")
    return _rmsd_mean(job / "backbone_rmsd.xvg"), _rmsd_mean(job / "ligand_rmsd.xvg")


def run(config, output: Path):
    """Write results.csv and return completed/partial/failed/skipped stage status."""
    output = Path(output).resolve()
    if any(output == Path(p) or Path(p) in output.parents for p in ("/home", "/tmp")):
        raise ValueError("MD output must be outside /home and /tmp")
    output.mkdir(parents=True, exist_ok=True)
    root = Path(config["run"]["output"]).resolve()
    settings = config.get("md", {})
    with (root / "selection" / "md_candidates.csv").open(newline="", encoding="utf-8-sig") as stream:
        candidates = list(csv.DictReader(stream))
    completed = {}
    previous_results = output / "results.csv"
    if previous_results.is_file():
        with previous_results.open(newline="", encoding="utf-8-sig") as stream:
            completed = {(row["compound_id"], row["smiles"]): row
                         for row in csv.DictReader(stream) if row.get("status") == "success"}
    results = []
    seen = set()
    enabled = settings.get("enabled", True)
    for number, candidate in enumerate(candidates):
        identifier = candidate["compound_id"]
        if identifier in seen:
            continue
        seen.add(identifier)
        previous = completed.get((identifier, candidate.get("smiles", "")))
        if enabled and previous is not None:
            results.append(previous)
            _write_results(output / "results.csv", results)
            continue
        row = {key: candidate.get(key, "") for key in ("compound_id", "smiles", "name")}
        row.update(status="skipped" if not enabled else "failed", error="MD disabled" if not enabled else "")
        if enabled:
            safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", identifier)[:70] or "ligand"
            job = output / f"{number + 1:03d}_{safe_name}"
            job.mkdir(parents=True, exist_ok=True)
            row["workdir"] = str(job)
            try:
                env = _environment(job)
                prepared = settings.get("prepared_systems", {}).get(identifier)
                if prepared:
                    coords, topology, residue, affinity = _prepared_system(prepared, job)
                else:
                    if not settings.get("forcefield", "amber99sb-ildn").startswith("amber"):
                        raise ValueError("Generic GAFF2 systems require an AMBER protein forcefield")
                    coords, topology, residue, affinity = _fresh_system(candidate, root / "target", settings, job, env)
                backbone, ligand = _simulate(settings, job, coords, topology, residue, env)
                row.update(status="success", docking_affinity_kcal_mol=affinity if affinity is not None else "",
                           production_ns=settings.get("production_ns", 5), backbone_rmsd_mean_nm=backbone,
                           ligand_rmsd_mean_nm=ligand)
            except Exception as error:
                row["error"] = str(error)
        results.append(row)
        # Persist each completed ligand so an interrupted batch retains prior outcomes.
        _write_results(output / "results.csv", results)
    if not results:
        _write_results(output / "results.csv", results)
    successful = sum(row["status"] == "success" for row in results)
    status = "skipped" if not candidates or not enabled else "completed" if successful == len(results) else "partial" if successful else "failed"
    return {"status": status, "success_count": successful, "candidate_count": len(results),
            "reason": "No selected MD candidates" if not candidates else "MD disabled" if not enabled else "",
            "results": str(output / "results.csv")}


def _write_results(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

