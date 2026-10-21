"""Explicit, bounded and hash-verified public background packages.

This module performs no network calls and approves no scientific evidence. A
package is only reusable source material; normal span and semantic review apply.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import SourceRecord
from .research_public import public_literature_source


def _sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def _norm(value):
    return re.sub(r"\s+", " ", str(value).casefold()).strip()


def background_task_scope(brief):
    """Bind loaded scope hints to the exact task, independently of its budgets."""
    return _sha(json.dumps({"disease": _norm(brief.disease), "species": _norm(brief.species),
        "candidates": sorted(_norm(n) for n in brief.existing_candidates)}, ensure_ascii=False, sort_keys=True))


@dataclass
class BackgroundPackage:
    sources: list[SourceRecord] = field(default_factory=list)
    identities: list[dict] = field(default_factory=list)
    boundary: dict = field(default_factory=dict)
    receipt: dict = field(default_factory=dict)


def _read_json(base, relative, *, expected_hash="", limit=500000):
    if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
            or ".." in Path(relative).parts):
        raise ValueError("Background package path must be relative")
    path = base / relative
    parts = Path(relative).parts
    chain = [base.parent, base, *(base.joinpath(*parts[:n]) for n in range(1, len(parts) + 1))]
    if (not path.resolve().is_relative_to(base.resolve()) or
            any(p.is_symlink() for p in chain) or
            not path.is_file() or path.stat().st_size > limit):
        raise ValueError("Background package file is missing, unsafe or exceeds size limit")
    raw = path.read_bytes()
    if expected_hash and _sha(raw) != expected_hash:
        raise ValueError("Background package file hash mismatch")
    return json.loads(raw), _sha(raw)


def load_background_package(root: Path, brief) -> BackgroundPackage:
    package_id = brief.background_package_id
    if not package_id:
        return BackgroundPackage()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", package_id):
        raise ValueError("Invalid background package id")
    base = Path(root) / "knowledge" / package_id
    manifest, manifest_hash = _read_json(base, "manifest.json", limit=150000)
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or
            manifest.get("package_id") != package_id or not isinstance(manifest.get("version"), str)):
        raise ValueError("Unsupported background package manifest")
    scope = manifest.get("scope", {})
    if not isinstance(scope, dict):
        raise ValueError("Background package scope is missing")  # noqa: TRY004 -- invalid manifest contract
    disease_names = scope.get("disease_names", [])
    candidate_names = scope.get("candidate_names", [])
    if (not isinstance(disease_names, list) or not isinstance(candidate_names, list) or
            _norm(brief.disease) not in {_norm(s) for s in disease_names if isinstance(s, str)} or
            not brief.existing_candidates or
            not all(_norm(s) in {_norm(n) for n in candidate_names if isinstance(n, str)}
                    for s in brief.existing_candidates)):
        raise ValueError("Background package is incompatible with explicit task disease/candidates")
    species = scope.get("species", [])
    if species and _norm(brief.species) not in {_norm(s) for s in species if isinstance(s, str)}:
        raise ValueError("Background package is incompatible with task species")
    entries = manifest.get("sources", [])
    if not isinstance(entries, list) or len(entries) > 12:
        raise ValueError("Background package exceeds bounded source count")
    package = BackgroundPackage(receipt={"package_id": package_id, "version": manifest["version"],
        "manifest_sha256": manifest_hash, "task_scope_sha256": background_task_scope(brief),
        "records": len(entries), "status": "verified_background_reuse"})
    seen = set()
    # Imported here to avoid a module cycle with ResearchFlow.inventory.
    from .research_flow import public_source
    for entry in entries:
        if not isinstance(entry, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
            raise ValueError("Background source requires a SHA-256 file receipt")
        value, _ = _read_json(base, entry.get("path"), expected_hash=entry["sha256"])
        source = SourceRecord.model_validate(value)
        if not public_source(source) or source.source_id != entry.get("source_id") or source.source_id in seen:
            raise ValueError("Background source is not a unique declared public literature record")
        seen.add(source.source_id)
        windows = entry.get("windows", [])
        if not isinstance(windows, list) or not 1 <= len(windows) <= 8:
            raise ValueError("Background source requires bounded continuous evidence windows")
        for window in windows:
            if (not isinstance(window, dict) or type(window.get("start")) is not int or
                    type(window.get("end")) is not int or
                    not 0 <= window["start"] < window["end"] <= len(source.text) or
                    _sha(source.text[window["start"]:window["end"]]) != window.get("sha256")):
                raise ValueError("Background evidence window offsets/hash mismatch")
        # One original continuous span prevents a quote from bridging omitted
        # text. Multiple declared windows must fit in this same bounded span.
        start, end = min(w["start"] for w in windows), max(w["end"] for w in windows)
        if end - start > 3600:
            raise ValueError("Background continuous excerpt exceeds 3600 characters")
        safe = SourceRecord.model_validate(public_literature_source(source))
        safe.text = source.text[start:end]
        safe.metadata.update(acquisition_origin="background_reuse", background_package_id=package_id,
            background_version=manifest["version"], background_manifest_sha256=manifest_hash,
            original_text_sha256=_sha(source.text), original_evidence_scope=source.evidence_scope,
            local_excerpt_only=True, excerpt_start=start, excerpt_end=end, excerpt_sha256=_sha(safe.text),
            excerpt_location="; ".join(str(w.get("location", ""))[:200] for w in windows),
            excerpt_selection="manifest_verified_continuous_span",
            inclusion_reason=str(entry.get("inclusion_reason", ""))[:1000],
            evidence_level=str(entry.get("evidence_level", ""))[:200])
        # Never label an excerpt of a full paper as full-text access in this run.
        if source.evidence_scope == "full_text":
            safe.evidence_scope = "abstract"
        package.sources.append(safe)
    for key, attr in (("identity_table", "identities"), ("project_boundary", "boundary")):
        entry = manifest.get(key)
        if entry is None:
            continue
        if not isinstance(entry, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
            raise ValueError("Background ancillary record requires a SHA-256 file receipt")
        value, _ = _read_json(base, entry.get("path"), expected_hash=entry["sha256"])
        if not isinstance(value, dict):
            raise ValueError("Background ancillary record must be an object")  # noqa: TRY004 -- invalid manifest contract
        if key == "identity_table":
            records = value.get("records", [])
            if value.get("schema_version") != 1 or not isinstance(records, list) or len(records) > 10 or not all(isinstance(r, dict) for r in records):
                raise ValueError("Background identity table exceeds bounded schema")
            value = records
        setattr(package, attr, value)
    return package


def relevant_excerpt(source, terms=(), *, limit=1800):
    """Choose one verbatim window, favoring result and contrary clauses together.

    This is source selection, not study adjudication. No sentences are rewritten
    or joined. The original range and text hash permit exact source inspection.
    """
    text = source.text
    if len(text) <= limit:
        return 0, len(text)
    task_terms = [_norm(t) for t in terms if isinstance(t, str) and len(t.strip()) >= 3]
    markers = re.compile(r"\b(?:results?|conclusions?|no (?:significant |statistically significant )?(?:difference|effect|improvement)|adverse|placebo|mixed|failed|not significant)\b|结果|结论|无显著|无统计学", re.IGNORECASE)
    # Window endpoints include the entire nearby result paragraph where space
    # permits, so a null primary endpoint is not lost to the abstract preamble.
    starts = {0, len(text) - limit}
    for match in markers.finditer(text):
        starts.add(max(0, min(len(text) - limit, match.start() - limit // 3)))
    def score(start):
        snippet = text[start:start + limit]
        normalized = _norm(snippet)
        return (sum(bool(re.search(re.escape(t), normalized)) for t in task_terms) * 3 +
                sum(2 if m.group().casefold() in {"result", "results", "conclusion", "conclusions"} else 1
                    for m in markers.finditer(snippet)))
    start = max(sorted(starts), key=score)
    return start, start + limit

