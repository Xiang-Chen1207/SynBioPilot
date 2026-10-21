"""Pure, conservative presentation of stored source scope and acquisition facts.

The original SourceRecord is never rewritten. Window coordinates are half-open
Unicode character offsets into the original available text, not byte counts.
Missing legacy acquisition facts stay unknown; a URL or date is not proof that
this run downloaded or read a full paper.
"""
from __future__ import annotations

import math

_SCOPES = frozenset({"abstract", "full_text", "database", "local_document"})
_REUSED = frozenset({"background_reuse", "saved_reuse", "library_reuse", "background_identity_reuse",
                     "cache_reuse", "exact_query_http_cache", "http_cache"})
_NEW = frozenset({"current_run_retrieval", "current_retrieval", "live_response",
                  "current_run_full_text", "live_full_text_response"})
_NEW_FULL_TEXT = frozenset({"current_run_full_text", "live_full_text_response"})
_FULL_TEXT_OPERATIONS = frozenset({"full_text", "full_text_retrieval", "full_text_download"})
_MAX_WINDOW_OFFSET = min(10**12, 2**53 - 1)


def _window_offset(value):
    # JSON has a single number type. Accept exact integral JSON numbers in both
    # Python and JavaScript, without accepting bool, fractions or unsafe ranges.
    if type(value) is int:
        return value if 0 <= value <= _MAX_WINDOW_OFFSET else None
    if type(value) is float and math.isfinite(value) and value.is_integer() and 0 <= value <= _MAX_WINDOW_OFFSET:
        return int(value)
    return None


def source_scope_view(source):
    """Return safe scope facts for a SourceRecord or its dictionary projection."""
    row = source if isinstance(source, dict) else {
        "evidence_scope": getattr(source, "evidence_scope", None),
        "metadata": getattr(source, "metadata", None), "text": getattr(source, "text", None)}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    stored = row.get("evidence_scope")
    stored = stored if isinstance(stored, str) and stored in _SCOPES else "unknown"
    original = metadata.get("original_evidence_scope", stored)
    original = original if isinstance(original, str) and original in _SCOPES else "unknown"
    excerpt = metadata.get("local_excerpt_only") is True
    start, end = _window_offset(metadata.get("excerpt_start")), _window_offset(metadata.get("excerpt_end"))
    text = row.get("text")
    # Validate against the actual current text only. Empty/export-omitted text
    # cannot verify a window; cached scope/length/verified metadata is not proof.
    window = {"start": start, "end": end} if (excerpt and start is not None and end is not None
        and start < end and isinstance(text, str) and bool(text) and end - start == len(text)) else None
    origin = next((metadata[key] for key in ("acquisition_origin", "acquisition", "origin")
                   if isinstance(metadata.get(key), str) and metadata[key] in _REUSED | _NEW), "unknown")
    receipt = metadata.get("query_receipt") if isinstance(metadata.get("query_receipt"), dict) else {}
    full_receipt = metadata.get("full_text_query_receipt")
    if not isinstance(full_receipt, dict):
        operation = receipt.get("operation")
        full_receipt = receipt if isinstance(operation, str) and operation in _FULL_TEXT_OPERATIONS else {}
    full_origin = full_receipt.get("cache_origin")
    if origin in _REUSED:
        acquisition = "reused"
    elif full_origin == "live_response":
        acquisition, origin = "newly_acquired", "live_full_text_response"
    elif full_origin == "exact_query_http_cache" or receipt.get("cache_origin") == "exact_query_http_cache" and origin not in _NEW_FULL_TEXT:
        acquisition, origin = "reused", "exact_query_http_cache"
    elif origin in _NEW:
        acquisition = "newly_acquired"
    elif receipt.get("cache_origin") == "live_response":
        acquisition, origin = "newly_acquired", "live_response"
    else:
        acquisition = "unknown"
    current = "local_excerpt" if excerpt else stored
    return {"original_scope": original, "current_scope": current, "local_excerpt": excerpt,
        "original_window": window, "acquisition": acquisition, "acquisition_origin": origin,
        "fresh_fulltext": current == "full_text" and acquisition == "newly_acquired" and origin in _NEW_FULL_TEXT}

