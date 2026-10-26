"""SYNTHETIC actual-coordinator Meta output boundary; no provider or research."""
import json

import pytest
import requests
import test_delivery_chain_scheduling as chain


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("SYNTHETIC budget audit must not contact any provider")
    monkeypatch.setattr(requests.sessions.Session,"request",forbidden)
    monkeypatch.setattr("scientist.llm.subprocess.run",forbidden)
    monkeypatch.setattr("scientist.llm.subprocess.Popen",forbidden)


@pytest.mark.parametrize("extra", [0,1])
def test_actual_meta_return_at_cap_or_one_byte_over_never_commits_an_unaccepted_contract(tmp_path,monkeypatch,extra):
    original_worker=chain.ChainWorker
    received=[]
    class SizedMetaWorker(original_worker):
        def complete(self,system,user,schema):
            value=super().complete(system,user,schema)
            if schema["title"] == "MetaDraft":
                value["common_gaps"]=["SYNTHETIC_META_BOUNDARY "]
                amount=len(json.dumps(value,ensure_ascii=False).encode())
                assert amount < 12000
                value["common_gaps"][0]+="x" * (12000 + extra - amount)
                received.append(len(json.dumps(value,ensure_ascii=False).encode()))
            return value
    monkeypatch.setattr(chain,"ChainWorker",SizedMetaWorker)
    run,control=chain.run_normal_chain(tmp_path)
    assert received == [12000+extra]
    assert [title for title,_ in control.requests].count("MetaDraft") == 1
    assert len(run.co_scientist.reviews) == 1
    assert run.evidence and run.co_scientist.hypotheses
    assert run.usage["output_bytes_inflight"] == 0
    assert run.usage["input_tokens_reserved"] <= 300000
    assert run.usage["output_tokens_reserved"] <= 96000
    meta_events=[event for event in run.co_scientist.events if event.kind == "llm" and event.agent_id == "meta_review"]
    if extra == 0:
        assert run.status.value == "completed"
        assert len(run.co_scientist.contracts) == 1
        assert run.co_scientist.contracts[0].status == "needs_evidence"
        assert meta_events[-1].operation_status == "completed"
        assert run.usage["output_bytes_received"] == run.usage["output_tokens_reserved"]
    else:
        assert run.status.value == "partial"
        assert run.co_scientist.contracts == []
        assert meta_events[-1].operation_status == "failed"
        assert not any(event.operation_status == "completed" for event in meta_events)
        assert run.usage["output_bytes_received"]-run.usage["output_tokens_reserved"] == 12001
        failure=next(row for row in run.co_scientist.budget_failures if row.get("required_output_bytes") == 12001)
        assert failure["blocked_before_provider"] is False
        assert failure["remaining_output_bytes"] > 12001  # the per-call cap, not total exhaustion, rejected it
        assert failure["operation_id"] == meta_events[-1].operation_id
        assert failure["accounting_unit"] == "utf8_bytes"

