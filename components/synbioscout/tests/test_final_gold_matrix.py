"""Six unmodified public source windows, transparent judgements and fake clock.

This matrix evaluates software behavior and serialization with synthetic fixtures.
No discarded historical provider response is read or reconstructed.
"""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
import test_delivery_r3_chain as r3

from scientist.compact_synthesis import CompactSynthesisDraft
from scientist.llm import serialized_request
from scientist.models import ResearchBrief, SourceRecord
from scientist.research import RESEARCH_SYSTEM, SynthesisDraft

NOTES = [
    "合成说明：该条保留研究对象、原始终点及给药背景，不能由一个症状量表推断全部核心症状的变化。",
    "合成说明：组间结果、时间变化和交互效应需要分别解释；未显著的比较仍随原文保留，不能删除。",
    "合成说明：辅助治疗条件与单药使用不可互换；具体研究的观察不能直接决定团队工程菌方案。",
    "合成说明：生化指标改变与临床获益是不同证据；现有来源没有提供本项目生产、释放和暴露测量。",
    "合成说明：阴性终点不会因同一候选出现其他支持材料而失效；观察时长和适用范围必须保留。",
    "合成说明：综述仅用于理解已有材料，不作为新增随机试验计数；所给窗口以外的全文内容仍未核验。",
]
ENGLISH = [
    "SYNTHETIC annotation: distinguish the measured symptom scale from other disease outcomes.",
    "SYNTHETIC annotation: retain the interaction result even when another comparison is favorable.",
    "SYNTHETIC annotation: adjunctive treatment is not evidence for a stand-alone intervention.",
    "SYNTHETIC annotation: a blood marker cannot establish clinical or nasal delivery benefit.",
    "SYNTHETIC annotation: absence of benefit is retained with its original endpoints and duration.",
    "SYNTHETIC annotation: a review is not an additional independent randomized trial.",
]
VARIANTS = ("original", "english", "chinese", "bilingual", "verbose")


def source_fixture():
    """Portable projection: public input/source windows, never run traversal."""
    path = Path(__file__).parent / "fixtures/final_gold_source_scenario.json"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "9fdf20e4999135777a67bb8ea5c28e3fb992467b3c787c25090d95523908ef8b"
    data = json.loads(raw)
    request = {"prepare_targets": False, "brief": data["brief"]}
    assert hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == data["request_sha256"]
    sources = [SourceRecord.model_validate(s) for s in data["sources"]]
    assert len(sources) == 6
    for source in sources:
        assert hashlib.sha256(source.text.encode()).hexdigest() == source.metadata["excerpt_sha256"]
    return ResearchBrief.model_validate(data["brief"]), sources


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    r3.guard.__wrapped__(monkeypatch)


def run_variant(tmp_path, monkeypatch, variant, *, target_bytes=None):
    monkeypatch.setattr(r3, "public_fixture", source_fixture)
    original = r3.R3Controller.response

    def response(control, title, payload):
        value = original(control, title, payload)
        if title != "SynthesisDraft":
            return value
        for index, row in enumerate(value["evidence"]):
            if variant in {"english", "bilingual", "verbose"}:
                row["limitations"].append(ENGLISH[index])
            if variant in {"chinese", "bilingual", "verbose"}:
                row["limitations"].append(NOTES[index])
            if variant == "verbose":
                # Deliberately legal long narrative, retained rather than
                # silently truncated to make a passing byte count.
                row["limitations"].extend([
                    "SYNTHETIC audit context " + str(index + 1) + ": " + note
                    for note in NOTES if note != NOTES[index]
                ])
        if target_bytes is not None:
            value["evidence"][0]["limitations"].append("")
            gap = target_bytes - len(json.dumps(value, ensure_ascii=False).encode())
            assert gap >= 0
            # Exact UTF-8 boundary, including a multibyte string and the last
            # 0–2 ASCII bytes. Not used as a compression-effectiveness claim.
            value["evidence"][0]["limitations"][-1] = "界" * (gap // 3) + "b" * (gap % 3)
        return value

    monkeypatch.setattr(r3.R3Controller, "response", response)
    return r3.run_r3_chain(tmp_path, monkeypatch, kind="public")


def measurements(run, control):
    wire = control.synthesis_wire
    synthesis = next(row for row in control.calls if row["schema"] == "SynthesisDraft")
    return {
        "synthetic": True, "source_windows": 6, "original_trials": 5, "reviews": 1,
        "source_texts_unchanged": all(
            s.text == original.text for original in control.sources
            for s in run.sources if s.source_id == original.source_id
        ),
        "wire_bytes": synthesis.get("output_bytes"),
        "expanded_bytes": run.usage.get("synthesis_expansion", {}).get("expanded_bytes"),
        "ledger_input_bytes": run.usage["input_tokens_reserved"],
        "received_output_bytes": run.usage["output_bytes_received"],
        "accepted_output_bytes": run.usage["output_tokens_reserved"],
        "calls": len(control.calls), "accepted_evidence": len(run.evidence),
        "independent_evaluations": len(r3.valid_evaluations(run)),
        "public_status": run.status.value,
        "simulated_seconds": round(840 - (control.co.ledger.deadline - control.clock.monotonic()), 6),
        "actual_model_calls": 0, "actual_scientific_http": 0,
        "call_input_bytes": [row["input_bytes"] for row in control.calls],
        "call_output_bytes": [row.get("output_bytes") for row in control.calls],
        "all_polarities_in_wire": sorted({e["polarity"] for e in wire["evidence"]}) if wire else [],
    }


@pytest.mark.parametrize("variant", VARIANTS)
def test_six_sources_with_varied_legal_narrative_and_utf8(tmp_path, monkeypatch, variant):
    run, control, _, offline = run_variant(tmp_path, monkeypatch, variant)
    assert len(control.sources) == 6
    r3.assert_accounting(run, control, offline)
    row = measurements(run, control)
    assert row["source_texts_unchanged"]
    assert row["all_polarities_in_wire"] == ["context", "mixed", "opposes", "supports"]
    if variant == "verbose":
        assert row["wire_bytes"] > 12000
        assert len(control.calls) == 2 and not run.evidence
        assert not r3.valid_evaluations(run)
        assert run.co_scientist.budget_failures[-1]["output_limit_categories"] == ["single_call"]
        assert "synthesis_expansion" not in run.usage
    else:
        r3.assert_complete(run, control, offline)
        assert row["wire_bytes"] <= 12000
        assert len(run.evidence) == 6
        assert [e.quote for e in run.evidence] == [e["quote"] for e in control.rows]
        # Six evidence spans include the review; only five original trial
        # findings are promoted by this transparent, predeclared fixture.
        assert len(run.candidate_evaluations[0].final.findings) == 5
        for call in control.calls[2:]:
            body = json.dumps(call["payload"], ensure_ascii=False)
            assert all(e.quote in body for e in run.evidence)


@pytest.mark.parametrize("size", [11999, 12000, 12001])
def test_six_source_multibyte_exact_output_boundary(tmp_path, monkeypatch, size):
    run, control, _, offline = run_variant(tmp_path, monkeypatch, "chinese", target_bytes=size)
    r3.assert_accounting(run, control, offline)
    assert control.calls[1]["output_bytes"] == size
    if size <= 12000:
        r3.assert_complete(run, control, offline)
        assert run.usage["synthesis_expansion"]["wire_bytes"] == size
    else:
        assert len(control.calls) == 2 and not run.evidence
        assert not r3.valid_evaluations(run) and "synthesis_expansion" not in run.usage


def test_schema_input_saving_is_measured_on_same_request_not_on_different_runs(tmp_path, monkeypatch):
    from scientist.coscientist import role_system
    run, control, _, offline = run_variant(tmp_path, monkeypatch, "original")
    r3.assert_complete(run, control, offline)
    payload = json.dumps(control.calls[1]["payload"], ensure_ascii=False)
    settings = control.co.settings.llm
    system = role_system("supervisor") + "\n" + RESEARCH_SYSTEM
    compatible = len(serialized_request(settings, system, payload, SynthesisDraft.model_json_schema()).encode())
    compact = len(serialized_request(settings, system, payload, CompactSynthesisDraft.model_json_schema()).encode())
    assert compatible > compact
    # Measurements refer to same in-memory request; no claim about real R3.
    assert control.calls[1]["input_bytes"] == compact
    assert deepcopy(control.calls[1]["payload"]) == json.loads(payload)

