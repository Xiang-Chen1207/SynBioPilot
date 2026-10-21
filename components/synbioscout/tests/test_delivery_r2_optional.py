"""SYNTHETIC optional expansion cannot erase an already reviewed core result."""
from test_delivery_r2_chain import (
    AssessmentController,
    ThreadClock,
    install_chain_fixture,
    no_live_transport_or_private_files,
    run_chain,
    valid_evaluations,
)

offline_transport = no_live_transport_or_private_files


def test_core_is_persisted_before_optional_hypothesis_failure(tmp_path, monkeypatch):
    clock = ThreadClock()
    install_chain_fixture(monkeypatch, clock)
    response = AssessmentController.response
    observed = []

    def fail_optional(self, title, payload):
        if title == "GenerationDraft":
            saved = self.co.store.load_run(self.co.run.project_id, self.co.run.run_id)
            final = valid_evaluations(saved)
            assert len(final) == 1
            assert final[0]["review_operation_id"] == "llm_004"
            observed.append(final[0]["assessment_id"])
            raise RuntimeError("SYNTHETIC optional generation failure after core save")
        return response(self, title, payload)

    monkeypatch.setattr(AssessmentController, "response", fail_optional)
    run, control, store, _ = run_chain(tmp_path, clock, delays={
        "AnchorDraft": 1, "SynthesisDraft": 1, "ReviewDraft": 1, "CandidateAssessmentReviewDraft": 1})
    assert observed and len(valid_evaluations(run)) == 1
    assert run.usage["candidate_assessment_status"] == "completed_review"
    assert run.usage["hypothesis_expansion_status"] == "started"
    assert run.status.value == "partial" and not run.co_scientist.contracts
    assert run.usage["output_bytes_inflight"] == 0
    saved = store.load_run(run.project_id, run.run_id)
    assert valid_evaluations(saved) == valid_evaluations(run)
    assert [item["schema"] for item in control.calls[:4]] == [
        "AnchorDraft", "SynthesisDraft", "ReviewDraft", "CandidateAssessmentReviewDraft"]

