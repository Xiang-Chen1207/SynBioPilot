"""SYNTHETIC Python/JavaScript window parity; no model, network or historical data."""
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scientist.exports import export_document
from scientist.models import ResearchBrief, ResearchRun, SourceRecord
from scientist.reports import source_scope_label
from scientist.research_public import public_literature_source
from scientist.source_scope import source_scope_view
from scientist.tool_public import public_tool_source

ROOT = Path(__file__).resolve().parents[1]
NODE_FALLBACK = Path("/Users/SYNTHETIC/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node")


def excerpt(text="abc", *, start=11889, end=14151):
    return {"source_id": "s_SYNTHETIC", "provider": "europe_pmc", "title": "SYNTHETIC source",
        "url": "https://europepmc.org/article/MED/1", "retrieved_at": "2026-01-01",
        "evidence_scope": "abstract", "text": text, "metadata": {"local_excerpt_only": True,
            "original_evidence_scope": "full_text", "excerpt_start": start, "excerpt_end": end,
            "acquisition_origin": "background_reuse"}}


def javascript_views(records, *, decode_nonfinite=False):
    executable = shutil.which("node") or (str(NODE_FALLBACK) if NODE_FALLBACK.is_file() else None)
    if executable is None:
        pytest.skip("Local Node runtime is unavailable")
    script = r'''const fs=require("node:fs"),vm=require("node:vm"),context={};vm.createContext(context);
const app=fs.readFileSync(process.argv[1],"utf8").replace(/\ninit\(\);\s*$/,"");
vm.runInContext(app+"\nglobalThis.api={sourceScopeView,sourceScopeLabel};",context);
const rows=JSON.parse(fs.readFileSync(0,"utf8"));
if(process.argv[2]==="decode-nonfinite") {
  const values={"SYNTHETIC_NAN":NaN,"SYNTHETIC_POS_INF":Infinity,"SYNTHETIC_NEG_INF":-Infinity};
  for(const row of rows) for(const key of ["excerpt_start","excerpt_end"])
    if(Object.hasOwn(values,row.metadata?.[key])) row.metadata[key]=values[row.metadata[key]];
}
process.stdout.write(JSON.stringify(rows.map(row=>({scope:context.api.sourceScopeView(row),label:context.api.sourceScopeLabel(row)}))));'''
    result = subprocess.run([executable, "-e", script, str(ROOT / "scientist/web/app.js"),
        "decode-nonfinite" if decode_nonfinite else "normal"],
        input=json.dumps(records), capture_output=True, text=True, check=True, timeout=20)
    return json.loads(result.stdout)


def cases():
    missing = excerpt()
    missing.pop("text")
    omitted = excerpt("")
    omitted["metadata"].update(export_text_included=False, retrieved_text_sha256="a" * 64,
        source_scope={"original_window": {"start": 11889, "end": 14151}, "fresh_fulltext": True},
        window_verified=True, observed_text_codepoints=2262, original_text_length=2262)
    forged = excerpt()
    forged["metadata"].update(source_scope={"original_window": {"start": 11889, "end": 14151}},
        window_verified=True, observed_text_codepoints=2262, export_text_included=True)
    return [
        ("valid_ascii", excerpt("x" * 2262), {"start": 11889, "end": 14151}),
        ("parent_length_mismatch", excerpt("abc"), None),
        ("forged_verification", forged, None),
        ("non_bmp_codepoints", excerpt("a🧬汉", start=10, end=13), {"start": 10, "end": 13}),
        ("combining_codepoints", excerpt("e\u0301🧬", start=10, end=13), {"start": 10, "end": 13}),
        ("utf16_length_wrong", excerpt("a🧬汉", start=10, end=14), None),
        ("integer_json_floats", excerpt("abc", start=1.0, end=4.0), {"start": 1, "end": 4}),
        ("empty_text", excerpt(""), None), ("missing_text", missing, None),
        ("export_omission", omitted, None), ("null_text", excerpt(None), None),
        ("nonstring_text", excerpt(2262), None),
        ("bool_coordinate", excerpt("abc", start=True, end=4), None),
        ("fractional_coordinate", excerpt("abc", start=1.5, end=4.5), None),
        ("negative_coordinate", excerpt("abc", start=-1, end=2), None),
        ("reversed_coordinate", excerpt("abc", start=4, end=1), None),
        ("empty_coordinate_range", excerpt("", start=1, end=1), None),
        ("above_bounded_offset", excerpt("abc", start=10**12, end=10**12 + 3), None),
        ("unsafe_js_integer", excerpt("abc", start=2**53, end=2**53 + 3), None),
        ("bounded_upper_edge", excerpt("abc", start=10**12 - 3, end=10**12), {"start": 10**12 - 3, "end": 10**12}),
        ("very_large_json_integer", excerpt("abc", start=10**400, end=10**400 + 3), None),
    ]


def test_window_and_label_parity_for_actual_text_codepoints_and_unverifiable_cases():
    rows = cases()
    originals = copy.deepcopy(rows)
    actual = javascript_views([row for _, row, _ in rows])
    for (name, row, window), javascript in zip(rows, actual, strict=True):
        python = source_scope_view(row)
        assert python["original_window"] == window, name
        assert javascript["scope"] == python, name
        # The English UI localizes labels, not canonical scope facts or the
        # existing Chinese report. Derive the expected English window from
        # the explicit fixture expectation, never from the JS implementation.
        expected_window = (
            f"original character window [{window['start']}, {window['end']})"
            if window else "original text window unverified"
        )
        assert javascript["label"] == (
            "Original Full text · saved contiguous excerpt · " + expected_window
            + " · Reused material · not a full-text retrieval this run"
        ), name
        assert ("original text window unverified" in javascript["label"]) is (window is None), name
        assert ("原文窗口未核实" in source_scope_label(row)) is (window is None), name
    assert rows == originals


@pytest.mark.parametrize("number,marker", [(float("nan"), "SYNTHETIC_NAN"),
    (float("inf"), "SYNTHETIC_POS_INF"), (-float("inf"), "SYNTHETIC_NEG_INF")])
def test_nonfinite_coordinates_are_never_verified(number, marker):
    python = [source_scope_view(excerpt("abc", start=number, end=3)),
              source_scope_view(excerpt("abc", start=0, end=number))]
    javascript = javascript_views([excerpt("abc", start=marker, end=3),
                                   excerpt("abc", start=0, end=marker)], decode_nonfinite=True)
    assert all(view["original_window"] is None for view in python)
    assert [view["scope"] for view in javascript] == python


def test_metadata_canonical_result_never_overrides_actual_text_in_either_direction():
    invalid = excerpt("abc")
    invalid["metadata"]["source_scope"] = source_scope_view(invalid)
    valid = excerpt("abc", start=2, end=5)
    valid["metadata"]["source_scope"] = {"original_window": None, "fresh_fulltext": True}
    views = javascript_views([invalid, valid])
    assert views[0]["scope"]["original_window"] is None
    assert views[1]["scope"]["original_window"] == {"start": 2, "end": 5}
    assert views[1]["scope"]["fresh_fulltext"] is False


def test_safe_projection_validates_returned_text_and_tool_omission_cannot_retain_verified_window():
    raw = excerpt("a🧬汉", start=10, end=13)
    before = copy.deepcopy(raw)
    literal = public_literature_source(raw)
    hidden = public_tool_source(raw)
    assert literal["metadata"]["source_scope"]["original_window"] == {"start": 10, "end": 13}
    assert hidden["text"] == "" and hidden["metadata"]["source_scope"]["original_window"] is None
    assert hidden["metadata"]["excerpt_start"] == 10 and hidden["metadata"]["excerpt_end"] == 13
    for row, js in zip([literal, hidden], javascript_views([literal, hidden]), strict=True):
        assert row["metadata"]["source_scope"] == source_scope_view(row) == js["scope"]
    assert public_tool_source(hidden)["metadata"]["source_scope"] == hidden["metadata"]["source_scope"]
    assert raw == before


def test_redaction_does_not_leave_a_window_derived_from_unreturned_text():
    body = "raw_prompt=PRIVATE_SCOPE_SENTINEL " * 20
    raw = excerpt(body, start=10, end=10 + len(body))
    public = public_literature_source(raw)
    assert "PRIVATE_SCOPE_SENTINEL" not in json.dumps(public)
    assert public["metadata"]["source_scope"]["original_window"] is None
    assert javascript_views([public])[0]["scope"] == source_scope_view(public)


def test_export_omission_recomputes_unknown_window_without_mutating_source_or_claiming_metadata_proof():
    s = SourceRecord.model_validate(excerpt("a🧬汉", start=10, end=13))
    run = ResearchRun(run_id="r_synthetic_scope_fix", project_id="p_synthetic", created_at="", updated_at="",
        brief=ResearchBrief(disease="SYNTHETIC disease"), sources=[s])
    original = run.model_dump()
    document = export_document(run)
    row = document["sources"][0]
    assert row["text"] == "" and row["metadata"]["export_text_included"] is False
    assert row["metadata"]["source_scope"]["original_window"] is None
    assert row["metadata"]["source_scope"] == source_scope_view(row) == javascript_views([row])[0]["scope"]
    assert source_scope_view(s)["original_window"] == {"start": 10, "end": 13}
    assert run.model_dump() == original

