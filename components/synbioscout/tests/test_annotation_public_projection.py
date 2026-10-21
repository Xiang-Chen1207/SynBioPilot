"""SYNTHETIC annotation provenance through normal results and portable exports."""
import hashlib
import io
import json
import zipfile

import pytest

from scientist.co_models import CoScientistState
from scientist.exports import export_research
from scientist.models import ResearchBrief, SourceRecord
from scientist.presentation import result_document
from scientist.store import ProjectStore
from scientist.tool_public import public_annotation_metadata, public_annotation_receipt

PRIVATE = "PRIVATE_ANNOTATION_SENTINEL"


def receipt(provider="open_targets", operation="Disease", parameters=None):
    return {"schema_version": 1, "provider": provider, "operation": operation,
        "parameters": parameters or {"id": "EFO_SYNTHETIC", "size": 6},
        "query_key": "a" * 64, "response_sha256": "b" * 64,
        "hash_scope": "canonical_json_response", "observed_at": "2026-10-15T09:00:00Z",
        "source_retrieved_at": "2026-10-15T08:00:00Z", "cache_origin": "exact_query_http_cache",
        "request_cache_keys": ["c" * 64], "interpretation": "query_observation_not_independent_validation",
        "raw_prompt": PRIVATE, "headers": {"Authorization": PRIVATE},
        "cache_path": "/Users/private/" + PRIVATE}


def metadata():
    search = receipt(operation="Search", parameters={"q": "SYNTHETIC marker syndrome", "entities": ["disease"]})
    checked = receipt(operation="DiseaseIdentity", parameters={"id": "EFO_SYNTHETIC"})
    return {"query_receipt": receipt(), "identity_scope": {
        "disease_id": "EFO_SYNTHETIC", "disease_name": "SYNTHETIC marker syndrome",
        "task_disease": "SYNTHETIC marker syndrome", "query_name": "SYNTHETIC marker syndrome",
        "species": "Homo sapiens", "run_id": "r_SYNTHETIC", "association_only": True,
        "status": "unique_checked_disease_identity", "identity_query_receipts": [search, checked],
        "raw_prompt": PRIVATE, "headers": {"Authorization": PRIVATE}, "path_secret": "/Users/" + PRIVATE},
        "raw_prompt": PRIVATE, "raw_diagnostics": PRIVATE}


def test_annotation_projection_keeps_exact_query_identity_species_hash_and_cache_origin():
    public = public_annotation_metadata(metadata())
    scope = public["identity_scope"]
    assert scope["disease_id"] == "EFO_SYNTHETIC"
    assert scope["species"] == "Homo sapiens" and scope["association_only"] is True
    assert scope["identity_query_receipts"][0]["parameters"] == {
        "q": "SYNTHETIC marker syndrome", "entities": ["disease"]}
    assert scope["identity_query_receipts"][1]["parameters"] == {"id": "EFO_SYNTHETIC"}
    outer = public["query_receipt"]
    assert outer["parameters"] == {"id": "EFO_SYNTHETIC", "size": 6}
    assert outer["response_sha256"] == "b" * 64 and outer["request_cache_keys"] == ["c" * 64]
    assert outer["cache_origin"] == "exact_query_http_cache"
    assert outer["source_retrieved_at"] != outer["observed_at"]
    assert PRIVATE not in json.dumps(public) and "/Users/" not in json.dumps(public)


def test_private_values_in_allowed_fields_are_filtered_and_other_keys_do_not_pass():
    raw = receipt(parameters={"query": "/Users/private/" + PRIVATE, "id": "EFO_SYNTHETIC",
        "species": "Homo sapiens", "headers": {"Authorization": PRIVATE}, "raw_prompt": PRIVATE,
        "entities": ["disease", {"raw_prompt": PRIVATE}, "api_key=" + PRIVATE]})
    raw["interpretation"] = "raw_prompt=" + PRIVATE
    raw["request_cache_keys"] += ["/Users/" + PRIVATE, {"headers": PRIVATE}, None]
    public = public_annotation_receipt(raw)
    assert public["parameters"]["id"] == "EFO_SYNTHETIC"
    assert public["parameters"]["species"] == "Homo sapiens"
    assert public["request_cache_keys"] == ["c" * 64]
    assert "query" not in public["parameters"] and "interpretation" not in public
    assert PRIVATE not in json.dumps(public)


@pytest.mark.parametrize("bad", [None, "bad", 1, [], {"parameters": None},
    {"parameters": ["bad"], "request_cache_keys": "not_a_list"},
    {"parameters": {"query": {"raw_prompt": PRIVATE}}, "request_cache_keys": [None, {}, 1]},
    {"identity_scope": {"identity_query_receipts": {"raw_prompt": PRIVATE}}, "query_receipt": []}])
def test_malformed_annotation_receipts_are_bounded_non_crashing_and_drop_private_data(bad):
    assert PRIVATE not in json.dumps(public_annotation_receipt(bad), allow_nan=False)
    assert PRIVATE not in json.dumps(public_annotation_metadata(bad), allow_nan=False)


@pytest.mark.parametrize("co_scientist", [False, True])
def test_normal_result_json_and_zip_keep_same_source_identity_chain_without_mutating_saved_run(tmp_path, co_scientist):
    store = ProjectStore(tmp_path / "SYNTHETIC_workspace")
    project = store.create_project("SYNTHETIC annotation projection")
    run = store.create_run(project["project_id"], ResearchBrief(disease="SYNTHETIC marker syndrome"))
    ot = SourceRecord(source_id="ot:disease:EFO_SYNTHETIC", provider="open_targets",
        title="SYNTHETIC association", url="https://platform.opentargets.org/disease/EFO_SYNTHETIC",
        retrieved_at="2026-10-15T08:00:00Z", text="SYNTHETIC public association body.",
        evidence_scope="database", metadata=metadata())
    protein = SourceRecord(source_id="uniprot:Q11111", provider="uniprot", title="SYNTHETIC ALPHA",
        url="https://www.uniprot.org/uniprotkb/Q11111", retrieved_at=ot.retrieved_at,
        text="SYNTHETIC public protein body.", evidence_scope="database",
        metadata={"primaryAccession": "Q11111", "organism": {"scientificName": "Homo sapiens"},
            "query_receipt": receipt("uniprot", "protein_identity", {
                "gene_symbol": "ALPHA", "species": "Homo sapiens", "size": 5,
                "query": '(gene_exact:ALPHA) AND (organism_name:"Homo sapiens")'}),
            "raw_prompt": PRIVATE})
    paper = SourceRecord(source_id="pmid:1", provider="europe_pmc", title="SYNTHETIC paper",
        url="https://europepmc.org/article/MED/1", retrieved_at=ot.retrieved_at,
        text="SYNTHETIC public abstract.", metadata={"query_receipt": receipt("europe_pmc", "literature_search", {
            "query": 'TITLE_ABS:"SYNTHETIC marker syndrome"', "pageSize": 6, "cursorMark": "*", "resultType": "core"}),
            "raw_prompt": PRIVATE})
    run.sources = [ot, protein, paper]
    if co_scientist:
        run.co_scientist = CoScientistState(tool_calls=[{"tool_name": name, "status": "ok", "sources": [source.model_dump()]}
            for name, source in (("disease_targets", ot), ("uniprot", protein), ("europepmc_search", paper))])
    store.save_run(run)
    before = (store.run_dir(run.project_id, run.run_id) / "run.json").read_bytes()
    result = result_document(run)
    exported = json.loads(export_research(store, run, "json")[0])
    with zipfile.ZipFile(io.BytesIO(export_research(store, run, "zip")[0])) as archive:
        zipped = json.loads(archive.read("research.json"))
        source_export = json.loads(archive.read("sources.json"))
        assert PRIVATE.encode() not in b"".join(archive.read(name) for name in archive.namelist())
    for document in (result, exported, zipped, {"sources": source_export}):
        sources = {s["source_id"]: s for s in document["sources"]}
        assert set(sources) == {s.source_id for s in run.sources}
        disease = sources[ot.source_id]["metadata"]
        assert disease["identity_scope"] == public_annotation_metadata(ot.metadata)["identity_scope"]
        assert disease["query_receipt"]["parameters"]["id"] == disease["identity_scope"]["disease_id"]
        assert disease["query_receipt"]["response_sha256"] == "b" * 64
        protein_meta = sources[protein.source_id]["metadata"]
        assert protein_meta["query_receipt"]["parameters"]["species"] == protein_meta["organism"]["scientificName"]
        assert protein_meta["primaryAccession"] == "Q11111"
        for source in sources.values():
            assert source["metadata"]["query_receipt"]["cache_origin"] == "exact_query_http_cache"
        assert PRIVATE not in json.dumps(document)
    assert all(s["text"] == "" for s in exported["sources"])
    assert exported["sources"][0]["metadata"]["retrieved_text_sha256"] == hashlib.sha256(ot.text.encode()).hexdigest()
    assert (store.run_dir(run.project_id, run.run_id) / "run.json").read_bytes() == before

