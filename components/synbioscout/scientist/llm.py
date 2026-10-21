"""Validated structured inference over Codex login or configured model APIs.

Codex is restricted to returning JSON: no shell, network browsing or agent delegation.
The surrounding application owns all retrieval, persistence and tool execution.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import jsonschema
import requests

from .models import LLMSettings
from .provider_receipts import MESSAGES, public_provider_failure
from .store import digest, write_json


class InferenceError(RuntimeError):
    def __init__(self, message, *, failure_metadata=None):
        self.failure_metadata = public_provider_failure(failure_metadata)
        # A metadata-bearing error never forwards a provider/OS exception body.
        if failure_metadata is not None:
            message = MESSAGES.get(self.failure_metadata.get("category"), "Model invocation failed")
        super().__init__(message)


def _output_facts(answer):
    facts = {"output_exists": None, "output_is_file": None, "output_size_bytes": None,
             "output_stat_status": "error"}
    try:
        info = answer.stat(follow_symlinks=False)
    except FileNotFoundError:
        return {**facts, "output_exists": False, "output_is_file": False, "output_stat_status": "missing"}
    except Exception:  # noqa: BLE001 -- optional facts cannot replace a primary process failure
        return facts
    regular = stat.S_ISREG(info.st_mode)
    return {"output_exists": True, "output_is_file": regular,
            "output_size_bytes": info.st_size if regular else None, "output_stat_status": "observed"}


def strict_schema(schema):
    """Normalize object schemas for strict outputs without changing domain validation."""
    value = json.loads(json.dumps(schema))
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for key in ("default", "title"):
                node.pop(key, None)
            for key, item in node.items():
                if key in ("properties", "$defs", "definitions", "patternProperties") and isinstance(item, dict):
                    # Maps contain field/type NAMES, not schema metadata.
                    # Preserve real fields literally named 'title'/'default'.
                    for child in item.values():
                        walk(child)
                else:
                    walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(value)
    return value


def api_request_body(settings, system, user, schema):
    if settings.backend == "responses":
        return {"model": settings.model, "instructions": system, "input": user,
                "text": {"format": {"type": "json_schema", "name": "research_result",
                                      "strict": True, "schema": strict_schema(schema)}}}
    return {"model": settings.model, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"}}


def schema_json(schema):
    """Exact Codex schema file bytes; JSON whitespace has no contract meaning."""
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"


def serialized_request(settings, system, user, schema):
    """Measure the dispatched UTF-8 body/schema, including transport escaping."""
    if settings.backend in {"codex", "none"}:
        return system + "\n\n" + user + schema_json(strict_schema(schema))
    # requests' JSON body uses ensure_ascii=True, the default separators and no NaN.
    return json.dumps(api_request_body(settings, system, user, schema), allow_nan=False)


class StructuredLLM:
    def __init__(self, settings: LLMSettings, directory: Path, max_calls=10):
        self.settings = settings
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_calls = max_calls
        self.calls = 0
        self.cache_hits = 0
        self.deadline = None
        self.attempt_path = self.directory / "attempts.json"
        if settings.base_url:
            parts = urlsplit(settings.base_url)
            if parts.username or parts.password or parts.query or parts.fragment:
                raise InferenceError("API base URL cannot contain credentials, query strings or fragments")

    def complete(self, system, user, schema):
        identity = {"backend": self.settings.backend, "model": self.settings.model,
                    "base_url": self.settings.base_url, "system": system,
                    "user": user, "schema": schema}
        key = digest(identity)
        cached = self.directory / f"{key}.json"
        if cached.is_file():
            payload = json.loads(cached.read_text())
            jsonschema.validate(payload["result"], schema)
            self.cache_hits += 1
            return payload["result"]
        attempts = json.loads(self.attempt_path.read_text()) if self.attempt_path.exists() else []
        if len(attempts) >= self.max_calls:
            raise InferenceError("Configured LLM call budget exhausted")
        if self.settings.backend == "none":
            raise InferenceError("LLM disabled: retrieved records are not interpreted as conclusions")
        self.calls += 1
        started = time.monotonic()
        if self.deadline:
            remaining = self.deadline - started
            if remaining <= 0:
                raise InferenceError("Research wall-time budget exhausted")
            self.settings = self.settings.model_copy(update={"timeout_seconds": max(1, min(self.settings.timeout_seconds, int(remaining)))})
        attempts.append({"request_digest": key, "started_monotonic": started})
        write_json(self.attempt_path, attempts)
        shape = strict_schema(schema)
        self._provider_started = started
        self._provider_observation = {"returncode": None, "signal": None, "output_exists": None,
            "output_is_file": None, "output_size_bytes": None, "output_stat_status": "not_checked"}
        if self.settings.backend == "codex":
            raw = self._codex(system, user, shape, key)
        else:
            raw = self._api(system, user, shape)
        if not raw.strip():
            raise self._failure("answer_empty", "output_read")
        try:
            result = json.loads(raw)
        except ValueError as exc:
            raise self._failure("json_invalid", "json_parse") from exc
        try:
            jsonschema.validate(result, schema)
        except jsonschema.ValidationError as exc:
            raise self._failure("schema_invalid", "schema_validation") from exc
        write_json(cached, {"request": identity, "result": result,
                            "duration_seconds": time.monotonic() - started})
        return result

    def _failure(self, category, stage):
        metadata = {"version": 1, "category": category, "stage": stage,
            "origin": "provider_call", "backend": self.settings.backend,
            "elapsed_seconds": round(max(0, time.monotonic() - self._provider_started), 6),
            **self._provider_observation}
        return InferenceError("Model invocation failed", failure_metadata=metadata)

    def _codex(self, system, user, schema, key):
        shape = self.directory / f"{key}.schema.json"
        answer = self.directory / f"{key}.answer.json"
        log = self.directory / f"{key}.log"
        try:
            executable = shutil.which(self.settings.executable)
        except OSError as exc:
            raise self._failure("launch_error", "executable_lookup") from exc
        if not executable:
            raise self._failure("launch_error", "executable_lookup")
        try:
            write_json(shape, schema, compact=True)
            args = [executable, "exec", "--ephemeral", "--ignore-user-config",
                    "--skip-git-repo-check", "--sandbox", "read-only",
                    "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
                    "-c", "features.shell_tool=false", "-c", "features.unified_exec=false",
                    "-c", "features.multi_agent=false", "-c", "project_doc_max_bytes=0",
                    "--output-schema", str(shape.resolve()),
                    "--output-last-message", str(answer.resolve()),
                    "--cd", str(self.directory.resolve()), "-"]
            args[2:2] = ["--model", self.settings.model or "gpt-5.5"]
            stream = log.open("w")
        except OSError as exc:
            self._provider_observation.update(_output_facts(answer))
            raise self._failure("launch_error", "prepare") from exc
        try:
            try:
                result = subprocess.run(args, input=system + "\n\n" + user,
                                        text=True, stdout=stream, stderr=subprocess.STDOUT,
                                        timeout=self.settings.timeout_seconds, check=False)
            finally:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001, S110 -- never expose log errors or replace the process outcome
                    pass
        except subprocess.TimeoutExpired as exc:
            self._provider_observation.update(_output_facts(answer))
            raise self._failure("timeout", "process") from exc
        except OSError as exc:
            self._provider_observation.update(_output_facts(answer))
            raise self._failure("launch_error", "process") from exc
        self._provider_observation.update(returncode=result.returncode,
            signal=-result.returncode if type(result.returncode) is int and -127 <= result.returncode < 0 else None)
        self._provider_observation.update(_output_facts(answer))
        if result.returncode:
            raise self._failure("process_nonzero", "process")
        if self._provider_observation["output_stat_status"] == "error":
            raise self._failure("read_error", "output_stat")
        if not self._provider_observation["output_exists"]:
            raise self._failure("answer_missing", "output_stat")
        if not self._provider_observation["output_is_file"]:
            raise self._failure("read_error", "output_stat")
        if self._provider_observation["output_size_bytes"] == 0:
            raise self._failure("answer_empty", "output_read")
        try:
            return answer.read_text()
        except (OSError, UnicodeError) as exc:
            raise self._failure("read_error", "output_read") from exc

    def _api(self, system, user, schema):
        key = os.environ.get(self.settings.api_key_env)
        if not key:
            raise InferenceError(f"Configure {self.settings.api_key_env} for this API backend")
        if not self.settings.model:
            raise InferenceError("An explicit API model is required")
        base = self.settings.base_url or "https://api.openai.com/v1"
        if not base.startswith("https://") and not base.startswith("http://127.0.0.1:"):
            raise InferenceError("API base URL must use HTTPS or a local loopback server")
        headers = {"Authorization": "Bearer " + key}
        if self.settings.backend == "responses":
            url = base.rstrip("/") + "/responses"
        else:
            url = base.rstrip("/") + "/chat/completions"
        body = api_request_body(self.settings, system, user, schema)
        try:
            response = requests.post(url, headers=headers, json=body,
                                     timeout=self.settings.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "network")
            raise InferenceError(f"Model API request failed ({status})") from exc
        if self.settings.backend == "responses":
            for item in payload.get("output", []):
                for content in item.get("content", []):
                    if content.get("type") == "refusal":
                        raise InferenceError("Model declined the request")
                    if content.get("type") == "output_text":
                        return content["text"]
            raise InferenceError("API returned no structured text")
        return payload["choices"][0]["message"]["content"]

