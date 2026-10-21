"""Local orchestration tests: fixtures/mocks only, no inference or cloud launch."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scientist import runtime
from scientist.models import (
    ExecutionStatus,
    LLMSettings,
    PlanStep,
    ResearchBrief,
    ResearchBudget,
    ScientistSettings,
    TargetPackage,
)
from scientist.store import write_json
from target2lead.common import ROOT, load_config


class FakeEngine:
    def __init__(self):
        self.store = None
        self.calls = []

    def execute(self, project_id, run_id, **kwargs):
        self.calls.append((project_id, run_id, kwargs))
        run = self.store.load_run(project_id, run_id)
        run.status = ExecutionStatus.COMPLETED
        self.store.save_run(run)
        return run


class RecordingExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        return SimpleNamespace()

    def shutdown(self, **kwargs):
        pass


@pytest.fixture
def service(tmp_path):
    cfg = load_config(ROOT / 'configs/keap1.json')
    cfg['run']['output'] = str(tmp_path / 'unused-base-output')
    cfg['run']['python'] = sys.executable
    base = tmp_path / 'base.json'
    write_json(base, cfg)
    settings = ScientistSettings(workspace=str(tmp_path / 'workspace'),
                                 target2lead_config=str(base), llm=LLMSettings(backend='none'))
    engine = FakeEngine()
    service = runtime.JobService(settings, recover=False, engine=engine)
    engine.store = service.store
    service.executor.shutdown(wait=False)
    service.executor = RecordingExecutor()
    project = service.store.create_project('Local fixture project')
    pid = project['project_id']
    run = service.store.create_run(pid, ResearchBrief(disease='fixture only',
        budget=ResearchBudget(max_md_jobs_total=4)))
    run.status = ExecutionStatus.COMPLETED
    run.target_packages = [TargetPackage(package_id='fixture-ready-package', status='ready',
        chain='A', **{key: cfg['target'][key] for key in
                     ('protein_pdb', 'sequence_fasta', 'reference_ligand_sdf')})]
    service.store.save_run(run)
    yield service, pid, run.run_id, settings, engine
    service.shutdown()


def test_handoff_reservations_enforce_total_md_budget(service):
    svc, pid, rid, _, engine = service
    first = svc.create_handoff(pid, rid, 'fixture-ready-package', max_md_jobs=2)
    second = svc.create_handoff(pid, rid, 'fixture-ready-package', max_md_jobs=2)
    assert first['max_md_jobs'] + second['max_md_jobs'] == 4
    assert Path(first['config_path']).is_file()
    with pytest.raises(ValueError, match='Total MD reservation'):
        svc.create_handoff(pid, rid, 'fixture-ready-package', max_md_jobs=1)
    assert len(svc.list_compute(pid)) == 2
    assert not svc.executor.calls and not engine.calls


def test_handoff_refuses_unreviewed_target_package(service):
    svc, pid, rid, _, _ = service
    run = svc.store.load_run(pid, rid)
    run.target_packages[0].status = 'needs_review'
    svc.store.save_run(run)
    with pytest.raises(ValueError, match='ready'):
        svc.create_handoff(pid, rid, 'fixture-ready-package', max_md_jobs=1)
    assert svc.list_compute(pid) == []
    assert svc.executor.calls == []


def test_preflight_block_prevents_subprocess_launch(service, monkeypatch):
    svc, pid, rid, _, _ = service
    job = svc.create_handoff(pid, rid, 'fixture-ready-package', max_md_jobs=1)
    missing = {'ready': False, 'checks': [{'name': 'drugclip.checkpoint_dir', 'ready': False}],
               'warnings': [], 'stages': ['prepare', 'drugclip']}
    monkeypatch.setattr(runtime, 'compute_preflight', lambda *args, **kwargs: missing)
    def forbidden(*args, **kwargs):
        pytest.fail('A blocked preflight must not launch a subprocess')
    monkeypatch.setattr(runtime.subprocess, 'run', forbidden)
    monkeypatch.setattr(runtime.subprocess, 'Popen', forbidden)
    result = svc.submit_compute(pid, job['compute_id'])
    assert result['status'] == 'blocked'
    assert result['preflight'] == missing
    assert svc.compute_job(pid, job['compute_id'])['status'] == 'blocked'
    assert (Path(job['config_path']).parent / 'preflight.json').is_file()
    assert svc.executor.calls == []


def test_restart_marks_jobs_interrupted_without_rerunning(service):
    svc, pid, rid, settings, engine = service
    job = svc.create_handoff(pid, rid, 'fixture-ready-package', max_md_jobs=1)
    job['status'] = 'running'
    write_json(Path(job['config_path']).parent / 'job.json', job)
    run = svc.store.load_run(pid, rid)
    run.status = ExecutionStatus.RUNNING
    run.steps.append(PlanStep(step_id='fixture-step', question='fixture',
                              capability='fixture', status='running'))
    svc.store.save_run(run)
    recovered = runtime.JobService(settings, recover=True, engine=engine)
    try:
        assert recovered.compute_job(pid, job['compute_id'])['status'] == 'interrupted'
        interrupted = recovered.store.load_run(pid, rid)
        assert interrupted.status == ExecutionStatus.FAILED
        assert interrupted.steps[0].status == 'interrupted'
        assert any('restarted' in error.lower() for error in interrupted.errors)
        assert engine.calls == []
    finally:
        recovered.shutdown()


def test_fake_research_engine_is_queued_and_runs_only_when_dispatched(service):
    svc, pid, _, _, engine = service
    run = svc.submit_research(pid, ResearchBrief(disease='fixture request'), prepare_targets=False)
    assert run.status == ExecutionStatus.QUEUED
    assert engine.calls == []
    fn, args, kwargs = svc.executor.calls[0]
    result = fn(*args, **kwargs)
    assert result.status == ExecutionStatus.COMPLETED
    assert engine.calls[0][2]['prepare_targets'] is False
    assert svc.store.load_run(pid, run.run_id).status == ExecutionStatus.COMPLETED


def test_exclusive_workspace_owner_rejects_second_service_and_releases_on_shutdown(tmp_path):
    settings = ScientistSettings(workspace=str(tmp_path / 'owned-workspace'),
                                 llm=LLMSettings(backend='none'))
    engine = FakeEngine()
    first = runtime.JobService(settings, recover=True, engine=engine)
    engine.store = first.store
    recovered = None
    try:
        pid = first.store.create_project('Lock fixture')['project_id']
        run = first.store.create_run(pid, ResearchBrief(disease='fixture pending request'))
        job_path = first.store.project_dir(pid) / 'compute/c_lock_fixture/job.json'
        write_json(job_path, {'status': 'running', 'compute_id': 'c_lock_fixture'})
        with pytest.raises(ValueError, match='owns this workspace'):
            runtime.JobService(settings, recover=True, engine=FakeEngine())
        with pytest.raises(ValueError, match='service is active'):
            runtime.JobService(settings, recover=False, engine=FakeEngine())
        assert first.store.load_run(pid, run.run_id).status == ExecutionStatus.QUEUED
        assert json.loads(job_path.read_text())['status'] == 'running'
        first.shutdown()
        first.shutdown()
        recovered = runtime.JobService(settings, recover=True, engine=FakeEngine())
        assert recovered.store.load_run(pid, run.run_id).status == ExecutionStatus.FAILED
        assert json.loads(job_path.read_text())['status'] == 'interrupted'
        assert engine.calls == []
    finally:
        first.shutdown()
        if recovered:
            recovered.shutdown()


def test_doctor_never_returns_environment_secrets(tmp_path, monkeypatch):
    secret = 'fixture-secret-not-for-output-7813'
    monkeypatch.setenv('OPENAI_API_KEY', secret)
    monkeypatch.setenv('CUSTOM_MODEL_TOKEN', secret)
    monkeypatch.setattr(runtime.shutil, 'which', lambda value: None)
    monkeypatch.setattr(runtime.importlib.util, 'find_spec', lambda name: None)
    settings = ScientistSettings(workspace=str(tmp_path),
        llm=LLMSettings(backend='none', api_key_env='CUSTOM_MODEL_TOKEN'))
    result = runtime.doctor(settings)
    assert secret not in json.dumps(result)
    assert 'api_key' not in result['llm']
    assert result['llm']['backend'] == 'none'
    assert result['llm']['timeout_seconds'] == settings.llm.timeout_seconds == 900
    assert result['dependencies']['rdkit'] is False


def test_compute_preflight_reports_missing_assets_without_running_models(tmp_path, monkeypatch):
    cfg = load_config(ROOT / 'configs/keap1.json')
    cfg['run']['output'] = str(tmp_path / 'compute-output')
    cfg['run']['python'] = sys.executable
    cfg['drugclip']['checkpoint_dir'] = str(tmp_path / 'missing-checkpoints')
    path = tmp_path / 'preflight.json'
    write_json(path, cfg)
    names = ['Bio', 'rdkit', 'torch', 'unicore', 'lmdb', 'pytorch_lightning', 'esm',
             'torch_geometric', 'admet_ai', 'cobra', 'pydantic', 'pandas']
    probes = []
    def diagnostic_only(args, **kwargs):
        probes.append(args)
        assert args[1] == '-c'
        assert 'run.py' not in args
        text = '1' if 'torch.cuda.is_available' in args[-1] else json.dumps({name: True for name in names})
        return SimpleNamespace(returncode=0, stdout=text, stderr='')
    monkeypatch.setattr(runtime.subprocess, 'run', diagnostic_only)
    monkeypatch.setattr(runtime.shutil, 'which', lambda value: sys.executable)
    result = runtime.compute_preflight(path)
    check = next(item for item in result['checks'] if item['name'] == 'drugclip.checkpoint_dir')
    assert result['ready'] is False
    assert check['ready'] is False
    assert check['value'] == str(tmp_path / 'missing-checkpoints')
    assert probes
    assert all(item[1] == '-c' for item in probes)
    assert 'md' in result['stages']

