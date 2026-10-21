from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MODELS = ('drugclip', 'sprint', 'psichic', 'pharmaconet')


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + '.partial')
    staged.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    staged.replace(path)


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    if fields is None:
        fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def runtime_env(output):
    runtime = Path(output).resolve() / '.runtime'
    settings = {'TMPDIR': 'tmp', 'TMP': 'tmp', 'TEMP': 'tmp',
                'XDG_CACHE_HOME': 'cache', 'XDG_CONFIG_HOME': 'config',
                'XDG_DATA_HOME': 'data', 'XDG_STATE_HOME': 'state',
                'PYTHONPYCACHEPREFIX': 'pycache', 'PIP_CACHE_DIR': 'cache/pip',
                'UV_CACHE_DIR': 'cache/uv', 'HF_HOME': 'cache/huggingface',
                'MPLCONFIGDIR': 'cache/matplotlib', 'NUMBA_CACHE_DIR': 'cache/numba',
                'CUDA_CACHE_PATH': 'cache/cuda', 'TRITON_CACHE_DIR': 'cache/triton',
                'TORCH_EXTENSIONS_DIR': 'cache/torch_extensions'}
    env = os.environ.copy()
    for key, suffix in settings.items():
        directory = runtime / suffix
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['TOKENIZERS_PARALLELISM'] = 'false'
    # TORCH_HOME may point at an external existing ESM cache; preserve it.
    inherited_torch = Path(env.get('TORCH_HOME', str(runtime/'cache/torch'))).resolve()
    if inherited_torch.parts[1:2] in [('home',), ('tmp',)]:
        inherited_torch = runtime/'cache/torch'
    env['TORCH_HOME'] = str(inherited_torch)
    Path(env['TORCH_HOME']).mkdir(parents=True, exist_ok=True)
    return env


def command(args, cwd, log, input_text=None, env=None):
    cwd = Path(cwd)
    cwd.mkdir(parents=True, exist_ok=True)
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('a') as stream:
        stream.write('\nCOMMAND ' + json.dumps([str(a) for a in args]) + '\n')
        stream.flush()
        result = subprocess.run([str(a) for a in args], cwd=cwd, env=env,
                                input=input_text, text=True, stdout=stream,
                                stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'Command exited {result.returncode}; see {log}')


def load_config(path):
    path = Path(path).resolve()
    cfg = json.loads(path.read_text())
    base = path.parent
    def resolve(value):
        value = os.path.expandvars(str(value))
        return str((base / value).resolve())
    for key in ('protein_pdb', 'sequence_fasta', 'reference_ligand_sdf'):
        cfg['target'][key] = resolve(cfg['target'][key])
    cfg['run']['output'] = resolve(cfg['run']['output'])
    if '/' in cfg['run'].get('python', ''):
        cfg['run']['python'] = resolve(cfg['run']['python'])
    if Path(cfg['run']['output']).parts[1:2] in [('home',), ('tmp',)]:
        raise ValueError('run.output must not be under /home or /tmp')
    cfg['library']['molecules'] = resolve(cfg['library']['molecules'])
    if cfg['library'].get('names_csv'):
        cfg['library']['names_csv'] = resolve(cfg['library']['names_csv'])
    for name in (*MODELS, 'admet', 'gate', 'md'):
        section = cfg[name]
        for key in ('python', 'repo', 'checkpoint', 'checkpoint_dir', 'weights',
                    'saprot_checkpoint', 'foldseek', 'screen_repo', 'models_dir',
                    'torch_home', 'codex_sqlite_home', 'gmx', 'vina', 'obabel', 'acpype',
                    'receptor_pdbqt'):
            if section.get(key) and ('/' in section[key] or key not in
                    ('python', 'gmx', 'vina', 'obabel', 'acpype', 'foldseek')):
                section[key] = resolve(section[key])
        for cache in section.get('embedding_caches', []):
            for key in ('embeddings', 'mappings'):
                cache[key] = resolve(cache[key])
    for system in cfg['md'].get('prepared_systems', {}).values():
        system['directory'] = resolve(system['directory'])
    for name, value in cfg['md'].get('tools', {}).items():
        if isinstance(value, list):
            if value and '/' in value[0]:
                value[0] = resolve(value[0])
        elif '/' in value and ' ' not in value:
            cfg['md']['tools'][name] = resolve(value)
    if cfg.get('scientist', {}).get('literature_candidates'):
        cfg['scientist']['literature_candidates'] = resolve(cfg['scientist']['literature_candidates'])
    return cfg


def input_fingerprints(cfg):
    """Hash scientific inputs, so a resumed merged run cannot reuse changed data."""
    paths = {f'target.{key}': cfg['target'][key] for key in
             ('protein_pdb', 'sequence_fasta', 'reference_ligand_sdf')}
    paths['library.molecules'] = cfg['library']['molecules']
    if cfg['library'].get('names_csv'):
        paths['library.names_csv'] = cfg['library']['names_csv']
    source = cfg.get('scientist', {}).get('literature_candidates')
    if source:
        paths['scientist.literature_candidates'] = source
    fingerprints = {}
    for name, value in paths.items():
        path = Path(value)
        if not path.is_file():
            fingerprints[name] = {'path': str(path.resolve()), 'sha256': None}
            continue
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        fingerprints[name] = {'path': str(path.resolve()), 'sha256': digest.hexdigest()}
    return fingerprints


def library_frame(cfg):
    import pandas as pd
    path = Path(cfg['library']['molecules'])
    frame = pd.read_parquet(path) if path.suffix == '.parquet' else pd.read_csv(path, dtype=str)
    columns = cfg['library'].get('columns', {})
    result = pd.DataFrame()
    for key, default in [('compound_id', 'compound_id'), ('smiles', 'smiles'),
                         ('name', 'name'), ('inchikey', 'inchikey')]:
        source = columns.get(key, default)
        if source in frame:
            result[key] = frame[source].fillna('').astype(str)
        elif key in ('compound_id', 'smiles'):
            raise ValueError(f'Library missing {source}; configure library.columns.{key}')
        else:
            result[key] = ''
    if result.empty or result.compound_id.eq('').any() or result.compound_id.duplicated().any():
        raise ValueError('Library must have nonempty unique compound_id values')
    if cfg['library'].get('names_csv'):
        names = pd.read_csv(cfg['library']['names_csv'], dtype=str).fillna('')
        lookup = names.set_index('compound_id')['name']
        result['name'] = result.compound_id.map(lookup).fillna(result['name'])
    return result


def finish_scores(frame, cfg, output, model):
    import numpy as np
    import pandas as pd
    library = library_frame(cfg)
    if frame.compound_id.duplicated().any():
        raise ValueError(f'{model}: duplicate compound_id in scores')
    frame = library.merge(frame, on='compound_id', how='left', validate='one_to_one')
    frame['raw_score'] = pd.to_numeric(frame['raw_score'], errors='coerce')
    finite = np.isfinite(frame['raw_score'])
    frame['status'] = frame['status'].fillna('not_encoded')
    frame['error'] = frame['error'].fillna('No successful input/embedding for this molecule')
    frame.loc[frame.status.eq('success') & ~finite, ['status', 'error']] = ['failed', 'Nonfinite score']
    frame['model'] = model
    frame['score_direction'] = 'higher_is_better'
    output.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output / 'scores.csv', index=False)
    count = int((frame.status.eq('success') & finite).sum())
    write_json(output / 'summary.json', {'model': model, 'rows': len(frame), 'success': count})
    if not count:
        raise RuntimeError(f'{model}: no successful scores; see {output}/scores.csv')

