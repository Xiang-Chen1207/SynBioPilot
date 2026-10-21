"""Exact product-object counterexamples. All documents and workers are SYNTHETIC."""
import copy
import io
import json
import zipfile

import pytest
import requests
from test_research_flow import attach, draft, hypothesis, setup_flow

from scientist.co_models import ResearchContract
from scientist.exports import export_research
from scientist.presentation import result_document
from scientist.research_verify import verify_run
from scientist.store import ProjectStore


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("SYNTHETIC product-object tests must not make network requests")
    monkeypatch.setattr(requests.sessions.Session, "request", reject)


WRONG_OBJECTS = [
    "Illustrative compound degradation products",
    "Illustrative compound-derived metabolites",
    "Illustrative compound receptor",
    "Illustrative compound sulfate",
    "Illustrative compound inactivating enzymes",
    "Illustrative compound conjugate",
    "Illustrative compound analogue",
    "Illustrative compound binding protein",
    "Illustrative compound-like molecules",
    "deoxy-Illustrative compound",
    "13C-Illustrative compound",
    "deoxy.Illustrative compound",
    "N,Illustrative compound",
]


def make_flow(quote):
    engine, flow = setup_flow()
    h = hypothesis(); h.claim = "SYNTHETIC strain is producing Illustrative compound"
    engine.state.hypotheses = [h]; flow.register(h)
    e = attach(engine, h, quote=quote, system=h.chassis_name)
    return engine, flow, h, e


@pytest.mark.parametrize("obj", WRONG_OBJECTS)
@pytest.mark.parametrize("template", [
    "SYNTHETIC strain produced {obj}.",
    "{obj} was synthesized by SYNTHETIC strain.",
    "SYNTHETIC strain production of {obj} was measured.",
    "SYNTHETIC strain生产了{obj}。",
    "{obj}由SYNTHETIC strain合成。",
])
def test_entire_active_passive_nominal_chinese_product_is_not_a_name_prefix(obj, template):
    engine, flow, h, e = make_flow(template.format(obj=obj))
    c = flow.claims_for(h)[0]
    flow.adjudicate([h], [draft(c, [e.evidence_id])])
    assert c.state == "context_only" and not flow.main_reviewed(h)
    assert verify_run(engine.run)["valid"]  # valid abstention receipt


@pytest.mark.parametrize("quote", [
    "SYNTHETIC strain produced Illustrative compound.",
    "SYNTHETIC strain produced measurable amounts of Illustrative compound.",
    "SYNTHETIC strain produced Illustrative compound under recorded conditions.",
    "SYNTHETIC strain produced Illustrative compound in culture.",
    "Illustrative compound was produced by SYNTHETIC strain.",
    "The Illustrative compound was synthesized by SYNTHETIC strain under recorded conditions.",
    "SYNTHETIC strain production of Illustrative compound was measured.",
    "SYNTHETIC strain production of Illustrative compound was measured under recorded conditions.",
    "SYNTHETIC strain合成了Illustrative compound。",
    "Illustrative compound由SYNTHETIC strain生产。",
])
def test_explicit_whole_target_product_and_finite_circumstantial_tail_remain_supported(quote):
    engine, flow, h, e = make_flow(quote)
    c = flow.claims_for(h)[0]
    flow.adjudicate([h], [draft(c, [e.evidence_id])])
    assert c.state == "supported" and flow.main_reviewed(h), quote
    assert verify_run(engine.run)["valid"]
    assert h.chassis.status == "unknown"  # no automatic experimental/clinical conclusion


@pytest.mark.parametrize("tail", [
    " inactivating enzymes", " under-recorded derivatives", " under recorded conditions receptor",
    " in medium-derived particles", " with a linked receptor", " (degradation products)", "'s metabolites",
])
def test_unknown_product_or_context_tail_abstains_without_suffix_blacklist(tail):
    _, flow, h, e = make_flow("SYNTHETIC strain produced Illustrative compound" + tail + ".")
    flow.adjudicate([h], [draft(flow.claims_for(h)[0], [e.evidence_id])])
    assert not flow.main_reviewed(h)


@pytest.mark.parametrize("obj", WRONG_OBJECTS[:3])
def test_wrong_product_cannot_propagate_through_review_meta_or_public_exports(obj, tmp_path):
    engine, flow, h, e = make_flow(f"SYNTHETIC strain produced {obj}.")
    main = flow.claims_for(h)[0]
    engine.call = lambda role, title, model, payload, **kwargs: model.model_validate({
        "reviews": [{"hypothesis_id":h.hypothesis_id,"version":1,"verdict":"retain",
                     "reason":"SYNTHETIC over-approval","valid_evidence_ids":[e.evidence_id]}],
        "claim_reviews":[draft(main,[e.evidence_id]).model_dump()]})
    engine.review([h], "SYNTHETIC product-object review")
    assert h.status == "needs_evidence" and engine.state.reviews[0].verdict == "revise"
    engine.phase = lambda *args, **kwargs: None; engine.finish = lambda *args, **kwargs: None
    contract = ResearchContract(hypothesis_id=h.hypothesis_id,version=1,status="human_review_needed",
        question="SYNTHETIC product identity question",judgment="SYNTHETIC over-approval",
        minimal_validation="SYNTHETIC identify complete product",evidence_ids=[e.evidence_id])
    engine.call = lambda role, title, model, payload, **kwargs: model.model_validate({"contracts":[contract.model_dump()],"common_gaps":[]})
    engine.meta()
    assert engine.state.contracts[0].status == "needs_evidence"
    # Replay an older unsafe self-approval: current shared gate must reject it.
    h.status = "reviewed"; engine.state.reviews[0].verdict = "retain"
    main.state = "supported"; flow.data.claim_reviews[0].verdict = "supported"
    engine.state.contracts[0].status = "human_review_needed"
    original = copy.deepcopy(engine.run.model_dump())
    view = result_document(engine.run)
    assert view["co_scientist"]["hypotheses"][0]["status"] == "needs_evidence"
    assert view["co_scientist"]["contracts"][0]["status"] == "needs_evidence"
    assert view["co_scientist"]["research_flow"]["claims"][0]["state"] != "supported"
    assert not verify_run(engine.run)["valid"]
    store = ProjectStore(tmp_path/"unused_export_store")
    pid = store.create_project("SYNTHETIC product-object export")["project_id"]
    export_run = engine.run.model_copy(update={"project_id":pid}, deep=True)
    for fmt in ("json", "zip"):
        data, _, _ = export_research(store, export_run, fmt)
        if fmt == "zip":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                exported = json.loads(archive.read("research.json"))
        else:
            exported = json.loads(data)
        assert exported["co_scientist"]["contracts"][0]["status"] == "needs_evidence"
    assert engine.run.model_dump() == original

