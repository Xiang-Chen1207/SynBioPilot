"""SYNTHETIC offline core-identity regressions; no PubChem/network research."""
import copy
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from test_budget_scheduling import setup_engine
from test_candidate_identity_sync import candidate, resolved

from scientist.evidence import decide_candidates
from scientist.http import HTTPClient
from scientist.identity import CandidateIdentitySync
from scientist.identity_prepare import prepare_core_identities, verified_background_identity
from scientist.models import ExecutionStatus
from scientist.sources import BiomedicalSources
from scientist.tool_plans import ToolScheduler
from scientist.tools import ToolRegistry, ToolResult


def setup_core(tmp_path, monkeypatch):
    engine, clock, llm_calls = setup_engine(tmp_path, monkeypatch)
    engine.run.brief.existing_candidates = ["Synthetic lactate (SL)"]
    engine.run.candidates = [candidate()]
    engine.sources.http = SimpleNamespace(deadline=engine.ledger.deadline,
        before_request=engine.ledger.request, request_count=0, cache_hits=0)
    engine.identity_sync = CandidateIdentitySync(engine.run, engine.state)
    engine.scheduler = ToolScheduler(engine.planned_tool, engine.state.tool_plans, engine.persist,
        bind_candidate=engine.identity_sync.bind, apply_identity=engine.identity_sync.apply)
    queries = []
    def execute(request):
        engine.sources.http.before_request()
        engine.sources.http.request_count += 1
        clock.now += 2
        queries.append(request.arguments["name"])
        return resolved(request.arguments["name"])
    engine.registry = SimpleNamespace(execute=execute)
    return engine, clock, llm_calls, queries


def background_row(*, name="Synthetic lactate", smiles="C[C@@H](O)C(=O)[O-].[Na+]", cid="12345"):
    observation = resolved(name, smiles=smiles, cid=cid)
    values = observation.records[0]
    source = observation.sources[0].model_copy(update={
        "retrieved_at": "2026-10-14T00:00:00+00:00",
        "url": "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/" + quote(name) + "/property/IsomericSMILES,InChIKey/JSON",
        "text": json.dumps({"PropertyTable": {"Properties": [{"CID": int(cid),
            "SMILES": values["smiles"], "InChIKey": values["inchikey"]}]}})})
    return {"canonical_name": name, "aliases": [], "smiles": values["smiles"],
        "inchikey": values["inchikey"], "pubchem_cid": cid,
        "source": source.model_dump(), "source_sha256": hashlib.sha256(source.text.encode()).hexdigest()}


def test_required_canonical_identity_runs_with_insufficient_optional_input_reserve(tmp_path, monkeypatch):
    engine, clock, llm_calls, queries = setup_core(tmp_path, monkeypatch)
    engine.run.candidates = []  # Actual trigger: initial extraction omitted the specified candidate.
    engine.ledger.counts["input_tokens_reserved"] = 175000
    engine.critical_reserve = lambda: {"bytes": 130000, "calls": 2, "output_bytes": 24000, "seconds": 125}
    before = dict(engine.ledger.counts)
    prepare_core_identities(engine)
    assert queries == ["Synthetic lactate"]  # Explicit full name, not display label or guessed spelling.
    entity = engine.run.candidates[0]
    assert entity.name == "Synthetic lactate (SL)" and entity.identity_status == "resolved"
    assert entity.routes == ["user_supplied"] and entity.decision == "hold"
    assert entity.role == "unknown" and not entity.evidence_ids and not entity.gate
    assert engine.ledger.counts["input_tokens_reserved"] == before["input_tokens_reserved"]
    assert engine.ledger.counts["llm_calls"] == before["llm_calls"]
    assert engine.ledger.counts["tool_calls"] == engine.ledger.counts["http_attempts"] == 1
    assert engine.ledger.deadline-clock.now > 125
    for role in ("reflection", "meta_review"):
        engine.invoke(role, "SYNTHETIC dispatch eligibility", "system", "{}", {"type": "object"})
    assert len(llm_calls) == 2
    decide_candidates(engine.run.candidates, engine.run.evidence)
    assert entity.decision == "hold"  # Identity alone never supplies clinical evidence.
    before_repeat = engine.run.model_dump()
    prepare_core_identities(engine)
    assert engine.run.model_dump() == before_repeat and len(queries) == 1


def test_failed_exact_query_tries_only_declared_fallback(tmp_path, monkeypatch):
    engine, _, _, queries = setup_core(tmp_path, monkeypatch)
    base = engine.registry.execute
    def execute(request):
        result = base(request)
        if request.arguments["name"] == "Synthetic lactate":
            return ToolResult(tool_name="pubchem", provider="pubchem", parameters=request.arguments,
                retrieved_at="SYNTHETIC", status="empty", identity={"status": "empty"})
        return result
    engine.registry.execute = execute
    prepare_core_identities(engine)
    assert queries == ["Synthetic lactate", "Synthetic lactate (SL)"]
    assert engine.run.candidates[0].identity_status == "resolved"
    assert engine.state.tool_plans[0]["identity_application"]["status"] == "not_resolved"
    assert engine.state.tool_plans[1]["identity_application"]["status"] == "applied"


def test_ambiguous_provider_identity_stops_alias_fishing(tmp_path, monkeypatch):
    engine, _, _, queries = setup_core(tmp_path, monkeypatch)
    base = engine.registry.execute
    def execute(request):
        base(request)
        return ToolResult(tool_name="pubchem", provider="pubchem", parameters=request.arguments,
            retrieved_at="SYNTHETIC", status="unavailable", identity={"status": "ambiguous"})
    engine.registry.execute = execute
    prepare_core_identities(engine)
    assert queries == ["Synthetic lactate"]
    assert engine.run.candidates[0].identity_status == "needs_review"
    assert not engine.run.candidates[0].smiles


def test_all_specified_candidates_survive_bounded_four_query_share(tmp_path, monkeypatch):
    engine, _, _, queries = setup_core(tmp_path, monkeypatch)
    engine.run.brief.existing_candidates = ["SYNTHETIC molecule " + str(i) for i in range(8)]
    engine.run.candidates = []
    base = engine.registry.execute
    def execute(request):
        base(request)
        return ToolResult(tool_name="pubchem", provider="pubchem", parameters=request.arguments,
            retrieved_at="SYNTHETIC", status="empty", identity={"status": "empty"})
    engine.registry.execute = execute
    prepare_core_identities(engine)
    assert len(engine.run.candidates) == 8 and len(queries) == 4
    assert engine.ledger.counts["tool_calls"] == engine.ledger.counts["http_attempts"] == 4
    assert all(c.decision == "hold" for c in engine.run.candidates)
    assert engine.run.candidates[-1].identity_history[-1]["status"] == "core_identity_allowance"


def test_background_reuse_verifies_structure_provenance_and_preserves_science(tmp_path, monkeypatch):
    engine, _, _, queries = setup_core(tmp_path, monkeypatch)
    row = background_row()
    engine.background_package = SimpleNamespace(receipt={"package_id": "SYNTHETIC-package",
        "version": "1", "manifest_sha256": "b" * 64})
    old_card = copy.deepcopy(engine.state.hypotheses[0].model_dump())
    old_science = engine.run.candidates[0].model_dump(include={"role", "role_status", "decision", "gate", "admet"})
    prepare_core_identities(engine, [row])
    entity = engine.run.candidates[0]
    assert not queries and engine.ledger.counts["http_attempts"] == engine.ledger.counts["tool_calls"] == 0
    assert entity.identity_status == "resolved" and entity.identity_revision == 1
    assert entity.model_dump(include=set(old_science)) == old_science
    assert engine.state.hypotheses[0].model_dump() == old_card
    observation = next(o for o in entity.identity_history if o.get("acquisition") == "background_reuse" and o.get("call_id"))
    assert observation["source_sha256"] == row["source_sha256"]
    assert observation["source_retrieved_at"] == "2026-10-14T00:00:00+00:00"
    assert observation["package_id"] == "SYNTHETIC-package"
    assert observation["package_version"] == "1" and observation["package_sha256"] == "b" * 64
    assert observation["call_id"].startswith("background_identity:") and not engine.state.tool_calls
    before = engine.run.model_dump()
    prepare_core_identities(engine, [row])
    assert engine.run.model_dump() == before


@pytest.mark.parametrize("change", ["stereochemistry", "salt", "cid", "hash", "alias", "date", "url"])
def test_background_row_rejects_unverified_forms_and_aliases(change):
    row = background_row()
    if change == "stereochemistry":
        row["smiles"] = row["smiles"].replace("@@", "@")
    elif change == "salt":
        row["smiles"] = row["smiles"].replace(".[Na+]", "")
    elif change == "cid":
        row["pubchem_cid"] = "99999"
    elif change == "hash":
        row["source_sha256"] = "f" * 64
    elif change == "alias":
        row["aliases"] = ["SYNTHETIC guessed abbreviation"]
    elif change == "date":
        row["source"]["retrieved_at"] = "unknown"
    else:
        row["source"]["url"] = row["source"]["url"].replace("pubchem.ncbi.nlm.nih.gov", "example.invalid")
    with pytest.raises((ValueError, KeyError)):
        verified_background_identity(row)


def test_conflicting_background_forms_and_duplicate_candidate_aliases_do_not_guess(tmp_path, monkeypatch):
    engine, _, _, queries = setup_core(tmp_path, monkeypatch)
    prepare_core_identities(engine, [background_row(), background_row(smiles="CCO", cid="99999")])
    assert not queries and engine.run.candidates[0].identity_history[-1]["status"] == "ambiguous_background_identity"
    engine2, _, _, queries2 = setup_core(tmp_path / "duplicate", monkeypatch)
    engine2.run.candidates.append(candidate(name="Synthetic lactate", identifier="other_entity"))
    prepare_core_identities(engine2)
    assert not queries2 and all(c.identity_status != "resolved" for c in engine2.run.candidates)


def test_core_phase_protects_review_time_and_keeps_terminal_history_read_only(tmp_path, monkeypatch):
    engine, clock, _, queries = setup_core(tmp_path, monkeypatch)
    engine.ledger.deadline = clock.now + 80
    engine.critical_reserve = lambda: {"seconds": 125}
    original_http_callback = engine.sources.http.before_request
    original_http_deadline = engine.sources.http.deadline
    prepare_core_identities(engine)
    assert not queries and engine.run.candidates[0].identity_history[-1]["status"] == "core_identity_allowance"
    assert engine.sources.http.before_request == original_http_callback
    assert engine.sources.http.deadline == original_http_deadline
    engine.run.status = ExecutionStatus.PARTIAL
    path = tmp_path / "SYNTHETIC_old_run.json"
    path.write_text(engine.run.model_dump_json())
    old_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    old = engine.run.model_dump()
    prepare_core_identities(engine, [background_row()])
    assert engine.run.model_dump() == old and hashlib.sha256(path.read_bytes()).hexdigest() == old_hash


def test_existing_identity_conflict_never_overwrites_or_reclassifies(tmp_path, monkeypatch):
    engine, _, _, queries = setup_core(tmp_path, monkeypatch)
    entity = engine.run.candidates[0]
    entity.smiles = "CCO"
    prepare_core_identities(engine, [background_row()])
    assert not queries and entity.smiles == "CCO" and entity.identity_status == "needs_review"
    assert entity.identity_history[-1]["status"] == "conflict" and entity.decision == "hold"


def test_verified_provider_synonym_has_own_cid_document_and_hash(tmp_path, monkeypatch):
    engine, _, _, queries = setup_core(tmp_path, monkeypatch)
    row = background_row(name="SYNTHETIC official full name")
    row["aliases"] = ["Synthetic lactate"]
    row["alias_source"] = {**row["source"], "source_id": "pubchem:12345:synonyms",
        "url": "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/12345/synonyms/JSON",
        "text": json.dumps({"InformationList": {"Information": [{"CID": 12345,
            "Synonym": ["Synthetic lactate", "SYNTHETIC official full name"]}]}})}
    row["alias_source_sha256"] = hashlib.sha256(row["alias_source"]["text"].encode()).hexdigest()
    prepare_core_identities(engine, [row])
    assert not queries and engine.run.candidates[0].identity_status == "resolved"
    assert "SYNTHETIC official full name" in engine.run.candidates[0].aliases
    assert any(s.source_id == "pubchem:12345:synonyms" for s in engine.run.sources)
    assert "pubchem:12345:synonyms" in engine.run.candidates[0].identity_source_ids
    assert engine.run.candidates[0].identity_history[0]["alias_source_sha256"] == row["alias_source_sha256"]
    row["alias_source"]["text"] = row["alias_source"]["text"].replace('12345', '99999')
    row["alias_source_sha256"] = hashlib.sha256(row["alias_source"]["text"].encode()).hexdigest()
    with pytest.raises(ValueError, match="ambiguous"):
        verified_background_identity(row)


@pytest.mark.parametrize("failure", ["retries", "slow_response"])
def test_actual_http_core_share_cannot_spend_reserved_review_resources(tmp_path, monkeypatch, failure):
    import requests
    engine, clock, llm_calls, _ = setup_core(tmp_path, monkeypatch)
    client = HTTPClient(tmp_path / "SYNTHETIC_http", retries=10)
    client.deadline, client.before_request = engine.ledger.deadline, engine.ledger.request
    engine.sources.http = client
    source = BiomedicalSources(client)
    engine.registry = ToolRegistry(source)
    network = []
    monkeypatch.setattr("scientist.http.time.sleep", lambda seconds: setattr(clock, "now", clock.now + seconds))
    def request(method, url, **options):
        network.append((method, url, options["timeout"]))
        response = requests.Response()
        response.url = url
        if failure == "retries":
            response.status_code = 500
            response._content = b'{"SYNTHETIC":"offline transient failure"}'
        else:
            clock.now += 61  # Provider violates its requested timeout; no timing guarantee claimed.
            response.status_code = 200
            response._content = background_row()["source"]["text"].encode()
        return response
    monkeypatch.setattr(client.session, "request", request)
    prepare_core_identities(engine)
    assert len(network) == (4 if failure == "retries" else 1)
    assert engine.ledger.counts["http_attempts"] == len(network)
    assert engine.ledger.counts["tool_calls"] == 1
    assert all(t["status"] != "budget_exhausted" for t in engine.state.tool_calls)
    if failure == "slow_response":
        assert engine.state.tool_calls[0]["stop_reason"] == "core_identity_time_allowance"
        assert all(e.operation_status != "budget_exhausted" for e in engine.state.events)
    assert not engine.run.candidates[0].smiles and engine.run.candidates[0].decision == "hold"
    assert client.deadline == engine.ledger.deadline and client.before_request == engine.ledger.request
    engine.invoke("reflection", "SYNTHETIC remaining review", "system", "{}", {"type": "object"})
    assert len(llm_calls) == 1


def test_background_identity_has_reuse_receipt_and_preserves_specified_origin_with_verified_alias(tmp_path, monkeypatch):
    from scientist.research_flow import ResearchFlow
    engine, _, _, queries = setup_core(tmp_path, monkeypatch)
    engine.flow = ResearchFlow(engine)
    engine.background_package = SimpleNamespace(receipt={'package_id':'synthetic_v1','version':'1.0.0','manifest_sha256':'a'*64})
    prepare_core_identities(engine, [background_row()])
    assert not queries
    q = engine.flow.data.queries[-1]
    assert q.status == 'reused' and q.purpose == 'background_identity' and q.request_count == 0
    assert all(s.origin == 'library' for g in engine.flow.data.publications for s in g.snapshots)
    h = engine.state.hypotheses[0].model_copy(update={'molecule_names':['Synthetic lactate'], 'origin':'synthesis'})
    normalized = engine.normalize_card(h)
    assert normalized.origin == 'user_supplied' and h.origin == 'synthesis'


def test_identity_context_carries_source_metadata_without_synonym_blob(tmp_path, monkeypatch):
    from scientist.context import source_context
    _engine, _, _, _ = setup_core(tmp_path, monkeypatch)
    row = background_row()
    source = __import__('scientist.models',fromlist=['SourceRecord']).SourceRecord.model_validate(row['source'])
    source.text += 'SYNTHETIC_VERBOSE_SYNONYM_BLOB' * 1000
    payload = source_context(source)
    assert payload['identity_only'] is True and payload['source_id'] == source.source_id
    assert 'SYNTHETIC_VERBOSE_SYNONYM_BLOB' not in json.dumps(payload)

