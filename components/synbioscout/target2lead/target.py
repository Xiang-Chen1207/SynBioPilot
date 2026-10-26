import json
from pathlib import Path
import shutil
from .common import write_json


def prepare(cfg):
    target = cfg['target']
    output = Path(cfg['run']['output']) / 'target'
    output.mkdir(parents=True, exist_ok=True)
    for key, name in [('protein_pdb', 'protein.pdb'), ('sequence_fasta', 'sequence.fasta'),
                      ('reference_ligand_sdf', 'reference.sdf')]:
        source = Path(target[key])
        if not source.is_file():
            raise FileNotFoundError(f'Target input {key}: {source}')
        shutil.copy2(source, output / name)
    fasta = (output / 'sequence.fasta').read_text().splitlines()
    if sum(line.startswith('>') for line in fasta) != 1:
        raise ValueError('Provide a single-sequence FASTA for the selected target')
    sequence = ''.join(line.strip() for line in fasta if not line.startswith('>'))
    if not sequence or set(sequence) - set('ACDEFGHIKLMNPQRSTVWYXUBZO'):
        raise ValueError('Invalid target protein sequence')
    chains = {line[21:22] for line in (output / 'protein.pdb').read_text().splitlines()
              if line.startswith('ATOM')}
    if target.get('chain', 'A') not in chains:
        raise ValueError('Selected chain is absent from the target PDB')
    sdf = (output / 'reference.sdf').read_text().splitlines()
    if sum(line.strip() == '$$$$' for line in sdf) != 1:
        raise ValueError('Reference SDF must contain exactly one molecule')
    if len(sdf) < 5 or 'V2000' not in sdf[3]:
        raise ValueError('Reference ligand must be a positioned, single-molecule V2000 SDF')
    n = int(sdf[3][:3])
    atoms = [[float(line[:10]), float(line[10:20]), float(line[20:30])]
             for line in sdf[4:4+n] if line[31:34].strip() != 'H']
    if not atoms:
        raise ValueError('Reference ligand contains no heavy atom coordinates')
    center = target.get('pocket_center') or [sum(a[i] for a in atoms)/len(atoms) for i in range(3)]
    write_json(output / 'target.json', {'target_id': target['id'], 'chain': target.get('chain', 'A'),
               'sequence_length': len(sequence), 'pocket_center': center,
               'box_size': target.get('box_size', [24, 24, 24])})

