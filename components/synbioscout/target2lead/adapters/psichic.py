"""PSICHIC XL paired protein/ligand graph inference."""
import json
import os
from pathlib import Path
import sys
from target2lead.common import library_frame, finish_scores


def run(cfg, output):
    import pandas as pd
    import torch
    settings = cfg['psichic']
    sys.path.insert(0, settings['repo'])
    if settings.get('torch_home'):
        os.environ['TORCH_HOME'] = settings['torch_home']
        torch.hub.set_dir(str(Path(settings['torch_home'])/'hub'))
    from models.net import net
    from utils.dataset import ProteinMoleculeDataset
    from utils.protein_init import protein_init
    from .psichic_ops import graph_dictionary, prediction_rows
    device = torch.device(settings.get('device', 'cuda'))
    if device.type == 'cuda':
        torch.cuda.set_device(device)
    torch.manual_seed(int(cfg['run'].get('seed', 42)))
    output.mkdir(parents=True, exist_ok=True)
    lines = (Path(cfg['run']['output'])/'target/sequence.fasta').read_text().splitlines()
    seq = ''.join(s.strip() for s in lines if not s.startswith('>'))
    graph_file = output/'protein_graph.pt'
    if graph_file.exists():
        protein = torch.load(graph_file, map_location='cpu', weights_only=False)
        if list(protein) != [seq]:
            raise ValueError('Cached PSICHIC protein graph does not match target sequence')
    else:
        protein = protein_init([seq])
        torch.save(protein, graph_file)
    weights = Path(settings['weights'])
    modelcfg = json.loads((weights/'config.json').read_text())
    degrees = torch.load(weights/'degree.pt', map_location='cpu', weights_only=False)
    model = net(degrees['ligand_deg'], degrees['protein_deg'], **modelcfg['params'],
                regression_head=True, classification_head=False, multiclassification_head=3, device=device).to(device)
    model.load_state_dict(torch.load(weights/'model.pt', map_location=device, weights_only=False), strict=True)
    model.eval()
    library = library_frame(cfg)
    rows = []
    chunk_size, batch_size = int(settings.get('chunk_size', 2000)), int(settings.get('batch_size', 64))
    for chunk, start in enumerate(range(0, len(library), chunk_size)):
        saved = output/f'chunk_{chunk:06d}.csv'
        if saved.exists():
            rows.extend(pd.read_csv(saved, dtype={'compound_id': str}).to_dict('records'))
            continue
        frame = library.iloc[start:start+chunk_size].copy().reset_index(drop=True)
        frame['canonical_isomeric_smiles'] = frame.smiles
        graphs, failures = graph_dictionary(frame, int(settings.get('graph_workers', 4)))
        good = frame[~frame.smiles.isin(failures)].reset_index(drop=True)
        chunk_rows = [{'compound_id': r.compound_id, 'raw_score': None, 'status': 'failed',
                       'error': failures[r.smiles]} for r in frame.itertuples() if r.smiles in failures]
        if len(good):
            good['Ligand'], good['Protein'] = good.smiles, seq
            dataset = ProteinMoleculeDataset(good, graphs, protein, device='cpu')
            for begin in range(0, len(good), batch_size):
                end = min(begin+batch_size, len(good))
                predictions = prediction_rows(model, [dataset[i] for i in range(begin, end)], device)
                for cid, pred in zip(good.compound_id.iloc[begin:end], predictions):
                    pred['compound_id'] = cid
                    pred['error'] = pred.pop('error_message', '') or ''
                    chunk_rows.append(pred)
        pd.DataFrame(chunk_rows).to_csv(saved, index=False)
        rows.extend(chunk_rows)
        print(f'PSICHIC {min(start+chunk_size, len(library))}/{len(library)}', flush=True)
    finish_scores(pd.DataFrame(rows), cfg, output, 'psichic')

