"""Independent SYNTHETIC alias controls and actual production-chain budget replay.

Pure identity counterexamples are mechanical tests, not model judgments. The
chain uses the original transparent workers and original limits without changes.
"""
from copy import deepcopy

import pytest
from test_candidate_conclusion_review import (
    assessment_case,
    review_response,
    synthetic_operation,
)
from test_delivery_r2_chain import (
    ANCHOR_SECONDS,
    ThreadClock,
    assert_accounting,
    install_chain_fixture,
    no_live_transport_or_private_files,
    run_chain,
    valid_evaluations,
)

from scientist.assessment_review import (
    apply_assessment_review,
    assessment_review_payload,
    bind_assessment_identities,
    public_validated_evaluations,
    store_assessment_proposals,
)
from scientist.research_flow import source_observation_statement

ALPHA = "Sentinel alpha compound"
BETA = "Sentinel beta compound"
CODE_RESULT = "SC changed the measured biomarker M compared with the recorded placebo group."
METHODS = "SYNTHETIC Methods: 31 human children were enrolled and randomized. "


@pytest.fixture(autouse=True)
def block_live_and_private(monkeypatch):
    no_live_transport_or_private_files.__wrapped__(monkeypatch)


def alias_case(quote, *, prefix="", suffix="", title="SYNTHETIC coded compound trial", claim=None, metadata=None):
    run, proposal, keys = assessment_case(polarity="supports")
    first = run.candidates[0]
    first.name = ALPHA
    first.aliases = ["SC"]
    second = first.model_copy(deep=True, update={"candidate_id": "mol_synthetic_beta",
        "entity_id": "entity_synthetic_beta", "name": BETA, "smiles": "CC(=O)O",
        "inchikey": "QTBSBXVTEAMEQO-UHFFFAOYSA-N", "pubchem_cid": "176"})
    run.candidates.append(second)
    run.brief.existing_candidates = [first.name, second.name]
    source, evidence = run.sources[0], run.evidence[0]
    source.title = title
    source.text = METHODS + prefix + quote + suffix
    source.metadata = metadata or {}
    evidence.quote = quote
    evidence.claim = claim or "The synthetic source reports the recorded biomarker observation for SC."
    proposals = []
    for candidate in run.candidates:
        item = deepcopy(proposal)
        item["candidate_name"] = candidate.name
        item["findings"][0]["statement"] = source_observation_statement(source.source_id, quote)
        proposals.append(item)
    return run, proposals, keys


def review_alias_case(run, proposals, keys):
    source_before = [source.model_dump() for source in run.sources]
    evidence_before = [evidence.model_dump() for evidence in run.evidence]
    store_assessment_proposals(run, proposals, keys)
    ready = bind_assessment_identities(run)
    payload = assessment_review_payload(run)
    response = review_response(run)
    for index, item in enumerate(response["reviews"]):
        item["review_id"] = f"SYNTHETIC_unique_alias_review_{index}"
    apply_assessment_review(run, response, operation_id=synthetic_operation(run))
    assert [source.model_dump() for source in run.sources] == source_before
    assert [evidence.model_dump() for evidence in run.evidence] == evidence_before
    return ready, payload, public_validated_evaluations(run)


@pytest.mark.parametrize("options", [
    {},
    {"title": f"SYNTHETIC {ALPHA} background discussion"},
    {"prefix": f"SYNTHETIC Background: {ALPHA} was discussed in another context. "},
    {"prefix": f"SYNTHETIC Background: {ALPHA} (SC) was discussed in another context. "},
    {"prefix": f"SYNTHETIC Background: {ALPHA} was discussed in another context. ",
     "claim": f"The synthetic source reports the recorded biomarker observation for {ALPHA}."},
    {"prefix": f"SYNTHETIC Background: {ALPHA} and {BETA} were discussed. "},
    {"prefix": "SYNTHETIC Background: PubChem CID:702 is recorded in an index. "},
    {"metadata": {"candidate_id": "mol_synthetic", "entity_id": "entity_synthetic",
                  "identity": {"SC": ALPHA, "pubchem_cid": "702"}}},
    {"prefix": f"SYNTHETIC {ALPHA} (SC) was documented. " + "SYNTHETIC unrelated context. " * 20},
    {"prefix": f"SYNTHETIC {ALPHA} (SC) was administered. ",
     "suffix": f" SYNTHETIC {BETA} (SC) was also administered."},
])
def test_ambiguous_code_is_not_resolved_by_background_title_claim_metadata_or_conflicting_definition(options):
    run, proposals, keys = alias_case(CODE_RESULT, **options)
    ready, payload, final = review_alias_case(run, proposals, keys)
    assert not ready and not payload["candidate_evaluations"] and not final


def test_two_explicit_entities_in_one_quote_do_not_assign_same_effect_to_both():
    quote = f"{ALPHA} and {BETA} were described; SC changed the measured biomarker M."
    run, proposals, keys = alias_case(quote)
    ready, _payload, final = review_alias_case(run, proposals, keys)
    assert not ready and not final


def test_full_name_and_identifier_for_different_entities_conflict_in_same_observation():
    quote = f"{ALPHA} (PubChem CID:176) changed the measured biomarker M compared with placebo."
    run, proposals, keys = alias_case(quote)
    ready, _payload, final = review_alias_case(run, proposals, keys)
    assert not ready and not final


@pytest.mark.parametrize("malformed", ["702x", "702-1", "702.0", "7020"])
def test_canonical_identifier_requires_a_complete_token_not_a_matching_numeric_prefix(malformed):
    quote = f"PubChem CID:{malformed} changed the measured biomarker M compared with placebo."
    run, proposals, keys = alias_case(quote)
    ready, _payload, final = review_alias_case(run, proposals, keys)
    assert not ready and not final


@pytest.mark.parametrize("counts", ["SC = 31, placebo = 30", "SC (n = 31) and placebo (n = 30)"])
def test_sample_size_statistics_do_not_redefine_a_single_candidate_identity(counts):
    run, proposals, keys = alias_case(CODE_RESULT, prefix=(
        f"SYNTHETIC {ALPHA} (SC) was administered. Participants were allocated to {counts}. "))
    run.candidates = run.candidates[:1]
    run.brief.existing_candidates = [ALPHA]
    ready, _payload, final = review_alias_case(run, proposals[:1], keys)
    assert len(ready) == len(final) == 1
    assert final[0]["entity_id"] == "entity_synthetic"


@pytest.mark.parametrize("quote,prefix,expected", [
    (f"{ALPHA} changed the measured biomarker M compared with placebo.",
     f"SYNTHETIC Background: {BETA} was also described. ", "entity_synthetic"),
    (f"{BETA} changed the measured biomarker M compared with placebo.",
     f"SYNTHETIC Background: {ALPHA} was also described. ", "entity_synthetic_beta"),
    ("PubChem CID:702 changed the measured biomarker M compared with placebo.", "", "entity_synthetic"),
    ("SC (PubChem CID:702) changed the measured biomarker M compared with placebo.", "", "entity_synthetic"),
    ("PubChem CID:176 changed the measured biomarker M compared with placebo.", "", "entity_synthetic_beta"),
    ("LFQSCWFLJHTTHZ-UHFFFAOYSA-N changed the measured biomarker M compared with placebo.", "", "entity_synthetic"),
    (CODE_RESULT, f"SYNTHETIC {ALPHA} (SC) was administered in this study. ", "entity_synthetic"),
    (CODE_RESULT, f"SYNTHETIC SC ({BETA}) was administered in this study. ", "entity_synthetic_beta"),
])
def test_unambiguous_literal_identity_or_local_explicit_mapping_can_bind_one_entity(quote, prefix, expected):
    run, proposals, keys = alias_case(quote, prefix=prefix)
    ready, payload, final = review_alias_case(run, proposals, keys)
    assert len(ready) == 1 and [item["entity_id"] for item in final] == [expected], [r.validation_errors for r in run.candidate_evaluations]
    row, = payload["candidate_evaluations"]
    candidate = next(c for c in run.candidates if c.entity_id == expected)
    identity = row["identity"]
    assert identity["name"] == candidate.name and identity["aliases"] == candidate.aliases
    for key in ("candidate_id", "entity_id", "identity_revision", "identity_fingerprint"):
        assert identity[key] == row[key]
    assert identity["pubchem_cid"] == candidate.pubchem_cid
    assert identity["inchikey"] == candidate.inchikey and identity["smiles"] == candidate.smiles
    binding, = row["evidence_bindings"]
    assert binding["entity_id"] == expected
    assert binding["evidence_id"] == run.evidence[0].evidence_id
    assert binding["source_id"] == run.sources[0].source_id
    assert binding["basis"] in {"unique_quote_name", "unique_quote_identifier", "explicit_source_definition"}
    for span in binding["definition_spans"]:
        assert run.sources[0].text[span["start"]:span["end"]] == span["text"]


def test_actual_public_chain_accounts_for_new_identity_mapping_with_original_long_latency_budget(tmp_path, monkeypatch):
    clock = ThreadClock()
    install_chain_fixture(monkeypatch, clock)
    run, control, _, _ = run_chain(tmp_path, clock, public=True, delays={
        "SynthesisDraft": ANCHOR_SECONDS, "ReviewDraft": ANCHOR_SECONDS,
        "CandidateAssessmentReviewDraft": ANCHOR_SECONDS})
    assert [call["schema"] for call in control.calls] == ["AnchorDraft", "SynthesisDraft", "ReviewDraft", "CandidateAssessmentReviewDraft"]
    assert len(valid_evaluations(run)) == 1 and len(run.evidence) == 6
    assert run.status.value == "partial" and not run.co_scientist.hypotheses
    assert control.boundaries[0]["remaining_seconds"] == pytest.approx(625.627)
    assert control.co.ledger.deadline - clock.monotonic() == pytest.approx(128.653)
    third = control.calls[-1]
    row, = third["payload"]["candidate_evaluations"]
    assert row["identity"]["name"] == run.candidates[0].name
    assert row["identity"]["identity_fingerprint"] == row["identity_fingerprint"]
    assert len(row["evidence_bindings"]) == 5
    assert run.brief.budget.max_llm_calls == 16 and run.brief.budget.timeout_seconds == 840
    assert run.brief.budget.max_input_tokens == 300000 and run.brief.budget.max_output_tokens == 96000
    assert_accounting(run, control)

