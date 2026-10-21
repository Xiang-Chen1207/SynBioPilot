"""Bounded local jobs, dependency checks and explicit compute handoffs."""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from target2lead.cli import stages_for_config
from target2lead.common import ROOT, load_config

from .integration import collect_compute_results, create_compute_config
from .models import ExecutionStatus, ScientistSettings
from .research import ResearchEngine
from .store import ProjectStore, safe_id, utc_now, write_json


def doctor(settings: ScientistSettings):
    dependencies = {name: importlib.util.find_spec(name) is not None for name in
                    ("pydantic", "requests", "rdkit", "Bio", "scipy", "matplotlib", "fastapi")}
    executable = shutil.which(settings.llm.executable)
    login = "not_checked"
    if settings.llm.backend == "codex" and executable:
        try:
            result = subprocess.run([executable, "login", "status"], capture_output=True,
                                    text=True, timeout=10, check=False)
            login = "available" if result.returncode == 0 else "not_logged_in"
        except (OSError, subprocess.TimeoutExpired):
            login = "check_failed"
    return {"python": sys.executable, "version": sys.version.split()[0],
            "workspace": str(Path(settings.workspace).resolve()), "dependencies": dependencies,
            "llm": {"backend": settings.llm.backend, "model": settings.llm.model or "gpt-5.5 (CLI fallback)",
                    "executable_available": bool(executable), "login": login,
                    "timeout_seconds": settings.llm.timeout_seconds,
                    "remote_data_allowed": settings.allow_remote_llm_data},
            "optional_providers": {"biomni": importlib.util.find_spec("biomni") is not None,
                "paperqa": importlib.util.find_spec("paperqa") is not None,
                "tooluniverse": importlib.util.find_spec("tooluniverse") is not None},
            "note": "Optional frameworks are detected, not automatically invoked. Native adapters implement the core workflow."}


def compute_preflight(config_path: Path, start="prepare", until="md"):
    cfg = load_config(config_path)
    stages = stages_for_config(cfg)
    if start not in stages or until not in stages or stages.index(start) > stages.index(until):
        raise ValueError("Invalid compute stage range")
    selected = stages[stages.index(start):stages.index(until) + 1]
    checks, warnings = [], []
    def check(name, value, ok, reason=""):
        checks.append({"name": name, "value": str(value), "ready": bool(ok), "reason": reason})
    for key, value in cfg["target"].items():
        if key in ("protein_pdb", "sequence_fasta", "reference_ligand_sdf"):
            check("target." + key, value, Path(value).is_file(), "requires a real target artifact")
    if any(s != "prepare" for s in selected):
        value = cfg["library"]["molecules"]
        check("library.molecules", value, Path(value).is_file())
    modules = {"prepare": ["Bio", "rdkit"], "drugclip": ["torch", "unicore", "lmdb"],
        "sprint": ["torch", "pytorch_lightning", "esm"],
        "psichic": ["torch", "torch_geometric"], "pharmaconet": ["torch"],
        "admet": ["admet_ai"], "gate": ["cobra", "pydantic"],
        "md": ["rdkit", "Bio"], "top100": ["pandas", "rdkit"],
        "merge_candidates": ["pandas", "rdkit"], "select": ["pandas", "rdkit"]}
    probes = {}
    for stage in selected:
        settings = cfg.get(stage, {})
        python = settings.get("python", cfg["run"].get("python", sys.executable))
        executable = shutil.which(python)
        if not executable:
            check(stage + ".python", python, False, "configured Python interpreter unavailable")
            continue
        check(stage + ".python", python, True)
        for key in ("repo", "screen_repo", "checkpoint", "checkpoint_dir", "saprot_checkpoint", "weights", "models_dir"):
            if settings.get(key):
                path = Path(settings[key])
                ok = path.exists() and (not path.is_dir() or any(path.iterdir()))
                check(stage + "." + key, path, ok)
        names = modules.get(stage, [])
        probe_key = (executable, tuple(names))
        if probe_key not in probes:
            code = ("import importlib.util,json; names=" + repr(names) +
                    "; print(json.dumps({n:importlib.util.find_spec(n) is not None for n in names}))")
            try:
                result = subprocess.run([executable, "-c", code], capture_output=True, text=True, timeout=15, check=False)
                probes[probe_key] = json.loads(result.stdout) if result.returncode == 0 else {}
            except (OSError, ValueError, subprocess.TimeoutExpired):
                probes[probe_key] = {}
        for name in names:
            check(stage + ".module." + name, executable, probes[probe_key].get(name, False))
        if stage == "drugclip" or settings.get("device", "").startswith("cuda"):
            try:
                result = subprocess.run([executable, "-c", "import torch; print(int(torch.cuda.is_available()))"],
                                        capture_output=True, text=True, timeout=15, check=False)
                cuda = result.returncode == 0 and result.stdout.strip() == "1"
            except (OSError, subprocess.TimeoutExpired):
                cuda = False
            check(stage + ".cuda", executable, cuda, "configured adapter requires CUDA")
        if stage == "sprint":
            value = settings.get("foldseek", "foldseek")
            check("sprint.foldseek", value, shutil.which(value) is not None)
        if stage == "gate":
            if settings.get("backend", "codex") == "codex":
                check("gate.codex", "codex", shutil.which("codex") is not None)
            gem = Path(settings["models_dir"]) / ("iBT721.xml" if settings["chassis_id"] == "lplantarum" else "iML1515.xml")
            check("gate.GEM", gem, gem.is_file())
        if stage == "md" and settings.get("enabled", True):
            from target2lead.adapters.md import TOOLS
            for tool, value in {**TOOLS, **settings.get("tools", {})}.items():
                parts = list(value) if isinstance(value, list) else shlex.split(value)
                binary = parts[0] if parts else ""
                check("md.tool." + tool, binary, shutil.which(binary) is not None)
    if not cfg.get("selection", {}).get("admet_bounds"):
        warnings.append("ADMET bounds are empty: properties will be annotated, not safety-filtered")
    if cfg.get("selection", {}).get("allow_gate_abstain", True):
        warnings.append("Gate E may enter exploratory screening; it is not positive chassis evidence")
    warnings.append("Readiness checks do not prove checkpoint compatibility or successful scientific execution")
    return {"ready": all(c["ready"] for c in checks), "stages": list(selected),
            "checks": checks, "warnings": warnings}


class JobService:
    """Single-process local service. Restarted jobs are marked interrupted, never rerun silently."""
    def __init__(self, settings: ScientistSettings, *, recover=True, engine=None):
        self.settings = settings
        self.store = ProjectStore(settings.workspace)
        self.engine = engine or ResearchEngine(self.store, settings)
        self._custom_engine = engine is not None
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scientist")
        self._lock = threading.RLock()
        self._active = set()
        self._closing = False
        self._owner = None
        if recover:
            import fcntl
            self._owner = (self.store.root / ".service.lock").open("a+")
            try:
                fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                self._owner.close()
                self.executor.shutdown(wait=False)
                raise ValueError("Another Scientist service owns this workspace; use its API") from exc
            self._owner.seek(0)
            self._owner.truncate()
            self._owner.write(str(os.getpid()))
            self._owner.flush()
        else:
            # CLI compute/handoff must not race a live web-service owner.
            import fcntl
            guard = (self.store.root / ".service.lock").open("a+")
            try:
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                guard.close()
                self.executor.shutdown(wait=False)
                raise ValueError("A Scientist service is active; create/launch handoffs through its API") from exc
            guard.close()
        if recover:
            self._recover()

    def _recover(self):
        for project in self.store.list_projects():
            pid = project["project_id"]
            for raw in self.store.list_runs(pid):
                if raw["status"] in ("queued", "running"):
                    run = self.store.load_run(pid, raw["run_id"])
                    run.status = ExecutionStatus.PARTIAL if run.sources else ExecutionStatus.FAILED
                    run.errors.append("Service restarted while job was pending; start a follow-up run to resume safely")
                    for step in run.steps:
                        if step.status == "running":
                            step.status = "interrupted"
                    if run.co_scientist:
                        from .co_models import AgentEvent
                        for actor in run.co_scientist.agents:
                            if actor.status in ("running", "queued"):
                                actor.status = "partial" if actor.status == "running" else "skipped"
                                actor.task = "服务重启：该角色未继续执行"
                                actor.updated_at = utc_now()
                        sequence = len(run.co_scientist.events) + 1
                        run.co_scientist.events.append(AgentEvent(
                            event_id=f"event_{sequence:04d}", sequence=sequence,
                            agent_id="supervisor", kind="interruption", status="partial",
                            title="服务重启，保存中断状态；没有自动恢复角色执行", created_at=utc_now()))
                    self.store.save_run(run)
            for path in self.store.project_dir(pid).glob("compute/*/job.json"):
                job = json.loads(path.read_text())
                if job["status"] in ("queued", "running"):
                    job.update(status="interrupted", error="Service restarted; inspect worker logs before an explicit resume")
                    write_json(path, job)

    def submit_research(self, project_id, brief, *, parent_run_id="", prepare_targets=True):
        if not self.settings.allow_remote_llm_data and self.settings.llm.backend != "none":
            raise ValueError("Remote LLM data sharing is disabled; use backend none or change local server configuration")
        run = self.store.create_run(project_id, brief, parent_run_id)
        key = (project_id, run.run_id)
        with self._lock:
            self._active.add(key)
        def work():
            try:
                engine = self.engine if self._custom_engine else ResearchEngine(self.store, self.settings)
                return engine.execute(project_id, run.run_id, prepare_targets=prepare_targets)
            finally:
                with self._lock:
                    self._active.discard(key)
                    self._release_owner_if_idle()
        self.executor.submit(work)
        return run

    def create_handoff(self, project_id, run_id, package_id, max_md_jobs=20):
        if not self.settings.target2lead_config:
            raise ValueError("Set target2lead_config in the local Scientist settings first")
        run = self.store.load_run(project_id, run_id)
        if run.status in (ExecutionStatus.QUEUED, ExecutionStatus.RUNNING):
            raise ValueError("Research is still running")
        base_config = load_config(Path(self.settings.target2lead_config))
        configured = base_config.get("gate", {})
        requested_name = " ".join(run.brief.chassis_name.casefold().split())
        configured_name = " ".join(str(configured.get("chassis_name", "")).casefold().split())
        if requested_name != configured_name or run.brief.chassis_id != configured.get("chassis_id"):
            raise ValueError("Requested chassis does not match the configured ChassisGate model; configure and validate the correct strain model before compute handoff")
        package = next((p for p in run.target_packages if p.package_id == package_id), None)
        if package is None:
            raise ValueError("Target package not found in this research run")
        with self._lock:
            jobs = [j for j in self.list_compute(project_id) if j["research_run_id"] == run_id]
            reserved = sum(j["max_md_jobs"] for j in jobs)
            if reserved + max_md_jobs > run.brief.budget.max_md_jobs_total:
                raise ValueError("Total MD reservation exceeds the research-run budget; split the target allocations")
            cid = "c_" + uuid.uuid4().hex[:16]
            directory = self.store.project_dir(project_id) / "compute" / cid
            resolved = [c for c in run.candidates if c.identity_status == "resolved" and
                        "literature" in c.routes and c.evidence_ids and c.decision != "hold" and
                        c.role in ("therapeutic_candidate", "symptom_management") and
                        c.role_status == "model_reviewed" and c.role_evidence_ids and
                        set(c.role_evidence_ids).issubset({e.evidence_id for e in run.evidence
                            if e.validated_span}.intersection(c.evidence_ids))]
            cfg = create_compute_config(Path(self.settings.target2lead_config), package, resolved,
                                        directory, max_md_jobs=max_md_jobs)
            value = {"compute_id": cid, "project_id": project_id, "research_run_id": run_id,
                     "target_package_id": package_id, "max_md_jobs": max_md_jobs,
                     "config_path": str(cfg), "status": "handoff_ready", "created_at": utc_now(),
                     "literature_candidates_included": [c.candidate_id for c in resolved],
                     "literature_candidates_excluded": [c.candidate_id for c in run.candidates
                        if "literature" in c.routes and c not in resolved],
                     "note": "Configuration created; no inference, Gate or MD has run"}
            write_json(directory / "job.json", value)
            return value

    def compute_job(self, project_id, compute_id):
        path = self.store.project_dir(project_id) / "compute" / safe_id(compute_id) / "job.json"
        if not path.is_file():
            raise FileNotFoundError("Compute job not found")
        return json.loads(path.read_text())

    def list_compute(self, project_id):
        return [json.loads(p.read_text()) for p in self.store.project_dir(project_id).glob("compute/*/job.json")]

    def submit_compute(self, project_id, compute_id, *, until="md", resume=False):
        with self._lock:
            job = self.compute_job(project_id, compute_id)
            if job["status"] in ("queued", "running"):
                raise ValueError("This compute job is already active")
            preflight = compute_preflight(Path(job["config_path"]), until=until)
            directory = Path(job["config_path"]).parent
            write_json(directory / "preflight.json", preflight)
            if not preflight["ready"]:
                job.update(status="blocked", preflight=preflight,
                           error="Required compute dependencies are missing; no subprocess was launched")
                write_json(directory / "job.json", job)
                return job
            job.update(status="queued", until=until, resume=resume, error="")
            write_json(directory / "job.json", job)
            self._active.add((project_id, compute_id))
            self.executor.submit(self._compute_worker, job)
            return job

    def _compute_worker(self, job):
        directory = Path(job["config_path"]).parent
        job.update(status="running", started_at=utc_now())
        write_json(directory / "job.json", job)
        args = [sys.executable, str(ROOT / "run.py"), "--config", job["config_path"],
                "run", "--until", job["until"]]
        if job["resume"]:
            args.append("--resume")
        run = self.store.load_run(job["project_id"], job["research_run_id"])
        try:
            with (directory / "worker.log").open("a") as log:
                process = subprocess.Popen(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    exit_code = process.wait(timeout=run.brief.budget.timeout_seconds)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=10)
                    raise
            job.update(status="completed" if exit_code == 0 else "failed", exit_code=exit_code)
        except subprocess.TimeoutExpired:
            job.update(status="failed", error="Compute wall-time budget exhausted; the exact worker process group was stopped")
        except Exception as exc:  # noqa: BLE001 -- persist errors at the external compute boundary
            job.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        job["finished_at"] = utc_now()
        write_json(directory / "job.json", job)
        try:
            write_json(directory / "results_snapshot.json", collect_compute_results(Path(job["config_path"])))
        except Exception as exc:  # noqa: BLE001 -- reporting failure must also be recorded
            job["collection_error"] = f"{type(exc).__name__}: {exc}"
            write_json(directory / "job.json", job)
        finally:
            with self._lock:
                self._active.discard((job["project_id"], job["compute_id"]))
                self._release_owner_if_idle()

    def _release_owner_if_idle(self):
        if self._closing and not self._active and self._owner:
            self._owner.close()
            self._owner = None

    def shutdown(self):
        self._closing = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        self._release_owner_if_idle()

