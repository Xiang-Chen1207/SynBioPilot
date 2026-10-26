"""SYNTHETIC background packages and reused-inventory acquisition regressions.

No remote requests or biomedical assertions are made by these tests.
"""
import hashlib
import json
from types import SimpleNamespace

import pytest
from test_retrieval_repairs import FixtureSources, paper, retrieve, task

from scientist.background import load_background_package, relevant_excerpt
from scientist.co_models import CoScientistState
from scientist.models import SourceRecord
from scientist.research_flow import ResearchFlow


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def write_package(root, *, text=None, scope="abstract"):
    text = text or ("SYNTHETIC adults with Marker syndrome received Compound Z. "
        "Results: marker endpoint improved; the primary endpoint did not differ from placebo. "
        "Adverse events were mixed; no clinical recommendation follows.")
    base = root / "knowledge" / "synthetic_v1"
    (base / "sources").mkdir(parents=True)
    source = SourceRecord(source_id="pubmed:1", provider="pubmed", title="SYNTHETIC controlled trial",
        url="https://pubmed.ncbi.nlm.nih.gov/1/", retrieved_at="2026-10-14", pmid="1", text=text,
        evidence_scope=scope, metadata={"synthetic": True, "private_prompt": "PRIVATE_SENTINEL"})
    raw = json.dumps(source.model_dump()).encode()
    (base / "sources" / "one.json").write_bytes(raw)
    manifest = {"schema_version": 1, "package_id": "synthetic_v1", "version": "1.0-synthetic",
        "scope": {"disease_names": ["Marker syndrome"], "candidate_names": ["Compound Z (CZ)"],
                  "species": ["Homo sapiens"]},
        "sources": [{"path": "sources/one.json", "sha256": sha(raw), "source_id": source.source_id,
                     "windows": [{"start": 0, "end": len(text), "sha256": sha(text),
                                  "location": "SYNTHETIC abstract Results"}],
                     "inclusion_reason": "Declared population/intervention; mixed findings retained",
                     "evidence_level": "synthetic_controlled_trial"}]}
    (base / "manifest.json").write_text(json.dumps(manifest))
    run, _ = task()
    run.brief.background_package_id = "synthetic_v1"
    return run, base, manifest, source


def save_manifest(base, manifest):
    (base / "manifest.json").write_text(json.dumps(manifest))


def test_package_is_opt_in_not_disease_triggered_and_preserves_verbatim_mixed_results(tmp_path):
    run, _, _, original = write_package(tmp_path)
    unselected = run.brief.model_copy(update={"background_package_id": ""})
    assert load_background_package(tmp_path, unselected).sources == []
    package = load_background_package(tmp_path, run.brief)
    s = package.sources[0]
    assert s.text == original.text
    assert "did not differ from placebo" in s.text
    assert s.metadata["acquisition_origin"] == "background_reuse"
    assert s.metadata["original_text_sha256"] == sha(original.text)
    assert s.metadata["excerpt_sha256"] == sha(s.text)
    assert s.metadata["excerpt_start"] == 0 and s.metadata["excerpt_end"] == len(original.text)
    assert "PRIVATE_SENTINEL" not in json.dumps(s.model_dump())
    assert run.evidence == [] and run.candidates == []


@pytest.mark.parametrize("changes", [
    {"disease": "Marker syndrome subtype"}, {"existing_candidates": ["Compound Z sodium"]},
    {"species": "Mus musculus"}, {"existing_candidates": []},
])
def test_package_rejects_incompatible_task_without_fuzzy_rebinding(tmp_path, changes):
    run, _, _, _ = write_package(tmp_path)
    with pytest.raises(ValueError, match="incompatible"):
        load_background_package(tmp_path, run.brief.model_copy(update=changes))


@pytest.mark.parametrize("corruption", ["file_hash", "window_hash", "unsafe_path", "symlink"])
def test_package_verifies_files_paths_and_continuous_window_receipts(tmp_path, corruption):
    run, base, manifest, _ = write_package(tmp_path)
    entry = manifest["sources"][0]
    if corruption == "file_hash":
        (base / entry["path"]).write_text("{}")
    elif corruption == "window_hash":
        entry["windows"][0]["sha256"] = "0" * 64
    elif corruption == "unsafe_path":
        entry["path"] = "../../outside.json"
    else:
        path = base / entry["path"]
        external = tmp_path / "external.json"
        external.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(external)
    save_manifest(base, manifest)
    with pytest.raises(ValueError):
        load_background_package(tmp_path, run.brief)


def test_fulltext_package_window_is_an_excerpt_and_cannot_join_discontinuous_quotes(tmp_path):
    text = "SYNTHETIC preamble. " + "A" * 500 + " Results: benefit mixed with no primary endpoint improvement."
    run, base, manifest, original = write_package(tmp_path, text=text, scope="full_text")
    entry = manifest["sources"][0]
    entry["windows"] = [{"start": 15, "end": 30, "sha256": sha(text[15:30]), "location": "intro"},
                        {"start": 520, "end": len(text), "sha256": sha(text[520:]), "location": "results"}]
    save_manifest(base, manifest)
    selected = load_background_package(tmp_path, run.brief).sources[0]
    assert selected.text == original.text[15:]
    assert selected.evidence_scope == "abstract"
    assert selected.metadata["original_evidence_scope"] == "full_text"
    assert selected.metadata["local_excerpt_only"] is True


def test_reuse_excerpt_finds_late_continuous_mixed_results_not_just_preamble():
    raw = "SYNTHETIC background filler. " * 150
    results = ("Results: adults with Marker syndrome received Compound Z; marker endpoint improved. "
               "The primary endpoint showed no significant difference from placebo. Adverse events occurred.")
    source = paper("1", "Marker syndrome Compound Z controlled trial", raw + results)
    start, end = relevant_excerpt(source, ["Marker syndrome", "Compound Z"])
    assert start > 0 and end - start <= 1800
    assert results in source.text[start:end]
    run, _ = task()
    flow = ResearchFlow(SimpleNamespace(run=run, state=CoScientistState(), disease_english="Marker syndrome"))
    copied = flow.reusable_source(source)
    assert copied.text == source.text[copied.metadata["excerpt_start"]:copied.metadata["excerpt_end"]]
    assert copied.metadata["excerpt_sha256"] == sha(copied.text)
    assert results in copied.text


def test_reused_papers_exceed_initial_quota_but_fresh_searches_execute_and_selected_cap_holds():
    run, plan = task(searches=2, papers=6)
    run.sources = [paper(str(i), "Marker syndrome Compound Z review", "SYNTHETIC context.",
                         types=["Review"], oa=False) for i in range(1, 8)]
    old_serialized = [s.model_dump() for s in run.sources]
    new = paper("new", "Marker syndrome Compound Z controlled trial",
                "SYNTHETIC primary endpoint had no improvement.", types=["Randomized Controlled Trial"], oa=False)
    sources = FixtureSources([new])
    retrieve(run, plan, sources)
    assert len(sources.queries) == 2
    assert len(run.sources) == 6
    assert "new" in {s.source_id for s in run.sources}
    assert next(s for s in run.sources if s.source_id == "new").metadata["acquisition_origin"] == "current_run_retrieval"
    assert all(s["metadata"].get("selection_reason") is None for s in old_serialized)
    assert run.evidence == []
    assert any("observed_source_union_exceeds_selected_paper_quota" in w for w in run.warnings)


def test_retrieval_duplicate_reuse_is_not_counted_as_new_acquisition():
    run, plan = task(searches=2, papers=2)
    old = paper("old", "Marker syndrome Compound Z randomized trial", "SYNTHETIC mixed endpoint.", oa=False)
    old.metadata["acquisition_origin"] = "background_reuse"
    run.sources = [old]
    calls = []
    sources = FixtureSources()
    def search(query, limit=8, **options):
        calls.append(query)
        return [old.model_copy(deep=True)] if len(calls) == 1 else [paper("new", "Marker syndrome Compound Z trial", "SYNTHETIC null endpoint.", oa=False)]
    sources.search_literature = search
    retrieve(run, plan, sources)
    assert len(calls) == 2 and len(run.sources) == 2
    assert old.metadata["current_query_observed"] is True
    assert old.metadata["acquisition_origin"] == "background_reuse"


@pytest.mark.parametrize("negative", ["no significant differences", "no statistically significant benefit",
                                     "no significant impact"])
def test_common_null_result_phrasing_remains_visible_in_bounded_source_selection(negative):
    run, plan = task(searches=1, papers=1)
    run.sources = [paper("old", "Marker syndrome Compound Z randomized trial",
                        "SYNTHETIC marker endpoint improved.", types=["Randomized Controlled Trial"], oa=False)]
    null = paper("new", "Marker syndrome Compound Z randomized trial",
                 "SYNTHETIC investigators reported " + negative + " for the marker endpoint.",
                 types=["Randomized Controlled Trial"], oa=False)
    retrieve(run, plan, FixtureSources([null]))
    assert len(run.sources) == 1 and run.sources[0].source_id == "new"
    assert "contrary_or_mixed_terms" in run.sources[0].metadata["selection_reason"]
    assert run.evidence == []


def aliased_package(tmp_path):
    run, base, manifest, _ = write_package(tmp_path, text=(
        "SYNTHETIC children with M condition received Zeta. Results: no improvement in the primary endpoint."))
    path = base / manifest["sources"][0]["path"]
    source = json.loads(path.read_text())
    source["title"] = "SYNTHETIC Zeta in M condition randomized controlled trial"
    source["metadata"]["publication_types"] = {"pubType": ["Randomized Controlled Trial"]}
    source["metadata"]["is_open_access"] = "N"
    path.write_text(json.dumps(source))
    manifest["sources"][0]["sha256"] = sha(path.read_bytes())
    save_manifest(base, manifest)
    package = load_background_package(tmp_path, run.brief)
    run.brief.budget.max_papers = 1
    run.brief.budget.max_searches = 1
    run.sources = [s.model_copy(deep=True) for s in package.sources]
    _, plan = task(searches=1, papers=1)
    return run, plan, package


def test_explicit_loaded_scope_handles_paper_aliases_without_preferring_unrelated_literal_mentions(tmp_path):
    run, plan, package = aliased_package(tmp_path)
    wrong = paper("wrong", "Other condition Other compound randomized trial",
        "SYNTHETIC discussion cites Marker syndrome and Compound Z. Other intervention improved another condition.",
        types=["Randomized Controlled Trial"], oa=False)
    sources = FixtureSources([wrong])
    sources.engine = SimpleNamespace(background_package=package)
    retrieve(run, plan, sources)
    assert len(sources.queries) == 1 and len(run.sources) == 1
    assert run.sources[0].source_id == "pubmed:1"
    assert "explicit_background_scope_requires_claim_review" in run.sources[0].metadata["selection_reason"]
    assert "no_retrieved_relevant_primary_trial" not in run.sources[0].metadata["coverage_gaps"]
    assert "no_retrieved_relevant_contrary_or_mixed_result" not in run.sources[0].metadata["coverage_gaps"]
    assert run.evidence == []


@pytest.mark.parametrize("mismatch", ["not_enabled", "different_package", "different_task", "changed_text", "metadata_only"])
def test_scope_hint_requires_loaded_matching_package_and_exact_snapshot_not_source_metadata(tmp_path, mismatch):
    run, plan, package = aliased_package(tmp_path)
    sources = FixtureSources()
    sources.engine = SimpleNamespace(background_package=package)
    run.sources[0].metadata.update(background_package_id="synthetic_v1",
        selection_reason="explicit_background_scope_requires_claim_review")
    if mismatch == "not_enabled":
        run.brief.background_package_id = ""
    elif mismatch == "different_package":
        run.brief.background_package_id = "different_v1"
    elif mismatch == "different_task":
        run.brief.disease = "Different condition"
    elif mismatch == "changed_text":
        run.sources[0].text += " Changed observation."
    else:
        sources.engine = SimpleNamespace()
    retrieve(run, plan, sources)
    assert len(run.sources) == 1
    assert "explicit_background_scope_requires_claim_review" not in run.sources[0].metadata["selection_reason"]
    assert "no_retrieved_relevant_primary_trial" in run.sources[0].metadata["coverage_gaps"]
    assert run.evidence == []


def test_background_activation_rejects_unsupported_standard_mode():
    from scientist.models import ResearchBrief
    with pytest.raises(ValueError, match='co_scientist'):
        ResearchBrief(disease='SYNTHETIC disease', background_package_id='synthetic_v1')

