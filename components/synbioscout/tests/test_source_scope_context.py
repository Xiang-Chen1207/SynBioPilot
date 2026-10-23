"""SYNTHETIC scope/status projections; no real studies or historical file writes."""
import copy
import json

import pytest

from scientist.context import accepted_evidence_index, source_context
from scientist.exports import export_document
from scientist.models import EvidenceRecord, ResearchBrief, ResearchRun, SourceRecord
from scientist.presentation import result_document
from scientist.research_public import (
    public_literature_source,
    public_research_flow,
    public_source_view,
)
from scientist.source_scope import source_scope_view
from scientist.tool_public import public_tool_source


def source(**updates):
    return SourceRecord(source_id="pmid:SYNTHETIC", provider="europe_pmc", title="SYNTHETIC source",
        url="https://europepmc.org/article/MED/1", retrieved_at="2026-01-01", **updates)


def citation(eid, sid, *, animal=False, polarity="supports", validated=True):
    return EvidenceRecord(evidence_id=eid, source_id=sid, claim="SYNTHETIC claim " * 200,
        quote="SYNTHETIC continuous quotation " * 300, location="SYNTHETIC results", polarity=polarity,
        study_system="SYNTHETIC rats; strain and sex unknown" if animal else "SYNTHETIC pediatric randomized trial",
        condition="SYNTHETIC gavage in animals; no human exposure" if animal else "SYNTHETIC oral adjunctive treatment",
        endpoint="SYNTHETIC rat behavior and qPCR transcription" if animal else "SYNTHETIC symptom endpoint",
        validated_span=validated)


def test_global_accepted_animal_remains_accepted_when_unlinked_without_human_applicability():
    values = [citation("human", "paper:human"), citation("animal", "paper:animal", animal=True),
              citation("pending", "paper:pending", validated=False)]
    before = [e.model_dump() for e in values]
    index = accepted_evidence_index(values, linked_evidence_ids=["human"])
    assert len(index) == 2
    by_id = {row["evidence_id"]: row for row in index}
    assert by_id["animal"]["global_status"] == "accepted"
    assert by_id["animal"]["version_linkage"] == "not_linked"
    assert by_id["animal"]["claim_applicability"] == "not_reviewed_for_current_claim"
    assert by_id["animal"]["study_system"] == values[1].study_system
    assert by_id["animal"]["condition"] == values[1].condition
    assert by_id["animal"]["endpoint"] == values[1].endpoint
    assert by_id["human"]["version_linkage"] == "linked"
    assert "quote" not in by_id["animal"] and "claim" not in by_id["animal"]
    assert len(json.dumps(index).encode()) < len(json.dumps(before).encode()) / 10
    assert [e.model_dump() for e in values] == before


def test_global_polarity_does_not_create_claim_direction_without_explicit_current_rows():
    values = [citation("mixed", "paper:mixed", polarity="mixed")]
    row = {"evidence_id": "mixed", "source_id": "paper:mixed", "claim_id": "claim:h:v2:main",
        "hypothesis_id": "h", "version": 2, "review_id": "review:v2", "verdict": "supported",
        "claim_relation": "supports", "relation_basis": "exact_source_report_only",
        "assessment_scope": "source_observation", "relation_schema_version": 2,
        "raw_prompt": "PRIVATE_SENTINEL"}
    bare = accepted_evidence_index(values, linked_evidence_ids=["mixed"])[0]
    assert bare["claim_applicability"] == "not_reviewed_for_current_claim" and "claim_reviews" not in bare
    explicit = accepted_evidence_index(values, linked_evidence_ids=["mixed"], claim_applicability=[row])[0]
    assert explicit["polarity"] == "mixed" and explicit["claim_reviews"][0]["claim_relation"] == "supports"
    assert explicit["claim_reviews"][0]["assessment_scope"] == "source_observation"
    assert private_sentinel_absent(explicit)
    for foreign in ({**row, "source_id": "different"}, {**row, "review_id": ""}, {**row, "verdict": []}):
        assert "claim_reviews" not in accepted_evidence_index(values, linked_evidence_ids=["mixed"], claim_applicability=[foreign])[0]
    assert "claim_reviews" not in accepted_evidence_index(values, claim_applicability=[row])[0]


def private_sentinel_absent(value):
    return "PRIVATE_SENTINEL" not in json.dumps(value)


def test_fulltext_excerpt_scope_and_original_continuous_window_match_context_and_public_projection():
    text = ("SYNTHETIC result with mixed and negative outcomes. " * 50)[:2262]
    s = source(text=text, evidence_scope="abstract", metadata={"local_excerpt_only": True,
        "original_evidence_scope": "full_text", "excerpt_start": 11889, "excerpt_end": 14151,
        "acquisition_origin": "background_reuse", "raw_prompt": "PRIVATE_SENTINEL"})
    before = s.model_dump()
    expected = {"original_scope": "full_text", "current_scope": "local_excerpt", "local_excerpt": True,
        "original_window": {"start": 11889, "end": 14151}, "acquisition": "reused",
        "acquisition_origin": "background_reuse", "fresh_fulltext": False}
    assert source_scope_view(s) == expected
    context = source_context(s, excerpt_chars=4500, include_prefix=True)
    assert context["scope"] == "local_excerpt" and context["source_scope"] == expected
    assert context["windows"][0] == {"start": 0, "end": 2262, "text": text, "original_start": 11889, "original_end": 14151}
    for project in (public_source_view, public_literature_source, public_tool_source):
        public = project(s.model_dump())
        assert public["evidence_scope"] == "abstract"
        # This tool observation omits text and retains a null window;
        # ordinary source views verify the window against their retained text.
        projected = {**expected, "original_window": None} if project is public_tool_source else expected
        assert public["metadata"]["source_scope"] == projected
        assert project(public)["metadata"]["source_scope"] == projected
        assert private_sentinel_absent(public)
    assert s.model_dump() == before


@pytest.mark.parametrize("metadata,scope,acquisition,fresh", [
    ({}, "full_text", "unknown", False),
    ({"full_text_source_url": "https://europepmc.org/articles/PMC1", "year": "2026"}, "full_text", "unknown", False),
    ({"acquisition_origin": "saved_reuse"}, "full_text", "reused", False),
    ({"acquisition_origin": "current_run_retrieval"}, "abstract", "newly_acquired", False),
    ({"acquisition_origin": "current_run_retrieval", "query_receipt": {"operation": "literature_search", "cache_origin": "exact_query_http_cache"}}, "abstract", "reused", False),
    ({"query_receipt": {"operation": "literature_search", "cache_origin": "live_response"}}, "full_text", "newly_acquired", False),
    ({"acquisition_origin": "current_run_full_text"}, "full_text", "newly_acquired", True),
    ({"query_receipt": {"operation": "full_text_retrieval", "cache_origin": "live_response"}}, "full_text", "newly_acquired", True),
    ({"query_receipt": {"operation": "full_text_retrieval", "cache_origin": "exact_query_http_cache"}}, "full_text", "reused", False),
    ({"acquisition": "background_reuse", "origin": "background_identity_reuse"}, "database", "reused", False),
])
def test_acquisition_and_fresh_fulltext_require_explicit_recorded_facts(metadata, scope, acquisition, fresh):
    s = source(text="SYNTHETIC", evidence_scope=scope, metadata=metadata)
    view = source_scope_view(s)
    assert view["acquisition"] == acquisition and view["fresh_fulltext"] is fresh
    public = public_source_view(s)
    assert public["metadata"]["source_scope"] == view
    assert public_source_view(public)["metadata"]["source_scope"] == view


def test_partial_excerpt_never_becomes_fresh_fulltext_and_missing_window_is_not_invented():
    s = source(text="SYNTHETIC", evidence_scope="abstract", metadata={"local_excerpt_only": True,
        "original_evidence_scope": "full_text", "acquisition_origin": "current_run_full_text"})
    assert source_scope_view(s)["original_window"] is None
    assert source_scope_view(s)["fresh_fulltext"] is False
    s.metadata.update(excerpt_start=100, excerpt_end=300)
    assert source_scope_view(s)["original_window"] is None  # Window length differs from the stored continuous text length.
    s.metadata.update(excerpt_start=True, excerpt_end=10)
    assert source_scope_view(s)["original_window"] is None
    assert source_scope_view({"metadata": {"query_receipt": {"operation": []}}})["acquisition"] == "unknown"


def test_legacy_scope_and_canonical_scope_are_consistent_across_results_and_export_without_mutation():
    run = ResearchRun(run_id="r_synthetic", project_id="p_synthetic", created_at="", updated_at="",
        brief=ResearchBrief(disease="SYNTHETIC"), sources=[source(text="SYNTHETIC", evidence_scope="full_text")])
    before = run.model_dump()
    view, exported = result_document(run), export_document(run)
    for payload in (view, exported):
        scope = payload["sources"][0]["metadata"]["source_scope"]
        assert scope["original_scope"] == scope["current_scope"] == "full_text"
        assert scope["acquisition"] == "unknown" and scope["fresh_fulltext"] is False
    assert exported["sources"][0]["text"] == ""
    assert run.model_dump() == before


def test_claim_relation_public_projection_keeps_typed_list_and_nullable_singleton_without_private_fields():
    relation = {"schema_version": 2, "claim_id": "c1", "hypothesis_id": "h1", "version": 1,
        "evidence_id": "e1", "source_id": "s1", "entity_id": "candidate:synthetic", "relation": "supports",
        "study_system": "SYNTHETIC rats", "condition": "SYNTHETIC gavage", "endpoint": "SYNTHETIC qPCR",
        "raw_prompt": "PRIVATE_SENTINEL"}
    raw = {"claims": [{"claim_id": "c1", "relation_schema_version": 2, "assessment_scope": "source_observation"}],
        "links": [{"claim_relation": relation}, {"claim_relation": None}, {}],
        "claim_reviews": [{"evidence_relations": [relation], "rejected_relation_ids": ["e2"]}]}
    before = copy.deepcopy(raw)
    public = public_research_flow(raw)
    assert public["links"][0]["claim_relation"]["relation"] == "supports"
    assert public["links"][1]["claim_relation"] is None and "claim_relation" not in public["links"][2]
    assert public["claim_reviews"][0]["evidence_relations"][0] == public["links"][0]["claim_relation"]
    assert public["claim_reviews"][0]["rejected_relation_ids"] == ["e2"]
    assert public["claims"][0]["assessment_scope"] == "source_observation"
    assert private_sentinel_absent(public) and raw == before

