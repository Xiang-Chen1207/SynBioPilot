"""SYNTHETIC admission tests through synthesis, independent review and real connectors.

Transport and structured workers are transparent offline fixtures. No evidence
receipt is handcrafted to obtain tractability admission.
"""
import json

import pytest
from test_annotation_index import setup_normal_path, target_row
from test_tool_http_identity import response

from scientist.models import EvidenceRecord, SourceRecord
from scientist.presentation import result_document

INTERVENTION_QUOTE = "SYNTHETIC ALPHA inhibition reduced the measured cellular endpoint under the recorded conditions."
CONTEXT_QUOTE = "ALPHA expression correlated with disease severity. No intervention was tested."
COUNTER_QUOTE = "SYNTHETIC ALPHA inhibition did not improve the same cellular endpoint in a separate recorded condition."


class DirectionReviewWorker:
    """Two schema-bound offline responses; never accesses a model provider."""

    def __init__(self, *, polarity="supports", direction="inhibit", direction_supported=True,
                 include_review=True, ledger=None, duplicate_target_review=False,
                 duplicate_evidence_review=False, extra_review_id=False, companion_polarity=None):
        self.polarity, self.direction = polarity, direction
        self.direction_supported, self.include_review = direction_supported, include_review
        self.ledger, self.requests = ledger, []
        self.duplicate_target_review, self.duplicate_evidence_review = duplicate_target_review, duplicate_evidence_review
        self.extra_review_id, self.companion_polarity = extra_review_id, companion_polarity

    def complete(self, system, user, schema):
        if self.ledger:
            self.ledger.llm(system, user, json.dumps(schema, sort_keys=True))
        payload = json.loads(user)
        self.requests.append({"schema": schema["title"], "payload": payload})
        if schema["title"] == "SynthesisDraft":
            draft = {"evidence": [{"key": "intervention", "source_id": "synthetic:intervention",
                "claim": "SYNTHETIC ALPHA inhibition changes the measured cellular endpoint",
                "quote": INTERVENTION_QUOTE, "location": "SYNTHETIC Results paragraph 1",
                "polarity": self.polarity, "study_system": "synthetic human-derived cells",
                "condition": "synthetic cellular intervention", "endpoint": "synthetic cellular measurement",
                "limitations": ["SYNTHETIC offline fixture; not patient efficacy"]}],
                "targets": [{"gene_symbol": "ALPHA", "desired_direction": self.direction,
                    "evidence_keys": ["intervention"], "rationale": "SYNTHETIC direction test",
                    "related_cells": [], "pathways": [], "uncertainties": []}],
                "candidates": [], "unresolved_questions": []}
            if self.companion_polarity:
                draft["evidence"].append({**draft["evidence"][0], "key": "counter", "quote": COUNTER_QUOTE,
                    "claim": COUNTER_QUOTE, "polarity": self.companion_polarity})
                draft["targets"][0]["evidence_keys"].append("counter")
            return draft
        assert schema["title"] == "ReviewDraft"
        ids = [item["evidence_id"] for item in payload["evidence"]]
        reviewed = {"reviews": [{"evidence_id": eid, "verdict": "supported_by_source",
            "reason": "SYNTHETIC exact source claim reviewed; not clinical validation"} for eid in ids],
            "target_links": [{"gene_symbol": "ALPHA", "direction_supported": self.direction_supported,
                "evidence_ids": ids + (["ev_SYNTHETIC_unrelated"] if self.extra_review_id else []),
                "reason": "SYNTHETIC independent direction review"}] if self.include_review else [],
            "molecule_links": [], "candidate_roles": []}
        if self.duplicate_target_review:
            reviewed["target_links"] *= 2
        if self.duplicate_evidence_review:
            reviewed["reviews"] *= 2
        return reviewed


def synthesize_direction_review(engine, run, directory, *, ledger=None, polarity="supports", direction="inhibit",
                                direction_supported=True, include_review=True, **options):
    """Use production synthesis; rebind identity through the already verified exact cache."""
    source = SourceRecord(source_id="synthetic:intervention", provider="europe_pmc",
        title="SYNTHETIC ALPHA cellular intervention", url="https://europepmc.org/article/MED/1",
        retrieved_at="2026-10-15T00:00:00Z", text=INTERVENTION_QUOTE + (
            "\n" + COUNTER_QUOTE if options.get("companion_polarity") else ""),
        metadata={"synthetic": True, "not_a_real_publication": True})
    run.sources.append(source)
    worker = DirectionReviewWorker(polarity=polarity, direction=direction,
        direction_supported=direction_supported, include_review=include_review, ledger=ledger, **options)
    directory.mkdir(parents=True, exist_ok=True)
    engine._synthesize(run, worker, directory)
    target = run.targets[0]
    accession, identity_source = engine.sources.resolve_target(target.gene_symbol, target.species)
    target.uniprot_id = accession
    target.assessment["identity_source"] = identity_source.source_id
    run.sources = [s for s in run.sources if s.source_id != identity_source.source_id] + [identity_source]
    return target, worker


def valid_details():
    return {**target_row("ALPHA", "Q11111", 1)["target"],
            "tractability": [{"label": "SYNTHETIC annotation", "modality": "small molecule", "value": True}]}


def add_context_observation(run, target):
    source = SourceRecord(source_id="synthetic:context", provider="europe_pmc",
        title="SYNTHETIC human observation", url="https://europepmc.org/article/MED/2", retrieved_at="",
        text=CONTEXT_QUOTE, metadata={"synthetic": True})
    evidence = EvidenceRecord(evidence_id="ev_SYNTHETIC_context", source_id=source.source_id,
        claim=CONTEXT_QUOTE, quote=CONTEXT_QUOTE, location="SYNTHETIC observational results",
        polarity="context", validated_span=True, study_system="Homo sapiens observational study",
        condition="SYNTHETIC disease cohort", endpoint="expression correlation")
    run.sources.append(source)
    run.evidence.append(evidence)
    target.evidence_ids = [evidence.evidence_id]
    target.desired_direction = "unknown"


@pytest.mark.parametrize("fresh_assessment", [True, False])
def test_parent_context_counterexamples_keep_six_transport_attempts_and_zero_tool_delta(tmp_path, monkeypatch, fresh_assessment):
    engine, run, _, _, ledger, calls = setup_normal_path(tmp_path, monkeypatch, details=valid_details())
    target = run.targets[0]
    add_context_observation(run, target)
    if fresh_assessment:
        target.assessment = {"identity_source": target.assessment["identity_source"]}
    evidence_before = [e.model_dump() for e in run.evidence]
    before = dict(ledger.counts)
    engine._target_due_diligence(run, target, target.uniprot_id)
    assert len(calls) == ledger.counts["http_attempts"] == before["http_attempts"] == 6
    assert ledger.counts["tool_calls"] == before["tool_calls"] == 0
    assert target.assessment["open_targets_tractability"] == "not_requested_no_reviewed_intervention"
    assert "tractability" not in target.assessment
    assert target.desired_direction == "unknown" and target.assessment["druggability"] == "unknown"
    assert not any(s.source_id.startswith("ot:target:") for s in run.sources)
    assert [e.model_dump() for e in run.evidence] == evidence_before


def test_positive_admission_comes_from_actual_synthesis_direction_review_and_cache_reentry(tmp_path, monkeypatch):
    engine, run, _, _, ledger, calls = setup_normal_path(tmp_path, monkeypatch, details=valid_details())
    target, worker = synthesize_direction_review(engine, run, tmp_path / "synthesis", ledger=ledger)
    assert [r["schema"] for r in worker.requests] == ["SynthesisDraft", "ReviewDraft"]
    assert ledger.counts["llm_calls"] == 2  # Two transparent offline worker responses, no live model.
    review = target.assessment["direction_review"]
    assert review["status"] == "supported_by_existing_direction_review"
    assert review["run_id"] == run.run_id and review["gene_symbol"] == "ALPHA"
    assert review["species"] == target.species and review["direction"] == "inhibit"
    assert set(review["reviewed_evidence_ids"]).issubset(target.evidence_ids)
    assert len(calls) == 6
    engine._target_due_diligence(run, target, target.uniprot_id)
    assert len(calls) == 7 and target.assessment["open_targets_tractability"] == "retrieved"
    assert target.assessment["tractability"] == valid_details()["tractability"]
    assert target.assessment["druggability"] == "unknown"
    saved_source = next(s.model_dump() for s in run.sources if s.source_id == "ot:target:ENSG000001")
    before_cache = ledger.counts["cache_hits"]
    engine._target_due_diligence(run, target, target.uniprot_id)
    assert len(calls) == 7 and ledger.counts["cache_hits"] == before_cache + 1
    assert target.assessment["open_targets_tractability"] == "cache_reused"
    assert target.assessment["tractability_check"] == {
        "scope": "current_due_diligence_check", "status": "cache_reused", "http_attempts": 0,
        "cache_hits": 1, "source_id": "ot:target:ENSG000001",
        "admission_evidence_ids": review["reviewed_evidence_ids"]}
    assert result_document(run)["targets"][0]["assessment"]["open_targets_tractability"] == "cache_reused"
    assert next(s.model_dump() for s in run.sources if s.source_id == "ot:target:ENSG000001") == saved_source
    target.assessment.pop("direction_review")
    engine._target_due_diligence(run, target, target.uniprot_id)
    assert len(calls) == 7 and "tractability" not in target.assessment
    assert target.assessment["open_targets_tractability"] == "not_requested_no_reviewed_intervention"
    assert target.assessment["tractability_check"]["source_id"] == ""
    assert next(s.model_dump() for s in run.sources if s.source_id == "ot:target:ENSG000001") == saved_source


@pytest.mark.parametrize("options", [{"polarity": "mixed"}, {"polarity": "opposes"},
    {"polarity": "context"}, {"direction": "unknown"}, {"direction_supported": False}, {"include_review": False},
    {"duplicate_target_review": True}, {"duplicate_evidence_review": True}, {"extra_review_id": True}])
def test_non_supporting_or_unreviewed_direction_never_admits_optional_annotation(tmp_path, monkeypatch, options):
    engine, run, _, _, ledger, calls = setup_normal_path(tmp_path, monkeypatch, details=valid_details())
    target, _ = synthesize_direction_review(engine, run, tmp_path / "synthesis", ledger=ledger, **options)
    before = dict(ledger.counts)
    engine._target_due_diligence(run, target, target.uniprot_id)
    assert len(calls) == ledger.counts["http_attempts"] == 6
    assert ledger.counts["tool_calls"] == before["tool_calls"] == 0
    assert target.assessment["open_targets_tractability"] == "not_requested_no_reviewed_intervention"
    assert "tractability" not in target.assessment


@pytest.mark.parametrize("companion_polarity", ["mixed", "opposes"])
def test_reviewed_support_with_counterevidence_keeps_both_without_promoting_druggability(tmp_path, monkeypatch, companion_polarity):
    engine, run, _, _, ledger, calls = setup_normal_path(tmp_path, monkeypatch, details=valid_details())
    target, _ = synthesize_direction_review(engine, run, tmp_path / "synthesis", ledger=ledger,
                                           companion_polarity=companion_polarity)
    evidence_before = [e.model_dump() for e in run.evidence]
    reviewed_ids = target.assessment["direction_review"]["reviewed_evidence_ids"]
    assert len(run.evidence) == 2 and len(reviewed_ids) == 1
    assert next(e for e in run.evidence if e.evidence_id == reviewed_ids[0]).polarity == "supports"
    engine._target_due_diligence(run, target, target.uniprot_id)
    assert len(calls) == 7 and target.assessment["open_targets_tractability"] == "retrieved"
    assert target.assessment["druggability"] == "unknown"
    assert [e.model_dump() for e in run.evidence] == evidence_before
    assert {e.evidence_id for e in run.evidence} == set(target.evidence_ids)


@pytest.mark.parametrize("change", ["run", "species", "target_gene", "target_direction", "evidence_text", "evidence_polarity", "source_text", "missing_evidence"])
def test_direction_receipt_cannot_be_replayed_after_task_target_or_evidence_changes(tmp_path, monkeypatch, change):
    engine, run, _, _, ledger, calls = setup_normal_path(tmp_path, monkeypatch, details=valid_details())
    target, _ = synthesize_direction_review(engine, run, tmp_path / "synthesis", ledger=ledger)
    if change == "run": run.run_id = "r_OTHER_SYNTHETIC"
    elif change == "species": target.species = "Mus musculus"
    elif change == "target_gene": target.gene_symbol = "BETA"
    elif change == "target_direction": target.desired_direction = "activate"
    elif change == "evidence_text": run.evidence[0].claim = "Changed scientific claim"
    elif change == "evidence_polarity": run.evidence[0].polarity = "opposes"
    elif change == "source_text": next(s for s in run.sources if s.source_id == "synthetic:intervention").text = "Changed source text"
    else: run.evidence = []
    engine._target_due_diligence(run, target, target.uniprot_id)
    assert len(calls) == ledger.counts["http_attempts"] == 6
    assert "tractability" not in target.assessment
    assert target.assessment["open_targets_tractability"] in {
        "not_requested_no_reviewed_intervention", "not_requested_association_only", "identity_mismatch",
        "species_checked_identity_unavailable", "not_retrieved_or_identity_ambiguous"}


def test_admitted_query_rejects_changed_official_identity_without_retaining_tractability(tmp_path, monkeypatch):
    wrong = {**target_row("BETA", "Q22222", 2)["target"], "tractability": valid_details()["tractability"]}
    engine, run, _, _, ledger, calls = setup_normal_path(tmp_path, monkeypatch, details=wrong)
    target, _ = synthesize_direction_review(engine, run, tmp_path / "synthesis", ledger=ledger)
    target.assessment["tractability"] = [{"label": "SYNTHETIC stale annotation"}]
    target.assessment["open_targets_tractability"] = "retrieved"
    engine._target_due_diligence(run, target, target.uniprot_id)
    assert len(calls) == 7 and target.assessment["open_targets_tractability"] == "identity_mismatch"
    assert "tractability" not in target.assessment
    assert not any(s.source_id.startswith("ot:target:") for s in run.sources)


def test_admitted_budget_stop_and_failure_reentry_have_current_explicit_status(tmp_path, monkeypatch):
    engine, run, _, http, ledger, calls = setup_normal_path(tmp_path, monkeypatch, details=valid_details())
    target, _ = synthesize_direction_review(engine, run, tmp_path / "synthesis", ledger=ledger)
    ledger.limits.max_http_requests = 6  # This SYNTHETIC fixture has no remaining request allowance.
    engine._target_due_diligence(run, target, target.uniprot_id)
    assert len(calls) == 6 and target.assessment["open_targets_tractability"] == "budget_exhausted"
    assert "tractability" not in target.assessment
    ledger.limits.max_http_requests = 12
    engine._target_due_diligence(run, target, target.uniprot_id)
    assert len(calls) == 7 and target.assessment["open_targets_tractability"] == "retrieved"
    saved_source = next(s.model_dump() for s in run.sources if s.source_id == "ot:target:ENSG000001")
    # Invalidate only this temporary SYNTHETIC query cache to exercise a real
    # failed transport attempt after a previous successful result.
    query_key = http.request_events[-1]["cache_key"]
    (http.cache_dir / (query_key + ".response")).unlink()
    http.retries = 0
    def unavailable(*args, **kwargs):
        calls.append((args, kwargs))
        return response(503, {"SYNTHETIC": "temporarily unavailable"})
    monkeypatch.setattr(http.session, "request", unavailable)
    engine._target_due_diligence(run, target, target.uniprot_id)
    assert len(calls) == 8 and target.assessment["open_targets_tractability"] == "retrieval_failed"
    assert "tractability" not in target.assessment
    assert target.assessment["druggability"] == "unknown"
    assert target.assessment["tractability_check"]["source_id"] == ""
    assert next(s.model_dump() for s in run.sources if s.source_id == "ot:target:ENSG000001") == saved_source


def test_changed_legal_annotation_body_gets_new_snapshot_without_overwriting_prior_observation(tmp_path, monkeypatch):
    engine, run, _, http, ledger, calls = setup_normal_path(tmp_path, monkeypatch, details=valid_details())
    target, _ = synthesize_direction_review(engine, run, tmp_path / "synthesis", ledger=ledger)
    engine._target_due_diligence(run, target, target.uniprot_id)
    saved_source = next(s.model_dump() for s in run.sources if s.source_id == "ot:target:ENSG000001")
    key = http.request_events[-1]["cache_key"]
    (http.cache_dir / (key + ".response")).unlink()
    changed = valid_details()
    changed["tractability"] = [{"label": "SYNTHETIC updated annotation", "modality": "small molecule", "value": False}]
    def updated(*args, **kwargs):
        calls.append((args, kwargs))
        return response(200, {"data": {"target": changed}})
    monkeypatch.setattr(http.session, "request", updated)
    engine._target_due_diligence(run, target, target.uniprot_id)
    current_source = target.assessment["tractability_check"]["source_id"]
    assert len(calls) == 8 and current_source.startswith("ot:target:ENSG000001:snapshot:")
    assert target.assessment["tractability"] == changed["tractability"]
    assert len([s for s in run.sources if s.source_id.startswith("ot:target:ENSG000001")]) == 2
    assert next(s.model_dump() for s in run.sources if s.source_id == "ot:target:ENSG000001") == saved_source
    new_source = next(s.model_dump() for s in run.sources if s.source_id == current_source)
    engine._target_due_diligence(run, target, target.uniprot_id)
    assert len(calls) == 8 and target.assessment["open_targets_tractability"] == "cache_reused"
    assert target.assessment["tractability_check"]["source_id"] == current_source
    assert next(s.model_dump() for s in run.sources if s.source_id == current_source) == new_source

