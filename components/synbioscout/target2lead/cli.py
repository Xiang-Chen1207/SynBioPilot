import argparse
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import sys
from .common import ROOT, MODELS, load_config, runtime_env, command, write_json, input_fingerprints

BASE_STAGES = ('prepare', *MODELS, 'top100', 'admet', 'gate', 'select', 'md')
STAGES = ('prepare', *MODELS, 'top100', 'merge_candidates', 'admet', 'gate', 'select', 'md')


def stages_for_config(cfg):
    return STAGES if cfg.get('scientist', {}).get('literature_candidates') else BASE_STAGES


def stage_output(root, stage):
    return root/'screen'/stage if stage in MODELS else root/({'prepare': 'target', 'select': 'selection'}.get(stage, stage))


def worker(cfg, stage):
    root = Path(cfg['run']['output'])
    os.environ.update(runtime_env(root))
    os.chdir(root)
    output = stage_output(root, stage)
    if stage == 'prepare':
        from .target import prepare
        return prepare(cfg)
    if stage in ('top100', 'merge_candidates', 'select'):
        from . import selection
        return getattr(selection, stage)(cfg)
    if stage == 'admet':
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
    if stage == 'md':
        from .selection import remap_prepared_systems
        remap_prepared_systems(cfg)
    settings = cfg.get(stage, {})
    threads = str(settings.get('threads', 4))
    for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS'):
        os.environ[key] = threads
    adapter = importlib.import_module('target2lead.adapters.'+stage)
    return adapter.run(cfg, output)


def run(cfg, start, until, resume):
    root = Path(cfg['run']['output'])
    root.mkdir(parents=True, exist_ok=True)
    env = runtime_env(root)
    effective = root/'config.json'
    state_path = root/'status.json'
    if effective.exists():
        if json.loads(effective.read_text()) != cfg:
            raise ValueError('Run configuration changed. Choose a new run.output; old results are preserved.')
        if not resume:
            raise ValueError('Output already exists; use --resume or a new run.output')
    write_json(effective, cfg)
    state = json.loads(state_path.read_text()) if state_path.exists() else {'stages': {}}
    stages = stages_for_config(cfg)
    if start not in stages or until not in stages:
        raise ValueError('merge_candidates requires scientist.literature_candidates')
    if cfg.get('scientist', {}).get('literature_candidates'):
        current_inputs = input_fingerprints(cfg)
        configured_inputs = cfg['scientist'].get('input_fingerprints', {})
        changed = any(name not in current_inputs or expected['path'] != current_inputs[name]['path']
                      or (expected.get('sha256') is not None and expected != current_inputs[name])
                      for name, expected in configured_inputs.items())
        if changed or (state.get('input_fingerprints') is not None and
                       state['input_fingerprints'] != current_inputs):
            raise ValueError('Scientific inputs changed. Choose a new run.output; old results are preserved.')
        if any(value['sha256'] is None for value in current_inputs.values()):
            raise ValueError('Scientific inputs are missing; inspect target, library and literature candidate paths')
        state['input_fingerprints'] = current_inputs
    begin, end = stages.index(start), stages.index(until)
    if begin > end:
        raise ValueError('--from must precede --until')
    for previous in stages[:begin]:
        if state['stages'].get(previous, {}).get('status') != 'completed':
            raise ValueError(f'Required prior stage {previous} is incomplete; resume from it')
    for stage in stages[begin:end+1]:
        if resume and state['stages'].get(stage, {}).get('status') in ('completed', 'skipped'):
            continue
        state['stages'][stage] = {'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat()}
        write_json(state_path, state)
        print(f'Running {stage} ...', flush=True)
        python = cfg.get(stage, {}).get('python', cfg['run'].get('python', sys.executable))
        try:
            command([python, ROOT/'run.py', '--config', effective, '_worker', stage],
                    root, root/'logs'/f'{stage}.log', env=env)
        except Exception as exc:
            state['stages'][stage].update(status='failed', error=str(exc))
            write_json(state_path, state)
            raise
        detail_path = stage_output(root, stage)/'stage_result.json'
        detail = json.loads(detail_path.read_text()) if detail_path.exists() else None
        status = 'completed'
        if stage == 'md' and detail and detail.get('status') in ('partial', 'failed', 'skipped'):
            status = detail['status']
        state['stages'][stage].update(status=status, finished_at=datetime.now(timezone.utc).isoformat())
        if detail:
            state['stages'][stage]['result'] = detail
        write_json(state_path, state)
        if status not in ('completed', 'skipped'):
            raise RuntimeError(f'MD finished with status {status}; see {root}/md/results.csv')
    print(f'Finished through {until}: {root}', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Four-model screening → Top100 ADMET/Gate → per-model Top5 MD')
    parser.add_argument('--config', required=True, type=Path)
    sub = parser.add_subparsers(dest='operation', required=True)
    sub.add_parser('plan')
    sub.add_parser('status')
    sub.add_parser('bridge', help='Run the Codex sidecar on a host with an existing Codex login')
    execute = sub.add_parser('run')
    execute.add_argument('--from', dest='start', choices=STAGES, default='prepare')
    execute.add_argument('--until', choices=STAGES, default='md')
    execute.add_argument('--resume', action='store_true')
    internal = sub.add_parser('_worker')
    internal.add_argument('stage', choices=STAGES)
    args = parser.parse_args()
    cfg = load_config(args.config)
    root = Path(cfg['run']['output'])
    if args.operation == 'plan':
        print(json.dumps({'target': cfg['target'], 'library': cfg['library']['molecules'],
            'chassis': cfg['gate']['chassis_name'], 'stages': stages_for_config(cfg), 'output': str(root),
            'top_per_model': cfg['run'].get('top_k', 100),
            'md_per_model': cfg['selection'].get('top_k_per_model', 5),
            'allow_gate_abstain': cfg['selection'].get('allow_gate_abstain', True),
            'note': 'Plan only: no model inference, downloads, or MD executed'}, ensure_ascii=False, indent=2))
    elif args.operation == 'status':
        path = root/'status.json'
        print(path.read_text() if path.exists() else 'No run has started.')
    elif args.operation == 'bridge':
        os.environ.update(runtime_env(root))
        from .llm import bridge
        bridge(cfg)
    elif args.operation == '_worker':
        result = worker(cfg, args.stage)
        if result is not None:
            write_json(stage_output(root, args.stage)/'stage_result.json', result)
    else:
        run(cfg, args.start, args.until, args.resume)


if __name__ == '__main__':
    main()

