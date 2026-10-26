"""Run the Wiki screening workflow; research and molecular analysis stay separate."""
import argparse
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
from target2lead.common import ROOT, load_config, runtime_env, command, write_json

MODELS = ('drugclip', 'sprint', 'pharmaconet', 'psichic')
STAGES = ('prepare', *MODELS, 'candidates', 'admet', 'composite', 'gate', 'stratify')


def worker(cfg, stage):
    root = Path(cfg['run']['output'])
    if stage == 'prepare':
        from target2lead.target import prepare
        return prepare(cfg)
    if stage in ('candidates', 'composite', 'stratify'):
        from . import scoring
        return getattr(scoring, stage)(cfg)
    if stage == 'admet':
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
    module = importlib.import_module('target2lead.adapters.' + stage)
    output = root/'screen'/stage if stage in MODELS else root/stage
    return module.run(cfg, output)


def run(cfg, until, resume):
    root = Path(cfg['run']['output'])
    root.mkdir(parents=True, exist_ok=True)
    effective, status_path = root/'config.json', root/'status.json'
    if effective.exists() and (not resume or json.loads(effective.read_text()) != cfg):
        raise ValueError('Choose a new output directory, or --resume with the unchanged configuration')
    write_json(effective, cfg)
    state = json.loads(status_path.read_text()) if status_path.exists() else {'stages': {}}
    env = runtime_env(root)
    for stage in STAGES[:STAGES.index(until) + 1]:
        if resume and state['stages'].get(stage, {}).get('status') == 'completed':
            continue
        state['stages'][stage] = {'status': 'running'}
        write_json(status_path, state)
        python = cfg.get(stage, {}).get('python', cfg['run'].get('python', sys.executable))
        try:
            command([python, ROOT/'run.py', '--config', effective, '_worker', stage],
                    root, root/'logs'/f'{stage}.log', env=env)
            state['stages'][stage] = {'status': 'completed'}
        except Exception:
            state['stages'][stage] = {'status': 'failed'}
            write_json(status_path, state)
            raise
        write_json(status_path, state)
        print(stage + ': completed', flush=True)


def postprocess(cfg):
    """Import existing prediction tables, without pretending to run models."""
    from . import scoring
    root = Path(cfg['run']['output'])
    if root.exists() and any(root.iterdir()):
        raise ValueError('postprocess requires a new or empty output directory')
    sources = cfg['inputs']
    root.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        output = root/'screen'/model
        output.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sources['scores'][model], output/'scores.csv')
    for name in ('admet', 'gate'):
        (root/name).mkdir(exist_ok=True)
        shutil.copyfile(sources[name], root/name/'predictions.csv')
    write_json(root/'config.json', cfg)
    result = {'input_mode': sources.get('kind', 'imported_predictions'),
              'new_model_inference': False,
              'candidates': scoring.candidates(cfg),
              'composite': scoring.composite(cfg),
              'stratification': scoring.stratify(cfg)}
    write_json(root/'summary.json', result)
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description='SynBioPilot: four-model Top1000 → ADMET8 composite → chassis route stratification')
    parser.add_argument('--config', type=Path, default=ROOT/'configs/hdac2.json')
    sub = parser.add_subparsers(dest='operation', required=True)
    sub.add_parser('plan')
    sub.add_parser('postprocess', help='Process saved predictions; no models or services are invoked')
    sub.add_parser('bridge', help='Optional ChassisGate file-queue sidecar')
    execute = sub.add_parser('run')
    execute.add_argument('--until', choices=STAGES, default='stratify')
    execute.add_argument('--resume', action='store_true')
    internal = sub.add_parser('_worker')
    internal.add_argument('stage', choices=STAGES)
    library = sub.add_parser('prepare-library')
    library.add_argument('--inputs', type=Path, nargs='+', required=True)
    library.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.operation == 'prepare-library':
        from .library import prepare_library
        prepare_library(args.inputs, args.output)
        return
    cfg = load_config(args.config)
    if tuple(cfg['run'].get('models', MODELS)) != MODELS:
        raise ValueError('This workflow uses DrugCLIP, SPRINT, PharmacoNet and PSICHIC in that order')
    for key, value in cfg.get('inputs', {}).items():
        if key == 'scores':
            cfg['inputs'][key] = {m: str((args.config.parent/p).resolve()) for m, p in value.items()}
        elif key in ('admet', 'gate'):
            cfg['inputs'][key] = str((args.config.parent/value).resolve())
    if args.operation == 'plan':
        print(json.dumps({'target': cfg['target']['id'], 'stages': STAGES,
            'top_per_model': cfg['run'].get('top_k', 1000), 'chassis': cfg['gate']['chassis_name'],
            'output': cfg['run']['output'], 'scientist': 'components/synbioscout',
            'molecular_analysis': 'target-specific molecular study; see molecular/README.md',
            'new_model_inference': False}, indent=2))
    elif args.operation == 'postprocess':
        postprocess(cfg)
    elif args.operation == 'bridge':
        from target2lead.llm import bridge
        os.environ.update(runtime_env(cfg['run']['output']))
        bridge(cfg)
    elif args.operation == '_worker':
        os.environ.update(runtime_env(cfg['run']['output']))
        result = worker(cfg, args.stage)
        if result is not None:
            write_json(Path(cfg['run']['output'])/'stage_results'/f'{args.stage}.json', result)
    else:
        run(cfg, args.until, args.resume)


if __name__ == '__main__':
    main()
