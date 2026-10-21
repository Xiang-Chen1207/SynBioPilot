"""Local research workbench; all compute settings are supplied by the server owner."""
from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .evidence import decide_candidates
from .experiments import analyze_experiment
from .exports import export_research
from .intake import (
    ID_PATTERN,
    LIMITS,
    DraftEdit,
    IntakeConfirmation,
    IntakeError,
    IntakeRequest,
    IntakeService,
)
from .integration import collect_compute_results
from .models import (
    CandidateRecord,
    ExperimentSpec,
    ResearchBrief,
    ScientistSettings,
    StrictModel,
    TargetPackageRequest,
)
from .presentation import result_document
from .reports import render_report
from .runtime import JobService, compute_preflight, doctor
from .store import safe_id, utc_now, write_json
from .targets import build_target_package


class ProjectInput(StrictModel):
    name: str = Field(min_length=1, max_length=250)
    request_id: str | None = Field(default=None, pattern=ID_PATTERN)
    intake_confirmation: IntakeConfirmation | None = None


class ResearchInput(StrictModel):
    brief: ResearchBrief
    parent_run_id: str = ""
    prepare_targets: bool | None = None
    request_id: str | None = Field(default=None, pattern=ID_PATTERN)
    intake_confirmation: IntakeConfirmation | None = None


class HandoffInput(StrictModel):
    run_id: str
    package_id: str
    max_md_jobs: int = Field(default=20, ge=0, le=20)


class ComputeInput(StrictModel):
    until: str = "md"
    resume: bool = False


class ExperimentInput(StrictModel):
    csv_text: str = Field(min_length=1, max_length=2_000_000)
    spec: ExperimentSpec


class TargetInput(StrictModel):
    run_id: str
    request: TargetPackageRequest


class DemoScoreColumns(StrictModel):
    model: str = Field(min_length=1, max_length=128)
    compound_id: str = Field(min_length=1, max_length=128)
    score: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=128)
    error: str = Field(default="", max_length=128)


class DemoScoresInput(StrictModel):
    csv_text: str = Field(max_length=500_000)
    columns: DemoScoreColumns


class FollowupInput(StrictModel):
    experiment_ids: list[str] = Field(default_factory=list)
    question: str = Field(default="", max_length=1000)
    prepare_targets: bool = False


class ContinueHypothesesInput(StrictModel):
    max_llm_calls: int = Field(default=6, ge=1, le=16)
    timeout_seconds: int = Field(default=900, ge=30, le=3600)


def create_app(settings: ScientistSettings | None = None, *, service=None, intake_parser=None):
    settings = settings or ScientistSettings()
    service = service or JobService(settings)
    store = service.store
    intake = IntakeService(settings, store, intake_parser)
    @asynccontextmanager
    async def lifespan(app):
        yield
        service.shutdown()
    app = FastAPI(title="SynBioScout AI Scientist", version=__version__, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.state.service = service
    app.state.intake = intake
    app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "web"), name="web_assets")
    # No CORS; reject cross-site mutations, including local-network browser attacks.
    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Cross-origin mutation is not allowed"}, status_code=403)
            size = request.headers.get("content-length", "0")
            if size.isdigit() and int(size) > 3_000_000:
                return JSONResponse({"detail": "Request exceeds local upload limit"}, status_code=413)
        return await call_next(request)

    @app.exception_handler(FileNotFoundError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Resource not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(IntakeError)
    async def intake_error(request, exc):
        return JSONResponse({"detail":exc.message, "code":exc.code}, status_code=exc.http_status,
                            headers={"Cache-Control":"no-store"})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        if request.url.path.startswith("/api/intake/"):
            return JSONResponse({"detail":"The draft request has an invalid format or length; input is not echoed.", "code":"invalid_request"},
                                status_code=422, headers={"Cache-Control":"no-store"})
        if not request.url.path.startswith("/api/demos/"):
            return await request_validation_exception_handler(request, exc)
        # Pydantic's default detail.input can echo an entire CSV or secret.
        allowed = {"body", "path", "query", "csv_text", "columns", "demo_id",
                   "model", "compound_id", "score", "status", "error"}
        details = [{"loc": [part if isinstance(part, int) or part in allowed else "[field]"
                            for part in item.get("loc", [])],
                    "msg": "The demo import request violates format or length limits; input is not echoed.",
                    "type": "demo_request_invalid"} for item in exc.errors()[:20]]
        return JSONResponse({"detail": details}, status_code=422,
                            headers={"Cache-Control": "no-store"})

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(Path(__file__).parent / "web" / "index.html")

    @app.get("/index.html", include_in_schema=False)
    def index_alias():
        return home()

    @app.get("/styles.css", include_in_schema=False)
    def style_alias():
        return FileResponse(Path(__file__).parent / "web" / "styles.css", media_type="text/css")

    @app.get("/app.js", include_in_schema=False)
    def script_alias():
        return FileResponse(Path(__file__).parent / "web" / "app.js", media_type="text/javascript")

    @app.get("/hdac2-demo.js", include_in_schema=False)
    def demo_script_alias():
        return FileResponse(Path(__file__).parent / "web" / "hdac2-demo.js", media_type="text/javascript")

    @app.get("/intake.js", include_in_schema=False)
    def intake_script_alias():
        return FileResponse(Path(__file__).parent / "web" / "intake.js", media_type="text/javascript")

    @app.get("/api/intake/limits")
    def intake_limits(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return {**LIMITS, "model_available":settings.llm.backend != "none" and settings.allow_remote_llm_data,
                "cost_note":"Parsing is metered separately and does not use or increase the research budget; UTF-8 bytes are not provider tokens or cost."}

    @app.post("/api/intake/drafts")
    def intake_parse(body: IntakeRequest, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return intake.parse(body)

    @app.get("/api/intake/drafts/{draft_id}")
    def intake_draft(draft_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return intake.draft(draft_id)

    @app.patch("/api/intake/drafts/{draft_id}")
    def intake_edit(draft_id: str, body: DraftEdit, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return intake.edit(draft_id, body)

    @app.get("/api/intake/submissions/{request_id}")
    def intake_submission(request_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return intake.submission(request_id)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "synbioscout_ai_scientist", "version": __version__,
                "mode": "local_research_not_clinical",
                "workspace_id": hashlib.sha256(str(store.root).encode()).hexdigest()[:16]}

    @app.get("/api/doctor")
    def readiness():
        return doctor(settings)

    @app.get("/api/research-tools")
    def research_tools():
        from .tools import ToolRegistry
        return {"tools": ToolRegistry(None).catalog(), "mode": "read_only_lightweight"}

    @app.get("/api/research-protocols")
    def research_protocols():
        from .protocols import ARIS_REFERENCE, catalog
        return {"protocols": catalog(), "reference": ARIS_REFERENCE,
                "implementation": "project_owned_bounded_contracts_not_full_ARIS_runtime"}

    @app.get("/api/demos")
    def demos(response: Response):
        from .demos import demo_catalog
        response.headers["Cache-Control"] = "no-store"
        return demo_catalog()

    @app.get("/api/demos/{demo_id}")
    def demo(demo_id: str, response: Response):
        from .demos import get_demo
        response.headers["Cache-Control"] = "no-store"
        return get_demo(demo_id)

    @app.post("/api/demos/{demo_id}/inspect-scores")
    def inspect_demo(demo_id: str, body: DemoScoresInput, response: Response):
        from .demos import get_demo, inspect_demo_scores
        get_demo(demo_id)  # Fixed catalog only; never resolve user paths or URLs.
        response.headers["Cache-Control"] = "no-store"
        return inspect_demo_scores(body.csv_text, body.columns.model_dump(exclude_defaults=True))

    @app.get("/api/projects")
    def projects():
        return store.list_projects()

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectInput):
        if body.request_id or body.intake_confirmation:
            if not body.request_id or not body.intake_confirmation:
                raise IntakeError("confirmation_required", "Creating a project through request intake requires explicit confirmation of the current draft.")
            return intake.create_project(body.name, body.request_id, body.intake_confirmation)
        return store.create_project(body.name)

    @app.get("/api/projects/{project_id}/runs")
    def runs(project_id: str):
        return store.list_runs(project_id)

    @app.post("/api/projects/{project_id}/runs", status_code=202)
    def research(project_id: str, body: ResearchInput):
        prepare = body.prepare_targets if body.prepare_targets is not None else (
            body.brief.research_mode == "standard")
        if body.request_id or body.intake_confirmation:
            if not body.request_id or not body.intake_confirmation or body.parent_run_id:
                raise IntakeError("confirmation_required", "Request intake requires explicit confirmation and cannot reuse a previous run.")
            return intake.submit(project_id, body.brief, body.request_id, body.intake_confirmation, prepare,
                lambda:service.submit_research(project_id, body.brief, parent_run_id="", prepare_targets=prepare))
        return service.submit_research(project_id, body.brief, parent_run_id=body.parent_run_id,
                                       prepare_targets=prepare)

    @app.get("/api/projects/{project_id}/runs/{run_id}")
    def run(project_id: str, run_id: str):
        return store.load_run(project_id, run_id)

    @app.get("/api/projects/{project_id}/runs/{run_id}/results")
    def research_results(project_id: str, run_id: str):
        return result_document(store.load_run(project_id, run_id))

    @app.get("/api/projects/{project_id}/runs/{run_id}/export")
    def export(project_id: str, run_id: str,
               format: Literal["markdown", "json", "csv", "zip"] = "markdown"):
        record = store.load_run(project_id, run_id)
        content, mime, filename = export_research(store, record, format)
        return Response(content, media_type=mime, headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        })

    @app.post("/api/projects/{project_id}/targets", status_code=201)
    def prepare_target(project_id: str, body: TargetInput):
        run = store.load_run(project_id, body.run_id)
        if run.status.value in ("queued", "running"):
            raise ValueError("Wait for the research job to finish before reviewing its target input")
        output = store.run_dir(project_id, body.run_id) / "targets" / "manual"
        package = build_target_package(body.request, output)
        # Append new immutable review/configuration; do not overwrite original source artifacts.
        if not any(p.package_id == package.package_id for p in run.target_packages):
            run.target_packages.append(package)
        for target in run.targets:
            if target.uniprot_id == body.request.uniprot_id:
                target.package_id, target.structure_status = package.package_id, package.status
        store.save_run(run)
        store.artifact_path(project_id, run.report_path).write_text(render_report(run))
        return package

    @app.get("/api/projects/{project_id}/memory")
    def memory(project_id: str, q: str = ""):
        return store.search_memory(project_id, q)

    @app.get("/api/projects/{project_id}/runs/{run_id}/target-packages/{package_id}/screening-checklist")
    def screening_checklist(project_id: str, run_id: str, package_id: str, response: Response):
        from .demo_handoff import target_screening_checklist
        run = store.load_run(project_id, run_id)
        package = next((p for p in run.target_packages if p.package_id == package_id), None)
        if package is None:
            raise FileNotFoundError("Target package not found in this run")
        response.headers["Cache-Control"] = "no-store"
        return target_screening_checklist(package, store.project_dir(project_id))

    @app.get("/api/projects/{project_id}/runs/{run_id}/target-packages/{package_id}/{file_kind}")
    def target_file(project_id: str, run_id: str, package_id: str, file_kind: str):
        run = store.load_run(project_id, run_id)
        if file_kind not in ("protein_pdb", "sequence_fasta", "reference_ligand_sdf"):
            raise FileNotFoundError("Unknown target artifact")
        package = next((p for p in run.target_packages if p.package_id == package_id), None)
        if package is None or not getattr(package, file_kind):
            raise FileNotFoundError("Target artifact not available")
        path = Path(getattr(package, file_kind)).resolve()
        project = store.project_dir(project_id).resolve()
        if not path.is_relative_to(project):
            raise FileNotFoundError("Target artifact is outside this project")
        return FileResponse(store.artifact_path(project_id, str(path.relative_to(project))), filename=path.name)

    @app.post("/api/projects/{project_id}/experiments", status_code=201)
    def experiment(project_id: str, body: ExperimentInput):
        if len(body.csv_text.splitlines()) > 50_000:
            raise ValueError("CSV exceeds 50,000 lines")
        run = None
        if body.spec.research_run_id:
            run = store.load_run(project_id, body.spec.research_run_id)
            if run.status.value in ("queued", "running"):
                raise ValueError("Research is running; upload feedback after it finishes")
            if body.spec.candidate_id and not any(body.spec.candidate_id in [c.candidate_id, *c.original_ids] for c in run.candidates):
                raise ValueError("Candidate ID does not belong to this research run")
        eid = "x_" + uuid.uuid4().hex[:16]
        output = store.project_dir(project_id) / "experiments" / eid
        output.mkdir(parents=True)
        csv_path = output / "upload.csv"
        csv_path.write_text(body.csv_text, encoding="utf-8")
        summary = analyze_experiment(csv_path, body.spec, output)
        summary.update(experiment_id=eid, created_at=utc_now())
        write_json(output / "summary.json", summary)
        if summary["status"] == "invalid_data":
            return JSONResponse(summary, status_code=422)
        artifact = str((output / "summary.json").relative_to(store.project_dir(project_id)))
        store.add_memory(project_id, kind="measured_assay", text=body.spec.name + ": " + summary["status"],
            artifact=artifact, verified=False, source_run_id=body.spec.research_run_id,
            metadata={"experiment_id": eid, "candidate_id": body.spec.candidate_id,
                      "qc_passed": body.spec.qc_passed, "scope": "submitted_assay_not_independent_verification"})
        if run:
            run.warnings.append(f"Measured assay {eid} recorded as {summary['status']}; linked candidate and chassis assessments retain their own review records")
            store.save_run(run)
            store.artifact_path(project_id, run.report_path).write_text(render_report(run))
        return summary

    @app.get("/api/projects/{project_id}/experiments")
    def experiments(project_id: str):
        return [json.loads(p.read_text()) for p in store.project_dir(project_id).glob("experiments/*/summary.json")]

    @app.post("/api/projects/{project_id}/runs/{run_id}/followup", status_code=202)
    def followup(project_id: str, run_id: str, body: FollowupInput):
        parent = store.load_run(project_id, run_id)
        if parent.status.value in ("queued", "running"):
            raise ValueError("Parent research is still running")
        for eid in body.experiment_ids:
            path = store.project_dir(project_id) / "experiments" / safe_id(eid) / "summary.json"
            if not path.is_file():
                raise ValueError("Experiment does not belong to this project: " + eid)
        brief = parent.brief.model_copy(deep=True)
        brief.experiment_ids = list(dict.fromkeys(brief.experiment_ids + body.experiment_ids))
        if body.question:
            brief.assumptions.append("Follow-up question (not evidence): " + body.question)
        return service.submit_research(project_id, brief, parent_run_id=run_id,
                                       prepare_targets=body.prepare_targets)

    @app.post("/api/projects/{project_id}/runs/{run_id}/continue-hypotheses", status_code=202)
    def continue_hypotheses(project_id: str, run_id: str, body: ContinueHypothesesInput):
        parent = store.load_run(project_id, run_id)
        if parent.status.value in ("queued", "running") or not parent.co_scientist or not parent.co_scientist.anchor or not parent.co_scientist.hypotheses:
            raise ValueError("Select a finished/partial Co-Scientist run with saved hypotheses first")
        brief = parent.brief.model_copy(deep=True)
        brief.continue_saved_hypotheses = True
        brief.budget.max_llm_calls = body.max_llm_calls
        brief.budget.timeout_seconds = body.timeout_seconds
        brief.budget.max_output_tokens = max(brief.budget.max_output_tokens, 96000)
        brief.budget.max_tool_calls = 0
        brief.budget.max_http_requests = 1
        return service.submit_research(project_id, brief, parent_run_id=run_id, prepare_targets=False)

    @app.post("/api/projects/{project_id}/compute", status_code=201)
    def handoff(project_id: str, body: HandoffInput):
        return service.create_handoff(project_id, body.run_id, body.package_id, body.max_md_jobs)

    @app.get("/api/projects/{project_id}/compute")
    def compute_jobs(project_id: str):
        return service.list_compute(project_id)

    @app.get("/api/projects/{project_id}/compute/{compute_id}")
    def compute(project_id: str, compute_id: str):
        job = service.compute_job(project_id, compute_id)
        return {"job": job, "results": collect_compute_results(Path(job["config_path"]))}

    @app.get("/api/projects/{project_id}/compute/{compute_id}/preflight")
    def preflight(project_id: str, compute_id: str, until: str = "md"):
        job = service.compute_job(project_id, compute_id)
        return compute_preflight(Path(job["config_path"]), until=until)

    @app.post("/api/projects/{project_id}/compute/{compute_id}/run", status_code=202)
    def compute_run(project_id: str, compute_id: str, body: ComputeInput):
        return service.submit_compute(project_id, compute_id, until=body.until, resume=body.resume)

    @app.post("/api/projects/{project_id}/compute/{compute_id}/collect")
    def collect(project_id: str, compute_id: str):
        job = service.compute_job(project_id, compute_id)
        if job["status"] in ("queued", "running"):
            raise ValueError("Wait for compute to finish before collecting into the report")
        results = collect_compute_results(Path(job["config_path"]))
        run = store.load_run(project_id, job["research_run_id"])
        old = {c.candidate_id: c for c in run.candidates}
        aliases = {a["original_id"]: a["canonical_id"] for a in results["aliases"]}
        remapped = {aliases.get(cid, cid): c for cid, c in old.items()}
        from target2lead.common import load_config
        from target2lead.selection import admet_pass
        config = load_config(Path(job["config_path"]))
        for row in results["candidates"]:
            cid = row["compound_id"]
            candidate = remapped.get(cid)
            if candidate is None:
                candidate = CandidateRecord(candidate_id=cid, name=row.get("name", cid),
                    smiles=row.get("smiles", ""), inchikey=row.get("inchikey", ""),
                    identity_status="resolved", routes=["target_screening"])
                run.candidates.append(candidate)
            if candidate.candidate_id != cid:
                candidate.original_ids = list(dict.fromkeys(candidate.original_ids + [candidate.candidate_id]))
            candidate.candidate_id = cid
            candidate.scores.extend([{**s, "target_package_id": job["target_package_id"],
                "compute_id": compute_id} for s in row["scores"]
                if not any(x.get("compute_id") == compute_id and x.get("model") == s.get("model") and
                           x.get("original_compound_id") == s.get("original_compound_id") for x in candidate.scores)])
            candidate.admet, candidate.gate = row["admet"], row["gate"]
            passed, reason = admet_pass(candidate.admet, config.get("selection", {}).get("admet_bounds", {}))
            candidate.admet.update(selection_pass=passed, selection_reason=reason)
            candidate.routes = list(dict.fromkeys(candidate.routes + ["target_screening"])) if row["scores"] else candidate.routes
        decide_candidates(run.candidates, run.evidence)
        run.warnings.append(f"Collected {compute_id}: {results['status']} (model predictions with recorded source and computation status)")
        store.save_run(run)
        store.artifact_path(project_id, run.report_path).write_text(render_report(run))
        return results

    @app.get("/api/projects/{project_id}/artifacts/{relative:path}")
    def artifact(project_id: str, relative: str):
        path = store.artifact_path(project_id, relative)
        return FileResponse(path, filename=path.name)

    return app

