"""Original ChassisGate fusion with per-compound resumable outputs."""
import json
from pathlib import Path
import sys
from urllib.parse import quote
from target2lead.common import ROOT, read_csv, write_csv, write_json
from target2lead.llm import LiteratureClient


def resolve_name(candidate, cache):
    if candidate.get('name'):
        return candidate['name'], 'library'
    import requests
    key = candidate['inchikey']
    if not key:
        return '', 'unresolved'
    file = cache/(key+'.json')
    if file.exists():
        return json.loads(file.read_text()).get('name', ''), 'pubchem_cache'
    try:
        result = requests.get('https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/'+quote(key, safe='')+'/synonyms/JSON', timeout=20)
        result.raise_for_status()
        synonyms = result.json()['InformationList']['Information'][0]['Synonym']
        name = next(s for s in synonyms if s and not s.isdigit())
        write_json(file, {'name': name})
        return name, 'pubchem'
    except Exception:
        return '', 'unresolved'


def run(cfg, output):
    candidates = read_csv(Path(cfg['run']['output'])/'composite/ranked_candidates.csv')
    output.mkdir(parents=True, exist_ok=True)
    if not candidates:
        write_csv(output/'predictions.csv', [], fields=['compound_id', 'gate_status', 'gate_class', 'gate_fine_class', 'gate_error'])
        write_json(output/'summary.json', {'molecules': 0, 'success': 0, 'failed': 0})
        return
    sys.path.insert(0, str(ROOT/'vendor/chassisgate'))
    from rdkit import Chem
    from deliverability.gem import GemProvider
    from deliverability.idmap import KeggIdMap
    from deliverability.kegg import KeggClient
    from deliverability.integrated import assess_integrated
    from deliverability.schema import Candidate, Chassis
    from deliverability.literature import LiteratureAssessment
    settings = cfg['gate']
    output.mkdir(parents=True, exist_ok=True)
    for sub in ['cache/kegg', 'cache/pubmed', 'cache/names', 'pairs', 'queue']:
        (output/sub).mkdir(parents=True, exist_ok=True)
    # A stopped bridge from an earlier failed stage must not exit immediately.
    (output/'queue/DONE').unlink(missing_ok=True)
    client = LiteratureClient(settings, output/('queue' if settings.get('backend') == 'file_queue' else 'codex'))
    provider = GemProvider(Path(settings['models_dir']))
    chassis = Chassis(id=settings['chassis_id'], name=settings['chassis_name'])
    if provider.load(chassis.id) is None:
        raise RuntimeError(f'Cannot load configured chassis GEM: {chassis.id}')
    mapping = KeggIdMap(output/'cache/kegg/inchikey_to_kegg.json')
    kegg = KeggClient(output/'cache/kegg/rest', id_resolver=mapping.kegg_for_inchikey)
    rows, traces = [], []
    for index, row in enumerate(candidates):
        pair_file = output/'pairs'/f'{index:05d}.json'
        if pair_file.exists():
            saved = json.loads(pair_file.read_text())
            if saved['row']['compound_id'] != row['compound_id']:
                raise ValueError('Changed candidate order; use a new run directory')
            if saved['row']['gate_status'] == 'success':
                rows.append(saved['row']); traces.append(saved.get('trace'))
                continue
        result = {'compound_id': row['compound_id'], 'gate_status': 'failed',
                  'gate_class': '', 'gate_fine_class': '', 'gate_error': '',
                  'literature_status': '', 'genome_state': '', 'fba_state': ''}
        trace = None
        try:
            mol = Chem.MolFromSmiles(row['smiles'])
            if mol is None:
                raise ValueError('Invalid SMILES')
            row['inchikey'] = row.get('inchikey') or Chem.MolToInchiKey(mol)
            name, source = resolve_name(row, output/'cache/names')
            result['resolved_name'], result['name_source'] = name, source
            pid = row['compound_id']+'__'+chassis.id
            # A missing name is not a negative literature search result.
            no_name = LiteratureAssessment(pair_id=pid, status='retrieval_error', retrieved_count=0,
                grounded_runs=0, p_raw=None, decision='abstain', semantic_source='',
                precursor_available='', evidence_pmids='') if not name else None
            assessment = assess_integrated(Candidate(id=row['compound_id'], name=name or row['smiles'],
                smiles=row['smiles'], inchikey=row['inchikey']), chassis, pair_id=pid,
                literature_client=client, literature_result=no_name,
                literature_k=int(settings.get('literature_k', 3)),
                literature_cache_dir=output/'cache/pubmed', provider=provider,
                kegg=kegg, organism_code=settings['kegg_organism_code'])
            trace = assessment.model_dump(mode='json')
            fused = trace['fusion']['gate']
            result.update(gate_status='success', gate_class=fused['pred_10class'],
                gate_fine_class=fused['fine_class'], literature_status=trace['literature']['status'],
                genome_state=trace['genome']['state'], fba_state=trace['fba']['state'],
                gate_rationale=fused['rationale'])
        except Exception as exc:
            result['gate_error'] = f'{type(exc).__name__}: {exc}'
        write_json(pair_file, {'row': result, 'trace': trace})
        rows.append(result); traces.append(trace)
        print(f'Gate {index+1}: {row["compound_id"]} {result["gate_status"]} {result["gate_class"]}', flush=True)
    write_csv(output/'predictions.csv', rows)
    with (output/'integrated_trace.jsonl').open('w') as f:
        for trace in traces:
            if trace is not None:
                f.write(json.dumps(trace, ensure_ascii=False)+'\n')
    (output/'queue/DONE').write_text('complete\n')
    success = sum(r['gate_status'] == 'success' for r in rows)
    write_json(output/'summary.json', {'molecules': len(rows), 'success': success, 'failed': len(rows)-success})
    if rows and not success:
        raise RuntimeError('Gate execution failed for every candidate; inspect predictions.csv')
