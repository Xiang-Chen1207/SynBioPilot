"""File-backed projects and immutable research artifacts; no conversation-only memory."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ResearchBrief, ResearchRun


def utc_now():
    return datetime.now(UTC).isoformat()


def digest(value: Any):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def write_json(path: Path, value: Any, *, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=None if compact else 2,
                              separators=(",", ":") if compact else None, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def safe_id(value: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,90}", value):
        raise ValueError("Invalid resource ID")
    return value


class ProjectStore:
    def __init__(self, workspace):
        self.root = Path(workspace).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def project_dir(self, project_id):
        path = self.root / "projects" / safe_id(project_id)
        if not (path / "project.json").is_file():
            raise FileNotFoundError("Project not found")
        return path

    def create_project(self, name: str):
        if not name.strip() or len(name) > 250:
            raise ValueError("Project name must contain 1–250 characters")
        pid = "p_" + uuid.uuid4().hex[:16]
        value = {"project_id": pid, "name": name.strip(), "created_at": utc_now()}
        write_json(self.root / "projects" / pid / "project.json", value)
        return value

    def list_projects(self):
        return sorted([json.loads(p.read_text()) for p in
                       (self.root / "projects").glob("*/project.json")],
                      key=lambda p: p["created_at"], reverse=True)

    def create_run(self, project_id: str, brief: ResearchBrief, parent_run_id=""):
        from .intent_policy import admit_run_inputs
        admit_run_inputs(brief, parent_run_id)
        project = self.project_dir(project_id)
        if parent_run_id:
            self.load_run(project_id, parent_run_id)
        rid = "r_" + uuid.uuid4().hex[:16]
        now = utc_now()
        run = ResearchRun(run_id=rid, project_id=project_id, created_at=now,
                          updated_at=now, brief=brief, parent_run_id=parent_run_id)
        directory = project / "research" / rid
        write_json(directory / "run.json", run.model_dump(mode="json"))
        write_json(directory / "input.json", {"brief": brief.model_dump(),
                                               "fingerprint": digest(brief.model_dump())})
        return run

    def run_dir(self, project_id, run_id):
        directory = self.project_dir(project_id) / "research" / safe_id(run_id)
        if not (directory / "run.json").is_file():
            raise FileNotFoundError("Research run not found")
        return directory

    def save_run(self, run: ResearchRun):
        with self._lock:
            run.updated_at = utc_now()
            directory = self.run_dir(run.project_id, run.run_id)
            write_json(directory / "run.json", run.model_dump(mode="json"))

    def load_run(self, project_id, run_id):
        return ResearchRun.model_validate_json(
            (self.run_dir(project_id, run_id) / "run.json").read_text())

    def list_runs(self, project_id):
        files = (self.project_dir(project_id) / "research").glob("*/run.json")
        return sorted([json.loads(p.read_text()) for p in files],
                      key=lambda p: p["created_at"], reverse=True)

    def add_memory(self, project_id, *, kind, text, artifact="", verified=False,
                   source_run_id="", metadata=None):
        record = {"memory_id": "m_" + uuid.uuid4().hex[:16], "kind": kind,
                  "text": text, "artifact": artifact, "verified": bool(verified),
                  "source_run_id": source_run_id, "created_at": utc_now(),
                  "metadata": metadata or {}}
        with self._lock:
            write_json(self.project_dir(project_id) / "memory" /
                       f"{record['memory_id']}.json", record)
        return record

    def search_memory(self, project_id, query="", limit=20):
        records = [json.loads(p.read_text()) for p in
                   (self.project_dir(project_id) / "memory").glob("*.json")]
        terms = query.lower().split()
        def score(item):
            hay = json.dumps(item, ensure_ascii=False).lower()
            return sum(t in hay for t in terms)
        records.sort(key=lambda item: (score(item), item["created_at"]), reverse=True)
        return [r for r in records if not terms or score(r)][:limit]

    def artifact_path(self, project_id, relative):
        project = self.project_dir(project_id).resolve()
        path = (project / relative).resolve()
        if not path.is_relative_to(project) or not path.is_file():
            raise FileNotFoundError("Artifact not found")
        if any(part.startswith(".") for part in Path(relative).parts):
            raise ValueError("Hidden files cannot be exported")
        return path


