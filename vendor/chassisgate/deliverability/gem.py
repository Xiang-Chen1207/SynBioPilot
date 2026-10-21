"""Genome-scale metabolic model (GEM) access for the mechanistic evidence channels.

The genome (`genome.py`) and FBA (`fba.py`) channels are **INPUTS** to the agent
(non-circular iron law): they feed the estimator and are never the gold judge.
Gold labels come from human + literature adjudication and LC-MS.

The six benchmark GEM files are not redistributed in the anonymous artifact.
Reviewers should download/place them under `data/gems/` as documented there.
This module only loads/caches those local inputs and maps a candidate molecule
to a model metabolite.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import cobra
from cobra.io import read_sbml_model

from .schema import Candidate

# chassis_id -> SBML filename expected under `models_dir`.
DEFAULT_REGISTRY = {
    "ecoli": "iML1515.xml",
    "bsubtilis": "iYO844.xml",
    "scerevisiae": "iMM904.xml",
    "lplantarum": "iBT721.xml",  # L. plantarum WCFS1, Teusink 2006
    "llactis": "iNF517.xml",        # Lactococcus lactis MG1363, Flahaut 2013, BiGG iNF517
    "cglutamicum": "iCW773.xml",    # Corynebacterium glutamicum ATCC 13032, Zhang 2017, BioModels MODEL2110010001
}


class GemProvider:
    """Loads (and caches) a cobra model per chassis id.

    Tests and in-memory callers can bypass disk via `register_model`.
    """

    def __init__(self, models_dir: Optional[Path] = None, registry: Optional[dict[str, str]] = None):
        self.models_dir = Path(models_dir) if models_dir else None
        self.registry = dict(DEFAULT_REGISTRY)
        if registry:
            self.registry.update(registry)
        self._cache: dict[str, cobra.Model] = {}

    def register_model(self, chassis_id: str, model: "cobra.Model") -> None:
        self._cache[chassis_id] = model

    def load(self, chassis_id: str) -> Optional["cobra.Model"]:
        if chassis_id in self._cache:
            return self._cache[chassis_id]
        fname = self.registry.get(chassis_id)
        if not fname or self.models_dir is None:
            return None
        path = self.models_dir / fname
        if not path.exists():
            return None
        try:
            model = read_sbml_model(str(path))
        except Exception:
            return None
        self._cache[chassis_id] = model
        return model


def _norm(s: str) -> str:
    return "".join(s.lower().split())


def _annotation_values(ann: dict, keys: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for k in keys:
        v = ann.get(k)
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, (list, tuple)):
            out.extend(str(x) for x in v)
    return out


def map_metabolite(
    model: "cobra.Model",
    *,
    inchikey: Optional[str] = None,
    kegg: Optional[str] = None,
    name: Optional[str] = None,
) -> Optional["cobra.Metabolite"]:
    """Map an external identifier to a model metabolite.

    Priority: InChIKey annotation (full, then 14-char skeleton) > KEGG annotation
    > exact (whitespace/case-insensitive) name match. Prefers a cytosolic
    compartment when several matches exist.
    """
    matches: list["cobra.Metabolite"] = []
    if inchikey:
        ik = inchikey.strip()
        ik14 = ik.split("-")[0]
        for met in model.metabolites:
            vals = _annotation_values(met.annotation or {}, ("inchikey", "inchi_key"))
            if any(v == ik or v.split("-")[0] == ik14 for v in vals):
                matches.append(met)
    if not matches and kegg:
        for met in model.metabolites:
            vals = _annotation_values(met.annotation or {}, ("kegg.compound", "kegg"))
            if kegg in vals:
                matches.append(met)
    if not matches and name:
        target = _norm(name)
        for met in model.metabolites:
            if met.name and _norm(met.name) == target:
                matches.append(met)
    if not matches:
        return None
    for met in matches:
        if met.compartment in ("c", "cytosol", "c0"):
            return met
    return matches[0]


def candidate_metabolite(model: "cobra.Model", candidate: Candidate) -> Optional["cobra.Metabolite"]:
    return map_metabolite(model, inchikey=candidate.inchikey, name=candidate.name)
