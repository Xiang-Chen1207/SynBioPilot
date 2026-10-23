"""Explicit, file-based integration with the real Target2Lead compute pipeline.

This module writes configurations for dedicated inference and job-execution entry
points. Biological evaluation retains its own evidence and status.
"""
from __future__ import annotations

import copy
import csv
import json
import math
from pathlib import Path
from typing import Any

from target2lead.common import (
    MODELS,
    input_fingerprints,
    library_frame,
    load_config,
    read_csv,
    write_csv,
    write_json,
)
from target2lead.selection import canonical_chemical_identity

from .models import CandidateRecord, TargetPackage

LITERATURE_FIELDS = ['compound_id', 'candidate_id', 'name', 'smiles', 'inchikey',
                     'pubchem_cid', 'original_ids', 'routes', 'evidence_ids',
                     'target_relations', 'rationale', 'uncertainties', 'identity_status',
                     'decision', 'decision_reasons']


def write_literature_candidates(candidates: list[CandidateRecord], path: Path) -> Path:
    """Write a source table retaining evidence/identity without inventing model scores."""
    path = Path(path).resolve()
    rows = []
    seen = set()
    for candidate in candidates:
        if not candidate.candidate_id or candidate.candidate_id in seen:
            raise ValueError('Literature candidate IDs must be nonempty and unique')
        seen.add(candidate.candidate_id)
        row = candidate.model_dump(mode='json')
        row['compound_id'] = candidate.candidate_id
        row['routes'] = list(dict.fromkeys(['literature', *candidate.routes]))
        row['original_ids'] = list(dict.fromkeys([candidate.candidate_id, *candidate.original_ids]))
        for key in ('original_ids', 'routes', 'evidence_ids', 'target_relations',
                    'uncertainties', 'decision_reasons'):
            row[key] = json.dumps(row[key], ensure_ascii=False, sort_keys=True)
        rows.append({key: row[key] for key in LITERATURE_FIELDS})
    write_csv(path, rows, LITERATURE_FIELDS)
    return path


def _map_prepared_systems(cfg: dict[str, Any], candidates: list[CandidateRecord]) -> None:
    systems = cfg['md'].get('prepared_systems', {})
    if not systems:
        return
    mapping = {}

    def add(identifier, smiles):
        canonical = canonical_chemical_identity(smiles)['compound_id']
        if identifier in mapping and mapping[identifier] != canonical:
            raise ValueError(f'Ambiguous prepared-system alias {identifier!r}')
        mapping[identifier] = canonical

    # Prepared systems are unusual and must have an exact chemical identity mapping.
    # The normal handoff does not load a potentially large molecular library.
    needed_ids = set(systems) | {identifier for candidate in candidates
                                for identifier in [candidate.candidate_id, *candidate.original_ids]}
    frame = library_frame(cfg)
    for row in frame[frame.compound_id.isin(needed_ids)].to_dict(orient='records'):
        add(row['compound_id'], row['smiles'])
    for candidate in candidates:
        for identifier in [candidate.candidate_id, *candidate.original_ids]:
            add(identifier, candidate.smiles)
    canonical_ids = set(mapping.values())
    remapped = {}
    for original, system in systems.items():
        canonical = mapping.get(original, original if original in canonical_ids else None)
        if canonical is None:
            raise ValueError(f'Prepared-system ID {original!r} has no unambiguous candidate/library mapping')
        if canonical in remapped and remapped[canonical] != system:
            raise ValueError(f'Conflicting prepared systems refer to the same structure {canonical}')
        remapped[canonical] = copy.deepcopy(system)
    cfg['md']['prepared_systems'] = remapped


def create_compute_config(base_config_path: Path, package: TargetPackage,
                          candidates: list[CandidateRecord], output: Path,
                          max_md_jobs: int = 20) -> Path:
    """Create an immutable local handoff in ``output`` and return its config path.

    Paths from the base file are resolved before moving the config. Model assets are
    checked at launch, allowing a handoff to be prepared on a machine without GPUs.
    """
    if package.status != 'ready':
        raise ValueError('Compute requires a ready, reviewed target package')
    if isinstance(max_md_jobs, bool) or not isinstance(max_md_jobs, int) or not 0 <= max_md_jobs <= 20:
        raise ValueError('max_md_jobs must be an integer between 0 and 20')
    cfg = copy.deepcopy(load_config(Path(base_config_path)))
    output = Path(output).resolve()
    if output.parts[1:2] in [('home',), ('tmp',)]:
        raise ValueError('Compute output must not be under /home or /tmp')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Compute output must be a new or empty directory; existing results are immutable')
    for key in ('protein_pdb', 'sequence_fasta', 'reference_ligand_sdf'):
        value = getattr(package, key)
        path = Path(value)
        if not value or not path.is_absolute() or not path.is_file():
            raise ValueError(f'Ready target package requires an existing absolute {key} path')
        cfg['target'][key] = str(path.resolve())
    cfg['target']['id'] = str(package.metadata.get('target_id') or package.package_id)
    if not package.chain:
        raise ValueError('Ready target package must identify the selected protein chain')
    cfg['target']['chain'] = package.chain
    if package.pocket_center:
        if len(package.pocket_center) != 3 or not all(math.isfinite(v) for v in package.pocket_center):
            raise ValueError('Target pocket center requires three finite coordinates')
        cfg['target']['pocket_center'] = list(package.pocket_center)
    else:
        cfg['target'].pop('pocket_center', None)
    cfg['run']['output'] = str(output)
    cfg['run']['top_k'] = min(100, max(1, int(cfg['run'].get('top_k', 100))))
    cfg['selection']['top_k_per_model'] = min(5, max(0, int(cfg['selection'].get('top_k_per_model', 5))))
    cfg['gate'].setdefault('chassis_id', 'lplantarum')
    cfg['gate'].setdefault('chassis_name', 'Lactiplantibacillus plantarum WCFS1')
    cfg['gate'].setdefault('kegg_organism_code', 'lpl')
    if cfg['gate']['chassis_id'] == 'lplantarum' and 'l168' in cfg['gate']['chassis_name'].lower():
        raise ValueError('The lplantarum GEM represents WCFS1; an L168 chassis needs its own validated model')
    conditions = package.metadata.get('compute_conditions', {})
    if not isinstance(conditions, dict) or set(conditions) - {'temperature_k', 'salt_molar'}:
        raise ValueError('Compute conditions support only temperature_k and salt_molar; pH and other overrides need engine support')
    for key, value in conditions.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'Invalid compute condition {key}')
        if (key == 'temperature_k' and value <= 0) or (key == 'salt_molar' and value < 0):
            raise ValueError(f'Invalid compute condition {key}')
        cfg['md'][key] = value
    _map_prepared_systems(cfg, candidates)
    source = output / 'literature_candidates.csv'
    cfg.setdefault('scientist', {}).update(literature_candidates=str(source),
        target_package_id=package.package_id, max_md_jobs=max_md_jobs,
        condition_policy={'source': 'base_config_with_explicit_supported_overrides',
                          'explicit_overrides': conditions,
                          'temperature_k': cfg['md'].get('temperature_k', 300),
                          'salt_molar': cfg['md'].get('salt_molar', 0.15),
                          'pH': 'not controlled by current MD engine'})
    output.mkdir(parents=True, exist_ok=True)
    write_literature_candidates(candidates, source)
    cfg['scientist']['input_fingerprints'] = input_fingerprints(cfg)
    path = output / 'compute_config.json'
    write_json(path, cfg)
    return path


def collect_compute_results(config_path: Path) -> dict:
    """Read persisted real outputs, preserving raw scores and failure/error records."""
    cfg = load_config(Path(config_path))
    root = Path(cfg['run']['output'])
    files, read_errors = {}, []

    def read(relative, kind='csv'):
        path = root / relative
        if not path.is_file():
            return {} if kind == 'json' else []
        files[relative] = str(path.resolve())
        try:
            return json.loads(path.read_text()) if kind == 'json' else read_csv(path)
        except (OSError, ValueError) as exc:
            read_errors.append({'file': str(path), 'error': f'{type(exc).__name__}: {exc}'})
            return {} if kind == 'json' else []

    state = read('status.json', 'json')
    job = read('job.json', 'json')
    stages = state.get('stages', {})
    unique = read('unique_candidates.csv')
    aliases = read('candidate_aliases.csv')
    selected_ids = {row['compound_id'] for row in unique}
    alias_to_canonical = {row['original_id']: row['canonical_id'] for row in aliases
                          if row['canonical_id'] in selected_ids}
    selected_structures, selected_keys = {}, set()
    for row in unique:
        cid = row['compound_id']
        alias_to_canonical[cid] = cid
        if row.get('original_ids'):
            try:
                original_ids = json.loads(row['original_ids'])
                if not isinstance(original_ids, list):
                    raise TypeError('original_ids is not a JSON list')
                for original in original_ids:
                    alias_to_canonical[str(original)] = cid
            except (ValueError, TypeError) as exc:
                read_errors.append({'file': str(root / 'unique_candidates.csv'),
                                    'error': f'Invalid candidate aliases: {exc}'})
        if row.get('smiles'):
            # A small structure index also discovers real scores for literature-only
            # compounds present in the full library but absent from model Top100.
            try:
                identity = canonical_chemical_identity(row['smiles'])
                selected_structures[(identity['smiles'], identity['inchikey'])] = cid
                selected_keys.add(identity['inchikey'])
            except ValueError:
                pass
    canonical_smiles = {smiles for smiles, key in selected_structures}
    scores, score_counts, score_files, real_scores = {}, {}, {}, {}
    for model in MODELS:
        relative = f'screen/{model}/scores.csv'
        path = root / relative
        scores[model] = []
        counts = {'total': 0, 'retained': 0, 'status_counts': {}, 'read_complete': False}
        score_counts[model] = counts
        if not path.is_file():
            continue
        files[relative] = score_files[model] = str(path.resolve())
        try:
            with path.open(encoding='utf-8-sig', newline='') as stream:
                for row in csv.DictReader(stream):
                    counts['total'] += 1
                    state_name = row.get('status', '')
                    counts['status_counts'][state_name] = counts['status_counts'].get(state_name, 0) + 1
                    original = row.get('compound_id', '')
                    canonical = alias_to_canonical.get(original)
                    if canonical is None and (row.get('inchikey') in selected_keys or
                                              row.get('smiles') in canonical_smiles):
                        try:
                            identity = canonical_chemical_identity(row.get('smiles', ''))
                            canonical = selected_structures.get((identity['smiles'], identity['inchikey']))
                        except ValueError:
                            pass
                    if canonical not in selected_ids:
                        continue
                    scores[model].append(row)
                    counts['retained'] += 1
                    real_scores.setdefault(canonical, []).append({**row, 'model': model,
                        'original_compound_id': original, 'canonical_compound_id': canonical})
            counts['read_complete'] = True
        except (OSError, ValueError, csv.Error) as exc:
            read_errors.append({'file': str(path), 'error': f'{type(exc).__name__}: {exc}'})
    membership = read('top100_membership.csv')
    assessments = {key: read(relative) for key, relative in
                   [('admet', 'admet/predictions.csv'), ('gate', 'gate/predictions.csv')]}
    shortlist = read('selection/literature_shortlist.csv')
    md_candidates = read('selection/md_candidates.csv')
    md_results = read('md/results.csv')
    selection_summary = read('selection/summary.json', 'json')
    md_stage = read('md/stage_result.json', 'json')
    stage_artifacts = {'prepare': ['target/target.json'],
                       **{model: [f'screen/{model}/scores.csv'] for model in MODELS},
                       'top100': ['top100_membership.csv', 'unique_candidates.csv'],
                       'merge_candidates': ['candidate_aliases.csv', 'literature_membership.csv'],
                       'admet': ['admet/predictions.csv'], 'gate': ['gate/predictions.csv'],
                       'select': ['selection/md_candidates.csv'], 'md': ['md/results.csv']}
    missing_files = [relative for stage, artifacts in stage_artifacts.items()
                     if stages.get(stage, {}).get('status') == 'completed'
                     for relative in artifacts if not (root / relative).is_file()]
    candidates = []
    lookup = {key: {row['compound_id']: row for row in rows} for key, rows in assessments.items()}
    md_lookup = {row['compound_id']: row for row in md_results}
    for row in unique:
        cid = row['compound_id']
        candidates.append({**row, 'scores': real_scores.get(cid, []),
                           'admet': lookup['admet'].get(cid, {}),
                           'gate': lookup['gate'].get(cid, {}), 'md': md_lookup.get(cid, {})})
    statuses = [stage.get('status', '') for stage in stages.values()]
    if 'failed' in statuses or md_stage.get('status') == 'failed':
        status = 'failed'
    elif 'partial' in statuses or md_stage.get('status') == 'partial':
        status = 'partial'
    elif 'running' in statuses:
        status = 'running'
    elif 'cancelled' in statuses:
        status = 'cancelled'
    elif not stages:
        status = 'not_started'
    else:
        from target2lead.cli import stages_for_config
        expected = stages_for_config(cfg)
        status = 'completed' if all(stages.get(stage, {}).get('status') in ('completed', 'skipped')
                                    for stage in expected) else 'incomplete'
    failures = [row for row in md_results if row.get('status') == 'failed']
    if status == 'completed' and (failures or read_errors):
        status = 'partial' if any(row.get('status') == 'success' for row in md_results) else 'failed'
    if status == 'completed' and missing_files:
        status = 'incomplete'
    job_status = job.get('status', '')
    if job_status in ('failed', 'interrupted', 'blocked', 'cancelled') or job_status in ('queued', 'running'):
        status = job_status
    elif job_status == 'completed' and status == 'incomplete' and not missing_files:
        from target2lead.cli import stages_for_config
        expected = stages_for_config(cfg)
        until = job.get('until')
        if until in expected and all(stages.get(stage, {}).get('status') in ('completed', 'skipped')
                                      for stage in expected[:expected.index(until) + 1]):
            status = 'completed_until'
    return {'status': status,
            'status_basis': 'persisted_job_terminal_status_overrides_stage_status; stage_output_records_and_artifacts_verified',
            'output': str(root), 'config_path': str(Path(config_path).resolve()),
            'job': job, 'job_status': job_status, 'completed_until': job.get('until', '')
            if status in ('completed', 'completed_until') else '',
            'stages': stages, 'files': files, 'read_errors': read_errors, 'missing_files': missing_files,
            'scores': scores, 'score_counts': score_counts, 'score_files': score_files,
            'score_scope': 'Raw rows matching shared unique candidates by original-ID aliases or exact chemical identity; full tables remain linked',
            'candidates': candidates, 'aliases': aliases,
            'top100_membership': membership, 'assessments': assessments,
            'literature_shortlist': shortlist, 'selection': selection_summary,
            'md_candidates': md_candidates, 'md_results': md_results, 'md_stage': md_stage,
                'interpretation_limits': ['Raw scores express within-model ranking under the model-specific scoring definition.',
                'Gate E is an exploratory abstention; execution failure remains a failure.',
                'Docking/MD status reports computation progress; outputs contain pose and trajectory metrics.']}

