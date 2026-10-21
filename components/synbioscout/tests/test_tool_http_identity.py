"""Offline fixtures exercising actual HTTP accounting and identity connectors."""

import json
import time

import pytest
import requests
from rdkit import Chem

from scientist.budgets import BudgetExceeded, RunLedger
from scientist.http import DataSourceError, HTTPClient
from scientist.models import ResearchBudget
from scientist.sources import BiomedicalSources, IdentityResolutionError


def response(status, payload):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    result.url = "https://example.org/fixture"
    return result


def http_with_responses(tmp_path, monkeypatch, *responses, retries=2, max_requests=10):
    http = HTTPClient(tmp_path / "cache", retries=retries)
    ledger = RunLedger(ResearchBudget(max_http_requests=max_requests), time.monotonic() + 30)
    http.deadline = ledger.deadline
    http.before_request, http.on_cache_hit = ledger.request, ledger.cache
    calls = []
    replies = iter(responses)

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        item = next(replies)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(http.session, "request", request)
    monkeypatch.setattr("scientist.http.time.sleep", lambda seconds: None)
    return http, ledger, calls


def test_actual_attempts_retries_then_cache_are_distinct(tmp_path, monkeypatch):
    http, ledger, calls = http_with_responses(
        tmp_path, monkeypatch, response(500, {}), response(200, {"ok": True}))
    assert http.get_json("https://example.org/fixture", {"limit": 1}) == {"ok": True}
    assert http.get_json("https://example.org/fixture", {"limit": 1}) == {"ok": True}
    assert http.request_count == ledger.counts["http_attempts"] == len(calls) == 2
    assert http.cache_hits == ledger.counts["cache_hits"] == 1
    assert [e["outcome"] for e in http.request_events] == ["http_error", "ok", "cache_hit"]
    assert [e["attempt"] for e in http.request_events] == [1, 2, 0]
    assert [e["retry_index"] for e in http.request_events] == [0, 1, 0]
    assert [e["http_status"] for e in http.request_events] == [500, 200, None]
    assert len({e["cache_key"] for e in http.request_events}) == 1
    assert all(options["allow_redirects"] is False for _, options in calls)


@pytest.mark.parametrize("failure", [requests.ConnectionError("private diagnostic"),
                                    requests.Timeout("private diagnostic")])
def test_network_exception_attempts_are_charged_and_diagnostics_absent(
        tmp_path, monkeypatch, failure):
    http, ledger, calls = http_with_responses(tmp_path, monkeypatch, failure, failure, retries=1)
    with pytest.raises(DataSourceError):
        http.get_json("https://example.org/fixture")
    assert http.request_count == ledger.counts["http_attempts"] == len(calls) == 2
    assert [e["outcome"] for e in http.request_events] == ["network_error", "network_error"]
    assert "private diagnostic" not in json.dumps(http.request_events)


def test_budget_blocked_retry_has_no_extra_network_attempt(tmp_path, monkeypatch):
    http, ledger, calls = http_with_responses(tmp_path, monkeypatch, response(503, {}),
                                            max_requests=1)
    with pytest.raises(BudgetExceeded):
        http.get_json("https://example.org/fixture")
    assert http.request_count == ledger.counts["http_attempts"] == len(calls) == 1
    assert [e["outcome"] for e in http.request_events] == ["http_error", "blocked_budget"]


def test_cache_works_at_request_cap_but_is_blocked_after_deadline(tmp_path, monkeypatch):
    http, ledger, calls = http_with_responses(tmp_path, monkeypatch, response(200, {"ok": True}),
                                            max_requests=1)
    url = "https://example.org/fixture"
    assert http.get_json(url) == {"ok": True}
    assert http.get_json(url) == {"ok": True}
    http.deadline = ledger.deadline = time.monotonic() - 1
    with pytest.raises(TimeoutError):
        http.get_json(url)
    assert http.cache_hits == ledger.counts["cache_hits"] == 1
    assert len(calls) == 1
    assert http.request_events[-1]["outcome"] == "blocked_budget"
    assert not http.request_events[-1]["cache_hit"]


def test_cache_callback_also_checks_ledger_deadline(tmp_path, monkeypatch):
    http, ledger, calls = http_with_responses(tmp_path, monkeypatch, response(200, {"ok": True}))
    assert http.get_json("https://example.org/fixture") == {"ok": True}
    ledger.deadline = time.monotonic() - 1
    with pytest.raises(BudgetExceeded):
        http.get_json("https://example.org/fixture")
    assert len(calls) == 1 and ledger.counts["cache_hits"] == 0
    assert http.request_events[-1]["outcome"] == "blocked_budget"


def test_retry_sleep_and_request_timeout_are_bounded_by_deadline(tmp_path, monkeypatch):
    http, _, calls = http_with_responses(tmp_path, monkeypatch, response(500, {}))
    now, waits = [100.0], []
    monkeypatch.setattr("scientist.http.time.monotonic", lambda: now[0])
    http.deadline = 100.25
    http.before_request = None

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("scientist.http.time.sleep", sleep)
    with pytest.raises(TimeoutError):
        http.get_json("https://example.org/fixture")
    assert waits == [0.25]
    assert calls[0][1]["timeout"] == 0.25
    assert len(calls) == http.request_count == 1
    assert http.request_events[-1]["stop_reason"] == "deadline"


def test_late_response_is_charged_but_not_cached_as_success(tmp_path, monkeypatch):
    http, _, _ = http_with_responses(tmp_path, monkeypatch)
    now = [100.0]
    http.before_request = None
    http.deadline = 100.5
    monkeypatch.setattr("scientist.http.time.monotonic", lambda: now[0])

    def request(*args, **kwargs):
        now[0] = 101.0
        return response(200, {"late": True})

    monkeypatch.setattr(http.session, "request", request)
    with pytest.raises(TimeoutError):
        http.get_json("https://example.org/fixture")
    assert http.request_count == 1 and not list(http.cache_dir.glob("*.response"))
    assert http.request_events[-1]["outcome"] == "deadline_exceeded"


def test_transport_empty_response_is_not_an_empty_database_collection(tmp_path, monkeypatch):
    empty = response(200, {})
    empty._content = b""
    http, ledger, calls = http_with_responses(tmp_path, monkeypatch, empty)
    with pytest.raises(DataSourceError, match="Empty response"):
        http.get_json("https://example.org/fixture")
    assert http.request_count == ledger.counts["http_attempts"] == len(calls) == 1
    assert http.request_events[-1]["outcome"] == "empty_response"


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 403, 404])
def test_redirects_and_permanent_http_errors_are_not_retried(tmp_path, monkeypatch, status):
    http, ledger, calls = http_with_responses(tmp_path, monkeypatch, response(status, {}))
    with pytest.raises(DataSourceError):
        http.get_json("https://example.org/fixture")
    assert len(calls) == http.request_count == ledger.counts["http_attempts"] == 1
    assert http.request_events[-1]["http_status"] == status
    assert http.request_events[-1]["outcome"] == (
        "redirect_rejected" if status < 400 else "http_error")


def test_public_attempt_trace_excludes_headers_post_body_and_url_secrets(tmp_path, monkeypatch):
    http, _, _ = http_with_responses(tmp_path, monkeypatch,
                                   response(200, {}), response(200, {"ok": True}))
    http.post_json("https://user:private-password@example.org/fixture?key=private-key#private-fragment",
                   {"query": "private-prompt", "api_key": "private-key"})
    http.get_json("https://example.org/fixture", {
        "limit": 1, "query": "bounded query", "api_key": "private-key",
        "object": {"prompt": "private-prompt"}}, headers={"Authorization": "private-header"})
    serialized = json.dumps(http.request_events)
    assert "private-" not in serialized
    assert http.request_events[0]["endpoint"] == "https://example.org/fixture"
    assert http.request_events[0]["parameters"] == {}
    assert http.request_events[1]["parameters"] == {"limit": 1, "query": "bounded query"}
    assert all(len(e["cache_key"]) == 64 for e in http.request_events)


def protein(accession, *, gene="FIXTURE_GENE", aliases=(), species="Homo sapiens", reviewed=True):
    return {"primaryAccession": accession, "entryType": (
        "UniProtKB reviewed (Swiss-Prot)" if reviewed else "UniProtKB unreviewed (TrEMBL)"),
        "genes": [{"geneName": {"value": gene}, "synonyms": [{"value": v} for v in aliases]}],
        "organism": {"scientificName": species}}


def connector(tmp_path, monkeypatch, payload, status=200):
    http, ledger, calls = http_with_responses(tmp_path, monkeypatch, response(status, payload))
    return BiomedicalSources(http), ledger, calls


def test_unique_exact_gene_and_synonym_resolve_without_changing_tuple_interface(tmp_path, monkeypatch):
    sources, ledger, calls = connector(tmp_path, monkeypatch,
                                      {"results": [protein("QFIXTURE", gene="PRIMARY", aliases=("ALIAS",))]})
    accession, source = sources.resolve_target("ALIAS")
    assert accession == "QFIXTURE" and source.source_id == "uniprot:QFIXTURE"
    assert ledger.counts["http_attempts"] == len(calls) == 1
    assert source.metadata["genes"][0]["geneName"]["value"] == "PRIMARY"


def test_reviewed_entry_does_not_hide_other_exact_identity(tmp_path, monkeypatch):
    sources, _, _ = connector(tmp_path, monkeypatch, {"results": [protein("QFIRST"),
                                                               protein("QSECOND", reviewed=False)]})
    with pytest.raises(IdentityResolutionError) as error:
        sources.resolve_target("FIXTURE_GENE")
    assert error.value.status == error.value.identity_status == "ambiguous"
    assert [c["accession"] for c in error.value.candidates] == ["QFIRST", "QSECOND"]
    assert [c["reviewed"] for c in error.value.candidates] == [True, False]


@pytest.mark.parametrize("entries,status", [
    ([], "empty"),
    ([protein("QOTHER", gene="OTHER")], "empty"),
    ([protein("QMOUSE", species="Mus musculus")], "mismatch"),
    ([protein("QHUMAN"), protein("QMOUSE", species="Mus musculus")], "mismatch"),
    ([protein("QONLY")] + [protein(f"QOTHER{i}", gene="OTHER") for i in range(4)], "truncated"),
])
def test_protein_unresolved_empty_species_or_hidden_page_does_not_select(
        tmp_path, monkeypatch, entries, status):
    sources, _, _ = connector(tmp_path, monkeypatch, {"results": entries})
    with pytest.raises(IdentityResolutionError) as error:
        sources.resolve_target("FIXTURE_GENE")
    assert error.value.status == status
    assert len(error.value.candidates) <= 10


def test_invalid_gene_is_not_silently_rewritten_into_a_valid_query(tmp_path, monkeypatch):
    sources, ledger, calls = connector(tmp_path, monkeypatch, {})
    with pytest.raises(ValueError):
        sources.resolve_target("FIXTURE/Gene")
    assert not calls and ledger.counts["http_attempts"] == 0


@pytest.mark.parametrize("payload", [{}, {"error": "unavailable"}, {"results": None},
                                    {"results": [{"genes": None}]}])
def test_malformed_protein_response_is_not_no_match(tmp_path, monkeypatch, payload):
    sources, _, _ = connector(tmp_path, monkeypatch, payload)
    with pytest.raises(DataSourceError) as error:
        sources.resolve_target("FIXTURE_GENE")
    assert not isinstance(error.value, IdentityResolutionError)


def molecule(cid, smiles="CCO"):
    return {"CID": cid, "IsomericSMILES": smiles,
            "InChIKey": Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))}


def test_unique_molecule_structure_and_cid_resolve(tmp_path, monkeypatch):
    row = molecule(702)
    sources, _, _ = connector(tmp_path, monkeypatch, {"PropertyTable": {"Properties": [row]}})
    identity, source = sources.resolve_molecule("fixture ethanol")
    assert identity["pubchem_cid"] == "702" and identity["inchikey"] == row["InChIKey"]
    assert source.source_id == "pubchem:702"
    assert "activity" not in identity and "production" not in identity


def test_molecule_same_name_with_salt_form_is_ambiguous(tmp_path, monkeypatch):
    rows = [molecule(1, "CN"), molecule(2, "C[NH3+].[Cl-]")]
    rows[0]["raw_prompt"] = "private-prompt"
    sources, _, _ = connector(tmp_path, monkeypatch, {"PropertyTable": {"Properties": rows}})
    with pytest.raises(IdentityResolutionError) as error:
        sources.resolve_molecule("same name")
    assert error.value.status == "ambiguous"
    assert [c["cid"] for c in error.value.candidates] == ["1", "2"]
    assert "private-prompt" not in json.dumps(error.value.candidates)


@pytest.mark.parametrize("rows,status", [
    ([], "empty"),
    ([{"CID": 1, "IsomericSMILES": "", "InChIKey": ""}], "mismatch"),
    ([{"CID": 1, "IsomericSMILES": "CCO", "InChIKey": "incorrect"}], "mismatch"),
    ([molecule(0)], "mismatch"),
    ([molecule(i + 1) for i in range(11)], "truncated"),
])
def test_molecule_empty_invalid_inconsistent_or_truncated_does_not_select(
        tmp_path, monkeypatch, rows, status):
    sources, _, _ = connector(tmp_path, monkeypatch, {"PropertyTable": {"Properties": rows}})
    with pytest.raises(IdentityResolutionError) as error:
        sources.resolve_molecule("fixture molecule")
    assert error.value.status == status
    assert len(error.value.candidates) <= 10


@pytest.mark.parametrize("payload,is_empty", [
    ({"Fault": {"Code": "PUGREST.NotFound", "Message": "No CID found"}}, True),
    ({"Fault": {"Code": "PUGREST.ServerBusy"}}, False),
    ({"error": "Not found"}, False),
])
def test_only_specific_pubchem_not_found_is_empty(tmp_path, monkeypatch, payload, is_empty):
    sources, ledger, calls = connector(tmp_path, monkeypatch, payload, status=404)
    with pytest.raises(DataSourceError) as error:
        sources.resolve_molecule("fixture molecule")
    assert isinstance(error.value, IdentityResolutionError) is is_empty
    if is_empty:
        assert error.value.status == "empty" and error.value.candidates == []
    assert ledger.counts["http_attempts"] == len(calls) == 1


def test_molecule_service_failure_remains_unavailable_and_consumes_retries(tmp_path, monkeypatch):
    http, ledger, calls = http_with_responses(tmp_path, monkeypatch,
                                            response(500, {}), response(500, {}), retries=1)
    with pytest.raises(DataSourceError) as error:
        BiomedicalSources(http).resolve_molecule("fixture molecule")
    assert not isinstance(error.value, IdentityResolutionError)
    assert ledger.counts["http_attempts"] == len(calls) == 2


@pytest.mark.parametrize("payload", [{}, {"Fault": {"Code": "PUGREST.NotFound"}},
                                    {"PropertyTable": {}},
                                    {"PropertyTable": {"Properties": None}}])
def test_malformed_molecule_response_is_not_no_match(tmp_path, monkeypatch, payload):
    sources, _, _ = connector(tmp_path, monkeypatch, payload)
    with pytest.raises(DataSourceError) as error:
        sources.resolve_molecule("fixture molecule")
    assert not isinstance(error.value, IdentityResolutionError)

