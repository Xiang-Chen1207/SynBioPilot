"""SYNTHETIC offline counts and safe exports; no biomedical efficacy assertions."""
import copy
import hashlib
import json

from scientist.co_models import CoScientistState
from scientist.exports import export_document
from scientist.flow_models import PublicationGroup, QueryReceipt, ResearchFlowState, SourceSnapshot
from scientist.models import (
    CandidateRecord,
    EvidenceRecord,
    ResearchBrief,
    ResearchRun,
    SourceRecord,
)
from scientist.presentation import result_document
from scientist.research_public import public_literature_source, research_observation_counts
from scientist.tool_public import (
    public_budget,
    public_budget_failure,
    public_entity_reference,
    public_tool_plan,
    public_tool_source,
)


def synthetic_run():
    run = ResearchRun(run_id="r_synthetic_scope", project_id="p_synthetic", created_at="", updated_at="",
                      brief=ResearchBrief(disease="SYNTHETIC observability fixture"))
    sources = [SourceRecord(source_id=f"paper:{i}", provider="europe_pmc", title=f"SYNTHETIC paper {i}",
               url="https://europepmc.org/article/MED/1", retrieved_at="", text="SYNTHETIC abstract",
               evidence_scope="abstract") for i in range(15)]
    sources += [SourceRecord(source_id=f"db:{i}", provider="pubchem", title="SYNTHETIC database",
                url="https://pubchem.ncbi.nlm.nih.gov/compound/1", retrieved_at="", evidence_scope="database")
                for i in range(2)]
    run.sources = sources[:12] + sources[15:]
    run.evidence = [EvidenceRecord(evidence_id=f"e_synthetic_{i}", source_id=f"paper:{i % 4}",
        claim="SYNTHETIC citation", quote="SYNTHETIC abstract", location="SYNTHETIC abstract",
        polarity="context", validated_span=True) for i in range(7)]
    flow = ResearchFlowState()
    for s in sources:
        flow.publications.append(PublicationGroup(paper_id="group:" + s.source_id,
            kind="database" if s.evidence_scope == "database" else "paper",
            snapshots=[SourceSnapshot(snapshot_id="snapshot:" + s.source_id, source_id=s.source_id,
                provider=s.provider, title=s.title, url=s.url, retrieved_at="", scope=s.evidence_scope,
                text_sha256=hashlib.sha256(s.text.encode()).hexdigest())]))
    flow.queries = [QueryReceipt(query_id=f"q_{i}", query="SYNTHETIC scope", purpose="SYNTHETIC",
        stage="landscape" if i < 4 else "full_text" if i < 6 else "supplement",
        request_count=1 if i < 6 else 0, status="truncated" if i < 4 else "ok" if i < 6 else "skipped")
        for i in range(28)]
    run.co_scientist = CoScientistState(research_flow=flow, tool_calls=[{"tool_name":"europepmc_search",
        "status":"ok", "sources":[s.model_dump() for s in sources[12:15]], "records":[]}])
    return run


def test_synthetic_counts_distinguish_ingested_raw_papers_databases_plans_and_reviews():
    run = synthetic_run()
    before = run.model_dump(mode="json")
    expected = {
        "canonical_paper_count":15, "database_group_count":2,
        "ingested_paper_group_count":12, "tool_observation_only_paper_group_count":3,
        "accepted_citation_count":7, "accepted_citation_source_count":4,
        "formal_hypothesis_review_count":0, "query_plan_count":28,
        "executed_literature_query_count":4, "executed_full_text_count":2,
        "reused_query_count":0, "unexecuted_query_count":22,
    }
    for document in (research_observation_counts(run), result_document(run)["summary"], export_document(run)["summary"]):
        assert {key:document[key] for key in expected} == expected
    assert run.model_dump(mode="json") == before


def test_receipt_reuse_unknown_execution_and_failed_http_do_not_become_fresh_success():
    run = synthetic_run()
    flow = run.co_scientist.research_flow
    flow.queries = [QueryReceipt(query_id="q_reuse", stage="reuse", purpose="SYNTHETIC", query="saved", status="reused"),
        QueryReceipt(query_id="q_legacy", stage="landscape", purpose="SYNTHETIC", query="legacy", status="ok"),
        QueryReceipt(query_id="q_failure", stage="landscape", purpose="SYNTHETIC", query="failed", status="unavailable", request_count=1)]
    counts = research_observation_counts(run)
    assert counts["reused_query_count"] == counts["unknown_execution_query_count"] == 1
    assert counts["executed_literature_query_count"] == 1  # query-attempt count; accepted results are counted separately
    assert counts["unexecuted_query_count"] == 0


def test_budget_failure_and_optional_skip_exports_keep_sizes_but_drop_private_fields():
    run = synthetic_run()
    failure = {"reason":"input_reservation", "stage":"reflection", "required_bytes":80489,
        "remaining_bytes":39451, "remaining_seconds":86.0, "required_calls":1, "remaining_calls":9,
        "blocked_before_provider":True, "prior_timeout":True, "accounting_unit":"utf8_bytes",
        "prompt":"PRIVATE_SENTINEL", "raw_diagnostics":{"stderr":"PRIVATE_SENTINEL"}}
    run.co_scientist.budget = {"input_tokens_reserved":260549, "output_bytes_received":99999, "limits":{"max_input_tokens":300000,
        "prompt":"PRIVATE_SENTINEL"}, "budget_failures":[failure], "optional_skips":[{**failure,
        "reason":"preserve_critical_review", "reserved_calls":2, "reserved_bytes":20000}], "credentials":"PRIVATE_SENTINEL"}
    run.co_scientist.budget_failures = [failure]
    run.co_scientist.optional_skips = [failure]
    before = copy.deepcopy(run.co_scientist.budget)
    document = export_document(run)
    budget = document["co_scientist"]["budget"]
    assert budget["budget_failures"][0]["required_bytes"] == 80489
    assert budget["budget_failures"][0]["blocked_before_provider"] is True
    assert budget["optional_skips"][0]["reserved_calls"] == 2
    assert budget["output_bytes_received"] == 99999
    assert document["co_scientist"]["budget_failures"][0]["remaining_bytes"] == 39451
    assert "PRIVATE_SENTINEL" not in json.dumps(document["co_scientist"]["optional_skips"])
    assert "PRIVATE_SENTINEL" not in json.dumps(budget)
    assert run.co_scientist.budget == before
    assert public_budget_failure({"required_bytes":float("inf"),"remaining_bytes":10**1000,"reason":"password=PRIVATE_SENTINEL"}) == {}
    assert public_budget({"http_attempts":-1,"raw_prompt":"PRIVATE_SENTINEL"}) == {}


def test_actual_publication_metadata_and_retrieval_scope_survive_safe_projection():
    s = synthetic_run().sources[0]
    s.metadata = {"authors":["SYNTHETIC Author", "raw_prompt=PRIVATE_SENTINEL"], "journal":"SYNTHETIC Journal",
        "search_scope":{"total_hits":2017,"sort":"relevance","pagination":"first_bounded_page",
            "hit_range_start":1,"hit_range_end":6,"has_more":True,"coverage":"bounded_subset",
            "headers":{"Authorization":"PRIVATE_SENTINEL"}}, "selection_reason":"matches object and endpoint",
        "retrieval_facet":"foundational_primary", "coverage_gaps":["Unfetched hits remain"],
        "full_text_selected":True,"provider_object":{"prompt":"PRIVATE_SENTINEL"}}
    before = s.model_dump()
    for doc in (public_literature_source(s), public_tool_source(s.model_dump())):
        assert doc["metadata"]["authors"] == ["SYNTHETIC Author"]
        assert doc["metadata"]["journal"] == "SYNTHETIC Journal"
        assert doc["metadata"]["search_scope"]["total_hits"] == 2017
        assert doc["metadata"]["search_scope"]["has_more"] is True
        assert "PRIVATE_SENTINEL" not in json.dumps(doc)
    assert s.model_dump() == before
    assert "authors" not in public_literature_source(s.model_copy(update={"metadata":{}}))["metadata"]


def test_entity_references_and_plan_binding_remain_safe_scientific_annotations():
    entity = {"entity_id":"candidate_original", "candidate_id":"mol_synthetic", "identity_status":"resolved",
        "identity_revision":1,"name":"SYNTHETIC candidate","aliases":["SYNTHETIC alias"], "source_ids":["pubchem:1"],
        "hypothesis_versions":[{"hypothesis_id":"h_synthetic", "version":1,
            "historical_identity_gaps":["PubChem unresolved in this historical version"], "prompt":"PRIVATE_SENTINEL"}],
        "scientific_assessment":"Versioned scientific claims and priority have not been reassessed", "raw_prompt":"PRIVATE_SENTINEL"}
    public = public_entity_reference(entity)
    assert public["hypothesis_versions"][0]["historical_identity_gaps"] == entity["hypothesis_versions"][0]["historical_identity_gaps"]
    assert public["identity_revision"] == 1
    assert "PRIVATE_SENTINEL" not in json.dumps(public)
    plan = public_tool_plan({"candidate_link":{"status":"verified", "entity_id":"candidate_original",
        "verification":"unique_exact_recorded_alias", "prompt":"PRIVATE_SENTINEL"},
        "identity_application":{"status":"applied", "identity_revision":1,"scientific_assessment":"unchanged"}})
    assert plan["identity_application"]["scientific_assessment"] == "unchanged"
    assert "PRIVATE_SENTINEL" not in json.dumps(plan)
    run = synthetic_run()
    run.co_scientist.entity_references = [entity]
    run.co_scientist.seed = {"parent_run_id":"r_synthetic_parent", "raw_prompt":"PRIVATE_SENTINEL"}
    run.candidates = [CandidateRecord(candidate_id="mol_synthetic", name="SYNTHETIC candidate",
        entity_id="candidate_original", identity_revision=1, identity_status="resolved",
        identity_form={"isomeric_smiles":"CCO", "components":["CCO"], "prompt":"PRIVATE_SENTINEL"},
        identity_history=[{"application_status":"applied", "previous_candidate_id":"candidate_original",
            "historical_identity_gaps":["Previously unresolved"], "raw_prompt":"PRIVATE_SENTINEL",
            "previous_identity":{"smiles":"", "provider_payload":"PRIVATE_SENTINEL"}}])]
    before = run.model_dump()
    doc = export_document(run)
    assert doc["candidates"][0]["identity_history"][0]["historical_identity_gaps"] == ["Previously unresolved"]
    assert doc["co_scientist"]["entity_references"][0]["identity_revision"] == 1
    assert doc["co_scientist"]["seed"] == {"parent_run_id":"r_synthetic_parent"}
    assert "PRIVATE_SENTINEL" not in json.dumps(doc)
    assert run.model_dump() == before

