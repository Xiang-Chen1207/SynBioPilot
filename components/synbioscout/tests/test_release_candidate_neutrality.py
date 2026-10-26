"""Release defaults and candidate choice; parser outputs are fixed offline fixtures."""
import json
from html.parser import HTMLParser
from pathlib import Path

import pytest
from test_intake import confirmed, parse, raw, setup as intake_setup

from scientist.intake import grounded_draft, neutral_brief
from scientist.models import ResearchBrief, ScientistSettings

ROOT = Path(__file__).resolve().parents[1]


class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


@pytest.mark.parametrize('name', ['caffeine', 'resveratrol', 'NAC', 'SFN'])
def test_named_candidate_survives_confirmed_api_submission(intake_setup, name):
    text = f'Assess {name} for migraine'
    intake_setup.parser.result = raw(text, 'migraine', name, phenotype=None)
    draft, _ = parse(intake_setup, text)
    row, project, _, payload = confirmed(intake_setup, draft)
    response = intake_setup.client.post('/api/projects/'+project['project_id']+'/runs', json=payload)
    assert response.status_code == 202
    submitted = intake_setup.calls[-1][1]
    assert submitted['existing_candidates'] == [name]
    assert submitted['background_package_id'] == ''
    assert submitted['chassis_name'] == '' and submitted['target_hints'] == []
    assert submitted['task_intent'] == 'candidate_assessment'


@pytest.mark.parametrize('disease', ['autism', 'migraine', 'rheumatoid arthritis'])
def test_disease_only_does_not_choose_candidates(disease):
    text = 'Explore '+disease
    draft = grounded_draft(raw(text, disease, None, phenotype=None), [text])
    assert draft['brief']['existing_candidates'] == []
    assert draft['brief']['background_package_id'] == ''
    assert draft['brief']['task_intent'] == 'disease_exploration'


@pytest.mark.parametrize('name', ['NAC', 'SFN', 'caffeine'])
def test_excluded_candidate_is_never_submitted_for_assessment(name):
    text = f'Study migraine; do not consider {name}'
    value = raw(text, 'migraine', name, phenotype=None)
    value['candidates'][0]['stance'] = 'exclude'
    value['intent'] = 'disease_exploration'
    draft = grounded_draft(value, [text])
    assert draft['brief']['existing_candidates'] == []
    assert draft['excluded_candidates'] == [name]


def test_fresh_config_requires_explicit_model_and_compute_setup():
    value = json.loads((ROOT/'examples/scientist/settings.example.json').read_text())
    settings = ScientistSettings.model_validate(value)
    assert settings.llm.backend == 'none' and settings.llm.model == ''
    assert settings.target2lead_config == '' and settings.allow_remote_llm_data is False
    assert settings.llm.timeout_seconds == 900


def test_example_brief_has_no_candidate_target_or_strain_preselection():
    brief = ResearchBrief.model_validate_json((ROOT/'examples/scientist/brief.example.json').read_text())
    assert brief.task_intent == 'disease_exploration'
    for field in ('existing_candidates','target_hints','target_packages','background_package_id',
                  'chassis_name','chassis_id','reference_chassis','experiment_ids'):
        assert not getattr(brief, field)
    assert brief.budget.max_md_jobs_total == 0
    assert neutral_brief()['existing_candidates'] == []


def test_entry_fields_are_blank_and_examples_are_opt_in():
    page = Controls()
    page.feed((ROOT/'scientist/web/index.html').read_text())
    by_id = {attrs['id']:attrs for _, attrs in page.items if 'id' in attrs}
    for name in ('existingCandidates','backgroundPackageId','chassis','hints'):
        assert not by_id[name].get('value')
    examples = [attrs for _, attrs in page.items if 'data-intake-example' in attrs]
    assert len(examples) == 2 and all(a['type']=='button' for a in examples)
    assert all('NAC' not in a['data-intake-example'] and 'SFN' not in a['data-intake-example'] for a in examples)
    assert 'disabled' in by_id['startResearch']

