"""Wiki equations (1)–(5), with explicit incomplete-input handling."""
from pathlib import Path
import numpy as np
import pandas as pd
from target2lead.common import library_frame, write_json

MODELS = ('drugclip', 'sprint', 'pharmaconet', 'psichic')
WEIGHTS = {'BBB_Martins': 25, 'PAMPA_NCATS': 10, 'Solubility_AqSolDB': 10,
           'hERG': 20, 'DILI': 20, 'CYP3A4_Veith': 5,
           'CYP1A2_Veith': 5, 'Pgp_Broccatelli': 5}
NOW = {'A1', 'A2', 'B1', 'B2'}
ROUTES = NOW | {'B3', 'C1', 'C2', 'D1', 'D2'}


def read(path):
    return pd.read_csv(path, dtype={'compound_id': str}, keep_default_na=False)


def unique(frame, label):
    if frame.compound_id.eq('').any() or frame.compound_id.duplicated().any():
        raise ValueError(f'{label}: compound_id must be nonempty and unique')


def valid_scores(frame):
    """Rank all successful finite outputs, before taking any Top-K subset."""
    unique(frame, 'model scores')
    frame = frame.copy()
    frame['raw_score'] = pd.to_numeric(frame.raw_score, errors='coerce')
    frame = frame[frame.status.eq('success') & np.isfinite(frame.raw_score)].copy()
    if frame.empty:
        raise ValueError('No successful finite scores in the reference pool')
    rank = frame.raw_score.rank(method='average', ascending=False)
    frame['percentile'] = 100.0 if len(frame) == 1 else 100 * (len(frame) - rank) / (len(frame) - 1)
    return frame.sort_values(['raw_score', 'compound_id'], ascending=[False, True])


def candidates(cfg):
    root = Path(cfg['run']['output'])
    library = library_frame(cfg)
    # The library is structurally deduplicated BEFORE inference, not after ranks.
    if library.smiles.eq('').any() or library.smiles.duplicated().any():
        raise ValueError('Use a structurally deduplicated, canonical-isomeric-SMILES library; see prepare-library')
    library_ids = set(library.compound_id)
    top_k = int(cfg['run'].get('top_k', 1000))
    if top_k < 1:
        raise ValueError('run.top_k must be positive')
    ranked, selected, summaries = {}, [], {}
    for model in MODELS:
        frame = read(root/'screen'/model/'scores.csv')[['compound_id', 'raw_score', 'status']]
        if set(frame.compound_id) != library_ids:
            raise ValueError(f'{model}: scores must cover the same complete library, including failure rows')
        valid = valid_scores(frame)
        ranked[model] = valid
        top = valid.head(top_k).copy()
        top['model'] = model
        top['rank'] = range(1, len(top) + 1)
        top.to_csv(root/'screen'/model/'top1000.csv', index=False)
        selected.append(top)
        summaries[model] = {'valid_reference_count': len(valid), 'selected': len(top)}
    membership = pd.concat(selected, ignore_index=True)
    membership.to_csv(root/'top1000_membership.csv', index=False)
    result = library[library.compound_id.isin(membership.compound_id)].copy()
    for model, valid in ranked.items():
        lookup = valid.set_index('compound_id')
        result[model + '_raw_score'] = result.compound_id.map(lookup.raw_score)
        result[model + '_percentile'] = result.compound_id.map(lookup.percentile)
        result[model + '_selected'] = result.compound_id.isin(membership.loc[membership.model.eq(model), 'compound_id'])
    result.sort_values('compound_id').to_csv(root/'unique_candidates.csv', index=False)
    detail = {'library_count': len(library), 'models': summaries,
              'selected_positions': len(membership), 'unique_candidates': len(result)}
    write_json(root/'candidate_summary.json', detail)
    return detail


def admet8(frame):
    """Return utilities, score and validity; a missing endpoint is never zero-filled."""
    numeric = pd.DataFrame({key: pd.to_numeric(frame[key], errors='coerce')
                            if key in frame else np.nan for key in WEIGHTS}, index=frame.index)
    valid = np.isfinite(numeric).all(axis=1)
    utilities = numeric.copy()
    for key in WEIGHTS:
        if key == 'Solubility_AqSolDB':
            utilities[key] = ((numeric[key] + 6) / 4).clip(0, 1)
        else:
            valid &= numeric[key].between(0, 1)
            if key not in ('BBB_Martins', 'PAMPA_NCATS'):
                utilities[key] = 1 - numeric[key]
    valid &= frame['admet_status'].eq('success') if 'admet_status' in frame else False
    score = utilities.mul(pd.Series(WEIGHTS)).sum(axis=1, min_count=len(WEIGHTS)).where(valid)
    return utilities, score, valid


def composite(cfg):
    root = Path(cfg['run']['output'])
    base = read(root/'unique_candidates.csv')
    prediction = read(root/'admet/predictions.csv')
    unique(prediction, 'ADMET')
    columns = ['compound_id'] + [k for k in (*WEIGHTS, 'admet_status', 'admet_error') if k in prediction]
    frame = base.merge(prediction[columns], on='compound_id', how='left', validate='one_to_one')
    utilities, score, admet_valid = admet8(frame)
    percentiles = frame[[m + '_percentile' for m in MODELS]].apply(pd.to_numeric, errors='coerce')
    complete_models = np.isfinite(percentiles).all(axis=1)
    frame['consensus_score'] = percentiles.mean(axis=1).where(complete_models)
    frame['admet8_score'] = score
    frame['score_status'] = np.where(complete_models & admet_valid, 'complete', 'pending')
    frame['pending_reason'] = [';'.join(reason for ok, reason in
        [(cm, 'missing_model_score'), (av, 'missing_or_invalid_admet')] if not ok)
        for cm, av in zip(complete_models, admet_valid)]
    frame['composite_score'] = (0.5 * frame.consensus_score + 0.5 * score).where(frame.score_status.eq('complete'))
    for key, weight in WEIGHTS.items():
        frame['admet8_points_' + key] = (utilities[key] * weight).where(admet_valid)
    frame = frame.sort_values(['composite_score', 'compound_id'], ascending=[False, True], na_position='last')
    frame['composite_rank'] = pd.Series(pd.NA, index=frame.index, dtype='Int64')
    complete = frame.score_status.eq('complete')
    frame.loc[complete, 'composite_rank'] = range(1, int(complete.sum()) + 1)
    out = root/'composite'
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out/'all_candidates.csv', index=False)
    frame[complete].to_csv(out/'ranked_candidates.csv', index=False)
    frame[~complete].to_csv(out/'pending_candidates.csv', index=False)
    return {'complete': int(complete.sum()), 'pending': int((~complete).sum())}


def stratify(cfg):
    root = Path(cfg['run']['output'])
    frame = read(root/'composite/all_candidates.csv')
    gates = read(root/'gate/predictions.csv')
    unique(gates, 'ChassisGate')
    gates = gates[['compound_id'] + [k for k in gates if k != 'compound_id' and k not in frame]]
    frame = frame.merge(gates, on='compound_id', how='left', validate='one_to_one')
    fine = frame.get('gate_fine_class', pd.Series('', index=frame.index)).fillna('')
    coarse = frame.get('gate_class', pd.Series('', index=frame.index)).fillna('')
    raw = fine.where(fine.ne(''), coarse)
    status = frame.get('gate_status', pd.Series('', index=frame.index)).fillna('')
    supported = status.eq('success') & raw.isin(ROUTES | {'E', 'E1', 'E2'})
    frame['route_class'] = raw.where(supported, 'E2')
    frame['deliverable_now'] = pd.Series(pd.NA, index=frame.index, dtype='boolean')
    known = supported & raw.isin(ROUTES)
    frame.loc[known, 'deliverable_now'] = raw[known].isin(NOW)
    frame['retained_for_route_followup'] = known & frame.score_status.eq('complete')
    frame['assessed_chassis'] = cfg['gate']['chassis_name']
    frame['l168_production_validated'] = False
    out = root/'stratification'
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out/'all_candidates.csv', index=False)
    frame[frame.retained_for_route_followup].to_csv(out/'route_followup.csv', index=False)
    frame[frame.deliverable_now.fillna(False) & frame.score_status.eq('complete')].to_csv(out/'deliverable_now.csv', index=False)
    summary = {'candidates': len(frame), 'retained_for_route_followup': int(frame.retained_for_route_followup.sum()),
               'deliverable_now': int((frame.deliverable_now.fillna(False) & frame.score_status.eq('complete')).sum()),
               'route_class_counts': {str(k): int(v) for k, v in frame.route_class.value_counts().items()},
               'chassis': cfg['gate']['chassis_name'], 'manual_overrides_applied': False}
    write_json(out/'summary.json', summary)
    return summary
