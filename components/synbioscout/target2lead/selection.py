import math
import hashlib
import json
from pathlib import Path
from .common import MODELS, read_csv, write_csv, write_json


CHEMICAL_FIELDS = ['compound_id', 'smiles', 'name', 'inchikey', 'identity_status',
                   'identity_error', 'original_ids', 'source_routes', 'evidence_ids',
                   'provenance']
MEMBERSHIP_FIELDS = ['compound_id', 'smiles', 'name', 'inchikey', 'model', 'model_rank',
                     'raw_score', 'status', 'original_compound_id', 'original_ids',
                     'source_routes', 'evidence_ids', 'provenance']


def canonical_chemical_identity(smiles):
    """Identify the entire structure without stripping salts, stereo or isotopes."""
    from rdkit import Chem
    mol = Chem.MolFromSmiles(str(smiles or ''))
    if mol is None or not mol.GetNumAtoms():
        raise ValueError('Invalid or empty SMILES')
    canonical = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    key = Chem.MolToInchiKey(mol)
    if not key:
        raise ValueError('RDKit could not generate a full InChIKey')
    digest = hashlib.sha256((canonical + '\n' + key).encode()).hexdigest()[:24]
    return {'compound_id': 'chem_' + digest, 'smiles': canonical, 'inchikey': key,
            'identity_status': 'resolved', 'identity_error': ''}


def _list(value):
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if not value:
        return []
    if str(value).lstrip().startswith('['):
        parsed = json.loads(value)
        if not isinstance(parsed, list):
            raise ValueError('Expected a JSON list in candidate provenance')
        return [str(item) for item in parsed if item]
    return [item.strip() for item in str(value).split(';') if item.strip()]


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def merge_candidates(cfg):
    """Merge literature and real model hits into a shared assessment input table."""
    root = Path(cfg['run']['output'])
    source = cfg.get('scientist', {}).get('literature_candidates')
    if not source:
        return {'status': 'skipped', 'reason': 'No literature candidate source configured'}
    model_rows = read_csv(root / 'top100_membership.csv')
    literature_rows = read_csv(source)
    compounds, aliases, alias_identities = {}, [], {}

    def register(row, route):
        original = str(row.get('candidate_id') or row.get('compound_id') or '')
        if not original:
            raise ValueError('Candidate source requires a nonempty compound_id or candidate_id')
        smiles = row.get('smiles', '')
        try:
            identity = canonical_chemical_identity(smiles)
        except ValueError as exc:
            # Failed identities remain inspectable and can fail ADMET/Gate normally.
            digest = hashlib.sha256((original + '\n' + smiles).encode()).hexdigest()[:24]
            identity = {'compound_id': 'unresolved_' + digest, 'smiles': smiles,
                        'inchikey': '', 'identity_status': 'unresolved',
                        'identity_error': str(exc)}
        cid = identity['compound_id']
        identifiers = list(dict.fromkeys([original, *([str(row['compound_id'])]
                          if row.get('compound_id') else []), *_list(row.get('original_ids'))]))
        routes = list(dict.fromkeys([route, *_list(row.get('routes'))]))
        evidence = _list(row.get('evidence_ids'))
        record = {**row, 'origin_route': route, 'original_compound_id': original}
        compound = compounds.setdefault(cid, {**identity, 'name': row.get('name', ''),
                                              'ids': [], 'routes': [], 'evidence': [],
                                              'records': []})
        if not compound['name'] and row.get('name'):
            compound['name'] = row['name']
        compound['ids'] = list(dict.fromkeys(compound['ids'] + identifiers))
        compound['routes'] = list(dict.fromkeys(compound['routes'] + routes))
        compound['evidence'] = list(dict.fromkeys(compound['evidence'] + evidence))
        compound['records'].append(record)
        for identifier in identifiers:
            previous = alias_identities.setdefault(identifier, cid)
            if previous != cid:
                raise ValueError(f'Ambiguous original candidate ID {identifier!r} maps to different structures')
            aliases.append({'original_id': identifier, 'canonical_id': cid,
                            'origin_route': route, 'model': row.get('model', ''),
                            'identity_status': identity['identity_status']})
        return {**row, **identity, 'original_compound_id': original}

    registered_model = [register(row, 'model') for row in model_rows]
    registered_literature = [register(row, 'literature') for row in literature_rows]
    shared = []
    for compound in compounds.values():
        shared.append({key: value for key, value in compound.items()
                       if key not in ('ids', 'routes', 'evidence', 'records')} |
                      {'original_ids': _json(compound['ids']),
                       'source_routes': _json(compound['routes']),
                       'evidence_ids': _json(compound['evidence']),
                       'provenance': _json(compound['records'])})
    lookup = {row['compound_id']: row for row in shared}
    deduped = {}
    for row in registered_model:
        key = row['model'], row['compound_id']
        previous = deduped.get(key)
        if previous is None or int(row['model_rank']) < int(previous['model_rank']):
            deduped[key] = {**row, **lookup[row['compound_id']]}
    membership = sorted(deduped.values(), key=lambda row:
                        (MODELS.index(row['model']), int(row['model_rank']), row['compound_id']))
    lit = {row['compound_id']: {**row, **lookup[row['compound_id']]}
           for row in registered_literature}
    write_csv(root / 'unique_candidates.csv', shared, CHEMICAL_FIELDS)
    write_csv(root / 'top100_membership.csv', membership,
              list(dict.fromkeys(MEMBERSHIP_FIELDS + [key for row in membership for key in row])))
    for model in MODELS:
        rows = [row for row in membership if row['model'] == model]
        write_csv(root / 'screen' / model / 'top100.csv', rows,
                  list(dict.fromkeys(MEMBERSHIP_FIELDS + [key for row in rows for key in row])))
    write_csv(root / 'literature_membership.csv', lit.values(),
              list(dict.fromkeys(CHEMICAL_FIELDS + [key for row in lit.values() for key in row])))
    write_csv(root / 'candidate_aliases.csv', aliases,
              ['original_id', 'canonical_id', 'origin_route', 'model', 'identity_status'])
    summary = {'status': 'completed', 'model_rows_before': len(model_rows),
               'model_memberships': len(membership), 'literature_rows': len(literature_rows),
               'unique_candidates': len(shared),
               'unresolved_identities': sum(row['identity_status'] != 'resolved' for row in shared)}
    write_json(root / 'merge_candidates' / 'summary.json', summary)
    return summary


def remap_prepared_systems(cfg):
    """Retain exact prepared-system identity after alias canonicalization."""
    systems = cfg.get('md', {}).get('prepared_systems', {})
    if not systems or not cfg.get('scientist', {}).get('literature_candidates'):
        return
    aliases = {row['original_id']: row['canonical_id'] for row in
               read_csv(Path(cfg['run']['output']) / 'candidate_aliases.csv')}
    remapped = {}
    for original, system in systems.items():
        canonical = aliases.get(original, original)
        if canonical in remapped and remapped[canonical] != system:
            raise ValueError(f'Different prepared systems map to chemical identity {canonical}')
        remapped[canonical] = system
    cfg['md']['prepared_systems'] = remapped


def top100(cfg):
    root = Path(cfg['run']['output'])
    selected = []
    for model in MODELS:
        rows = read_csv(root / 'screen' / model / 'scores.csv')
        good = [r for r in rows if r['status'] == 'success' and math.isfinite(float(r['raw_score']))]
        good.sort(key=lambda r: (-float(r['raw_score']), r['compound_id']))
        rows = [{**r, 'model': model, 'model_rank': i}
                for i, r in enumerate(good[:int(cfg['run'].get('top_k', 100))], 1)]
        write_csv(root / 'screen' / model / 'top100.csv', rows)
        selected.extend(rows)
    write_csv(root / 'top100_membership.csv', selected)
    unique = {r['compound_id']: {k: r.get(k, '') for k in ('compound_id', 'smiles', 'name', 'inchikey')}
              for r in selected}
    write_csv(root / 'unique_candidates.csv', unique.values(), ['compound_id', 'smiles', 'name', 'inchikey'])


def admet_pass(row, bounds):
    if row.get('admet_status') != 'success':
        return False, 'ADMET prediction failed'
    for endpoint, limits in bounds.items():
        try:
            value = float(row[endpoint])
        except (KeyError, ValueError, TypeError):
            return False, f'Missing ADMET endpoint {endpoint}'
        if not math.isfinite(value):
            return False, f'Nonfinite ADMET endpoint {endpoint}'
        if 'min' in limits and value < limits['min']:
            return False, f'{endpoint} below configured minimum'
        if 'max' in limits and value > limits['max']:
            return False, f'{endpoint} above configured maximum'
    return True, ''


def select(cfg):
    if cfg.get('scientist', {}).get('literature_candidates'):
        return _select_merged(cfg)
    root = Path(cfg['run']['output'])
    admet = {r['compound_id']: r for r in read_csv(root / 'admet/predictions.csv')}
    gate = {r['compound_id']: r for r in read_csv(root / 'gate/predictions.csv')}
    options = cfg['selection']
    allowed = set(options.get('gate_classes', ['A1', 'A2', 'B1', 'B2']))
    allow_unknown = options.get('allow_gate_abstain', True)
    all_rows = []
    for row in read_csv(root / 'top100_membership.csv'):
        cid = row['compound_id']
        a, g = admet.get(cid, {}), gate.get(cid, {})
        row.update(a)
        row.update({k: v for k, v in g.items() if k not in ('smiles', 'name', 'inchikey')})
        ok, reason = admet_pass(a, options.get('admet_bounds', {}))
        label = g.get('gate_class', '')
        tier = 0 if label in allowed else 1
        if g.get('gate_status') != 'success':
            ok, reason = False, 'Gate execution failed or missing'
        elif label not in allowed and not (allow_unknown and label == 'E'):
            ok, reason = False, f'Gate class {label} is excluded'
        row.update(selection_eligible=ok, selection_reason=reason,
                   gate_priority=tier, exploratory=(label == 'E'), md_selected=False)
        all_rows.append(row)
    k = int(options.get('top_k_per_model', 5))
    selected = []
    counts = {}
    for model in MODELS:
        candidates = [r for r in all_rows if r['model'] == model and r['selection_eligible']]
        candidates.sort(key=lambda r: (r['gate_priority'], int(r['model_rank']), r['compound_id']))
        for rank, row in enumerate(candidates[:k], 1):
            row['md_selected'] = True
            selected.append({**row, 'md_selection_rank': rank})
        counts[model] = len(candidates[:k])
        write_csv(root / 'selection' / f'{model}_top5.csv', candidates[:k],
                  list(dict.fromkeys(key for r in all_rows for key in r)) if all_rows else ['compound_id'])
        model_rows = [r for r in all_rows if r['model'] == model]
        write_csv(root / 'selection' / f'{model}_top100_admet_gate.csv', model_rows)
    unique = {}
    for row in selected:
        cid = row['compound_id']
        if cid not in unique:
            unique[cid] = {**row, 'selected_by_models': row['model']}
        else:
            unique[cid]['selected_by_models'] += ';' + row['model']
    write_csv(root / 'selection/md_candidates.csv', unique.values(),
              list(next(iter(unique.values()))) if unique else ['compound_id', 'smiles', 'name', 'selected_by_models'])
    write_csv(root / 'selection/all_candidates.csv', all_rows)
    write_json(root / 'selection/summary.json', {'selected_per_model': counts,
        'unique_md_candidates': len(unique), 'requested_per_model': k,
        'shortfall': {m: k-n for m, n in counts.items() if n < k},
        'allow_gate_abstain': allow_unknown, 'admet_bounds': options.get('admet_bounds', {})})


def _select_merged(cfg):
    root = Path(cfg['run']['output'])
    admet = {row['compound_id']: row for row in read_csv(root / 'admet/predictions.csv')}
    gate = {row['compound_id']: row for row in read_csv(root / 'gate/predictions.csv')}
    options = cfg['selection']
    allowed = set(options.get('gate_classes', ['A1', 'A2', 'B1', 'B2']))
    allow_unknown = bool(options.get('allow_gate_abstain', True))
    k = min(5, max(0, int(options.get('top_k_per_model', 5))))
    budget = int(cfg.get('scientist', {}).get('max_md_jobs', 20))
    if not 0 <= budget <= 20:
        raise ValueError('scientist.max_md_jobs must be between 0 and 20')

    def assess(source):
        row = dict(source)
        cid = row['compound_id']
        a, g = admet.get(cid, {}), gate.get(cid, {})
        # Keep assessment outputs alongside the model's original score, rank and provenance.
        protected = {'model', 'model_rank', 'raw_score', 'status', 'original_compound_id',
                     'original_ids', 'source_routes', 'evidence_ids', 'provenance'}
        row.update({key: value for key, value in a.items() if key not in protected})
        row.update({key: value for key, value in g.items() if key not in protected |
                    {'smiles', 'name', 'inchikey'}})
        ok, reason = admet_pass(a, options.get('admet_bounds', {}))
        reasons = [reason] if reason else []
        label = g.get('gate_class', '')
        failed = a.get('admet_status') != 'success' or g.get('gate_status') != 'success'
        if a.get('admet_error') and not ok:
            reasons.append(a['admet_error'])
        if g.get('gate_status') != 'success':
            ok = False
            reasons.append('Gate execution failed or missing' +
                           (': ' + g['gate_error'] if g.get('gate_error') else ''))
        elif label not in allowed and not (allow_unknown and label == 'E'):
            ok = False
            reasons.append(f'Gate class {label} is excluded')
        exploratory = g.get('gate_status') == 'success' and label == 'E'
        row.update(selection_eligible=ok, selection_reason='; '.join(reasons),
                   gate_priority=0 if label in allowed else 1,
                   exploratory=exploratory, md_selected=False,
                   assessment_state='failed' if failed else 'exploratory' if ok and exploratory
                   else 'eligible' if ok else 'excluded')
        return row

    all_rows = [assess(row) for row in read_csv(root / 'top100_membership.csv')]
    # Deduplicate membership aliases before allocating candidate slots.
    per_model = {}
    for row in all_rows:
        key = row['model'], row['compound_id']
        previous = per_model.get(key)
        if previous is None or int(row['model_rank']) < int(previous['model_rank']):
            per_model[key] = row
    all_rows = list(per_model.values())
    selected, counts = [], {}
    for model in MODELS:
        candidates = [row for row in all_rows if row['model'] == model and row['selection_eligible']]
        candidates.sort(key=lambda row: (row['gate_priority'], int(row['model_rank']), row['compound_id']))
        choices = candidates[:k]
        counts[model] = len(choices)
        for rank, row in enumerate(choices, 1):
            row['md_selection_rank'] = rank
            selected.append(row)

    unique = {}
    for row in selected:
        cid = row['compound_id']
        if cid not in unique:
            if len(unique) >= budget:
                row['md_budget_reason'] = 'Per-run unique MD job budget exhausted'
                continue
            unique[cid] = {**row, 'selected_by_models': row['model'], 'md_selected': True}
        else:
            unique[cid]['selected_by_models'] += ';' + row['model']
        row['md_selected'] = True
    fields = list(dict.fromkeys(MEMBERSHIP_FIELDS + [key for row in all_rows for key in row]))
    for model in MODELS:
        rows = [row for row in all_rows if row['model'] == model]
        choices = [row for row in selected if row['model'] == model]
        write_csv(root / 'selection' / f'{model}_top5.csv', choices, fields)
        write_csv(root / 'selection' / f'{model}_top100_admet_gate.csv', rows, fields)
    write_csv(root / 'selection/md_candidates.csv', unique.values(),
              list(dict.fromkeys(fields + ['selected_by_models'])))

    literature = []
    model_selected = set(unique)
    for source in read_csv(root / 'literature_membership.csv'):
        source = {key: value for key, value in source.items()
                  if key not in {'model', 'model_rank', 'raw_score', 'status', 'md_selection_rank'}}
        row = assess(source)
        row['assessment_route'] = 'literature'
        row['model_route_md_selected'] = row['compound_id'] in model_selected
        row['md_selected'] = False
        row['md_policy'] = 'Literature shortlist records research leads; MD scheduling follows the model route, with overlap reported separately'
        if row.get('decision') == 'hold':
            row['selection_eligible'] = False
            row['assessment_state'] = 'held' if row['assessment_state'] != 'failed' else 'failed'
            row['selection_reason'] += ('; ' if row['selection_reason'] else '') + 'Literature decision is hold'
        literature.append(row)
    literature.sort(key=lambda row: (not row['selection_eligible'], row['gate_priority'], row['compound_id']))
    lit_fields = list(dict.fromkeys(CHEMICAL_FIELDS + [key for row in literature for key in row]))
    write_csv(root / 'selection/literature_shortlist.csv', literature, lit_fields)
    write_csv(root / 'selection/all_candidates.csv', all_rows, fields)
    summary = {'selected_per_model': counts, 'unique_md_candidates': len(unique),
               'requested_per_model': k, 'max_md_jobs': budget,
               'shortfall': {model: k - count for model, count in counts.items() if count < k},
               'allow_gate_abstain': allow_unknown, 'admet_bounds': options.get('admet_bounds', {}),
               'literature_candidates': len(literature),
               'literature_eligible': sum(row['selection_eligible'] for row in literature),
               'literature_md_policy': 'model_route_only'}
    write_json(root / 'selection/summary.json', summary)
    return summary

