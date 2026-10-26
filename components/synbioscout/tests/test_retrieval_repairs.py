"""SYNTHETIC offline reproductions; these fixtures are not biomedical findings."""
from types import SimpleNamespace

import pytest

from scientist.budgets import BudgetExceeded
from scientist.co_models import CoScientistState
from scientist.models import ResearchBrief, ResearchBudget, ResearchRun, SourceRecord
from scientist.research import PlanDraft, ResearchEngine, retrieval_queries
from scientist.research_flow import ResearchFlow
from scientist.sources import BiomedicalSources, IdentityResolutionError


def paper(sid, title, text, *, types=(), year="2026", oa=True):
    return SourceRecord(source_id=sid, provider="europe_pmc", title="SYNTHETIC " + title,
        text=text, url="https://pubmed.ncbi.nlm.nih.gov/1/", retrieved_at="",
        pmcid="PMC" + sid if oa else "", metadata={"synthetic": True,
        "is_open_access": "Y" if oa else "N", "year": year,
        "publication_types": {"pubType": list(types)}})


def task(*, searches=2, targets=0, papers=6):
    run = ResearchRun(run_id="r_synthetic", project_id="p_synthetic", created_at="", updated_at="",
        brief=ResearchBrief(disease="Marker syndrome", existing_candidates=["Compound Z (CZ)"],
            phenotype="marker endpoint", budget=ResearchBudget(max_targets=targets,
            max_searches=searches, max_papers=papers)))
    plan = PlanDraft(disease_english="Marker syndrome", phenotype_english="marker endpoint",
                     searches=['"Marker syndrome" AND intervention'], research_questions=[], assumptions=[])
    return run, plan


class FixtureSources:
    def __init__(self, records=(), hits=()):
        self.records, self.hits = list(records), list(hits)
        self.queries, self.deep_reads, self.associations, self.names = [], [], [], []

    def search_entity(self, text, entity):
        self.names.append((text, entity))
        return self.hits

    def disease_targets(self, disease_id, limit):
        self.associations.append(disease_id)
        return {"id": disease_id, "name": "Marker syndrome"}

    def database_source(self, key, title, value, url):
        return SourceRecord(source_id=key, provider="open_targets", title=title, url=url,
                            retrieved_at="", text="SYNTHETIC association fixture", evidence_scope="database", metadata=value)

    def search_literature(self, query, limit=8, *, sort="relevance", facet=""):
        self.queries.append((query, limit, sort, facet))
        return [s.model_copy(deep=True) for s in self.records[:limit]]

    def full_text(self, source):
        if source.pmcid and source.metadata.get("is_open_access") == "Y":
            self.deep_reads.append(source.source_id)
            return source.model_copy(update={"evidence_scope": "full_text"})


def retrieve(run, plan, sources):
    engine = ResearchEngine.__new__(ResearchEngine)
    engine.sources, engine.store = sources, None
    engine._retrieve(run, plan)
    return engine


def test_limited_facets_include_original_contrary_recent_and_mechanism_without_acronym_only():
    run, plan = task(searches=2)
    queries = retrieval_queries(run, plan, 2)
    assert len(queries) == 2
    assert queries[0][1:] == ("relevance", "foundational_trials,contrary")
    assert queries[1][1:] == ("date", "recent,mechanism")
    assert "randomized" in queries[0][0] and "no effect" in queries[0][0]
    assert '"Compound Z"' in queries[0][0] and ' OR "CZ"' not in queries[0][0]
    assert "mechanism" in queries[1][0]
    assert len(retrieval_queries(run, plan, 1)) == 1
    assert len(retrieval_queries(run, plan, 4)) == 4


def test_lexical_query_leads_are_preserved_without_rebinding_task_names_or_disease_identity():
    run, plan = task(searches=2)
    plan.searches = ['"Marker syndrome" AND ("Alternate Compound spelling" OR CZ)']
    before = run.brief.model_dump()
    queries = retrieval_queries(run, plan, 2)
    assert all(plan.searches[0] in query for query, _, _ in queries)
    assert all('"Compound Z"' in query for query, _, _ in queries)
    assert run.brief.model_dump() == before


def test_competing_intervention_trial_is_not_ranked_above_specified_candidate_trial():
    from scientist.research import literature_priority
    run, plan = task()
    competing = paper("1", "Marker syndrome Other Compound randomized trial",
        "Marker syndrome Other Compound improved outcomes. Compound Z is discussed as background.", types=["Randomized Controlled Trial"])
    specified = paper("2", "Marker syndrome Compound Z randomized trial",
        "Marker syndrome Compound Z endpoint measured.", types=["Randomized Controlled Trial"], year="2009")
    other = literature_priority(competing, run, plan)
    correct = literature_priority(specified, run, plan)
    assert correct[0] > other[0]
    assert "candidate_only_in_abstract_intervention_unconfirmed" in other[1]


def test_old_original_and_negative_trial_receive_fulltext_before_recent_reviews_and_wrong_object():
    recent = paper("1", "Marker syndrome Compound Z review", "Marker syndrome Compound Z mechanism overview.", types=["Review"])
    wrong_object = paper("2", "Different syndrome Compound Z randomized trial", "Different syndrome Compound Z showed benefit.", types=["Randomized Controlled Trial"])
    original = paper("3", "Marker syndrome Compound Z randomized trial", "Marker syndrome Compound Z marker endpoint improved.", types=["Randomized Controlled Trial"], year="2009")
    negative = paper("4", "Marker syndrome Compound Z controlled trial", "Marker syndrome Compound Z had no improvement in marker endpoint.", types=["Randomized Controlled Trial"], year="2014")
    mechanism = paper("5", "Marker syndrome Compound Z cellular mechanism", "Marker syndrome Compound Z in vitro cells pathway assay.")
    sources = FixtureSources([recent, wrong_object, original, negative, mechanism])
    run, plan = task(searches=1, papers=5)
    retrieve(run, plan, sources)
    assert sources.deep_reads[:2] == [negative.source_id, original.source_id]
    assert sources.deep_reads[2] == mechanism.source_id
    assert len(sources.queries) == 1 and len(run.sources) == 5
    assert all(s.evidence_scope == "abstract" for s in run.sources if s.source_id in {"1", "2"})
    assert "reported_primary_trial" in next(s for s in run.sources if s.source_id == "3").metadata["selection_reason"]
    assert "object_or_intervention_match_unconfirmed" in next(s for s in run.sources if s.source_id == "2").metadata["selection_reason"]
    assert run.evidence == []  # Retrieval selection never approves scientific claims.


def test_no_primary_or_contrary_hit_records_gap_without_asserting_nonexistence():
    sources = FixtureSources([paper("1", "Marker syndrome Compound Z review", "Marker syndrome Compound Z context.", types=["Review"], oa=False)])
    run, plan = task(searches=2)
    retrieve(run, plan, sources)
    assert len(sources.queries) == 2
    assert sources.deep_reads == []
    gaps = run.sources[0].metadata["coverage_gaps"]
    assert "no_retrieved_relevant_primary_trial" in gaps
    assert "no_retrieved_relevant_contrary_or_mixed_result" in gaps
    assert any("retrieval scope and uncovered ranges are recorded" in w for w in run.warnings)


@pytest.mark.parametrize("error,gap", [(TimeoutError("SYNTHETIC timeout"), "full_text_stopped_by_deadline"),
    (BudgetExceeded("SYNTHETIC shared budget"), "full_text_stopped_by_shared_budget")])
def test_fulltext_timeout_retains_retrieved_abstracts_without_accepting_evidence(error, gap):
    sources = FixtureSources([paper("1", "Marker syndrome Compound Z randomized trial",
        "Marker syndrome Compound Z marker endpoint.", types=["Randomized Controlled Trial"])])
    sources.full_text = lambda source: (_ for _ in ()).throw(error)
    run, plan = task(searches=1)
    retrieve(run, plan, sources)
    assert len(run.sources) == 1 and run.sources[0].evidence_scope == "abstract"
    assert gap in run.sources[0].metadata["coverage_gaps"]
    assert run.evidence == [] and run.targets == run.candidates == []


@pytest.mark.parametrize("hits", [
    [{"id": "MONDO_1", "name": "Marker syndrome due to gene Q deficiency"}],
    [{"id": "MONDO_1", "name": "Marker syndrome"}, {"id": "EFO_2", "name": "Marker syndrome"}],
])
def test_approximate_subtype_and_multiple_exact_disease_names_never_query_associations(hits):
    sources = FixtureSources(hits=hits)
    run, plan = task(targets=1, searches=1)
    retrieve(run, plan, sources)
    assert sources.associations == []
    assert any("association lookup skipped; candidates" in w for w in run.warnings)
    assert any(hits[0]["id"] in w for w in run.warnings)


def test_pure_literature_mode_skips_both_disease_search_and_associations():
    sources = FixtureSources(hits=[{"id": "MONDO_1", "name": "Marker syndrome"}])
    run, plan = task(targets=0, searches=1)
    retrieve(run, plan, sources)
    assert sources.names == sources.associations == []


def test_real_connector_requires_separate_confirmed_ontology_record_before_resolving():
    calls = []
    def post(url, payload):
        calls.append(payload)
        if "DiseaseIdentity" in payload["query"]:
            return {"data": {"disease": {"id": "MONDO_123", "name": "Marker syndrome"}}}
        return {"data": {"search": {"hits": [{"id": "MONDO_123", "name": "Marker syndrome"}]}}}
    provider = BiomedicalSources(SimpleNamespace(post_json=post))
    assert provider.resolve_disease("Marker syndrome")["identity_status"] == "resolved"
    assert len(calls) == 2 and all("associatedTargets" not in c["query"] for c in calls)
    provider._graphql = lambda *a: {"disease": {"id": "MONDO_123", "name": "Marker syndrome subtype"}}
    provider.search_entity = lambda *a: [{"id": "MONDO_123", "name": "Marker syndrome"}]
    with pytest.raises(IdentityResolutionError, match="does not confirm"):
        provider.resolve_disease("Marker syndrome")


def test_bounded_full_disease_search_cannot_claim_unique_identity():
    provider = BiomedicalSources(SimpleNamespace())
    provider.search_entity = lambda *a: [{"id": "MONDO_1", "name": "Marker syndrome"}] + [
        {"id": "MONDO_" + str(i), "name": "Marker subtype " + str(i)} for i in range(2, 6)]
    with pytest.raises(IdentityResolutionError) as failure:
        provider.resolve_disease("Marker syndrome")
    assert failure.value.identity_status == "truncated"


def test_connector_records_real_scope_sort_and_provider_bibliography_without_eager_paging():
    calls = []
    def get(url, *, params):
        calls.append(params)
        return {"hitCount": 2017, "nextCursorMark": "synthetic_next", "resultList": {"result": [
            {"pmid": "1", "title": "SYNTHETIC paper", "abstractText": "SYNTHETIC abstract",
             "authorList": {"author": [{"fullName": "SYNTHETIC Author"}]},
             "journalInfo": {"journal": {"title": "SYNTHETIC Journal"}}},
            {"pmid": "2", "title": "SYNTHETIC paper two"}, {"pmid": "3", "title": "SYNTHETIC over-return"}]}}
    provider = BiomedicalSources(SimpleNamespace(get_json=get))
    values = provider.search_literature("SYNTHETIC query", 2, facet="foundational_trials")
    assert len(values) == 2 and len(calls) == 1
    assert "sort_date" not in calls[0] and calls[0]["cursorMark"] == "*"
    scope = values[0].metadata["search_scope"]
    assert scope["total_hits"] == 2017 and scope["hit_range_start"] == 1 and scope["hit_range_end"] == 2
    assert scope["coverage"] == "bounded_subset" and scope["has_more"] is True
    assert values[0].metadata["authors"] == ["SYNTHETIC Author"]
    assert values[0].metadata["journal"] == "SYNTHETIC Journal"
    assert values[1].metadata["authors"] == [] and values[1].metadata["journal"] == ""
    provider.search_literature("SYNTHETIC recent", 2, sort="date", cursor_mark="synthetic_next")
    assert calls[1]["sort_date"] == "y"
    assert provider.last_literature_scope["hit_range_start"] is None


def test_missing_hitcount_and_cursor_do_not_claim_complete_coverage():
    provider = BiomedicalSources(SimpleNamespace(get_json=lambda *a, **k: {"resultList": {"result": []}}))
    assert provider.search_literature("SYNTHETIC missing scope", 1) == []
    assert provider.last_literature_scope["has_more"] is None
    assert provider.last_literature_scope["coverage"] == "total_unknown"


@pytest.mark.parametrize("total", [1, 2, 3, 4, 5, 8])
def test_stage_queries_share_same_total_with_two_landscape_queries_when_room_exists(total):
    run, _ = task(searches=total)
    engine = SimpleNamespace(run=run, state=CoScientistState(), persist=lambda: None)
    flow = ResearchFlow(engine)
    assert flow.data.stage_limits["landscape"] == (2 if total >= 4 else 1)
    assert sum(v for k, v in flow.data.stage_limits.items() if k != "full_text") == total


def test_search_receipt_and_fulltext_reason_preserve_safe_actual_selection_fields():
    run, _ = task()
    s = paper("1", "Marker syndrome Compound Z randomized trial", "SYNTHETIC exact quotation", types=["Randomized Controlled Trial"])
    s.metadata.update(authors=["SYNTHETIC Author"], journal="SYNTHETIC Journal",
        selection_reason="task_object_match; reported_primary_trial", coverage_gaps=["bounded_subset"],
        search_scope={"total_hits": 2017, "facet": "foundational_trials,contrary", "sort": "relevance",
                      "pagination": "first_bounded_page", "hit_range_start": 1, "hit_range_end": 1, "has_more": True})
    impl = SimpleNamespace(full_text=lambda source: source, http=SimpleNamespace(request_count=0, cache_hits=0))
    engine = SimpleNamespace(run=run, state=CoScientistState(), persist=lambda: None,
                             sources=SimpleNamespace(source=impl, http=impl.http))
    flow = ResearchFlow(engine)
    q = flow.before_search("SYNTHETIC query")
    flow.search_finished(q, [s], 1)
    assert q.total_hits == 2017 and q.sort == "relevance" and q.facet == "foundational_trials,contrary"
    assert (q.hit_range_start, q.hit_range_end, q.has_more) == (1, 1, True)
    assert flow.data.publications[0].snapshots[0].authors == ["SYNTHETIC Author"]
    flow.full_text(s)
    assert flow.data.queries[-1].selection_reason == "task_object_match; reported_primary_trial"
    assert flow.data.queries[-1].coverage_gaps == ["bounded_subset"]


def test_coscientist_wrapper_shares_two_facet_queries_and_two_ranked_fulltext_reads():
    import time

    from scientist.budgets import RunLedger
    from scientist.coscientist import BoundedSources
    run, plan = task(searches=5, papers=12)
    records = [paper(str(i), "Marker syndrome Compound Z randomized trial",
        "Marker syndrome Compound Z " + ("no improvement" if i == 1 else "marker endpoint"),
        types=["Randomized Controlled Trial"], year="2009") for i in range(1, 4)]
    impl = FixtureSources(records)
    impl.http = SimpleNamespace(request_count=0, cache_hits=0)
    engine = SimpleNamespace(run=run, state=CoScientistState(), persist=lambda: None,
        ledger=RunLedger(run.brief.budget, time.monotonic() + 60))
    engine.sources = BoundedSources(impl, engine)
    engine.flow = ResearchFlow(engine)
    initial = run.model_copy(deep=True)
    initial.brief.budget.max_papers = 6  # Same half-quota reservation as the real coordinator.
    retrieve(initial, plan, engine.sources)
    run.sources = initial.sources
    assert engine.sources.search_count == len(impl.queries) == 2
    assert len(impl.deep_reads) == 2 and impl.deep_reads[0] == "1"
    assert engine.flow.data.stage_usage["landscape"] == engine.flow.data.stage_usage["full_text"] == 2
    assert all(q.selection_reason for q in engine.flow.data.queries if q.stage == "full_text")
    assert len(run.sources) == 3 and len(run.evidence) == 0


@pytest.mark.parametrize("scope,status,unknown", [
    ({"total_hits": 1, "has_more": False}, "ok", False),
    ({"total_hits": 2017, "has_more": True}, "truncated", False),
    ({"total_hits": 2017}, "truncated", False),
    ({"total_hits": 2017, "has_more": False}, "truncated", False),
    ({}, "truncated", True),
    ({"total_hits": True}, "truncated", True),
])
def test_literature_tool_status_uses_known_actual_scope_instead_of_return_count(scope, status, unknown):
    from scientist.tools import LiteratureArguments, ToolRegistry, ToolResult
    s = paper("1", "Fixture", "SYNTHETIC abstract", oa=False)
    s.metadata["search_scope"] = scope
    # Invoke just the deterministic tool adapter, with no HTTP or implicit body retrieval.
    tools = ToolRegistry.__new__(ToolRegistry)
    tools.sources = SimpleNamespace(search_literature=lambda query, limit: [s])
    result = ToolResult(tool_name="europepmc_search", provider="europe_pmc", parameters={}, status="invalid", retrieved_at="")
    tools._europepmc_search(LiteratureArguments(query="SYNTHETIC query", limit=1), result)
    assert result.status == status
    assert any("coverage is unknown" in warning for warning in result.warnings) is unknown
    assert result.records[0]["evidence_scope"] == "abstract"


def test_literature_tool_does_not_reuse_previous_queries_scope():
    from scientist.tools import LiteratureArguments, ToolRegistry, ToolResult
    s = paper("1", "Fixture", "SYNTHETIC abstract", oa=False)
    tools = ToolRegistry.__new__(ToolRegistry)
    tools.sources = SimpleNamespace(search_literature=lambda query, limit: [s],
        last_literature_scope={"query": "SYNTHETIC different query", "total_hits": 1, "has_more": False})
    result = ToolResult(tool_name="europepmc_search", provider="europe_pmc", parameters={}, status="invalid", retrieved_at="")
    tools._europepmc_search(LiteratureArguments(query="SYNTHETIC current query", limit=1), result)
    assert result.status == "truncated" and any("coverage is unknown" in w for w in result.warnings)

