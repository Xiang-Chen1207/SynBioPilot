"""SYNTHETIC parent FIX1 reproduction; no real HTTP, models or historical writes."""
import csv
import hashlib
import io
import json

import pytest
from test_candidate_identity_sync import candidate
from test_core_identity_preparation import background_row, setup_core

from scientist.evidence import decide_candidates
from scientist.exports import table_exports
from scientist.identity import declared_candidate_names
from scientist.identity_prepare import ensure_specified_candidates, prepare_core_identities
from scientist.models import ExecutionStatus, ResearchRun
from scientist.tool_public import public_candidate_identity
from scientist.tools import ToolResult


def abbreviation_only(tmp_path, monkeypatch):
    engine, clock, model_calls, queries = setup_core(tmp_path, monkeypatch)
    entity = engine.run.candidates[0]
    entity.name, entity.aliases = "SL", []
    base = engine.registry.execute
    def execute(request):
        result = base(request)
        if request.arguments["name"] == "SL":
            return ToolResult(tool_name="pubchem", provider="pubchem", parameters=request.arguments,
                status="empty", retrieved_at="SYNTHETIC", identity={"status": "empty"})
        return result
    engine.registry.execute = execute
    return engine, clock, model_calls, queries


def test_abbreviation_only_extraction_queries_unique_declared_full_name_first(tmp_path, monkeypatch):
    engine, _, model_calls, queries = abbreviation_only(tmp_path, monkeypatch)
    entity = engine.run.candidates[0]
    science = entity.model_dump(include={"role", "role_status", "gate", "admet", "decision"})
    prepare_core_identities(engine)
    assert queries == ["Synthetic lactate"]
    assert entity.identity_status == "resolved" and entity.name == "SL"
    assert entity.aliases == []  # User input was not written into official/provider aliases.
    assert len(entity.user_name_declarations) == 1
    declaration = entity.user_name_declarations[0].model_dump()
    assert declaration == {"label": "Synthetic lactate (SL)", "full_name": "Synthetic lactate",
        "abbreviation": "SL", "source": "brief.existing_candidates", "source_index": 0,
        "run_id": engine.run.run_id}
    assert declared_candidate_names(entity) == ["Synthetic lactate", "Synthetic lactate (SL)", "SL"]
    assert entity.model_dump(include=set(science)) == science
    decide_candidates(engine.run.candidates, engine.run.evidence)
    assert entity.decision == "hold" and not model_calls
    assert engine.ledger.counts["http_attempts"] == engine.ledger.counts["tool_calls"] == 1
    view = public_candidate_identity(entity.model_dump())
    assert view["user_name_declarations"] == [declaration]
    assert engine.state.entity_references[0]["user_name_declarations"] == [declaration]
    exported = next(csv.DictReader(io.StringIO(table_exports(engine.run)["candidates.csv"].decode("utf-8-sig"))))
    assert json.loads(exported["user_name_declarations"]) == [declaration]
    assert json.loads(exported["aliases"]) == []


def test_abbreviation_only_candidate_matches_full_name_only_verified_background(tmp_path, monkeypatch):
    engine, _, _, queries = abbreviation_only(tmp_path, monkeypatch)
    row = background_row()
    assert row["aliases"] == []
    prepare_core_identities(engine, [row])
    entity = engine.run.candidates[0]
    assert not queries and entity.identity_status == "resolved" and entity.decision == "hold"
    assert engine.ledger.counts["tool_calls"] == engine.ledger.counts["http_attempts"] == 0
    assert entity.user_name_declarations[0].source == "brief.existing_candidates"
    observation = next(o for o in entity.identity_history if o.get("call_id"))
    assert observation["acquisition"] == "background_reuse"
    assert observation["source_sha256"] == row["source_sha256"]
    assert entity.aliases == ["Synthetic lactate"]  # This name was separately verified by official data.


@pytest.mark.parametrize("reverse", [False, True])
def test_two_full_names_sharing_abbreviation_abstain_independent_of_order(tmp_path, monkeypatch, reverse):
    engine, _, _, queries = abbreviation_only(tmp_path, monkeypatch)
    names = ["Synthetic lactate (SL)", "Sodium lactate (SL)"]
    engine.run.brief.existing_candidates = list(reversed(names)) if reverse else names
    prepare_core_identities(engine, [background_row()])
    assert not queries and len(engine.run.candidates) == 1
    entity = engine.run.candidates[0]
    assert entity.user_name_declarations == [] and entity.aliases == []
    assert entity.identity_status == "needs_review" and entity.decision == "hold"
    assert entity.identity_history[-1]["status"] == "ambiguous_user_name_declaration"
    assert entity.routes == [] and not entity.smiles


@pytest.mark.parametrize("other_name", ["SL", "Synthetic lactate"])
def test_multiple_matching_candidates_do_not_receive_mass_attached_names(tmp_path, monkeypatch, other_name):
    engine, _, _, queries = abbreviation_only(tmp_path, monkeypatch)
    engine.run.candidates.append(candidate(name=other_name, identifier="another_synthetic_entity"))
    prepare_core_identities(engine)
    assert not queries
    assert all(not c.user_name_declarations and not c.aliases and not c.smiles for c in engine.run.candidates)
    assert all(c.identity_history[-1]["status"] == "ambiguous_user_name_declaration" for c in engine.run.candidates)


def test_different_explicit_candidate_full_name_with_same_abbreviation_is_not_rebound(tmp_path, monkeypatch):
    engine, _, _, queries = abbreviation_only(tmp_path, monkeypatch)
    engine.run.candidates[0].name = "Sodium lactate (SL)"
    prepare_core_identities(engine, [background_row()])
    assert not queries and not engine.run.candidates[0].user_name_declarations
    assert engine.run.candidates[0].identity_history[-1]["status"] == "ambiguous_user_name_declaration"


def test_duplicate_declaration_and_repeated_preparation_are_idempotent(tmp_path, monkeypatch):
    engine, _, _, queries = abbreviation_only(tmp_path, monkeypatch)
    engine.run.brief.existing_candidates *= 2
    prepare_core_identities(engine)
    before = engine.run.model_dump()
    prepare_core_identities(engine)
    assert engine.run.model_dump() == before
    assert queries == ["Synthetic lactate"] and len(engine.run.candidates[0].user_name_declarations) == 1


@pytest.mark.parametrize("qualifier", ["sodium", "R", "HCL"])
def test_form_and_stereochemistry_qualifiers_are_not_removed_to_link_abbreviation(tmp_path, monkeypatch, qualifier):
    engine, _, _, queries = abbreviation_only(tmp_path, monkeypatch)
    label = f"Synthetic lactate ({qualifier})"
    engine.run.brief.existing_candidates = [label]
    ensure_specified_candidates(engine.run)
    assert not queries and engine.run.candidates[0].user_name_declarations == []
    new = engine.run.candidates[1]
    assert new.name == label and new.user_name_declarations[0].full_name == label
    assert new.user_name_declarations[0].abbreviation == "" and new.aliases == []


def test_declaration_is_retained_when_core_budget_prevents_query_without_spending_review(tmp_path, monkeypatch):
    engine, clock, models, queries = abbreviation_only(tmp_path, monkeypatch)
    engine.run.brief.budget.max_tool_calls = 0
    before = dict(engine.ledger.counts)
    prepare_core_identities(engine)
    entity = engine.run.candidates[0]
    assert not queries and engine.ledger.counts == before
    assert entity.user_name_declarations[0].full_name == "Synthetic lactate"
    assert entity.identity_history[-1]["status"] == "core_identity_allowance"
    assert entity.identity_status == "needs_review" and entity.decision == "hold"
    assert engine.ledger.deadline - clock.now > 125
    engine.invoke("reflection", "SYNTHETIC remaining review", "system", "{}", {"type": "object"})
    assert len(models) == 1


def test_terminal_legacy_json_without_declarations_remains_read_only(tmp_path, monkeypatch):
    engine, _, _, queries = abbreviation_only(tmp_path, monkeypatch)
    engine.run.status = ExecutionStatus.PARTIAL
    old = engine.run.model_dump(mode="json")
    old["candidates"][0].pop("user_name_declarations")
    path = tmp_path / "SYNTHETIC_old.json"
    path.write_text(json.dumps(old))
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    engine.run = ResearchRun.model_validate_json(path.read_text())
    before = engine.run.model_dump()
    prepare_core_identities(engine, [background_row()])
    assert engine.run.model_dump() == before and not queries
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum


def test_public_declaration_projection_drops_arbitrary_private_fields(tmp_path, monkeypatch):
    engine, _, _, _ = abbreviation_only(tmp_path, monkeypatch)
    prepare_core_identities(engine)
    row = engine.run.candidates[0].model_dump()
    row["user_name_declarations"][0]["raw_prompt"] = "PRIVATE_SENTINEL"
    view = public_candidate_identity(row)
    assert view["user_name_declarations"][0]["source"] == "brief.existing_candidates"
    assert "PRIVATE_SENTINEL" not in json.dumps(view)

