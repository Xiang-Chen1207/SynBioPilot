"""Six-fold official DrugCLIP; never reuse old target-dependent scores."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
from pathlib import Path
import sys
from target2lead.common import finish_scores, library_frame, write_json
from .drugclip_ops import encode, infer_batch, write_lmdb


def conformer(item):
    cid, smi, seed, vocabulary = item
    from rdkit import Chem
    from rdkit.Chem import AllChem
    import numpy as np
    try:
        mol = Chem.MolFromSmiles(smi)
        if mol is None or not mol.GetNumHeavyAtoms():
            raise ValueError('Invalid or empty molecule')
        if len(Chem.GetMolFrags(mol)) != 1:
            raise ValueError('Multicomponent structure: no silent fragment stripping')
        if mol.GetNumHeavyAtoms() > 511:
            raise ValueError('More than 511 heavy atoms: no silent truncation')
        if {a.GetSymbol() for a in mol.GetAtoms() if a.GetAtomicNum() != 1} - set(vocabulary):
            raise ValueError('Atom outside checkpoint dictionary')
        mol = Chem.AddHs(mol)
        params = AllChem.ETKDGv3()
        params.randomSeed, params.numThreads, params.maxIterations = seed, 1, 1000
        if AllChem.EmbedMolecule(mol, params) < 0:
            raise ValueError('ETKDG conformer generation failed')
        if AllChem.MMFFHasAllMoleculeParams(mol):
            AllChem.MMFFOptimizeMolecule(mol, mmffVariant='MMFF94s', maxIters=200)
        mol = Chem.RemoveHs(mol)
        coords = np.asarray(mol.GetConformer().GetPositions(), dtype=np.float32)
        return {'atoms': [a.GetSymbol() for a in mol.GetAtoms()], 'coordinates': [coords],
                'smi': smi, 'IDs': cid, 'name': cid}, ''
    except Exception as exc:
        return None, f'{type(exc).__name__}: {exc}'


def run(cfg, output):
    import numpy as np
    import pandas as pd
    import torch
    from unicore import checkpoint_utils
    settings = cfg['drugclip']
    sys.path[:0] = [settings['repo'], settings['screen_repo']]
    import unimol  # register the official architecture
    from unimol.tasks.drugclip import DrugCLIP
    from pocket_from_sdf import process_one_pair
    torch.cuda.set_device(int(settings.get('device_index', 0)))
    seed = int(cfg['run'].get('seed', 42))
    torch.manual_seed(seed)
    output.mkdir(parents=True, exist_ok=True)
    args = argparse.Namespace(data=str(Path(settings['repo'])/'dict'), seed=seed,
        max_seq_len=512, max_pocket_atoms=511, arch='drugclip',
        finetune_mol_model=None, finetune_pocket_model=None)
    task = DrugCLIP.setup_task(args)
    model = task.build_model(args).cuda().eval()
    target = Path(cfg['run']['output'])/'target'
    pockets = process_one_pair(str(target/'protein.pdb'), str(target/'reference.sdf'))
    if len(pockets) != 1 or not pockets[0]['pocket_atoms']:
        raise ValueError('Reference ligand must define one nonempty binding pocket')
    pockets[0]['pocket'] = cfg['target']['id']
    write_lmdb(output/'pocket.lmdb', pockets)
    pocket_ds = task.load_pockets_dataset(str(output/'pocket.lmdb'))
    pocket_batch = pocket_ds.collater([pocket_ds[0]])['net_input']
    library = library_frame(cfg)
    chunks = []
    caches = settings.get('embedding_caches', [])
    errors = []
    if caches:
        for cache in caches:
            mappings = sorted(Path(cache['mappings']).glob('*.mapping.parquet'))
            mappings = [p for p in mappings if p.stem != 'controls.mapping']
            if not mappings:
                raise FileNotFoundError(f'No DrugCLIP mappings in {cache["mappings"]}')
            for path in mappings:
                name = path.name.removesuffix('.mapping.parquet')
                embeddings = Path(cache['embeddings'])/name
                if not embeddings.is_dir():
                    continue
                mapping = pd.read_parquet(path)
                encoded = mapping[mapping.lmdb_index.notna()].sort_values('lmdb_index')
                if not len(encoded):
                    continue
                if encoded.lmdb_index.astype(int).tolist() != list(range(len(encoded))):
                    raise ValueError(f'Non-contiguous DrugCLIP embedding indices: {path}')
                chunks.append({'ids': encoded.compound_id.astype(str).tolist(), 'vectors': embeddings})
        if not chunks:
            raise ValueError('No DrugCLIP embedding shards matched their mapping files')
    else:
        size = int(settings.get('chunk_size', 10000))
        dictionaries = Path(settings['repo'])/'dict/dict_mol.txt'
        vocabulary = [line.split()[0] for line in dictionaries.read_text().splitlines() if line.strip()]
        context = multiprocessing.get_context('spawn')
        with ProcessPoolExecutor(max_workers=int(settings.get('cpu_workers', 8)), mp_context=context) as pool:
            for chunk, start in enumerate(range(0, len(library), size)):
                frame = library.iloc[start:start+size]
                values = pool.map(conformer, [(r.compound_id, r.smiles, seed, vocabulary)
                                             for r in frame.itertuples()], chunksize=16)
                records = []
                for row, (record, error) in zip(frame.itertuples(), values):
                    if record is None:
                        errors.append({'compound_id': row.compound_id, 'raw_score': np.nan,
                                       'status': 'failed', 'error': error})
                    else:
                        records.append(record)
                if not records:
                    continue
                directory = output/'embeddings'/f'shard_{chunk:05d}'
                directory.mkdir(parents=True, exist_ok=True)
                lmdb = directory/'molecules.lmdb'
                write_lmdb(lmdb, records)
                ids = [r['IDs'] for r in records]
                pd.DataFrame({'compound_id': ids, 'lmdb_index': range(len(ids))}).to_parquet(
                    directory.with_suffix('.mapping.parquet'), index=False)
                chunks.append({'ids': ids, 'vectors': directory, 'lmdb': lmdb})
    # Cache mode only holds six target vectors and score columns; ligand vectors
    # are memory-mapped. Fresh mode shares the same scoring and calibration.
    results = [np.full((len(c['ids']), 6), np.nan, dtype=np.float32) for c in chunks]
    for fold in range(6):
        state = checkpoint_utils.load_checkpoint_to_cpu(str(Path(settings['checkpoint_dir'])/f'fold_{fold}.pt'))
        mismatch = model.load_state_dict(state['model'], strict=False)
        if any(k.startswith(('mol_model.', 'pocket_model.', 'mol_project.', 'pocket_project.'))
               for k in mismatch.missing_keys):
            raise RuntimeError('Checkpoint is missing required DrugCLIP parameters')
        del state
        with torch.inference_mode():
            pocket = encode(model, pocket_batch, pocket=True)
            pv = pocket.cpu().numpy()[0]
            if not np.isfinite(pv).all():
                raise ValueError('Nonfinite pocket embedding')
            for chunk, scores in zip(chunks, results):
                file = chunk['vectors']/f'fold_{fold}.npy'
                if 'lmdb' not in chunk:
                    vectors = np.load(file, mmap_mode='r')
                    if vectors.shape != (len(chunk['ids']), len(pv)):
                        raise ValueError(f'Embedding/mapping dimension mismatch: {file}')
                    for start in range(0, len(vectors), 65536):
                        v = np.asarray(vectors[start:start+65536])
                        values = v @ pv
                        values[~np.isfinite(v).all(axis=1)] = np.nan
                        scores[start:start+len(v), fold] = values
                else:
                    dataset = task.load_mols_dataset_dtwg(str(chunk['lmdb']), 'atoms', 'coordinates', dataset_type=1)
                    vectors = np.full((len(chunk['ids']), 128), np.nan, dtype=np.float32)
                    failures = {}
                    for start in range(0, len(vectors), int(settings.get('batch_size', 128))):
                        ids = list(range(start, min(start+int(settings.get('batch_size', 128)), len(vectors))))
                        infer_batch(model, dataset, ids, pocket, vectors, scores[:, fold], failures)
                    np.save(file, vectors)
                    write_json(chunk['vectors']/f'fold_{fold}_errors.json', failures)
        print(f'DrugCLIP fold {fold+1}/6 complete', flush=True)
    rows = list(errors)
    for chunk, scores in zip(chunks, results):
        for cid, vector in zip(chunk['ids'], scores):
            ok = np.isfinite(vector).all()
            rows.append({'compound_id': cid, 'raw_score': float(vector.mean()) if ok else np.nan,
                         'status': 'success' if ok else 'failed',
                         'error': '' if ok else 'Missing or invalid fold embedding',
                         'mean_cosine_similarity': float(vector.mean()) if ok else np.nan})
    frame = pd.DataFrame(rows)
    frame = frame[frame.compound_id.isin(set(library.compound_id))]
    frame['_success'] = frame.status.eq('success')
    frame = frame.sort_values('_success', kind='stable').drop_duplicates('compound_id', keep='last').drop(columns='_success')
    values = frame.loc[frame.status.eq('success'), 'raw_score'].to_numpy()
    if len(values):
        median = float(np.median(values))
        mad = float(np.median(np.abs(values-median)))
        frame.loc[frame.status.eq('success'), 'raw_score'] = .6745*(values-median)/(mad+1e-6)
        write_json(output/'calibration.json', {'median': median, 'mad': mad, 'success_count': len(values)})
    finish_scores(frame, cfg, output, 'drugclip')

