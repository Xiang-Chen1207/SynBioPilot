"""Content-free provider failures; values as well as keys are allowlisted."""
from __future__ import annotations

import math

CATEGORIES = frozenset({"launch_error", "process_nonzero", "answer_missing", "answer_empty",
                        "timeout", "json_invalid", "schema_invalid", "read_error"})
STAGES = frozenset({"executable_lookup", "prepare", "process", "output_stat", "output_read",
                    "json_parse", "schema_validation"})
BACKENDS = frozenset({"codex", "responses", "compatible"})
STATUSES = frozenset({"not_checked", "observed", "missing", "error"})
MESSAGES = {
    "launch_error": "Model invocation could not be launched",
    "process_nonzero": "Codex process exited unsuccessfully",
    "answer_missing": "Codex answer file is missing",
    "answer_empty": "Model answer is empty",
    "timeout": "Model invocation timed out",
    "json_invalid": "Model output is not valid JSON",
    "schema_invalid": "Model output failed schema validation",
    "read_error": "Model answer metadata or content could not be read",
}


def public_provider_failure(value):
    """Do not infer missing historical facts or pass arbitrary diagnostic text."""
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
        return {}
    for key, choices in (("category", CATEGORIES), ("stage", STAGES),
                         ("backend", BACKENDS), ("origin", {"provider_call"})):
        if not isinstance(value.get(key), str) or value[key] not in choices:
            return {}
    result = {key: value[key] for key in ("version", "category", "stage", "backend", "origin")}
    elapsed = value.get("elapsed_seconds")
    if type(elapsed) in (int, float) and 0 <= elapsed <= 10**9 and math.isfinite(elapsed):
        result["elapsed_seconds"] = elapsed
    for key in ("output_exists", "output_is_file"):
        if key in value and (value[key] is None or type(value[key]) is bool):
            result[key] = value[key]
    for key, low, high in (("returncode", -(2**31), 2**31), ("output_size_bytes", 0, 10**18)):
        item = value.get(key)
        if key in value and (item is None or type(item) is int and low <= item < high):
            result[key] = item
    signal = value.get("signal")
    if "signal" in value and (signal is None or type(signal) is int and 1 <= signal <= 127
                              and type(result.get("returncode")) is int and result["returncode"] == -signal):
        result["signal"] = signal
    status = value.get("output_stat_status")
    if isinstance(status, str) and status in STATUSES:
        result["output_stat_status"] = status
    return result

