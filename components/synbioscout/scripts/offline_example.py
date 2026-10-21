"""Run a synthetic API/analysis example without models or external requests."""
from __future__ import annotations
import argparse
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from scientist.api import create_app
from scientist.experiments import analyze_experiment
from scientist.models import ExperimentSpec, ScientistSettings

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    destination = parser.parse_args().output.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=False)
    def forbidden(*args, **kwargs):
        raise RuntimeError('This synthetic example does not permit external transport')
    with tempfile.TemporaryDirectory(prefix='synbioscout_synthetic_') as temp:
        settings = ScientistSettings(workspace=temp, llm={'backend':'none'},
                                     allow_remote_llm_data=False)
        with patch('requests.sessions.Session.request', forbidden), \
             patch('socket.create_connection', forbidden), \
             patch('subprocess.run', forbidden), patch('subprocess.Popen', forbidden):
            with TestClient(create_app(settings)) as client:
                health = client.get('/api/health')
                tools = client.get('/api/research-tools')
                assert health.status_code == tools.status_code == 200
                page = client.get('/')
                assert page.status_code == 200 and '<html lang="en"' in page.text
            spec = ExperimentSpec.model_validate_json(
                (ROOT/'examples/scientist/experiment_SYNTHETIC_growth.json').read_text())
            analysis = analyze_experiment(
                ROOT/'examples/scientist/experiment_SYNTHETIC_growth.csv', spec,
                destination/'growth_analysis')
    receipt = {'synthetic':True, 'model_calls':0, 'scientific_http_requests':0,
               'api_health_status':health.json()['status'],
               'english_ui':True, 'tool_operations':len(tools.json()['tools']),
               'analysis':analysis,
               'note':'Artificial measurements; no biological or clinical validation.'}
    (destination/'SYNTHETIC_EXAMPLE.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps({'synthetic':True,'output':str(destination),'tool_operations':receipt['tool_operations']}))

if __name__ == '__main__':
    main()

