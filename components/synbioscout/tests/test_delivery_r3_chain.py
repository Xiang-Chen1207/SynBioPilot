"""SYNTHETIC complete production R3 chain, never a discarded real response.

Only the explicitly named latest public run/request may be read for kind=public.
Other kinds are wholly artificial. All model/HTTP transports are transparent
stubs; production orchestration, identity verification and reviews really run.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import test_delivery_r2_chain as old
from test_core_identity_preparation import background_row
from test_delivery_r3_compact import legacy_equivalent, synthetic_17188_pair, synthetic_sources

from scientist.background import BackgroundPackage, background_task_scope
from scientist.compact_synthesis import expand_synthesis_draft
from scientist.identity_prepare import core_identity_ready
from scientist.models import ResearchBrief, ScientistSettings, SourceRecord
from scientist.research import ResearchEngine, SynthesisDraft
from scientist.sources import IdentityResolutionError
from scientist.store import ProjectStore, safe_id, write_json

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_RUN = "runs/scientist/projects/p_ab2fce59e2054907/research/r_45f13124cb444f2f/run.json"
PUBLIC_SHA = "5917e2b9e9366acd6a4eccb84fdf5a81c13ad8e935e637bcf24b191e1ab518d9"
REQUEST_FILE = "docs/nac_asd_iteration/SIM_DELIVERY_NAC_R2_PREFLIGHT.json"
REQUEST_SHA = old.REQUEST_SHA  # canonical request object, not the whole preflight file
ANCHOR_SECONDS, CORE_SECONDS, BOUNDARY_SECONDS = 133.148, 217.101, .015
SCHEMAS = ["AnchorDraft", "SynthesisDraft", "ReviewDraft", "CandidateAssessmentReviewDraft"]
guard = old.no_live_transport_or_private_files
ThreadClock = old.ThreadClock
valid_evaluations = old.valid_evaluations


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    guard.__wrapped__(monkeypatch)


def public_fixture():
    def read(relative):
        assert relative in {PUBLIC_RUN, REQUEST_FILE}
        assert not Path(relative).is_absolute() and not {"..", "inference"}.intersection(Path(relative).parts)
        target = ROOT
        for part in Path(relative).parts:
            target /= part
            assert not target.is_symlink()
        assert target.resolve().is_relative_to(ROOT)
        return target.read_bytes()
    raw = read(PUBLIC_RUN)
    assert hashlib.sha256(raw).hexdigest() == PUBLIC_SHA
    saved = json.loads(raw)
    request = json.loads(read(REQUEST_FILE))["request"]
    assert hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == REQUEST_SHA
    assert request["brief"] == saved["brief"] and request["prepare_targets"] is False
    assert not saved["evidence"] and not saved["candidates"] and len(saved["sources"]) == 6
    return ResearchBrief.model_validate(request["brief"]), [SourceRecord.model_validate(s) for s in saved["sources"]]


def compact_from_legacy(value):
    result = copy.deepcopy(value)
    for proposal in result["assessment_proposals"]:
        proposal["schema_version"] = 2
        assert all(len(f["evidence_keys"]) == 1 for f in proposal["findings"])
        proposal["findings"] = [{"finding_key": f["finding_key"], "evidence_key": f["evidence_keys"][0],
            "projection": "source_observation"} for f in proposal["findings"]]
    return result


class R3Controller(old.AssessmentController):
    def __init__(self, brief, sources, clock, *, kind="sentinel", wire_mode="compact", invalid_reference="", **options):
        super().__init__(brief, old.sentinel_sources() if kind == "sized" else sources, clock,
            delays={"AnchorDraft": ANCHOR_SECONDS, "SynthesisDraft": CORE_SECONDS,
                    "ReviewDraft": CORE_SECONDS, "CandidateAssessmentReviewDraft": CORE_SECONDS,
                    **options.pop("delays", {})}, **options)
        self.kind, self.sources, self.wire_mode = kind, sources, wire_mode
        self.invalid_reference = invalid_reference
        self.identity_timeline, self.verification_calls = [], []
        self.synthesis_wire = self.expected_expanded = None
        if kind == "sized":
            self.artificial_compact, self.artificial_legacy = synthetic_17188_pair()
            self.rows = copy.deepcopy(self.artificial_compact["evidence"])

    def factory(self, settings, directory, max_calls):
        return R3Worker(settings, self)

    def response(self, title, payload):
        if title != "SynthesisDraft":
            return super().response(title, payload)
        if self.kind == "sized":
            compact = copy.deepcopy(self.artificial_compact)
        else:
            compact = compact_from_legacy(super().response(title, payload))
        if self.invalid_reference == "missing":
            compact["assessment_proposals"][0]["findings"][0]["evidence_key"] = "SYNTHETIC_missing"
        elif self.invalid_reference == "duplicate":
            compact["assessment_proposals"][0]["findings"][1]["evidence_key"] = compact["assessment_proposals"][0]["findings"][0]["evidence_key"]
        elif self.invalid_reference == "foreign_candidate":
            compact["assessment_proposals"][0]["candidate_name"] = "SYNTHETIC foreign entity"
        return legacy_equivalent(compact) if self.wire_mode == "legacy" else compact


class R3Worker(old.AssessmentWorker):
    def complete(self, system, user, schema):
        c = self.control
        identity = [{"candidate_id": x.candidate_id, "entity_id": x.entity_id, "identity_status": x.identity_status,
            "identity_revision": x.identity_revision, "identity_source_ids": list(x.identity_source_ids)} for x in c.co.run.candidates]
        ready = core_identity_ready(c.co.run)
        try:
            value = super().complete(system, user, schema)
        finally:
            row = c.calls[-1]
            row["identity_ready_before"] = ready
            row["identities_before"] = identity
            row["actual_elapsed_seconds"] = c.clock.monotonic() - row["started"]
        if schema["title"] == "SynthesisDraft":
            c.synthesis_wire = copy.deepcopy(value)
            if not c.invalid_reference:
                c.expected_expanded = SynthesisDraft.model_validate(
                    legacy_equivalent(value) if c.wire_mode == "compact" else value).model_dump(mode="json")
        return value


class OfflineR3Sources(old.OfflineSources):
    def __init__(self, clock):
        super().__init__()
        self.clock, self.identity_queries = clock, []
    def resolve_molecule(self, name):
        self.identity_queries.append(name)
        if self.http.before_request:
            self.http.before_request()
        self.http.request_count += 1
        self.clock.advance(1)
        raise IdentityResolutionError("SYNTHETIC exact identity lookup empty", status="empty")


def install_r3(monkeypatch, control, *, identity_mode="verified", boundary_delay=BOUNDARY_SECONDS, identity_seconds=0):
    from scientist import background, identity_prepare
    from scientist import coscientist as co
    from scientist.research_flow import ResearchFlow
    monkeypatch.setattr("scientist.budgets.time.monotonic", control.clock.monotonic)
    real_load, real_inventory, real_init = background.load_background_package, ResearchFlow.inventory, co.CoScientistEngine.__init__
    real_prepare, real_verify = co.prepare_core_identities, identity_prepare.verified_background_identity
    def load(root, brief):
        if control.kind == "public":
            package = real_load(root, brief)
        else:
            row = background_row(name="Sentinel compound", smiles="CCO", cid="702") if control.kind == "sized" else background_row(smiles="C[C@@H](O)C(=O)O")
            package = BackgroundPackage(sources=copy.deepcopy(control.sources), identities=[row],
                receipt={"package_id": brief.background_package_id, "version": "SYNTHETIC_R3",
                    "manifest_sha256": "a" * 64, "status": "verified_background_reuse",
                    "task_scope_sha256": background_task_scope(brief), "records": len(control.sources)})
        package = copy.deepcopy(package)
        if identity_mode == "missing":
            package.identities = []
        elif identity_mode == "invalid":
            package.identities[0]["source_sha256"] = "0" * 64
        elif identity_mode == "conflict":
            alternative = copy.deepcopy(package.identities[0])
            alternative["pubchem_cid"] = "999999"
            package.identities.append(alternative)
        return package
    def inventory(flow):
        value = real_inventory(flow)
        control.clock.advance(boundary_delay)
        return value
    def initialize(engine, legacy):
        real_init(engine, legacy)
        control.co = engine
    def verified(row):
        result = real_verify(row)
        control.verification_calls.append({"at": control.clock.monotonic(), "source_sha256": row["source_sha256"]})
        control.clock.advance(identity_seconds)
        return result
    def prepare(engine, rows=(), *, local_only=False):
        before = control.clock.monotonic()
        value = real_prepare(engine, rows, local_only=local_only)
        control.identity_timeline.append({"local_only": local_only, "before": before,
            "after": control.clock.monotonic(), "after_model_calls": len(control.calls),
            "current_receipt_ready": core_identity_ready(engine.run),
            "identity_statuses": [x.identity_status for x in engine.run.candidates],
            "identity_revisions": [x.identity_revision for x in engine.run.candidates]})
        return value
    monkeypatch.setattr(background, "load_background_package", load)
    monkeypatch.setattr(ResearchFlow, "inventory", inventory)
    monkeypatch.setattr(co.CoScientistEngine, "__init__", initialize)
    monkeypatch.setattr(co, "prepare_core_identities", prepare)
    monkeypatch.setattr(identity_prepare, "verified_background_identity", verified)


def run_r3_chain(tmp_path, monkeypatch, *, kind="sentinel", fixed_ids=None, identity_mode="verified",
                 boundary_delay=BOUNDARY_SECONDS, identity_seconds=0, budget_changes=None, **options):
    clock = ThreadClock()
    if kind == "public":
        brief, sources = public_fixture()
    else:
        brief = old.sentinel_brief()
        if kind == "sized":
            brief.existing_candidates = ["Sentinel compound (SC)"]
            brief.background_package_id = "synthetic_r3_assessment_v1"
            sources = synthetic_sources(synthetic_17188_pair()[0])
        else:
            sources = old.sentinel_sources()
    if budget_changes:
        brief.budget = brief.budget.model_copy(update=budget_changes)
    control = R3Controller(brief, sources, clock, kind=kind, **options)
    install_r3(monkeypatch, control, identity_mode=identity_mode, boundary_delay=boundary_delay, identity_seconds=identity_seconds)
    store = ProjectStore(tmp_path / "SYNTHETIC_R3_workspace")
    project = store.create_project("SYNTHETIC R3 complete assessment chain")
    if fixed_ids:
        pid, rid = map(safe_id, fixed_ids)
        original = store.project_dir(project["project_id"])
        destination = original.with_name(pid)
        original.rename(destination)
        project["project_id"] = pid
        write_json(destination / "project.json", project)
    created = store.create_run(project["project_id"], brief)
    if fixed_ids:
        original = store.run_dir(project["project_id"], created.run_id)
        original.rename(original.with_name(rid))
        created.run_id = rid
        store.save_run(created)
    offline = OfflineR3Sources(clock)
    engine = ResearchEngine(store, ScientistSettings(workspace=str(store.root), llm={"backend": "codex", "model": "gpt-6.1-sol"}),
        sources=offline, llm_factory=control.factory)
    run = engine.execute(project["project_id"], created.run_id, prepare_targets=False)
    return run, control, store, offline


def assert_accounting(run, control, offline):
    assert run.usage["llm_calls"] == run.co_scientist.budget["actual_llm_calls"] == len(control.calls)
    assert run.usage["input_tokens_reserved"] == sum(row["input_bytes"] for row in control.calls)
    assert run.usage["output_bytes_received"] == sum(row.get("output_bytes", 0) for row in control.calls)
    assert run.usage["output_tokens_reserved"] <= run.usage["output_bytes_received"]
    assert run.usage["input_tokens_reserved"] <= run.brief.budget.max_input_tokens
    assert run.usage["output_tokens_reserved"] <= run.brief.budget.max_output_tokens
    assert run.usage["output_bytes_inflight"] == 0
    assert run.usage["http_attempts"] == offline.http.request_count
    assert not run.targets and not run.target_packages and not offline.online_queries


def assert_complete(run, control, offline):
    assert [row["schema"] for row in control.calls] == SCHEMAS, run.errors
    assert len(valid_evaluations(run)) == 1, [r.validation_errors for r in run.candidate_evaluations]
    assert run.status.value == "partial" and not run.co_scientist.hypotheses and not run.co_scientist.contracts
    assert run.usage["candidate_assessment_status"] == "completed_review"
    assert control.identity_timeline[0]["local_only"] and control.identity_timeline[0]["after_model_calls"] == 1
    assert control.identity_timeline[0]["current_receipt_ready"]
    assert all(row["identity_ready_before"] for row in control.calls[1:])
    assert len(control.verification_calls) == 1
    expanded, receipt = expand_synthesis_draft(control.synthesis_wire)
    assert expanded.model_dump(mode="json") == control.expected_expanded
    assert json.loads((control.co.directory / "synthesis_draft.json").read_text()) == control.expected_expanded
    assert run.usage["synthesis_expansion"] == receipt
    assert receipt["wire_bytes"] == control.calls[1]["output_bytes"]
    assert [e.quote for e in run.evidence] == [e["quote"] for e in control.synthesis_wire["evidence"]]
    third = control.calls[-1]["payload"]
    assert len(third["accepted_evidence"]) == len(run.evidence)
    assert {e["polarity"] for e in third["accepted_evidence"]} >= {"mixed", "opposes"}
    original = {s.source_id: s.text for s in control.sources}
    assert all(s.text == original[s.source_id] for s in run.sources if s.source_id in original)
    assert_accounting(run, control, offline)


@pytest.mark.parametrize("kind", ["public", "sentinel", "sized"])
def test_complete_production_chain_with_observed_latencies_and_actual_early_identity(tmp_path, monkeypatch, kind):
    run, control, _, offline = run_r3_chain(tmp_path, monkeypatch, kind=kind)
    assert_complete(run, control, offline)
    assert control.calls[2]["global_deadline"] - control.calls[2]["started"] == pytest.approx(489.736)
    assert control.co.ledger.deadline - control.clock.monotonic() == pytest.approx(55.534)
    assert control.calls[2]["global_deadline"] - control.calls[2]["deadline"] == pytest.approx(222.101)
    assert control.boundaries[0]["reserved_deadline_seconds"] == pytest.approx(2 * ANCHOR_SECONDS + 5)
    if kind == "public":
        assert hashlib.sha256((ROOT / PUBLIC_RUN).read_bytes()).hexdigest() == PUBLIC_SHA
    if kind == "sized":
        assert run.usage["synthesis_expansion"]["wire_bytes"] == 10034
        assert run.usage["synthesis_expansion"]["expanded_bytes"] == 17188


def test_artificial_legacy_17188_is_rejected_without_scientific_ingestion(tmp_path, monkeypatch):
    run, control, _, offline = run_r3_chain(tmp_path, monkeypatch, kind="sized", wire_mode="legacy")
    assert [row["schema"] for row in control.calls] == SCHEMAS[:2]
    # FINAL_GOLD: strict new-call validation now rejects v1 before the output
    # ledger; the old 17188-byte fixture is unchanged and archived in baseline.
    assert len(json.dumps(control.artificial_legacy, ensure_ascii=False).encode()) == 17188
    assert "output_bytes" not in control.calls[-1]
    failure = run.co_scientist.budget_failures[-1]
    assert failure["reason"] == "provider_failure"  # includes schema failures
    assert not failure.get("output_limit_categories")
    assert not run.evidence and not valid_evaluations(run)
    assert "synthesis_expansion" not in run.usage
    assert_accounting(run, control, offline)


@pytest.mark.parametrize("identity_mode", ["missing", "conflict", "invalid"])
def test_unverified_identity_cannot_remove_sixty_seconds_or_enter_conclusion_review(tmp_path, monkeypatch, identity_mode):
    run, control, _, offline = run_r3_chain(tmp_path, monkeypatch, identity_mode=identity_mode)
    assert [row["schema"] for row in control.calls] == SCHEMAS[:2]
    assert not control.calls[1]["identity_ready_before"]
    assert control.boundaries[0]["reserved_deadline_seconds"] == pytest.approx(2 * ANCHOR_SECONDS + 65)
    assert not run.evidence and not valid_evaluations(run)
    assert run.co_scientist.budget_failures[-1]["reserved_seconds"] == pytest.approx(282.101)
    assert_accounting(run, control, offline)


def test_failed_required_lookup_is_simulated_bounded_and_not_a_verified_local_receipt(tmp_path, monkeypatch):
    run, control, _, offline = run_r3_chain(tmp_path, monkeypatch, identity_mode="missing",
        delays={"SynthesisDraft": 90, "ReviewDraft": 90})
    assert offline.identity_queries and not valid_evaluations(run)
    assert not core_identity_ready(run) and all(not row["current_receipt_ready"] for row in control.identity_timeline)
    assert run.usage["http_attempts"] <= 4 and run.usage["tool_calls"] <= 4
    assert_accounting(run, control, offline)


@pytest.mark.parametrize("options", [{"reject": True}, {"tamper": "version"}, {"tamper": "identity_during_review"},
    {"timeout_stage": "ReviewDraft"}, {"late_stage": "CandidateAssessmentReviewDraft"},
    {"delays": {"SynthesisDraft": 240}}, {"boundary_delay": 500},
    {"target_output_bytes": 12001}, {"budget_changes": {"max_input_tokens": 100000}},
    {"invalid_reference": "missing"}, {"invalid_reference": "duplicate"}, {"invalid_reference": "foreign_candidate"}])
def test_full_chain_failures_remain_partial_and_never_fabricate_a_reviewed_final(tmp_path, monkeypatch, options):
    run, control, _, offline = run_r3_chain(tmp_path, monkeypatch, **options)
    assert run.status.value == "partial" and not valid_evaluations(run)
    assert not run.co_scientist.hypotheses and not run.co_scientist.contracts
    assert_accounting(run, control, offline)


def test_output_boundary_and_measured_expansion_growth_are_reserved_in_next_actual_inputs(tmp_path, monkeypatch):
    run, control, _, offline = run_r3_chain(tmp_path, monkeypatch, kind="sized", target_output_bytes=12000)
    assert_complete(run, control, offline)
    receipt = run.usage["synthesis_expansion"]
    assert receipt["wire_bytes"] == 12000 and receipt["expanded_bytes"] == 19154
    assert receipt["growth_bytes"] == 7154
    assert any(len(t) > 1000 for e in control.calls[-1]["payload"]["accepted_evidence"] for t in e["limitations"])
    assert run.usage["output_tokens_reserved"] == sum(row["output_bytes"] for row in control.calls)


def test_actual_local_identity_cost_is_charged_before_remaining_work_without_refund(tmp_path, monkeypatch):
    run, control, _, offline = run_r3_chain(tmp_path, monkeypatch, identity_seconds=11)
    assert_complete(run, control, offline)
    assert control.identity_timeline[0]["after"] - control.identity_timeline[0]["before"] == 11
    assert control.co.ledger.deadline - control.clock.monotonic() == pytest.approx(44.534)


def test_fixed_synthetic_ids_are_set_before_identity_and_review_fingerprints(tmp_path, monkeypatch):
    run, control, _, offline = run_r3_chain(tmp_path, monkeypatch, kind="sized",
        fixed_ids=("p_synthetic_ui", "r_synthetic_r3_reviewed"))
    assert_complete(run, control, offline)
    assert run.project_id == "p_synthetic_ui" and run.run_id == "r_synthetic_r3_reviewed"

