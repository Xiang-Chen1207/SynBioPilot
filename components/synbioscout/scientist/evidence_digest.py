"""Deterministic evidence strata and reference coverage, without effect inference.

The digest indexes existing accepted continuous citations. It never replaces the
underlying evidence/source records or counts database snapshots as new trials.
"""
from __future__ import annotations

import re
import unicodedata

from .candidate_assessments import candidate_names, matching_candidates, names_in_quote
from .flow_models import ReferenceCoverage
from .store import digest


def _norm(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def _identifiers(source):
    if source.evidence_scope in {"database", "local_document"}:
        return [source.evidence_scope + ":" + source.source_id]
    from .research_flow import publication_aliases
    return sorted(a for a in publication_aliases(source) if not a.startswith("title:")) or ["source:" + source.source_id]


def _publication_groups(sources):
    groups = []
    for source in sources:
        ids = set(_identifiers(source))
        matches = [g for g in groups if ids.intersection(g["identifiers"])]
        if matches:
            group = matches[0]
            for other in matches[1:]:
                group["identifiers"].update(other["identifiers"])
                group["sources"].extend(other["sources"])
                groups.remove(other)
        else:
            group = {"identifiers":set(), "sources":[]}
            groups.append(group)
        group["identifiers"].update(ids)
        if all((s.source_id, s.provider, s.text) != (source.source_id, source.provider, source.text) for s in group["sources"]):
            group["sources"].append(source)
    for group in groups:
        group["identifiers"] = sorted(group["identifiers"])
        group["publication_id"] = "publication_" + digest(group["identifiers"])[:16]
    return groups


def _publication_type(source):
    raw = source.metadata.get("publication_types", {})
    values = raw.get("pubType", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
    types = {_norm(v) for v in values if isinstance(v, str)}
    # The source type is not the design of every experiment reported inside it.
    if types.intersection({"review", "systematic review", "meta-analysis"}):
        return "review_index"
    if types.intersection({"randomized controlled trial", "randomised controlled trial"}):
        return "randomized_controlled_trial"
    if "controlled clinical trial" in types:
        return "controlled_clinical_trial"
    if types.intersection({"observational study", "cohort study", "case-control study"}):
        return "observational_study"
    if types.intersection({"clinical trial", "clinical study"}):
        return "clinical_study_unspecified_design"
    return "unknown"


def _system_layer(value):
    """Only explicit recorded systems, never the requested human population."""
    text = _norm(value)
    cell = bool(re.search(r"\b(?:cells?|cell lines?|in vitro)\b|细胞|体外", text))
    animal = bool(re.search(r"\b(?:mice|mouse|rats?|murine|rodents?|animal)\b|小鼠|大鼠|动物", text))
    human = bool(re.search(r"\b(?:humans?|homo sapiens|children|adults?|patients?|participants?)\b|人体|人群|儿童|患者", text))
    if cell and human and not animal:
        return "human_cells" if "human cells" in text or "人源细胞" in text else "mixed_or_unspecified_systems"
    if sum((cell, animal, human)) > 1:
        return "mixed_or_unspecified_systems"
    return "cellular" if cell else "animal" if animal else "human" if human else "unknown"


def _intervention_mode(condition):
    text = _norm(condition)
    adjunct = bool(re.search(r"\b(?:adjunct(?:ive)?|add-on)\b|辅助治疗|联合治疗", text))
    single = bool(re.search(r"\b(?:monotherapy|standalone treatment)\b|单药治疗", text))
    return "unknown" if adjunct == single else "adjunctive" if adjunct else "monotherapy"


def _accepted(run):
    sources = {s.source_id:s for s in run.sources}
    # Summarize persisted acceptance, not a second semantic adjudication. Public
    # views intentionally omit database source bodies; that must not silently
    # erase an already accepted contrary citation from the digest.
    return [e for e in run.evidence if e.validated_span and e.source_id in sources
            and e.quote and e.location.strip()]


def _reference_rows(run, reference_sources):
    flow = run.co_scientist.research_flow if run.co_scientist else None
    rows = [row.model_copy(deep=True) for row in flow.reference_coverage] if flow else []
    known = {r.reference_id for r in rows}
    for source in reference_sources:
        ids = _identifiers(source)
        reference_id = "reference_" + digest(ids)[:16]
        if reference_id in known:
            continue
        rows.append(ReferenceCoverage(reference_id=reference_id, source_id=source.source_id, identifiers=ids,
            title=source.title, url=source.url,
            package_id=str(source.metadata.get("background_package_id", "")),
            package_version=str(source.metadata.get("background_version", "")),
            package_manifest_sha256=str(source.metadata.get("background_manifest_sha256", "")),
            declared_evidence_level=str(source.metadata.get("evidence_level", "unknown")),
            available_in_reference=bool(source.text.strip()), source_scope=source.evidence_scope,
            original_source_scope=str(source.metadata.get("original_evidence_scope",source.evidence_scope)),
            local_excerpt_only=source.metadata.get("local_excerpt_only") is True))
        known.add(reference_id)
    accepted = _accepted(run)
    for row in rows:
        selected = [s for s in run.sources if set(row.identifiers).intersection(_identifiers(s))]
        row.selected_source_ids = list(dict.fromkeys(s.source_id for s in selected))
        row.selection_status = "selected" if selected else "not_selected"
        row.access_status = "available" if row.available_in_reference or any(s.text.strip() for s in selected) else "no_access"
        row.accepted_evidence_ids = [e.evidence_id for e in accepted if e.source_id in row.selected_source_ids]
        row.citation_status = "accepted" if row.accepted_evidence_ids else "unreviewed_or_unaccepted"
    return rows


def build_evidence_digest(run, reference_sources=()):
    """Return a compact public index; every accepted citation keeps its own scope."""
    accepted = _accepted(run)
    publications = []
    for group in _publication_groups(run.sources):
        sources = group["sources"]
        source_ids = list(dict.fromkeys(s.source_id for s in sources))
        types = sorted({_publication_type(s) for s in sources} - {"unknown"})
        source_design = types[0] if len(types) == 1 else "conflicting_provider_types" if types else "unknown"
        records = []
        for e in accepted:
            if e.source_id not in source_ids:
                continue
            # Exact declared aliases are a mention check, not a treatment-effect
            # attribution. Formal claim review still decides whether it supports.
            entities = [c for c in run.candidates if names_in_quote(c, e.quote)]
            unique = [c for c in entities if all(len(matching_candidates(run.candidates,[name])) == 1
                for name in candidate_names(c) if name and _norm(name) in _norm(e.quote))]
            records.append({"evidence_id":e.evidence_id,"source_id":e.source_id,"polarity":e.polarity,
                "study_system":e.study_system,"system_layer":_system_layer(e.study_system),
                "condition":e.condition,"endpoint":e.endpoint,
                "intervention_mode":_intervention_mode(e.condition),
                "explicit_candidate_mentions":[c.name for c in unique],
                "candidate_mention_is_not_intervention_support":True,
                "location":e.location, "limitations":list(e.limitations)})
        publications.append({"publication_id":group["publication_id"], "identifiers":group["identifiers"],
            "kind":sources[0].evidence_scope if sources[0].evidence_scope in {"database","local_document"} else "paper",
            "source_ids":source_ids,"source_design":source_design,"design_basis":"provider_publication_types_only",
            "design_does_not_classify_each_reported_experiment":True,
            "accepted_citations":records})
    references = _reference_rows(run, reference_sources)
    from .research_public import _project
    papers = [p for p in publications if p["kind"] == "paper"]
    return {"version":"evidence-digest-1", "publications":publications,
        "reference_coverage":[_project(r, ReferenceCoverage) for r in references],
        "counts":{"selected_publications":len(papers), "accepted_citations":len(accepted),
            "accepted_publications":sum(bool(p["accepted_citations"]) for p in papers),
            "randomized_trial_publications":sum(p["source_design"] == "randomized_controlled_trial" for p in papers),
            "review_publications":sum(p["source_design"] == "review_index" for p in papers),
            "polarity_citations":{polarity:sum(e.polarity == polarity for e in accepted)
                for polarity in ("supports","opposes","mixed","context")}},
        "scope_notes":["Publication identities are grouped by stable identifiers, not independent experimental replications.",
            "Study design follows each citation's source passage, recorded study_system and condition.",
            "The digest records study-level findings and conditions, retaining mixed and contrary citations separately.",
            "Source reading scope, continuous-citation acceptance and claim-review status are recorded separately.",
            "Formal review must use the linked original evidence and source windows, not this index alone."]}


def persist_evidence_digest(run, reference_sources=()):
    """Persist only declared reference identities/status in an active current run."""
    if run.status.value not in {"queued","running"}:
        return build_evidence_digest(run)
    rows = _reference_rows(run, reference_sources)
    if run.co_scientist and run.co_scientist.research_flow:
        run.co_scientist.research_flow.reference_coverage = rows
    return build_evidence_digest(run)


def _content_key(value):
    # Normalize formatting, never negate words, rearrange clauses or infer synonyms.
    return _norm(value).rstrip(".。!?！？")


def hypothesis_groups(cards, candidates):
    """Retain proposals; collapse only exact testable content and evidence repeats."""
    groups = {}
    for h in cards:
        entities = []
        for name in h.molecule_names:
            matches = matching_candidates(candidates,[name])
            entities.append("entity:" + (matches[0].entity_id or matches[0].candidate_id) if len(matches) == 1 else "name:" + _norm(name))
        key = (tuple(sorted(entities)),tuple(sorted(_norm(t) for t in h.target_symbols)),
            *(_content_key(getattr(h,field)) for field in
              ("claim","mechanism","chassis_name","condition","endpoint","prediction","validation_question")),
            tuple(sorted(h.evidence_ids)),tuple(sorted(h.opposing_evidence_ids)),h.origin)
        groups.setdefault(key,[]).append(h)
    return [{"representative":items[0].hypothesis_id,"representative_version":items[0].version,
        "hypothesis_ids":[h.hypothesis_id for h in items],
        "hypothesis_versions":[{"hypothesis_id":h.hypothesis_id,"version":h.version} for h in items],
        "rule":"exact_testable_content_entities_scope_and_citations", "duplicates_retained":True}
        for items in groups.values()]

