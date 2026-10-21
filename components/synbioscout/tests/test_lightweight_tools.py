"""Offline contract and safety checks; all biology values below are fixtures."""
from __future__ import annotations

import copy

import pytest
import requests
from pydantic import ValidationError

from scientist.http import DataSourceError
from scientist.models import SourceRecord
from scientist.sources import BiomedicalSources
from scientist.tools import (
    CHEMBL_URL,
    QUICKGO_URL,
    REACTOME_URL,
    ToolRegistry,
    ToolRequest,
)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Lightweight-tool unit tests cannot send real requests")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)


class FixtureHTTP:
    def __init__(self, response=None, failure=None, cached=False):
        self.response = response
        self.failure = failure
        self.cached = cached
        self.request_count = 0
        self.cache_hits = 0
        self.calls = []

    def get_json(self, url, params=None, *, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if self.cached:
            self.cache_hits += 1
        else:
            self.request_count += 1
        if self.failure:
            raise self.failure
        return copy.deepcopy(self.response)


def registry(response=None, **kwargs):
    client = FixtureHTTP(response, **kwargs)
    return ToolRegistry(BiomedicalSources(client)), client


def invoke(tool, tool_name, **arguments):
    return tool.execute(ToolRequest(name=tool_name, arguments=arguments))


def activity_payload(*, total=1, rows=None):
    return {"activities": rows if rows is not None else [{
        "activity_id": 7, "molecule_chembl_id": "CHEMBL1", "target_chembl_id": "CHEMBL2",
        "target_organism": "Homo sapiens", "target_tax_id": "9606",
        "assay_chembl_id": "CHEMBL3", "assay_type": "B",
        "assay_description": "Fixture cellular assay", "document_chembl_id": "CHEMBL4",
        "standard_type": "IC50", "standard_relation": ">", "standard_units": "nM",
        "standard_value": "500", "relation": ">", "units": "uM", "value": "0.5",
        "data_validity_comment": "Fixture warning", "potential_duplicate": 1,
    }], "page_meta": {"total_count": total, "limit": 5, "offset": 0,
                       "next": "/chembl/api/data/activity.json?offset=5" if total > 5 else None}}


def quickgo_payload(*, total=1, rows=None):
    return {"numberOfHits": total, "pageInfo": {"resultsPerPage": 5, "current": 1, "total": 1},
            "results": rows if rows is not None else [{
                "id": "fixture_annotation", "geneProductId": "UniProtKB:Q9BY41", "taxonId": 9606,
                "goId": "GO:0004407", "goEvidence": "IEA", "evidenceCode": "ECO:0000501",
                "qualifier": "NOT|enables", "goAspect": "molecular_function",
                "reference": "GO_REF:0000002", "withFrom": [{"connectedXrefs": []}],
                "assignedBy": "Fixture source", "date": "20260101",
            }]}


def test_catalog_exposes_only_eight_lightweight_public_tools():
    tools, client = registry()
    catalog = tools.catalog()
    assert len(catalog) == 8
    assert {entry["name"] for entry in catalog} == {
        "europepmc_search", "opentargets_search", "disease_targets", "uniprot", "pubchem",
        "chembl", "reactome_pathways", "quickgo_annotations",
    }
    assert all(entry["read_only"] and entry["parameters_schema"]["additionalProperties"] is False
               for entry in catalog)
    assert not client.calls


@pytest.mark.parametrize("name", ["shell", "docking", "drugclip", "admet", "md", "get_url"])
def test_unknown_and_heavy_tools_never_execute(name):
    tools, client = registry()
    result = invoke(tools, name, url="https://example.invalid")
    assert result.status == "invalid"
    assert not client.calls


@pytest.mark.parametrize("extra", [
    {"url": "https://example.invalid"}, {"endpoint": "activity"},
    {"headers": {"Authorization": "fixture"}}, {"code": "print('fixture')"},
    {"filters": {"limit": 500}}, {"shell": "pwd"},
])
def test_chembl_rejects_unstructured_or_arbitrary_endpoint_parameters(extra):
    tools, client = registry(activity_payload())
    result = invoke(tools, "chembl", resource="activity", target_chembl_id="CHEMBL2", **extra)
    assert result.status == "invalid"
    assert result.request_count == 0 and not client.calls


@pytest.mark.parametrize("updates", [
    {"limit": 11}, {"limit": 0}, {"limit": "5"}, {"limit": True},
    {"page": 3}, {"page": 0}, {"page": "1"},
    {"species": "human"}, {"species": "Homo sapiens\" AND organism_id:10090"},
    {"target_chembl_id": "CHEMBL2/../../activity"}, {"target_chembl_id": "CHEMBL0"},
])
def test_strict_limits_species_and_identifiers(updates):
    tools, client = registry(activity_payload())
    args = {"resource": "activity", "target_chembl_id": "CHEMBL2", **updates}
    result = invoke(tools, "chembl", **args)
    assert result.status == "invalid"
    assert not client.calls


@pytest.mark.parametrize("args", [
    {"resource": "activity"}, {"resource": "mechanism"}, {"resource": "target"},
    {"resource": "molecule", "chembl_id": "CHEMBL1", "query": "fixture"},
    {"resource": "activity", "chembl_id": "CHEMBL1"},
    {"resource": "molecule", "chembl_id": "CHEMBL1", "species": "Homo sapiens"},
    {"resource": "mechanism", "molecule_chembl_id": "CHEMBL1", "species": "Homo sapiens"},
    {"resource": "assay", "query": "fixture"},
])
def test_chembl_disallows_unbounded_or_incompatible_queries(args):
    tools, client = registry(activity_payload())
    assert invoke(tools, "chembl", **args).status == "invalid"
    assert not client.calls


def test_chembl_preserves_assay_units_relation_species_and_document_without_interpretation():
    tools, client = registry(activity_payload())
    result = invoke(tools, "chembl", resource="activity", target_chembl_id="CHEMBL2",
                    species="Homo sapiens", limit=5)
    assert result.status == "ok" and result.request_count == 1
    assert result.records == activity_payload()["activities"]
    assert result.records[0]["standard_relation"] == ">"
    assert result.records[0]["units"] != result.records[0]["standard_units"]
    assert result.sources[0].provider == "chembl"
    assert result.sources[0].evidence_scope == "database"
    assert "CHEMBL3" in result.sources[0].text and '"standard_value": "500"' in result.sources[0].text
    assert client.calls == [{"url": CHEMBL_URL + "/activity.json", "params": {
        "target_chembl_id": "CHEMBL2", "target_organism": "Homo sapiens", "offset": 0,
        "limit": 5}, "headers": {"Accept": "application/json"}}]
    assert "https://www.ebi.ac.uk/chembl/api/data/activity.json?" in result.sources[0].url
    assert "assays are reported separately by endpoint, unit and experimental system" in " ".join(result.warnings)


def test_chembl_page_two_uses_bounded_offset_and_never_follows_next_url():
    payload = activity_payload(total=20)
    payload["page_meta"]["next"] = "https://example.invalid/forbidden"
    tools, client = registry(payload)
    result = invoke(tools, "chembl", resource="activity", target_chembl_id="CHEMBL2", page=2)
    assert result.status == "truncated"
    assert len(client.calls) == 1 and client.calls[0]["params"]["offset"] == 5
    assert all(call["url"].startswith(CHEMBL_URL) for call in client.calls)
    assert result.sources[0].metadata["pagination"]["total_count"] == 20


@pytest.mark.parametrize("resource,collection,args,field", [
    ("molecule", "molecules", {"chembl_id": "CHEMBL1"}, "molecule_chembl_id"),
    ("target", "targets", {"chembl_id": "CHEMBL2"}, "target_chembl_id"),
    ("assay", "assays", {"chembl_id": "CHEMBL3"}, "assay_chembl_id"),
    ("mechanism", "mechanisms", {"molecule_chembl_id": "CHEMBL1"}, "molecule_chembl_id"),
])
def test_fixed_chembl_resources_have_correct_collections_and_id_filters(resource, collection, args, field):
    tools, client = registry({collection: [{field: next(iter(args.values()))}],
                              "page_meta": {"total_count": 1, "next": None}})
    result = invoke(tools, "chembl", resource=resource, **args)
    assert result.status == "ok"
    assert client.calls[0]["url"] == CHEMBL_URL + "/" + resource + ".json"
    assert client.calls[0]["params"][field] == next(iter(args.values()))


def test_chembl_species_mismatch_is_unavailable_not_success_or_empty():
    payload = activity_payload()
    payload["activities"][0]["target_organism"] = "Mus musculus"
    tools, _ = registry(payload)
    result = invoke(tools, "chembl", resource="activity", target_chembl_id="CHEMBL2",
                    species="Homo sapiens")
    assert result.status == "unavailable" and not result.records and not result.sources


def test_chembl_provider_ignoring_identity_filter_is_rejected():
    payload = activity_payload()
    payload["activities"][0]["target_chembl_id"] = "CHEMBL999"
    tools, _ = registry(payload)
    result = invoke(tools, "chembl", resource="activity", target_chembl_id="CHEMBL2")
    assert result.status == "unavailable" and not result.sources


def test_reactome_species_flags_stable_id_and_local_pagination_are_explicit():
    rows = [{"dbId": n, "stId": "R-HSA-" + str(n), "speciesName": "Homo sapiens",
             "displayName": "Fixture pathway " + str(n), "isInferred": n == 6,
             "schemaClass": "Pathway"} for n in range(1, 9)]
    tools, client = registry(rows)
    result = invoke(tools, "reactome_pathways", uniprot_id="Q9BY41", limit=5, page=2)
    assert result.status == "truncated" and result.records == rows[5:8]
    assert result.records[0]["isInferred"] is True
    assert client.calls[0]["url"] == REACTOME_URL + "/mapping/UniProt/Q9BY41/pathways"
    assert client.calls[0]["params"] == {"species": "9606"}
    pagination = result.sources[0].metadata["pagination"]
    assert pagination["mode"] == "local_slice_of_mapping_response"
    assert pagination["total_count"] == 8 and pagination["page"] == 2
    assert "Pathway membership is reported with matched species, sources and inferred flags" in " ".join(result.warnings)


@pytest.mark.parametrize("arguments", [
    {"uniprot_id": "Q9BY41", "taxon_id": "9606"},
    {"uniprot_id": "Q9BY41", "taxon_id": -1},
    {"uniprot_id": "Q9BY41", "taxon_id": 42},
    {"uniprot_id": "../../secret"},
    {"uniprot_id": "Q9BY41", "species": "Homo sapiens"},
    {"uniprot_id": "Q9BY41", "page": 3},
])
def test_reactome_rejects_unverifiable_species_identifiers_and_unbounded_page(arguments):
    tools, client = registry([])
    assert invoke(tools, "reactome_pathways", **arguments).status == "invalid"
    assert not client.calls


def test_reactome_wrong_provider_species_is_not_accepted():
    tools, _ = registry([{"stId": "R-MMU-1", "speciesName": "Mus musculus"}])
    assert invoke(tools, "reactome_pathways", uniprot_id="Q9BY41").status == "unavailable"


@pytest.mark.parametrize("status,expected", [(404, "empty"), (500, "unavailable")])
def test_reactome_documented_404_is_query_no_mapping_not_server_failure(status, expected):
    response = requests.Response()
    response.status_code = status
    cause = requests.HTTPError("Fixture HTTP response", response=response)
    failure = DataSourceError("Fixture wrapped provider response")
    failure.__cause__ = cause
    tools, _ = registry(failure=failure)
    result = invoke(tools, "reactome_pathways", uniprot_id="Q9BY41")
    assert result.status == expected
    if status == 404:
        assert result.sources[0].metadata["provider_http_status"] == 404
        assert "query coverage and provider mapping scope" in result.warnings[0]
    else:
        assert not result.sources


def test_quickgo_preserves_negative_qualifier_evidence_taxon_and_reference():
    tools, client = registry(quickgo_payload())
    result = invoke(tools, "quickgo_annotations", uniprot_id="Q9BY41", taxon_id=9606,
                    go_id="GO:0004407", evidence_code="ECO:0000501", aspect="molecular_function")
    assert result.status == "ok" and result.records == quickgo_payload()["results"]
    assert result.records[0]["qualifier"] == "NOT|enables"
    assert result.records[0]["evidenceCode"] == "ECO:0000501"
    assert result.records[0]["goEvidence"] == "IEA"
    assert result.records[0]["reference"] == "GO_REF:0000002"
    assert client.calls[0]["url"] == QUICKGO_URL
    assert client.calls[0]["params"]["geneProductId"] == "UniProtKB:Q9BY41"
    assert client.calls[0]["params"]["taxonUsage"] == "exact"
    assert client.calls[0]["params"]["goUsage"] == "exact"
    assert client.calls[0]["headers"] == {"Accept": "application/json"}
    assert result.sources[0].provider == "quickgo"


@pytest.mark.parametrize("field,value", [
    ("taxonId", 10090), ("taxonId", None), ("geneProductId", "UniProtKB:P04637"),
])
def test_quickgo_provider_entity_and_species_mismatch_is_rejected(field, value):
    payload = quickgo_payload()
    payload["results"][0][field] = value
    tools, _ = registry(payload)
    result = invoke(tools, "quickgo_annotations", uniprot_id="Q9BY41")
    assert result.status == "unavailable" and not result.sources


@pytest.mark.parametrize("updates", [
    {"limit": 11}, {"taxon_id": 0}, {"taxon_id": True}, {"taxon_id": "9606"},
    {"go_id": "GO:123"}, {"evidence_code": "IEA"}, {"aspect": "all"},
    {"uniprot_id": "https://example.invalid/Q9BY41"}, {"endpoint": "annotation/search"},
])
def test_quickgo_strict_parameters_reject_invalid_requests_without_network(updates):
    tools, client = registry(quickgo_payload())
    args = {"uniprot_id": "Q9BY41", **updates}
    assert invoke(tools, "quickgo_annotations", **args).status == "invalid"
    assert not client.calls


@pytest.mark.parametrize("name,args,payload", [
    ("chembl", {"resource": "activity", "target_chembl_id": "CHEMBL2"},
     activity_payload(total=0, rows=[])),
    ("reactome_pathways", {"uniprot_id": "Q9BY41"}, []),
    ("quickgo_annotations", {"uniprot_id": "Q9BY41"}, quickgo_payload(total=0, rows=[])),
])
def test_valid_successful_no_hits_is_empty_with_auditable_query(name, args, payload):
    tools, _ = registry(payload)
    result = invoke(tools, name, **args)
    assert result.status == "empty" and result.records == []
    assert len(result.sources) == 1 and result.sources[0].metadata["records"] == []
    assert result.request_count == 1


@pytest.mark.parametrize("name,args", [
    ("chembl", {"resource": "activity", "target_chembl_id": "CHEMBL2"}),
    ("reactome_pathways", {"uniprot_id": "Q9BY41"}),
    ("quickgo_annotations", {"uniprot_id": "Q9BY41"}),
])
def test_provider_failure_never_becomes_empty_or_fabricated_source(name, args):
    tools, _ = registry(failure=DataSourceError("Fixture server failure"))
    result = invoke(tools, name, **args)
    assert result.status == "unavailable" and result.request_count == 1
    assert not result.records and not result.sources


@pytest.mark.parametrize("exception", [
    TimeoutError("Public-data request budget exhausted"),
    RuntimeError("Shared HTTP budget exceeded"),
])
def test_shared_budget_failure_has_explicit_status(exception):
    tools, _ = registry(failure=exception)
    result = invoke(tools, "quickgo_annotations", uniprot_id="Q9BY41")
    assert result.status == "budget_exhausted"
    assert not result.sources and result.request_count == 1


def test_cache_counts_are_reused_from_shared_http_client():
    tools, _ = registry(quickgo_payload(), cached=True)
    result = invoke(tools, "quickgo_annotations", uniprot_id="Q9BY41")
    assert result.status == "ok" and result.request_count == 0 and result.cache_hits == 1


@pytest.mark.parametrize("name,args,payload", [
    ("chembl", {"resource": "activity", "target_chembl_id": "CHEMBL2"}, {"error": "fixture"}),
    ("quickgo_annotations", {"uniprot_id": "Q9BY41"}, {"results": [], "numberOfHits": "0"}),
    ("reactome_pathways", {"uniprot_id": "Q9BY41"}, {"error": "fixture"}),
])
def test_malformed_success_response_is_unavailable(name, args, payload):
    tools, _ = registry(payload)
    assert invoke(tools, name, **args).status == "unavailable"


def test_literature_reuses_abstract_connector_never_downloads_full_text(monkeypatch):
    tools, client = registry()
    calls = []
    fixture = SourceRecord(source_id="pmid:1", provider="europe_pmc", title="Fixture paper",
                           url="https://pubmed.ncbi.nlm.nih.gov/1/", retrieved_at="2026-01-01",
                           text="Fixture abstract only", evidence_scope="abstract")
    def search(query, limit):
        calls.append((query, limit))
        return [fixture]
    monkeypatch.setattr(tools.sources, "search_literature", search)
    monkeypatch.setattr(tools.sources, "full_text", lambda *args: pytest.fail("No implicit full text"))
    result = invoke(tools, "europepmc_search", query="fixture hypothesis", limit=1)
    assert calls == [("fixture hypothesis", 1)]
    assert result.status == "truncated" and result.sources == [fixture]
    assert result.records[0]["evidence_scope"] == "abstract"
    assert not client.calls


def test_old_identity_connectors_are_reused_not_reimplemented(monkeypatch):
    tools, _ = registry()
    fixture = tools.sources.database_source("fixture", "Fixture", {"verified": True},
                                            "https://example.invalid", "uniprot")
    monkeypatch.setattr(tools.sources, "resolve_target", lambda gene, species: ("Q9BY41", fixture))
    monkeypatch.setattr(tools.sources, "resolve_molecule", lambda name: (
        {"pubchem_cid": "1", "smiles": "C", "inchikey": "fixture"}, fixture))
    assert invoke(tools, "uniprot", gene_symbol="HDAC8").records[0]["uniprot_id"] == "Q9BY41"
    assert invoke(tools, "pubchem", name="fixture").records[0]["pubchem_cid"] == "1"


def test_tool_request_disallows_silent_extra_fields_and_wrong_arguments_type():
    with pytest.raises(ValidationError):
        ToolRequest(name="chembl", arguments={}, url="https://example.invalid")
    with pytest.raises(ValidationError):
        ToolRequest(name="chembl", arguments="arbitrary code")

