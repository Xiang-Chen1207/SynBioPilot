"""SYNTHETIC v2 source-report proofs; never claim clinical efficacy or prediction."""
import copy
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_research_flow import draft, hypothesis, setup_flow, source

from scientist.candidate_assessments import assessment_cards
from scientist.co_models import HypothesisReview, ResearchContract
from scientist.coscientist import MetaDraft, ReflectionDraft
from scientist.flow_models import ClaimEvidenceRelation, ClaimReviewDraft
from scientist.models import CandidateRecord, EvidenceRecord, ResearchRun, ScientistSettings
from scientist.presentation import result_document
from scientist.research_flow import source_observation_statement

QUOTE = "At week 12, IC and placebo groups did not differ significantly on the clinical endpoint but blood marker M was higher with IC."


def observation_case(*, polarity="mixed", quote=QUOTE):
    engine, flow = setup_flow()
    engine.settings = ScientistSettings(llm={"backend":"none"})
    h = hypothesis()
    h.molecule_names = ["IC"]
    h.condition = "SYNTHETIC 12-week randomized oral trial"
    h.endpoint = "SYNTHETIC clinical endpoint and blood marker M"
    s = source(text="SYNTHETIC Methods: 31 human children were enrolled and randomized to IC or placebo. " + quote,metadata={"synthetic":True})
    e = EvidenceRecord(evidence_id="synthetic_mixed",source_id=s.source_id,quote=quote,claim="SYNTHETIC mixed study result",
        location="SYNTHETIC result sentence",polarity=polarity,study_system="Homo sapiens children",
        condition=h.condition,endpoint=h.endpoint,validated_span=True)
    h.claim = source_observation_statement(s.source_id,e.quote)
    if polarity == "opposes":
        h.opposing_evidence_ids = [e.evidence_id]
    else:
        h.evidence_ids = [e.evidence_id]
    engine.run.candidates = [CandidateRecord(candidate_id="synthetic_candidate",entity_id="synthetic_entity",
        name="Illustrative compound (IC)",identity_status="needs_review",role_status="needs_review",decision="hold")]
    engine.run.sources = [s]; engine.run.evidence = [e]; engine.state.hypotheses = [h]
    flow.ingest([s]); flow.register(h)
    main = next(c for c in flow.claims_for(h) if c.kind == "main")
    relation = ClaimEvidenceRelation(claim_id=main.claim_id,hypothesis_id=h.hypothesis_id,version=h.version,
        evidence_id=e.evidence_id,source_id=e.source_id,entity_id="synthetic_entity",relation="supports",
        study_system=e.study_system,condition=e.condition,endpoint=e.endpoint)
    review = ClaimReviewDraft(relation_schema_version=2,claim_scope="source_observation",evidence_relations=[relation],
        claim_id=main.claim_id,hypothesis_id=h.hypothesis_id,version=h.version,verdict="supported",
        evidence_ids=[e.evidence_id],reason="SYNTHETIC exact reported observation only")
    return engine,flow,h,s,e,main,review


def adjudicate(engine,flow,h,review):
    rest = [draft(c,[],verdict="unknown") for c in flow.claims_for(h) if c.claim_id != review.claim_id]
    flow.adjudicate([h],[review,*rest])


@pytest.mark.parametrize("polarity", ["mixed","opposes","supports"])
def test_exact_source_observation_can_be_supported_without_changing_global_polarity_or_therapy_status(polarity):
    engine,flow,h,_,e,main,review = observation_case(polarity=polarity)
    before = e.model_dump()
    adjudicate(engine,flow,h,review)
    assert main.state == "supported"
    assert flow.main_review_state(h) == "source_observation_supported"
    assert not flow.main_reviewed(h)
    assert "窄原文观察" in flow.main_review_gap(h) and "各自需要支持证据" in flow.main_review_gap(h)
    assert e.model_dump() == before
    assert flow.verify()["valid"]
    link = next(link for link in flow.data.links if link.claim_id == main.claim_id)
    assert link.polarity == polarity and link.claim_relation.relation == "supports"
    card = assessment_cards(engine.run)[0]
    assert card["scientific_status"] == "needs_evidence"
    assert len(card["observation_support"]) == 1
    assert card["observation_support"][0]["polarity"] == polarity
    if polarity in {"mixed","opposes"}:
        assert card["opposing_evidence_ids"] == [e.evidence_id]
    assert engine.run.candidates[0].decision == "hold"


@pytest.mark.parametrize("claim", [
    "IC improved the clinical endpoint in children.",
    "IC and placebo are clinically equivalent.",
    "IC has no effect in all patients.",
    "Marker M accurately predicts individual clinical improvement.",
    "IC treats this disorder because the marker increased.",
])
def test_same_mixed_evidence_does_not_support_opposite_efficacy_equivalence_or_individual_prediction(claim):
    engine,flow,h,_,e,main,review = observation_case()
    h.claim = main.text = claim
    adjudicate(engine,flow,h,review)
    assert main.state == "context_only" and not flow.main_reviewed(h)
    assert e.polarity == "mixed"
    assert flow.data.claim_reviews[0].rejected_relation_ids == [e.evidence_id]
    assert all(link.claim_relation is None for link in flow.data.links)
    assert flow.verify()["valid"]


@pytest.mark.parametrize("change", ["extra_claim", "partial_quote", "other_entity", "ambiguous_entity", "other_source", "duplicate_source",
    "duplicate_span", "other_hypothesis", "other_version", "other_claim", "condition", "endpoint", "study_system", "duplicate_relation", "relation_self_label"])
def test_observation_exception_requires_exact_projection_identity_scope_and_unique_version(change):
    engine,flow,h,s,e,main,review = observation_case()
    relation = review.evidence_relations[0]
    if change == "extra_claim":
        h.claim = main.text = h.claim + " This proves efficacy."
    elif change == "partial_quote":
        h.claim = main.text = source_observation_statement(s.source_id,"blood marker M was higher with IC.")
    elif change == "other_entity":
        relation.entity_id = "another_entity"
    elif change == "ambiguous_entity":
        engine.run.candidates.append(engine.run.candidates[0].model_copy(update={"candidate_id":"another_candidate","entity_id":"another_entity"}))
    elif change == "other_source":
        relation.source_id = "source_other"
    elif change == "duplicate_source":
        engine.run.sources.append(s.model_copy(deep=True))
    elif change == "duplicate_span":
        s.text += " " + e.quote
        flow.ingest([s])
    elif change == "other_hypothesis":
        relation.hypothesis_id = "h_other"
    elif change == "other_version":
        relation.version = 2
    elif change == "other_claim":
        relation.claim_id = relation.claim_id.replace("main","intervention")
    elif change == "condition":
        relation.condition = "different condition"
    elif change == "endpoint":
        relation.endpoint = "different endpoint"
    elif change == "study_system":
        relation.study_system = "different population"
    elif change == "duplicate_relation":
        review.evidence_relations.append(relation.model_copy(deep=True))
    else:
        relation.relation = "opposes"
    adjudicate(engine,flow,h,review)
    assert main.state != "supported"
    assert not flow.main_reviewed(h)
    assert assessment_cards(engine.run)[0]["observation_support"] == []


@pytest.mark.parametrize("system,source_prefix", [
    ("Mus musculus mice", "SYNTHETIC mice study. "),
    ("Homo sapiens children", "SYNTHETIC mice study. "),
    ("human cells", "SYNTHETIC human cells. "),
    ("unknown", "SYNTHETIC children study. "),
    ("Homo sapiens children", "SYNTHETIC human children and rats study. "),
])
def test_animal_cell_unknown_or_mixed_system_cannot_be_projected_to_human_observation(system,source_prefix):
    engine,flow,h,s,e,main,review = observation_case()
    e.study_system = review.evidence_relations[0].study_system = system
    s.text = source_prefix + e.quote
    flow.ingest([s])
    adjudicate(engine,flow,h,review)
    assert main.state != "supported"
    assert not flow.main_reviewed(h)


def test_old_schema_remains_conservative_and_a_new_label_alone_cannot_upgrade_it():
    engine,flow,h,_,e,main,_ = observation_case()
    old_review = draft(main,[e.evidence_id],verdict="supported")
    assert old_review.relation_schema_version == 1 and old_review.evidence_relations == []
    adjudicate(engine,flow,h,old_review)
    assert main.state == "context_only"
    assert flow.main_review_state(h) == "reviewed_not_supported"
    before = copy.deepcopy(engine.run.model_dump())
    assessment_cards(engine.run)
    assert engine.run.model_dump() == before
    with pytest.raises(ValidationError):
        ClaimEvidenceRelation(schema_version=3,claim_id=main.claim_id,hypothesis_id=h.hypothesis_id,
            version=1,evidence_id=e.evidence_id,source_id=e.source_id,entity_id="synthetic_entity",
            relation="supports",study_system=e.study_system,condition=e.condition,endpoint=e.endpoint)


def test_saved_relationship_is_revalidated_against_the_reviewer_receipt():
    engine,flow,h,_,_,_main,review = observation_case()
    adjudicate(engine,flow,h,review)
    assert flow.verify()["valid"]
    flow.data.claim_reviews[0].evidence_relations[0].entity_id = "tampered_entity"
    assert not flow.verify()["valid"]
    assert flow.main_review_state(h) == "invalid_receipts"
    assert assessment_cards(engine.run)[0]["observation_support"] == []


def test_real_review_does_not_upgrade_negative_observation_to_a_retained_therapy_card():
    engine,flow,h,_,e,_,review = observation_case(polarity="opposes")
    engine.call = lambda *args, **kwargs: ReflectionDraft(reviews=[HypothesisReview(hypothesis_id=h.hypothesis_id,version=h.version,
        verdict="retain",reason="SYNTHETIC source report",valid_evidence_ids=[e.evidence_id])],claim_reviews=[review])
    engine.review([h],"SYNTHETIC formal observation review")
    assert flow.main_review_state(h) == "source_observation_supported"
    assert h.status == "needs_evidence"
    gap = " ".join(engine.state.reviews[0].required_evidence)
    assert "窄原文观察" in gap
    assert "缺少其自身已审查的支持证据" not in gap


def test_public_wink_quote_supports_only_a_new_synthetic_exact_report_and_history_stays_unchanged():
    # This explicit allowlisted public file is never traversed or written.
    path = Path(__file__).resolve().parents[1]/"runs/scientist/projects/p_a9da74bdb1904094/research/r_352ecd16d3904496/run.json"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    original = ResearchRun.model_validate_json(path.read_text())
    record = next(e for e in original.evidence if e.source_id == "pmid:27103982")
    src = next(s for s in original.sources if s.source_id == record.source_id)
    engine,flow,h,_,_,_,_ = observation_case()
    engine.run.sources = [src.model_copy(deep=True)]
    engine.run.evidence = [record.model_copy(deep=True)]
    engine.run.candidates = [original.candidates[0].model_copy(deep=True)]
    candidate = engine.run.candidates[0]
    h.molecule_names = [candidate.name]
    h.claim = source_observation_statement(src.source_id,record.quote)
    h.condition,h.endpoint = record.condition,record.endpoint
    h.evidence_ids = [record.evidence_id]
    flow.data.claims=[];flow.data.publications=[]
    flow.ingest(engine.run.sources);flow.register(h)
    main=flow.claims_for(h)[0]
    relation=ClaimEvidenceRelation(claim_id=main.claim_id,hypothesis_id=h.hypothesis_id,version=h.version,
        evidence_id=record.evidence_id,source_id=record.source_id,entity_id=candidate.entity_id or candidate.candidate_id,
        relation="supports",study_system=record.study_system,condition=record.condition,endpoint=record.endpoint)
    review=ClaimReviewDraft(relation_schema_version=2,claim_scope="source_observation",evidence_relations=[relation],
        claim_id=main.claim_id,hypothesis_id=h.hypothesis_id,version=h.version,verdict="supported",
        evidence_ids=[record.evidence_id],reason="SYNTHETIC re-adjudication of a public source-report assertion only")
    adjudicate(engine,flow,h,review)
    assert main.state == "supported" and flow.main_review_state(h) == "source_observation_supported"
    assert not flow.main_reviewed(h)
    assert record.polarity == "mixed"
    assert original.co_scientist.research_flow.claim_reviews[6].verdict == "context_only"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert before == "e1da71c5996301304c5325479b3f4d90daeb222dbd63395f815e05c12d5abd67"


def test_new_observation_evidence_requires_its_current_claim_query_assignment():
    for assigned in (False, True):
        engine,flow,h,s,e,main,review = observation_case()
        engine.run.evidence = []
        h.evidence_ids = []
        if assigned:
            query = flow.new_query("supplement","source_observation","SYNTHETIC source query",
                claim_ids=[main.claim_id],hypothesis_id=h.hypothesis_id,version=h.version,
                status="ok",source_ids=[s.source_id])
            main.query_ids.append(query.query_id)
        flow.adjudicate([h],[review],[e.model_copy(update={"validated_span":False})])
        assert (main.state == "supported") is assigned
        assert bool(engine.run.evidence) is assigned
        assert bool(assessment_cards(engine.run)[0]["observation_support"]) is assigned
        assert engine.run.candidates[0].decision == "hold"


def test_meta_can_select_verified_negative_observation_as_question_basis_without_upgrading_therapy():
    engine,flow,h,_,e,_,review = observation_case(polarity="opposes")
    def call(role,title,schema,payload,**kwargs):
        if schema is ReflectionDraft:
            return ReflectionDraft(reviews=[HypothesisReview(hypothesis_id=h.hypothesis_id,version=h.version,
                verdict="retain",reason="SYNTHETIC source observation",valid_evidence_ids=[e.evidence_id])],claim_reviews=[review])
        assert schema is MetaDraft
        return MetaDraft(contracts=[ResearchContract(hypothesis_id=h.hypothesis_id,version=h.version,
            status="awaiting_measurement",question="Which paired observations are missing?",judgment="SYNTHETIC report-based question",
            evidence_ids=[e.evidence_id],minimal_validation="Inspect original paired outcomes; no treatment prediction")],common_gaps=[])
    engine.call = call
    engine.review([h],"SYNTHETIC observation review")
    engine.meta()
    assert h.status == "needs_evidence"
    assert engine.state.contracts[0].status == "needs_evidence"
    assert engine.state.contracts[0].evidence_ids == [e.evidence_id]
    assert engine.state.contracts[0].opposing_evidence_ids == [e.evidence_id]
    assert e.polarity == "opposes" and not flow.main_reviewed(h)


def test_defensive_view_of_retained_negative_observation_does_not_invent_missing_review():
    engine,flow,h,_,e,_,review = observation_case(polarity="opposes")
    engine.call = lambda *args,**kwargs: ReflectionDraft(reviews=[HypothesisReview(hypothesis_id=h.hypothesis_id,version=h.version,
        verdict="retain",reason="SYNTHETIC source observation",valid_evidence_ids=[e.evidence_id])],claim_reviews=[review])
    engine.review([h],"SYNTHETIC observation review")
    # Simulate an externally saved retain label. The display must downgrade its
    # clinical status while preserving the fact that this narrow claim was reviewed.
    h.status="reviewed"
    engine.state.reviews[0].verdict="retain"
    result=result_document(engine.run)
    assert result["co_scientist"]["hypotheses"][0]["status"] == "needs_evidence"
    assert result["candidate_assessments"][0]["scientific_status"] == "needs_evidence"
    assert result["candidate_assessments"][0]["observation_support"]
    assert "没有对应版本的有效独立上下文审查" not in " ".join(result["co_scientist"]["hypotheses"][0]["inference_gaps"])
    assert flow.main_review_state(h) == "source_observation_supported"


def test_meta_observation_allowlist_does_not_admit_legacy_negative_inference():
    engine,flow,h,_,e,_,review = observation_case(polarity="opposes")
    review.relation_schema_version = 1
    review.claim_scope = "inference"
    review.evidence_relations = []
    def call(role,title,schema,payload,**kwargs):
        if schema is ReflectionDraft:
            return ReflectionDraft(reviews=[HypothesisReview(hypothesis_id=h.hypothesis_id,version=h.version,
                verdict="retain",reason="SYNTHETIC legacy inference",valid_evidence_ids=[e.evidence_id])],claim_reviews=[review])
        return MetaDraft(contracts=[ResearchContract(hypothesis_id=h.hypothesis_id,version=h.version,
            status="awaiting_measurement",question="SYNTHETIC unsupported question",judgment="SYNTHETIC",
            evidence_ids=[e.evidence_id],minimal_validation="Verify the unsupported premise")],common_gaps=[])
    engine.call=call
    engine.review([h],"SYNTHETIC legacy review")
    engine.meta()
    assert flow.main_review_state(h) == "reviewed_not_supported"
    assert engine.state.contracts[0].status == "needs_evidence"
    assert engine.state.contracts[0].evidence_ids == []
    assert engine.state.contracts[0].opposing_evidence_ids == [e.evidence_id]
    assert assessment_cards(engine.run)[0]["observation_support"] == []

