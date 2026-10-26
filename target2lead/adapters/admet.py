"""ADMET-AI predictions, one prediction per unique SMILES."""
from pathlib import Path
from target2lead.common import write_json


def run(cfg, output):
    import numpy as np
    import pandas as pd
    from rdkit import Chem
    from admet_ai import ADMETModel
    output.mkdir(parents=True, exist_ok=True)
    inputs = pd.read_csv(Path(cfg['run']['output'])/'unique_candidates.csv', dtype=str).fillna('')
    model = ADMETModel(include_physchem=True, num_workers=int(cfg['admet'].get('num_workers', 0)))
    valid, errors = [], {}
    for smi in inputs.smiles.drop_duplicates():
        mol = Chem.MolFromSmiles(smi)
        if mol is None or not mol.GetNumAtoms():
            errors[smi] = 'Invalid SMILES'
        else:
            valid.append(smi)
    predictions = []
    # Bisect failing batches to preserve the other selected molecules.
    def predict(smiles):
        if not smiles:
            return
        try:
            frame = model.predict(smiles=smiles)
            if not isinstance(frame, pd.DataFrame) or frame.index.duplicated().any():
                raise ValueError('Unexpected ADMET-AI output table')
            predictions.append(frame)
            return
        except Exception as exc:
            message = f'{type(exc).__name__}: {exc}'
        if len(smiles) == 1:
            errors[smiles[0]] = message
        else:
            mid = len(smiles)//2
            predict(smiles[:mid]); predict(smiles[mid:])
    predict(valid)
    predicted = pd.concat(predictions) if predictions else pd.DataFrame()
    predicted.index.name = 'smiles'
    columns = list(predicted.columns)
    result = inputs.merge(predicted.reset_index(), on='smiles', how='left', validate='many_to_one')
    result['admet_status'], result['admet_error'] = 'success', ''
    for i, row in result.iterrows():
        if row.smiles in errors or not columns or not np.isfinite(row[columns].to_numpy(dtype=float)).all():
            result.loc[i, 'admet_status'] = 'failed'
            result.loc[i, 'admet_error'] = errors.get(row.smiles, 'Missing or nonfinite prediction')
    result.to_csv(output/'predictions.csv', index=False)
    success = int(result.admet_status.eq('success').sum())
    write_json(output/'summary.json', {'molecules': len(result), 'success': success,
               'failed': len(result)-success, 'endpoints': columns})
    if len(result) and not success:
        raise RuntimeError('ADMET failed for all selected molecules')
