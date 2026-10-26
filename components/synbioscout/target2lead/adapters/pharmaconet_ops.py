"""Inference helpers adapted from the exercised model1 adapters."""
import io
import time
import math
MODEL = None
OPTIONS = None


def conformer_sdf(smiles, options):
    from rdkit import Chem
    from rdkit.Chem import rdDistGeom
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError("INVALID_SMILES")
    mol = Chem.AddHs(mol)
    params = rdDistGeom.srETKDGv3()
    params.randomSeed = int(options["random_seed"])
    params.numThreads = 1
    conformers = list(rdDistGeom.EmbedMultipleConfs(mol, int(options["conformer_count"]), params))
    if not conformers:
        raise ValueError("NO_CONFORMERS")
    buffer = io.StringIO()
    with Chem.SDWriter(buffer) as writer:
        for conformer_id in conformers:
            writer.write(mol, confId=conformer_id)
        writer.flush()
        text = buffer.getvalue()
    return text, len(conformers)


def score_sdf_in_memory(sdf_text):
    """Use the official API with exactly the same quantized atoms as SDF I/O."""
    import numpy as np
    from openbabel import pybel
    molecules = []
    positions = []
    for record in sdf_text.split("$$$$\n"):
        if not record.strip():
            continue
        # Retain the blank molecule title: stripping it corrupts Molfile fields.
        molecule = pybel.readstring("sdf", record + "$$$$\n")
        molecule.removeh()
        molecules.append(molecule)
        positions.append(np.array([atom.coords for atom in molecule.atoms], dtype=np.float32))
    if not molecules:
        raise ValueError("NO_CONFORMERS")
    if any(len(molecule.atoms) != len(molecules[0].atoms) for molecule in molecules):
        raise ValueError("CONFORMER_ATOM_COUNT_MISMATCH")
    return float(MODEL.scoring_pbmol(molecules[0], positions))


def initialize_worker(options):
    global MODEL, OPTIONS
    from pmnet.pharmacophore_model import PharmacophoreModel
    MODEL = PharmacophoreModel.load(options["pharmacophore"])
    OPTIONS = options


def score_molecule(item):
    index, compound_id, smiles, state_id = item
    started = time.monotonic()
    result = dict(master_row=index, compound_id=compound_id, input_state_id=state_id,
                  status="failed", raw_score=None, error_code=None, error_message=None,
                  requested_conformers=int(OPTIONS["conformer_count"]), actual_conformers=0,
                  eligible=True, eligible_index=index)
    result["conformer_state_id"] = (f"{state_id}:srETKDGv3:seed{OPTIONS['random_seed']}"
                                    f":requested{OPTIONS['conformer_count']}")
    try:
        sdf_text, result["actual_conformers"] = conformer_sdf(smiles, OPTIONS)
        score = score_sdf_in_memory(sdf_text)
        if not math.isfinite(score):
            raise ValueError("NONFINITE_OFFICIAL_SCORE")
        result.update(status="success", raw_score=score)
    except Exception as error:
        result["error_code"] = type(error).__name__
        result["error_message"] = str(error)[:2000]
        if str(error) == "INVALID_SMILES":
            result["status"] = "invalid_structure"
    result["runtime_s"] = time.monotonic() - started
    return result

