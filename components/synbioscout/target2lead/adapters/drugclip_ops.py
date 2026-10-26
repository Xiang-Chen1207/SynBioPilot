"""Inference helpers adapted from the exercised model1 adapters."""
from pathlib import Path
import pickle


def write_lmdb(path, records):
    import lmdb
    path = Path(path)
    tmp = path.with_suffix('.partial.lmdb')
    if tmp.exists():
        tmp.unlink()
    env = lmdb.open(str(tmp), subdir=False, map_size=64 * 1024 ** 3, lock=False)
    with env.begin(write=True) as txn:
        for i, row in enumerate(records):
            # Official Uni-Core iterates cursor keys lexicographically, not as integers.
            txn.put(f'{i:012d}'.encode('ascii'), pickle.dumps(row, protocol=4))
    env.sync()
    env.close()
    tmp.replace(path)


def encode(model, net_input, pocket=False):
    """Exact official normalized encoder/projector forward from drugclip.py."""
    import torch
    prefix = 'pocket' if pocket else 'mol'
    module = model.pocket_model if pocket else model.mol_model
    project = model.pocket_project if pocket else model.mol_project
    tokens = net_input[prefix + '_src_tokens'].cuda(non_blocking=True)
    dist = net_input[prefix + '_src_distance'].cuda(non_blocking=True)
    edges = net_input[prefix + '_src_edge_type'].cuda(non_blocking=True)
    if next(module.parameters()).dtype == torch.float16:
        dist = dist.half()
    n = dist.shape[-1]
    bias = module.gbf_proj(module.gbf(dist, edges)).permute(0, 3, 1, 2).contiguous().view(-1, n, n)
    output = module.encoder(module.embed_tokens(tokens), padding_mask=tokens.eq(module.padding_idx), attn_mask=bias)
    z = project(output[0][:, 0, :])
    return (z / z.norm(dim=-1, keepdim=True)).float()


def infer_batch(model, dataset, ids, pocket, embeddings, scores, failures, sample=None):
    import numpy as np
    import torch
    z = values = None
    try:
        if sample is None:
            sample = dataset.collater([dataset[i] for i in ids])['net_input']
        z = encode(model, sample)
        values = (z @ pocket.T)[:, 0]
        a, s = z.cpu().numpy(), values.cpu().numpy()
        good = np.isfinite(a).all(axis=1) & np.isfinite(s)
        for pos, i in enumerate(ids):
            if good[pos]:
                embeddings[i] = a[pos]
                scores[i] = s[pos]
            else:
                failures[i] = 'Nonfinite official encoder output'
        return
    except (RuntimeError, ValueError, AssertionError, KeyError, IndexError) as exc:
        error_message = f'{type(exc).__name__}: {exc}'[:1500]
        out_of_memory = isinstance(exc, torch.cuda.OutOfMemoryError)
    # Leave the exception block first: its traceback owns the failed encoder's
    # GPU tensors. Retrying from inside except retains those allocations.
    sample = z = values = None
    if out_of_memory:
        torch.cuda.empty_cache()
    if len(ids) > 1:
        mid = len(ids)//2
        infer_batch(model, dataset, ids[:mid], pocket, embeddings, scores, failures)
        infer_batch(model, dataset, ids[mid:], pocket, embeddings, scores, failures)
    else:
        failures[ids[0]] = error_message

