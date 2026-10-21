"""One GPU pocket model followed by bounded CPU molecule matching."""
import csv
import multiprocessing as mp
from pathlib import Path
import queue
import sys
import time
from target2lead.common import finish_scores, library_frame


def worker(options, incoming, outgoing):
    sys.path.insert(0, str(Path(options['repo'])/'src'))
    from .pharmaconet_ops import initialize_worker, score_molecule
    initialize_worker(options)
    while True:
        item = incoming.get()
        if item is None:
            return
        outgoing.put(score_molecule(item))


def run(cfg, output):
    import pandas as pd
    settings = cfg['pharmaconet']
    sys.path.insert(0, str(Path(settings['repo'])/'src'))
    output.mkdir(parents=True, exist_ok=True)
    pharmacophore = output/'target.pm'
    if not pharmacophore.exists():
        from pmnet.module import PharmacoNet
        model = PharmacoNet(device=settings.get('device', 'cuda'), weight_path=settings['checkpoint'])
        target = Path(cfg['run']['output'])/'target'
        prediction = model.run(str(target/'protein.pdb'), ref_ligand_path=str(target/'reference.sdf'))
        if not prediction.nodes:
            raise ValueError('No pharmacophore nodes for target')
        prediction.save(str(pharmacophore))
        del prediction, model
        import torch
        torch.cuda.empty_cache()
    library = library_frame(cfg)
    partial = output/'molecule_scores.csv'
    fields = ['compound_id', 'raw_score', 'status', 'error']
    previous = pd.read_csv(partial, dtype={'compound_id': str}) if partial.exists() else pd.DataFrame(columns=fields)
    completed = set(previous.compound_id)
    jobs = iter((i, r.compound_id, r.smiles, r.compound_id)
                for i, r in enumerate(library.itertuples()) if r.compound_id not in completed)
    options = {**settings, 'pharmacophore': str(pharmacophore),
               'random_seed': int(cfg['run'].get('seed', 42)),
               'conformer_count': int(settings.get('conformer_count', 10))}
    context = mp.get_context('spawn')
    timeout = float(settings.get('molecule_timeout_seconds', 120))
    def start_slot():
        incoming, outgoing = context.Queue(), context.Queue()
        process = context.Process(target=worker, args=(options, incoming, outgoing))
        process.start()
        return {'process': process, 'in': incoming, 'out': outgoing, 'job': None, 'started': None}
    slots = [start_slot() for _ in range(int(settings.get('cpu_workers', 8)))]
    exhausted = False
    try:
        with partial.open('a', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            if not stream.tell():
                writer.writeheader()
            while not exhausted or any(s['job'] is not None for s in slots):
                for index, slot in enumerate(slots):
                    if slot['job'] is None and not exhausted:
                        try:
                            slot['job'] = next(jobs)
                        except StopIteration:
                            exhausted = True
                            continue
                        slot['started'] = time.monotonic()
                        slot['in'].put(slot['job'])
                    if slot['job'] is None:
                        continue
                    try:
                        row = slot['out'].get_nowait()
                        writer.writerow({'compound_id': row['compound_id'], 'raw_score': row['raw_score'],
                                         'status': row['status'], 'error': row.get('error_message') or ''})
                        stream.flush()
                        slot['job'] = None
                    except queue.Empty:
                        dead = not slot['process'].is_alive()
                        if dead or time.monotonic()-slot['started'] > timeout:
                            writer.writerow({'compound_id': slot['job'][1], 'raw_score': '', 'status': 'failed',
                                             'error': 'Worker exited' if dead else f'Matching timeout ({timeout}s)'})
                            stream.flush()
                            slot['process'].terminate()
                            slot['process'].join()
                            slot['in'].close(); slot['out'].close()
                            slots[index] = start_slot()
                time.sleep(.05)
    finally:
        for slot in slots:
            slot['process'].terminate()
            slot['process'].join()
            slot['in'].close(); slot['out'].close()
    finish_scores(pd.read_csv(partial, dtype={'compound_id': str}), cfg, output, 'pharmaconet')
