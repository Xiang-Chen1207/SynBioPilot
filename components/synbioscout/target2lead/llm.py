"""ChassisGate JSON client over Codex CLI, shared-filesystem queue, or API."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
from .common import ROOT, write_json


class LiteratureClient:
    def __init__(self, settings, directory):
        self.settings = settings
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def complete_json(self, system, user, *, temperature):
        backend = self.settings.get('backend', 'codex')
        if backend == 'file_queue':
            request = self.directory/(uuid.uuid4().hex+'.request.json')
            response = request.with_name(request.name.replace('.request.', '.response.'))
            write_json(request, {'system': system, 'user': user, 'temperature': temperature})
            deadline = time.monotonic()+int(self.settings.get('timeout_seconds', 1200))
            while time.monotonic() < deadline:
                if response.exists():
                    payload = json.loads(response.read_text())
                    if payload.get('error'):
                        raise RuntimeError(payload['error'])
                    return payload['content']
                time.sleep(2)
            raise TimeoutError('Codex bridge did not return the literature request')
        if backend == 'api':
            import requests
            key_name = self.settings.get('api_key_env', 'DEEPSEEK_API_KEY')
            key = os.environ.get(key_name)
            if not key:
                raise RuntimeError(f'Missing environment variable {key_name}')
            result = requests.post(self.settings['base_url'].rstrip('/')+'/chat/completions',
                headers={'Authorization': 'Bearer '+key}, json={
                    'model': self.settings['model'], 'messages': [
                        {'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                    'temperature': temperature, 'response_format': {'type': 'json_object'}},
                timeout=int(self.settings.get('timeout_seconds', 180)))
            result.raise_for_status()
            return result.json()['choices'][0]['message']['content']
        if backend != 'codex':
            raise ValueError(f'Unknown literature backend: {backend}')
        codex = shutil.which(self.settings.get('codex_binary', 'codex'))
        if codex is None:
            raise RuntimeError('Codex is unavailable; install/login or use file_queue/API')
        token = uuid.uuid4().hex
        answer, log = self.directory/(token+'.answer.json'), self.directory/(token+'.log')
        sqlite = Path(self.settings.get('codex_sqlite_home', self.directory/'sqlite')).resolve()
        args = [codex, 'exec', '--ephemeral', '--ignore-user-config', '--skip-git-repo-check',
                '--sandbox', 'read-only', '--model', self.settings.get('model', 'gpt-6-astra'),
                '-c', 'approval_policy="never"', '-c', 'web_search="disabled"',
                '-c', 'features.shell_tool=false', '-c', 'features.unified_exec=false',
                '-c', 'features.shell_snapshot=false', '-c', 'features.multi_agent=false',
                '-c', 'project_doc_max_bytes=0', '-c', 'history.persistence="none"',
                '-c', 'model_reasoning_effort="'+self.settings.get('reasoning_effort', 'high')+'"',
                '-c', f'log_dir="{self.directory / "logs"}"', '-c', f'sqlite_home="{sqlite}"',
                '--output-schema', str(ROOT/'resources/literature.schema.json'),
                '--output-last-message', str(answer), '--cd', str(self.directory), '-']
        for attempt in range(2):
            with log.open('a') as stream:
                result = subprocess.run(args, input=system+'\n\n'+user, text=True,
                    stdout=stream, stderr=subprocess.STDOUT,
                    timeout=int(self.settings.get('timeout_seconds', 600)))
            if result.returncode == 0:
                content = answer.read_text()
                json.loads(content)
                return content
            transient = any(s in log.read_text(errors='replace') for s in
                            ('503', '502', '429', 'connection reset', 'request timed out'))
            if attempt or not transient:
                raise RuntimeError(f'Codex exited {result.returncode}; see {log}')
            time.sleep(10)


def bridge(cfg):
    directory = Path(cfg['run']['output'])/'gate/queue'
    directory.mkdir(parents=True, exist_ok=True)
    settings = {**cfg['gate'], 'backend': 'codex'}
    client = LiteratureClient(settings, directory/'runtime')
    print(f'Codex bridge listening: {directory}', flush=True)
    while not (directory/'DONE').exists():
        for path in sorted(directory.glob('*.request.json')):
            response = path.with_name(path.name.replace('.request.', '.response.'))
            if response.exists():
                continue
            request = json.loads(path.read_text())
            try:
                content = client.complete_json(request['system'], request['user'], temperature=request['temperature'])
                write_json(response, {'content': content})
            except Exception as exc:
                write_json(response, {'error': str(exc)})
        time.sleep(2)

