"""Transparent SYNTHETIC contracts; no biomedical or model/network execution."""

from copy import deepcopy

import pytest

from scientist.assessment_models import CandidateAssessmentReviewDraft
from scientist.assessment_review import (
    BINDING_FIELDS,
    apply_assessment_review,
    assessment_review_payload,
    bind_assessment_identities,
    public_validated_evaluations,
    store_assessment_proposals,
)
from scientist.co_models import AgentEvent, CoScientistState
from scientist.models import (
    CandidateRecord,
    EvidenceRecord,
    ExecutionStatus,
    ResearchBrief,
    ResearchRun,
    SourceRecord,
)
from scientist.research_flow import source_observation_statement


def assessment_case(
    *,
    polarity="mixed",
    system="Homo sapiens children",
    condition="SYNTHETIC adjunctive 12-week trial",
    endpoint="SYNTHETIC associated symptom and marker endpoints",
    body=None,
):
    """Synthetic validator fixture with local review data."""
    quote = "SC and placebo did not differ on the recorded clinical endpoint, while blood marker M was higher with SC."
    source = SourceRecord(
        source_id="synthetic:sentinel",
        provider="synthetic",
        title="SYNTHETIC Sentinel compound trial",
        url="https://example.invalid/synthetic",
        retrieved_at="2026-10-16T00:00:00Z",
        evidence_scope="abstract",
        text=body
        or "SYNTHETIC Methods: 31 human children were enrolled and randomized to SC or placebo. "
        + quote,
    )
    evidence = EvidenceRecord(
        evidence_id="ev_synthetic_sentinel",
        source_id=source.source_id,
        claim="The synthetic trial reports mixed endpoint observations for SC.",
        quote=quote,
        location="SYNTHETIC results",
        polarity=polarity,
        study_system=system,
        condition=condition,
        endpoint=endpoint,
        validated_span=True,
    )
    candidate = CandidateRecord(
        candidate_id="mol_synthetic",
        entity_id="entity_synthetic",
        name="Sentinel compound (SC)",
        aliases=["SC"],
        identity_status="resolved",
        identity_revision=1,
        smiles="CCO",
        inchikey="LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
        pubchem_cid="702",
        evidence_ids=[evidence.evidence_id],
    )
    run = ResearchRun(
        run_id="r_synthetic_assessment",
        project_id="p_synthetic_assessment",
        created_at="synthetic",
        updated_at="synthetic",
        status="running",
        brief=ResearchBrief(
            disease="Synthetic syndrome",
            task_intent="candidate_assessment",
            research_mode="co_scientist",
            existing_candidates=[candidate.name],
            population="SYNTHETIC human children",
            phenotype="SYNTHETIC associated symptom endpoint",
            assumptions=["SYNTHETIC original supplied task document; not evidence."],
        ),
        sources=[source],
        evidence=[evidence],
        candidates=[candidate],
        co_scientist=CoScientistState(),
    )
    status = {
        "supports": "limited_source_findings",
        "opposes": "no_supported_benefit",
        "mixed": "mixed_evidence",
        "context": "needs_evidence",
    }[polarity]
    proposal = {
        "candidate_name": candidate.name,
        "conclusion": "The reviewed trial reports limited endpoint observations. Applicability to the requested use remains uncertain.",
        "scientific_status": status,
        "findings": [
            {
                "finding_key": "f1",
                "statement": source_observation_statement(source.source_id, quote),
                "evidence_keys": ["e1"],
                "scope": {
                    key: getattr(evidence, key) for key in ("study_system", "condition", "endpoint")
                },
            }
        ],
        "unknowns": [
            "Human clinical applicability, core symptom benefit and delivery remain unestablished."
        ],
        "next_questions": [
            "What evidence is needed for the requested population and administration context?"
        ],
    }
    return run, proposal, {"e1": evidence.evidence_id}


def review_response(run, *, verdict="accept", review_id="review_synthetic"):
    """Transparent echo reviewer, intentionally no scientific intelligence."""
    rows = assessment_review_payload(run)["candidate_evaluations"]
    return {
        "reviews": [
            {
                **{key: row[key] for key in BINDING_FIELDS},
                "review_id": review_id,
                "verdict": verdict,
                "reason": "SYNTHETIC reviewer fixture inspects limited scope; not real model validation.",
                "item_reviews": [
                    {
                        "finding_id": f["finding_id"],
                        "verdict": verdict,
                        "applicability": f["applicability"],
                        "evidence_ids": f["evidence_ids"],
                        "reason": "SYNTHETIC exact finding reviewed.",
                    }
                    for f in row["draft"]["findings"]
                ],
                "supporting_evidence_ids": row["draft"]["supporting_evidence_ids"],
                "opposing_evidence_ids": row["draft"]["opposing_evidence_ids"],
            }
            for row in rows
        ]
    }


def synthetic_operation(run, operation_id="llm_synthetic_003"):
    run.co_scientist.events.append(
        AgentEvent(
            event_id="event_synthetic_review",
            sequence=1,
            agent_id="reflection",
            kind="inference",
            status="completed",
            title="独立候选结论审查",
            created_at="synthetic",
            summary="SYNTHETIC contract test with a mocked review call.",
            operation_id=operation_id,
            operation_status="completed",
        )
    )
    return operation_id


def complete_assessment(run, proposal, keys):
    store_assessment_proposals(run, [proposal], keys)
    bind_assessment_identities(run)
    operation = synthetic_operation(run)
    return apply_assessment_review(run, review_response(run), operation_id=operation)


@pytest.mark.parametrize("polarity", ["supports", "opposes", "mixed", "context"])
def test_independent_limited_assessment_completes_without_therapy_promotion(polarity):
    run, proposal, keys = assessment_case(polarity=polarity)
    before = run.evidence[0].model_dump()
    result = complete_assessment(run, proposal, keys)
    assert len(result) == 1, run.candidate_evaluations[0].validation_errors
    assert result[0]["status"] == "completed_review"
    assert result[0]["conclusion"] == proposal["conclusion"]
    assert run.evidence[0].model_dump() == before
    assert run.candidates[0].role_status == "unclassified"
    if polarity == "mixed":
        assert (
            result[0]["supporting_evidence_ids"]
            == result[0]["opposing_evidence_ids"]
            == list(keys.values())
        )


def test_no_legacy_rationale_or_quote_review_can_manufacture_assessment():
    run, _, _ = assessment_case()
    run.candidates[0].rationale = "A confident legacy statement."
    run.candidates[0].role_status = "model_reviewed"
    assert bind_assessment_identities(run) == []
    assert public_validated_evaluations(run) == []
    assert (
        ResearchRun.model_validate(
            run.model_dump(exclude={"candidate_evaluations"})
        ).candidate_evaluations
        == []
    )


def test_draft_remains_distinct_from_review_and_full_input_preserves_counterevidence():
    run, proposal, keys = assessment_case()
    store_assessment_proposals(run, [proposal], keys)
    bind_assessment_identities(run)
    payload = assessment_review_payload(run)
    assert payload["accepted_evidence"][0]["polarity"] == "mixed"
    assert run.evidence[0].quote in payload["sources"][0]["windows"][0]["text"]
    assert "SYNTHETIC original supplied task document" in str(payload["task"])
    assert public_validated_evaluations(run) == []
    assert run.candidate_evaluations[0].final is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 99),
        ("candidate_id", "wrong"),
        ("entity_id", "wrong"),
        ("identity_revision", 99),
        ("identity_fingerprint", "0" * 64),
        ("task_fingerprint", "0" * 64),
        ("conclusion_fingerprint", "0" * 64),
        ("evidence_fingerprint", "0" * 64),
    ],
)
def test_wrong_binding_cannot_complete(field, value):
    run, proposal, keys = assessment_case()
    store_assessment_proposals(run, [proposal], keys)
    bind_assessment_identities(run)
    response = review_response(run)
    response["reviews"][0][field] = value
    assert apply_assessment_review(run, response, operation_id=synthetic_operation(run)) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "wrong_item",
        "missing_opposition",
        "withhold",
        "no_operation",
        "wrong_actor",
        "failed_operation",
    ],
)
def test_incomplete_or_ambiguous_independent_review_cannot_complete(mutation):
    run, proposal, keys = assessment_case()
    store_assessment_proposals(run, [proposal], keys)
    bind_assessment_identities(run)
    response = review_response(run)
    operation = synthetic_operation(run)
    if mutation == "missing":
        response["reviews"] = []
    if mutation == "duplicate":
        response["reviews"] *= 2
    if mutation == "wrong_item":
        response["reviews"][0]["item_reviews"][0]["finding_id"] = "wrong"
    if mutation == "missing_opposition":
        response["reviews"][0]["opposing_evidence_ids"] = []
    if mutation == "withhold":
        response["reviews"][0]["verdict"] = "withhold"
    if mutation == "no_operation":
        operation = ""
    if mutation == "wrong_actor":
        run.co_scientist.events[0].agent_id = "generation"
    if mutation == "failed_operation":
        run.co_scientist.events[0].status = "failed"
    assert apply_assessment_review(run, response, operation_id=operation) == []
    assert run.candidate_evaluations[0].status == "needs_review"


@pytest.mark.parametrize(
    "mutation",
    [
        "identity_revision",
        "identity_structure",
        "task",
        "source",
        "quote",
        "scope",
        "conclusion",
        "remove_link",
        "duplicate_source",
    ],
)
def test_current_binding_change_invalidates_without_rewriting_historical_review(mutation):
    run, proposal, keys = assessment_case()
    assert complete_assessment(run, proposal, keys)
    before = deepcopy(run.candidate_evaluations[0].model_dump())
    if mutation == "identity_revision":
        run.candidates[0].identity_revision += 1
    if mutation == "identity_structure":
        run.candidates[0].smiles = "CC"
    if mutation == "task":
        run.brief.delivery_context = "SYNTHETIC different route"
    if mutation == "source":
        run.sources[0].text += " Changed original source."
    if mutation == "quote":
        run.evidence[0].quote = "Different quote"
    if mutation == "scope":
        run.evidence[0].endpoint = "SYNTHETIC another endpoint"
    if mutation == "conclusion":
        run.candidate_evaluations[0].draft.conclusion = "Changed conclusion."
    if mutation == "remove_link":
        run.candidates[0].evidence_ids = []
    if mutation == "duplicate_source":
        run.sources.append(run.sources[0].model_copy(deep=True))
    assert public_validated_evaluations(run) == []
    if mutation != "conclusion":
        assert run.candidate_evaluations[0].model_dump() == before


def test_unrelated_new_evidence_preserves_exact_reviewed_scope_but_new_counter_requires_review():
    run, proposal, keys = assessment_case()
    initial = complete_assessment(run, proposal, keys)
    unrelated = run.evidence[0].model_copy(
        update={
            "evidence_id": "unrelated",
            "polarity": "opposes",
            "claim": "Another compound had no effect.",
            "quote": "Another compound had no effect in the recorded assay.",
        }
    )
    run.sources.append(
        run.sources[0].model_copy(
            update={
                "source_id": "synthetic:unrelated",
                "text": unrelated.quote,
                "title": "Unrelated synthetic experiment",
            }
        )
    )
    unrelated.source_id = "synthetic:unrelated"
    run.evidence.append(unrelated)
    assert public_validated_evaluations(run) == initial
    extra = run.evidence[0].model_copy(update={"evidence_id": "new_counter", "polarity": "opposes"})
    run.evidence.append(extra)
    # Named, unique counterevidence is relevant even before an extractor links it.
    assert public_validated_evaluations(run) == []
    run.candidates[0].evidence_ids.append(extra.evidence_id)
    assert public_validated_evaluations(run) == []
    assert run.candidate_evaluations[0].final is not None


@pytest.mark.parametrize(
    "mutation",
    [
        "no_evidence",
        "missing_key",
        "duplicate_key",
        "duplicate_proposal",
        "unknown_candidate",
        "wrong_scope",
        "new_paraphrase",
        "repeated_quote",
        "unresolved_identity",
        "unlinked_evidence",
    ],
)
def test_unreviewable_proposal_never_becomes_ready(mutation):
    run, proposal, keys = assessment_case()
    proposals = [proposal]
    if mutation == "no_evidence":
        run.evidence = []
    if mutation == "missing_key":
        proposal["findings"][0]["evidence_keys"] = ["missing"]
    if mutation == "duplicate_key":
        proposal["findings"][0]["evidence_keys"] *= 2
    if mutation == "duplicate_proposal":
        proposals *= 2
    if mutation == "unknown_candidate":
        proposal["candidate_name"] = "Unrelated chemical"
    if mutation == "wrong_scope":
        proposal["findings"][0]["scope"]["endpoint"] = "core syndrome"
    if mutation == "new_paraphrase":
        proposal["findings"][0]["statement"] = "SC works for everyone."
    if mutation == "repeated_quote":
        run.sources[0].text += " " + run.evidence[0].quote
    if mutation == "unresolved_identity":
        run.candidates[0].identity_status = "needs_review"
    if mutation == "unlinked_evidence":
        run.candidates[0].evidence_ids = []
    store_assessment_proposals(run, proposals, keys)
    assert bind_assessment_identities(run) == []
    assert public_validated_evaluations(run) == []


def test_omitted_counterfinding_cannot_be_filled_automatically():
    run, proposal, keys = assessment_case(polarity="supports")
    second = run.evidence[0].model_copy(
        update={"evidence_id": "opposing_extra", "polarity": "opposes"}
    )
    run.evidence.append(second)
    run.candidates[0].evidence_ids.append(second.evidence_id)
    store_assessment_proposals(run, [proposal], keys)
    assert bind_assessment_identities(run) == []
    assert run.candidate_evaluations[0].draft.opposing_evidence_ids == []
    assert run.candidate_evaluations[0].final is None


def test_named_counterevidence_cannot_be_hidden_by_omitting_candidate_link():
    run, proposal, keys = assessment_case(polarity="supports")
    run.evidence.append(
        run.evidence[0].model_copy(
            update={"evidence_id": "unlinked_counter", "polarity": "opposes"}
        )
    )
    store_assessment_proposals(run, [proposal], keys)
    assert bind_assessment_identities(run) == []
    assert any(
        "missing a current candidate association" in e
        for e in run.candidate_evaluations[0].validation_errors
    )
    assert "unlinked_counter" not in run.candidates[0].evidence_ids


def test_identity_preparation_can_bind_already_proposed_finding_without_inserting_new_one():
    run, proposal, keys = assessment_case()
    run.candidates[0].name = "Sentinel compound"
    run.candidates[0].aliases = []
    proposal["candidate_name"] = "Sentinel compound"
    run.sources[0].title = "SYNTHETIC trial with SC"
    store_assessment_proposals(run, [proposal], keys)
    assert run.candidate_evaluations[0].draft.opposing_evidence_ids == []
    run.candidates[0].aliases = ["SC"]
    assert bind_assessment_identities(run)
    assert run.candidate_evaluations[0].draft.opposing_evidence_ids == list(keys.values())
    assert len(run.candidate_evaluations[0].draft.findings) == 1


@pytest.mark.parametrize(
    "conclusion",
    [
        "Clinical benefit and delivery remain unknown.",
        "No clinical benefit or delivery is established.",
        "疗效和递送仍未知。",
    ],
)
def test_explicit_limited_summary_does_not_fail_for_standard_abstention_word_order(conclusion):
    run, proposal, keys = assessment_case()
    proposal["conclusion"] = conclusion
    assert complete_assessment(run, proposal, keys)


@pytest.mark.parametrize(
    "prefix",
    [
        "Results: SC was studied in children with the synthetic syndrome. ",
        "SYNTHETIC Methods: 31 human children were enrolled. Patients were not enrolled from clinic Q. ",
    ],
)
def test_incomplete_population_proof_retained_as_exact_observation_with_explicit_limit(prefix):
    run, proposal, keys = assessment_case()
    run.sources[0].text = prefix + run.evidence[0].quote
    result = complete_assessment(run, proposal, keys)
    assert len(result) == 1, run.candidate_evaluations[0].validation_errors
    finding = result[0]["findings"][0]
    assert finding["applicability"] == "population_uncertain"
    assert finding["scope"]["study_system"] == "Homo sapiens children"
    assert finding["scope_limitations"] and result[0]["scientific_status"] == "mixed_evidence"
    assert finding["statement"] == source_observation_statement(
        run.sources[0].source_id, run.evidence[0].quote
    )


@pytest.mark.parametrize(
    "conclusion",
    [
        "SC cures the syndrome.",
        "SC is equivalent to the approved treatment.",
        "SC predicts individual clinical response.",
        "SC monotherapy is supported by this trial.",
        "The study shows improved core symptoms.",
        "The chassis produces SC and delivers it to the brain.",
        "SC improves human outcomes.",
        "Delivery is not established, but SC cures the syndrome.",
        "SC cures the syndrome without risk.",
        "SC not only cures the syndrome.",
        "Dose is unknown, SC cures the syndrome.",
    ],
)
def test_obvious_therapeutic_and_delivery_extrapolation_cannot_be_waived(conclusion):
    run, proposal, keys = assessment_case()
    proposal["conclusion"] = conclusion
    store_assessment_proposals(run, [proposal], keys)
    assert bind_assessment_identities(run) == []


@pytest.mark.parametrize(
    "system,body",
    [
        ("rat", "SYNTHETIC Methods: Rats were treated with SC. "),
        ("Danio rerio zebrafish", "SYNTHETIC Methods: The experiment used Danio rerio zebrafish. "),
        ("in vitro cells", "SYNTHETIC Methods: The experiment used cultured cells. "),
        ("unknown", "SYNTHETIC Methods: The experimental model was unspecified. "),
    ],
)
def test_nonhuman_or_unknown_findings_can_be_limited_background_not_human_application(system, body):
    run, proposal, keys = assessment_case(system=system)
    run.sources[0].text = body + run.evidence[0].quote
    assert complete_assessment(run, proposal, keys)
    run2, proposal2, keys2 = assessment_case(system=system)
    run2.sources[0].text = body + run2.evidence[0].quote
    proposal2["conclusion"] = "The animal study supports benefit in human patients."
    store_assessment_proposals(run2, [proposal2], keys2)
    assert bind_assessment_identities(run2) == []


def test_background_human_word_cannot_validate_declared_human_study_scope():
    run, proposal, keys = assessment_case()
    run.sources[0].text = (
        "Background: Human patients need treatment. Methods: The study used Wistar rodents. "
        + run.evidence[0].quote
    )
    store_assessment_proposals(run, [proposal], keys)
    assert bind_assessment_identities(run) == []


def test_closed_run_is_read_only_and_repeated_review_receipt_invalidates():
    run, proposal, keys = assessment_case()
    assert complete_assessment(run, proposal, keys)
    run.status = ExecutionStatus.COMPLETED
    before = run.model_dump()
    assert store_assessment_proposals(run, [proposal], keys) == []
    assert bind_assessment_identities(run) == []
    apply_assessment_review(run, {"reviews": []})
    assert run.model_dump() == before
    run.status = ExecutionStatus.RUNNING
    old_review = run.candidate_evaluations[0].reviews[0]
    apply_assessment_review(run, CandidateAssessmentReviewDraft(reviews=[old_review]))
    assert public_validated_evaluations(run) == []


@pytest.mark.parametrize("duplicate", [False, True])
def test_alternate_assessment_id_for_same_entity_version_fails_json_markdown_csv(
    tmp_path, duplicate
):
    from test_delivery_r2_public import CONCLUSION, evaluation_run, rows

    from scientist.exports import export_document, markdown_bytes, table_exports
    from scientist.presentation import result_document

    _, run = evaluation_run(tmp_path)
    record = run.candidate_evaluations[0]
    changed = record.model_copy(deep=True) if duplicate else record
    changed.assessment_id = "assessment_synthetic_second_id"
    changed.reviews[0].assessment_id = changed.assessment_id
    changed.reviews[0].review_id = "review_synthetic_second_id"
    if duplicate:
        run.candidate_evaluations.append(changed)
    before = run.model_dump_json()
    assert public_validated_evaluations(run) == []
    assert result_document(run)["candidate_evaluations"] == []
    assert export_document(run)["candidate_evaluations"] == []
    assert all(
        c["validated_evaluation"] is None for c in result_document(run)["candidate_assessments"]
    )
    assert CONCLUSION not in markdown_bytes(run).decode()
    tables = table_exports(run)
    assert rows(tables["candidate_evaluations.csv"]) == []
    assert all(not row["conclusion"] for row in rows(tables["candidates.csv"]))
    assert run.model_dump_json() == before


def test_normal_same_entity_versions_have_one_current_independently_reviewed_conclusion():
    run, proposal, keys = assessment_case()
    assert complete_assessment(run, proposal, keys)[0]["version"] == 1
    store_assessment_proposals(run, [proposal], keys)
    assert run.candidate_evaluations[-1].version == 2
    assert public_validated_evaluations(run) == []
    assert len(bind_assessment_identities(run)) == 1
    response = review_response(run, review_id="review_synthetic_v2")
    results = apply_assessment_review(
        run, response, operation_id=synthetic_operation(run, "llm_synthetic_v2")
    )
    assert len(results) == 1 and results[0]["version"] == 2
    assert run.candidate_evaluations[0].status == "completed_review"

