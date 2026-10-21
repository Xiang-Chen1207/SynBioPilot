"""SYNTHETIC offline scheduling and public-context regressions; no real research."""
import json
from types import SimpleNamespace

import pytest
import requests
from test_research import LITERATURE_QUOTE, FakeLLM, default_draft, source

from scientist.budgets import BudgetExceeded, RunLedger
from scientist.co_models import (
    AgentState,
    CoScientistState,
    HypothesisCard,
    HypothesisReview,
    ProblemAnchor,
)
from scientist.coscientist import ROLES, CoScientistEngine
from scientist.llm import api_request_body, serialized_request, strict_schema
from scientist.models import (
    EvidenceRecord,
    ExecutionStatus,
    ResearchBrief,
    ResearchBudget,
    ScientistSettings,
)
from scientist.research import ResearchEngine
from scientist.store import ProjectStore


class Clock:
    now = 1000.0
    def monotonic(self):
        return self.now


def setup_engine(tmp_path, monkeypatch, *, remaining=840, max_input=300000):
    clock = Clock()
    monkeypatch.setattr("scientist.budgets.time.monotonic", clock.monotonic)
    store = ProjectStore(tmp_path / "synthetic")
    pid = store.create_project("SYNTHETIC budget")['project_id']
    brief = ResearchBrief(disease="虚构疾病", research_mode="co_scientist",
        population="指定实验人群，年龄与条件仍需验证", delivery_context="鼻腔递送暴露和稳定性未知",
        phenotype="核心与伴随终点分别分析。" * 40,
        research_goal="仅评估指定候选，不是盲发现。" * 35, existing_candidates=["SYNTHETIC compound"],
        chassis_name="SYNTHETIC L168", budget=ResearchBudget(max_llm_calls=16,
            max_input_tokens=max_input, timeout_seconds=840, max_targets=0))
    run = store.create_run(pid, brief)
    run.status = ExecutionStatus.RUNNING
    calls = []
    def factory(settings, directory, max_calls):
        calls.append(str(directory))
        return SimpleNamespace(calls=0, cache_hits=0, complete=lambda *args: {})
    legacy = ResearchEngine(store, ScientistSettings(workspace=str(store.root)),
                            sources=SimpleNamespace(), llm_factory=factory)
    engine = CoScientistEngine(legacy)
    engine.run, engine.directory = run, store.run_dir(pid, run.run_id)
    engine.ledger = RunLedger(brief.budget, clock.now + remaining)
    engine.sources = SimpleNamespace(search_count=0)
    engine.state = run.co_scientist = CoScientistState(anchor=ProblemAnchor(
        disease=brief.disease, phenotype=brief.phenotype, species=brief.species,
        chassis_name=brief.chassis_name, population=brief.population,
        delivery_context=brief.delivery_context, research_goal=brief.research_goal),
        agents=[AgentState(agent_id=k, role=v[0]) for k,v in ROLES.items()])
    engine.flow = None
    paper = source("fixture:paper:1", LITERATURE_QUOTE)
    engine.run.sources = [paper]
    ev = EvidenceRecord(evidence_id="ev_synthetic", claim="SYNTHETIC support", source_id=paper.source_id,
        quote=LITERATURE_QUOTE, location="SYNTHETIC abstract", polarity="supports", validated_span=True,
        study_system="SYNTHETIC cells", condition="recorded", endpoint="cellular")
    engine.run.evidence = [ev]
    engine.state.hypotheses = [HypothesisCard(hypothesis_id="h_synthetic", title="SYNTHETIC",
        claim="SYNTHETIC assay hypothesis", chassis_name=brief.chassis_name, condition="recorded",
        endpoint="cellular", evidence_ids=[ev.evidence_id], origin="user_supplied",
        prediction="Measure", validation_question="Measure endpoint")]
    return engine, clock, calls


def test_serialized_transport_measurement_matches_actual_body():
    for backend in ("responses", "compatible"):
        settings = ScientistSettings(llm={"backend": backend, "model": "SYNTHETIC"}).llm
        shape = {"type": "object", "properties": {"疾病": {"type": "string"}}}
        body = api_request_body(settings, "中文\n", "限制与反证", shape)
        prepared = requests.Request("POST", "https://example.invalid", json=body).prepare()
        assert serialized_request(settings, "中文\n", "限制与反证", shape).encode() == prepared.body
    settings = ScientistSettings().llm
    shape = {"title": "SYNTHETIC", "type": "object", "properties": {"text": {"type": "string"}}}
    assert serialized_request(settings, "系统", "用户", shape) == (
        "系统\n\n用户" + json.dumps(strict_schema(shape), ensure_ascii=False, separators=(",", ":")) + "\n")


@pytest.mark.parametrize("key,value,reason", [
    ("input_tokens_reserved", 299900, "Input"), ("llm_calls", 15, "LLM"),
    ("output_tokens_reserved", 95900, "Output")])
def test_shared_preflight_preserves_required_resources(key, value, reason):
    import time
    ledger = RunLedger(ResearchBudget(max_llm_calls=16), time.monotonic() + 840)
    ledger.counts[key] = value
    before = dict(ledger.counts)
    with pytest.raises(BudgetExceeded, match=reason) as caught:
        ledger.reserve_request(50, required_output_bytes=50,
            reserve={"bytes": 100, "calls": 1, "output_bytes": 100, "seconds": 60})
    assert ledger.counts == before
    assert caught.value.details['blocked_before_provider'] is True


def test_long_chinese_serialized_request_is_blocked_before_factory(tmp_path, monkeypatch):
    engine, _, calls = setup_engine(tmp_path, monkeypatch)
    engine.ledger.counts['input_tokens_reserved'] = 260549
    before = dict(engine.ledger.counts)
    with pytest.raises(BudgetExceeded, match="Input reservation"):
        engine.invoke("reflection", "SYNTHETIC required review", "system", "长中文" * 10000,
                      {"type": "object"})
    assert not calls and engine.actual_calls == 0 and engine.ledger.counts == before
    saved = engine.store.load_run(engine.run.project_id, engine.run.run_id)
    failure = saved.co_scientist.budget_failures[-1]
    assert failure['required_bytes'] > 39451 and failure['remaining_bytes'] == 39451
    assert failure['blocked_before_provider'] is True and failure['accounting_unit'] == 'utf8_bytes'


def test_late_optional_extraction_does_not_consume_formal_review(tmp_path, monkeypatch):
    engine, _, calls = setup_engine(tmp_path, monkeypatch, remaining=87)
    engine.call_durations = [158.148]
    before_evidence = [e.model_dump() for e in engine.run.evidence]
    engine.supplement_evidence(["fixture:paper:1"])
    assert not calls
    failure = engine.state.optional_skips[0]
    assert failure['remaining_seconds'] == 87 and failure['reserved_seconds'] >= 158.148 * 2
    assert [e.model_dump() for e in engine.run.evidence] == before_evidence
    assert not engine.state.contracts
    # The required review is still dispatchable with the remaining wall clock.
    engine.invoke("reflection", "SYNTHETIC formal review", "system", "{}", {"type": "object"})
    assert len(calls) == 1 and engine.ledger.counts['llm_calls'] == 1


def test_optional_review_timeout_preserves_artifacts_and_rejects_new_evidence(tmp_path, monkeypatch):
    engine, clock, calls = setup_engine(tmp_path, monkeypatch, remaining=840)
    before_evidence = [e.model_dump() for e in engine.run.evidence]
    before_cards = [h.model_dump() for h in engine.state.hypotheses]
    def factory(settings, directory, max_calls):
        calls.append(str(directory))
        worker = FakeLLM(settings, directory, max_calls, draft=default_draft(), verdicts={})
        original = worker.complete
        def complete(system, user, schema):
            if schema['title'] == 'ReviewDraft':
                worker.calls += 1
                clock.now += 86.023
                raise TimeoutError('SYNTHETIC provider timeout')
            return original(system, user, schema)
        worker.complete = complete
        return worker
    engine.legacy.llm_factory = factory
    engine.supplement_evidence(["fixture:paper:1"])
    assert len(calls) == 2
    assert [e.model_dump() for e in engine.run.evidence] == before_evidence
    assert [h.model_dump() for h in engine.state.hypotheses] == before_cards
    assert not engine.state.contracts and not engine.state.reviews
    assert engine.prior_timeout and any(f['reason']=='provider_timeout' for f in engine.state.budget_failures)
    assert any('pending' in w for w in engine.run.warnings)


def test_compact_context_retains_both_sides_and_continuous_source_windows(tmp_path, monkeypatch):
    engine, _, _ = setup_engine(tmp_path, monkeypatch)
    support = engine.run.evidence[0]
    support.quote = 'SYNTHETIC exact supporting sentence.'
    oppose = support.model_copy(update={'evidence_id':'ev_opposition', 'polarity':'opposes',
        'quote':'SYNTHETIC exact negative sentence.', 'study_system':'SYNTHETIC humans',
        'condition':'different dose', 'endpoint':'accompanying symptom'})
    engine.run.sources[0].text = 'x' * 2500 + support.quote + 'y' * 3000 + oppose.quote + 'z' * 3000
    engine.run.evidence.append(oppose)
    card = engine.state.hypotheses[0]
    card.opposing_evidence_ids = [oppose.evidence_id]
    original = engine.run.sources[0].text
    payload = engine.context([card])
    assert payload['anchor']['population'] == engine.run.brief.population
    assert payload['anchor']['delivery_context'] == engine.run.brief.delivery_context
    assert payload['anchor']['research_goal'] == engine.run.brief.research_goal
    assert payload['user_supplied_candidates'] == engine.run.brief.existing_candidates
    assert {e['polarity'] for e in payload['accepted_evidence']} == {'supports','opposes'}
    assert payload['hypotheses'][0]['origin'] == 'user_supplied'
    for window in payload['sources'][0]['windows']:
        assert original[window['start']:window['end']] == window['text']
    assert all(any(e.quote in w['text'] for w in payload['sources'][0]['windows']) for e in (support, oppose))
    assert sum(len(w['text']) for w in payload['sources'][0]['windows']) < len(original)


def test_optional_provider_deadline_excludes_critical_reserve(tmp_path, monkeypatch):
    engine, clock, _ = setup_engine(tmp_path, monkeypatch)
    workers = []
    def factory(settings, directory, max_calls):
        worker = SimpleNamespace(calls=0, cache_hits=0)
        def complete(*args):
            worker.calls += 1
            assert worker.deadline == clock.now + 840 - 320
            return {}
        worker.complete = complete
        workers.append(worker)
        return worker
    engine.legacy.llm_factory = factory
    engine.invoke('supervisor', 'SYNTHETIC optional', 'system', '{}', {'type':'object'},
                  optional=True, reserve={'seconds':320, 'calls':2, 'bytes':30000, 'output_bytes':24000})
    assert len(workers) == 1 and engine.actual_calls == 1
    assert engine.ledger.deadline == clock.now + 840


def test_oversized_optional_response_is_counted_but_cannot_use_critical_output_allowance(tmp_path, monkeypatch):
    engine, _, _ = setup_engine(tmp_path, monkeypatch)
    result = {'text':'中文' * 4000}
    worker = SimpleNamespace(calls=0, cache_hits=0)
    def complete(*args):
        worker.calls += 1
        return result
    worker.complete = complete
    engine.legacy.llm_factory = lambda *args, **kwargs: worker
    before = engine.run.evidence[0].model_dump()
    with pytest.raises(BudgetExceeded, match='Output reservation'):
        engine.invoke('supervisor', 'SYNTHETIC oversized response', 'system', '{}', {'type':'object'},
                      optional=True, reserve={'seconds':120, 'calls':2, 'bytes':30000, 'output_bytes':24000})
    assert engine.actual_calls == 1
    assert engine.ledger.counts['output_bytes_received'] == len(json.dumps(result, ensure_ascii=False).encode())
    assert engine.ledger.counts['output_tokens_reserved'] == 0
    assert engine.state.budget_failures[-1]['blocked_before_provider'] is False
    assert engine.state.budget_failures[-1]['reserved_output_bytes'] == 24000
    assert engine.run.evidence[0].model_dump() == before and not engine.state.contracts


def test_formal_context_keeps_uncited_negative_in_same_source_and_other_paper(tmp_path, monkeypatch):
    engine, _, _ = setup_engine(tmp_path, monkeypatch)
    negative = 'SYNTHETIC human observation did not demonstrate treatment benefit; intervention was in mice.'
    engine.run.sources[0].text = LITERATURE_QUOTE + 'x' * 2800 + negative
    uncited = source('synthetic:negative', 'SYNTHETIC independent negative study in a different population.')
    engine.run.sources.append(uncited)
    payload = engine.context(engine.state.hypotheses)
    by_id = {s['source_id']:s for s in payload['sources']}
    assert any(negative in w['text'] for w in by_id['fixture:paper:1']['windows'])
    assert by_id[uncited.source_id]['review_status'] == 'unreviewed_source'
    assert any(uncited.text in w['text'] for w in by_id[uncited.source_id]['windows'])
    assert len(payload['accepted_evidence']) == 1  # observation is not promoted by context retention


def test_meta_context_uses_current_version_reviews_only(tmp_path, monkeypatch):
    engine, _, _ = setup_engine(tmp_path, monkeypatch)
    old = engine.state.hypotheses[0]
    current = old.model_copy(update={'version':2, 'parent_version':1})
    engine.state.hypotheses.append(current)
    for version in (1, 2):
        engine.state.reviews.append(HypothesisReview(hypothesis_id=old.hypothesis_id, version=version,
            verdict='revise', reason='SYNTHETIC version-specific review'))
    payload = engine.meta_payload([current])
    assert [r['version'] for r in payload['reviews']] == [2]
    assert [h['version'] for h in payload['hypotheses']] == [2]
    assert [r.version for r in engine.state.reviews] == [1, 2]


def test_optional_revision_timeout_still_attempts_meta_on_reviewed_originals(tmp_path):
    from test_coscientist import Controller, CoSources, Worker
    class TimeoutWorker(Worker):
        def complete(self, system, user, schema):
            if schema['title'] == 'EvolutionDraft':
                self.calls += 1
                raise TimeoutError('SYNTHETIC optional revision timeout')
            return super().complete(system, user, schema)
    class TimeoutController(Controller):
        def factory(self, settings, directory, max_calls):
            return TimeoutWorker(settings, directory, max_calls, self)
    store = ProjectStore(tmp_path / 'synthetic_revision')
    pid = store.create_project('SYNTHETIC revision timeout')['project_id']
    brief = ResearchBrief(disease='Fixture disease', research_mode='co_scientist',
        existing_candidates=['Illustrative compound'], budget=ResearchBudget(max_llm_calls=16,
            max_papers=8, max_input_tokens=500000, max_output_tokens=100000))
    run = store.create_run(pid, brief)
    control = TimeoutController()
    engine = ResearchEngine(store, ScientistSettings(workspace=str(store.root)),
                            sources=CoSources(), llm_factory=control.factory)
    result = engine.execute(pid, run.run_id)
    assert result.co_scientist.reviews and result.co_scientist.contracts
    assert all(h.version == 1 for h in result.co_scientist.hypotheses)
    assert all(c.version == 1 for c in result.co_scientist.contracts)
    assert any(name == 'MetaDraft' for name, _, _ in control.requests)
    assert result.co_scientist.optional_skips
    assert any(f['reason'] == 'provider_timeout' for f in result.co_scientist.budget_failures)


def test_long_task_background_keeps_constraints_as_traceable_unverified_windows(tmp_path, monkeypatch):
    from scientist.context import task_context
    engine, _, _ = setup_engine(tmp_path, monkeypatch)
    condition = 'SYNTHETIC strain L168; unknown delivery exposure; qPCR alone is not a protein measurement.'
    boundary = '必须保留：核心终点与伴随终点分开；未知产量，不能推断临床获益。'
    document = ('SYNTHETIC unverified wet-lab background\n' +
                'unrelated procedure detail\n' * 150 + condition + '\n' +
                'unrelated procedure detail\n' * 150 + boundary + '\nend of supplied background')
    engine.run.brief.assumptions = ['SYNTHETIC assumptions are not evidence',
                                   'document_sha256=' + 'a'*64, document]
    before = engine.run.brief.model_dump()
    compact = task_context(engine.run.brief)
    windows = compact['background_documents'][0]['windows']
    assert compact['background_documents'][0]['reference'] == 'brief.assumptions[2]'
    assert compact['background_documents'][0]['scope'] == 'unverified_user_supplied_background_not_accepted_evidence'
    assert any(condition in w['text'] for w in windows)
    assert any(boundary in w['text'] for w in windows)
    assert all(document[w['start']:w['end']] == w['text'] for w in windows)
    assert len(json.dumps(compact)) < len(json.dumps(task_context(engine.run.brief, include_documents=True)))
    assert 'document_sha256' not in json.dumps(compact)
    assert document in task_context(engine.run.brief, include_documents=True)['assumptions']
    assert engine.run.brief.model_dump() == before


def test_compact_claim_conditions_are_inherited_only_when_exactly_equal(tmp_path, monkeypatch):
    from scientist.flow_models import ClaimRecord, ResearchFlowState
    engine, _, _ = setup_engine(tmp_path, monkeypatch)
    card = engine.state.hypotheses[0]
    shared = ClaimRecord(claim_id='c_shared',hypothesis_id=card.hypothesis_id,version=card.version,
        kind='main',text='SYNTHETIC shared scope',condition=card.condition,endpoint=card.endpoint)
    different = shared.model_copy(update={'claim_id':'c_other', 'condition':'SYNTHETIC different dose',
                                         'endpoint':'SYNTHETIC different endpoint'})
    engine.state.research_flow = ResearchFlowState(claims=[shared, different])
    data = engine.context([card])
    first, second = data['literature_landscape']['claims']
    assert 'condition' not in first and 'endpoint' not in first
    assert data['hypotheses'][0]['condition'] == shared.condition
    assert second['condition'] == different.condition and second['endpoint'] == different.endpoint
    assert engine.state.research_flow.claims[0].condition == shared.condition

