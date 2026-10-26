"""SYNTHETIC FIX1 relevance/attribution controls; no model, HTTP or saved runs."""
from copy import deepcopy

import pytest
from test_candidate_conclusion_review import (
    assessment_case,
    complete_assessment,
    review_response,
    synthetic_operation,
)

from scientist.assessment_review import (
    apply_assessment_review,
    assessment_review_payload,
    bind_assessment_identities,
    public_validated_evaluations,
    store_assessment_proposals,
)
from scientist.research_flow import source_observation_statement


def append_accepted(run, polarity, *, linked, unrelated=False):
    name = "Different compound" if unrelated else "SC"
    quote = f"{name} had a newly recorded result on the synthetic endpoint in the follow-up trial."
    source = run.sources[0].model_copy(deep=True, update={"source_id": "synthetic:follow_up",
        "title": "SYNTHETIC follow-up", "text": "Methods: 40 human children were enrolled. " + quote})
    evidence = run.evidence[0].model_copy(deep=True, update={"evidence_id": "ev_follow_up", "source_id": source.source_id,
        "quote": quote, "claim": f"The synthetic source reports a new finding for {name}.", "polarity": polarity})
    run.sources.append(source)
    run.evidence.append(evidence)
    if linked:
        run.candidates[0].evidence_ids.append(evidence.evidence_id)
    return evidence


def shared_alias_case(*, quote=None, prefix="", suffix=""):
    run, proposal, keys = assessment_case()
    first = run.candidates[0]
    first.name = "Sentinel alpha compound"
    first.aliases = ["SC"]
    second = first.model_copy(deep=True, update={"candidate_id": "mol_synthetic_beta",
        "entity_id": "entity_synthetic_beta", "name": "Sentinel beta compound", "smiles": "CC(=O)O",
        "inchikey": "QTBSBXVTEAMEQO-UHFFFAOYSA-N", "pubchem_cid": "176"})
    run.candidates.append(second)
    run.brief.existing_candidates = [first.name, second.name]
    quote = quote or "SC changed the recorded biomarker but did not improve the recorded clinical endpoint."
    run.sources[0].title = "SYNTHETIC coded trial"
    run.sources[0].text = "Methods: 31 human children were enrolled and randomized. " + prefix + quote + suffix
    run.evidence[0].quote = quote
    run.evidence[0].claim = "The synthetic source reports the recorded results for SC."
    proposals = []
    for candidate in run.candidates:
        item = deepcopy(proposal)
        item["candidate_name"] = candidate.name
        item["findings"][0]["statement"] = source_observation_statement(run.sources[0].source_id, quote)
        proposals.append(item)
    return run, proposals, keys


def accept_ready(run):
    response = review_response(run)
    for index, row in enumerate(response["reviews"]):
        row["review_id"] = f"review_synthetic_fix1_{index}"
    return apply_assessment_review(run, response, operation_id=synthetic_operation(run))


@pytest.mark.parametrize("initial", ["supports", "opposes", "mixed", "context"])
@pytest.mark.parametrize("added", ["supports", "opposes", "mixed", "context"])
@pytest.mark.parametrize("linked", [False, True])
def test_new_relevant_evidence_is_symmetric_in_polarity_without_rewriting_history(initial, added, linked):
    run, proposal, keys = assessment_case(polarity=initial)
    assert complete_assessment(run, proposal, keys)
    before = deepcopy(run.candidate_evaluations[0].model_dump())
    evidence = append_accepted(run, added, linked=linked)
    assert public_validated_evaluations(run) == []
    assert run.candidate_evaluations[0].model_dump() == before
    assert evidence.evidence_id not in run.candidate_evaluations[0].reviewed_evidence_ids
    assert run.candidate_evaluations[0].final is not None


@pytest.mark.parametrize("added", ["supports", "opposes", "mixed", "context"])
def test_truly_unrelated_new_evidence_does_not_expand_or_remove_the_reviewed_scope(added):
    run, proposal, keys = assessment_case(polarity="opposes")
    original = complete_assessment(run, proposal, keys)
    append_accepted(run, added, linked=False, unrelated=True)
    assert public_validated_evaluations(run) == original


@pytest.mark.parametrize("added", ["supports", "opposes", "mixed", "context"])
def test_pre_review_omission_of_any_unique_related_record_requires_explicit_association(added):
    run, proposal, keys = assessment_case()
    append_accepted(run, added, linked=False)
    store_assessment_proposals(run, [proposal], keys)
    assert bind_assessment_identities(run) == []
    assert any("missing a current candidate association" in error for error in run.candidate_evaluations[0].validation_errors)


def test_shared_alias_cannot_become_two_ready_records_or_current_finals():
    run, proposals, keys = shared_alias_case()
    original = [e.model_dump() for e in run.evidence]
    store_assessment_proposals(run, proposals, keys)
    assert bind_assessment_identities(run) == []
    assert accept_ready(run) == []
    payload = assessment_review_payload(run)
    assert {row["entity_id"] for row in payload["candidate_identities"]} == {
        "entity_synthetic", "entity_synthetic_beta"}
    assert all(row["aliases"] == ["SC"] for row in payload["candidate_identities"])
    assert [e.model_dump() for e in run.evidence] == original


@pytest.mark.parametrize("suffix", [
    " SC = Unknown third compound.", " SC denotes an unknown third compound.",
    " Unknown third compound (SC) was administered.", " SC (Unknown third compound) was administered.",
    " Sentinel beta compound (SC) was also used.",
])
def test_a_known_local_definition_cannot_override_conflicting_or_unknown_third_definition(suffix):
    run, proposals, keys = shared_alias_case(prefix="Sentinel alpha compound (SC) was used in this study. ", suffix=suffix)
    store_assessment_proposals(run, proposals, keys)
    assert bind_assessment_identities(run) == []
    assert accept_ready(run) == []


def test_competing_alias_added_after_review_invalidates_current_attribution_without_changing_history():
    run, proposal, keys = assessment_case()
    assert complete_assessment(run, proposal, keys)
    before = deepcopy(run.candidate_evaluations[0].model_dump())
    second = run.candidates[0].model_copy(deep=True, update={"candidate_id": "mol_beta", "entity_id": "entity_beta",
        "name": "Different beta compound", "aliases": ["SC"], "pubchem_cid": "176", "smiles": "CC(=O)O",
        "inchikey": "QTBSBXVTEAMEQO-UHFFFAOYSA-N"})
    run.candidates.append(second)
    assert public_validated_evaluations(run) == []
    assert run.candidate_evaluations[0].model_dump() == before


@pytest.mark.parametrize("quote", [
    "Sentinel alpha compound changed the endpoint compared with placebo.",
    "PubChem CID:702 changed the endpoint compared with placebo.",
    "SC (PubChem CID:702) changed the endpoint compared with placebo.",
])
def test_unique_literal_identity_positive_and_safe_complete_competing_identity_payload(quote):
    run, proposals, keys = shared_alias_case(quote=quote)
    run.sources[0].metadata = {"secret_mapping": "PRIVATE_METADATA_SENTINEL"}
    run.candidates[0].identity_form = {"untrusted": "PRIVATE_IDENTITY_FORM_SENTINEL"}
    store_assessment_proposals(run, proposals, keys)
    ready = bind_assessment_identities(run)
    assert [record.entity_id for record in ready] == ["entity_synthetic"]
    payload = assessment_review_payload(run)
    row = payload["candidate_evaluations"][0]
    assert row["identity"]["identity_fingerprint"] == row["identity_fingerprint"]
    assert len(payload["candidate_identities"]) == 2
    assert "PRIVATE_IDENTITY_FORM_SENTINEL" not in str(row["identity"])
    assert "PRIVATE_METADATA_SENTINEL" not in str(row["evidence_bindings"])
    binding = row["evidence_bindings"][0]
    assert binding["entity_id"] == "entity_synthetic"
    for span in binding["definition_spans"]:
        assert run.sources[0].text[span["start"]:span["end"]] == span["text"]
    assert len(accept_ready(run)) == 1


@pytest.mark.parametrize("definition", [
    "Unknown third compound (SC) was administered. ",
    "SC (Unknown third compound) was administered. ",
    "SC stands for Unknown third compound. ",
    "SC = Unknown third compound. ",
])
def test_single_candidate_alias_still_abstains_for_explicit_unknown_source_definition(definition):
    run, proposal, keys = assessment_case()
    assert len(run.candidates) == 1  # A fresh singleton fixture, no competitor removed.
    run.sources[0].text = "Methods: 31 human children were enrolled. " + definition + run.evidence[0].quote
    assert complete_assessment(run, proposal, keys) == []
    assert run.candidate_evaluations[0].status == "needs_review"


@pytest.mark.parametrize("annotation", ["", "SC (n=31) was the study group. ",
    "SC (N = 31) was the study group. ", "The sample sizes were SC = 31, placebo = 30. ",
    "Sentinel compound (SC) was administered. ",
    "Sentinel compound (SC) was administered. " + "Additional recorded study context. " * 20])
def test_single_unambiguous_alias_no_definition_and_numeric_annotations_remain_usable(annotation):
    run, proposal, keys = assessment_case()
    run.sources[0].text = "Methods: 31 human children were enrolled. " + annotation + run.evidence[0].quote
    assert len(complete_assessment(run, proposal, keys)) == 1


def test_standard_canonical_id_with_sentence_period_is_not_rejected_as_numeric_extension():
    quote = "The study reports PubChem CID:702. The recorded endpoint changed compared with placebo."
    run, proposals, keys = shared_alias_case(quote=quote)
    store_assessment_proposals(run, proposals, keys)
    assert [record.entity_id for record in bind_assessment_identities(run)] == ["entity_synthetic"]
    assert len(accept_ready(run)) == 1

