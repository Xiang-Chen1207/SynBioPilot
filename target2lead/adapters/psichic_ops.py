"""Inference helpers adapted from the exercised model1 adapters."""
import pickle
import multiprocessing
import concurrent.futures
import time
FOLLOW = ['mol_x', 'clique_x', 'prot_node_aa']


def featurize(smiles):
    from utils.ligand_init import smiles2graph
    try:
        return smiles, pickle.dumps(smiles2graph(smiles), protocol=5), None
    except Exception as exc:
        return smiles, None, f'{type(exc).__name__}: {exc}'


def graph_dictionary(frame, graph_workers):
    items = frame['canonical_isomeric_smiles'].drop_duplicates().tolist()
    if graph_workers > 1:
        with concurrent.futures.ProcessPoolExecutor(max_workers=graph_workers, mp_context=multiprocessing.get_context('spawn')) as pool:
            values = list(pool.map(featurize, items, chunksize=32))
    else:
        values = [featurize(s) for s in items]
    return {s:pickle.loads(g) for s,g,e in values if g is not None}, {s:e for s,g,e in values if g is None}


def make_batch(samples, device):
    from torch_geometric.data import Batch
    return Batch.from_data_list(samples, follow_batch=FOLLOW).to(device)


def forward(model, data):
    return model(mol_x=data.mol_x, mol_x_feat=data.mol_x_feat,
                 bond_x=data.mol_edge_attr, atom_edge_index=data.mol_edge_index,
                 clique_x=data.clique_x, clique_edge_index=data.clique_edge_index,
                 atom2clique_index=data.atom2clique_index, residue_x=data.prot_node_aa,
                 residue_evo_x=data.prot_node_evo, residue_edge_index=data.prot_edge_index,
                 residue_edge_weight=data.prot_edge_weight, mol_batch=data.mol_x_batch,
                 prot_batch=data.prot_node_aa_batch, clique_batch=data.clique_x_batch,
                 save_cluster=False)


def prediction_rows(model, samples, device):
    """Bisect failed batches after releasing exception frames and GPU tensors."""
    import numpy as np
    import torch
    start = time.perf_counter()
    data = reg = cls = mcls = unused = None
    error_code = error_message = None
    try:
        with torch.inference_mode():
            data = make_batch(samples, device)
            reg, cls, mcls, *unused = forward(model, data)
            affinity = reg.reshape(-1).float().cpu().numpy()
            probabilities = torch.softmax(mcls, -1).float().cpu().numpy().reshape(-1, 3)
            if len(affinity) != len(samples) or not np.isfinite(affinity).all() or not np.isfinite(probabilities).all():
                raise ValueError('Model returned missing/nonfinite predictions')
        elapsed = (time.perf_counter()-start)/len(samples)
        return [dict(status='success', raw_score=float(a), affinity_regression=float(a),
                     functional_antagonist_probability=float(p[0]),
                     functional_nonbinder_probability=float(p[1]),
                     functional_agonist_probability=float(p[2]), runtime_s=elapsed,
                     error_code=None, error_message=None) for a, p in zip(affinity, probabilities)]
    except Exception as exc:
        error_code, error_message = type(exc).__name__, str(exc)[:2000]
    # Leave except first, so its traceback no longer retains forward intermediates.
    data = reg = cls = mcls = unused = None
    torch.cuda.empty_cache()
    if len(samples) > 1:
        half = len(samples)//2
        return prediction_rows(model, samples[:half], device) + prediction_rows(model, samples[half:], device)
    return [dict(status='failed', raw_score=None, runtime_s=time.perf_counter()-start,
                 error_code=error_code, error_message=error_message)]
