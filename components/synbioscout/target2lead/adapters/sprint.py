"""Official Morgan/SaProt SPRINT, with target-independent ligand cache reuse."""
import os
from pathlib import Path
import sys
from target2lead.common import command, finish_scores, library_frame


def run(cfg, output):
    import numpy as np
    import pandas as pd
    import torch
    import torch.nn.functional as F
    settings = cfg['sprint']
    repo = Path(settings['repo'])
    sys.path.insert(0, str(repo))
    from ultrafast.model import DrugTargetCoembeddingLightning
    from ultrafast.saprot_utils import load_esm_saprot
    device = torch.device(settings.get('device', 'cuda'))
    if device.type == 'cuda':
        torch.cuda.set_device(device)
    output.mkdir(parents=True, exist_ok=True)
    target = Path(cfg['run']['output']) / 'target'
    sequences = output / 'target_saprot.csv'
    if not sequences.exists():
        env = os.environ.copy()
        env['PATH'] = str(Path(settings['foldseek']).parent) + os.pathsep + env.get('PATH', '')
        command([sys.executable, repo/'utils/structure_to_saprot.py', '-I', target/'protein.pdb',
                 '--chain', cfg['target'].get('chain', 'A'), '-O', sequences, '--no-plddt-mask'],
                output, output/'structure_to_saprot.log', env=env)
    table = pd.read_csv(sequences)
    if len(table) != 1:
        raise ValueError('Expected one SaProt target sequence')
    seq = str(table.iloc[0]['Target Sequence'])
    if len(seq) % 2 or not 1 <= len(seq)//2 <= 1022:
        raise ValueError('SaProt sequence exceeds checkpoint context; choose a target domain explicitly')
    encoder, alphabet = load_esm_saprot(settings['saprot_checkpoint'])
    encoder.eval().to(device)
    _, _, tokens = alphabet.get_batch_converter()([(cfg['target']['id'], seq)])
    with torch.inference_mode():
        reps = encoder(tokens.to(device), repr_layers=[33], return_contacts=False)['representations'][33]
        reps = reps[:, 1:-1, :].clone()
    del encoder
    if device.type == 'cuda':
        torch.cuda.empty_cache()
    model = DrugTargetCoembeddingLightning.load_from_checkpoint(settings['checkpoint'], map_location='cpu', strict=True)
    model.eval().to(device)
    if model.args.drug_featurizer != 'MorganFeaturizer' or model.args.target_featurizer != 'SaProtFeaturizer':
        raise ValueError('This adapter requires the Morgan/SaProt checkpoint')
    with torch.inference_mode():
        target_vector = F.normalize(model.embed(reps, sample_type='target').reshape(1, -1), dim=1)
    if not torch.isfinite(target_vector).all():
        raise ValueError('Nonfinite SPRINT target embedding')
    target_vector = target_vector.cpu().numpy()[0]
    np.save(output/'target.npy', target_vector)
    del reps
    rows = []
    caches = settings.get('embedding_caches', [])
    if caches:
        for cache in caches:
            files = sorted(Path(cache['embeddings']).glob('shard_*.npy'))
            if not files:
                raise FileNotFoundError(f'No SPRINT shards at {cache["embeddings"]}')
            for path in files:
                ids = pd.read_parquet(Path(cache['mappings'])/(path.stem+'.parquet'), columns=['compound_id'])
                ids['compound_id'] = ids.compound_id.astype(str)
                vectors = np.load(path, mmap_mode='r')
                if vectors.shape != (len(ids), len(target_vector)):
                    raise ValueError(f'Embedding/mapping shape mismatch: {path}')
                scores = np.full(len(ids), np.nan)
                for start in range(0, len(ids), 65536):
                    v = np.asarray(vectors[start:start+65536])
                    norm = np.linalg.norm(v, axis=1)
                    valid = np.isfinite(v).all(axis=1) & (norm > 0)
                    s = np.full(len(v), np.nan)
                    s[valid] = (v[valid] @ target_vector) / norm[valid]
                    scores[start:start+len(v)] = s
                rows.extend({'compound_id': cid, 'raw_score': score,
                             'status': 'success' if np.isfinite(score) else 'failed',
                             'error': '' if np.isfinite(score) else 'Invalid cached embedding'}
                            for cid, score in zip(ids.compound_id, scores))
    else:
        from rdkit import Chem, DataStructs
        from rdkit.Chem import rdFingerprintGenerator
        generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
        library = library_frame(cfg)
        cache_dir = output/'embeddings'
        cache_dir.mkdir(exist_ok=True)
        chunk_size = int(settings.get('batch_size', 4096))
        for chunk, start in enumerate(range(0, len(library), chunk_size)):
            frame = library.iloc[start:start+chunk_size]
            vectors = np.full((len(frame), len(target_vector)), np.nan, dtype=np.float32)
            valid, fps = [], []
            for j, smi in enumerate(frame.smiles):
                mol = Chem.MolFromSmiles(smi)
                if mol is not None and mol.GetNumAtoms():
                    mol = Chem.MolFromSmiles(Chem.MolToSmiles(mol, isomericSmiles=True))
                    fp = np.zeros(2048, dtype=np.float32)
                    DataStructs.ConvertToNumpyArray(generator.GetFingerprint(mol), fp)
                    valid.append(j)
                    fps.append(fp)
            if valid:
                with torch.inference_mode():
                    vectors[valid] = model.embed(torch.tensor(np.stack(fps), device=device), sample_type='drug').cpu().numpy()
            np.save(cache_dir/f'shard_{chunk:06d}.npy', vectors)
            frame[['compound_id']].to_parquet(cache_dir/f'shard_{chunk:06d}.parquet', index=False)
            norm = np.linalg.norm(vectors, axis=1)
            scores = (vectors @ target_vector) / np.where(norm > 0, norm, np.nan)
            rows.extend({'compound_id': cid, 'raw_score': score,
                         'status': 'success' if np.isfinite(score) else 'failed',
                         'error': '' if np.isfinite(score) else 'Invalid structure or model output'}
                        for cid, score in zip(frame.compound_id, scores))
    finish_scores(pd.DataFrame(rows), cfg, output, 'sprint')

