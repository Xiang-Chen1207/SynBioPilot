"""Offline target preparation contracts; all public data comes from explicit fixtures."""
from __future__ import annotations

import io
import json
import math
from pathlib import Path

import numpy as np
import pytest
from Bio.PDB import MMCIFIO, Atom, Chain, Model, Residue, Structure
from Bio.SeqUtils import seq3
from rdkit import Chem
from rdkit.Geometry import Point3D

from scientist.models import TargetPackageRequest
from scientist.targets import build_target_package


class FixtureHTTP:
    def __init__(self, *, sequence="ACDEFGHIKLMNPQRSTVWY", taxon=9606,
                 mutation=False, missing=False, sdf_shift=0, ligand_id="IQK",
                 no_ligand=False, metal=False, duplicates=False, model_number=1):
        self.calls = []
        self.model_number = model_number
        organism = {"taxonId": 9606, "scientificName": "Homo sapiens", "commonName": "Human"}
        self.uniprot = {"primaryAccession": "Q14145", "sequence": {"value": sequence},
                        "organism": organism, "genes": [{"geneName": {"value": "KEAP1"}}]}
        self.fasta = ">sp|Q14145|KEAP1_HUMAN fixture\n" + sequence + "\n"
        self.entry = {"rcsb_entry_container_identifiers": {
            "polymer_entity_ids": ["1"], "non_polymer_entity_ids": [] if no_ligand else ["2"]},
            "exptl": [{"method": "X-RAY DIFFRACTION"}],
            "rcsb_entry_info": {"resolution_combined": [1.8]}, "struct": {"title": "Fixture"}}
        self.entity = {"rcsb_polymer_entity_container_identifiers": {
            "auth_asym_ids": ["A"], "asym_ids": ["A"], "entity_id": "1",
            "reference_sequence_identifiers": [{"database_name": "UniProt", "database_accession": "Q14145"}]},
            "rcsb_entity_source_organism": [{"ncbi_taxonomy_id": taxon}],
            "entity_poly": {"pdbx_seq_one_letter_code_can": sequence}}
        self.ligand = {"pdbx_entity_nonpoly": {"comp_id": ligand_id, "name": "Synthetic inhibitor"}}
        structure = Structure.Structure("fixture")
        model = Model.Model(0, serial_num=model_number)
        structure.add(model)
        chain = Chain.Chain("A")
        model.add(chain)
        serial = 1
        for i, aa in enumerate(sequence):
            if missing and i == 8:
                continue
            if mutation and i == 8:
                aa = "A" if aa != "A" else "W"
            residue = Residue.Residue((" ", i + 10, " "), seq3(aa).upper(), "")
            chain.add(residue)
            center = np.array([3.5 + i % 3 * 0.5, (i % 5 - 2) * 1.0, (i % 7 - 3) * 0.7])
            for name, offset, element in (
                ("N", [0, 0, 0], "N"), ("CA", [0.3, 0, 0], "C"),
                ("C", [0.6, 0, 0], "C"), ("O", [0.6, 0.3, 0], "O")):
                atom = Atom.Atom(name, center + offset, 20.0, 1.0, " ", name.ljust(4), serial, element)
                residue.add(atom)
                serial += 1
        molecule = Chem.MolFromSmiles("c1ccccc1")
        conformer = Chem.Conformer(molecule.GetNumAtoms())
        points = []
        for i in range(6):
            point = np.array([math.cos(i * math.pi/3) * 1.4, math.sin(i * math.pi/3) * 1.4, 0.0])
            points.append(point)
            conformer.SetAtomPosition(i, Point3D(*(point + sdf_shift)))
        molecule.AddConformer(conformer)
        self.sdf = Chem.MolToMolBlock(molecule) + "\n$$$$\n"
        if not no_ligand:
            residue = Residue.Residue(("H_" + ligand_id, 601, " "), ligand_id, "")
            chain.add(residue)
            for i, point in enumerate(points):
                name = f"C{i+1}"
                residue.add(Atom.Atom(name, point, 20.0, 1.0, " ", name.ljust(4), serial, "C"))
                serial += 1
            if duplicates:
                residue = Residue.Residue(("H_" + ligand_id, 602, " "), ligand_id, "")
                chain.add(residue)
                for i, point in enumerate(points):
                    name = f"C{i+1}"
                    residue.add(Atom.Atom(name, point + [0, 0, 1], 20.0, 1.0, " ", name.ljust(4), serial, "C"))
                    serial += 1
        if metal:
            residue = Residue.Residue(("H_ZN", 700, " "), "ZN", "")
            chain.add(residue)
            residue.add(Atom.Atom("ZN", np.array([2., 0., 0.]), 20., 1., " ", "ZN  ", serial, "ZN"))
        writer = MMCIFIO()
        writer.set_structure(structure)
        buffer = io.StringIO()
        writer.save(buffer)
        self.cif = buffer.getvalue()

    def get_json(self, url, params=None):
        self.calls.append(("json", url, params))
        if url.endswith("Q14145.json"):
            return self.uniprot
        if "/entry/" in url:
            return self.entry
        if "/polymer_entity/" in url:
            return self.entity
        if "/nonpolymer_entity/" in url:
            return self.ligand
        raise AssertionError(f"Unexpected public request: {url}")

    def get_text(self, url, params=None):
        self.calls.append(("text", url, params))
        if url.endswith("Q14145.fasta"):
            return self.fasta
        if url.endswith(".cif"):
            return self.cif
        if url.endswith("/ligand"):
            assert params["auth_asym_id"] == "A"
            assert params["auth_seq_id"] == 601
            assert params["model_nums"] == self.model_number
            assert params["encoding"] == "sdf"
            return self.sdf
        raise AssertionError(f"Unexpected public request: {url}")

    def post_json(self, url, payload):
        self.calls.append(("post", url, payload))
        assert payload["return_type"] == "polymer_entity"
        assert payload["request_options"]["results_content_type"] == ["experimental"]
        taxonomy = payload["query"]["nodes"][2]["parameters"]
        assert taxonomy == {
            "attribute": "rcsb_entity_source_organism.taxonomy_lineage.id",
            "operator": "exact_match", "value": "9606"}
        return {"result_set": [{"identifier": "4IQK_1"}]}


def request(**changes):
    return TargetPackageRequest(uniprot_id="Q14145", pdb_id="4IQK", protein_chain="A",
                                ligand_id="IQK", **changes)


def test_prepares_same_frame_single_v2000_and_traceable_inputs(tmp_path):
    http = FixtureHTTP()
    package = build_target_package(request(), tmp_path, http)
    assert package.status == "ready", package.checks
    assert package.chain == "A"
    assert np.allclose(package.pocket_center, [0, 0, 0], atol=1e-6)
    sdf = Path(package.reference_ligand_sdf).read_text()
    assert sdf.count("$$$$") == 1
    assert "V2000" in sdf.splitlines()[3]
    protein = Path(package.protein_pdb).read_text()
    assert "ATOM" in protein and "IQK" not in protein
    assert package.model_readiness["drugclip"]["ready"]
    assert package.model_readiness["sprint"]["ready"]
    assert not package.model_readiness["md"]["ready"]
    assert package.model_readiness["md"]["status"] == "md_needs_prepared_system"
    assert package.metadata["sequence_alignment"]["identity"] == 1.0
    assert package.metadata["sequence_alignment"]["canonical_range"] == [1, 20]
    assert all(Path(source["raw_file"]).is_file() for source in package.metadata["sources"])
    assert (Path(package.protein_pdb).parent / "package.json").is_file()
    assert not any("pubchem" in call[1].lower() for call in http.calls)


def test_wrong_structure_species_is_blocked(tmp_path):
    package = build_target_package(request(), tmp_path, FixtureHTTP(taxon=10090))
    assert package.status == "blocked"
    assert not package.checks["structure_species"]["passed"]
    assert not package.model_readiness["drugclip"]["ready"]


def test_wrong_requested_species_is_blocked_before_structure_fetch(tmp_path):
    http = FixtureHTTP()
    package = build_target_package(request(species="Mus musculus"), tmp_path, http)
    assert package.status == "blocked"
    assert "requested_species" in package.checks["blocking_issues"]
    assert not any("rcsb" in call[1] for call in http.calls)


def test_wrong_chain_cannot_fall_back_to_different_chain(tmp_path):
    req = request().model_copy(update={"protein_chain": "B"})
    package = build_target_package(req, tmp_path, FixtureHTTP())
    assert package.status == "blocked"
    assert not package.checks["protein_chain_identity"]["passed"]
    assert not package.protein_pdb


def test_displaced_instance_sdf_is_blocked_even_with_source_pocket_contacts(tmp_path):
    package = build_target_package(request(), tmp_path, FixtureHTTP(sdf_shift=30))
    assert package.status == "blocked"
    assert package.checks["bound_reference_ligand"]["passed"]
    assert not package.checks["ligand_coordinate_frame"]["passed"]
    assert not package.reference_ligand_sdf


def test_mutation_requires_review_and_is_mapped_to_canonical(tmp_path):
    package = build_target_package(request(), tmp_path, FixtureHTTP(mutation=True))
    assert package.status == "needs_review"
    mutation = package.metadata["sequence_alignment"]["mutations"][0]
    assert mutation["canonical_position"] == 9
    assert mutation["auth_residue"] == "18"
    assert not package.checks["pocket_mutations"]["passed"]
    allowed = build_target_package(request(allow_mutations=True), tmp_path, FixtureHTTP(mutation=True))
    assert allowed.status == "ready"


def test_unresolved_pocket_residue_requires_review(tmp_path):
    package = build_target_package(request(), tmp_path, FixtureHTTP(missing=True))
    assert package.status == "needs_review"
    assert package.metadata["sequence_alignment"]["missing_positions"] == [9]
    assert package.checks["pocket_missing_residues"]["details"] == [9]


def test_structure_domain_sequence_is_explicit_mapped_fragment(tmp_path):
    http = FixtureHTTP()
    http.uniprot["sequence"]["value"] = "MMMMM" + http.uniprot["sequence"]["value"] + "PPPPP"
    http.fasta = ">sp|Q14145|fixture\n" + http.uniprot["sequence"]["value"] + "\n"
    package = build_target_package(request(sequence_scope="structure_domain"), tmp_path, http)
    assert package.status == "ready"
    sequence = "".join(Path(package.sequence_fasta).read_text().splitlines()[1:])
    assert sequence == "ACDEFGHIKLMNPQRSTVWY"
    assert package.metadata["sequence_alignment"]["canonical_range"] == [6, 25]
    assert package.model_readiness["psichic"]["sequence_scope"] == "structure_domain"


def test_sprint_length_limit_does_not_silently_truncate(tmp_path):
    sequence = ("ACDEFGHIKLMNPQRSTVWY" * 55)[:1030]
    package = build_target_package(request(), tmp_path, FixtureHTTP(sequence=sequence))
    assert package.status == "needs_review", package.checks
    assert not package.model_readiness["sprint"]["ready"]
    assert package.model_readiness["sprint"]["structure_sequence_length"] == 1030
    assert len("".join(Path(package.sequence_fasta).read_text().splitlines()[1:])) == 1030
    assert package.model_readiness["psichic"]["ready"]


def test_metals_are_preserved_with_md_preparation_warning(tmp_path):
    package = build_target_package(request(), tmp_path, FixtureHTTP(metal=True))
    assert package.status == "needs_review"
    assert "HETATM" in Path(package.protein_pdb).read_text()
    assert "ZN" in Path(package.protein_pdb).read_text()
    assert package.metadata["retained_hetero_groups"][0]["component_id"] == "ZN"
    assert not package.checks["cofactor_metal_preparation"]["passed"]
    assert not package.model_readiness["md"]["ready"]


def test_automatic_search_uses_experimental_taxonomy_and_ligand_contacts(tmp_path):
    req = request().model_copy(update={"pdb_id": "", "protein_chain": "", "ligand_id": ""})
    http = FixtureHTTP()
    package = build_target_package(req, tmp_path, http)
    assert package.status == "needs_review", package.checks
    assert "reference_ligand_identity" in package.checks["review_issues"]
    assert not package.model_readiness["drugclip"]["ready"]
    assert package.metadata["structure_search"]["limit"] == 4
    assert package.metadata["reference_ligand"]["component_id"] == "IQK"
    assert any(call[0] == "post" for call in http.calls)


def test_cofactor_is_never_automatically_used_as_query_reference(tmp_path):
    req = request().model_copy(update={"ligand_id": ""})
    package = build_target_package(req, tmp_path, FixtureHTTP(ligand_id="ATP"))
    assert package.status == "needs_review"
    assert not package.reference_ligand_sdf
    assert not package.model_readiness["drugclip"]["ready"]
    assert not package.model_readiness["pharmaconet"]["ready"]
    assert "ATP" in Path(package.protein_pdb).read_text()


def test_automatic_query_uses_searchable_string_lineage_not_metadata_taxonomy_id(tmp_path):
    # RCSB metadata schema exposes ncbi_taxonomy_id, but the live Search service
    # returns HTTP 400 for querying it. taxonomy_lineage.id is indexed as string.
    req = request().model_copy(update={"pdb_id": "", "ligand_id": ""})
    http = FixtureHTTP()
    package = build_target_package(req, tmp_path, http)
    payload = next(call[2] for call in http.calls if call[0] == "post")
    predicates = [node["parameters"] for node in payload["query"]["nodes"]]
    assert not any(p["attribute"].endswith(".ncbi_taxonomy_id") for p in predicates)
    assert {"attribute": "rcsb_entity_source_organism.taxonomy_lineage.id",
            "operator": "exact_match", "value": "9606"} in predicates
    assert package.metadata["structure_search"]["taxonomy_filter"]["value"] == "9606"
    assert package.status == "needs_review"


def test_lineage_search_hit_is_rejected_when_actual_entity_taxon_differs(tmp_path):
    req = request().model_copy(update={"pdb_id": "", "ligand_id": ""})
    package = build_target_package(req, tmp_path, FixtureHTTP(taxon=10090))
    assert package.status == "blocked"
    assert "experimental_structure_available" in package.checks["blocking_issues"]
    assert package.metadata["structure_search"]["candidates"] == []


def test_multiple_ligand_sites_require_explicit_instance(tmp_path):
    http = FixtureHTTP(duplicates=True)
    package = build_target_package(request(), tmp_path, http)
    assert package.status == "needs_review"
    assert not package.checks["reference_site_unambiguous"]["passed"]
    assert not package.model_readiness["drugclip"]["ready"]
    req = request(ligand_chain="A", ligand_residue="601")
    selected = build_target_package(req, tmp_path, FixtureHTTP(duplicates=True))
    assert selected.checks["reference_site_unambiguous"]["passed"]
    # Other bound compounds remain visible as retained hetero groups for review.
    assert selected.status == "needs_review"


def test_actual_deposited_model_number_is_selected(tmp_path):
    package = build_target_package(request(model_number=2), tmp_path,
                                   FixtureHTTP(model_number=2))
    assert package.status == "ready", package.checks
    wrong = build_target_package(request(model_number=1), tmp_path,
                                 FixtureHTTP(model_number=2))
    assert wrong.status == "blocked"
    assert not wrong.checks["model_number"]["passed"]


def test_package_is_immutable_and_detects_artifact_tampering(tmp_path):
    http = FixtureHTTP()
    package = build_target_package(request(), tmp_path, http)
    calls = len(http.calls)
    reloaded = build_target_package(request(), tmp_path, http)
    assert reloaded == package
    assert len(http.calls) == calls
    assert json.loads((Path(package.protein_pdb).parent / "checks.json").read_text()) == package.checks
    Path(package.sequence_fasta).write_text(">tampered\nAAAA\n")
    with pytest.raises(ValueError, match="artifact changed"):
        build_target_package(request(), tmp_path, http)


def test_network_failure_produces_explanatory_blocked_package(tmp_path):
    class Offline:
        def get_json(self, url, params=None):
            raise RuntimeError("Fixture source unavailable")

    package = build_target_package(request(), tmp_path, Offline())
    assert package.status == "blocked"
    assert package.checks["preparation_error"]["details"]["message"] == "Fixture source unavailable"
    assert (tmp_path / package.package_id / "package.json").is_file()


def test_nonfinite_sdf_coordinates_are_blocked(tmp_path):
    http = FixtureHTTP()
    lines = http.sdf.splitlines()
    lines[4] = "       nan" + lines[4][10:]
    http.sdf = "\n".join(lines) + "\n"
    package = build_target_package(request(), tmp_path, http)
    assert package.status == "blocked"
    assert not package.reference_ligand_sdf
    assert not package.model_readiness["drugclip"]["ready"]


def test_sdf_multiple_molecules_and_explicit_absent_ligand_are_blocked(tmp_path):
    http = FixtureHTTP()
    http.sdf += http.sdf
    package = build_target_package(request(), tmp_path, http)
    assert package.status == "blocked"
    assert not package.checks["single_ligand_sdf"]["passed"]
    absent = build_target_package(request().model_copy(update={"ligand_id": "BAD"}),
                                  tmp_path, FixtureHTTP())
    assert absent.status == "blocked"
    assert not absent.checks["bound_reference_ligand"]["passed"]


def test_deposited_construct_terminal_holes_are_checked_near_ligand(tmp_path):
    http = FixtureHTTP()
    http.uniprot["sequence"]["value"] = "MMM" + http.uniprot["sequence"]["value"]
    http.fasta = ">sp|Q14145|fixture\n" + http.uniprot["sequence"]["value"] + "\n"
    http.entity["rcsb_polymer_entity_align"] = [{
        "reference_database_name": "UniProt", "reference_database_accession": "Q14145",
        "aligned_regions": [{"entity_beg_seq_id": 1, "ref_beg_seq_id": 1, "length": 23}]}]
    package = build_target_package(request(), tmp_path, http)
    assert package.status == "needs_review"
    assert package.metadata["sequence_alignment"]["missing_positions"] == [1, 2, 3]
    assert package.checks["pocket_missing_residues"]["details"] == [1, 2, 3]


def test_nonmatching_structure_sequence_cannot_pass_accession_metadata_alone(tmp_path):
    http = FixtureHTTP()
    http.uniprot["sequence"]["value"] = "W" * 20
    http.fasta = ">sp|Q14145|fixture\n" + "W" * 20 + "\n"
    package = build_target_package(request(), tmp_path, http)
    assert package.status == "blocked"
    assert not package.checks["canonical_fragment_mapping"]["passed"]


def test_unbound_noncofactor_is_not_accepted_as_reference(tmp_path):
    http = FixtureHTTP()
    # Move source ligand and SDF together, preserving their common frame while
    # destroying meaningful protein contacts.
    lines = http.cif.splitlines()
    updated = []
    for line in lines:
        if line.startswith("HETATM"):
            fields = line.split()
            # MMCIFIO coordinates are columns 10, 11, 12 in this fixture table.
            for i in (10, 11, 12):
                fields[i] = str(float(fields[i]) + 100)
            line = " ".join(fields)
        updated.append(line)
    http.cif = "\n".join(updated) + "\n"
    package = build_target_package(request(), tmp_path, http)
    assert package.status == "blocked"
    assert not package.checks["bound_reference_ligand"]["passed"]
    assert package.checks["bound_reference_ligand"]["details"]["candidates"][0]["contact_residues_6A"] == 0

