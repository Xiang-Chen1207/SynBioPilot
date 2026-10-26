"""Bounded core entity preparation; no scientific conclusions or implicit library use.

The caller supplies only an explicitly enabled, manifest-checked background table.
Public identity documents are verified again against their exact structure and name
query. Network fallback uses the run ledger's HTTP allowance; its LLM input-byte usage is zero.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from urllib.parse import unquote, urlsplit

from .identity import _aliases, _exact, declared_aliases
from .models import CandidateNameDeclaration, CandidateRecord, ExecutionStatus, SourceRecord
from .store import digest
from .tools import ToolRequest, ToolResult


def _official_document(value, checksum):
    source = SourceRecord.model_validate(value)
    url = urlsplit(source.url)
    if (source.provider != "pubchem" or source.evidence_scope != "database" or
            url.scheme != "https" or url.hostname != "pubchem.ncbi.nlm.nih.gov" or
            url.username or url.password or url.query or url.fragment or url.port):
        raise ValueError("Official PubChem document required")
    datetime.fromisoformat(source.retrieved_at)
    if hashlib.sha256(source.text.encode()).hexdigest() != checksum:
        raise ValueError("Background identity source checksum mismatch")
    return source, json.loads(source.text), unquote(url.path)


def verified_background_identity(row):
    """Return a verified single identity, never infer salts/stereochemistry/aliases."""
    from rdkit import Chem

    source, data, path = _official_document(row["source"], row["source_sha256"])
    canonical_name = row["canonical_name"]
    prefix = "/rest/pug/compound/name/"
    if (not isinstance(canonical_name, str) or not canonical_name or not path.startswith(prefix) or
            "/property/" not in path or not path.endswith("/JSON") or
            _exact(path[len(prefix):].split("/property/", 1)[0]) != _exact(canonical_name)):
        raise ValueError("Background identity must retain its exact official name query")
    properties = data["PropertyTable"]["Properties"]
    if not isinstance(properties, list) or len(properties) != 1:
        raise ValueError("Background identity is not unique")
    prop = properties[0]
    if not isinstance(prop, dict):
        raise ValueError("Background identity property is malformed")  # noqa: TRY004 -- invalid public document contract
    cid = str(prop["CID"])
    smiles = prop.get("SMILES") or prop.get("IsomericSMILES") or prop.get("CanonicalSMILES")
    molecule = Chem.MolFromSmiles(smiles or "")
    if not molecule or not molecule.GetNumAtoms() or not cid.isdigit() or int(cid) <= 0:
        raise ValueError("Background identity has no valid structure")
    canonical = Chem.MolToSmiles(molecule, isomericSmiles=True)
    key = Chem.MolToInchiKey(molecule)
    if (key != prop.get("InChIKey") or key != row.get("inchikey") or
            canonical != row.get("smiles") or cid != row.get("pubchem_cid") or
            source.source_id != "pubchem:" + cid):
        raise ValueError("Background identity structure/CID/stereochemistry mismatch")
    aliases = row.get("aliases", [])
    if not isinstance(aliases, list) or any(not isinstance(a, str) or not a for a in aliases):
        raise ValueError("Invalid identity aliases")
    alias_sources = []
    if aliases:
        alias_source, alias_data, alias_path = _official_document(
            row["alias_source"], row["alias_source_sha256"])
        if alias_path != f"/rest/pug/compound/cid/{cid}/synonyms/JSON":
            raise ValueError("Identity aliases require the same PubChem CID")
        entries = alias_data["InformationList"]["Information"]
        if (not isinstance(entries, list) or len(entries) != 1 or
                not isinstance(entries[0], dict) or str(entries[0].get("CID")) != cid):
            raise ValueError("Identity alias source is ambiguous")
        provider_aliases = entries[0].get("Synonym", [])
        if (not isinstance(provider_aliases, list) or any(not isinstance(a, str) for a in provider_aliases) or
                not {_exact(a) for a in aliases}.issubset({_exact(a) for a in provider_aliases})):
            raise ValueError("Identity alias is not confirmed by its official source")
        alias_sources.append(alias_source)
    return {"candidate_id": "mol_" + digest([canonical, key])[:16], "smiles": canonical,
            "inchikey": key, "pubchem_cid": cid}, source, alias_sources


def ensure_specified_candidates(run):
    """Prepare identity records for every candidate-assessment input, including names awaiting synthesis."""
    if run.status not in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}:
        return []
    declarations = []
    seen = set()
    for index, name in enumerate(run.brief.existing_candidates):
        parts = declared_aliases(name)
        full, abbreviation = (parts[0], parts[1]) if parts else (name, "")
        key = (_exact(full), _exact(abbreviation))
        if key in seen:
            continue
        seen.add(key)
        declarations.append(CandidateNameDeclaration(label=name, full_name=full, abbreviation=abbreviation,
            source_index=index, run_id=run.run_id))
    # Freeze every declaration/candidate match before adding any names. Otherwise
    # the first of two full names sharing an acronym could win by input order.
    matches = [[c for c in run.candidates if {_exact(d.label), _exact(d.full_name), _exact(d.abbreviation)}
                .difference({""}).intersection(map(_exact, _aliases(c)))] for d in declarations]
    candidate_full_names = {id(c): {_exact(d.full_name) for d, linked in zip(declarations, matches, strict=True)
                                   if any(c is item for item in linked)} for c in run.candidates}
    selected = []
    created = {}
    for declaration, linked in zip(declarations, matches, strict=True):
        different_explicit_name = any(
            declared_aliases(c.name) and
            _exact(declared_aliases(c.name)[0]) != _exact(declaration.full_name) and
            _exact(declaration.full_name) not in {_exact(alias) for alias in c.aliases}
            for c in linked)
        if len(linked) > 1 or any(len(candidate_full_names[id(c)]) > 1 for c in linked) or different_explicit_name:
            for candidate in linked:
                if candidate.identity_status != "resolved":
                    candidate.identity_status = "needs_review"
                _mark(candidate, run, "ambiguous_user_name_declaration")
            continue
        if not linked:
            candidate = created.get(_exact(declaration.full_name))
            if candidate is None:
                candidate = CandidateRecord(candidate_id="unresolved_" + digest(declaration.label)[:12], name=declaration.label,
                    routes=["user_supplied"], decision="hold",
                    role="unknown", role_status="needs_review", identity_status="needs_review",
                    rationale="Assessment input retained with its candidate origin; evidence review follows",
                    uncertainties=["Assessment input awaits evidence synthesis"])
                run.candidates.append(candidate)
                created[_exact(declaration.full_name)] = candidate
            linked = [candidate]
        candidate = linked[0]
        if declaration not in candidate.user_name_declarations:
            candidate.user_name_declarations.append(declaration)
        if "user_supplied" not in candidate.routes:
            candidate.routes.append("user_supplied")
        if not any(c is candidate for c in selected):
            selected.append(candidate)
    return selected


def _mark(candidate, run, status, **details):
    item = {"core_preparation": True, "run_id": run.run_id, "origin": "core_identity",
            "status": status, "application_status": status, **details}
    if status in {"applied", "unchanged"} and candidate.identity_status == "resolved":
        from .assessment_review import _identity_fingerprint
        item["identity_fingerprint"] = _identity_fingerprint(candidate)
        item["source_fingerprints"] = _identity_source_fingerprints(run, candidate)
    # Re-entering preparation must not manufacture additional observations.
    if item not in candidate.identity_history:
        candidate.identity_history.append(item)


def _identity_source_fingerprints(run, candidate):
    rows = {}
    for sid in candidate.identity_source_ids:
        matches = [s for s in run.sources if s.source_id == sid]
        if len(matches) != 1:
            return {}
        source = matches[0]
        rows[sid] = digest({k: getattr(source, k) for k in (
            "source_id", "provider", "url", "evidence_scope", "retrieved_at", "text")})
    return rows


def _prepared_identity_current(run, candidate):
    from .assessment_review import _identity_fingerprint
    if candidate.identity_status != "resolved" or not all((candidate.smiles, candidate.inchikey,
            candidate.pubchem_cid, candidate.entity_id, candidate.identity_revision)):
        return False
    sources = _identity_source_fingerprints(run, candidate)
    return bool(sources) and any(row.get("core_preparation") and row.get("run_id") == run.run_id
        and row.get("status") in {"applied", "unchanged"}
        and row.get("identity_fingerprint") == _identity_fingerprint(candidate)
        and row.get("source_fingerprints") == sources for row in candidate.identity_history)


def core_identity_ready(run, candidate_names=None):
    """Only this run's actual, still-current identity completion removes time."""
    names = run.brief.existing_candidates if candidate_names is None else candidate_names
    if not names:
        return False
    matched = []
    for name in names:
        matches = [c for c in run.candidates if _exact(name) in {_exact(a) for a in _aliases(c)}]
        if len(matches) != 1 or not _prepared_identity_current(run, matches[0]):
            return False
        matched.append(matches[0].entity_id)
    return len(matched) == len(set(matched))


def reuse_prepared_candidates(run, prepared):
    """Keep verified identity state while replacing science with the new review."""
    if run.status not in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}:
        return
    incoming = list(run.candidates)
    matches = [[old for old in prepared if _exact(new.name) in {_exact(a) for a in _aliases(old)}]
               for new in incoming]
    used = set()
    result = []
    for new, linked in zip(incoming, matches, strict=True):
        if len(linked) == 1 and sum(any(old is linked[0] for old in row) for row in matches) == 1:
            old = linked[0]
            merged = old.model_copy(deep=True)
            for field in ("evidence_ids", "target_relations", "role", "role_reason", "role_evidence_ids",
                          "role_status", "rationale", "uncertainties"):
                setattr(merged, field, getattr(new, field))
            merged.routes = list(dict.fromkeys([*old.routes, *new.routes]))
            merged.original_ids = list(dict.fromkeys([*old.original_ids, new.candidate_id]))
            if merged.identity_assessment_pending:
                from .evidence import candidate_assessment_fingerprint
                # Attaching an early identity to new reviewed science must not
                # itself reinterpret the identity-only priority hold.
                merged.identity_assessment_fingerprint = candidate_assessment_fingerprint(merged, run.evidence)
            used.add(id(old))
            result.append(merged)
        else:
            result.append(new)
    result.extend(old for old in prepared if id(old) not in used)
    run.candidates = result


def prepare_core_identities(engine, identity_records=(), *, local_only=False):
    """Prepare names before Generation, within four queries/HTTPs and 60 seconds.

    These upper shares are reserved within the existing allowances. HTTP retry
    attempts also consume the share. Time for critical review is protected.
    Zero-byte identity preparation remains eligible independently of the
    serialized review estimate. Explicit conflicts/ambiguities stop that candidate's automatic lookup.
    """
    run, ledger, sync = engine.run, engine.ledger, engine.identity_sync
    if run.status not in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}:
        return
    specified = ensure_specified_candidates(run)
    candidates = specified + [c for c in run.candidates if not any(c is s for s in specified)]
    invalidated = False
    for candidate in candidates:
        prior = [row for row in candidate.identity_history if row.get("core_preparation")
                 and row.get("run_id") == run.run_id and row.get("status") in {"applied", "unchanged"}
                 and row.get("identity_fingerprint")]
        if prior and not _prepared_identity_current(run, candidate):
            before = (candidate.identity_status, len(candidate.identity_history))
            candidate.identity_status = "needs_review"
            _mark(candidate, run, "prepared_identity_changed")
            invalidated = invalidated or before != (candidate.identity_status, len(candidate.identity_history))
    candidates = [c for c in candidates if not any(o.get("core_preparation") and o.get("run_id") == run.run_id
                                                  for o in c.identity_history)]
    if not candidates:
        if invalidated:
            engine.persist()
        return
    tool_start, http_start = ledger.counts["tool_calls"], ledger.counts["http_attempts"]
    tool_share = min(4, max(1, run.brief.budget.max_tool_calls // 3)) if run.brief.budget.max_tool_calls else 0
    http_share = min(4, max(1, run.brief.budget.max_http_requests // 4))
    # critical_reserve is task-aware: explicit candidate assessment protects
    # its independent conclusion review; hypothesis/Meta expansion has its own selection.
    reserve_seconds = engine.critical_reserve().get("seconds", 0)
    core_deadline = min(time.monotonic() + 60, ledger.deadline - reserve_seconds)
    http = getattr(engine.sources, "http", None)
    old_deadline = getattr(http, "deadline", None)
    old_before = getattr(http, "before_request", None)
    package = getattr(engine, "background_package", None)
    package_receipt = getattr(package, "receipt", {}) or {}

    def before_request():
        if ledger.counts["http_attempts"] - http_start >= http_share:
            raise TimeoutError("Core identity HTTP allowance reached")
        if time.monotonic() >= core_deadline:
            raise TimeoutError("Core identity time allowance reached")
        if old_before:
            old_before()
        else:
            ledger.request()

    if http is not None:
        http.before_request = before_request
        http.deadline = min(old_deadline, core_deadline) if old_deadline is not None else core_deadline
    try:
        for candidate in candidates:
            ledger.check()
            if candidate.role == "compound_class":
                candidate.identity_status = "not_single_compound"
                _mark(candidate, run, "not_single_compound")
                continue
            if candidate.identity_status == "resolved":
                _mark(candidate, run, "already_resolved")
                continue
            matches = []
            for row in identity_records:
                if not isinstance(row, dict):
                    continue
                aliases = row.get("aliases", [])
                labels = [row.get("canonical_name", ""), *(aliases if isinstance(aliases, list) else [])]
                if {_exact(n) for n in _aliases(candidate)}.intersection(map(_exact, labels)):
                    matches.append(row)
            # Identical duplicate rows are harmless; alternative forms are not.
            matches = list({digest(row): row for row in matches}.values())
            if len(matches) > 1:
                candidate.identity_status = "needs_review"
                _mark(candidate, run, "ambiguous_background_identity")
                continue
            if matches:
                row = matches[0]
                try:
                    values, source, alias_sources = verified_background_identity(row)
                    ledger.check()
                    if time.monotonic() >= core_deadline:
                        raise TimeoutError("Core identity verification time allowance reached")
                    names = [_exact(n) for n in _aliases(candidate)]
                    query = next(n for n in [row["canonical_name"], *row.get("aliases", [])] if _exact(n) in names)
                    request = ToolRequest(name="pubchem", arguments={"name": query})
                    provenance = {"origin": "background_identity_reuse", "acquisition": "background_reuse",
                        "source_sha256": row["source_sha256"], "source_retrieved_at": source.retrieved_at,
                        **{key: package_receipt[value] for key, value in (
                            ("package_id", "package_id"), ("package_version", "version"),
                            ("package_sha256", "manifest_sha256")) if value in package_receipt}}
                    source = source.model_copy(update={"metadata": {**source.metadata, **provenance}})
                    result = ToolResult(tool_name="pubchem", provider="pubchem", parameters=request.arguments,
                        status="ok", retrieved_at=source.retrieved_at, sources=[source],
                        records=[{"name_query": query, **values}], call_id="background_identity:" + digest(row)[:16],
                        identity={"status": "resolved", "entity_type": "molecule", "query": query,
                                  "selected_id": values["pubchem_cid"], "inchikey": values["inchikey"]})
                    outcome = sync.apply({"origin": "background_identity_reuse", "candidate_link": sync.bind(request)}, request, result)
                    if outcome.get("status") in {"applied", "unchanged"}:
                        candidate.aliases = list(dict.fromkeys([*candidate.aliases, row["canonical_name"], *row.get("aliases", [])]))
                        observation = next(o for o in candidate.identity_history if o.get("call_id") == result.call_id)
                        observation.update(provenance)
                        if alias_sources:
                            observation.update(alias_source_id=alias_sources[0].source_id,
                                alias_source_sha256=row["alias_source_sha256"],
                                alias_source_retrieved_at=alias_sources[0].retrieved_at)
                            observation["source_ids"] = list(dict.fromkeys([*observation["source_ids"], alias_sources[0].source_id]))
                            candidate.identity_source_ids = list(dict.fromkeys([*candidate.identity_source_ids, alias_sources[0].source_id]))
                            alias_sources = [s.model_copy(update={"metadata": {**s.metadata, **provenance,
                                "source_sha256": row["alias_source_sha256"], "source_retrieved_at": s.retrieved_at}})
                                for s in alias_sources]
                        engine.append_sources([source, *alias_sources])
                        flow = getattr(engine, "flow", None)
                        if flow:
                            reused = [source, *alias_sources]
                            flow.ingest(reused, "library")
                            flow.new_query("reuse", "background_identity",
                                "复用已核对的背景化学身份，获取时间沿用原记录", provider="pubchem",
                                status="reused", source_ids=[s.source_id for s in reused],
                                reused_from=str(provenance.get("package_id", "")),
                                stop_reason="background_identity_not_new_query")
                    _mark(candidate, run, outcome.get("status", "invalid_observation"), acquisition="background_reuse")
                    continue
                except (ValueError, TypeError, KeyError, StopIteration):
                    # An unverifiable package never supplies authoritative fields.
                    candidate.identity_status = "needs_review"
                    _mark(candidate, run, "invalid_background_identity")
                    continue
            if local_only:
                # A verifiable local record is required to record a completed
                # attempt: the normal bounded path remains available later.
                continue
            aliases = declared_aliases(candidate.name)
            declared_full_names = [d.full_name for d in candidate.user_name_declarations if d.run_id == run.run_id]
            queries = list(dict.fromkeys([*declared_full_names, *(aliases[:1]), candidate.name, *candidate.aliases]))[:3]
            reason = "unresolved"
            for query in queries:
                remaining_tools = min(tool_share - (ledger.counts["tool_calls"] - tool_start),
                                      run.brief.budget.max_tool_calls - ledger.counts["tool_calls"])
                remaining_http = min(http_share - (ledger.counts["http_attempts"] - http_start),
                                     run.brief.budget.max_http_requests - ledger.counts["http_attempts"])
                if remaining_tools <= 0 or remaining_http <= 0 or time.monotonic() >= core_deadline:
                    reason = "core_identity_allowance"
                    break
                request = ToolRequest(name="pubchem", arguments={"name": query})
                binding = sync.bind(request)
                if binding.get("status") != "verified":
                    reason = "ambiguous_candidate_alias" if binding.get("status") == "ambiguous" else "unlinked_candidate_alias"
                    break
                plan = engine.scheduler.create(request, origin="core_identity",
                    reason="Bounded required chemical identity preparation before hypothesis generation")
                result = engine.scheduler.run(plan) or engine.scheduler.memo.get(digest(request.model_dump()))
                if result is None:
                    reason = "identity_observation_unavailable"
                    continue
                # A local HTTP deadline must not be mislabeled as global budget
                # exhaustion and abort formal review that still has its reserve.
                if result.status == "budget_exhausted" and time.monotonic() >= core_deadline and time.monotonic() < ledger.deadline:
                    result.status, result.stop_reason = "unavailable", "core_identity_time_allowance"
                    result.warnings = ["Core identity time allowance reached; formal review time remains reserved"]
                    plan.update(status=result.status, stop_reason=result.stop_reason)
                    for saved in engine.state.tool_calls:
                        if saved.get("call_id") == result.call_id:
                            saved.update(status=result.status, stop_reason=result.stop_reason, warnings=result.warnings)
                    for event in engine.state.events:
                        if event.operation_id == result.call_id and event.operation_status == "budget_exhausted":
                            event.operation_status = result.status
                            event.summary = "unavailable; core_identity_time_allowance"
                for observation in candidate.identity_history:
                    if observation.get("call_id") == result.call_id:
                        observation.update(origin="core_identity", acquisition="new_query")
                reason = plan.get("identity_application", {}).get("status", result.status)
                if candidate.identity_status == "resolved" or reason in {"conflict", "invalid_observation"} or result.identity.get("status") in {"ambiguous", "mismatch", "truncated"}:
                    break
            _mark(candidate, run, reason, acquisition="new_query", remaining_seconds=round(max(0, ledger.deadline-time.monotonic()), 3))
    finally:
        if http is not None:
            http.deadline, http.before_request = old_deadline, old_before
        sync.refresh_references()
        engine.persist()

