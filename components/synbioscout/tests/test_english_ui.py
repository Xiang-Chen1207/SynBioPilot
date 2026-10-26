"""English UI chrome, without rewriting multilingual scientific/user content."""
import json
import os
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.nodes = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


def test_static_interface_is_english_and_retains_unique_control_ids():
    source = (ROOT / "scientist/web/index.html").read_text()
    page = Page(source)
    assert next(attrs for tag, attrs in page.nodes if tag == "html")["lang"] == "en"
    assert not re.search(r"[\u3400-\u9fff]", source)
    ids = [attrs["id"] for _, attrs in page.nodes if "id" in attrs]
    assert len(ids) == len(set(ids))
    assert {
        "researchForm", "requirementText", "parseRequirement", "intakeDraft",
        "intakeFields", "intakeQuestions", "manualResearch", "confirmIntake",
        "startResearch", "agentTimeline", "candidateCards", "evidenceCards",
        "demoImportForm", "targetForm", "experimentForm", "doctorPanel",
    }.issubset(ids)


def test_examples_and_confirmation_are_explicit_not_automatic_actions():
    page = Page((ROOT / "scientist/web/index.html").read_text())
    examples = [attrs for _, attrs in page.nodes if "data-intake-example" in attrs]
    assert {row["data-intake-example"] for row in examples} == {
        "Explore migraine", "Assess caffeine for migraine",
    }
    assert all(row["type"] == "button" for row in examples)
    by_id = {attrs["id"]: attrs for _, attrs in page.nodes if "id" in attrs}
    assert by_id["parseRequirement"]["type"] == "button"
    assert by_id["confirmIntake"]["type"] == "checkbox"
    assert "disabled" in by_id["startResearch"]
    assert by_id["startResearch"]["type"] == "submit"


def test_english_intake_preserves_multilingual_grounding_and_limits():
    from scientist.intake import FIELD_NAMES, LIMITS, SYSTEM, negative_context
    assert all(not re.search(r"[\u3400-\u9fff]", value) for value in FIELD_NAMES.values())
    assert negative_context("NAC", "研究自闭症，不要NAC")
    assert negative_context("NAC", "Explore autism without NAC")
    assert "VERBATIM" in SYSTEM
    assert LIMITS["model_calls"] == 1
    assert LIMITS["timeout_seconds"] == 45
    assert LIMITS["output_utf8_bytes"] == 6000


def test_display_translation_is_bounded_and_does_not_rewrite_original_text():
    node = os.environ.get("SYNBIOSCOUT_TEST_NODE") or shutil.which("node")
    if not node:
        import pytest
        pytest.skip("Node is required for the display-only translation check")
    script = r'''
const fs=require("node:fs"),vm=require("node:vm"),c={};vm.createContext(c);
const source=fs.readFileSync(process.argv[1],"utf8").replace(/\ninit\(\);\s*$/,"");
vm.runInContext(source+"\nglobalThis.translate=systemText;",c);
const original="患者原文：不要NAC；这是引用而不是界面提示。";
const known="candidate_hypothesis_expansion；Wall-time scheduling allowance exhausted；UTF-8字节预留，非token计量或费用。";
const legacy="用户给定候选不算自主发现";
const current="保留候选来源：用户输入或当前研究流程识别";
process.stdout.write(JSON.stringify({original:c.translate(original),known:c.translate(known),
  legacy:c.translate(legacy),current:c.translate(current),
  legacyPlan:c.translate("2 份供人工评审的研究合同"),
  currentPlan:c.translate("2 份自动生成、供后续审阅的研究计划")}));
'''
    result = subprocess.run(
        [node, "-e", script, str(ROOT / "scientist/web/app.js")],
        check=True, capture_output=True, text=True, timeout=20,
    )
    actual = json.loads(result.stdout)
    assert actual["original"] == "患者原文：不要NAC；这是引用而不是界面提示。"
    assert actual["legacy"] == actual["current"]
    assert "assessment input" in actual["current"]
    assert actual["legacyPlan"] == actual["currentPlan"]
    assert "automatically generated research plans" in actual["currentPlan"]
    assert actual["known"] == (
        "candidate_hypothesis_expansion; Wall-time scheduling allowance exhausted; "
        "UTF-8 byte reservation, not token usage or cost."
    )

