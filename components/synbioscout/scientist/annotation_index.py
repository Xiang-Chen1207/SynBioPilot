"""Versioned receipts for exact normal-connector queries, not a discovery corpus.

Responses still come exclusively from HTTPClient's query/body-keyed cache or its
bounded network path. This small index records provenance; it cannot enumerate
candidate molecules, silently inject prior literature, or bypass the run ledger.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .store import utc_now, write_json


def query_receipt(http, provider, operation, parameters, result, event_start):
    identity = {"provider": provider, "operation": operation, "parameters": parameters}
    encode = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    key = hashlib.sha256(encode(identity)).hexdigest()
    result_hash = hashlib.sha256(encode(result)).hexdigest()
    events = getattr(http, "request_events", [])[event_start:]
    successes = [e for e in events if e.get("outcome") in {"ok", "cache_hit"}]
    live = any(e.get("outcome") == "ok" for e in successes)
    cached = any(e.get("outcome") == "cache_hit" for e in successes)
    origin = "live_response" if live else "exact_query_http_cache" if cached else "transport_unrecorded"
    observed_at = utc_now()
    receipt = {"schema_version": 1, **identity, "query_key": key,
        "response_sha256": result_hash, "hash_scope": "canonical_json_response",
        "observed_at": observed_at, "source_retrieved_at": observed_at if live else "",
        "cache_origin": origin,
        "request_cache_keys": list(dict.fromkeys(e["cache_key"] for e in successes if e.get("cache_key"))),
        "interpretation": "query_observation_not_independent_validation"}
    cache_dir = getattr(http, "cache_dir", None)
    if cache_dir is None or not successes:
        return receipt
    directory = Path(cache_dir) / "annotation_index_v1"
    path = directory / (key + ".json")
    try:
        if directory.is_symlink() or path.is_symlink():
            return receipt
        if cached and path.is_file() and path.stat().st_size < 30000:
            previous = json.loads(path.read_text())
            if (isinstance(previous, dict) and previous.get("query_key") == key and previous.get("parameters") == parameters
                    and previous.get("response_sha256") == result_hash
                    and previous.get("request_cache_keys") == receipt["request_cache_keys"]):
                receipt["source_retrieved_at"] = previous.get("source_retrieved_at", "")
        # Keep a deliberately small receipt index. A full index never blocks
        # research or causes eviction/retrieval outside the normal HTTP path.
        if not directory.exists() or path.exists() or len(list(directory.glob("*.json"))) < 256:
            directory.mkdir(parents=True, exist_ok=True)
            write_json(path, receipt)
    except (OSError, ValueError, TypeError):
        receipt["index_status"] = "receipt_not_persisted"
    return receipt

