"""Launcher checks are local and mocked; no live inference or model downloads."""
import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SPEC = importlib.util.spec_from_file_location(
    "scientist_launcher", Path(__file__).parents[1] / "scripts/start_scientist.py"
)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").touch()
    template = tmp_path / "examples/scientist/settings.example.json"
    template.parent.mkdir(parents=True)
    template.write_text(json.dumps({
        "workspace": "runs/scientist", "target2lead_config": "configs/keap1.json",
        "llm": {"backend": "codex"},
    }))
    return tmp_path


def test_first_config_has_repository_relative_paths(root):
    path = root / "scientist.local.json"
    settings, created = launcher.ensure_settings(path, root, create=True)
    assert created
    assert settings["workspace"] == str(root / "runs/scientist")
    assert settings["target2lead_config"] == str(root / "configs/keap1.json")
    assert json.loads(path.read_text()) == settings


def test_existing_config_is_never_overwritten(root):
    path = root / "scientist.local.json"
    contents = '{"workspace":"my-results","llm":{"backend":"none"}}'
    path.write_text(contents)
    settings, created = launcher.ensure_settings(path, root, create=True)
    assert not created
    assert settings["workspace"] == "my-results"
    assert path.read_text() == contents


def test_check_only_does_not_create_settings(root, monkeypatch):
    monkeypatch.setattr(launcher, "dependency_check", lambda *a: {"version": [3, 13, 9]})
    monkeypatch.setattr(launcher, "probe_service", lambda *a: None)
    monkeypatch.setattr(launcher, "port_in_use", lambda *a: False)
    assert launcher.main(["--check"], root=root) == 0
    assert not (root / "scientist.local.json").exists()


def test_native_windows_fails_check_with_wsl_guidance(root, monkeypatch, capsys):
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    spawn = Mock()
    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    assert launcher.main(["--check"], root=root) == 1
    assert "WSL" in capsys.readouterr().err
    spawn.assert_not_called()
    assert not (root / "scientist.local.json").exists()


@pytest.mark.parametrize("port", ["0", "65536", "-1", "not-a-port", "8765; echo bad"])
def test_port_cannot_be_out_of_range_or_shell_code(port):
    with pytest.raises(argparse.ArgumentTypeError):
        launcher.port_number(port)


def test_health_requires_exact_service_identifier():
    legacy = {"status": "ok", "version": "0.1.0", "mode": "local_research_not_clinical"}
    assert not launcher.is_scientist(legacy)
    assert not launcher.is_scientist({**legacy, "service": "another_app"})
    assert launcher.is_scientist({**legacy, "service": launcher.SERVICE_NAME})


def test_other_port_owner_is_not_stopped(root, monkeypatch):
    monkeypatch.setattr(launcher, "dependency_check", lambda *a: {"version": [3, 13, 9]})
    monkeypatch.setattr(launcher, "probe_service", lambda *a: {"service": "other"})
    monkeypatch.setattr(launcher, "port_in_use", lambda *a: True)
    spawn = Mock()
    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    assert launcher.main(["--no-browser"], root=root) == 1
    spawn.assert_not_called()


def test_existing_scientist_opens_without_second_process(root, monkeypatch):
    monkeypatch.setattr(launcher, "dependency_check", lambda *a: {"version": [3, 13, 9]})
    monkeypatch.setattr(launcher, "probe_service", lambda *a: {
        "status": "ok", "service": launcher.SERVICE_NAME,
        "mode": "local_research_not_clinical", "version": "0.2.0",
        "workspace_id": launcher.workspace_id({"workspace": "runs/scientist"},
                                               root / "scientist.local.json"),
    })
    monkeypatch.setattr(launcher, "port_in_use", lambda *a: True)
    spawn, browser = Mock(), Mock()
    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    monkeypatch.setattr(launcher.webbrowser, "open", browser)
    assert launcher.main([], root=root) == 0
    spawn.assert_not_called()
    browser.assert_called_once_with("http://127.0.0.1:8765/")


def test_different_workspace_is_not_silently_reused(root, monkeypatch):
    monkeypatch.setattr(launcher, "dependency_check", lambda *a: {"version": [3, 13, 9]})
    monkeypatch.setattr(launcher, "probe_service", lambda *a: {
        "status": "ok", "service": launcher.SERVICE_NAME,
        "mode": "local_research_not_clinical", "workspace_id": "other-workspace",
    })
    monkeypatch.setattr(launcher, "port_in_use", lambda *a: True)
    browser, spawn = Mock(), Mock()
    monkeypatch.setattr(launcher.webbrowser, "open", browser)
    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    assert launcher.main([], root=root) == 1
    browser.assert_not_called()
    spawn.assert_not_called()


def test_new_server_uses_fixed_argument_vector(root, monkeypatch):
    process = Mock()
    process.wait.return_value = 0
    process.poll.return_value = 0
    monkeypatch.setattr(launcher, "dependency_check", lambda *a: {"version": [3, 13, 9]})
    monkeypatch.setattr(launcher, "probe_service", lambda *a: None)
    monkeypatch.setattr(launcher, "port_in_use", lambda *a: False)
    monkeypatch.setattr(launcher, "wait_ready", lambda *a, **kw: {})
    spawn = Mock(return_value=process)
    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)
    assert launcher.main(["--no-browser", "--port", "9012"], root=root) == 0
    command = spawn.call_args.args[0]
    assert command == [
        str(root / ".venv/bin/python"), "-m", "scientist.cli", "--settings",
        str(root / "scientist.local.json"), "serve", "--port", "9012",
    ]
    assert "shell" not in spawn.call_args.kwargs


def test_missing_dependencies_are_clear_and_never_installed(root, monkeypatch):
    run = Mock(return_value=SimpleNamespace(
        returncode=0, stdout=json.dumps({"version": [3, 13, 9], "missing": ["fastapi"]}),
    ))
    monkeypatch.setattr(launcher.subprocess, "run", run)
    with pytest.raises(launcher.LauncherError, match="fastapi"):
        launcher.dependency_check(root / ".venv/bin/python", root)
    assert run.call_count == 1
    assert "pip" not in run.call_args.args[0]


def test_failed_readiness_only_stops_child(root, monkeypatch):
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(launcher, "dependency_check", lambda *a: {"version": [3, 13, 9]})
    monkeypatch.setattr(launcher, "probe_service", lambda *a: None)
    monkeypatch.setattr(launcher, "port_in_use", lambda *a: False)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(launcher, "wait_ready", Mock(side_effect=launcher.LauncherError("not ready")))
    assert launcher.main(["--no-browser"], root=root) == 1
    process.terminate.assert_called_once()


def test_mac_entry_point_exists():
    command = Path(__file__).parents[1] / "start_ai_scientist.command"
    assert command.is_file()
    assert '"$SCIENTIST_ROOT/scripts/start_scientist.py"' in command.read_text()


def test_ready_service_must_match_expected_workspace(monkeypatch):
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(launcher, "probe_service", lambda *a: {
        "status": "ok", "service": launcher.SERVICE_NAME,
        "mode": "local_research_not_clinical", "workspace_id": "other-workspace",
    })
    with pytest.raises(launcher.LauncherError, match="另一个数据目录"):
        launcher.wait_ready(process, 8765, expected_workspace="expected-workspace")

