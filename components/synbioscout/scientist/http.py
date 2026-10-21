"""Bounded public-data requests with content-keyed, atomic response caching."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests


class DataSourceError(RuntimeError):
    pass


# Public query fields used by the fixed database adapters. Other fields, headers
# and POST bodies are deliberately absent from the public attempt trace.
_PUBLIC_PARAMS = frozenset({
    "query", "format", "size", "pageSize", "sort_date", "resultType", "limit", "offset",
    "molecule_chembl_id", "target_chembl_id", "assay_chembl_id", "pref_name__icontains",
    "organism", "assay_organism", "target_organism", "species", "taxonId", "taxonUsage",
    "geneProductId", "goId", "goUsage", "evidenceCode", "aspect", "page", "chembl_id",
    "q", "molecule_structures__standard_inchi_key", "target_components__accession",
})
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def _public_endpoint(url):
    parts = urlsplit(url)
    # Userinfo, URL query parameters and fragments may contain secrets. The
    # endpoint path and separately allowlisted GET parameters identify the query.
    host = parts.hostname or ""
    if parts.port:
        host += ":" + str(parts.port)
    return urlunsplit((parts.scheme, host, parts.path, "", ""))[:1200]


def _public_params(params):
    if not isinstance(params, dict):
        return {}
    return {key: value[:500] if isinstance(value, str) else value
            for key, value in params.items() if key in _PUBLIC_PARAMS and
            (value is None or isinstance(value, (str, int, float, bool)))}


class HTTPClient:
    def __init__(self, cache_dir: Path, timeout: int = 30, retries: int = 2):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.retries = retries
        self.deadline = None
        self.request_count = 0
        self.cache_hits = 0
        self.before_request = None
        self.on_cache_hit = None
        self.request_events = []
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "SynBioScout/0.1 (iGEM biomedical research)"

    def _remaining(self):
        remaining = self.deadline - time.monotonic() if self.deadline is not None else self.timeout
        if remaining <= 0:
            raise TimeoutError("Public-data request budget exhausted")
        return remaining

    def _event(self, method, url, params, key, attempt, *, cache_hit=False):
        event = {"endpoint": _public_endpoint(url), "method": method,
                 "parameters": _public_params(params) if method == "GET" else {},
                 "cache_key": key, "attempt": attempt, "retry_index": max(0, attempt - 1),
                 "cache_hit": cache_hit, "outcome": "pending", "http_status": None}
        self.request_events.append(event)
        return event

    def _retry_wait(self, attempt, event):
        # Do not sleep past the shared deadline, even after a failed attempt.
        try:
            delay = min(2 ** attempt, 4, self._remaining())
            time.sleep(delay)
            self._remaining()
        except TimeoutError:
            event["stop_reason"] = "deadline"
            raise

    def _request(self, method, url, *, params=None, payload=None, headers=None):
        if not url.startswith("https://"):
            raise ValueError("Public source downloads require HTTPS")
        identity = [method, url, params, payload]
        if headers:
            identity.append(headers)
        key = hashlib.sha256(json.dumps(identity,
                                       sort_keys=True).encode()).hexdigest()
        cache = self.cache_dir / f"{key}.response"
        if cache.exists():
            event = self._event(method, url, params, key, 0, cache_hit=True)
            try:
                self._remaining()
                if self.on_cache_hit:
                    self.on_cache_hit()
            except (RuntimeError, TimeoutError):
                event["outcome"] = "blocked_budget"
                event["cache_hit"] = False
                raise
            body = cache.read_bytes()
            self.cache_hits += 1
            event["outcome"] = "cache_hit"
            return body
        for attempt in range(self.retries + 1):
            event = self._event(method, url, params, key, attempt + 1)
            try:
                remaining = self._remaining()
                if self.before_request:
                    self.before_request()
            except (RuntimeError, TimeoutError):
                event["outcome"] = "blocked_budget"
                raise
            self.request_count += 1
            try:
                options = {"params": params, "json": payload,
                           "timeout": min(self.timeout, remaining), "allow_redirects": False}
                if headers:
                    options["headers"] = headers
                response = self.session.request(method, url, **options)
                event["http_status"] = response.status_code
                if 300 <= response.status_code < 400:
                    event["outcome"] = "redirect_rejected"
                    raise DataSourceError("Public source redirect was not followed")
                if response.status_code in _RETRY_STATUS and attempt < self.retries:
                    event["outcome"] = "http_error"
                    self._retry_wait(attempt, event)
                    continue
                response.raise_for_status()
                body = response.content
                if not body:
                    event["outcome"] = "empty_response"
                    raise DataSourceError(f"Empty response from {_public_endpoint(url)}")
                try:
                    self._remaining()
                except TimeoutError:
                    event["outcome"] = "deadline_exceeded"
                    event["stop_reason"] = "deadline"
                    raise
                staged = cache.with_suffix(f".{uuid.uuid4().hex}.partial")
                staged.write_bytes(body)
                staged.replace(cache)
                event["outcome"] = "ok"
                return body
            except requests.RequestException as exc:
                event["outcome"] = "http_error" if isinstance(exc, requests.HTTPError) else "network_error"
                status = event["http_status"]
                retryable = not isinstance(exc, requests.HTTPError) or status in _RETRY_STATUS
                if attempt == self.retries or not retryable:
                    raise DataSourceError(
                        f"{method} {_public_endpoint(url)}: {type(exc).__name__}") from exc
                self._retry_wait(attempt, event)
        raise DataSourceError(f"Request failed: {_public_endpoint(url)}")

    def get_bytes(self, url, params=None, *, headers=None):
        return self._request("GET", url, params=params, headers=headers)

    def get_text(self, url, params=None, *, headers=None):
        return self.get_bytes(url, params, headers=headers).decode("utf-8")

    def get_json(self, url, params=None, *, headers=None):
        try:
            return json.loads(self.get_text(url, params, headers=headers))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataSourceError(f"Invalid JSON response from {_public_endpoint(url)}") from exc

    def post_json(self, url, payload):
        try:
            return json.loads(self._request("POST", url, payload=payload))
        except json.JSONDecodeError as exc:
            raise DataSourceError(f"Invalid JSON response from {_public_endpoint(url)}") from exc

