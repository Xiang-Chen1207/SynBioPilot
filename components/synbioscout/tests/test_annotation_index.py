"""SYNTHETIC normal-connector/cache tests; no external requests or LLM calls."""
import json

import pytest
from test_retrieval_repairs import task
from test_tool_http_identity import http_with_responses, protein, response

from scientist.models import TargetCard
from scientist.research import ResearchEngine, retrieval_queries
from scientist.sources import BiomedicalSources


def target_row(gene, accession, number):
    return {"score": .8, "datatypeScores": [{"id": "genetic_association", "score": .8}],
            "target": {"id": "ENSG00000" + str(number), "approvedSymbol": gene,
                       "approvedName": "SYNTHETIC " + gene,
                       "proteinIds": [{"id": accession, "source": "uniprot_swissprot"}]}}


def setup_normal_path(tmp_path, monkeypatch, *, details=None):
    disease = {"id": "EFO_SYNTHETIC", "name": "Marker syndrome"}
    annotation = {**disease, "associatedTargets": {"rows": [target_row("ALPHA", "Q11111", 1),
                                                             target_row("BETA", "Q22222", 2)]}}
    replies = [response(200, {"data": {"search": {"hits": [disease]}}}),
               response(200, {"data": {"disease": disease}}),
               response(200, {"data": {"disease": annotation}}),
               response(200, {"hitCount": 0, "resultList": {"result": []}}),
               response(200, {"results": [protein("Q11111", gene="ALPHA")]}),
               response(200, {"results": [protein("Q22222", gene="BETA")]})]
    if details is not None:
        replies.append(response(200, {"data": {"target": details}}))
    http, ledger, calls = http_with_responses(tmp_path, monkeypatch, *replies, max_requests=12)
    sources = BiomedicalSources(http)
    run, plan = task(searches=1, papers=2, targets=2)
    run.brief.existing_candidates = []
    engine = ResearchEngine.__new__(ResearchEngine)
    engine.sources, engine.store = sources, None
    engine._retrieve(run, plan)
    run.targets = [TargetCard(target_id="t_" + symbol, gene_symbol=symbol, desired_direction="inhibit")
                   for symbol in ("ALPHA", "BETA")]
    engine._resolve_identities(run)
    return engine, run, plan, http, ledger, calls


def test_two_target_identities_share_normal_http_ledger_and_associations_do_not_imply_direction(tmp_path, monkeypatch):
    engine, run, plan, http, ledger, calls = setup_normal_path(tmp_path, monkeypatch)
    assert len(run.targets) == 2 and len(calls) == ledger.counts["http_attempts"] == 6
    assert {t.uniprot_id for t in run.targets} == {"Q11111", "Q22222"}
    for target in run.targets:
        assert target.desired_direction == "unknown"
        assert target.assessment["druggability"] == "unknown"
        assert target.assessment["open_targets_tractability"] == "not_requested_association_only"
        association = target.assessment["association"]
        assert association["disease_id"] == "EFO_SYNTHETIC"
        assert association["species"] == "Homo sapiens"
        assert len(association["source_text_sha256"]) == 64
    original = {s.source_id: s.metadata.get("query_receipt", {}) for s in run.sources}
    assert all(r["cache_origin"] == "live_response" for r in original.values() if r)
    assert len(list((http.cache_dir / "annotation_index_v1").glob("*.json"))) == 6
    # Same real connector entry points must hit the normal exact HTTP cache.
    engine._retrieve(run, plan)
    engine._resolve_identities(run)
    assert len(calls) == 6 and ledger.counts["http_attempts"] == 6
    assert ledger.counts["cache_hits"] == 6
    for source in run.sources:
        receipt = source.metadata.get("query_receipt")
        if receipt:
            assert receipt["cache_origin"] == "exact_query_http_cache"
            assert receipt["source_retrieved_at"] == original[source.source_id]["source_retrieved_at"]
            assert receipt["response_sha256"] == original[source.source_id]["response_sha256"]
            assert len(receipt["request_cache_keys"]) == 1
    assert run.evidence == [] and run.candidates == []


def test_cached_query_is_species_bound_and_different_species_never_uses_prior_protein_identity(tmp_path, monkeypatch):
    http, ledger, calls = http_with_responses(tmp_path, monkeypatch,
        response(200, {"results": [protein("Q11111", gene="ALPHA")]}),
        response(200, {"results": [protein("P11111", gene="ALPHA", species="Mus musculus")]}))
    provider = BiomedicalSources(http)
    _, human = provider.resolve_target("ALPHA", "Homo sapiens")
    _, mouse = provider.resolve_target("ALPHA", "Mus musculus")
    _, cached = provider.resolve_target("ALPHA", "Homo sapiens")
    assert len(calls) == ledger.counts["http_attempts"] == 2
    assert human.source_id != mouse.source_id
    assert human.metadata["query_receipt"]["query_key"] != mouse.metadata["query_receipt"]["query_key"]
    assert cached.metadata["query_receipt"]["cache_origin"] == "exact_query_http_cache"
    assert cached.metadata["query_receipt"]["parameters"]["species"] == "Homo sapiens"


@pytest.mark.parametrize("mismatch", ["run", "disease", "species", "official_id"])
def test_target_annotations_require_current_verified_disease_and_species_scope(tmp_path, monkeypatch, mismatch):
    engine, run, _, _, _, calls = setup_normal_path(tmp_path, monkeypatch)
    record = next(s for s in run.sources if s.provider == "open_targets")
    scope = record.metadata["identity_scope"]
    if mismatch == "run": scope["run_id"] = "r_other"
    elif mismatch == "disease": scope["task_disease"] = "Different syndrome"
    elif mismatch == "species": scope["species"] = "Mus musculus"
    else: record.metadata["id"] = "EFO_OTHER"
    target = run.targets[0]
    engine._target_due_diligence(run, target, target.uniprot_id)
    assert "association" not in target.assessment
    assert target.assessment["open_targets_tractability"] == "not_retrieved_or_identity_ambiguous"
    assert len(calls) == 6


def test_optional_tractability_response_cannot_switch_official_target_identity(tmp_path, monkeypatch):
    from test_target_tractability_admission import synthesize_direction_review
    details = {**target_row("BETA", "Q22222", 2)["target"], "tractability": [{"label": "SYNTHETIC", "value": True}]}
    engine, run, _, _, _, calls = setup_normal_path(tmp_path, monkeypatch, details=details)
    target, _ = synthesize_direction_review(engine, run, tmp_path / "synthesis")
    engine._target_due_diligence(run, target, target.uniprot_id)
    assert target.assessment["open_targets_tractability"] == "identity_mismatch"
    assert "tractability" not in target.assessment
    assert len(calls) == 7
    assert not any(s.source_id.startswith("ot:target:") for s in run.sources)


def test_query_index_does_not_expose_other_queries_or_inject_candidate_names(tmp_path, monkeypatch):
    http, ledger, calls = http_with_responses(tmp_path, monkeypatch,
        response(200, {"hitCount": 1, "resultList": {"result": [{"pmid": "1", "title": "SYNTHETIC Compound Z trial", "abstractText": "SYNTHETIC"}]}}),
        response(200, {"hitCount": 0, "resultList": {"result": []}}))
    provider = BiomedicalSources(http)
    first = provider.search_literature('TITLE_ABS:"Marker syndrome"', limit=2)
    assert provider.search_literature('TITLE_ABS:"Different condition"', limit=2) == []
    repeated = provider.search_literature('TITLE_ABS:"Marker syndrome"', limit=2)
    assert first[0].source_id == repeated[0].source_id
    assert len(calls) == 2 and ledger.counts["cache_hits"] == 1
    assert repeated[0].metadata["query_receipt"]["cache_origin"] == "exact_query_http_cache"
    entries = [json.loads(p.read_text()) for p in (http.cache_dir / "annotation_index_v1").glob("*.json")]
    assert all("Compound Z" not in json.dumps(e) for e in entries)  # Receipts contain no corpus/response body.


def test_disease_exploration_queries_are_title_abstract_bounded_and_cover_opposition_and_mechanism():
    run, plan = task(searches=5, targets=2)
    run.brief.existing_candidates = []
    queries = retrieval_queries(run, plan, 2)
    assert len(queries) == 2
    assert all('TITLE_ABS:("Marker syndrome")' in q for q, _, _ in queries)
    assert all("Compound Z" not in q for q, _, _ in queries)
    assert "PUB_TYPE:\"Randomized Controlled Trial\"" in queries[0][0]
    assert "no statistically significant difference" in queries[0][0]
    assert "mechanism" in queries[1][0] and queries[1][1] == "date"

