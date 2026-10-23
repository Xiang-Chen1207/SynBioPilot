"""Canonical-isomeric-SMILES union of configured database exports."""
from pathlib import Path
import csv
from rdkit import Chem


def prepare_library(inputs, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    structures, rejected, source_rows = {}, [], []
    for path in inputs:
        with Path(path).open(encoding='utf-8-sig', newline='') as stream:
            for n, row in enumerate(csv.DictReader(stream), 2):
                source = row.get('source_database') or Path(path).stem
                raw = row.get('smiles') or row.get('canonical_isomeric_smiles', '')
                molecule = Chem.MolFromSmiles(raw) if raw else None
                if molecule is None or not molecule.GetNumAtoms():
                    rejected.append({'source_database': source, 'row': n, 'smiles': raw})
                    continue
                smiles = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
                record = structures.setdefault(smiles, {'name': row.get('name', ''), 'sources': set()})
                record['sources'].add(source)
                source_rows.append((smiles, source, row.get('compound_id', str(n)), raw))
    if not structures:
        raise ValueError('No valid molecules in supplied exports')
    ids = {smiles: f'NP{i:09d}' for i, smiles in enumerate(sorted(structures), 1)}
    def write(name, fields, rows):
        with (output/name).open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fields)
            writer.writeheader()
            writer.writerows(rows)
    write('molecules.csv', ['compound_id', 'smiles', 'name', 'inchikey', 'source_database'],
          ({'compound_id': ids[s], 'smiles': s, 'name': structures[s]['name'],
            'inchikey': Chem.MolToInchiKey(Chem.MolFromSmiles(s)),
            'source_database': ';'.join(sorted(structures[s]['sources']))} for s in sorted(structures)))
    write('source_records.csv', ['compound_id', 'source_database', 'source_id', 'source_smiles'],
          ({'compound_id': ids[s], 'source_database': source, 'source_id': ident, 'source_smiles': raw}
           for s, source, ident, raw in source_rows))
    write('rejected.csv', ['source_database', 'row', 'smiles'], rejected)
    print(f'{len(structures)} unique structures; {len(rejected)} rejected rows; output: {output}')
