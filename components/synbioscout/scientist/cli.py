"""SynBioScout command line. Research and heavy computation are separate actions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .experiments import analyze_experiment
from .models import ExperimentSpec, ResearchBrief, ScientistSettings, TargetPackageRequest
from .research import ResearchEngine
from .runtime import JobService, compute_preflight, doctor
from .store import ProjectStore
from .targets import build_target_package


def load_settings(path=None):
    if not path:
        return ScientistSettings(workspace=str(Path("runs/scientist").resolve()))
    path = Path(path).resolve()
    settings = ScientistSettings.model_validate_json(path.read_text())
    for field in ("workspace", "target2lead_config"):
        value = getattr(settings, field)
        if value:
            setattr(settings, field, str((path.parent / value).resolve()))
    return settings


def main():
    parser = argparse.ArgumentParser(description="SynBioScout evidence-grounded AI Scientist")
    parser.add_argument("--settings", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    research = sub.add_parser("research")
    research.add_argument("--brief", required=True, type=Path)
    research.add_argument("--project-id", default="")
    research.add_argument("--no-target-download", action="store_true")
    research.add_argument("--full-json", action="store_true", help="Print full source records rather than a compact handoff")
    target = sub.add_parser("prepare-target")
    target.add_argument("--request", required=True, type=Path)
    target.add_argument("--output", required=True, type=Path)
    experiment = sub.add_parser("analyze-experiment")
    experiment.add_argument("--csv", required=True, type=Path)
    experiment.add_argument("--spec", required=True, type=Path)
    experiment.add_argument("--output", required=True, type=Path)
    preflight = sub.add_parser("compute-preflight")
    preflight.add_argument("--config", required=True, type=Path)
    preflight.add_argument("--until", default="md")
    handoff = sub.add_parser("handoff")
    for field in ("project-id", "run-id", "package-id"):
        handoff.add_argument("--" + field, required=True)
    handoff.add_argument("--max-md-jobs", type=int, default=20)
    compute = sub.add_parser("compute-run")
    compute.add_argument("--project-id", required=True)
    compute.add_argument("--compute-id", required=True)
    compute.add_argument("--until", default="md")
    compute.add_argument("--resume", action="store_true")
    status = sub.add_parser("status")
    status.add_argument("--project-id", required=True)
    args = parser.parse_args()
    settings = load_settings(args.settings)
    if args.command == "doctor":
        result = doctor(settings)
    elif args.command == "serve":
        import uvicorn

        from .api import create_app
        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port, workers=1)
        return
    elif args.command == "research":
        service = JobService(settings)
        try:
            store = service.store
            pid = args.project_id or store.create_project("SynBioScout research")["project_id"]
            run = store.create_run(pid, ResearchBrief.model_validate_json(args.brief.read_text()))
            result = ResearchEngine(store, settings).execute(pid, run.run_id,
                prepare_targets=not args.no_target_download).model_dump(mode="json")
            if not args.full_json:
                result = {"project_id": pid, "run_id": run.run_id, "status": result["status"],
                    "counts": {key: len(result[key]) for key in
                               ("sources", "evidence", "targets", "candidates", "target_packages")},
                    "report": str(store.project_dir(pid) / result["report_path"]),
                    "steps": result["steps"], "usage": result["usage"],
                    "warnings": result["warnings"], "errors": result["errors"]}
        finally:
            service.shutdown()
    elif args.command == "prepare-target":
        request = TargetPackageRequest.model_validate_json(args.request.read_text())
        result = build_target_package(request, args.output).model_dump()
    elif args.command == "analyze-experiment":
        spec = ExperimentSpec.model_validate_json(args.spec.read_text())
        result = analyze_experiment(args.csv, spec, args.output)
    elif args.command == "compute-preflight":
        result = compute_preflight(args.config, until=args.until)
    elif args.command == "status":
        store = ProjectStore(settings.workspace)
        result = {"runs": store.list_runs(args.project_id), "memory": store.search_memory(args.project_id)}
    else:
        service = JobService(settings, recover=False)
        if args.command == "handoff":
            result = service.create_handoff(args.project_id, args.run_id, args.package_id, args.max_md_jobs)
        else:
            result = service.submit_compute(args.project_id, args.compute_id, until=args.until, resume=args.resume)
            # CLI waits for its explicitly launched local job; server keeps it asynchronous.
            service.executor.shutdown(wait=True)
            result = service.compute_job(args.project_id, args.compute_id)
        service.shutdown()
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()

