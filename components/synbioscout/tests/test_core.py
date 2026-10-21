"""Offline core guards; text, identities and model responses are test fixtures."""
import json

import pytest
import requests

from scientist.evidence import decide_candidates, validate_evidence
from scientist.http import DataSourceError
from scientist.llm import InferenceError, StructuredLLM
from scientist.models import CandidateRecord, EvidenceRecord, LLMSettings, SourceRecord
from scientist.sources import BiomedicalSources
from scientist.store import ProjectStore

QUOTE = 'The fixture compound decreased the measured assay endpoint.'
SCHEMA = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
          'required': ['ok'], 'additionalProperties': False}


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Core unit tests must not send real HTTP or model requests')
    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)


def source():
    return SourceRecord(source_id='fixture:source', provider='offline_fixture',
        title='Illustrative fixture', url='https://example.invalid/fixture',
        retrieved_at='2026-01-01T00:00:00Z',
        text='Introductory fixture text.\nThe  fixture compound decreased the measured assay endpoint.\nEnd.')


def evidence_payload(**updates):
    return {'evidence_id': 'fixture:evidence', 'claim': 'Fixture descriptive observation',
            'source_id': 'fixture:source', 'quote': QUOTE, 'location': 'fixture paragraph 2',
            'polarity': 'supports', 'study_system': 'fixture cells',
            'condition': 'fixture condition', 'endpoint': 'fixture endpoint',
            'limitations': ['Fixture only'], **updates}


def test_exact_span_guard_accepts_whitespace_only_variations():
    accepted, rejected = validate_evidence([evidence_payload()], [source()])
    assert not rejected
    assert len(accepted) == 1 and accepted[0].validated_span is True
    assert accepted[0].quote == QUOTE


@pytest.mark.parametrize('updates,reason', [
    ({'quote': 'The fixture compound improved clinical outcomes in humans.'}, 'absent'),
    ({'quote': 'short'}, 'too short'),
    ({'source_id': 'unknown:source'}, 'unknown source'),
    ({'location': ''}, 'location'),
])
def test_exact_span_guard_rejects_unknown_or_changed_support(updates, reason):
    accepted, rejected = validate_evidence([evidence_payload(**updates)], [source()])
    assert accepted == []
    assert reason in rejected[0]['reason']


@pytest.mark.parametrize('admet', [{'admet_status': 'failed'}, {'status': 'failed'}])
def test_admet_failure_cannot_be_prioritized_by_positive_gate(admet):
    candidate = CandidateRecord(candidate_id='fixture-candidate', name='fixture',
        identity_status='resolved', evidence_ids=['fixture:evidence'], admet=admet,
        gate={'gate_status': 'success', 'gate_class': 'A1'})
    decide_candidates([candidate], [EvidenceRecord(**evidence_payload(), validated_span=True)])
    assert candidate.decision == 'hold'
    assert any('ADMET execution failed' in reason for reason in candidate.decision_reasons)


@pytest.mark.parametrize('gate_status', [None, '', 'unknown', 'queued', 'partial', 'failed'])
def test_non_successful_gate_status_never_prioritizes(gate_status):
    gate = {'gate_class': 'A1'}
    if gate_status is not None:
        gate['gate_status'] = gate_status
    candidate = CandidateRecord(candidate_id='fixture-candidate', name='fixture',
        identity_status='resolved', evidence_ids=['fixture:evidence'], gate=gate)
    decide_candidates([candidate], [EvidenceRecord(**evidence_payload(), validated_span=True)])
    assert candidate.decision != 'prioritize'


def test_successful_gate_e_stays_exploratory():
    candidate = CandidateRecord(candidate_id='fixture-candidate', name='fixture',
        identity_status='resolved', evidence_ids=['fixture:evidence'],
        gate={'gate_status': 'success', 'gate_class': 'E'})
    decide_candidates([candidate], [EvidenceRecord(**evidence_payload(), validated_span=True)])
    assert candidate.decision == 'exploratory'


def test_store_rejects_identifiers_traversal_absolute_paths_and_escaped_symlinks(tmp_path):
    store = ProjectStore(tmp_path / 'workspace')
    pid = store.create_project('Fixture project')['project_id']
    project = store.project_dir(pid)
    outside = tmp_path / 'outside.txt'
    outside.write_text('fixture private data')
    with pytest.raises(ValueError, match='Invalid resource ID'):
        store.project_dir('../outside')
    with pytest.raises(ValueError, match='Invalid resource ID'):
        store.run_dir(pid, '../../outside')
    with pytest.raises(FileNotFoundError, match='Artifact not found'):
        store.artifact_path(pid, str(outside))
    with pytest.raises(FileNotFoundError, match='Artifact not found'):
        store.artifact_path(pid, '../../../outside.txt')
    (project / 'escaped.txt').symlink_to(outside)
    with pytest.raises(FileNotFoundError, match='Artifact not found'):
        store.artifact_path(pid, 'escaped.txt')
    (project / '.secrets').mkdir()
    (project / '.secrets/token.txt').write_text('fixture secret')
    with pytest.raises(ValueError, match='Hidden files'):
        store.artifact_path(pid, '.secrets/token.txt')
    (project / 'public.txt').write_text('fixture public artifact')
    assert store.artifact_path(pid, 'public.txt') == project / 'public.txt'


@pytest.mark.parametrize('failure', ['request', 'invalid_json', 'invalid_schema'])
def test_failed_llm_attempts_persist_across_instances_and_exhaust_budget(tmp_path, monkeypatch, failure):
    settings = LLMSettings(backend='compatible', model='offline-fixture',
                           base_url='https://example.invalid/v1')
    calls = []
    def failed_api(self, system, user, schema):
        calls.append(user)
        if failure == 'request':
            raise InferenceError('Synthetic request failure')
        return 'not JSON' if failure == 'invalid_json' else '{"ok":"not a boolean"}'
    monkeypatch.setattr(StructuredLLM, '_api', failed_api)
    for user in ['fixture request 1', 'fixture request 2']:
        client = StructuredLLM(settings, tmp_path / 'inference', max_calls=2)
        with pytest.raises(InferenceError):
            client.complete('fixture system', user, SCHEMA)
    attempts = json.loads((tmp_path / 'inference/attempts.json').read_text())
    assert len(attempts) == 2
    assert all('request_digest' in attempt for attempt in attempts)
    third = StructuredLLM(settings, tmp_path / 'inference', max_calls=2)
    with pytest.raises(InferenceError, match='budget exhausted'):
        third.complete('fixture system', 'fixture request 3', SCHEMA)
    assert calls == ['fixture request 1', 'fixture request 2']
    assert len(json.loads((tmp_path / 'inference/attempts.json').read_text())) == 2


def test_llm_cache_hit_is_available_after_budget_exhaustion_without_a_new_attempt(tmp_path, monkeypatch):
    settings = LLMSettings(backend='compatible', model='offline-fixture',
                           base_url='https://example.invalid/v1')
    calls = []
    def successful_api(self, system, user, schema):
        calls.append(user)
        return '{"ok":true}'
    monkeypatch.setattr(StructuredLLM, '_api', successful_api)
    first = StructuredLLM(settings, tmp_path / 'inference', max_calls=1)
    assert first.complete('fixture system', 'fixture request', SCHEMA) == {'ok': True}
    second = StructuredLLM(settings, tmp_path / 'inference', max_calls=1)
    assert second.complete('fixture system', 'fixture request', SCHEMA) == {'ok': True}
    assert second.cache_hits == 1 and second.calls == 0
    assert len(json.loads(second.attempt_path.read_text())) == 1
    with pytest.raises(InferenceError, match='budget exhausted'):
        second.complete('fixture system', 'different request', SCHEMA)
    assert calls == ['fixture request']


@pytest.mark.parametrize('url', [
    'https://user:fixture-password@example.invalid/v1',
    'https://fixture-user@example.invalid/v1',
    'https://example.invalid/v1?api_key=fixture-secret',
    'https://example.invalid/v1#fixture-secret',
])
def test_llm_base_url_rejects_credentials_queries_and_fragments_before_logging(tmp_path, url):
    settings = LLMSettings(backend='compatible', model='offline-fixture', base_url=url)
    with pytest.raises(InferenceError, match='credentials, query strings or fragments'):
        StructuredLLM(settings, tmp_path / 'inference')
    assert list((tmp_path / 'inference').glob('*.json')) == []


class FakeHTTP:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        return self.payload


def test_unknown_protein_identity_is_an_error():
    connector = BiomedicalSources(FakeHTTP({'results': []}))
    with pytest.raises(DataSourceError, match='ambiguous/unresolved'):
        connector.resolve_target('FIXTURE_GENE')


def test_protein_species_mismatch_is_an_error():
    connector = BiomedicalSources(FakeHTTP({'results': [{
        'primaryAccession': 'FIXTURE_ONLY', 'entryType': 'UniProtKB reviewed (Swiss-Prot)',
        'genes': [{'geneName': {'value': 'FIXTURE_GENE'}}],
        'organism': {'scientificName': 'Mus musculus'}}]}))
    with pytest.raises(DataSourceError, match='species mismatch'):
        connector.resolve_target('FIXTURE_GENE', 'Homo sapiens')


@pytest.mark.parametrize('payload,reason', [
    ({'PropertyTable': {'Properties': []}}, 'maps to 0 entities'),
    ({'PropertyTable': {'Properties': [{'CID': 1, 'IsomericSMILES': '', 'InChIKey': ''}]}}, 'invalid structure'),
    ({'PropertyTable': {'Properties': [{'CID': 1, 'IsomericSMILES': 'CCO', 'InChIKey': 'incorrect'}]}}, 'disagree'),
])
def test_missing_empty_or_inconsistent_molecule_identity_is_an_error(payload, reason):
    connector = BiomedicalSources(FakeHTTP(payload))
    with pytest.raises(DataSourceError, match=reason):
        connector.resolve_molecule('fixture molecule')

