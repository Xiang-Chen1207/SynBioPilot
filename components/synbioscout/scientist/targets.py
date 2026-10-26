"""Prepare traceable, immutable experimental target inputs for Target2Lead.

The reference molecule is an RCSB *instance*, never CCD ideal coordinates or a
PubChem conformer. This is coordinate preparation, not protonation, biological
site validation, or an MD system builder; those limits are persisted explicitly.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import TargetPackage, TargetPackageRequest

UNIPROT = "https://rest.uniprot.org/uniprotkb"
DATA = "https://data.rcsb.org/rest/v1/core"
SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
FILES = "https://files.rcsb.org/download"
MODELS = "https://models.rcsb.org/v1"
PREPARATION_VERSION = 2
# Conservative exclusion: explicit requests remain possible but require review.
COFACTORS = {
    "ATP", "ADP", "AMP", "GTP", "GDP", "GMP", "CTP", "CDP", "CMP", "UTP", "UDP",
    "UMP", "NAD", "NAP", "NAI", "NDP", "FAD", "FMN", "HEM", "HEC", "HEA", "COA",
    "SAM", "SAH", "PLP", "PMP", "THF", "B12", "SF4", "FES", "MTA",
}
ADDITIVES = {
    "HOH", "DOD", "WAT", "SO4", "PO4", "GOL", "EDO", "PEG", "PGE", "PG4", "PG5",
    "DMS", "ACT", "ACE", "FMT", "TRS", "MES", "HEP", "MPD", "BME", "DTT", "IPA",
    "EOH", "MOH", "CIT", "CL", "BR", "IOD", "NA", "K", "CA", "MG", "MN", "ZN",
    "FE", "CU", "CO", "NI", "CD", "HG", "CS", "LI", "BA", "SR",
    "NAG", "NDG", "BMA", "MAN", "FUC", "GAL", "GLC", "BGC",
}


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class _Preparation:
    def __init__(self, request: TargetPackageRequest, directory: Path, http: Any):
        self.request, self.directory, self.http = request, directory, http
        self.raw = directory / "raw"
        self.raw.mkdir()
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.checks: dict[str, Any] = {}
        self.metadata: dict[str, Any] = {
            "preparation_version": PREPARATION_VERSION,
            "request": request.model_dump(),
            "retrieved_at": datetime.now(UTC).isoformat(),
            "sources": [],
            "limitations": [
                "Experimental asymmetric unit; biological assembly/site must be confirmed.",
                "No hydrogen addition, protonation, missing-atom repair or MD parameterization.",
                "Automatic ligand relevance uses conservative exclusions and contacts, not pharmacology.",
            ],
        }
        self.artifacts = {"protein_pdb": "", "sequence_fasta": "", "reference_ligand_sdf": ""}
        self.chain = ""
        self.center: list[float] = []
        self.readiness: dict[str, dict[str, Any]] = {}

    def fetch(self, kind: str, url: str, filename: str, params: Any = None,
              payload: Any = None) -> Any:
        if payload is not None:
            value = self.http.post_json(url, payload)
        else:
            value = getattr(self.http, "get_" + kind)(url, params=params)
        raw = (json.dumps(value, sort_keys=True).encode() if kind == "json"
               else value.encode() if isinstance(value, str) else value)
        (self.raw / filename).write_bytes(raw)
        self.metadata["sources"].append({
            "url": url, "params": params, "payload": payload,
            "raw_file": str(self.raw / filename), "sha256": _sha(raw),
        })
        return value

    def issue(self, name: str, passed: bool, details: Any, *, review: bool = False) -> None:
        self.checks[name] = {"passed": bool(passed), "details": details}
        if not passed:
            (self.warnings if review else self.errors).append(name)

    def finish(self) -> TargetPackage:
        status = "blocked" if self.errors else "needs_review" if self.warnings else "ready"
        if self.errors:
            for item in self.readiness.values():
                item["ready"] = False
                item["reason"] = "Package blocked: " + ", ".join(self.errors)
        self.checks["blocking_issues"] = self.errors
        self.checks["review_issues"] = self.warnings
        self.metadata["artifact_sha256"] = {
            key: _sha(Path(value).read_bytes()) for key, value in self.artifacts.items() if value
        }
        package = TargetPackage(
            package_id=self.directory.name, status=status, **self.artifacts, chain=self.chain,
            pocket_center=self.center, model_readiness=self.readiness, checks=self.checks,
            metadata=self.metadata,
        )
        _json(self.directory / "metadata.json", self.metadata)
        _json(self.directory / "checks.json", self.checks)
        _json(self.directory / "package.json", package.model_dump())
        return package

    def uniprot(self) -> tuple[str, int]:
        accession = self.request.uniprot_id.upper().strip()
        if not re.fullmatch(r"[A-Z0-9]{6,10}", accession):
            raise ValueError("A canonical UniProt accession is required; isoforms need explicit mapping")
        entry = self.fetch("json", f"{UNIPROT}/{accession}.json", "uniprot.json")
        fasta = self.fetch("text", f"{UNIPROT}/{accession}.fasta", "uniprot.fasta")
        self.issue("uniprot_identity", entry.get("primaryAccession") == accession,
                   {"requested": accession, "returned": entry.get("primaryAccession")})
        sequence = "".join(line.strip() for line in fasta.splitlines()
                           if not line.startswith(">"))
        valid = (sum(line.startswith(">") for line in fasta.splitlines()) == 1 and sequence
                 and not set(sequence) - set("ACDEFGHIKLMNPQRSTVWYXUBZO")
                 and sequence == entry.get("sequence", {}).get("value"))
        self.issue("uniprot_sequence", bool(valid), {"length": len(sequence)})
        organism = entry.get("organism", {})
        taxon = int(organism.get("taxonId", 0))
        aliases = {str(taxon), organism.get("scientificName", "").casefold(),
                   organism.get("commonName", "").casefold()}
        aliases.discard("")
        if taxon == 9606:
            aliases.add("human")
        self.issue("requested_species", self.request.species.casefold().strip() in aliases,
                   {"requested": self.request.species, "organism": organism})
        self.metadata["uniprot"] = {
            "accession": entry.get("primaryAccession"), "organism": organism,
            "genes": entry.get("genes", []), "proteinDescription": entry.get("proteinDescription"),
            "canonical_length": len(sequence), "sequence_sha256": _sha(sequence.encode()),
        }
        return sequence, taxon

    def entry(self, pdb: str, taxon: int) -> dict[str, Any]:
        entry = self.fetch("json", f"{DATA}/entry/{pdb}", f"{pdb}_entry.json")
        ids = entry.get("rcsb_entry_container_identifiers", {})
        entities = []
        for entity_id in ids.get("polymer_entity_ids", [])[:24]:
            entity = self.fetch("json", f"{DATA}/polymer_entity/{pdb}/{entity_id}",
                                f"{pdb}_polymer_{entity_id}.json")
            refs = entity.get("rcsb_polymer_entity_container_identifiers", {}).get(
                "reference_sequence_identifiers", [])
            if any(ref.get("database_name") == "UniProt" and
                   ref.get("database_accession") == self.request.uniprot_id.upper() for ref in refs):
                entities.append(entity)
        ligands = []
        for entity_id in ids.get("non_polymer_entity_ids", [])[:40]:
            ligand = self.fetch("json", f"{DATA}/nonpolymer_entity/{pdb}/{entity_id}",
                                f"{pdb}_nonpolymer_{entity_id}.json")
            ligands.append(ligand)
        resolution = entry.get("rcsb_entry_info", {}).get("resolution_combined", [99]) or [99]
        return {"pdb": pdb, "entry": entry, "entities": entities, "ligands": ligands,
                "resolution": min(float(x) for x in resolution), "taxon": taxon}

    @staticmethod
    def ligand_info(ligand: dict[str, Any]) -> tuple[str, bool]:
        component = ligand.get("pdbx_entity_nonpoly", {})
        comp = component.get("comp_id", "").upper()
        name = str(component.get("name", "")).lower()
        safe = (bool(comp) and comp not in COFACTORS | ADDITIVES and
                not any(term in name for term in ("cofactor", "heme", "flavin", "buffer")))
        return comp, safe

    def choose_entry(self, taxon: int) -> dict[str, Any] | None:
        if self.request.pdb_id:
            pdb = self.request.pdb_id.upper().strip()
            if not re.fullmatch(r"[0-9][A-Z0-9]{3}", pdb):
                raise ValueError("Expected a four-character experimental PDB identifier")
            return self.entry(pdb, taxon)
        payload = {
            "query": {"type": "group", "logical_operator": "and", "nodes": [
                {"type": "terminal", "service": "text", "parameters": {
                    "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession",
                    "operator": "exact_match", "value": self.request.uniprot_id.upper()}},
                {"type": "terminal", "service": "text", "parameters": {
                    "attribute": "rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_name",
                    "operator": "exact_match", "value": "UniProt"}},
                {"type": "terminal", "service": "text", "parameters": {
                    # ncbi_taxonomy_id exists in Data metadata but is not a
                    # searchable attribute. The Search schema indexes lineage
                    # IDs as strings; actual species is checked again below.
                    "attribute": "rcsb_entity_source_organism.taxonomy_lineage.id",
                    "operator": "exact_match", "value": str(taxon)}},
            ]},
            "return_type": "polymer_entity",
            "request_options": {"paginate": {"start": 0, "rows": 12},
                                "results_content_type": ["experimental"]},
        }
        search = self.fetch("json", SEARCH, "rcsb_search.json", payload=payload)
        pdbs = list(dict.fromkeys(hit["identifier"].split("_")[0].upper()
                                 for hit in search.get("result_set", [])))[:4]
        candidates = []
        failures = []
        for pdb in pdbs:
            try:
                candidate = self.entry(pdb, taxon)
                candidate["eligible_ligands"] = [self.ligand_info(x)[0] for x in candidate["ligands"]
                                                 if self.ligand_info(x)[1]]
                if candidate["entities"] and self.entity_taxa(candidate["entities"][0]) == {taxon}:
                    candidates.append(candidate)
            except Exception as exc:  # noqa: BLE001 -- retain individual provider failures
                failures.append({"pdb": pdb, "error": f"{type(exc).__name__}: {exc}"})
        self.metadata["structure_search"] = {
            "limit": 4, "failures": failures,
            "taxonomy_filter": {"attribute": "rcsb_entity_source_organism.taxonomy_lineage.id",
                                "operator": "exact_match", "value": str(taxon),
                                "actual_entity_taxon_check": "exact source taxon set",
                                "schema_url": "https://search.rcsb.org/rcsbsearch/v2/metadata/schema"},
            "candidates": [{"pdb": x["pdb"], "resolution": x["resolution"],
                            "eligible_ligands": x["eligible_ligands"]} for x in candidates],
        }
        if not candidates:
            self.issue("experimental_structure_available", False, "No matching experimental structure")
            return None
        candidates.sort(key=lambda x: (not bool(x["eligible_ligands"]), x["resolution"], x["pdb"]))
        return candidates[0]

    @staticmethod
    def entity_taxa(entity: dict[str, Any]) -> set[int]:
        return {int(source["ncbi_taxonomy_id"]) for source in
                entity.get("rcsb_entity_source_organism", []) if source.get("ncbi_taxonomy_id")}

    def prepare(self, canonical: str, taxon: int, candidate: dict[str, Any]) -> None:
        import numpy as np
        from Bio.PDB import MMCIFParser, is_aa
        from Bio.SeqUtils import seq1

        pdb = candidate["pdb"]
        entry = candidate["entry"]
        methods = [x.get("method", "") for x in entry.get("exptl", [])]
        experimental = bool(methods) and not any("THEORETICAL" in m.upper() for m in methods)
        self.issue("experimental_structure", experimental, methods)
        self.metadata["structure"] = {
            "pdb_id": pdb, "resolution_angstrom": candidate["resolution"],
            "experimental_methods": methods, "title": entry.get("struct", {}).get("title", ""),
            "biological_assembly": "asymmetric_unit",
        }
        if not candidate["entities"]:
            self.issue("structure_uniprot_mapping", False, "No polymer entity maps to requested UniProt")
            return
        cif = self.fetch("text", f"{FILES}/{pdb}.cif", f"{pdb}.cif")
        structure = MMCIFParser(QUIET=True).get_structure(pdb, io.StringIO(cif))
        models = [model for model in structure if int(model.serial_num) == self.request.model_number]
        self.issue("model_number", len(models) == 1,
                   {"requested": self.request.model_number,
                    "available": [m.serial_num for m in structure]})
        if not models:
            return
        model = models[0]
        chain_entities = {}
        for entity in candidate["entities"]:
            identifiers = entity.get("rcsb_polymer_entity_container_identifiers", {})
            for chain in identifiers.get("auth_asym_ids", []):
                chain_entities[chain] = entity
        requested_chain = self.request.protein_chain
        if requested_chain:
            eligible = [requested_chain] if requested_chain in chain_entities and requested_chain in model else []
        else:
            eligible = sorted(chain for chain in chain_entities if chain in model)
        self.issue("protein_chain_identity", bool(eligible),
                   {"requested": requested_chain, "matching_chains": sorted(chain_entities)})
        if not eligible:
            return
        # Choose a bound chain by ligand contacts when the request does not name one.
        ligand_codes = {self.ligand_info(x)[0]: self.ligand_info(x)[1] for x in candidate["ligands"]}
        instances = []
        for chain in model:
            for residue in chain:
                if residue.id[0] == " " or is_aa(residue, standard=False):
                    continue
                code = residue.resname.strip().upper()
                if self.request.ligand_id:
                    if code != self.request.ligand_id.upper():
                        continue
                elif not ligand_codes.get(code, False):
                    continue
                if self.request.ligand_chain and chain.id != self.request.ligand_chain:
                    continue
                label = str(residue.id[1]) + residue.id[2].strip()
                if self.request.ligand_residue and label != self.request.ligand_residue:
                    continue
                atoms = self.heavy_atoms(residue)
                if len(atoms) >= 5:
                    instances.append((chain.id, residue, np.array([a.coord for a in atoms])))
        ranked = []
        for chain_id in eligible:
            residues = [r for r in model[chain_id] if is_aa(r, standard=False)]
            protein_atoms = [a for r in residues for a in self.heavy_atoms(r)]
            coords = np.array([a.coord for a in protein_atoms])
            for ligand_chain, ligand, ligand_coords in instances:
                if coords.size and np.isfinite(coords).all() and np.isfinite(ligand_coords).all():
                    distance = np.linalg.norm(coords[:, None, :] - ligand_coords[None, :, :], axis=2)
                    close = sum(float(np.linalg.norm(
                        np.array([a.coord for a in self.heavy_atoms(r)])[:, None, :] -
                        ligand_coords[None, :, :], axis=2).min()) <= 6 for r in residues
                                if self.heavy_atoms(r))
                    ranked.append((close, int((distance <= 4.5).sum()), chain_id,
                                   ligand_chain, ligand, ligand_coords))
        ranked.sort(key=lambda x: (-x[0], -x[1], x[2], x[3], x[4].id[1]))
        chain_id = ranked[0][2] if ranked and not requested_chain else eligible[0]
        self.chain = chain_id if len(chain_id) == 1 else "A"
        entity = chain_entities[chain_id]
        self.issue("structure_species", self.entity_taxa(entity) == {taxon},
                   {"expected_taxon": taxon, "source_taxa": sorted(self.entity_taxa(entity))})
        self.metadata["structure"].update({
            "source_auth_chain": chain_id, "prepared_chain": self.chain,
            "entity_identifiers": entity.get("rcsb_polymer_entity_container_identifiers", {}),
            "construct_sequence": entity.get("entity_poly", {}).get("pdbx_seq_one_letter_code_can", ""),
            "deposited_construct": entity.get("rcsb_polymer_entity", {}),
            "reference_alignments": entity.get("rcsb_polymer_entity_align", []),
        })
        protein_residues = [r for r in model[chain_id] if is_aa(r, standard=False)]
        observed = "".join(seq1(r.resname, custom_map={"MSE": "M", "SEC": "U", "PYL": "O"})
                           for r in protein_residues)
        protein_coords = np.array([a.coord for r in protein_residues for a in self.heavy_atoms(r)])
        self.issue("protein_coordinates", bool(protein_coords.size) and bool(np.isfinite(protein_coords).all()),
                   {"heavy_atoms": len(protein_coords), "observed_residues": len(observed)})
        if not protein_coords.size or not np.isfinite(protein_coords).all():
            return
        mapped = self.sequence_mapping(canonical, observed, protein_residues, entity)
        sequence = canonical if self.request.sequence_scope == "canonical" else observed
        description = f"{self.request.uniprot_id}|{pdb}|chain={chain_id}|scope={self.request.sequence_scope}"
        fasta_file = self.directory / "sequence.fasta"
        fasta_file.write_text(">" + description + "\n" + "\n".join(
            sequence[i:i+80] for i in range(0, len(sequence), 80)) + "\n")
        self.artifacts["sequence_fasta"] = str(fasta_file)
        selected = [x for x in ranked if x[2] == chain_id and x[0] >= 3 and x[1] >= 5]
        ligand = selected[0] if selected else None
        self.issue("bound_reference_ligand", ligand is not None,
                   {"requested": self.request.ligand_id,
                    "candidates": [{"id": x[4].resname, "chain": x[3],
                                    "residue": str(x[4].id[1]) + x[4].id[2].strip(),
                                    "contact_residues_6A": x[0], "contact_atom_pairs_4_5A": x[1]}
                                   for x in ranked[:20]]}, review=not bool(self.request.ligand_id))
        ligand_coords = None
        reference_valid = False
        if ligand:
            _, pairs, _, ligand_chain, ligand_residue, ligand_coords = ligand
            identities = {x[4].resname for x in selected}
            sites = [x for x in selected if x[4].resname == ligand_residue.resname]
            explicit_site = bool(self.request.ligand_chain and self.request.ligand_residue)
            self.issue("reference_ligand_identity", bool(self.request.ligand_id),
                       {"identities": sorted(identities),
                        "query_ligand_explicitly_requested": bool(self.request.ligand_id),
                        "reason": "Automatically located ligands require biological site/role confirmation"},
                       review=True)
            self.issue("reference_site_unambiguous", len(sites) == 1 or explicit_site,
                       {"bound_instances_on_chain": len(sites), "explicit_site": explicit_site}, review=True)
            self.issue("reference_is_query_ligand", ligand_codes.get(ligand_residue.resname, False),
                       {"component": ligand_residue.resname, "cofactor_or_additive":
                        not ligand_codes.get(ligand_residue.resname, False)}, review=True)
            params = {"auth_asym_id": ligand_chain, "auth_seq_id": ligand_residue.id[1],
                      "label_comp_id": ligand_residue.resname, "model_nums": self.request.model_number,
                      "encoding": "sdf"}
            if ligand_residue.id[2].strip():
                params["pdbx_PDB_ins_code"] = ligand_residue.id[2].strip()
            sdf = self.fetch("text", f"{MODELS}/{pdb.lower()}/ligand", "reference_instance.sdf", params)
            reference_valid = self.prepare_sdf(sdf, ligand_residue)
            self.center = [float(v) for v in ligand_coords.mean(axis=0)]
            self.metadata["reference_ligand"] = {
                "component_id": ligand_residue.resname, "auth_chain": ligand_chain,
                "auth_residue": str(ligand_residue.id[1]) + ligand_residue.id[2].strip(),
                "model_number": self.request.model_number, "source": "RCSB ModelServer instance",
                "contact_residues_6A": ligand[0], "contact_atom_pairs_4_5A": pairs,
            }
            self.pocket_quality(protein_residues, mapped, ligand_coords)
        self.write_protein(model, chain_id, protein_residues, ligand, ligand_coords)
        length_ok = 1 <= len(observed) <= 1022
        self.issue("sprint_chain_length", length_ok,
                   {"observed_chain_length": len(observed), "maximum": 1022}, review=True)
        query_ready = reference_valid and not any(x in self.warnings for x in
            ("reference_ligand_identity", "reference_site_unambiguous", "reference_is_query_ligand"))
        self.readiness = {
            "drugclip": {"ready": query_ready, "reason": "Validated bound ligand defines 6 Å pocket"
                         if query_ready else "Reference ligand/site needs confirmation"},
            "pharmaconet": {"ready": query_ready, "reason": "Pocket center from experimental ligand"
                           if query_ready else "Validated reference ligand required"},
            "sprint": {"ready": length_ok, "reason": "Selected structural chain; no truncation"
                       if length_ok else "Selected chain exceeds SaProt 1022-residue limit",
                       "structure_sequence_length": len(observed)},
            "psichic": {"ready": bool(sequence), "reason": "Explicit single-sequence FASTA",
                        "sequence_scope": self.request.sequence_scope, "sequence_length": len(sequence)},
            "md": {"ready": False, "status": "md_needs_prepared_system",
                   "reason": "Requires protonation, topology, ligand/cofactor parameters and assembly review"},
        }

    @staticmethod
    def heavy_atoms(residue: Any) -> list[Any]:
        return [atom for atom in residue.get_atoms() if atom.element.strip().upper() not in {"H", "D"}]

    def sequence_mapping(self, canonical: str, observed: str, residues: list[Any],
                         entity: dict[str, Any]) -> dict[str, Any]:
        from Bio.Align import PairwiseAligner

        aligner = PairwiseAligner()
        aligner.mode = "global"
        aligner.match_score = 2
        aligner.mismatch_score = -3
        aligner.open_gap_score = -7
        aligner.extend_gap_score = -0.5
        aligner.end_gap_score = 0
        if not canonical or not observed:
            self.issue("canonical_fragment_mapping", False, "Empty canonical or structural sequence")
            return {"mapping": [], "missing_positions": [], "mutations": []}
        alignments = aligner.align(canonical, observed)
        alignment = alignments[0]
        try:
            alternative = alignments[1]
            unambiguous = (alignment.aligned.tolist() == alternative.aligned.tolist())
        except IndexError:
            unambiguous = True
        self.issue("sequence_mapping_unambiguous", unambiguous,
                   "Equal-score alternative residue mappings require review", review=True)
        mapping, mutations = [], []
        for canonical_index, observed_index in zip(*alignment.indices):
            if canonical_index < 0 or observed_index < 0:
                continue
            residue = residues[int(observed_index)]
            item = {"canonical_position": int(canonical_index) + 1,
                    "structure_index": int(observed_index),
                    "auth_residue": str(residue.id[1]) + residue.id[2].strip(),
                    "canonical_aa": canonical[int(canonical_index)],
                    "structure_aa": observed[int(observed_index)]}
            mapping.append(item)
            if item["canonical_aa"] != item["structure_aa"]:
                mutations.append(item)
        covered = {item["canonical_position"] for item in mapping}
        expected_positions = set(range(min(covered), max(covered)+1)) if covered else set()
        # SIFTS regions describe the deposited construct, including unresolved
        # terminal residues. Do not label canonical residues outside the construct
        # as structural holes just because this is a domain crystal structure.
        reference_regions = []
        for reference in entity.get("rcsb_polymer_entity_align", []):
            if (reference.get("reference_database_name") == "UniProt" and
                    reference.get("reference_database_accession") == self.request.uniprot_id.upper()):
                for region in reference.get("aligned_regions", []):
                    start, length = int(region.get("ref_beg_seq_id", 0)), int(region.get("length", 0))
                    if start > 0 and length > 0 and start + length - 1 <= len(canonical):
                        expected_positions.update(range(start, start + length))
                        reference_regions.append(region)
        missing = sorted(expected_positions - covered)
        identity = (len(mapping) - len(mutations)) / len(mapping) if mapping else 0
        mapped_fraction = len(mapping) / len(observed)
        result = {"mapping": mapping, "missing_positions": missing, "mutations": mutations,
                  "identity": identity, "observed_mapping_fraction": mapped_fraction,
                  "canonical_coverage": len(covered) / len(canonical),
                  "canonical_range": [min(covered), max(covered)] if covered else [],
                  "provider_reference_regions": reference_regions,
                  "unmapped_structure_residues": len(observed) - len(mapping)}
        self.metadata["sequence_alignment"] = result
        self.issue("canonical_fragment_mapping", identity >= 0.9 and mapped_fraction >= 0.9,
                   {key: value for key, value in result.items() if key != "mapping"})
        self.issue("structure_mutations", not mutations or self.request.allow_mutations,
                   {"mutations": mutations, "allow_mutations": self.request.allow_mutations}, review=True)
        return result

    def prepare_sdf(self, sdf: str, source_residue: Any) -> bool:
        import numpy as np
        from rdkit import Chem

        if sum(line.strip() == "$$$$" for line in sdf.splitlines()) != 1:
            self.issue("single_ligand_sdf", False, "ModelServer returned other than one molecule")
            return False
        supplier = Chem.ForwardSDMolSupplier(io.BytesIO(sdf.encode()), removeHs=False)
        molecules = list(supplier)
        molecule = molecules[0] if len(molecules) == 1 else None
        self.issue("ligand_chemical_parse", molecule is not None,
                   "RDKit sanitization and single-molecule parse")
        if molecule is None or molecule.GetNumConformers() != 1:
            return False
        conformer = molecule.GetConformer()
        conformer.Set3D(True)
        atoms = [(atom.GetSymbol().upper(), np.array(conformer.GetAtomPosition(atom.GetIdx())))
                 for atom in molecule.GetAtoms() if atom.GetAtomicNum() > 1]
        coords = np.array([point for _, point in atoms])
        finite = bool(len(atoms)) and bool(np.isfinite(coords).all())
        self.issue("ligand_coordinates", finite, {"heavy_atoms": len(atoms)})
        if not finite:
            return False
        source = [(a.element.upper(), a.coord) for a in self.heavy_atoms(source_residue)]
        remaining = list(range(len(source)))
        distances = []
        for symbol, point in atoms:
            compatible = [i for i in remaining if source[i][0] == symbol]
            if not compatible:
                distances.append(float("inf"))
                break
            nearest = min(compatible, key=lambda i: float(np.linalg.norm(source[i][1] - point)))
            distances.append(float(np.linalg.norm(source[nearest][1] - point)))
            remaining.remove(nearest)
        frame_ok = len(atoms) == len(source) and not remaining and max(distances, default=999) <= 0.05
        self.issue("ligand_coordinate_frame", frame_ok,
                   {"source_heavy_atoms": len(source), "sdf_heavy_atoms": len(atoms),
                    "maximum_atom_displacement_angstrom": max(distances) if distances and
                     all(np.isfinite(distances)) else None, "tolerance_angstrom": 0.05})
        if not frame_ok:
            return False
        if molecule.GetNumAtoms() > 999 or molecule.GetNumBonds() > 999:
            self.issue("v2000_capacity", False, "Reference exceeds V2000 atom/bond capacity")
            return False
        molecule.SetProp("_Name", source_residue.resname)
        file = self.directory / "reference.sdf"
        file.write_text(Chem.MolToMolBlock(molecule, forceV3000=False) + "\n$$$$\n")
        self.artifacts["reference_ligand_sdf"] = str(file)
        self.issue("single_ligand_sdf", True, "One sanitized, positioned V2000 molecule")
        return True

    def pocket_quality(self, residues: list[Any], mapping: dict[str, Any], ligand_coords: Any) -> None:
        import numpy as np

        def near(index: int) -> bool:
            coords = np.array([a.coord for a in self.heavy_atoms(residues[index])])
            return bool(coords.size) and float(np.linalg.norm(
                coords[:, None, :] - ligand_coords[None, :, :], axis=2).min()) <= 8

        mapped = mapping.get("mapping", [])
        nearby_mutations = [item for item in mapping.get("mutations", []) if near(item["structure_index"])]
        holes = []
        for position in mapping.get("missing_positions", []):
            before = [item for item in mapped if item["canonical_position"] < position]
            after = [item for item in mapped if item["canonical_position"] > position]
            flanks = ([before[-1]] if before else []) + ([after[0]] if after else [])
            if any(near(item["structure_index"]) for item in flanks):
                holes.append(position)
        incomplete = [{"auth_residue": str(r.id[1]) + r.id[2].strip(),
                       "missing_backbone_atoms": sorted({"N", "CA", "C", "O"} - set(r.child_dict))}
                      for i, r in enumerate(residues) if near(i) and
                      {"N", "CA", "C", "O"} - set(r.child_dict)]
        self.issue("pocket_mutations", not nearby_mutations or self.request.allow_mutations,
                   nearby_mutations, review=True)
        self.issue("pocket_missing_residues", not holes, holes, review=True)
        self.issue("pocket_backbone_completeness", not incomplete, incomplete, review=True)

    def write_protein(self, model: Any, source_chain: str, residues: list[Any],
                      ligand: Any, ligand_coords: Any) -> None:
        import numpy as np
        from Bio.PDB import PDBIO, Chain, Model, Structure, is_aa

        prepared = Structure.Structure("prepared")
        prepared_model = Model.Model(0)
        prepared.add(prepared_model)
        chain = Chain.Chain(self.chain)
        prepared_model.add(chain)
        for residue in residues:
            clone = self.clone_residue(residue)
            clone.id = (" ", clone.id[1], clone.id[2])
            chain.add(clone)
        retained = []
        next_residue = max((r.id[1] for r in residues), default=0) + 1
        protein_coords = np.array([a.coord for r in residues for a in self.heavy_atoms(r)])
        for source in model:
            for residue in source:
                if is_aa(residue, standard=False) or residue.id[0] == " " or residue.resname in {"HOH", "WAT", "DOD"}:
                    continue
                if ligand and source.id == ligand[3] and residue.id == ligand[4].id:
                    continue
                coords = np.array([a.coord for a in self.heavy_atoms(residue)])
                if not coords.size or not np.isfinite(coords).all():
                    continue
                # Preserve selected-chain hetero groups and nearby metals/cofactors in other chains.
                near_protein = float(np.linalg.norm(
                    coords[:, None, :] - protein_coords[None, :, :], axis=2).min()) <= 4
                near_ligand = ligand_coords is not None and float(np.linalg.norm(
                    coords[:, None, :] - ligand_coords[None, :, :], axis=2).min()) <= 6
                if source.id == source_chain or near_protein or near_ligand:
                    clone = self.clone_residue(residue)
                    clone.id = (clone.id[0], next_residue, " ")
                    chain.add(clone)
                    retained.append({"component_id": residue.resname, "source_chain": source.id,
                                     "source_residue": str(residue.id[1]) + residue.id[2].strip(),
                                     "prepared_residue": next_residue,
                                     "near_pocket": bool(near_ligand),
                                     "reason": "Preserved for cofactor/metal/assembly review"})
                    next_residue += 1
        file = self.directory / "protein.pdb"
        writer = PDBIO()
        writer.set_structure(prepared)
        writer.save(str(file))
        self.artifacts["protein_pdb"] = str(file)
        self.metadata["retained_hetero_groups"] = retained
        self.metadata["removed"] = {"waters": True, "reference_ligand_from_protein": bool(ligand),
                                    "other_polymer_chains": True}
        self.metadata["alternate_conformer_policy"] = "Bio.PDB highest-occupancy selected atoms; altloc labels cleared"
        self.issue("cofactor_metal_preparation", not retained, retained, review=True)

    @staticmethod
    def clone_residue(residue: Any) -> Any:
        from Bio.PDB import Residue

        clone = Residue.Residue(residue.id, residue.resname, residue.segid)
        for wrapper in residue.get_atoms():
            atom = wrapper.selected_child if hasattr(wrapper, "selected_child") else wrapper
            # Exclude the parent so a long chain is not copied in full per atom.
            selected = copy.deepcopy(atom, {id(atom.parent): None})
            selected.set_altloc(" ")
            selected.disordered_flag = 0
            clone.add(selected)
        return clone


def build_target_package(request: TargetPackageRequest, output: Path, http: Any = None) -> TargetPackage:
    """Build beneath ``output/<fingerprint>``; an existing package is reused, never overwritten.

    Client injection supports deterministic offline fixtures. Failures are persisted as
    blocked packages with source material retained, so a UI can explain the failure.
    """
    if not isinstance(request, TargetPackageRequest):
        request = TargetPackageRequest.model_validate(request)
    fingerprint = _sha(json.dumps({"version": PREPARATION_VERSION, "request": request.model_dump()},
                                  sort_keys=True).encode())
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    label = re.sub(r"[^A-Za-z0-9_-]", "_", request.target_id or request.uniprot_id)[:50]
    directory = output / f"{label}-{fingerprint[:16]}"
    if directory.exists():
        package_file = directory / "package.json"
        if not package_file.is_file():
            raise FileExistsError(f"Incomplete immutable target package: {directory}")
        package = TargetPackage.model_validate_json(package_file.read_text())
        if package.metadata.get("input_fingerprint") != fingerprint:
            raise ValueError("Package fingerprint mismatch; refusing to overwrite")
        for key, expected in package.metadata.get("artifact_sha256", {}).items():
            file = Path(getattr(package, key))
            if not file.is_file() or _sha(file.read_bytes()) != expected:
                raise ValueError(f"Immutable package artifact changed: {key}")
        for source in package.metadata.get("sources", []):
            file = Path(source["raw_file"])
            if not file.is_file() or _sha(file.read_bytes()) != source["sha256"]:
                raise ValueError(f"Immutable package source changed: {file.name}")
        return package
    directory.mkdir()
    if http is None:
        from .http import HTTPClient
        http = HTTPClient(output / ".http-cache")
    preparation = _Preparation(request, directory, http)
    preparation.metadata["input_fingerprint"] = fingerprint
    try:
        # Optional imports fail into a clear persisted package instead of breaking the app.
        from Bio.PDB import MMCIFParser  # noqa: F401
        from rdkit import Chem  # noqa: F401
        canonical, taxon = preparation.uniprot()
        if preparation.errors:
            return preparation.finish()
        candidate = preparation.choose_entry(taxon)
        if candidate:
            preparation.prepare(canonical, taxon, candidate)
    except Exception as exc:  # noqa: BLE001 -- persist blocked artifacts with diagnostic data
        preparation.issue("preparation_error", False,
                          {"type": type(exc).__name__, "message": str(exc)})
    return preparation.finish()

