"""Offline public-tool export checks; biological values are synthetic fixtures."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest
import requests

from scientist.sources import BiomedicalSources
from scientist.tool_public import public_tool_plan, public_tool_result, public_tool_source
from scientist.tools import ToolRegistry, ToolRequest


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Public-tool projection tests must not send network requests")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)


def activity():
    return {
        "activity_id": 7, "molecule_chembl_id": "CHEMBL1", "target_chembl_id": "CHEMBL2",
        "target_organism": "Homo sapiens", "target_tax_id": "9606",
        "assay_chembl_id": "CHEMBL3", "assay_type": "B", "assay_description": "Fixture assay",
        "document_chembl_id": "CHEMBL4", "record_id": 8,
        "standard_type": "IC50", "standard_relation": ">", "standard_units": "nM",
        "standard_value": "500", "relation": ">", "units": "uM", "value": "0.5",
        "data_validity_comment": "Fixture caveat", "potential_duplicate": 1,
    }


def source(provider="chembl", metadata=None):
    return {"source_id": provider + ":tool:fixture", "provider": provider,
            "title": "Fixture database observation", "url": "https://www.ebi.ac.uk/chembl/",
            "retrieved_at": "2026-10-12T00:00:00Z", "evidence_scope": "database",
            "text": "RAW_PROVIDER_SNAPSHOT", "metadata": metadata or {}}


def observation(**updates):
    return {"tool_name": "chembl", "provider": "chembl", "parameters": {
        "resource": "activity", "target_chembl_id": "CHEMBL2", "species": "Homo sapiens",
        "limit": 5, "page": 1}, "status": "ok", "retrieved_at": "2026-10-12T00:00:00Z",
        "records": [activity()], "sources": [source(metadata={"records": [activity()]})],
        "warnings": ["Heterogeneous assays cannot be combined as efficacy scores"],
        "request_count": 1, "cache_hits": 0, **updates}


def test_legacy_result_preserves_scientific_rows_without_inventing_new_fields():
    stored = observation()
    before = copy.deepcopy(stored)
    public = public_tool_result(stored)
    assert public["records"] == [activity()]
    assert "call_id" not in public and "identity" not in public and "http_trace" not in public
    assert public["sources"][0]["source_id"] == "chembl:tool:fixture"
    assert public["sources"][0]["text"] == ""
    assert public["sources"][0]["metadata"]["retrieved_text_sha256"] == hashlib.sha256(
        stored["sources"][0]["text"].encode()
    ).hexdigest()
    assert public["sources"][0]["metadata"]["export_text_included"] is False
    assert public_tool_result(public) == public
    assert stored == before


def test_previous_export_digest_requires_empty_text_and_explicit_export_marker():
    original = source()
    public = public_tool_source(original)
    assert public_tool_source(public) == public
    empty_hash = hashlib.sha256(b"").hexdigest()
    missing_marker = copy.deepcopy(public)
    missing_marker["metadata"].pop("export_text_included")
    assert public_tool_source(missing_marker)["metadata"]["retrieved_text_sha256"] == empty_hash
    malformed_digest = copy.deepcopy(public)
    malformed_digest["metadata"]["retrieved_text_sha256"] = "not-a-digest"
    assert public_tool_source(malformed_digest)["metadata"]["retrieved_text_sha256"] == empty_hash
    new_text = copy.deepcopy(public)
    new_text["text"] = "NEW_FIXTURE_TEXT"
    assert public_tool_source(new_text)["metadata"]["retrieved_text_sha256"] == hashlib.sha256(
        b"NEW_FIXTURE_TEXT"
    ).hexdigest()


def test_real_executor_result_projects_assay_units_species_and_source_ids():
    class FixtureHTTP:
        request_count = 0
        cache_hits = 0

        def get_json(self, endpoint, params=None, *, headers=None):
            self.request_count += 1
            return {"activities": [activity()], "page_meta": {"total_count": 1, "next": None}}

    registry = ToolRegistry(BiomedicalSources(FixtureHTTP()))
    result = registry.execute(ToolRequest(name="chembl", arguments={"resource": "activity",
        "target_chembl_id": "CHEMBL2", "species": "Homo sapiens", "limit": 5}))
    public = public_tool_result(result.model_dump(mode="json"))
    row = public["records"][0]
    assert result.status == "ok" and public["request_count"] == 1
    assert row["relation"] == row["standard_relation"] == ">"
    assert row["units"] == "uM" and row["standard_units"] == "nM"
    assert row["value"] == "0.5" and row["standard_value"] == "500"
    assert row["target_organism"] == "Homo sapiens" and row["document_chembl_id"] == "CHEMBL4"
    assert public["sources"][0]["metadata"]["records"] == public["records"]
    assert public["sources"][0]["source_id"] == result.sources[0].source_id
    assert public["sources"][0]["text"] == "" and result.sources[0].text
    assert "universal_score" not in row and "pkpd" not in row


def test_raw_parameters_on_invalid_or_unknown_calls_are_never_copied():
    bad = {"headers": {"Authorization": "Bearer private-value"}, "api_key": "private-value",
           "raw_prompt": "PRIVATE_PROMPT", "url": "https://example.invalid", "limit": "5",
           "page": True, "resource": "activity", "target_chembl_id": "CHEMBL2"}
    public = public_tool_result(observation(status="invalid", parameters=bad,
                                            requested_parameters=bad, raw_prompt="PRIVATE_PROMPT"))
    assert public["parameters"] == {"resource": "activity", "target_chembl_id": "CHEMBL2"}
    assert public["requested_parameters"] == public["parameters"]
    unknown = public_tool_result(observation(tool_name="arbitrary_provider", parameters=bad))
    assert unknown["parameters"] == {} and unknown["provider"] == "none"
    assert unknown["records"] == [{}]
    assert "PRIVATE_PROMPT" not in json.dumps(public) + json.dumps(unknown)
    assert "private-value" not in json.dumps(public) + json.dumps(unknown)


def test_malicious_nested_provider_fields_dropped_in_rows_and_sources():
    row = {**activity(), "provider_object": {"api_key": "LEAK"}, "headers": {"token": "LEAK"},
           "stdout": "LEAK", "raw_prompt": "LEAK", "value": {"credential": "LEAK"}}
    metadata = {"records": [row], "parameters": {"resource": "activity", "headers": {"x": "LEAK"}},
                "raw_prompt": "LEAK", "provider_payload": {"x": "LEAK"},
                "pagination": {"limit": 5, "raw_prompt": "LEAK"}}
    public = public_tool_result(observation(records=[row], sources=[source(metadata=metadata)]))
    assert "LEAK" not in json.dumps(public)
    assert "value" not in public["records"][0]
    assert public["sources"][0]["metadata"]["parameters"] == {"resource": "activity"}


@pytest.mark.parametrize("unsafe", [
    "API_KEY=fixture-private-value", "Bearer fixture-private-value", "sk-abcdefgh123456789",
    "/Users/SYNTHETIC/private-file.json", "file:///tmp/private-file", "C:\\Users\\private-file",
    "Traceback (most recent call last):", "stdout: private-value", "stderr: private-value",
    "raw_prompt: private-value", "system_prompt: private-value", "secret: private-value",
    "token=private-value", "ghp_abcdefgh123456789", "github_pat_abcdefgh123456789",
    "-----BEGIN RSA PRIVATE KEY-----", "<|system|> private-value",
])
def test_sensitive_strings_are_omitted_even_inside_approved_fields(unsafe):
    public = public_tool_result(observation(warnings=[unsafe], stop_reason=unsafe,
        parameters={"resource": "activity", "query": unsafe},
        records=[{**activity(), "assay_description": unsafe}],
        identity={"status": "unresolved", "reason": unsafe, "query": unsafe},
        http_trace=[{"endpoint": unsafe, "outcome": unsafe}]))
    encoded = json.dumps(public)
    assert unsafe not in encoded
    assert public["warnings"] == [] and "stop_reason" not in public
    assert "assay_description" not in public["records"][0]


def test_call_identity_http_trace_and_pagination_are_bounded_and_explicit():
    public = public_tool_result(observation(
        call_id="call_1", parent_call_id="call_0", plan_id="plan_1", gap_id="gap_1", count=1, record_count=1,
        stop_reason="return_limit", identity={"status": "ambiguous", "entity_type": "molecule",
            "query": "Fixture salt", "reason": "Two chemical forms", "candidates": [
                {"id": "CHEMBL1", "pref_name": "Fixture base", "raw_provider": {"x": "LEAK"}},
                {"id": "CHEMBL2", "pref_name": "Fixture salt"}], "headers": {"x": "LEAK"}},
        http_trace=[{"endpoint": "https://www.ebi.ac.uk/chembl/api/data/molecule.json",
            "method": "GET", "parameters": {"limit": 5, "offset": 0, "pref_name__iexact": "Fixture",
                                             "headers": {"x": "LEAK"}},
            "cache_key": "abc123", "attempt": 2, "retry": 1, "cache_hit": False,
            "outcome": "ok", "http_status": 200, "raw_response": {"x": "LEAK"}}],
        pagination={"page": 1, "limit": 5, "total_count": 2, "next": None, "raw": "LEAK"}))
    assert public["call_id"] == "call_1" and public["parent_call_id"] == "call_0"
    assert public["record_count"] == 1
    assert public["identity"]["status"] == "ambiguous"
    assert "selected_id" not in public["identity"]
    assert len(public["identity"]["candidates"]) == 2
    assert public["http_trace"][0]["attempt"] == 2
    assert public["http_trace"][0]["cache_hit"] is False
    assert public["pagination"] == {"page": 1, "limit": 5, "total_count": 2,
                                     "has_next": False, "next": None}
    assert "LEAK" not in json.dumps(public)


def test_chembl_identity_forms_components_synonyms_remain_distinct():
    rows = [{"molecule_chembl_id": "CHEMBL1", "pref_name": "Fixture base", "molecule_type": "Small molecule",
             "molecule_structures": {"canonical_smiles": "CCO", "standard_inchi_key": "FIXTUREKEY",
                                     "raw_prompt": "LEAK"},
             "molecule_hierarchy": {"molecule_chembl_id": "CHEMBL1", "parent_chembl_id": "CHEMBL1",
                                    "active_chembl_id": "CHEMBL1", "provider_object": "LEAK"},
             "molecule_synonyms": [{"molecule_synonym": "Fixture alias", "syn_type": "OTHER",
                                    "provider_object": "LEAK"}]},
            {"molecule_chembl_id": "CHEMBL2", "pref_name": "Fixture salt",
             "molecule_hierarchy": {"parent_chembl_id": "CHEMBL1"}}]
    public = public_tool_result(observation(records=rows, identity={"status": "ambiguous"}))
    assert public["records"][0]["molecule_hierarchy"]["parent_chembl_id"] == "CHEMBL1"
    assert public["records"][1]["molecule_chembl_id"] == "CHEMBL2"
    assert public["records"][0]["molecule_synonyms"][0]["molecule_synonym"] == "Fixture alias"
    assert "LEAK" not in json.dumps(public)
    target = {"target_chembl_id": "CHEMBL3", "organism": "Mus musculus", "target_type": "PROTEIN COMPLEX",
              "target_components": [{"accession": "P12345", "component_type": "PROTEIN",
                  "target_component_synonyms": [{"component_synonym": "Fixture gene", "syn_type": "GENE_SYMBOL"}],
                  "target_component_xrefs": [{"xref_id": "123", "xref_src_db": "Ensembl", "prompt": "LEAK"}],
                  "provider_object": {"x": "LEAK"}}]}
    projected = public_tool_result(observation(records=[target]))["records"][0]
    assert projected["organism"] == "Mus musculus"
    assert projected["target_components"][0]["accession"] == "P12345"
    assert "LEAK" not in json.dumps(projected)


def test_go_not_and_evidence_reference_and_species_are_preserved():
    row = {"id": "fixture_go", "geneProductId": "UniProtKB:Q9BY41", "taxonId": 9606,
           "goId": "GO:0004407", "qualifier": "NOT|enables", "goEvidence": "IEA",
           "evidenceCode": "ECO:0000501", "reference": "GO_REF:0000002",
           "withFrom": [{"connectedXrefs": [{"db": "UniProtKB", "id": "Q9BY41", "prompt": "LEAK"}]}],
           "provider_object": {"x": "LEAK"}, "efficacy": 1}
    public = public_tool_result(observation(tool_name="quickgo_annotations", records=[row]))
    assert public["records"][0]["qualifier"] == "NOT|enables"
    assert public["records"][0]["goEvidence"] == "IEA"
    assert public["records"][0]["evidenceCode"] == "ECO:0000501"
    assert public["records"][0]["reference"] == "GO_REF:0000002"
    assert public["records"][0]["taxonId"] == 9606
    assert "efficacy" not in public["records"][0] and "LEAK" not in json.dumps(public)


def test_reactome_membership_and_association_are_not_upgraded_to_conclusions():
    pathways = public_tool_result(observation(tool_name="reactome_pathways", records=[{
        "dbId": 123, "stId": "R-HSA-123", "speciesName": "Homo sapiens", "isInferred": True,
        "displayName": "Fixture pathway", "chassis_can_produce": True, "causal_efficacy": 0.9}]))
    assert pathways["records"][0] == {"dbId": 123, "stId": "R-HSA-123",
        "speciesName": "Homo sapiens", "isInferred": True, "displayName": "Fixture pathway"}
    association = public_tool_result(observation(tool_name="disease_targets", records=[{
        "disease_id": "EFO_1", "score": 0.7, "causal_probability": 0.7, "pkpd": {"IC50": 2},
        "target": {"id": "ENSG1", "approvedSymbol": "FIXTURE", "provider_object": "LEAK"}}]))
    assert association["records"][0]["score"] == 0.7
    assert "causal_probability" not in association["records"][0]
    assert "pkpd" not in association["records"][0]
    unknown_number = public_tool_result(observation(records=[{**activity(), "value": None}]))
    assert unknown_number["records"][0]["value"] is None


def test_plan_catalog_arguments_and_identity_are_whitelisted_and_not_mutated():
    plan = {"plan_id": "plan_1", "gap_id": "gap_1", "origin": "model_gap", "status": "pending",
        "reason": "Need an assay observation", "hypothesis_ids": ["hypothesis_1"], "call_ids": [],
        "request": {"name": "chembl", "arguments": {"resource": "activity", "molecule_name": "Fixture",
                    "expected_inchikey": "AAAAAAAAAAAAAA-BBBBBBBBBB-C", "headers": {"Authorization": "LEAK"}},
                    "raw_prompt": "LEAK"},
        "identity": {"status": "unresolved", "reason": "A unique ID is required"},
        "provider_object": {"x": "LEAK"}, "raw_prompt": "LEAK"}
    before = copy.deepcopy(plan)
    public = public_tool_plan(plan)
    assert public["request"]["arguments"] == {"resource": "activity", "molecule_name": "Fixture",
                                              "expected_inchikey": "AAAAAAAAAAAAAA-BBBBBBBBBB-C"}
    assert public["identity"]["status"] == "unresolved" and "selected_id" not in public["identity"]
    assert public["origin"] == "model_gap" and "LEAK" not in json.dumps(public)
    assert plan == before
    assert "request" not in public_tool_plan({"request": {"name": "shell", "arguments": {"code": "LEAK"}}})


def test_uniprot_pubchem_sources_preserve_identity_but_drop_raw_annotations():
    protein = public_tool_source(source("uniprot", metadata={
        "primaryAccession": "Q9BY41", "organism": {"scientificName": "Mus musculus", "taxonId": 10090,
                                                    "raw_prompt": "LEAK"},
        "genes": [{"geneName": {"value": "Fixture", "provider_object": "LEAK"}}],
        "comments": [{"text": "LEAK"}], "raw_response": {"x": "LEAK"}}))
    assert protein["metadata"]["organism"]["scientificName"] == "Mus musculus"
    assert protein["metadata"]["primaryAccession"] == "Q9BY41"
    assert "LEAK" not in json.dumps(protein)
    compound = public_tool_source(source("pubchem", metadata={"cid": "123", "smiles": "CCO",
        "rawSMILES": "OCC", "inchikey": "FIXTUREKEY", "unknown": {"x": "LEAK"}}))
    assert compound["metadata"]["cid"] == "123" and compound["metadata"]["smiles"] == "CCO"
    assert compound["metadata"]["rawSMILES"] == "OCC" and compound["metadata"]["inchikey"] == "FIXTUREKEY"


def test_literature_sources_are_citations_without_abstracts_or_opaque_metadata():
    paper = source("europe_pmc", metadata={"year": "2026", "query": "PRIVATE_SEARCH_TEXT",
        "publication_types": {"raw_prompt": "LEAK"}, "provider_object": {"x": "LEAK"}})
    paper.update(pmid="123", doi="10.1000/fixture", pmcid="PMC123", evidence_scope="abstract")
    public = public_tool_result(observation(tool_name="europepmc_search", records=[paper], sources=[paper]))
    assert public["records"][0]["pmid"] == "123" and public["records"][0]["text"] == ""
    assert public["records"][0]["metadata"]["year"] == "2026"
    assert set(public["records"][0]["metadata"]) == {
        "year", "retrieved_text_sha256", "export_text_included", "source_scope", "acquisition_origin"}
    assert public["records"][0]["metadata"]["source_scope"]["current_scope"] == "abstract"
    assert public["records"][0]["metadata"]["source_scope"]["acquisition"] == "unknown"
    assert public["records"][0]["metadata"]["source_scope"]["fresh_fulltext"] is False
    assert "PRIVATE_SEARCH_TEXT" not in json.dumps(public) and "RAW_PROVIDER_SNAPSHOT" not in json.dumps(public)


def test_url_and_graphql_trace_do_not_export_credentials_or_arbitrary_endpoints():
    result = public_tool_result(observation(http_trace=[{
        "endpoint": "https://example.invalid/arbitrary", "method": "POST",
        "parameters": {"query": "query Search($q:String!) { search(queryString:$q) { id } }",
            "variables": {"q": "Fixture", "entities": ["target"], "raw_prompt": "LEAK"},
            "headers": {"Authorization": "LEAK"}}, "outcome": "ok"}]))
    trace = result["http_trace"][0]
    assert "endpoint" not in trace and "query" not in trace["parameters"]
    assert trace["parameters"]["variables"] == {"q": "Fixture", "entities": ["target"]}
    for url in ("http://example.invalid/", "file:///Users/SYNTHETIC/private", "https://user:pass@example.invalid/",
                "https://localhost/private", "https://192.168.1.1/private", "https://server.local/private",
                "https://www.ebi.ac.uk/?q=%2FUsers%2Fcy%2Fprivate", "https://www.ebi.ac.uk/?q=sk%2Dabcdefgh123456789",
                "https://example.invalid/?access_token=private-value"):
        assert "url" not in public_tool_source({**source(), "url": url})


def test_projection_bounds_records_candidates_trace_strings_and_nonfinite_numbers():
    public = public_tool_result(observation(records=[activity()] * 20,
        identity={"status": "ambiguous", "candidates": [{"id": "CHEMBL1"}] * 30},
        http_trace=[{"attempt": 1}] * 200, warnings=["x" * 501, "safe"],
        count=float("nan")))
    assert len(public["records"]) == len(public["identity"]["candidates"]) == 10
    assert len(public["http_trace"]) == 100 and public["warnings"] == ["safe"]
    assert "count" not in public


@pytest.mark.parametrize("bad", [None, [], "not a record", 3])
def test_non_mapping_legacy_values_are_ignored(bad):
    assert public_tool_result(bad) == public_tool_plan(bad) == public_tool_source(bad) == {}


def test_malformed_nested_fields_are_ignored_without_crashing():
    public = public_tool_result({"tool_name": {"raw_prompt": "LEAK"}, "status": [],
        "parameters": {"raw_prompt": "LEAK"}, "records": [None, "LEAK"],
        "identity": {"status": [], "candidates": [None]},
        "http_trace": [{"method": {}, "parameters": {"entities": [{"raw_prompt": "LEAK"}]}}]})
    assert public["provider"] == "none" and public["parameters"] == {}
    assert "LEAK" not in json.dumps(public)
    assert public_tool_plan({"origin": [], "status": {}, "request": {"name": []}}) == {}
    assert public_tool_source({"provider": [], "metadata": {}, "evidence_scope": []}) == {"text": "", "metadata": {}}

