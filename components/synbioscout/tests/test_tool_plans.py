"""Offline executor/identity-plan contracts; every biological value is SYNTHETIC.

The stack uses the production HTTPClient, BiomedicalSources, ToolRegistry and
ToolScheduler. Only requests.Session.request is replaced; no provider is called.
"""
from __future__ import annotations

import copy
import json
import time
from collections import deque

import pytest
import requests

from scientist.budgets import BudgetExceeded, RunLedger
from scientist.http import HTTPClient
from scientist.models import ResearchBudget
from scientist.sources import BiomedicalSources
from scientist.store import utc_now
from scientist.tool_plans import ToolScheduler
from scientist.tools import CHEMBL_URL, QUICKGO_URL, ToolRegistry, ToolRequest, ToolResult

ETHANOL_KEY = "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
ACCESSION = "Q9BY41"
SPECIES = "Homo sapiens"


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Tool-plan tests must never send a real HTTP request")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    # Retry behavior and accounting remain real; only backoff wall time is skipped.
    monkeypatch.setattr("scientist.http.time.sleep", lambda _: None)


def pubchem(rows=None):
    return {"PropertyTable": {"Properties": rows if rows is not None else [
        {"CID": 111, "SMILES": "CCO", "InChIKey": ETHANOL_KEY}]}}


def molecule(identifier="CHEMBL101", *, key=ETHANOL_KEY, parent=None):
    return {"molecule_chembl_id": identifier, "pref_name": "SYNTHETIC ethanol",
            "molecule_structures": {"standard_inchi_key": key, "canonical_smiles": "CCO"},
            "molecule_hierarchy": {"parent_chembl_id": parent or identifier},
            "molecule_synonyms": [{"molecule_synonym": "SYNTHETIC alias"}]}


def protein(accession=ACCESSION, *, species=SPECIES, reviewed=True, gene="SYNTHETIC8"):
    return {"primaryAccession": accession,
            "entryType": "UniProtKB reviewed (Swiss-Prot)" if reviewed else
                         "UniProtKB unreviewed (TrEMBL)",
            "organism": {"scientificName": species},
            "genes": [{"geneName": {"value": gene}}]}


def target(identifier="CHEMBL201", *, accession=ACCESSION, species=SPECIES,
           target_type="SINGLE PROTEIN"):
    return {"target_chembl_id": identifier, "pref_name": "SYNTHETIC target",
            "organism": species, "target_type": target_type,
            "target_components": [{"accession": accession, "component_type": "PROTEIN"}]}


def chembl(resource, rows, *, total=None, next_page=None):
    collection = {"molecule": "molecules", "target": "targets", "activity": "activities",
                  "mechanism": "mechanisms", "assay": "assays"}[resource]
    return {collection: rows, "page_meta": {"total_count": len(rows) if total is None else total,
                                           "next": next_page, "limit": 5, "offset": 0}}


def activity(identifier=7):
    return {"activity_id": identifier, "molecule_chembl_id": "CHEMBL101",
            "target_chembl_id": "CHEMBL201", "target_organism": SPECIES,
            "target_tax_id": "9606", "assay_chembl_id": "CHEMBL301", "assay_type": "B",
            "assay_description": "SYNTHETIC cellular binding assay",
            "document_chembl_id": "CHEMBL401", "standard_type": "IC50",
            "standard_value": "500", "standard_relation": ">", "standard_units": "nM",
            "value": "0.5", "relation": ">", "units": "uM",
            "data_validity_comment": "SYNTHETIC contextual warning", "potential_duplicate": 1}


class OfflineStack:
    def __init__(self, tmp_path, monkeypatch, *, retries=0, max_http=32, max_tools=12):
        self.ledger = RunLedger(ResearchBudget(max_http_requests=max_http,
                                              max_tool_calls=max_tools), time.monotonic() + 60)
        self.http = HTTPClient(tmp_path / "cache", retries=retries)
        self.http.deadline = self.ledger.deadline
        self.http.before_request, self.http.on_cache_hit = self.ledger.request, self.ledger.cache
        self.registry = ToolRegistry(BiomedicalSources(self.http))
        self.plans, self.calls, self.network, self.snapshots = [], [], [], []
        self.responses = deque()
        monkeypatch.setattr(self.http.session, "request", self.request)
        self.scheduler = ToolScheduler(self.execute, self.plans, self.persist)

    def enqueue(self, suffix, payload=None, *, status=200, failure=None):
        self.responses.append((suffix, payload, status, failure))

    def request(self, method, url, **options):
        assert self.responses, "Unexpected offline request: " + url
        suffix, payload, status, failure = self.responses.popleft()
        assert url.endswith(suffix), (url, suffix)
        assert options.get("allow_redirects") is False
        self.network.append({"method": method, "url": url, **copy.deepcopy(options)})
        if failure is not None:
            raise failure
        response = requests.Response()
        response.status_code, response.url = status, url
        response._content = json.dumps(payload).encode()
        response.headers["Content-Type"] = "application/json"
        return response

    def execute(self, request, *, plan_id="", gap_id="", parent_call_id="", identity=None):
        # Production scheduler receives this same ledger-owning executor contract.
        try:
            self.ledger.tool()
            result = self.registry.execute(request)
        except BudgetExceeded:
            result = ToolResult(tool_name=request.name, provider="none", parameters={},
                                status="budget_exhausted", retrieved_at=utc_now(),
                                stop_reason="shared_budget")
        result.call_id = f"tool_{len(self.calls):03d}"
        result.parent_call_id, result.plan_id, result.gap_id = parent_call_id, plan_id, gap_id
        result.requested_parameters = request.arguments
        if identity and not result.identity:
            result.identity = copy.deepcopy(identity)
        self.calls.append(result)
        return result

    def persist(self):
        self.snapshots.append(copy.deepcopy(self.plans))

    def plan(self, name="chembl", **arguments):
        return self.scheduler.create(ToolRequest(name=name, arguments=arguments),
                                     origin="model_gap", reason="SYNTHETIC evidence gap",
                                     hypothesis_ids=["h_synthetic:v1"], gap_id="gap_synthetic")

    def run(self, name="chembl", **arguments):
        plan = self.plan(name, **arguments)
        return plan, self.scheduler.run(plan)

    def molecule_responses(self, *, resource="activity", rows=None):
        self.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
        self.enqueue("/molecule.json", chembl("molecule", [molecule()]))
        self.enqueue("/" + resource + ".json", chembl(resource, rows if rows is not None else [activity()]))

    def target_responses(self, *, rows=None):
        self.enqueue("/uniprotkb/search", {"results": [protein()]})
        self.enqueue("/target.json", chembl("target", [target()]))
        self.enqueue("/activity.json", chembl("activity", rows if rows is not None else [activity()]))


@pytest.fixture
def stack(tmp_path, monkeypatch):
    return OfflineStack(tmp_path, monkeypatch)


@pytest.mark.parametrize("resource", ["activity", "mechanism"])
def test_name_resolves_pubchem_then_exact_chembl_before_query_with_parent_trace(stack, resource):
    row = activity() if resource == "activity" else {
        "mec_id": 9, "molecule_chembl_id": "CHEMBL101", "target_chembl_id": "CHEMBL201",
        "action_type": "INHIBITOR", "mechanism_of_action": "SYNTHETIC binding mechanism"}
    stack.molecule_responses(resource=resource, rows=[row])
    plan, result = stack.run(resource=resource, molecule_name="SYNTHETIC alias", limit=2)
    assert result.status == plan["status"] == "ok"
    assert result.records == [row] and result.record_count == 1
    assert [call.tool_name for call in stack.calls] == ["pubchem", "chembl", "chembl"]
    assert plan["call_ids"] == [call.call_id for call in stack.calls]
    assert [call.parent_call_id for call in stack.calls] == ["", "tool_000", "tool_001"]
    assert all(call.plan_id == plan["plan_id"] and call.gap_id == "gap_synthetic" for call in stack.calls)
    assert result.identity["molecule_chembl_id"] == "CHEMBL101"
    assert stack.network[1]["params"]["molecule_structures__standard_inchi_key"] == ETHANOL_KEY
    assert stack.network[2]["params"]["molecule_chembl_id"] == "CHEMBL101"
    assert stack.calls[1].http_trace[0]["parameters"]["molecule_structures__standard_inchi_key"] == ETHANOL_KEY
    assert stack.ledger.snapshot()["tool_calls"] == stack.ledger.snapshot()["http_attempts"] == 3
    assert all(source.evidence_scope == "database" for call in stack.calls for source in call.sources)
    assert result.sources[0].metadata["scope"] == "database_annotation_not_intervention_or_production_proof"
    assert "heterogeneous" in " ".join(result.warnings)
    assert not stack.responses


def test_target_symbol_resolves_uniprot_accession_and_species_before_activity(stack):
    stack.target_responses()
    plan, result = stack.run(resource="activity", target_symbol="SYNTHETIC8", species=SPECIES)
    assert result.status == "ok" and result.records == [activity()]
    assert stack.calls[0].identity["selected_id"] == ACCESSION
    assert stack.network[1]["params"] == {"limit": 5, "offset": 0,
        "target_components__accession": ACCESSION, "organism": SPECIES}
    assert stack.calls[1].http_trace[0]["parameters"]["target_components__accession"] == ACCESSION
    assert stack.network[2]["params"]["target_chembl_id"] == "CHEMBL201"
    assert stack.network[2]["params"]["target_organism"] == SPECIES
    assert result.identity["target_chembl_id"] == "CHEMBL201"
    assert plan["call_ids"] == ["tool_000", "tool_001", "tool_002"]


def test_chemical_forms_return_candidates_and_never_select_first_or_query_activity(stack):
    rows = [{"CID": 111, "SMILES": "CCO", "InChIKey": ETHANOL_KEY},
            {"CID": 112, "SMILES": "CCO.Cl", "InChIKey": "SYNTHETIC_SALT_FORM"}]
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem(rows))
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC chemical forms")
    assert result is None and plan["status"] == "unresolved"
    assert plan["identity"]["status"] == "ambiguous"
    assert [row["cid"] for row in plan["identity"]["candidates"]] == ["111", "112"]
    assert not plan["identity"].get("selected_id")
    assert len(stack.calls) == len(stack.network) == 1


@pytest.mark.parametrize("entity", ["molecule", "target"])
def test_multiple_chembl_forms_or_targets_remain_unresolved(stack, entity):
    if entity == "molecule":
        stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
        rows = [molecule(), molecule("CHEMBL102", parent="CHEMBL101")]
        arguments = {"molecule_name": "SYNTHETIC alias"}
    else:
        stack.enqueue("/uniprotkb/search", {"results": [protein()]})
        rows = [target(), target("CHEMBL202")]
        arguments = {"target_symbol": "SYNTHETIC8", "species": SPECIES}
    stack.enqueue("/" + entity + ".json", chembl(entity, rows))
    plan, result = stack.run(resource="activity", **arguments)
    assert result is None and plan["status"] == "unresolved"
    assert plan["identity"]["status"] == "ambiguous"
    assert len(plan["identity"]["candidates"]) == 2
    assert not plan["identity"].get("selected_id")
    assert len(stack.calls) == len(stack.network) == 2


def test_uniprot_reviewed_entry_does_not_hide_another_exact_construct(stack):
    stack.enqueue("/uniprotkb/search", {"results": [protein(), protein("P04637", reviewed=False)]})
    plan, result = stack.run(resource="activity", target_symbol="SYNTHETIC8", species=SPECIES)
    assert result is None and plan["identity"]["status"] == "ambiguous"
    assert len(plan["identity"]["candidates"]) == 2
    assert not plan["identity"].get("selected_id") and len(stack.network) == 1


@pytest.mark.parametrize("source", ["uniprot", "chembl"])
def test_same_symbol_wrong_species_cannot_reach_activity(stack, source):
    stack.enqueue("/uniprotkb/search", {"results": [protein(species="Mus musculus") if source == "uniprot"
                                                    else protein()]})
    if source == "chembl":
        stack.enqueue("/target.json", chembl("target", [target(species="Mus musculus")]))
    plan, result = stack.run(resource="activity", target_symbol="SYNTHETIC8", species=SPECIES)
    assert result is None and plan["status"] in {"unresolved", "unavailable"}
    assert not plan["identity"].get("target_chembl_id")
    assert all(not call["url"].endswith("/activity.json") for call in stack.network)


def test_target_complex_cannot_be_selected_as_single_protein(stack):
    stack.enqueue("/uniprotkb/search", {"results": [protein()]})
    stack.enqueue("/target.json", chembl("target", [target(target_type="PROTEIN COMPLEX")]))
    plan, result = stack.run(resource="activity", target_symbol="SYNTHETIC8", species=SPECIES)
    assert result is None and plan["identity"]["status"] == "unresolved"
    assert not plan["identity"].get("selected_id") and len(stack.network) == 2


def test_pubchem_structure_key_mismatch_stops_the_chain(stack):
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem([
        {"CID": 111, "SMILES": "C", "InChIKey": ETHANOL_KEY}]))
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC mismatched structure")
    assert result is None and plan["identity"]["status"] == "mismatch"
    assert len(stack.network) == 1 and not plan["identity"].get("selected_id")


def test_chembl_structure_key_mismatch_stops_after_pubchem_without_activity(stack):
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
    row = molecule()
    row["molecule_structures"]["canonical_smiles"] = "C"
    stack.enqueue("/molecule.json", chembl("molecule", [row]))
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC alias")
    assert result is None and plan["identity"]["status"] != "resolved"
    assert not plan["identity"].get("selected_id") and len(stack.network) == 2


def test_target_gene_alias_preserves_species_and_accession_identity(stack):
    row = protein(gene="SYNTHETIC_PRIMARY")
    row["genes"][0]["synonyms"] = [{"value": "SYNTHETIC8"}]
    stack.enqueue("/uniprotkb/search", {"results": [row]})
    stack.enqueue("/target.json", chembl("target", [target()]))
    stack.enqueue("/activity.json", chembl("activity", [activity()]))
    plan, result = stack.run(resource="activity", target_symbol="SYNTHETIC8", species=SPECIES)
    assert result.status == plan["status"] == "ok" and result.records == [activity()]
    assert stack.calls[0].identity["selected_id"] == ACCESSION
    assert stack.network[1]["params"]["organism"] == SPECIES


def test_chembl_alias_search_does_not_select_a_single_fuzzy_name_match(stack):
    row = molecule()
    stack.enqueue("/molecule/search.json", chembl("molecule", [row]))
    plan, result = stack.run(resource="molecule", query="SYNTHETIC partial")
    assert result.status == "ok" and result.identity["status"] == "unresolved"
    assert not result.identity["selected_id"] and len(stack.network) == 1
    assert stack.network[0]["params"]["q"] == "SYNTHETIC partial"
    assert plan["identity"]["candidates"][0]["id"] == "CHEMBL101"


def test_typed_name_intent_uses_identity_chain_before_activity(stack):
    stack.molecule_responses()
    plan, result = stack.run(resource="activity", query="SYNTHETIC alias", entity_type="molecule")
    assert result.status == plan["status"] == "ok"
    assert stack.calls[0].tool_name == "pubchem" and stack.calls[1].identity["selected_id"] == "CHEMBL101"
    assert "query" not in stack.network[-1]["params"]


@pytest.mark.parametrize("resource", ["activity", "mechanism"])
@pytest.mark.parametrize("entity", ["molecule", "target"])
def test_typed_query_cannot_override_same_entity_id_before_any_http(stack, resource, entity):
    arguments = {"resource": resource, "query": "SYNTHETIC alias" if entity == "molecule" else "SYNTHETIC8",
                 "entity_type": entity, entity + "_chembl_id": "CHEMBL999"}
    if entity == "target":
        arguments["species"] = SPECIES
    plan, result = stack.run(**arguments)
    assert result.status == plan["status"] == "invalid"
    assert plan["request"]["arguments"] == arguments
    assert result.requested_parameters == arguments and not result.records and not result.sources
    assert plan["call_ids"] == [result.call_id] and stack.snapshots[-1][0] == plan
    assert len(stack.calls) == stack.ledger.snapshot()["tool_calls"] == 1
    assert not stack.network and result.request_count == stack.ledger.snapshot()["http_attempts"] == 0


@pytest.mark.parametrize("arguments", [
    {"resource": "molecule", "query": "SYNTHETIC alias", "entity_type": "target"},
    {"resource": "target", "query": "SYNTHETIC8", "entity_type": "molecule", "species": SPECIES},
    {"resource": "molecule", "query": "SYNTHETIC alias", "entity_type": "molecule",
     "molecule_chembl_id": "CHEMBL999"},
    {"resource": "target", "query": "SYNTHETIC8", "entity_type": "target",
     "target_chembl_id": "CHEMBL999", "species": SPECIES},
    {"resource": "activity", "query": "SYNTHETIC alias", "entity_type": "molecule",
     "target_symbol": "SYNTHETIC8", "species": SPECIES},
    {"resource": "activity", "query": "SYNTHETIC8", "entity_type": "target",
     "molecule_name": "SYNTHETIC alias", "species": SPECIES},
    {"resource": "assay", "query": "SYNTHETIC8", "entity_type": "target",
     "target_chembl_id": "CHEMBL999", "species": SPECIES},
    {"resource": "activity", "query": "SYNTHETIC invalid symbol", "entity_type": "target",
     "species": SPECIES},
])
def test_query_entity_resource_and_selector_conflicts_are_saved_invalid_without_http(stack, arguments):
    plan, result = stack.run(**arguments)
    assert result.status == plan["status"] == "invalid"
    assert plan["call_ids"] == [result.call_id] and stack.snapshots[-1][0] == plan
    assert result.requested_parameters == arguments and not result.records and not result.sources
    assert len(stack.calls) == stack.ledger.snapshot()["tool_calls"] == 1
    assert not stack.network and stack.ledger.snapshot()["http_attempts"] == 0


@pytest.mark.parametrize("selector", [
    {"molecule_name": "SYNTHETIC alias"},
    {"molecule_chembl_id": "CHEMBL101"},
    {"query": "SYNTHETIC alias", "entity_type": "molecule"},
])
def test_mechanism_species_requires_target_selector_before_any_http(stack, selector):
    arguments = {"resource": "mechanism", "species": SPECIES, **selector}
    plan, result = stack.run(**arguments)
    assert result.status == plan["status"] == "invalid"
    assert plan["request"]["arguments"] == result.requested_parameters == arguments
    assert not result.records and not result.sources and plan["call_ids"] == [result.call_id]
    assert stack.snapshots[-1][0] == plan
    assert len(stack.calls) == stack.ledger.snapshot()["tool_calls"] == 1
    assert not stack.network and result.request_count == stack.ledger.snapshot()["http_attempts"] == 0


def test_legacy_low_level_mechanism_species_remains_invalid_without_http(stack):
    result = stack.execute(ToolRequest(name="chembl", arguments={
        "resource": "mechanism", "molecule_chembl_id": "CHEMBL101", "species": SPECIES}))
    assert result.status == "invalid" and not result.records and not result.sources
    assert len(stack.calls) == stack.ledger.snapshot()["tool_calls"] == 1
    assert not stack.network and stack.ledger.snapshot()["http_attempts"] == 0


@pytest.mark.parametrize("selector", [
    {"target_symbol": "SYNTHETIC8"},
    {"target_chembl_id": "CHEMBL201"},
    {"query": "SYNTHETIC8", "entity_type": "target"},
])
def test_mechanism_species_is_checked_by_explicit_or_typed_target_identity(stack, selector):
    if "target_chembl_id" not in selector:
        stack.enqueue("/uniprotkb/search", {"results": [protein()]})
    stack.enqueue("/target.json", chembl("target", [target()]))
    row = {"mec_id": 9, "target_chembl_id": "CHEMBL201", "molecule_chembl_id": "CHEMBL101"}
    stack.enqueue("/mechanism.json", chembl("mechanism", [row]))
    plan, result = stack.run(resource="mechanism", species=SPECIES, **selector)
    assert result.status == plan["status"] == "ok" and result.records == [row]
    assert stack.network[-2]["params"]["organism"] == SPECIES
    assert result.identity["species"] == SPECIES and result.identity["target_chembl_id"] == "CHEMBL201"
    assert stack.network[-1]["params"]["target_chembl_id"] == "CHEMBL201"
    assert "species" not in result.parameters and not stack.responses


@pytest.mark.parametrize("entity", ["molecule", "target"])
def test_matching_typed_identity_search_still_uses_the_legacy_direct_query(stack, entity):
    query = "SYNTHETIC alias" if entity == "molecule" else "SYNTHETIC target"
    stack.enqueue("/" + entity + "/search.json", chembl(entity, [molecule() if entity == "molecule" else target()]))
    arguments = {"species": SPECIES} if entity == "target" else {}
    plan, result = stack.run(resource=entity, query=query, entity_type=entity, **arguments)
    assert result.status == plan["status"] == "ok" and result.identity["status"] == "resolved"
    assert stack.network[0]["params"]["q"] == query and "entity_type" not in result.parameters
    assert len(stack.calls) == len(stack.network) == 1 and not stack.responses


@pytest.mark.parametrize("explicit_entity", ["molecule", "target"])
def test_untyped_query_with_entity_id_stays_unresolved_without_guessing_or_http(stack, explicit_entity):
    plan, result = stack.run(resource="activity", query="SYNTHETIC unknown entity",
                             **{explicit_entity + "_chembl_id": "CHEMBL999"})
    assert result is None and plan["status"] == "unresolved"
    assert plan["stop_reason"] == "identity_type_required"
    assert not stack.calls and not stack.network and stack.ledger.snapshot()["http_attempts"] == 0


@pytest.mark.parametrize("resource", ["activity", "mechanism"])
@pytest.mark.parametrize("query_entity", ["molecule", "target"])
def test_typed_query_can_combine_other_entity_id_and_keeps_both_verified_constraints(stack, resource, query_entity):
    if query_entity == "molecule":
        stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
        stack.enqueue("/molecule.json", chembl("molecule", [molecule()]))
        stack.enqueue("/target.json", chembl("target", [target()]))
        arguments = {"query": "SYNTHETIC alias", "target_chembl_id": "CHEMBL201"}
        explicit_index = 2
    else:
        stack.enqueue("/molecule.json", chembl("molecule", [molecule()]))
        stack.enqueue("/uniprotkb/search", {"results": [protein()]})
        stack.enqueue("/target.json", chembl("target", [target()]))
        arguments = {"query": "SYNTHETIC8", "molecule_chembl_id": "CHEMBL101"}
        explicit_index = 0
    row = activity() if resource == "activity" else {
        "mec_id": 9, "molecule_chembl_id": "CHEMBL101", "target_chembl_id": "CHEMBL201",
        "action_type": "INHIBITOR", "mechanism_of_action": "SYNTHETIC binding mechanism"}
    stack.enqueue("/" + resource + ".json", chembl(resource, [row]))
    plan, result = stack.run(resource=resource, entity_type=query_entity, species=SPECIES, **arguments)
    assert result.status == plan["status"] == "ok" and result.records == [row]
    selected = {"molecule_chembl_id": "CHEMBL101", "target_chembl_id": "CHEMBL201"}
    assert all(result.identity[field] == identifier for field, identifier in selected.items())
    assert all(stack.network[-1]["params"][field] == identifier for field, identifier in selected.items())
    opposite_id = "target_chembl_id" if query_entity == "molecule" else "molecule_chembl_id"
    assert stack.network[explicit_index]["params"][opposite_id] == arguments[opposite_id]
    assert stack.calls[explicit_index].identity["selected_id"] == arguments[opposite_id]
    assert stack.network[2]["params"]["organism"] == SPECIES
    if resource == "activity":
        assert stack.network[-1]["params"]["target_organism"] == SPECIES
    assert "query" not in result.parameters and "entity_type" not in result.parameters
    assert [call.parent_call_id for call in stack.calls] == ["", "tool_000", "tool_001", "tool_002"]
    assert len(plan["call_ids"]) == len(stack.network) == 4 and not stack.responses
    assert stack.ledger.snapshot()["tool_calls"] == stack.ledger.snapshot()["http_attempts"] == 4


@pytest.mark.parametrize("query_entity", ["molecule", "target"])
def test_typed_query_other_entity_id_mismatch_stops_without_experimental_query(stack, query_entity):
    if query_entity == "molecule":
        stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
        stack.enqueue("/molecule.json", chembl("molecule", [molecule()]))
        stack.enqueue("/target.json", chembl("target", [target("CHEMBL999")]))
        arguments = {"query": "SYNTHETIC alias", "target_chembl_id": "CHEMBL201"}
        expected_calls = 3
    else:
        stack.enqueue("/molecule.json", chembl("molecule", [molecule("CHEMBL999")]))
        arguments = {"query": "SYNTHETIC8", "molecule_chembl_id": "CHEMBL101"}
        expected_calls = 1
    plan, result = stack.run(resource="activity", entity_type=query_entity, species=SPECIES, **arguments)
    assert result is None and plan["status"] == "unavailable"
    assert stack.calls[-1].status == "unavailable" and not stack.calls[-1].records
    assert not stack.calls[-1].identity.get("selected_id")
    assert len(stack.calls) == len(stack.network) == expected_calls and not stack.responses
    assert all(not call["url"].endswith("/activity.json") for call in stack.network)


@pytest.mark.parametrize("resource", ["activity", "mechanism"])
def test_scheduler_id_only_legacy_request_keeps_both_identity_constraints(stack, resource):
    stack.enqueue("/molecule.json", chembl("molecule", [molecule()]))
    stack.enqueue("/target.json", chembl("target", [target()]))
    row = activity() if resource == "activity" else {
        "mec_id": 9, "molecule_chembl_id": "CHEMBL101", "target_chembl_id": "CHEMBL201"}
    stack.enqueue("/" + resource + ".json", chembl(resource, [row]))
    plan, result = stack.run(resource=resource, molecule_chembl_id="CHEMBL101",
                             target_chembl_id="CHEMBL201", species=SPECIES)
    assert result.status == plan["status"] == "ok" and result.records == [row]
    assert result.parameters["molecule_chembl_id"] == "CHEMBL101"
    assert result.parameters["target_chembl_id"] == "CHEMBL201"
    assert len(stack.calls) == len(stack.network) == 3 and not stack.responses


@pytest.mark.parametrize("resource", ["activity", "mechanism"])
@pytest.mark.parametrize("selector", ["molecule", "target", "both"])
def test_legacy_low_level_id_only_requests_still_execute_without_name_compilation(stack, resource, selector):
    arguments = {"resource": resource}
    if selector in {"molecule", "both"}:
        arguments["molecule_chembl_id"] = "CHEMBL101"
    if selector in {"target", "both"}:
        arguments["target_chembl_id"] = "CHEMBL201"
    if resource == "activity":
        arguments["species"] = SPECIES
    row = activity() if resource == "activity" else {
        "mec_id": 9, "molecule_chembl_id": "CHEMBL101", "target_chembl_id": "CHEMBL201"}
    stack.enqueue("/" + resource + ".json", chembl(resource, [row]))
    result = stack.execute(ToolRequest(name="chembl", arguments=arguments))
    assert result.status == "ok" and result.records == [row]
    for field, identifier in arguments.items():
        assert result.parameters[field] == identifier
    assert len(stack.calls) == len(stack.network) == 1 and not stack.responses
    assert stack.ledger.snapshot()["tool_calls"] == stack.ledger.snapshot()["http_attempts"] == 1


def test_both_molecule_and_target_names_keep_both_verified_ids_in_activity(stack):
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
    stack.enqueue("/molecule.json", chembl("molecule", [molecule()]))
    stack.enqueue("/uniprotkb/search", {"results": [protein()]})
    stack.enqueue("/target.json", chembl("target", [target()]))
    stack.enqueue("/activity.json", chembl("activity", [activity()]))
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC alias",
                             target_symbol="SYNTHETIC8", species=SPECIES)
    assert result.status == "ok" and result.records == [activity()]
    assert result.identity["molecule_chembl_id"] == "CHEMBL101"
    assert result.identity["target_chembl_id"] == "CHEMBL201"
    assert [call.parent_call_id for call in stack.calls] == ["", "tool_000", "tool_001", "tool_002", "tool_003"]
    assert len(plan["call_ids"]) == 5 and stack.ledger.snapshot()["tool_calls"] == 5


def test_chembl_hidden_identity_rows_below_limit_cannot_establish_uniqueness(stack):
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
    stack.enqueue("/molecule.json", chembl("molecule", [molecule()], total=2))
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC alias")
    assert result is None and plan["identity"]["status"] != "resolved"
    assert not plan["identity"].get("selected_id") and len(stack.network) == 2


@pytest.mark.parametrize("arguments", [
    {"resource": "target", "molecule_name": "SYNTHETIC alias"},
    {"resource": "molecule", "target_symbol": "SYNTHETIC8", "species": SPECIES},
    {"resource": "assay", "molecule_name": "SYNTHETIC alias"},
    {"resource": "activity", "chembl_id": "CHEMBL101", "molecule_name": "SYNTHETIC alias"},
])
def test_incompatible_resource_identity_intent_is_invalid_before_resolution_http(stack, arguments):
    plan, result = stack.run(**arguments)
    assert result.status == plan["status"] == "invalid"
    assert not stack.network and stack.ledger.snapshot()["http_attempts"] == 0


@pytest.mark.parametrize("provider", ["pubchem", "uniprot", "chembl"])
def test_incomplete_identity_search_stops_before_experimental_records(stack, provider):
    arguments = {"molecule_name": "SYNTHETIC alias"}
    if provider == "pubchem":
        stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem([
            {"CID": 111 + i, "SMILES": "CCO", "InChIKey": ETHANOL_KEY} for i in range(11)]))
    elif provider == "uniprot":
        arguments = {"target_symbol": "SYNTHETIC8", "species": SPECIES}
        stack.enqueue("/uniprotkb/search", {"results": [protein() for _ in range(5)]})
    else:
        stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
        stack.enqueue("/molecule.json", chembl("molecule", [molecule()], total=6,
                                              next_page="https://example.invalid/not-followed"))
    plan, result = stack.run(resource="activity", **arguments)
    assert result is None and plan["identity"]["status"] == "truncated"
    assert not plan["identity"].get("selected_id")
    assert all(not call["url"].endswith("/activity.json") for call in stack.network)


@pytest.mark.parametrize("provider", ["pubchem", "uniprot", "chembl"])
def test_successful_empty_identity_response_is_no_match_and_not_activity(stack, provider):
    arguments = {"molecule_name": "SYNTHETIC absent"}
    if provider == "pubchem":
        stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem([]))
    elif provider == "uniprot":
        arguments = {"target_symbol": "SYNTHETIC8", "species": SPECIES}
        stack.enqueue("/uniprotkb/search", {"results": []})
    else:
        stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
        stack.enqueue("/molecule.json", chembl("molecule", []))
    plan, result = stack.run(resource="activity", **arguments)
    assert result is None and plan["status"] == "unresolved"
    assert plan["identity"]["status"] == "empty" and stack.calls[-1].status == "empty"
    assert not plan["identity"].get("selected_id")


def test_pubchem_documented_not_found_is_empty_and_not_retried(tmp_path, monkeypatch):
    stack = OfflineStack(tmp_path, monkeypatch, retries=2)
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", {"Fault": {"Code": "PUGREST.NotFound"}},
                  status=404)
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC absent")
    assert result is None and plan["identity"]["status"] == "empty"
    assert stack.calls[0].request_count == stack.ledger.snapshot()["http_attempts"] == 1
    assert not stack.responses


@pytest.mark.parametrize("arguments", [
    {"resource": "activity", "molecule_name": "SYNTHETIC", "limit": 11},
    {"resource": "activity", "target_symbol": "SYNTHETIC8"},
    {"resource": "activity", "molecule_name": "SYNTHETIC", "molecule_chembl_id": "CHEMBL101"},
    {"resource": "activity", "query": "SYNTHETIC", "entity_type": "molecule", "url": "https://example.invalid"},
])
def test_invalid_plan_parameters_never_send_http(stack, arguments):
    plan, result = stack.run(**arguments)
    assert result.status == plan["status"] == "invalid"
    assert not stack.network and stack.ledger.snapshot()["http_attempts"] == 0


def test_untyped_name_query_remains_unresolved_without_guessing_entity_or_calling_tools(stack):
    plan, result = stack.run(resource="activity", query="SYNTHETIC unnamed entity")
    assert result is None and plan["status"] == "unresolved"
    assert plan["stop_reason"] == "identity_type_required"
    assert not stack.calls and not stack.network


@pytest.mark.parametrize("arguments", [
    {"resource": "activity"}, {"resource": "mechanism"},
    {"resource": "activity", "query": "SYNTHETIC name"},
    {"resource": "activity", "inchikey": ETHANOL_KEY},
    {"resource": "mechanism", "chembl_id": "CHEMBL101"},
])
def test_low_level_activity_and_mechanism_id_validation_is_not_relaxed(stack, arguments):
    result = stack.registry.execute(ToolRequest(name="chembl", arguments=arguments))
    assert result.status == "invalid" and not stack.network


@pytest.mark.parametrize("failure", [None, requests.ConnectionError("SYNTHETIC disconnected")])
def test_provider_500_or_network_error_cannot_become_empty_or_fabricated_activity(stack, failure):
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", {"error": "SYNTHETIC provider failure"},
                  status=500, failure=failure)
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC alias")
    assert result is None and plan["status"] == "unavailable"
    assert stack.calls[0].status == "unavailable" and not stack.calls[0].records
    assert not stack.calls[0].sources and stack.calls[0].request_count == 1
    assert len(stack.network) == 1


@pytest.mark.parametrize("failed_step", ["identity", "activity"])
def test_intermediate_or_final_failure_keeps_prior_identity_sources_and_no_activity(stack, failed_step):
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
    stack.enqueue("/molecule.json", chembl("molecule", [molecule()]) if failed_step == "activity" else
                  {"error": "SYNTHETIC identity provider unavailable"},
                  status=200 if failed_step == "activity" else 500)
    if failed_step == "activity":
        stack.enqueue("/activity.json", {"error": "SYNTHETIC activity provider unavailable"}, status=500)
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC alias")
    assert plan["status"] == "unavailable" and stack.calls[-1].status == "unavailable"
    assert not stack.calls[-1].records and not stack.calls[-1].sources
    assert stack.calls[0].identity["selected_id"] == "111" and stack.calls[0].sources
    assert len(plan["call_ids"]) == (2 if failed_step == "identity" else 3)
    if failed_step == "activity":
        assert result is stack.calls[-1] and plan["identity"]["molecule_chembl_id"] == "CHEMBL101"
        assert stack.calls[1].sources
    else:
        assert result is None
    assert stack.snapshots[-1][0] == plan


def test_resolved_identity_with_zero_activity_is_a_saved_empty_query_not_efficacy(stack):
    stack.molecule_responses(rows=[])
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC alias")
    assert result.status == plan["status"] == "empty" and result.stop_reason == "no_records"
    assert result.identity["molecule_chembl_id"] == "CHEMBL101" and not result.records
    assert result.sources[0].metadata["records"] == []
    assert result.sources[0].metadata["scope"] == "database_annotation_not_intervention_or_production_proof"
    assert len(plan["call_ids"]) == len(stack.network) == 3


@pytest.mark.parametrize("budget", ["http", "tool"])
def test_budget_after_resolution_preserves_partial_identity_and_explicit_stop(tmp_path, monkeypatch, budget):
    stack = OfflineStack(tmp_path, monkeypatch, max_http=2 if budget == "http" else 32,
                         max_tools=2 if budget == "tool" else 12)
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
    stack.enqueue("/molecule.json", chembl("molecule", [molecule()]))
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC alias")
    assert result.status == plan["status"] == "budget_exhausted"
    assert result.stop_reason == plan["stop_reason"] == "shared_budget"
    assert not result.records and len(stack.network) == 2
    assert stack.calls[0].sources and stack.calls[1].sources
    assert plan["identity"]["molecule_chembl_id"] == "CHEMBL101"
    assert len(plan["call_ids"]) == 3 and stack.snapshots[-1][0] == plan
    assert stack.ledger.snapshot()["http_attempts"] == 2
    assert stack.ledger.snapshot()["tool_calls"] == (2 if budget == "tool" else 3)
    if budget == "http":
        assert result.request_count == 0 and result.http_trace[-1]["outcome"] == "blocked_budget"


def test_duplicate_plan_and_shared_identity_steps_do_not_spend_twice(stack):
    stack.molecule_responses()
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC alias")
    duplicate = stack.plan(resource="activity", molecule_name="SYNTHETIC alias")
    assert stack.scheduler.run(duplicate) is None
    assert duplicate["status"] == "skipped" and duplicate["duplicate_of"] == plan["plan_id"]
    assert len(stack.calls) == 3
    stack.enqueue("/mechanism.json", chembl("mechanism", [{"mec_id": 9,
                  "molecule_chembl_id": "CHEMBL101", "target_chembl_id": "CHEMBL201"}]))
    other, mechanism = stack.run(resource="mechanism", molecule_name="SYNTHETIC alias")
    assert result.status == mechanism.status == "ok"
    assert other["call_ids"][:2] == plan["call_ids"][:2]
    assert mechanism.parent_call_id == "tool_001" and mechanism.call_id == "tool_003"
    assert stack.ledger.snapshot()["tool_calls"] == stack.ledger.snapshot()["http_attempts"] == 4
    assert len(stack.network) == 4 and not stack.responses


def test_real_http_cache_is_distinct_from_scheduler_memo_and_actual_attempts(stack):
    stack.molecule_responses()
    first, result = stack.run(resource="activity", molecule_name="SYNTHETIC alias")
    fresh_plans = []
    fresh_scheduler = ToolScheduler(stack.execute, fresh_plans)
    second = fresh_scheduler.create(ToolRequest(name="chembl", arguments={
        "resource": "activity", "molecule_name": "SYNTHETIC alias"}))
    cached = fresh_scheduler.run(second)
    assert cached.status == result.status == "ok" and cached.records == result.records
    assert len(first["call_ids"]) == len(second["call_ids"]) == 3
    assert not set(first["call_ids"]).intersection(second["call_ids"])
    assert len(stack.network) == 3 and len(stack.calls) == 6
    assert all(call.cache_hits == 1 and call.request_count == 0 for call in stack.calls[3:])
    assert all(call.http_trace[0]["outcome"] == "cache_hit" and call.http_trace[0]["attempt"] == 0
               for call in stack.calls[3:])
    counts = stack.ledger.snapshot()
    assert counts["http_attempts"] == 3 and counts["cache_hits"] == 3 and counts["tool_calls"] == 6


def test_real_http_retry_success_counts_each_attempt_once(tmp_path, monkeypatch):
    stack = OfflineStack(tmp_path, monkeypatch, retries=1)
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", {"error": "SYNTHETIC retry"}, status=503)
    stack.molecule_responses()
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC alias")
    assert result.status == plan["status"] == "ok"
    assert stack.calls[0].request_count == 2 and stack.calls[0].cache_hits == 0
    assert [entry["http_status"] for entry in stack.calls[0].http_trace] == [503, 200]
    assert [entry["retry_index"] for entry in stack.calls[0].http_trace] == [0, 1]
    assert stack.ledger.snapshot()["http_attempts"] == 4 and stack.ledger.snapshot()["tool_calls"] == 3


def test_retry_is_blocked_by_shared_http_budget_without_a_second_network_attempt(tmp_path, monkeypatch):
    stack = OfflineStack(tmp_path, monkeypatch, retries=2, max_http=1)
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", {"error": "SYNTHETIC retry"}, status=500)
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC alias")
    assert result is None and plan["status"] == "budget_exhausted"
    assert stack.calls[0].request_count == 1 and len(stack.network) == 1
    assert [entry["outcome"] for entry in stack.calls[0].http_trace] == ["http_error", "blocked_budget"]
    assert stack.ledger.snapshot()["http_attempts"] == stack.ledger.snapshot()["tool_calls"] == 1


def test_activity_pagination_is_bounded_and_keeps_original_assay_context(stack):
    stack.enqueue("/property/IsomericSMILES,InChIKey/JSON", pubchem())
    stack.enqueue("/molecule.json", chembl("molecule", [molecule()]))
    row = activity()
    stack.enqueue("/activity.json", chembl("activity", [row], total=40,
                                          next_page="https://example.invalid/not-followed"))
    plan, result = stack.run(resource="activity", molecule_name="SYNTHETIC alias", limit=2, page=2)
    assert result.status == plan["status"] == "truncated" and result.stop_reason == "return_limit"
    assert result.records == [row] and result.pagination["page"] == 2
    assert stack.network[-1]["params"]["offset"] == 2
    assert stack.network[-1]["params"]["limit"] == 2 and len(stack.network) == 3
    assert result.records[0]["units"] == "uM" and result.records[0]["standard_units"] == "nM"
    assert result.records[0]["relation"] == result.records[0]["standard_relation"] == ">"
    assert result.records[0]["activity_id"] == 7 and result.records[0]["assay_chembl_id"] == "CHEMBL301"
    assert result.records[0]["target_organism"] == SPECIES
    assert result.sources[0].provider == "chembl" and result.sources[0].url.startswith(CHEMBL_URL)
    assert not any(key in row for key in ("efficacy_score", "production_yield", "pkpd_parameter"))


@pytest.mark.parametrize("field,value", [
    ("goId", "GO:0000002"), ("evidenceCode", "ECO:0000002"), ("goAspect", "cellular_component")])
def test_go_ignored_exact_filter_is_unavailable_without_annotation_source(stack, field, value):
    row = {"geneProductId": "UniProtKB:" + ACCESSION, "taxonId": 9606,
           "goId": "GO:0000001", "evidenceCode": "ECO:0000001",
           "goAspect": "molecular_function", "qualifier": "NOT|enables", "goEvidence": "IEA"}
    row[field] = value
    stack.enqueue("/annotation/search", {"results": [row], "numberOfHits": 1, "pageInfo": {}})
    plan, result = stack.run("quickgo_annotations", uniprot_id=ACCESSION, go_id="GO:0000001",
                             evidence_code="ECO:0000001", aspect="molecular_function")
    assert result.status == plan["status"] == "unavailable" and not result.records and not result.sources
    assert result.request_count == 1 and stack.network[0]["url"] == QUICKGO_URL


def test_old_tool_result_with_no_trace_fields_still_validates_without_invented_calls():
    old = {"tool_name": "chembl", "provider": "chembl", "parameters": {}, "status": "empty",
           "retrieved_at": "2026-01-01", "records": [], "sources": [], "warnings": []}
    result = ToolResult.model_validate(old)
    assert not result.call_id and not result.parent_call_id and not result.plan_id and not result.gap_id
    assert not result.http_trace and not result.identity and not result.pagination
    assert old.keys() == {"tool_name", "provider", "parameters", "status", "retrieved_at",
                          "records", "sources", "warnings"}

