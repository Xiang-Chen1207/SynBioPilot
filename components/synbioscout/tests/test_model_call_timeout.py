"""SYNTHETIC timeout propagation; no live providers or private run files."""
import json
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from scientist.llm import StructuredLLM
from scientist.models import LLMSettings


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("SYNTHETIC timeout tests forbid live providers")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.mark.parametrize("remaining,expected", [(3600, 900), (525.9, 525)])
def test_codex_timeout_reaches_process_and_respects_remaining_deadline(tmp_path, monkeypatch, remaining, expected):
    now = 1000.0
    monkeypatch.setattr("scientist.llm.time.monotonic", lambda: now)
    monkeypatch.setattr("scientist.llm.shutil.which", lambda value: "/SYNTHETIC/codex")
    observed = []
    def process(args, **options):
        observed.append(options["timeout"])
        answer = Path(args[args.index("--output-last-message") + 1])
        assert answer.is_relative_to(tmp_path)
        answer.write_text('{"value":1}')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", process)
    settings = LLMSettings(backend="codex", executable="SYNTHETIC", model="SYNTHETIC")
    worker = StructuredLLM(settings, tmp_path / "SYNTHETIC", max_calls=1)
    worker.deadline = now + remaining
    result = worker.complete("SYNTHETIC", "SYNTHETIC", {
        "type": "object", "properties": {"value": {"type": "integer"}},
        "required": ["value"], "additionalProperties": False})
    assert result == {"value": 1}
    assert observed == [expected]
    assert settings.timeout_seconds == 900  # Clipping a call must not alter server settings.


def test_default_example_and_explicit_timeout_compatibility():
    root = Path(__file__).resolve().parents[1]
    example = json.loads((root / "examples/scientist/settings.example.json").read_text())
    assert LLMSettings().timeout_seconds == example["llm"]["timeout_seconds"] == 900
    assert LLMSettings(timeout_seconds=240).timeout_seconds == 240
    with pytest.raises(ValueError):
        LLMSettings(timeout_seconds=1801)

