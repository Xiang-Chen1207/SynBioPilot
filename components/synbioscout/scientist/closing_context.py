"""Lossless scientific references for citation-level Meta closing only.

Formal review must continue to receive its full original source windows. This
encoding omits only their repeated raw text at closing and retains window audit
coordinates/hashes. Every accepted quote, scope, negative/unknown statement,
identity and exact hypothesis version otherwise has a reversible representation.
"""
from __future__ import annotations

import copy
import hashlib
import json

_ENCODING = "citation_level_closing_v1"
_SCOPE_FIELDS = ("source_id", "study_system", "condition", "endpoint", "polarity")
_GAP_FIELDS = frozenset({"inference_gaps", "gaps", "required_evidence", "objections", "uncertainties",
                         "remaining_unknowns", "limitations", "historical_identity_gaps"})
_CLAIM_FIELDS = {"claim_id": "claim_id", "hypothesis_id": "hypothesis_id", "version": "version",
                 "claim_kind": "kind", "relation_schema_version": "relation_schema_version",
                 "assessment_scope": "assessment_scope"}


def _same(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def _unique(rows, key):
    result, duplicates = {}, set()
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and key in row:
            value = row[key]
            if not isinstance(value, (str, int)):
                continue
            if value in result:
                duplicates.add(value)
            result[value] = row
    return {key: value for key, value in result.items() if key not in duplicates}


def _table(rows):
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        return rows
    columns = list(dict.fromkeys(key for row in rows for key in row))
    constants = {key: rows[0][key] for key in columns if key in rows[0]
                 and all(key in row and _same(row[key], rows[0][key]) for row in rows)}
    columns = [key for key in columns if key not in constants]
    aliases, kept = {}, []
    for key in columns:
        same = next((other for other in kept if all(key in row and other in row and _same(row[key], row[other]) for row in rows)), None)
        if same is None:
            kept.append(key)
        else:
            aliases[key] = same
    columns = kept
    # Missing cells are distinct from a present null value. Their positions are
    # retained only where needed, rather than filling absent scientific fields.
    missing = [[index for index, key in enumerate(columns) if key not in row] for row in rows]
    table = {"context_table": 1, "columns": columns, "rows": [[row.get(key) for key in columns] for row in rows]}
    if constants:
        table["constants"] = constants
    if aliases:
        table["alias_columns"] = aliases
    if any(missing):
        table["missing_cells"] = missing
    return table


def _untable(value):
    if not isinstance(value, dict) or value.get("context_table") != 1:
        return value
    missing = value.get("missing_cells", [[] for _ in value["rows"]])
    rows = [{**copy.deepcopy(value.get("constants", {})),
             **{key: item for index, (key, item) in enumerate(zip(value["columns"], row, strict=True))
             if index not in missing[row_index]}} for row_index, row in enumerate(value["rows"])]
    for row in rows:
        row.update({key: copy.deepcopy(row[other]) for key, other in value.get("alias_columns", {}).items()})
    return rows


def compact_closing_context(context):
    """Encode references without mutating input or changing any scientific verdict."""
    if context.get("context_encoding", {}).get("name") == _ENCODING:
        return copy.deepcopy(context)
    result = copy.deepcopy(context)
    evidence = _unique(result.get("accepted_evidence", []), "evidence_id")
    index = _unique(result.get("global_accepted_evidence_index", []), "evidence_id")
    sources = _unique(result.get("sources", []), "source_id")
    landscape = result.get("literature_landscape")
    claims = _unique(landscape.get("claims", []) if isinstance(landscape, dict) else [], "claim_id")
    cards = {(h["hypothesis_id"], h["version"]): h for h in result.get("hypotheses", [])
             if isinstance(h, dict) and "hypothesis_id" in h and "version" in h}
    associations = result.get("reviewed_evidence_associations", [])
    original_associations = copy.deepcopy(associations)
    review_fields = None
    for entry in result.get("global_accepted_evidence_index", []):
        reviews = entry.get("claim_reviews", [])
        refs = []
        for review in reviews:
            matches = [i for i, row in enumerate(original_associations)
                if row.get("evidence_id") == entry.get("evidence_id") and row.get("source_id") == entry.get("source_id")
                and all(key in row and _same(row[key], value) for key, value in review.items())]
            if len(matches) != 1:
                break
            refs.append({"row": matches[0], "fields": list(review)})
        else:
            if refs:
                entry.pop("claim_reviews")
                fields = refs[0]["fields"]
                if all(ref["fields"] == fields for ref in refs) and (review_fields is None or review_fields == fields):
                    review_fields = fields
                    entry["claim_review_refs"] = [ref["row"] for ref in refs]
                else:
                    entry["claim_review_refs"] = refs
    for row in result.get("accepted_evidence", []):
        scope = index.get(row.get("evidence_id"))
        if scope and all(key in row and key in scope and _same(row[key], scope[key]) for key in _SCOPE_FIELDS):
            for key in _SCOPE_FIELDS:
                row.pop(key)
            row["scope_ref"] = row["evidence_id"]
    for row in associations:
        scope = index.get(row.get("evidence_id"))
        if scope and all(key in row and _same(row[key], scope.get(key)) for key in ("source_id", "polarity")):
            row.pop("source_id")
            row.pop("polarity")
            row["evidence_scope_ref"] = row["evidence_id"]
        claim = claims.get(row.get("claim_id"))
        if claim and all(key in row and field in claim and _same(row[key], claim[field]) for key, field in _CLAIM_FIELDS.items()):
            row["claim_ref"] = row["claim_id"]
            for key in _CLAIM_FIELDS:
                row.pop(key)
    for claim in claims.values():
        card = cards.get((claim.get("hypothesis_id"), claim.get("version")))
        if not card or not isinstance(claim.get("text"), str):
            continue
        matched = next((key for key, value in card.items() if isinstance(value, str) and value == claim["text"]), None)
        if matched:
            claim.pop("text")
            claim["text_ref"] = {"hypothesis_id": card["hypothesis_id"], "version": card["version"], "field": matched}
        else:
            tokens = [(value, {"hypothesis_field": key}) for key, value in card.items()
                      if isinstance(value, str) and len(value.encode()) >= 70]
            tokens += [(value, {"anchor_field": key}) for key, value in result.get("anchor", {}).items()
                       if isinstance(value, str) and len(value.encode()) >= 70]
            text, parts, start = claim["text"], [], 0
            while start < len(text):
                hits = [(text.find(token, start), token, ref) for token, ref in tokens if token in text[start:]]
                if not hits:
                    parts.append(text[start:])
                    break
                position, token, ref = min(hits, key=lambda hit: (hit[0], -len(hit[1])))
                if position > start:
                    parts.append(text[start:position])
                parts.append(ref)
                start = position + len(token)
            if any(isinstance(part, dict) for part in parts) and len(json.dumps(parts, ensure_ascii=False).encode()) < len(text.encode()):
                claim.pop("text")
                claim["text_parts"] = parts
    candidates = _unique(result.get("candidates", []), "entity_id")
    for reference in result.get("entity_references", []):
        candidate = candidates.get(reference.get("entity_id"))
        if not candidate:
            continue
        fields = [key for key in reference if key != "entity_id" and key in candidate and _same(reference[key], candidate[key])]
        if fields:
            reference["candidate_ref"] = reference["entity_id"]
            reference["candidate_fields"] = fields
            for key in fields:
                reference.pop(key)
    # Gap strings are preserved exactly, including negative/unknown statements.
    # Only exact known quote/URL substrings can be replaced by typed references.
    quote_candidates = [(row["quote"], {"quote_ref": eid}) for eid, row in evidence.items()
                        if isinstance(row.get("quote"), str) and row["quote"]]
    url_candidates = [(row["url"], {"source_url_ref": sid}) for sid, row in sources.items()
                      if isinstance(row.get("url"), str) and row["url"]]
    tokens = [(text, ref) for text, ref in quote_candidates + url_candidates
              if sum(text == other for other, _ in quote_candidates + url_candidates) == 1]
    tokens.sort(key=lambda pair: len(pair[0]), reverse=True)
    def gap_value(text):
        segments, start = [], 0
        while start < len(text):
            hits = [(text.find(token, start), token, reference) for token, reference in tokens if token in text[start:]]
            if not hits:
                segments.append(text[start:])
                break
            position, token, reference = min(hits, key=lambda hit: (hit[0], -len(hit[1])))
            if position > start:
                segments.append(text[start:position])
            segments.append(reference)
            start = position + len(token)
        encoded = {"segments": segments}
        has_quote_ref = any(isinstance(s, dict) and "quote_ref" in s for s in segments)
        if not has_quote_ref and len(json.dumps(encoded, ensure_ascii=False).encode()) >= len(json.dumps(text, ensure_ascii=False).encode()):
            return text
        return encoded if any(isinstance(s, dict) for s in segments) else text
    gaps, gap_lookup = [], {}
    def pool(value):
        if isinstance(value, list):
            for item in value:
                pool(item)
        elif isinstance(value, dict):
            for key, item in list(value.items()):
                if key in _GAP_FIELDS and isinstance(item, list) and item and all(isinstance(v, str) for v in item):
                    refs = []
                    for text in item:
                        if text not in gap_lookup:
                            gap_lookup[text] = len(gaps)
                            gaps.append(gap_value(text))
                        refs.append(gap_lookup[text])
                    value.pop(key)
                    value.setdefault("gap_refs", {})[key] = refs
                else:
                    pool(item)
    pool(result)
    scopes, window_audit_input = [], []
    for source in result.get("sources", []):
        if "windows" in source:
            windows = source.pop("windows")
            window_audit_input.append({"source_id": source["source_id"], "windows": windows})
            source["source_window_audit"] = [{key: value for key, value in window.items() if key != "text"} for window in windows]
        scope = source.get("source_scope")
        if isinstance(scope, dict):
            expected = {"scope": scope.get("current_scope"), "original_scope": scope.get("original_scope"),
                "acquisition_origin": scope.get("acquisition_origin"),
                "original_excerpt_start": (scope.get("original_window") or {}).get("start")}
            fields = [key for key, value in expected.items() if key in source and _same(source[key], value)]
            for key in fields:
                source.pop(key)
            source["source_scope_ref"] = len(scopes)
            source["scope_fields_from_ref"] = True if fields == list(expected) else fields
            scopes.append(source.pop("source_scope"))
    result["gap_texts"] = gaps
    result["source_scope_table"] = _table(scopes)
    result["sources"] = _table(result.get("sources", []))
    if isinstance(landscape, dict):
        landscape["claims"] = _table(landscape.get("claims", []))
    result["reviewed_evidence_associations"] = _table(associations)
    result["global_accepted_evidence_index"] = _table(result.get("global_accepted_evidence_index", []))
    result["context_encoding"] = {
        "name": _ENCODING,
        "rules": (
            "Meta closes reviewed quotes, not sources. Tables zip columns/rows; constants/alias_columns restore repeats; missing_cells=absent. "
            "scope_ref=global evidence; claim_ref=exact-version claim; source_scope_ref=scope table. "
            "Review refs select association rows/review_ref_fields; candidate_ref keeps the same entity. "
            "gap_refs index gap_texts; join segments/text_parts using literal strings and explicit quote/URL/card/anchor refs. "
            "Quotes intact; raw windows local, bounds/hash audited. Never infer omitted text."),
        "review_ref_fields": review_fields or [],
    }
    if window_audit_input:
        result["context_encoding"]["source_windows_sha256"] = hashlib.sha256(json.dumps(
            window_audit_input, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return result


def expand_closing_context(context, *, source_windows=None):
    """Reverse references exactly; optional keyed windows restore the audit-only omission.

    source_windows maps source_id to the original windows. Without it, omitted
    raw window text remains absent rather than being invented from quotes.
    """
    result = copy.deepcopy(context)
    if result.get("context_encoding", {}).get("name") != _ENCODING:
        return result
    encoding = result.pop("context_encoding")
    result["sources"] = _untable(result["sources"])
    scopes = _untable(result.pop("source_scope_table"))
    for source in result["sources"]:
        if "source_scope_ref" in source:
            scope = scopes[source.pop("source_scope_ref")]
            source["source_scope"] = copy.deepcopy(scope)
            values = {"scope": scope.get("current_scope"), "original_scope": scope.get("original_scope"),
                "acquisition_origin": scope.get("acquisition_origin"),
                "original_excerpt_start": (scope.get("original_window") or {}).get("start")}
            fields = source.pop("scope_fields_from_ref")
            source.update(values if fields is True else {key: values[key] for key in fields})
    for key in ("global_accepted_evidence_index", "reviewed_evidence_associations"):
        result[key] = _untable(result[key])
    landscape = result.get("literature_landscape")
    if isinstance(landscape, dict):
        landscape["claims"] = _untable(landscape["claims"])
    index = _unique(result.get("global_accepted_evidence_index", []), "evidence_id")
    evidence = _unique(result.get("accepted_evidence", []), "evidence_id")
    claims = _unique(landscape.get("claims", []) if isinstance(landscape, dict) else [], "claim_id")
    cards = {(h["hypothesis_id"], h["version"]): h for h in result.get("hypotheses", [])}
    for row in result.get("accepted_evidence", []):
        if "scope_ref" in row:
            scope = index[row.pop("scope_ref")]
            row.update({key: scope[key] for key in _SCOPE_FIELDS})
    for claim in claims.values():
        if "text_ref" in claim:
            ref = claim.pop("text_ref")
            claim["text"] = cards[(ref["hypothesis_id"], ref["version"])][ref["field"]]
        elif "text_parts" in claim:
            card = cards[(claim["hypothesis_id"], claim["version"])]
            claim["text"] = "".join(part if isinstance(part, str) else card[part["hypothesis_field"]]
                if "hypothesis_field" in part else result["anchor"][part["anchor_field"]] for part in claim.pop("text_parts"))
    for row in result.get("reviewed_evidence_associations", []):
        if "claim_ref" in row:
            claim = claims[row.pop("claim_ref")]
            row.update({key: claim[field] for key, field in _CLAIM_FIELDS.items()})
        if "evidence_scope_ref" in row:
            scope = index[row.pop("evidence_scope_ref")]
            row.update({key: scope[key] for key in ("source_id", "polarity")})
    for row in result.get("global_accepted_evidence_index", []):
        if "claim_review_refs" in row:
            row["claim_reviews"] = [{key: result["reviewed_evidence_associations"][ref if type(ref) is int else ref["row"]][key]
                for key in encoding["review_ref_fields"] if type(ref) is int} if type(ref) is int else
                {key: result["reviewed_evidence_associations"][ref["row"]][key] for key in ref["fields"]}
                for ref in row.pop("claim_review_refs")]
    sources = _unique(result.get("sources", []), "source_id")
    gaps = []
    for gap in result.pop("gap_texts"):
        if isinstance(gap, str):
            gaps.append(gap)
        else:
            gaps.append("".join(segment if isinstance(segment, str) else
                evidence[segment["quote_ref"]]["quote"] if "quote_ref" in segment else
                sources[segment["source_url_ref"]]["url"] for segment in gap["segments"]))
    def unpool(value):
        if isinstance(value, list):
            for item in value:
                unpool(item)
        elif isinstance(value, dict):
            for key, refs in value.pop("gap_refs", {}).items():
                value[key] = [gaps[index] for index in refs]
            for item in value.values():
                unpool(item)
    unpool(result)
    candidates = _unique(result.get("candidates", []), "entity_id")
    for row in result.get("entity_references", []):
        if "candidate_ref" in row:
            candidate = candidates[row.pop("candidate_ref")]
            row.update({key: candidate[key] for key in row.pop("candidate_fields")})
    if source_windows is not None and "source_windows_sha256" in encoding:
        restored_windows = [{"source_id": row["source_id"], "windows": source_windows[row["source_id"]]}
                            for row in result.get("sources", []) if "source_window_audit" in row]
        checksum = hashlib.sha256(json.dumps(restored_windows, sort_keys=True, ensure_ascii=False,
                                           separators=(",", ":")).encode()).hexdigest()
        if checksum != encoding["source_windows_sha256"]:
            raise ValueError("Source window audit mismatch")
    for row in result.get("sources", []):
        if "source_window_audit" in row:
            audit = row.pop("source_window_audit")
            if source_windows is not None:
                windows = source_windows[row["source_id"]]
                expected = [{key: value for key, value in window.items() if key != "text"} for window in windows]
                if expected != audit:
                    raise ValueError("Source window audit mismatch")
                row["windows"] = copy.deepcopy(windows)
            else:
                row["windows"] = []
                row["source_window_audit"] = audit
    return result

