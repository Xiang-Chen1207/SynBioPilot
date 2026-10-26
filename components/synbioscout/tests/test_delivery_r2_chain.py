"""SYNTHETIC complete assessment-priority chain with actual production orchestration.

The public-sized fixture reads only the two explicitly named public input files
and the normal frozen background package. Model outputs are transparent stubs,
an offline software evaluation using local synthetic fixtures.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
import requests
from test_core_identity_preparation import background_row

from scientist.background import BackgroundPackage, background_task_scope
from scientist.llm import serialized_request, strict_schema
from scientist.models import ResearchBrief, ResearchBudget, ScientistSettings, SourceRecord
from scientist.research import ResearchEngine
from scientist.research_flow import source_observation_statement
from scientist.store import ProjectStore

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_RUN = "runs/scientist/projects/p_378574bc97964e81/research/r_b341edfc222b4c2a/run.json"
PUBLIC_SHA = "ea7e4ddc705e1c6acad435073bb52750f2fd545f5da5e7277f83a61789178558"
REQUEST_FILE = "docs/nac_asd_iteration/SIM_DELIVERY_NAC_R1_PREFLIGHT.json"
REQUEST_SHA = "1e52df5c1a8a56ba2979890935e1fdd6e616a9d9fafdc11c3b2feb6b08f3ce76"
ANCHOR_SECONDS = 165.658
HISTORICAL_RETRIEVAL_SECONDS = 48.593
BOUNDARY_OVERHEAD_SECONDS = .122
SENTINEL_NAME = "Synthetic lactate (SL)"


def public_fixture():
    allowed = {PUBLIC_RUN, REQUEST_FILE}
    def read(relative):
        if relative not in allowed or "inference" in Path(relative).parts or ".." in Path(relative).parts:
            raise AssertionError("Public fixture path was not explicitly allowlisted")
        path = ROOT / relative
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
            raise AssertionError("Public fixture cannot follow a symlink or escape repository")
        return path.read_bytes()
    raw = read(PUBLIC_RUN)
    assert hashlib.sha256(raw).hexdigest() == PUBLIC_SHA
    saved = json.loads(raw)
    request = json.loads(read(REQUEST_FILE))["request"]
    assert hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == REQUEST_SHA
    assert request["brief"] == saved["brief"] and request["prepare_targets"] is False
    sources = [SourceRecord.model_validate(s) for s in saved["sources"]]
    assert len(sources) == 6 and not saved["evidence"] and not saved["candidates"]
    return ResearchBrief.model_validate(request["brief"]), sources


class ThreadClock:
    """Independent fake wall clocks for separate concurrently tested runs."""
    def __init__(self):
        self.local = threading.local()
    def monotonic(self):
        return getattr(self.local, "now", 1000.0)
    def advance(self, seconds):
        self.local.now = self.monotonic() + seconds
    def set(self, value):
        self.local.now = value


@pytest.fixture(autouse=True)
def no_live_transport_or_private_files(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("SYNTHETIC full-chain test forbids actual transport/process")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(requests.api, "request", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, blocked)
    monkeypatch.setattr(os, "system", blocked)
    monkeypatch.setattr(os, "popen", blocked)
    original_open = Path.open
    def safe_open(path, *args, **kwargs):
        if "inference" in path.parts or path.name in {"auth.json", "config.toml"}:
            raise AssertionError("Synthetic execution must not access inference/auth/config files")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", safe_open)


def sentinel_brief():
    return ResearchBrief(disease="SYNTHETIC marker syndrome", research_mode="co_scientist",
        task_intent="candidate_assessment", existing_candidates=[SENTINEL_NAME],
        background_package_id="synthetic_assessment_v1", phenotype="SYNTHETIC associated symptom and separate biomarker",
        population="SYNTHETIC defined pediatric population", chassis_name="SYNTHETIC strain Q9",
        conditions="SYNTHETIC intervention conditions; comparator remains explicit",
        delivery_context="SYNTHETIC delivery and exposure unknown", research_goal="SYNTHETIC bounded source assessment",
        budget=ResearchBudget(max_targets=0, literature_max_candidates=2, max_papers=12, max_searches=5,
            max_llm_calls=16, max_md_jobs_total=0, timeout_seconds=840, max_hypotheses=2,
            max_http_requests=28, max_tool_calls=12, max_input_tokens=300000, max_output_tokens=96000))


def sentinel_sources(*, negative_only=False):
    records = [
        ("supports", "SYNTHETIC Synthetic lactate improved the recorded associated symptom in a pediatric controlled trial.", "Homo sapiens; SYNTHETIC pediatric controlled trial", "SYNTHETIC oral adjunctive treatment", "SYNTHETIC associated symptom"),
        ("mixed", "SYNTHETIC Synthetic lactate changed a biomarker but did not improve the primary clinical endpoint.", "Homo sapiens; SYNTHETIC pediatric controlled trial", "SYNTHETIC randomized treatment", "SYNTHETIC biomarker and separate primary clinical endpoint"),
        ("opposes", "SYNTHETIC Synthetic lactate showed no benefit on the measured primary or secondary endpoints.", "Homo sapiens; SYNTHETIC controlled trial", "SYNTHETIC longer adjunctive treatment", "SYNTHETIC clinical endpoints"),
        ("context", "SYNTHETIC Synthetic lactate changed a cellular marker in rats; this was not a human treatment experiment.", "SYNTHETIC rats; unknown strain and sex", "SYNTHETIC animal intervention", "SYNTHETIC cellular marker"),
    ]
    return [SourceRecord(source_id=f"synthetic:source:{i}", provider="europe_pmc", title="SYNTHETIC marker syndrome " + SENTINEL_NAME,
        url=f"https://europepmc.org/article/MED/{90000000+i}", retrieved_at="2026-01-01", text=("SYNTHETIC Methods: this study enrolled human children. " if row[0] != "context" else "SYNTHETIC Methods: this study examined rats. ") + row[1],
        metadata={"synthetic": True, "not_a_real_publication": True, "is_open_access": "N",
                  "synthetic_scope": {"polarity": row[0], "study_system": row[2], "condition": row[3], "endpoint": row[4]}})
        for i, row in enumerate(records, 1) if not negative_only or row[0] == "opposes"]


def extraction_rows(sources):
    # Real-source strings below are exact public source observations. Their
    # automated acceptance here is synthetic and never reported as a new study.
    quotes = {
        "pmid:22342106": ("supports", "Compared with placebo, NAC resulted in significant improvements on ABC irritability subscale (F = 6.80; p < .001; d = .96)."),
        "pmid:23886027": ("mixed", "Moreover, the interactive effect of time and group (time×group) was not statistically different (Table 2 )."),
        "pmid:25580916": ("supports", "By week 10, the NAC group showed significantly more reduction in irritability (P = 0.02) and hyperactivity/noncompliance (P = 0.01) subscales scores."),
        "pmid:27103982": ("mixed", "At week 12, there was no statistically significant difference between the NAC and placebo groups on the CGI-I (p > 0.69) but the glutathione (GSH) level in blood was significantly higher in the NAC group (p < 0.05)."),
        "pmid:27316706": ("opposes", "There were no differences between N-acetyl cysteine and placebo-treated groups on any of the outcome measures for either primary or secondary endpoints."),
        "pmid:32900213": ("context", "By systematically searching the PubMed, Embase and Cochrane Library, we obtained five randomized controlled trials."),
    }
    result = []
    for i, source in enumerate(sources, 1):
        if source.source_id.startswith("synthetic:"):
            scope = source.metadata["synthetic_scope"]
            polarity, quote = scope["polarity"], source.text
        else:
            polarity, quote = quotes[source.source_id]
            if source.source_id == "pmid:23886027":
                # Keep the complete continuous results paragraph: group effect,
                # null time×group interaction, and other null scales together.
                quote = source.text[source.text.index("Repeated measures ANOVA showed both NAC"):]
            scope = {"study_system": "Homo sapiens; reported pediatric trial" if polarity != "context" else "Systematic review index; not an additional independent trial",
                "condition": "Only this source's reported intervention and comparator; no formulation equivalence inferred",
                "endpoint": "Only the endpoint in the continuous quoted source observation"}
        assert quote in source.text
        result.append({"key": f"e{i}", "source_id": source.source_id, "claim": ("NAC source observation: " + quote) if source.source_id == "pmid:23886027" else (("N-acetylcysteine source observation: " + quote) if source.source_id == "pmid:32900213" else quote), "quote": quote,
            "location": "SYNTHETIC extraction from supplied continuous source text", "polarity": polarity,
            **{key: scope[key] for key in ("study_system", "condition", "endpoint")},
            "limitations": ["SYNTHETIC offline workflow response; not an independent scientific validation"]})
    return result


class OfflineSources:
    def __init__(self):
        self.http = SimpleNamespace(request_count=0, cache_hits=0, before_request=None, on_cache_hit=None, deadline=None)
        self.online_queries = []
    def search_literature(self, query, limit=8, **kwargs):
        self.online_queries.append(query)
        raise AssertionError("Given-source assessment must not require online expansion")
    def resolve_molecule(self, name):
        raise AssertionError("Verified explicit background identity must use normal core preparation")
    def resolve_target(self, *args, **kwargs):
        raise AssertionError("This assessment fixture has max_targets=0")


class AssessmentController:
    def __init__(self, brief, sources, clock, *, delays=None, reject=False, timeout_stage="", late_stage="",
                 target_output_bytes=None, tamper="", review_barrier=None):
        self.brief, self.sources, self.clock = brief, sources, clock
        self.delays = {"AnchorDraft": ANCHOR_SECONDS, "SynthesisDraft": 90.0,
            "ReviewDraft": 90.0, "CandidateAssessmentReviewDraft": 90.0, **(delays or {})}
        self.reject, self.timeout_stage, self.late_stage = reject, timeout_stage, late_stage
        self.target_output_bytes, self.tamper, self.review_barrier = target_output_bytes, tamper, review_barrier
        self.calls, self.boundaries, self.co = [], [], None
        self.rows = extraction_rows(sources)

    def factory(self, settings, directory, max_calls):
        return AssessmentWorker(settings, self)

    def response(self, title, payload):
        if title == "AnchorDraft":
            return {"disease_english": self.brief.disease, "searches": ["SYNTHETIC bounded intervention and contrary query"],
                "questions": ["SYNTHETIC what remains unmeasured?"], "success_criterion": "SYNTHETIC source-limited assessment"}
        if title == "SynthesisDraft":
            # The review index is supplied to the independent reviewer as
            # background, counted separately from the five independent intervention trials.
            candidate_rows = [row for row in self.rows if row["source_id"] != "pmid:32900213"]
            negative_only = all(row["polarity"] == "opposes" for row in candidate_rows)
            observation_summary = ("The reported sources contain no supported benefit on their measured endpoints."
                if negative_only else "The supplied sources contain favorable, mixed and negative observations.")
            findings = [{"finding_key": f"f{i}", "statement": source_observation_statement(row["source_id"], row["quote"]), "evidence_keys": [row["key"]],
                         "scope": {key: row[key] for key in ("study_system", "condition", "endpoint")}}
                        for i, row in enumerate(candidate_rows, 1)]
            return {"evidence": copy.deepcopy(self.rows), "targets": [], "candidates": [{
                "name": self.brief.existing_candidates[0], "evidence_keys": [row["key"] for row in candidate_rows],
                "target_relations": [], "rationale": "SYNTHETIC draft rationale is not an independently reviewed conclusion",
                "uncertainties": ["SYNTHETIC production, delivery and exposure remain unknown"],
                "role": "unknown", "role_reason": "SYNTHETIC no general therapeutic role established", "role_evidence_keys": []}],
                "assessment_proposals": [{"candidate_name": self.brief.existing_candidates[0],
                    "conclusion": "SYNTHETIC limited assessment: " + observation_summary + " The scope of each finding remains separate; no general clinical efficacy is established. No production or delivery exposure is established.",
                    "scientific_status": "no_supported_benefit" if negative_only else "mixed_evidence", "findings": findings,
                    "unknowns": ["SYNTHETIC strain production and exposure unknown", "SYNTHETIC no animal-to-human or adjunct-to-monotherapy extrapolation"],
                    "next_questions": ["SYNTHETIC what independent measurement resolves the remaining scope gap?"]}],
                "unresolved_questions": []}
        if title == "ReviewDraft":
            return {"reviews": [{"evidence_id": e["evidence_id"], "verdict": "supported_by_source",
                                  "reason": "SYNTHETIC independent literal-source semantic review"} for e in payload["evidence"]],
                "target_links": [], "molecule_links": [], "candidate_roles": [{"name": c["name"], "role": "unknown",
                    "role_supported": False, "evidence_ids": [], "reason": "SYNTHETIC role not established"} for c in payload["candidates"]]}
        if title == "CandidateAssessmentReviewDraft":
            if self.review_barrier:
                self.review_barrier.wait(timeout=5)
            reviews = []
            for item in payload["candidate_evaluations"]:
                draft = item["draft"]
                review = {key: item[key] for key in ("assessment_id", "version", "candidate_id", "entity_id",
                    "identity_revision", "identity_fingerprint", "task_fingerprint", "conclusion_fingerprint", "evidence_fingerprint")}
                review.update(review_id="synthetic_review:" + item["assessment_id"], verdict="withhold" if self.reject else "accept",
                    reason="SYNTHETIC independent candidate conclusion review; not medical validation",
                    item_reviews=[{"finding_id": finding["finding_id"], "verdict": "withhold" if self.reject else "accept",
                        "evidence_ids": finding["evidence_ids"],
                        **({"applicability": finding["applicability"]} if "applicability" in finding else {}),
                        "reason": "SYNTHETIC same-entity same-scope observation"} for finding in draft["findings"]],
                    supporting_evidence_ids=draft["supporting_evidence_ids"], opposing_evidence_ids=draft["opposing_evidence_ids"])
                if self.tamper == "version":
                    review["version"] += 1
                if self.tamper == "identity_during_review":
                    self.co.run.candidates[0].identity_revision += 1
                reviews.append(review)
            return {"reviews": reviews}
        raise AssertionError("Unexpected optional hypothesis invocation in core-only fixture: " + title)


class AssessmentWorker:
    def __init__(self, settings, controller):
        self.settings, self.control = settings, controller
        self.calls = self.cache_hits = 0
    def complete(self, system, user, schema):
        self.calls += 1
        controller = self.control
        title, payload = schema["title"], json.loads(user)
        row = {"schema": title, "payload": payload, "input_bytes": len(serialized_request(self.settings, system, user, schema).encode()),
               "schema_sha256": hashlib.sha256(json.dumps(strict_schema(schema), sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
               "user_bytes": len(user.encode()),
               "started": controller.clock.monotonic(), "deadline": self.deadline,
               "global_deadline": controller.co.ledger.deadline, "inflight_bytes": controller.co.ledger.snapshot()["output_bytes_inflight"]}
        controller.calls.append(row)
        if title == "SynthesisDraft":
            controller.boundaries.append({"remaining_seconds": controller.co.ledger.deadline - controller.clock.monotonic(),
                "reserved_deadline_seconds": controller.co.ledger.deadline - self.deadline,
                # R3 migration: observe actual dispatch state, independently of
                # the reservation helper whose 60-second behavior is tested.
                "prepared_identities": [c.model_dump(mode="json") for c in controller.co.run.candidates],
                "identity_sources": [s.model_dump(mode="json") for s in controller.co.run.sources
                    if s.provider == "pubchem" and s.evidence_scope == "database"]})
        if title == controller.timeout_stage:
            controller.clock.set(self.deadline)
            raise TimeoutError("SYNTHETIC provider respected stage deadline")
        controller.clock.advance(controller.delays.get(title, 0))
        if title == controller.late_stage:
            controller.clock.set(controller.co.ledger.deadline + .001)
        result = controller.response(title, payload)
        if (title == "SynthesisDraft" and not hasattr(controller, "wire_mode")
                and "CandidateAssessmentProposal" not in schema.get("$defs", {})):
            # FINAL_GOLD contract migration: the old fixture's scientific
            # choices stay identical, but new calls must now send v2.
            # Explicit R3 legacy-wire negative controls are not converted.
            for proposal in result["assessment_proposals"]:
                evidence = {e["key"]: e for e in result["evidence"]}
                for finding in proposal["findings"]:
                    assert len(finding["evidence_keys"]) == 1
                    item = evidence[finding["evidence_keys"][0]]
                    assert finding["statement"] == source_observation_statement(item["source_id"], item["quote"])
                    assert finding["scope"] == {k: item[k] for k in ("study_system", "condition", "endpoint")}
                proposal["schema_version"] = 2
                proposal["findings"] = [
                    {"finding_key": f["finding_key"], "evidence_key": f["evidence_keys"][0],
                     "projection": "source_observation"} for f in proposal["findings"]
                ]
        if title in {"SynthesisDraft", "ReviewDraft"}:
            from scientist import research
            model = getattr(research, title)
        elif title == "CandidateAssessmentReviewDraft":
            from scientist import assessment_models
            model = getattr(assessment_models, title)
        else:
            from scientist import coscientist
            model = getattr(coscientist, title)
        result = model.model_validate(result).model_dump(mode="json")
        if title == "SynthesisDraft" and controller.target_output_bytes:
            result["evidence"][0]["limitations"].append("")
            padding = controller.target_output_bytes - len(json.dumps(result, ensure_ascii=False).encode())
            assert padding >= 0
            result["evidence"][0]["limitations"][-1] = "x" * padding
        jsonschema.validate(result, strict_schema(schema))
        row["returned"] = controller.clock.monotonic()
        row["output_bytes"] = len(json.dumps(result, ensure_ascii=False).encode())
        return result


def install_chain_fixture(monkeypatch, clock, *, boundary_delay=HISTORICAL_RETRIEVAL_SECONDS + BOUNDARY_OVERHEAD_SECONDS,
                          negative_only=False):
    from scientist import background
    from scientist.coscientist import CoScientistEngine
    from scientist.research_flow import ResearchFlow
    monkeypatch.setattr("scientist.budgets.time.monotonic", clock.monotonic)
    real_load, real_inventory, real_init = background.load_background_package, ResearchFlow.inventory, CoScientistEngine.__init__
    def load(root, brief):
        if brief.background_package_id != "synthetic_assessment_v1":
            return real_load(root, brief)
        assert brief.existing_candidates == [SENTINEL_NAME] and brief.disease == "SYNTHETIC marker syndrome"
        records = sentinel_sources(negative_only=negative_only)
        return BackgroundPackage(sources=records, identities=[background_row(smiles="C[C@@H](O)C(=O)O")],
            receipt={"package_id": brief.background_package_id, "version": "SYNTHETIC1", "manifest_sha256": "a" * 64,
                "status": "verified_background_reuse", "task_scope_sha256": background_task_scope(brief), "records": len(records)})
    def inventory(flow):
        result = real_inventory(flow)
        clock.advance(boundary_delay)
        return result
    def initialize(engine, legacy):
        real_init(engine, legacy)
        legacy.llm_factory.__self__.co = engine
    monkeypatch.setattr(background, "load_background_package", load)
    monkeypatch.setattr(ResearchFlow, "inventory", inventory)
    monkeypatch.setattr(CoScientistEngine, "__init__", initialize)


def run_chain(tmp_path, clock, *, public=False, budget_changes=None, negative_only=False, **options):
    brief, sources = public_fixture() if public else (sentinel_brief(), sentinel_sources(negative_only=negative_only))
    if budget_changes:
        brief.budget = brief.budget.model_copy(update=budget_changes)
    store = ProjectStore(tmp_path / "SYNTHETIC_assessment_chain")
    project = store.create_project("SYNTHETIC complete candidate assessment chain")
    created = store.create_run(project["project_id"], brief)
    control = AssessmentController(brief, sources, clock, **options)
    offline = OfflineSources()
    engine = ResearchEngine(store, ScientistSettings(workspace=str(store.root),
        llm={"backend": "codex", "model": "gpt-6.1-sol"}), sources=offline, llm_factory=control.factory)
    result = engine.execute(project["project_id"], created.run_id, prepare_targets=False)
    return result, control, store, offline


def valid_evaluations(run):
    from scientist.assessment_review import public_validated_evaluations
    return public_validated_evaluations(run)


def assert_accounting(run, control):
    assert run.usage["llm_calls"] == len(control.calls)
    assert run.co_scientist.budget["actual_llm_calls"] == len(control.calls)
    assert run.co_scientist.budget["llm_calls"] == len(control.calls)
    assert run.usage["input_tokens_reserved"] == sum(call["input_bytes"] for call in control.calls)
    assert run.usage["input_tokens_reserved"] <= run.brief.budget.max_input_tokens
    assert run.usage["output_tokens_reserved"] <= run.brief.budget.max_output_tokens
    assert run.usage["output_bytes_inflight"] == 0
    assert run.usage["http_attempts"] == run.usage["tool_calls"] == 0
    assert not run.target_packages and not run.targets


@pytest.mark.parametrize("public", [False, True])
def test_full_production_chain_finishes_independent_assessment_after_long_anchor_without_hypotheses(tmp_path, monkeypatch, public):
    clock = ThreadClock()
    install_chain_fixture(monkeypatch, clock)
    run, control, _, offline = run_chain(tmp_path, clock, public=public)
    titles = [call["schema"] for call in control.calls]
    assert titles == ["AnchorDraft", "SynthesisDraft", "ReviewDraft", "CandidateAssessmentReviewDraft"], run.errors
    assert len(valid_evaluations(run)) == 1, run.errors
    assert run.usage["candidate_assessment_status"] == "completed_review"
    assert run.usage["hypothesis_expansion_status"] == "not_run"
    assert run.status.value == "partial" and not run.co_scientist.hypotheses and not run.co_scientist.reviews and not run.co_scientist.contracts
    assert control.boundaries[0]["remaining_seconds"] == pytest.approx(625.627)
    boundary = control.boundaries[0]
    assert boundary["reserved_deadline_seconds"] == pytest.approx(2 * ANCHOR_SECONDS + 5)
    assert len(boundary["prepared_identities"]) == 1
    prepared = boundary["prepared_identities"][0]
    assert prepared["identity_status"] == "resolved" and prepared["identity_revision"] == 1
    observation = next(o for o in prepared["identity_history"]
        if o.get("origin") == "background_identity_reuse" and o.get("application_status") == "applied")
    assert observation["acquisition"] == "background_reuse"
    proof = next(s for s in boundary["identity_sources"] if s["source_id"] == "pubchem:" + prepared["pubchem_cid"])
    assert hashlib.sha256(proof["text"].encode()).hexdigest() == observation["source_sha256"]
    prop = json.loads(proof["text"])["PropertyTable"]["Properties"][0]
    assert str(prop["CID"]) == prepared["pubchem_cid"] and prop["InChIKey"] == prepared["inchikey"]
    from rdkit import Chem
    assert Chem.MolToInchiKey(Chem.MolFromSmiles(prepared["smiles"])) == prepared["inchikey"]
    assert any(o.get("core_preparation") and o.get("run_id") == run.run_id
        and o.get("status") == "applied" for o in prepared["identity_history"])
    assert all(getattr(run.candidates[0], key) == prepared[key] for key in (
        "candidate_id", "entity_id", "identity_revision", "smiles", "inchikey", "pubchem_cid", "identity_source_ids"))
    assert run.candidates[0].identity_status == "resolved" and run.candidates[0].identity_revision == 1
    assert run.candidates[0].decision == "hold" and "user_supplied" in run.candidates[0].routes
    assert not offline.online_queries
    assert {e.polarity for e in run.evidence} >= {"supports", "mixed", "opposes"}
    third = control.calls[-1]["payload"]
    assert all(item["identity_revision"] == 1 for item in third["candidate_evaluations"])
    assert len(third["accepted_evidence"]) == len(control.rows)
    accepted = {e.quote: e for e in run.evidence}
    assert len(accepted) == len(control.rows)
    for row in control.rows:
        item = accepted[row["quote"]]
        assert item.claim == row["claim"] and item.polarity == row["polarity"]
        assert all(getattr(item, key) == row[key] for key in ("study_system", "condition", "endpoint"))
    original_sources = {source.source_id: source for source in control.sources}
    assert all(s.text == original_sources[s.source_id].text for s in run.sources if s.source_id in original_sources)
    final = valid_evaluations(run)[0]
    assert set(final["opposing_evidence_ids"]) == {e.evidence_id for e in run.evidence if e.polarity in {"mixed", "opposes"}}
    if public:
        assert hashlib.sha256((ROOT / PUBLIC_RUN).read_bytes()).hexdigest() == PUBLIC_SHA
        assert len(run.evidence) == 6
    else:
        serialized = json.dumps([call["payload"] for call in control.calls], ensure_ascii=False)
        assert "NAC" not in serialized and "Wet Lab" not in serialized and "nac_asd_v1" not in serialized
    assert_accounting(run, control)


@pytest.mark.parametrize("options", [{"reject": True}, {"tamper": "version"}, {"tamper": "identity_during_review"},
    {"timeout_stage": "ReviewDraft"}, {"timeout_stage": "CandidateAssessmentReviewDraft"},
    {"late_stage": "CandidateAssessmentReviewDraft"}, {"target_output_bytes": 12001}])
def test_complete_chain_failures_keep_partial_without_fabricated_final_review(tmp_path, monkeypatch, options):
    clock = ThreadClock()
    install_chain_fixture(monkeypatch, clock)
    run, control, _, _ = run_chain(tmp_path, clock, **options)
    assert run.status.value == "partial" and run.sources
    assert not valid_evaluations(run)
    assert not run.co_scientist.hypotheses and not run.co_scientist.contracts
    assert run.usage["candidate_assessment_status"] != "completed_review"
    assert_accounting(run, control)


@pytest.mark.parametrize("budget", [{"max_llm_calls": 3}, {"max_input_tokens": 30000}, {"max_output_tokens": 30000}])
def test_small_shared_budget_never_turns_a_draft_into_an_independently_reviewed_assessment(tmp_path, monkeypatch, budget):
    clock = ThreadClock()
    install_chain_fixture(monkeypatch, clock)
    run, control, _, _ = run_chain(tmp_path, clock, budget_changes=budget)
    assert run.status.value == "partial" and run.sources
    assert not valid_evaluations(run)
    assert run.co_scientist.budget_failures
    assert_accounting(run, control)


def test_near_deadline_blocks_before_evidence_worker_without_calling_it(tmp_path, monkeypatch):
    clock = ThreadClock()
    install_chain_fixture(monkeypatch, clock, boundary_delay=500)
    run, control, _, _ = run_chain(tmp_path, clock)
    assert [call["schema"] for call in control.calls] == ["AnchorDraft"]
    assert run.status.value == "partial" and not run.evidence and not valid_evaluations(run)
    assert any(f.get("blocked_before_provider") for f in run.co_scientist.budget_failures)
    assert_accounting(run, control)


def test_legal_output_growth_is_remeasured_and_negative_scope_remains_in_third_review(tmp_path, monkeypatch):
    clock = ThreadClock()
    install_chain_fixture(monkeypatch, clock)
    run, control, _, _ = run_chain(tmp_path, clock, target_output_bytes=11999)
    assert len(valid_evaluations(run)) == 1, run.errors
    synthesis = next(c for c in control.calls if c["schema"] == "SynthesisDraft")
    assert synthesis["output_bytes"] == 11999
    third = control.calls[-1]["payload"]
    assert {e["polarity"] for e in third["accepted_evidence"]} >= {"mixed", "opposes"}
    assert any("rats" in e["study_system"] for e in third["accepted_evidence"])
    assert any(len(item) > 1000 for e in third["accepted_evidence"] for item in e["limitations"])
    assert_accounting(run, control)


def test_latency_growth_rechecks_the_actual_next_stage_without_spending_reserved_calls(tmp_path, monkeypatch):
    clock = ThreadClock()
    install_chain_fixture(monkeypatch, clock)
    run, control, _, _ = run_chain(tmp_path, clock, delays={"SynthesisDraft": 220})
    assert [call["schema"] for call in control.calls] == ["AnchorDraft", "SynthesisDraft"]
    assert control.calls[-1]["returned"] < control.calls[-1]["deadline"]
    assert run.status.value == "partial" and not run.evidence and not valid_evaluations(run)
    failure = run.co_scientist.budget_failures[-1]
    assert failure["blocked_before_provider"] and failure["required_seconds"] == 220
    assert failure["remaining_seconds"] == pytest.approx(405.627)
    assert_accounting(run, control)


@pytest.mark.parametrize("public", [False, True])
def test_every_core_stage_can_take_the_observed_long_anchor_latency(tmp_path, monkeypatch, public):
    clock = ThreadClock()
    install_chain_fixture(monkeypatch, clock)
    run, control, _, _ = run_chain(tmp_path, clock, public=public, delays={
        "SynthesisDraft": ANCHOR_SECONDS, "ReviewDraft": ANCHOR_SECONDS,
        "CandidateAssessmentReviewDraft": ANCHOR_SECONDS})
    assert len(control.calls) == 4 and len(valid_evaluations(run)) == 1, run.errors
    assert all(call["returned"] < call["deadline"] for call in control.calls)
    assert control.co.ledger.deadline - clock.monotonic() == pytest.approx(128.653)
    assert run.status.value == "partial" and not run.co_scientist.hypotheses
    assert_accounting(run, control)


def test_all_negative_sources_can_complete_a_negative_independent_assessment(tmp_path, monkeypatch):
    clock = ThreadClock()
    install_chain_fixture(monkeypatch, clock, negative_only=True)
    run, control, _, _ = run_chain(tmp_path, clock, negative_only=True)
    assert [call["schema"] for call in control.calls] == ["AnchorDraft", "SynthesisDraft", "ReviewDraft", "CandidateAssessmentReviewDraft"]
    final, = valid_evaluations(run)
    assert final["scientific_status"] == "no_supported_benefit"
    assert not final["supporting_evidence_ids"] and len(final["opposing_evidence_ids"]) == 1
    assert "favorable" not in final["conclusion"]
    assert run.usage["candidate_assessment_status"] == "completed_review"
    assert run.status.value == "partial" and not run.co_scientist.hypotheses
    assert run.candidates[0].decision == "hold"
    assert_accounting(run, control)


def test_two_concurrent_complete_runs_keep_rejected_reviews_and_ledgers_isolated(tmp_path, monkeypatch):
    clock = ThreadClock()
    install_chain_fixture(monkeypatch, clock)
    barrier = threading.Barrier(2)
    def execute(index):
        return run_chain(tmp_path / str(index), clock, reject=True, review_barrier=barrier)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, range(2)))
    assert len({run.run_id for run, *_ in results}) == 2
    for run, control, _, _ in results:
        assert run.status.value == "partial" and not valid_evaluations(run)
        assert [call["schema"] for call in control.calls][-1] == "CandidateAssessmentReviewDraft"
        assert not run.co_scientist.contracts
        assert_accounting(run, control)

