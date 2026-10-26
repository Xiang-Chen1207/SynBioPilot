import json
from pathlib import Path

import pytest

from scientist.integration import (
    collect_compute_results,
    create_compute_config,
    write_literature_candidates,
)
from scientist.models import CandidateRecord, TargetPackage
from target2lead.cli import BASE_STAGES, run, stages_for_config
from target2lead.common import MODELS, load_config, read_csv, write_csv, write_json
from target2lead.selection import canonical_chemical_identity, merge_candidates, select


@pytest.fixture
def compute(tmp_path):
    target = {}
    for key, name in [('protein_pdb', 'protein.pdb'), ('sequence_fasta', 'sequence.fasta'),
                      ('reference_ligand_sdf', 'reference.sdf')]:
        path = tmp_path / name
        path.write_text('local fixture only\n')
        target[key] = str(path)
    library = tmp_path / 'library.csv'
    write_csv(library, [{'compound_id': 'library-ethanol', 'smiles': 'CCO', 'name': 'ethanol'}])
    cfg = {'run': {'output': str(tmp_path / 'run'), 'top_k': 100},
           'target': {'id': 'fixture-target', 'chain': 'A', **target},
           'library': {'molecules': str(library)},
           'selection': {'top_k_per_model': 5, 'allow_gate_abstain': True, 'admet_bounds': {}},
           'gate': {'models_dir': './gems', 'chassis_id': 'lplantarum',
                    'chassis_name': 'Lactiplantibacillus plantarum WCFS1', 'kegg_organism_code': 'lpl'},
           'admet': {}, 'md': {'prepared_systems': {}, 'temperature_k': 300, 'salt_molar': 0.15},
           **{model: {'repo': './external/' + model, 'checkpoint': './weights/' + model}
              for model in MODELS}}
    base = tmp_path / 'base.json'
    write_json(base, cfg)
    package = TargetPackage(package_id='fixture-package', status='ready', chain='B', **target)
    return cfg, base, package


def model_row(identifier, smiles, rank, model='drugclip'):
    return {'compound_id': identifier, 'smiles': smiles, 'name': identifier,
            'inchikey': '', 'model': model, 'model_rank': rank,
            'raw_score': 100 - rank, 'status': 'success'}


def merge_fixture(cfg, source_rows, model_rows):
    root = Path(cfg['run']['output'])
    root.mkdir(parents=True, exist_ok=True)
    source = root / 'source.csv'
    write_csv(source, source_rows, ['compound_id', 'name', 'smiles', 'evidence_ids', 'decision'])
    cfg['scientist'] = {'literature_candidates': str(source), 'max_md_jobs': 20}
    write_csv(root / 'top100_membership.csv', model_rows,
              ['compound_id', 'smiles', 'name', 'inchikey', 'model', 'model_rank', 'raw_score', 'status'])
    merge_candidates(cfg)
    return root


def write_assessments(root, classes=None):
    rows = read_csv(root / 'unique_candidates.csv')
    write_csv(root / 'admet/predictions.csv',
              [{'compound_id': row['compound_id'], 'admet_status': 'success'} for row in rows],
              ['compound_id', 'admet_status'])
    write_csv(root / 'gate/predictions.csv',
              [{'compound_id': row['compound_id'], 'gate_status': 'success',
                'gate_class': (classes or {}).get(row['name'], 'A1')} for row in rows],
              ['compound_id', 'gate_status', 'gate_class'])


def test_aliases_collapse_same_model_and_keep_real_scores(compute):
    cfg, _, _ = compute
    rows = [model_row('library-ethanol', 'CCO', 1), model_row('ethanol-alias', 'OCC', 2),
            model_row('sprint-ethanol', 'CCO', 4, 'sprint'), model_row('other', 'CCC', 3)]
    root = merge_fixture(cfg, [{'compound_id': 'lit-ethanol', 'name': 'ethanol paper hit',
                               'smiles': 'OCC', 'evidence_ids': '["E1"]'}], rows)
    assert len(read_csv(root / 'unique_candidates.csv')) == 2
    membership = read_csv(root / 'top100_membership.csv')
    ethanol = canonical_chemical_identity('CCO')['compound_id']
    assert [(row['model'], row['model_rank']) for row in membership if row['compound_id'] == ethanol] == [
        ('drugclip', '1'), ('sprint', '4')]
    alias_map = {row['original_id']: row['canonical_id'] for row in read_csv(root / 'candidate_aliases.csv')}
    assert {alias_map[key] for key in ['library-ethanol', 'ethanol-alias', 'sprint-ethanol', 'lit-ethanol']} == {ethanol}
    provenance = json.loads(next(row for row in read_csv(root / 'unique_candidates.csv')
                                if row['compound_id'] == ethanol)['provenance'])
    assert {row['original_compound_id'] for row in provenance} == {
        'library-ethanol', 'ethanol-alias', 'sprint-ethanol', 'lit-ethanol'}
    write_assessments(root)
    select(cfg)
    assert len(read_csv(root / 'selection/drugclip_top5.csv')) == 2
    assert len(read_csv(root / 'selection/md_candidates.csv')) == 2


def test_merge_does_not_rewrite_original_model_score_tables(compute):
    cfg, _, _ = compute
    root = Path(cfg['run']['output'])
    score_path = root / 'screen/drugclip/scores.csv'
    rows = [model_row('one', 'CCO', 1), model_row('two', 'OCC', 2)]
    write_csv(score_path, rows)
    before = score_path.read_bytes()
    merge_fixture(cfg, [], rows)
    assert score_path.read_bytes() == before


def test_identity_preserves_stereo_salts_and_isotopes():
    identities = [canonical_chemical_identity(smiles)['compound_id'] for smiles in
                  ['C[C@H](O)F', 'C[C@@H](O)F', 'CC(O)F', 'CCO', 'CCO.[Na+]', '[13CH3]CO']]
    assert len(set(identities)) == len(identities)
    assert canonical_chemical_identity('OCC') == canonical_chemical_identity('CCO')
    assert '.' in canonical_chemical_identity('CCO.[Na+]')['smiles']


def test_literature_shortlist_is_shared_but_never_forces_md(compute):
    cfg, _, _ = compute
    root = merge_fixture(cfg, [{'compound_id': 'literature-only', 'name': 'paper candidate',
                               'smiles': 'CCN', 'decision': 'prioritize'}],
                         [model_row('model-ethanol', 'CCO', 1)])
    write_assessments(root, {'paper candidate': 'E'})
    select(cfg)
    literature = read_csv(root / 'selection/literature_shortlist.csv')[0]
    assert literature['selection_eligible'] == 'True'
    assert literature['exploratory'] == 'True'
    assert literature['assessment_state'] == 'exploratory'
    assert literature['md_selected'] == 'False'
    assert 'raw_score' not in literature and 'model_rank' not in literature
    assert {row['smiles'] for row in read_csv(root / 'selection/md_candidates.csv')} == {'CCO'}


def test_gate_failed_is_distinct_from_successful_e_abstention(compute):
    cfg, _, _ = compute
    root = merge_fixture(cfg, [], [model_row('e-success', 'CCO', 1), model_row('gate-failed', 'CCN', 2)])
    write_assessments(root)
    ids = {row['name']: row['compound_id'] for row in read_csv(root / 'unique_candidates.csv')}
    write_csv(root / 'gate/predictions.csv', [
        {'compound_id': ids['e-success'], 'gate_status': 'success', 'gate_class': 'E', 'gate_error': ''},
        {'compound_id': ids['gate-failed'], 'gate_status': 'failed', 'gate_class': 'E', 'gate_error': 'provider failed'},
    ])
    select(cfg)
    rows = {row['name']: row for row in read_csv(root / 'selection/all_candidates.csv')}
    assert rows['e-success']['assessment_state'] == 'exploratory'
    assert rows['gate-failed']['assessment_state'] == 'failed'
    assert rows['gate-failed']['exploratory'] == 'False'
    assert 'provider failed' in rows['gate-failed']['selection_reason']
    assert {row['name'] for row in read_csv(root / 'selection/md_candidates.csv')} == {'e-success'}


def test_merge_rejects_ambiguous_original_id(compute):
    cfg, _, _ = compute
    with pytest.raises(ValueError, match='Ambiguous original'):
        merge_fixture(cfg, [{'compound_id': 'same-id', 'name': 'other', 'smiles': 'CCN'}],
                      [model_row('same-id', 'CCO', 1)])


def test_empty_tables_have_headers_and_budget_caps_unique_md(compute):
    cfg, _, _ = compute
    root = merge_fixture(cfg, [], [])
    write_assessments(root)
    assert select(cfg)['unique_md_candidates'] == 0
    for relative in ['unique_candidates.csv', 'top100_membership.csv',
                     'selection/md_candidates.csv', 'selection/literature_shortlist.csv']:
        assert 'compound_id' in (root / relative).read_text(encoding='utf-8-sig').splitlines()[0]
    rows = [model_row(str(index), 'C' * (index + 1), index + 1, model)
            for index, model in enumerate(MODELS)]
    root = merge_fixture(cfg, [], rows)
    cfg['scientist']['max_md_jobs'] = 2
    write_assessments(root)
    assert select(cfg)['unique_md_candidates'] == 2


def test_handoff_resolves_base_paths_preserves_conditions_and_is_immutable(compute, tmp_path):
    _, base, package = compute
    candidate = CandidateRecord(candidate_id='lit-1', name='ethanol', smiles='CCO',
                                original_ids=['external-1'], evidence_ids=['E1'])
    path = create_compute_config(base, package, [candidate], tmp_path / 'compute', max_md_jobs=3)
    cfg = load_config(path)
    assert cfg['target']['chain'] == 'B'
    assert cfg['target']['protein_pdb'] == package.protein_pdb
    assert cfg['drugclip']['repo'] == str(tmp_path / 'external/drugclip')
    assert cfg['scientist']['max_md_jobs'] == 3
    assert cfg['md']['temperature_k'] == 300 and cfg['md']['salt_molar'] == 0.15
    assert cfg['gate']['chassis_name'].endswith('WCFS1')
    assert 'merge_candidates' in stages_for_config(cfg)
    assert set(json.loads(read_csv(tmp_path / 'compute/literature_candidates.csv')[0]['original_ids'])) == {
        'lit-1', 'external-1'}
    with pytest.raises(ValueError, match='immutable'):
        create_compute_config(base, package, [candidate], tmp_path / 'compute')
    with pytest.raises(ValueError, match='ready'):
        create_compute_config(base, package.model_copy(update={'status': 'needs_review'}), [], tmp_path / 'other')
    bad_conditions = package.model_copy(update={'metadata': {'compute_conditions': {'pH': 7.4}}})
    with pytest.raises(ValueError, match='engine support'):
        create_compute_config(base, bad_conditions, [], tmp_path / 'other')


def test_prepared_system_mapping_and_ambiguity(compute, tmp_path):
    cfg, base, package = compute
    cfg['md']['prepared_systems'] = {'library-ethanol': {'directory': './prepared', 'ligand_resname': 'LIG'}}
    write_json(base, cfg)
    result = load_config(create_compute_config(base, package, [], tmp_path / 'mapped'))
    canonical = canonical_chemical_identity('CCO')['compound_id']
    assert list(result['md']['prepared_systems']) == [canonical]
    assert result['md']['prepared_systems'][canonical]['directory'] == str(tmp_path / 'prepared')
    cfg['md']['prepared_systems']['unknown-id'] = {'directory': './other', 'ligand_resname': 'LIG'}
    write_json(base, cfg)
    with pytest.raises(ValueError, match='unambiguous'):
        create_compute_config(base, package, [], tmp_path / 'ambiguous')


def test_changed_input_is_rejected_before_any_resume_worker(compute, tmp_path):
    _, base, package = compute
    path = create_compute_config(base, package, [], tmp_path / 'compute')
    cfg = load_config(path)
    source = Path(cfg['scientist']['literature_candidates'])
    original = source.read_bytes()
    source.write_bytes(original + b'\n')
    with pytest.raises(ValueError, match='Scientific inputs changed'):
        run(cfg, 'prepare', 'prepare', True)
    assert not (tmp_path / 'compute/status.json').exists()


def test_collect_real_failed_records_and_original_scores(compute, tmp_path):
    _, base, package = compute
    path = create_compute_config(base, package, [], tmp_path / 'compute')
    cfg = load_config(path)
    root = Path(cfg['run']['output'])
    write_json(root / 'status.json', {'stages': {'md': {'status': 'failed', 'error': 'real fixture failure'}}})
    write_csv(root / 'screen/drugclip/scores.csv', [model_row('original-id', 'CCO', 1)])
    write_csv(root / 'unique_candidates.csv', [{'compound_id': 'canonical-id', 'smiles': 'CCO'}])
    write_csv(root / 'candidate_aliases.csv', [{'original_id': 'original-id', 'canonical_id': 'canonical-id'}])
    write_csv(root / 'md/results.csv', [{'compound_id': 'canonical-id', 'status': 'failed', 'error': 'tool missing'}])
    result = collect_compute_results(path)
    assert result['status'] == 'failed'
    assert result['md_results'][0]['error'] == 'tool missing'
    assert result['candidates'][0]['scores'][0]['raw_score'] == '99'
    assert result['candidates'][0]['scores'][0]['original_compound_id'] == 'original-id'
    assert result['files']['md/results.csv'] == str(root / 'md/results.csv')
    assert 'confidence' not in result and 'efficacy' not in result


def test_collect_does_not_claim_completion_when_stage_files_are_missing(compute, tmp_path):
    _, base, package = compute
    path = create_compute_config(base, package, [], tmp_path / 'missing-output')
    cfg = load_config(path)
    write_json(Path(cfg['run']['output']) / 'status.json',
               {'stages': {stage: {'status': 'completed'} for stage in stages_for_config(cfg)}})
    result = collect_compute_results(path)
    assert result['status'] == 'incomplete'
    assert 'screen/drugclip/scores.csv' in result['missing_files']


@pytest.mark.parametrize('terminal', ['failed', 'interrupted', 'blocked'])
def test_compute_job_terminal_status_overrides_stale_running_stage(compute, tmp_path, terminal):
    _, base, package = compute
    path = create_compute_config(base, package, [], tmp_path / terminal)
    root = path.parent
    write_json(root / 'status.json', {'stages': {'drugclip': {'status': 'running'}}})
    write_json(root / 'job.json', {'status': terminal, 'error': 'persisted runner outcome'})
    result = collect_compute_results(path)
    assert result['status'] == terminal
    assert result['stages']['drugclip']['status'] == 'running'
    assert result['job']['error'] == 'persisted runner outcome'
    assert 'job_terminal_status_overrides_stage_status' in result['status_basis']


def test_bounded_compute_completion_is_reported_as_completed_until(compute, tmp_path):
    _, base, package = compute
    path = create_compute_config(base, package, [], tmp_path / 'bounded')
    root = path.parent
    write_json(root / 'status.json', {'stages': {'prepare': {'status': 'completed'}}})
    write_json(root / 'job.json', {'status': 'completed', 'until': 'prepare'})
    write_json(root / 'target/target.json', {'target_id': 'fixture-package'})
    result = collect_compute_results(path)
    assert result['status'] == 'completed_until'
    assert result['completed_until'] == 'prepare'


def test_collect_streams_only_shared_candidate_scores_and_links_full_files(compute, tmp_path, monkeypatch):
    from scientist import integration
    _, base, package = compute
    path = create_compute_config(base, package, [], tmp_path / 'streamed')
    root = path.parent
    identity = canonical_chemical_identity('CCO')
    write_csv(root / 'unique_candidates.csv', [{**identity, 'original_ids': '["original-id"]'}])
    write_csv(root / 'candidate_aliases.csv',
              [{'original_id': 'original-id', 'canonical_id': identity['compound_id']}])
    rows = [model_row(f'outside-{index}', 'CCCC', index + 1) for index in range(1000)]
    rows += [model_row('original-id', 'CCO', 1), model_row('unlisted-ethanol-alias', 'OCC', 2)]
    rows[-1]['inchikey'] = identity['inchikey']
    rows[0]['status'] = 'failed'
    score_path = root / 'screen/drugclip/scores.csv'
    write_csv(score_path, rows)
    before = score_path.read_bytes()
    original_reader = integration.read_csv
    def small_tables_only(value):
        assert Path(value).name != 'scores.csv', 'Full score tables must use streaming CSV reads'
        return original_reader(value)
    monkeypatch.setattr(integration, 'read_csv', small_tables_only)
    result = collect_compute_results(path)
    assert {row['compound_id'] for row in result['scores']['drugclip']} == {
        'original-id', 'unlisted-ethanol-alias'}
    assert result['score_counts']['drugclip'] == {
        'total': 1002, 'retained': 2, 'status_counts': {'failed': 1, 'success': 1001},
        'read_complete': True}
    assert result['score_files']['drugclip'] == str(score_path)
    assert len(result['candidates'][0]['scores']) == 2
    assert score_path.read_bytes() == before


def test_no_scientist_source_uses_original_pipeline_and_writer_empty(compute, tmp_path):
    cfg, _, _ = compute
    assert stages_for_config(cfg) == BASE_STAGES
    path = write_literature_candidates([], tmp_path / 'empty.csv')
    assert read_csv(path) == []
    assert 'compound_id' in path.read_text(encoding='utf-8-sig').splitlines()[0]

