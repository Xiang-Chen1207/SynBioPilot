#!/usr/bin/env python3
"""Local-only SynBioScout launcher; deliberately needs only the Python standard library."""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

SERVICE_NAME = "synbioscout_ai_scientist"
REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MODULES = (
    "scientist", "fastapi", "uvicorn", "pydantic", "requests", "jsonschema",
    "Bio", "rdkit", "numpy", "pandas", "scipy", "matplotlib",
)


class LauncherError(RuntimeError):
    """A local startup condition that the user can fix."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端口必须为 1–65535 的整数") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须为 1–65535 的整数")
    return port


def find_python(root: Path) -> Path:
    candidate = root / ".venv/bin/python"
    if candidate.is_file():
        return candidate
    raise LauncherError(
        "没有找到本项目的 .venv 环境。请先使用 Python 3.11+ 执行：\n"
        "  python3 -m venv .venv\n"
        "  .venv/bin/python -m pip install -e '.[dev]'\n"
        "支持 macOS/Linux；Windows 请在 WSL 中安装 Linux 环境。启动器不会自动安装依赖。"
    )


def dependency_check(python: Path, root: Path) -> dict:
    # A fixed argument vector: neither configuration values nor user text become shell code.
    code = (
        "import importlib.util,json,sys; "
        f"names={REQUIRED_MODULES!r}; "
        "print(json.dumps({'version':list(sys.version_info[:3]),"
        "'missing':[n for n in names if importlib.util.find_spec(n) is None]}))"
    )
    try:
        completed = subprocess.run(
            [str(python), "-c", code], cwd=root, capture_output=True, text=True,
            timeout=20, check=False,
        )
        if completed.returncode:
            raise LauncherError(".venv 的 Python 无法完成依赖检查，请重建或修复环境。")
        result = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise LauncherError("无法检查本项目 Python 环境，请确认 .venv 可用。") from exc
    if result.get("version", [])[:2] < [3, 11]:
        raise LauncherError("AI Scientist 需要 Python 3.11 或更高版本。")
    if result.get("missing"):
        raise LauncherError(
            "缺少软件依赖：" + ", ".join(result["missing"]) + "。\n"
            "请在项目目录执行：.venv/bin/python -m pip install -e '.[dev]'"
        )
    return result


def read_settings(path: Path) -> dict:
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LauncherError(f"无法读取配置文件：{path}。请检查文件内容及权限。") from exc
    if not isinstance(settings, dict):
        raise LauncherError("配置文件最外层必须是 JSON 对象。")
    for name in ("workspace", "target2lead_config"):
        if name in settings and not isinstance(settings[name], str):
            raise LauncherError(f"配置字段 {name} 必须是路径字符串。")
    return settings


def ensure_settings(path: Path, root: Path, *, create: bool) -> tuple[dict, bool]:
    if path.is_file():
        return read_settings(path), False
    template = root / "examples/scientist/settings.example.json"
    settings = read_settings(template)
    # The example's relative paths are repository-relative, not examples/scientist-relative.
    for name in ("workspace", "target2lead_config"):
        if settings.get(name):
            settings[name] = str((root / settings[name]).resolve())
    if create:
        if not path.parent.is_dir():
            raise LauncherError(f"配置目录不存在：{path.parent}")
        try:
            # Exclusive creation preserves an existing config even during concurrent launches.
            with path.open("x", encoding="utf-8") as stream:
                json.dump(settings, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        except FileExistsError:
            return read_settings(path), False
        except OSError as exc:
            raise LauncherError(f"无法创建配置文件：{path}") from exc
    return settings, create


def probe_service(port: int, *, timeout: float = 1.0) -> dict | None:
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
        )
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as response:
            if response.status != 200:
                return None
            payload = response.read(65537)
            if len(payload) > 65536:
                return None
            health = json.loads(payload)
            if isinstance(health, dict):
                return health
    except (OSError, ValueError, urllib.error.URLError):
        pass
    return None


def is_scientist(health: dict | None) -> bool:
    return bool(
        health and health.get("service") == SERVICE_NAME
        and health.get("status") == "ok"
        and health.get("mode") == "local_research_not_clinical"
    )


def workspace_id(settings: dict, settings_path: Path) -> str:
    workspace = (settings_path.parent / settings.get("workspace", "runs/scientist")).resolve()
    return hashlib.sha256(str(workspace).encode()).hexdigest()[:16]


def port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def wait_ready(process, port: int, *, timeout: float = 30.0,
               expected_workspace: str = "") -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LauncherError("服务启动失败；请查看上方日志。已有工作目录服务请使用其网页。")
        health = probe_service(port)
        if is_scientist(health):
            if expected_workspace and health.get("workspace_id") != expected_workspace:
                raise LauncherError("该端口的 AI Scientist 使用另一个数据目录，停止本次启动。")
            return health
        time.sleep(0.25)
    raise LauncherError("服务未在 30 秒内就绪；请查看日志和配置后重试。")


def stop_child(process) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def check_launch(root: Path, settings_path: Path, port: int) -> dict:
    if sys.platform == "win32":
        raise LauncherError(
            "当前软件支持 macOS/Linux，不支持原生 Windows 服务。"
            "Windows 请在 WSL 中重新安装本项目 Linux .venv，再使用 WSL Python 启动；"
            "不要复用 Windows .venv。"
        )
    python = find_python(root)
    deps = dependency_check(python, root)
    settings, _ = ensure_settings(settings_path, root, create=False)
    health = probe_service(port)
    occupied = port_in_use(port)
    if occupied and not is_scientist(health):
        raise LauncherError(
            f"127.0.0.1:{port} 已被其他服务占用，或运行的是旧版 Scientist。"
            "请先正常停止原服务，或用 --port 指定其他端口；不会终止已有程序。"
        )
    expected_workspace = workspace_id(settings, settings_path)
    if is_scientist(health) and health.get("workspace_id") != expected_workspace:
        raise LauncherError(
            f"127.0.0.1:{port} 的 AI Scientist 使用另一个数据目录。"
            "请确认该服务的配置；不会替换或停止已有服务。"
        )
    return {
        "ready": True,
        "python": str(python),
        "python_version": ".".join(map(str, deps["version"])),
        "settings": str(settings_path),
        "settings_exists": settings_path.is_file(),
        "settings_will_create_on_start": not settings_path.is_file(),
        "url": f"http://127.0.0.1:{port}/",
        "existing_scientist": is_scientist(health),
        "workspace_id": expected_workspace,
        "local_only": True,
        "model_calls_performed": False,
    }


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    root = (root or REPO_ROOT).resolve()
    parser = argparse.ArgumentParser(description="启动 SynBioScout AI Scientist 本地软件")
    parser.add_argument("--port", type=port_number, default=8765)
    parser.add_argument("--settings", type=Path, default=root / "scientist.local.json")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--check", action="store_true", help="仅预检，不创建配置或调用模型")
    args = parser.parse_args(argv)
    settings_path = args.settings.expanduser().resolve()
    process = None
    try:
        result = check_launch(root, settings_path, args.port)
        if args.check:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        url = result["url"]
        if result["existing_scientist"]:
            print(f"AI Scientist 已经运行：{url}")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        _, created = ensure_settings(settings_path, root, create=True)
        if created:
            print(f"已创建本机配置（没有覆盖任何现有配置）：{settings_path}", flush=True)
        python = find_python(root)
        command = [
            str(python), "-m", "scientist.cli", "--settings", str(settings_path),
            "serve", "--port", str(args.port),
        ]
        print("正在启动本地软件；仅监听 127.0.0.1。", flush=True)
        process = subprocess.Popen(command, cwd=root)
        wait_ready(process, args.port, expected_workspace=result["workspace_id"])
        print(f"AI Scientist 已就绪：{url}\n请保留此窗口；按 Ctrl+C 正常停止。", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        return process.wait()
    except KeyboardInterrupt:
        print("\n正在停止本次启动的服务，已保存的项目和结果不会删除。", flush=True)
        return 0
    except (LauncherError, OSError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1
    finally:
        if process is not None:
            stop_child(process)


if __name__ == "__main__":
    sys.exit(main())

