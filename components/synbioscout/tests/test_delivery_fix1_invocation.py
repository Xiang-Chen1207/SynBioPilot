"""SYNTHETIC local save failures; no model/HTTP, temporary fixture stores only."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from test_budget_scheduling import setup_engine

from scientist.budgets import BudgetExceeded


def invoke(engine, title='SYNTHETIC reservation cleanup'):
    return engine.invoke('reflection', title, 'SYNTHETIC', '{}', {'type':'object'},
        reserve={'bytes':1000,'calls':1,'output_bytes':12000,'seconds':125}, output_cap=12000)


def worker_factory(constructions):
    def factory(*args, **kwargs):
        constructions.append(True)
        worker=SimpleNamespace(calls=0,cache_hits=0)
        def complete(*args):
            worker.calls += 1
            return {'synthetic':True}
        worker.complete=complete
        return worker
    return factory


@pytest.mark.parametrize('failure_mode', ['event', 'persist', 'event_timeout'])
def test_pre_provider_record_failure_releases_reservation_without_masking_original_or_faking_provider(tmp_path, monkeypatch, failure_mode):
    engine, _, _ = setup_engine(tmp_path, monkeypatch)
    constructions=[]
    engine.legacy.llm_factory=worker_factory(constructions)
    primary=(TimeoutError if failure_mode=='event_timeout' else OSError)('SYNTHETIC primary local event failure')
    secondary=RuntimeError('SYNTHETIC secondary reporting failure')
    calls=[]
    def fail(*args, **kwargs):
        calls.append(True)
        raise primary if len(calls)==1 else secondary
    monkeypatch.setattr(engine, 'persist' if failure_mode=='persist' else 'event', fail)
    evidence=[e.model_dump() for e in engine.run.evidence]
    with pytest.raises(type(primary)) as caught:
        invoke(engine)
    assert caught.value is primary
    assert len(calls)==2  # A best-effort failure record cannot replace the cause.
    assert not constructions and engine.actual_calls==0
    assert engine.ledger.snapshot()['output_bytes_inflight']==0
    assert engine.active['reflection']==0
    assert engine.ledger.counts['llm_calls']==1 and engine.ledger.counts['input_tokens_reserved']>0
    assert engine.ledger.counts['output_tokens_reserved']==engine.ledger.counts['output_bytes_received']==0
    assert not engine.prior_timeout
    failure=engine.state.budget_failures[-1]
    assert failure['reason']=='coordinator_failure' and failure['blocked_before_provider'] is True
    assert 'provider_failure' not in failure
    assert [e.model_dump() for e in engine.run.evidence]==evidence and not engine.state.contracts


@pytest.mark.parametrize('failure_mode', ['event', 'persist'])
def test_completion_record_failure_counts_worker_once_preserves_output_and_releases_active_slot(tmp_path, monkeypatch, failure_mode):
    engine, _, _ = setup_engine(tmp_path, monkeypatch)
    constructions=[]
    engine.legacy.llm_factory=worker_factory(constructions)
    primary=OSError('SYNTHETIC completion save failure')
    secondary=RuntimeError('SYNTHETIC failure-report save failure')
    original=getattr(engine,failure_mode)
    calls=[]
    def maybe_fail(*args, **kwargs):
        if failure_mode=='event':
            is_completed=kwargs.get('operation_status')=='completed'
        else:
            is_completed=bool(engine.state.events and engine.state.events[-1].operation_status=='completed')
        if is_completed or calls:
            calls.append(True)
            raise primary if len(calls)==1 else secondary
        return original(*args, **kwargs)
    monkeypatch.setattr(engine,failure_mode,maybe_fail)
    with pytest.raises(OSError) as caught:
        invoke(engine)
    assert caught.value is primary and len(calls)==2
    assert len(constructions)==engine.actual_calls==1
    assert engine.active['reflection']==0 and len(engine.call_durations)==1
    assert engine.ledger.snapshot()['output_bytes_inflight']==0
    expected=len(json.dumps({'synthetic':True},ensure_ascii=False).encode())
    assert engine.ledger.counts['output_bytes_received']==engine.ledger.counts['output_tokens_reserved']==expected
    assert engine.ledger.counts['llm_calls']==1
    failure=engine.state.budget_failures[-1]
    assert failure['reason']=='coordinator_failure' and failure['blocked_before_provider'] is False
    assert 'provider_failure' not in failure and not engine.state.contracts


def test_failed_event_in_parallel_worker_does_not_release_or_count_other_worker(tmp_path, monkeypatch):
    engine, _, _ = setup_engine(tmp_path, monkeypatch)
    constructions=[]
    engine.legacy.llm_factory=worker_factory(constructions)
    original=engine.event
    def event(role, kind, status, title, *args, **kwargs):
        if title=='SYNTHETIC failing worker' and kwargs.get('operation_status')=='running':
            raise OSError('SYNTHETIC failed before provider')
        return original(role,kind,status,title,*args,**kwargs)
    monkeypatch.setattr(engine,'event',event)
    barrier=threading.Barrier(2)
    def execute(title):
        barrier.wait(timeout=3)
        try:
            return invoke(engine,title)
        except OSError:
            return 'failed_before_provider'
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(execute,['SYNTHETIC failing worker','SYNTHETIC valid worker']))
    assert result==['failed_before_provider',{'synthetic':True}]
    assert engine.ledger.counts['llm_calls']==2 and engine.actual_calls==len(constructions)==1
    assert engine.active['reflection']==0 and engine.ledger.snapshot()['output_bytes_inflight']==0
    engine.ledger.release_output('llm_001')
    engine.ledger.release_output('llm_002')
    assert engine.ledger.snapshot()['output_bytes_inflight']==0
    assert engine.ledger.counts['llm_calls']==2 and engine.actual_calls==1


def test_preflight_error_is_not_replaced_by_error_while_saving_its_receipt(tmp_path, monkeypatch):
    engine, _, _ = setup_engine(tmp_path, monkeypatch)
    engine.ledger.counts['input_tokens_reserved']=engine.run.brief.budget.max_input_tokens
    monkeypatch.setattr(engine,'event',lambda *args,**kwargs: (_ for _ in ()).throw(OSError('SYNTHETIC save error')))
    with pytest.raises(BudgetExceeded,match='Input reservation'):
        invoke(engine)
    assert engine.ledger.counts['llm_calls']==engine.actual_calls==0
    assert engine.ledger.snapshot()['output_bytes_inflight']==0
    assert engine.state.budget_failures[-1]['blocked_before_provider'] is True


def test_cleanup_error_after_release_does_not_hide_original_or_skip_settlement(tmp_path, monkeypatch):
    engine, _, _=setup_engine(tmp_path,monkeypatch)
    primary=OSError('SYNTHETIC original recording error')
    def fail_event(*args,**kwargs):
        raise primary
    release=engine.ledger.release_output
    releases=[]
    def cleanup_failure(identifier):
        releases.append(identifier)
        release(identifier)
        raise RuntimeError('SYNTHETIC cleanup reporting error')
    monkeypatch.setattr(engine,'event',fail_event)
    monkeypatch.setattr(engine.ledger,'release_output',cleanup_failure)
    with pytest.raises(OSError) as caught:
        invoke(engine)
    assert caught.value is primary and len(releases)==2
    assert engine.active['reflection']==0 and engine.actual_calls==0
    assert engine.ledger.snapshot()['output_bytes_inflight']==0
    assert engine.ledger.counts['llm_calls']==1 and engine.ledger.counts['input_tokens_reserved']>0


def test_local_adapter_read_failure_before_attempt_is_not_provider_failure(tmp_path, monkeypatch):
    from scientist.llm import StructuredLLM
    engine,_,_=setup_engine(tmp_path,monkeypatch)
    constructions=[]
    def factory(settings,directory,max_calls):
        # Only this temporary synthetic adapter directory is used. There is no
        # historical inference access or provider/network/process invocation.
        worker=StructuredLLM(settings,tmp_path/'SYNTHETIC_local_adapter',max_calls=max_calls)
        worker.attempt_path=tmp_path
        constructions.append(worker)
        return worker
    engine.legacy.llm_factory=factory
    monkeypatch.setattr(StructuredLLM,'_codex',lambda *a,**kw: pytest.fail('Provider must not run'))
    monkeypatch.setattr(StructuredLLM,'_api',lambda *a,**kw: pytest.fail('Provider must not run'))
    with pytest.raises(IsADirectoryError):
        invoke(engine)
    assert len(constructions)==1 and constructions[0].calls==engine.actual_calls==0
    failure=engine.state.budget_failures[-1]
    assert failure['reason']=='coordinator_failure' and failure['blocked_before_provider'] is True
    assert 'provider_failure' not in failure and not engine.prior_timeout
    assert engine.active['reflection']==0 and engine.ledger.snapshot()['output_bytes_inflight']==0
    assert engine.ledger.counts['llm_calls']==1 and engine.ledger.counts['input_tokens_reserved']>0

