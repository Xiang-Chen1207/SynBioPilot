"""SYNTHETIC CSV/ZIP scope regressions; no scientific or provider calls."""
import csv
import hashlib
import io
import json
import zipfile

import pytest

from scientist.exports import export_document, export_research, table_exports
from scientist.models import EvidenceRecord, ExecutionStatus, ResearchBrief, SourceRecord
from scientist.presentation import result_document
from scientist.source_scope import source_scope_view
from scientist.store import ProjectStore

QUOTE = "SYNTHETIC limited quoted observation; no therapeutic conclusion."
FULL_ONLY = "SYNTHETIC_FULL_BODY_NOT_FOR_EXPORT"
PRIVATE = "PRIVATE_METADATA_SENTINEL"


def source_cases():
    body = QUOTE + "\n" + FULL_ONLY
    body += "x" * (2262 - len(body))
    unicode_body = QUOTE + "\n非BMP字符🙂🧪；范围按Unicode码点。"
    excerpt_metadata = {"original_evidence_scope": "full_text", "local_excerpt_only": True,
        "excerpt_start": 11889, "excerpt_end": 14151, "acquisition_origin": "background_reuse"}
    shapes = [
        ("reused_full_excerpt", "abstract", "europe_pmc", body, excerpt_metadata),
        ("new_abstract", "abstract", "europe_pmc", QUOTE + "\n" + FULL_ONLY,
            {"acquisition_origin": "current_run_retrieval"}),
        ("new_full_text", "full_text", "europe_pmc", QUOTE + "\n" + FULL_ONLY,
            {"acquisition_origin": "current_run_full_text"}),
        ("identity_reuse", "database", "pubchem", QUOTE,
            {"acquisition": "background_reuse"}),
        ("legacy_unknown_acquisition", "abstract", "europe_pmc", QUOTE, {}),
        ("unicode_excerpt", "abstract", "europe_pmc", unicode_body,
            {**excerpt_metadata, "excerpt_start": 100, "excerpt_end": 100 + len(unicode_body)}),
        ("malformed_window", "abstract", "europe_pmc", "abc", {**excerpt_metadata,
            "source_scope": {"original_scope": "full_text", "current_scope": "local_excerpt",
                "original_window": {"start": 11889, "end": 14151}, "local_excerpt": True,
                "acquisition": "reused", "fresh_fulltext": False}}),
        ("empty_excerpt", "abstract", "europe_pmc", "", excerpt_metadata),
    ]
    return [SourceRecord(source_id="synthetic:" + name, provider=provider, title="SYNTHETIC " + name,
        url="https://example.invalid/" + name, retrieved_at="SYNTHETIC", evidence_scope=scope, text=text,
        metadata={**metadata, "synthetic": True, "raw_prompt": PRIVATE,
            "private_cache_path": "/Users/SYNTHETIC/private/cache.txt"})
        for name, scope, provider, text, metadata in shapes]


def scope_run(tmp_path):
    store = ProjectStore(tmp_path / "SYNTHETIC_workspace")
    pid = store.create_project("SYNTHETIC source range export")["project_id"]
    run = store.create_run(pid, ResearchBrief(disease="SYNTHETIC scope examples"))
    run.status = ExecutionStatus.PARTIAL
    run.sources = source_cases()
    run.evidence = [EvidenceRecord(evidence_id="ev_" + s.source_id.removeprefix("synthetic:"), source_id=s.source_id,
        claim="SYNTHETIC source scope fixture", quote=QUOTE if QUOTE in s.text else "SYNTHETIC unavailable old quote",
        location="SYNTHETIC recorded position", polarity="context", validated_span=QUOTE in s.text)
        for s in run.sources]
    run.evidence.append(EvidenceRecord(evidence_id="ev_missing_source", source_id="synthetic:missing_source",
        claim="SYNTHETIC old missing source", quote=QUOTE, location="SYNTHETIC unknown", polarity="context"))
    store.save_run(run)
    return store, run


def csv_rows(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def test_csv_canonical_scope_distinguishes_original_material_window_and_acquisition(tmp_path):
    _, run = scope_run(tmp_path)
    by_id = {row["source_id"]: row for row in csv_rows(table_exports(run)["evidence.csv"])}
    old = by_id["synthetic:reused_full_excerpt"]
    assert old["source_scope"] == old["current_scope"] == "local_excerpt"
    assert old["stored_source_scope"] == "abstract" and old["original_scope"] == "full_text"
    assert old["local_excerpt"] == "True"
    assert (old["original_window_start"], old["original_window_end"], old["original_window_unit"]) == ("11889", "14151", "unicode_code_points")
    assert old["acquisition"] == "reused" and old["reused"] == "True" and old["fresh_fulltext"] == "False"
    assert by_id["synthetic:new_abstract"]["source_scope"] == "abstract"
    assert by_id["synthetic:new_abstract"]["acquisition"] == "newly_acquired"
    assert by_id["synthetic:new_abstract"]["reused"] == "False"
    assert by_id["synthetic:new_full_text"]["source_scope"] == "full_text"
    assert by_id["synthetic:new_full_text"]["fresh_fulltext"] == "True"
    assert by_id["synthetic:identity_reuse"]["source_scope"] == "database"
    assert by_id["synthetic:identity_reuse"]["reused"] == "True"
    assert by_id["synthetic:legacy_unknown_acquisition"]["acquisition"] == "unknown"
    assert by_id["synthetic:legacy_unknown_acquisition"]["reused"] == ""
    unknown = by_id["synthetic:missing_source"]
    assert unknown["source_scope"] == unknown["original_scope"] == "unknown"
    assert unknown["original_window_start"] == unknown["original_window_end"] == unknown["reused"] == ""


def test_csv_unicode_uses_codepoints_and_bad_empty_windows_remain_unverified(tmp_path):
    _, run = scope_run(tmp_path)
    rows = {row["source_id"]: row for row in csv_rows(table_exports(run)["evidence.csv"])}
    source = next(s for s in run.sources if s.source_id == "synthetic:unicode_excerpt")
    row = rows[source.source_id]
    assert int(row["original_window_end"]) - int(row["original_window_start"]) == len(source.text)
    assert len(source.text.encode("utf-16-le")) // 2 > len(source.text)
    for name in ("malformed_window", "empty_excerpt"):
        row = rows["synthetic:" + name]
        assert row["source_scope"] == "local_excerpt"
        assert row["original_window_start"] == row["original_window_end"] == row["original_window_unit"] == ""


def test_zip_csv_is_identical_to_table_render_and_omitted_json_body_does_not_revalidate_window(tmp_path):
    store, run = scope_run(tmp_path)
    path = store.run_dir(run.project_id, run.run_id) / "run.json"
    original, checksum = run.model_dump_json(), hashlib.sha256(path.read_bytes()).hexdigest()
    tables = table_exports(run)
    full_view = result_document(run)
    exported = export_document(run)
    zip_bytes, _, _ = export_research(store, run, "zip")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        assert archive.read("evidence.csv") == tables["evidence.csv"]
        zipped = json.loads(archive.read("research.json"))
        for view in (exported, zipped):
            export_note = view["presentation"]["source_text_export"]
            assert "source bodies are omitted" in export_note and "original_window is null" in export_note
            assert "CSV and Markdown" in export_note and "may retain verified windows" in export_note
            for source in view["sources"]:
                assert source["text"] == "" and source["metadata"]["export_text_included"] is False
                assert source["metadata"]["source_scope"] == source_scope_view(source)
                assert source["metadata"]["source_scope"]["original_window"] is None
        assert FULL_ONLY not in archive.read("evidence.csv").decode("utf-8-sig")
    assert full_view["sources"][0]["metadata"]["source_scope"]["original_window"] == {"start": 11889, "end": 14151}
    assert run.model_dump_json() == original and hashlib.sha256(path.read_bytes()).hexdigest() == checksum


@pytest.mark.parametrize("formula", ['=HYPERLINK("https://example.invalid")', "+1+2", "-1+2", "@SUM(1,2)", "\t=1"])
def test_csv_scope_changes_keep_formula_and_privacy_guards(tmp_path, formula):
    store, run = scope_run(tmp_path)
    run.sources[0].title = formula
    run.evidence[0].claim = formula
    run.evidence[0].location = "/Users/SYNTHETIC_PRIVATE_CACHE/results.txt"
    data = table_exports(run)["evidence.csv"]
    row = csv_rows(data)[0]
    assert row["source_title"] == "'" + formula and row["claim"] == "'" + formula
    assert row["location"] == "[local path omitted]"
    assert PRIVATE not in data.decode("utf-8-sig") and FULL_ONLY not in data.decode("utf-8-sig")
    assert "/Users/" not in data.decode("utf-8-sig")
    assert "metadata" not in row and "text" not in row
    packed, _, _ = export_research(store, run, "zip")
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        assert archive.read("evidence.csv") == data

