"""Pure binding and validation for the independent candidate conclusion review.

This module performs bounded source-level assessment; general language
entailment and therapeutic classification are separate tasks. Exact accepted findings, explicit scope,
finite extrapolation guards and a separate model review are all required. The
reviewer accepts only after deterministic guards pass. Model calls and I/O live in the controller.
"""
from __future__ import annotations

import re

from .assessment_models import (
    CandidateAssessmentContent,
    CandidateAssessmentFinding,
    CandidateAssessmentProposal,
    CandidateAssessmentReviewDraft,
    CandidateEvaluation,
)
from .context import source_context, task_context
from .store import digest

BINDING_FIELDS = ("assessment_id", "version", "candidate_id", "entity_id", "identity_revision",
                  "identity_fingerprint", "task_fingerprint", "conclusion_fingerprint", "evidence_fingerprint")
ASSESSMENT_INSTRUCTIONS = (
    "Independently review each explicit candidate conclusion and every finding, including quotation and molecular role. "
    "Echo all binding fields unchanged. Use a new unique review_id; exactly one review per assessment and one item_review "
    "per finding with its exact evidence_ids and applicability. Accept only if the conclusion is a faithful limited synthesis of those "
    "findings and every item is supported within its original study_system, condition and endpoint. Review all accepted "
    "evidence and original source windows, including mixed/opposes and evidence omitted by the proposal. Missing material "
    "counterevidence requires revise or withhold. Keep source acceptance, candidate efficacy, symptom endpoints and core-disease benefit separate; preserve "
    "adjunct/monotherapy and animal/cell/human study scopes. Distinguish an administered "
    "compound from chassis production/delivery/exposure. Unknowns and next_questions must explain remaining applicability "
    "limits. A mixed/negative/needs_evidence conclusion may be accepted as a completed assessment; completion status is separate from "
    "therapeutic support. Review the supplied conclusions and findings as written. Nonempty deterministic_errors "
    "require resolution before an accept verdict. Copy supporting_evidence_ids and opposing_evidence_ids exactly, retaining mixed "
    "in both. Source-level findings are copied accepted claims or complete exact source-observation wrappers."
)


def _unique(values):
    return len(values) == len(set(values))


def _mutable(run):
    return str(getattr(run.status, "value", run.status)) in {"queued", "running"}


def _candidate(run, record):
    matches = [c for c in run.candidates if (c.entity_id or c.candidate_id) == record.entity_id]
    return matches[0] if len(matches) == 1 else None


def _identity_fingerprint(candidate):
    return digest({key: getattr(candidate, key) for key in (
        "candidate_id", "entity_id", "name", "aliases", "identity_revision", "identity_status",
        "smiles", "inchikey", "pubchem_cid", "identity_source_ids", "identity_form")} | {
        "user_name_declarations": [d.model_dump(mode="json") for d in candidate.user_name_declarations]})


def _task_fingerprint(run):
    return digest({"run_id": run.run_id, "project_id": run.project_id,
                   "brief": run.brief.model_dump(mode="json")})


def _label_pattern(label):
    return r"(?<![\w-])" + re.escape(label).replace(r"\ ", r"\s+") + r"(?![\w-])"


def _identity_labels(run):
    from .candidate_assessments import candidate_names, exact_name
    labels = {}
    for candidate in run.candidates:
        entity = candidate.entity_id or candidate.candidate_id
        for label in candidate_names(candidate):
            if label.strip():
                entry = labels.setdefault(exact_name(label), {"label": label, "entities": set()})
                entry["entities"].add(entity)
    return list(labels.values())


def _name_mentions(text, labels):
    mentions = [{**entry, "start": match.start(), "end": match.end()}
                for entry in labels for match in re.finditer(_label_pattern(entry["label"]), text, re.IGNORECASE)]
    # Prefer the complete salt/chemical-name span over contained shorter names.
    # Equal spans keep all competing identities.
    return [mention for mention in mentions if not any(
        other["start"] <= mention["start"] and other["end"] >= mention["end"]
        and other["end"] - other["start"] > mention["end"] - mention["start"] for other in mentions)]


def _identifier_labels(run):
    owners = {}
    for candidate in run.candidates:
        entity = candidate.entity_id or candidate.candidate_id
        if candidate.pubchem_cid.isdigit():
            pattern = r"\b(?:PubChem(?:\s+CID)?|CID)\s*[:=#]?\s*" + re.escape(candidate.pubchem_cid) + r"(?![\w-]|\.\d)"
            owners.setdefault(pattern, {"label": "PubChem CID:" + candidate.pubchem_cid, "entities": set()})["entities"].add(entity)
        if candidate.inchikey:
            pattern = _label_pattern(candidate.inchikey)
            owners.setdefault(pattern, {"label": candidate.inchikey, "entities": set()})["entities"].add(entity)
    return [{**entry, "pattern": pattern} for pattern, entry in owners.items()]


def _identifier_mentions(run, text):
    return [{**entry, "start": match.start(), "end": match.end()}
            for entry in _identifier_labels(run) for match in re.finditer(entry["pattern"], text, re.IGNORECASE)]


def _definition_markers(source, code):
    """Extract explicit code definitions; handle sample-size parentheses as counts."""
    code_pattern = _label_pattern(code)
    verbal = False
    for match in re.finditer(code_pattern + r"\s*(=|means\b|denotes\b|refers to\b|stands for\b|代表|意为)", source.text, re.IGNORECASE):
        # '(NAC = 16, placebo = 15)' reports group sizes. Chemical identity uses
        # explicit name definitions; textual/identifier assignments remain unknown.
        if match[1] == "=" and re.match(r"\s*\d+(?:\.\d+)?\s*(?=[,;.)])", source.text[match.end():]):
            continue
        verbal = True
    parenthetical = list(re.finditer(r"\(\s*" + code_pattern + r"\s*\)", source.text, re.IGNORECASE))
    parenthetical += [match for match in re.finditer(code_pattern + r"\s*\(([^)]{1,150})\)", source.text, re.IGNORECASE)
                     if not re.fullmatch(r"\s*(?:n\s*=\s*)?\d[\d\s.,%+-]*\s*", match[1], re.IGNORECASE)]
    return verbal, parenthetical


def _explicit_definition(source, quote_start, quote_end, mention, labels, *, require_local=True):
    """Resolve finite, local definitions in source text; titles, claims and metadata retain separate roles.

    Only exact Full name (code) / code (Full name) mappings are recognized. Any
    conflicting or unrecognized explicit code definition in this source makes
    a shared code unresolved. This quote uses definitions within its local context.
    """
    code = mention["label"]
    definitions = []
    for entry in labels:
        if len(entry["entities"]) != 1 or entry["label"].casefold() == code.casefold():
            continue
        anchor = entry.get("pattern", _label_pattern(entry["label"]))
        alias = _label_pattern(code)
        for pattern in (anchor + r"\s*\(\s*" + alias + r"\s*\)",
                        alias + r"\s*\(\s*" + anchor + r"\s*\)"):
            for match in re.finditer(pattern, source.text, re.IGNORECASE):
                start = max(source.text.rfind(".", 0, match.start()), source.text.rfind("\n", 0, match.start())) + 1
                prefix = source.text[start:match.start()]
                if re.search(r"\b(?:background|previous|prior|earlier|unrelated|not|unknown|rather than|instead of)\b|背景|既往|未知|并非", prefix, re.IGNORECASE):
                    return None
                definitions.append({"entity_id": next(iter(entry["entities"])), "start": match.start(),
                                    "end": match.end(), "text": match.group()})
    # Unknown explicit definitions are not rescued by a different known one.
    verbal, possible = _definition_markers(source, code)
    if verbal:
        return None
    if any(not any(d["start"] <= match.start() and d["end"] >= match.end() for d in definitions) for match in possible):
        return None
    entities = {d["entity_id"] for d in definitions}
    if not require_local:
        return (next(iter(entities)), [{k: d[k] for k in ("start", "end", "text")} for d in definitions]) if len(entities) == 1 else None
    local = [d for d in definitions if d["start"] >= max(0, quote_start - 240) and d["end"] <= quote_end]
    if len(entities) != 1 or not local:
        return None
    # Keep the definition within its paragraph and research-context section.
    local = [d for d in local if "\n\n" not in source.text[d["end"]:quote_start] and not re.search(
        r"\b(?:background|previous|prior|unrelated)\b|背景|既往", source.text[d["end"]:quote_start], re.IGNORECASE)]
    if not local:
        return None
    return next(iter(entities)), [{k: d[k] for k in ("start", "end", "text")} for d in local]


def _evidence_binding(run, evidence, source):
    """Return at most one source entity; coexistence is never effect attribution."""
    labels = _identity_labels(run)
    names = _name_mentions(evidence.quote, labels)
    identifiers = _identifier_mentions(run, evidence.quote)
    mentions = names + identifiers
    if not mentions:
        return None
    entities, definitions, matched = set(), [], []
    quote_start = source.text.find(evidence.quote)
    for mention in mentions:
        if len(mention["entities"]) == 1:
            # A single known alias needs no extra source definition. However,
            # an explicit contradictory/unknown definition must not be ignored
            # just because this run happens to have only one candidate.
            if mention in names and any(_definition_markers(source, mention["label"])):
                mapping = _explicit_definition(source, quote_start, quote_start + len(evidence.quote), mention,
                    labels + _identifier_labels(run), require_local=False)
                if mapping is None or mapping[0] not in mention["entities"]:
                    return None
            entities.update(mention["entities"])
        elif mention in names:
            mapping = _explicit_definition(source, quote_start, quote_start + len(evidence.quote), mention,
                                           labels + _identifier_labels(run))
            if mapping is None:
                return None
            entity, spans = mapping
            entities.add(entity)
            definitions.extend(span for span in spans if span not in definitions)
        else:
            return None
        matched.append(mention["label"])
    if len(entities) != 1:
        return None
    return {"evidence_id": evidence.evidence_id, "source_id": source.source_id, "entity_id": next(iter(entities)),
            "basis": "explicit_source_definition" if definitions else "unique_quote_identifier" if identifiers else "unique_quote_name",
            "matched_labels": list(dict.fromkeys(matched)), "definition_spans": definitions}


def _evidence(run, candidate, evidence_id, *, require_link=True):
    """One accepted record, one continuous unique original span, one explicit entity."""
    matches = [e for e in run.evidence if e.evidence_id == evidence_id]
    if len(matches) != 1 or (require_link and evidence_id not in candidate.evidence_ids):
        return None
    evidence = matches[0]
    sources = [s for s in run.sources if s.source_id == evidence.source_id]
    if len(sources) != 1:
        return None
    source = sources[0]
    if (not evidence.validated_span or len(evidence.quote.strip()) < 15 or not evidence.location.strip()
            or source.text.count(evidence.quote) != 1
            or not (source.evidence_scope in {"abstract", "full_text"} or
                    (source.evidence_scope == "local_document" and source.provider == "local_experiment"))):
        return None
    binding = _evidence_binding(run, evidence, source)
    if binding is None or binding["entity_id"] != (candidate.entity_id or candidate.candidate_id):
        return None
    return evidence, source


def _candidate_pool(run, candidate):
    return sorted({e.evidence_id for e in run.evidence if _evidence(run, candidate, e.evidence_id)})


def _unlinked_related_evidence(run, candidate):
    result = []
    for evidence in run.evidence:
        if evidence.evidence_id in candidate.evidence_ids:
            continue
        matches = [c for c in run.candidates if _evidence(run, c, evidence.evidence_id, require_link=False)]
        if len(matches) == 1 and matches[0] is candidate:
            result.append(evidence.evidence_id)
    return sorted(set(result))


def _evidence_fingerprint(run, candidate, ids):
    if not ids or not _unique(ids):
        return ""
    rows = []
    for eid in sorted(ids):
        pair = _evidence(run, candidate, eid)
        if pair is None:
            return ""
        evidence, source = pair
        rows.append({"evidence": evidence.model_dump(mode="json"),
                     "source": {k: getattr(source, k) for k in (
                         "source_id", "provider", "title", "url", "retrieved_at", "pmid", "doi", "pmcid", "evidence_scope")},
                     "source_text_sha256": digest(source.text)})
    return digest(rows)


def _polarity_ids(run, ids):
    lookup = {e.evidence_id: e for e in run.evidence}
    return ([eid for eid in ids if lookup[eid].polarity in {"supports", "mixed"}],
            [eid for eid in ids if lookup[eid].polarity in {"opposes", "mixed"}])


def store_assessment_proposals(run, proposals, key_to_id):
    """Persist only explicit synthesis output, never retrofit a legacy rationale."""
    from .candidate_assessments import matching_candidates
    if not _mutable(run):
        return []
    proposals = [CandidateAssessmentProposal.model_validate(p) for p in proposals]
    added = []
    for proposal in proposals:
        matches = matching_candidates(run.candidates, [proposal.candidate_name])
        if len(matches) != 1:
            continue
        candidate = matches[0]
        candidate.entity_id = candidate.entity_id or candidate.candidate_id
        existing = [r for r in run.candidate_evaluations if r.entity_id == candidate.entity_id]
        version = max((r.version for r in existing), default=0) + 1
        aid = "assessment_" + digest([run.run_id, candidate.entity_id])[:20]
        errors = []
        if sum(bool(matching_candidates(run.candidates, [p.candidate_name])) and
               matching_candidates(run.candidates, [p.candidate_name])[0] is candidate for p in proposals) != 1:
            errors.append("Duplicate candidate proposals require explicit revision")
        if not _unique([f.finding_key for f in proposal.findings]):
            errors.append("Duplicate finding keys")
        findings = []
        for item in proposal.findings:
            mapped = [key_to_id[k] for k in item.evidence_keys if k in key_to_id]
            if len(mapped) != len(item.evidence_keys) or not _unique(item.evidence_keys) or not _unique(mapped):
                errors.append("Missing or duplicate evidence keys in finding")
            findings.append(CandidateAssessmentFinding(
                finding_id="finding_" + digest([aid, version, item.finding_key])[:20],
                statement=item.statement, evidence_ids=mapped, scope=item.scope))
        ids = sorted({eid for f in findings for eid in f.evidence_ids if _evidence(run, candidate, eid)})
        support, opposition = _polarity_ids(run, ids)
        content = CandidateAssessmentContent(conclusion=proposal.conclusion, scientific_status=proposal.scientific_status,
            findings=findings, supporting_evidence_ids=support, opposing_evidence_ids=opposition,
            unknowns=proposal.unknowns, next_questions=proposal.next_questions)
        record = CandidateEvaluation(assessment_id=aid, version=version, candidate_id=candidate.candidate_id,
            entity_id=candidate.entity_id, draft=content, validation_errors=list(dict.fromkeys(errors)))
        run.candidate_evaluations.append(record)
        added.append(record)
    return added


_NEGATED = re.compile(r"\b(?:no|not|cannot|can't|without|unknown|uncertain|unproven|unestablished|insufficient|lack\w*|"
                       r"unresolved|unsupported|unverified|unmeasured|undetermined|whether|needs?|requires?|failed)\b|"
                       r"不|未|无|无法|未知|缺乏|不足|待|尚|需要|是否", re.IGNORECASE)
_RISKY = re.compile(r"\b(?:cures?|curative|treats?|treatment efficacy|effective(?:ness)?|efficacious|"
                      r"equivalent|equivalence|noninferior|non-inferior|predict\w*|guarantee\w*|"
                      r"clinically proven|established therap\w*|monotherapy|standalone|"
                      r"deliver\w*|produc\w*|biosynth\w*|bioavailab\w*|brain exposure|"
                      r"core symptoms?|core features?)\b|治愈|治疗有效|疗效确证|等效|非劣|预测|"
                      r"单药|独立治疗|递送|产出|生产|合成|脑暴露|生物利用度|核心症状", re.IGNORECASE)
_BENEFIT = re.compile(r"\b(?:improv\w*|reduc\w*|benefit\w*|ameliorat\w*|efficac\w*)\b|改善|获益|有效|缓解", re.IGNORECASE)
_SOURCE_BOUND = re.compile(r"\b(?:source|study|trial|reported|recorded|observed|tested|finding|evidence|endpoint|scope)\b|"
                          r"来源|研究|试验|报道|报告|记录|观察|终点|证据|范围", re.IGNORECASE)
_CLINICAL = re.compile(r"\b(?:humans?|patients?|children|people|clinical|therapeutic)\b|人体|人群|患者|儿童|临床|治疗", re.IGNORECASE)
_ASSERTION = re.compile(r"\b(?:supports?|demonstrat\w*|establish\w*|confirms?|shows?|proves?)\b|支持|证实|证明|表明", re.IGNORECASE)
_LIMIT_NOUNS = frozenset({"the", "its", "clinical", "human", "individual", "population", "patient", "candidate",
    "compound", "benefit", "benefits", "efficacy", "effect", "effects", "safety", "applicability", "prediction",
    "response", "core", "symptom", "symptoms", "production", "synthesis", "biosynthesis", "delivery", "exposure",
    "brain", "bioavailability", "chassis", "strain", "mechanism", "pharmacokinetics", "and", "or", "for", "of",
    "in", "to", "this", "requested", "use", "context"})


def _terminal_limitation(clause):
    """Recognize a short noun-list abstention, never a preceding positive verb."""
    match = re.fullmatch(r"\s*(.+?)\s+(?:is|are|remains?|remain)\s+(?:unknown|uncertain|unestablished|unproven|"
                         r"unresolved|unsupported|unverified|unmeasured|not (?:established|proven|supported|verified|known))\s*", clause, re.IGNORECASE)
    if match:
        words = re.findall(r"[a-z]+", match[1].casefold())
        return bool(words) and set(words).issubset(_LIMIT_NOUNS)
    return bool(re.fullmatch(r"\s*(?:(?:疗效|效益|临床适用性|人群适用性|个体预测|核心症状获益|递送|生产|合成|暴露|机制|安全性|和|与|及|、)+)"
                            r"(?:仍|尚)?(?:未知|不明|未证实|未建立)\s*", clause))


def _prose_errors(text, *, human_scope):
    errors = []
    text = ".".join(sentence for sentence in re.split(r"[.;；。!?！？\n]", text)
                    if not _terminal_limitation(sentence))
    # Check each contrast clause independently, preserving the scope of its negation
    # and any later positive clinical assertion.
    for clause in re.split(r"[.,;，；。!?！？\n]|\b(?:but|however|whereas|and|although|despite)\b|但|然而", text):
        if not clause.strip():
            continue
        # 'not only' is affirmative; a negator after the effect ('cures without
        # risk') leaves that effect affirmative. This finite check is deliberately
        # conservative, while the separate reviewer must judge the actual prose.
        clause = re.sub(r"\bnot only\b", "", clause, flags=re.IGNORECASE)
        effects = [m for pattern in (_RISKY, _BENEFIT, _ASSERTION) for m in pattern.finditer(clause)]
        first_effect = min((m.start() for m in effects), default=len(clause))
        if _NEGATED.search(clause[:first_effect]):
            continue
        if _RISKY.search(clause):
            errors.append("Conclusion asserts an unlicensed therapeutic, predictive or delivery extrapolation")
        elif _BENEFIT.search(clause) and not _SOURCE_BOUND.search(clause):
            errors.append("Benefit statement must remain explicitly within its reported source scope")
        if not human_scope and _CLINICAL.search(clause) and (_BENEFIT.search(clause) or _ASSERTION.search(clause)):
            errors.append("Human clinical applicability requires a matching human study scope; source scope is nonhuman or unknown")
    return errors


def _scope_errors(evidence, source):
    from .research_flow import observation_study_objects, observation_systems
    declared = observation_systems(evidence.study_system)
    actual, _unresolved = observation_study_objects(source.text)
    source_systems = observation_systems(source.text)
    # Absence of a recruitment sentence is not contradictory evidence. It is
    # exposed as population_uncertain below, without clinical applicability.
    # An affirmative incompatible study object remains a hard conflict.
    nonhuman = {"mouse", "rat", "nonhuman", "cell", "computational"}
    if "human" in declared and (declared != {"human"} or actual.intersection(nonhuman) or
                               (actual != {"human"} and source_systems.intersection(nonhuman))):
        return ["Human study scope conflicts with the source's study objects"]
    if declared and actual and declared.isdisjoint(actual) and not (
            "nonhuman" in declared and actual.issubset(nonhuman)):
        return ["Declared study scope conflicts with the source's current study objects"]
    # These checks detect conflicts; positive applicability requires a separate assessment.
    source_text = (source.title + " " + source.text).casefold()
    scope_text = (evidence.condition + " " + evidence.endpoint).casefold()
    if re.search(r"\b(?:adjunct\w*|add-on|add on|co-administered)\b|辅助|联合", source_text) and re.search(
            r"\b(?:monotherapy|standalone|alone)\b|单药|独立治疗", scope_text):
        return ["Monotherapy scope requires monotherapy evidence; this source reports adjunctive treatment"]
    if re.search(r"\bcore (?:symptoms?|features?)\b|核心症状", scope_text) and not re.search(
            r"\bcore (?:symptoms?|features?)\b|核心症状", evidence.quote.casefold()):
        return ["Core endpoint is not present in the quoted observation"]
    return []


def _scope_annotation(evidence, source):
    from .research_flow import observation_study_objects, observation_systems
    declared = observation_systems(evidence.study_system)
    actual, unresolved = observation_study_objects(source.text)
    if declared == {"human"}:
        if actual == {"human"} and not unresolved:
            return "source_scope_only", ["Applicability covers the reported study population and endpoints; individual prediction and clinical recommendation require separate assessment."]
        return "population_uncertain", [
            "The exact source observation is retained; human study-population applicability is unresolved within this source window."]
    return "nonhuman_or_unknown", [
        "Source background: nonhuman or unknown study system. Human clinical-effect support requires matching human intervention evidence."]


def _finding_annotation(run, candidate, finding):
    pairs = [_evidence(run, candidate, eid) for eid in finding.evidence_ids]
    annotations = [_scope_annotation(*pair) for pair in pairs if pair is not None]
    levels = {value[0] for value in annotations}
    applicability = ("nonhuman_or_unknown" if "nonhuman_or_unknown" in levels else
                     "population_uncertain" if "population_uncertain" in levels else "source_scope_only")
    return applicability, list(dict.fromkeys(text for _, texts in annotations for text in texts))


def _content_errors(run, candidate, record):
    from .research_flow import observation_systems, source_observation_statement
    errors = []
    content = record.draft
    if any(not text.strip() for text in [content.conclusion, *content.unknowns, *content.next_questions,
                                        *[finding.statement for finding in content.findings]]):
        errors.append("Assessment conclusion, findings, unknowns and next questions must be substantive")
    ids = record.reviewed_evidence_ids
    if not ids or not _unique(ids):
        return ["No unique accepted evidence set for candidate assessment"]
    if _unlinked_related_evidence(run, candidate):
        errors.append("Uniquely candidate-attributed accepted evidence is missing a current candidate association")
    support, opposition = _polarity_ids(run, ids) if all(_evidence(run, candidate, eid) for eid in ids) else ([], [])
    if content.supporting_evidence_ids != support or content.opposing_evidence_ids != opposition:
        errors.append("Draft polarity references omit or change reviewed supporting/opposing evidence")
    finding_ids = [f.finding_id for f in content.findings]
    if not _unique(finding_ids):
        errors.append("Duplicate finding identifiers")
    covered = set()
    human_scopes = []
    for finding in content.findings:
        if not finding.evidence_ids or not _unique(finding.evidence_ids) or not set(finding.evidence_ids).issubset(ids):
            errors.append("Finding lacks unique evidence within the reviewed set")
            continue
        covered.update(finding.evidence_ids)
        applicability, limitations = _finding_annotation(run, candidate, finding)
        if finding.applicability != applicability or finding.scope_limitations != limitations:
            errors.append("Finding applicability differs from its deterministic source scope limitations")
        for eid in finding.evidence_ids:
            pair = _evidence(run, candidate, eid)
            if pair is None:
                errors.append("Finding is not bound to a unique accepted current candidate source span")
                continue
            evidence, source = pair
            human_scope = applicability == "source_scope_only" and observation_systems(evidence.study_system) == {"human"}
            human_scopes.append(human_scope)
            if finding.scope.model_dump() != {key: getattr(evidence, key) for key in ("study_system", "condition", "endpoint")}:
                errors.append("Finding scope differs from its accepted evidence scope")
            if finding.statement not in {evidence.claim, source_observation_statement(evidence.source_id, evidence.quote)}:
                errors.append("Finding must copy an accepted claim or its complete exact source observation")
            elif finding.statement == evidence.claim:
                errors.extend(_prose_errors(finding.statement, human_scope=human_scope))
            if applicability == "population_uncertain" and finding.statement != source_observation_statement(evidence.source_id, evidence.quote):
                errors.append("Uncertain population requires the complete verbatim source observation")
            errors.extend(_scope_errors(evidence, source))
    if covered != set(ids):
        errors.append("Proposal findings require coverage of the full candidate evidence set, including counterevidence")
    if opposition and support and content.scientific_status not in {"mixed_evidence", "needs_evidence"}:
        errors.append("Mixed supporting/opposing evidence requires a mixed or unresolved conclusion")
    if not support and content.scientific_status not in {"no_supported_benefit", "needs_evidence"}:
        errors.append("No accepted positive finding supports the proposed scientific status")
    if not support and not opposition and content.scientific_status != "needs_evidence":
        errors.append("Intervention conclusions require intervention evidence; this record has context scope")
    if any(f.applicability == "population_uncertain" for f in content.findings) and content.scientific_status not in {"mixed_evidence", "needs_evidence"}:
        errors.append("Uncertain population requires a mixed or unresolved overall assessment")
    # Prose review remains independent. These conservative finite guards block
    # obvious extrapolation even when an erroneous reviewer emits accept. They
    # intentionally make no claim of general biomedical language understanding.
    errors.extend(_prose_errors(content.conclusion, human_scope=bool(human_scopes) and all(human_scopes)))
    return list(dict.fromkeys(errors))


def bind_assessment_identities(run):
    """Freeze current identity/task/evidence before the separate conclusion call.

    Reviewed records are never rebound to silently make an old receipt valid.
    """
    if not _mutable(run):
        return []
    ready = []
    for record in run.candidate_evaluations:
        if record.reviews or record.final is not None:
            continue
        candidate = _candidate(run, record)
        if candidate is None:
            record.status = "needs_review"
            continue
        record.candidate_id = candidate.candidate_id
        record.identity_revision = candidate.identity_revision
        record.identity_fingerprint = _identity_fingerprint(candidate)
        record.task_fingerprint = _task_fingerprint(run)
        record.reviewed_evidence_ids = _candidate_pool(run, candidate)
        record.evidence_fingerprint = _evidence_fingerprint(run, candidate, record.reviewed_evidence_ids)
        # Identity aliases may only become available during core preparation.
        # Refresh derived polarity references from explicitly proposed findings,
        # never insert an omitted observation or silently expand a reviewed final.
        proposed_ids = sorted({eid for f in record.draft.findings for eid in f.evidence_ids
                               if _evidence(run, candidate, eid)})
        record.draft.supporting_evidence_ids, record.draft.opposing_evidence_ids = _polarity_ids(run, proposed_ids)
        for finding in record.draft.findings:
            finding.applicability, finding.scope_limitations = _finding_annotation(run, candidate, finding)
        record.conclusion_fingerprint = digest(record.draft.model_dump(mode="json"))
        errors = [error for error in record.validation_errors if error in {
            "Duplicate candidate proposals require explicit revision", "Duplicate finding keys",
            "Missing or duplicate evidence keys in finding"}]
        if candidate.identity_status != "resolved" or not all((candidate.smiles, candidate.inchikey, candidate.pubchem_cid)):
            errors.append("Candidate chemical identity is unresolved")
        errors.extend(_content_errors(run, candidate, record))
        record.validation_errors = list(dict.fromkeys(errors))
        record.status = "needs_review" if errors or not record.evidence_fingerprint else "ready_for_review"
        if record.status == "ready_for_review":
            ready.append(record)
    return ready


def _review_identity(candidate):
    """Bounded explicit identity data; no arbitrary provider/source metadata."""
    from .tool_public import _fields, _strings
    row = _fields({key: getattr(candidate, key) for key in (
        "candidate_id", "entity_id", "name", "identity_revision", "identity_status", "smiles", "inchikey", "pubchem_cid")},
        {"candidate_id", "entity_id", "name", "identity_revision", "identity_status", "smiles", "inchikey", "pubchem_cid"}, 1200)
    row["identity_fingerprint"] = _identity_fingerprint(candidate)
    row["aliases"] = _strings(candidate.aliases, 500, 100)
    row["omitted_alias_count"] = len(candidate.aliases) - len(row["aliases"])
    row["identity_source_ids"] = _strings(candidate.identity_source_ids, 300, 100)
    row["user_name_declarations"] = [_fields(d.model_dump(mode="json"),
        {"label", "full_name", "abbreviation", "source", "source_index", "run_id"}, 500)
        for d in candidate.user_name_declarations]
    return row


def assessment_review_payload(run):
    """Full current evidence/source windows; no pruning of negative/context data."""
    rows = []
    for record in run.candidate_evaluations:
        candidate = _candidate(run, record)
        if record.status != "ready_for_review" or record.reviews or candidate is None:
            continue
        rows.append({**{k: getattr(record, k) for k in BINDING_FIELDS},
                     "identity": _review_identity(candidate),
                     "evidence_bindings": [_evidence_binding(run, *pair) for eid in record.reviewed_evidence_ids
                         if (pair := _evidence(run, candidate, eid)) is not None],
                     "reviewed_evidence_ids": list(record.reviewed_evidence_ids),
                     "draft": record.draft.model_dump(mode="json"),
                     "deterministic_errors": list(record.validation_errors)})
    return {"task": task_context(run.brief, include_documents=True),
            "candidate_identities": [_review_identity(candidate) for candidate in run.candidates],
            "candidate_evaluations": rows,
            "accepted_evidence": [e.model_dump(mode="json") for e in run.evidence],
            "sources": [source_context(s, run.evidence, include_prefix=True) for s in run.sources],
            "instructions": ASSESSMENT_INSTRUCTIONS}


def _current_errors(run, record):
    candidate = _candidate(run, record)
    if candidate is None or record.candidate_id != candidate.candidate_id:
        return ["Candidate identity binding is absent or ambiguous"]
    errors = list(record.validation_errors)
    if sum(r.entity_id == record.entity_id and r.version == record.version for r in run.candidate_evaluations) != 1:
        errors.append("Candidate entity and assessment version are ambiguous")
    if record.assessment_id != "assessment_" + digest([run.run_id, record.entity_id])[:20]:
        errors.append("Assessment identifier does not match its run and stable entity")
    if (candidate.identity_status != "resolved" or record.identity_revision != candidate.identity_revision
            or record.identity_fingerprint != _identity_fingerprint(candidate)):
        errors.append("Candidate identity changed or remains unresolved")
    if record.task_fingerprint != _task_fingerprint(run):
        errors.append("Full task binding changed")
    if record.conclusion_fingerprint != digest(record.draft.model_dump(mode="json")):
        errors.append("Conclusion or finding scope changed")
    if not record.evidence_fingerprint or record.evidence_fingerprint != _evidence_fingerprint(run, candidate, record.reviewed_evidence_ids):
        errors.append("Reviewed evidence/source binding changed")
    # Relevance is symmetric in evidence polarity: new support also challenges
    # a prior negative assessment. Preserve the historical final without adding
    # new citations to a review that never saw them.
    current_pool = _candidate_pool(run, candidate)
    newly_linked = set(current_pool) - set(record.reviewed_evidence_ids)
    if newly_linked:
        errors.append("New candidate evidence requires an updated assessment regardless of polarity")
    errors.extend(_content_errors(run, candidate, record))
    return list(dict.fromkeys(errors))


def _valid_review(run, record):
    if len(record.reviews) != 1:
        return False
    if sum(r.assessment_id == record.assessment_id and r.version == record.version for r in run.candidate_evaluations) != 1:
        return False
    review = record.reviews[0]
    if not review.review_id.strip() or not review.reason.strip() or any(not item.reason.strip() for item in review.item_reviews):
        return False
    events = [event for event in getattr(run.co_scientist, "events", [])
              if record.review_operation_id and event.operation_id == record.review_operation_id
              and event.agent_id == "reflection" and event.status == "completed"
              and event.operation_status == "completed" and event.title == "独立候选结论审查"]
    if len(events) != 1:
        return False
    if sum(r.review_id == review.review_id for row in run.candidate_evaluations for r in row.reviews) != 1:
        return False
    if any(getattr(review, key) != getattr(record, key) for key in BINDING_FIELDS) or review.verdict != "accept":
        return False
    if (review.supporting_evidence_ids != record.draft.supporting_evidence_ids or
            review.opposing_evidence_ids != record.draft.opposing_evidence_ids):
        return False
    if len(review.item_reviews) != len(record.draft.findings):
        return False
    for finding in record.draft.findings:
        items = [item for item in review.item_reviews if item.finding_id == finding.finding_id]
        if (len(items) != 1 or items[0].verdict != "accept" or items[0].evidence_ids != finding.evidence_ids
                or items[0].applicability != finding.applicability):
            return False
    return not _current_errors(run, record)


def apply_assessment_review(run, draft, *, operation_id=""):
    if not _mutable(run):
        return public_validated_evaluations(run)
    draft = CandidateAssessmentReviewDraft.model_validate(draft)
    pending = [r for r in run.candidate_evaluations if r.status == "ready_for_review" and not r.reviews]
    for review in draft.reviews:
        # Retain wrong-version attempts on the uniquely identified assessment;
        # later accepted receipts are recorded alongside them.
        matches = [r for r in run.candidate_evaluations if r.assessment_id == review.assessment_id and r.version == review.version]
        if not matches:
            matches = [r for r in run.candidate_evaluations if r.assessment_id == review.assessment_id]
        if len(matches) == 1:
            matches[0].reviews.append(review)
            if not matches[0].review_operation_id:
                matches[0].review_operation_id = operation_id
    for record in run.candidate_evaluations:
        if record not in pending and not record.reviews:
            continue
        if _valid_review(run, record):
            record.final = record.draft.model_copy(deep=True)
            record.status = "completed_review"
        else:
            record.status = "needs_review"
            # Keep any historically reviewed final as audit data, but it is no
            # longer a currently validated public conclusion.
    return public_validated_evaluations(run)


def public_validated_evaluations(run):
    """Explicit public fields, validated on original data before presentation.

    No draft, source text, metadata blob or reviewer prose is projected here.
    The presentation layer applies its normal text safety sanitizer afterwards.
    """
    result = []
    for record in getattr(run, "candidate_evaluations", []):
        if any(other.entity_id == record.entity_id and other.version > record.version for other in run.candidate_evaluations):
            continue
        if (record.status != "completed_review" or record.final is None or
                record.final != record.draft or not _valid_review(run, record)):
            continue
        content = record.final
        result.append({"schema_version": 1, **{k: getattr(record, k) for k in BINDING_FIELDS},
            "status": "completed_review", "review_id": record.reviews[0].review_id,
            "review_operation_id": record.review_operation_id,
            "conclusion": content.conclusion, "scientific_status": content.scientific_status,
            "findings": [{"finding_id": f.finding_id, "statement": f.statement,
                          "applicability": f.applicability, "scope_limitations": list(f.scope_limitations),
                          "evidence_ids": list(f.evidence_ids), "scope": {
                              "study_system": f.scope.study_system, "condition": f.scope.condition,
                              "endpoint": f.scope.endpoint}} for f in content.findings],
            "supporting_evidence_ids": list(content.supporting_evidence_ids),
            "opposing_evidence_ids": list(content.opposing_evidence_ids),
            "unknowns": list(content.unknowns), "next_questions": list(content.next_questions)})
    return result

