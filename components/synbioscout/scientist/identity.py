"""Apply verified chemical observations to explicitly linked, current-run entities.

This module has no file/network access. Chemical identity annotations cannot change
scientific roles, priority, accepted evidence, Gate or versioned hypothesis claims.
"""
from __future__ import annotations

import re
import unicodedata

from .evidence import candidate_assessment_fingerprint
from .models import ExecutionStatus
from .store import digest
from .tools import ToolRequest, ToolResult


def _exact(value):
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def declared_aliases(name):
    """Only expand an explicitly parenthesized acronym, never approximate names.

    Chemical form/stereochemistry qualifiers such as '(sodium)' or '(R)' are
    deliberately not stripped. Provider synonyms may be recorded separately.
    """
    match = re.fullmatch(r"(.+?)\s*\(([A-Z][A-Z0-9-]{1,11})\)", name.strip())
    if not match:
        return []
    # The acronym must be declared by, and correspond to, the label's words.
    # This excludes form labels ('HCL', 'TFA', etc.) without dropping fragments.
    initials = [word[0].upper() for word in re.findall(r"[A-Za-z0-9]+", match[1])]
    cursor = iter(initials)
    if not all(any(letter == initial for initial in cursor) for letter in match[2].replace("-", "")):
        return []
    return [match[1].strip(), match[2]]


def declared_candidate_names(candidate):
    """Names from uniquely linked user declarations, not official synonyms."""
    return list(dict.fromkeys(name for declaration in candidate.user_name_declarations
                              for name in (declaration.full_name, declaration.label, declaration.abbreviation) if name))


def _aliases(candidate):
    return list(dict.fromkeys([candidate.name, *candidate.aliases, *declared_aliases(candidate.name),
                              *declared_candidate_names(candidate)]))


def _molecule_query(request):
    if request.name == "pubchem":
        return request.arguments.get("name", "")
    if request.name == "chembl":
        return request.arguments.get("molecule_name", "") or (
            request.arguments.get("query", "") if request.arguments.get("entity_type") == "molecule"
            or request.arguments.get("resource") == "molecule" else "")
    return ""


def merge_identity_annotations(destination, incoming):
    """Preserve aliases and provenance when the existing resolver merges IDs."""
    if destination.candidate_id != incoming.candidate_id or any(
        getattr(destination, field) != getattr(incoming, field)
        for field in ("smiles", "inchikey", "pubchem_cid")
    ):
        return False
    destination.aliases = list(dict.fromkeys([*destination.aliases, incoming.name, *incoming.aliases,
                                             *declared_aliases(incoming.name)]))
    seen_declarations = {digest(d.model_dump()) for d in destination.user_name_declarations}
    for declaration in incoming.user_name_declarations:
        if digest(declaration.model_dump()) not in seen_declarations:
            destination.user_name_declarations.append(declaration.model_copy(deep=True))
            seen_declarations.add(digest(declaration.model_dump()))
    destination.identity_source_ids = list(dict.fromkeys([*destination.identity_source_ids,
                                                          *incoming.identity_source_ids]))
    destination.original_ids = list(dict.fromkeys([*destination.original_ids, *incoming.original_ids,
        *([incoming.entity_id] if incoming.entity_id and incoming.entity_id != destination.entity_id else [])]))
    destination.identity_revision = max(destination.identity_revision, incoming.identity_revision)
    if incoming.identity_assessment_pending and not destination.identity_assessment_pending:
        destination.identity_assessment_pending = True
        destination.identity_priority_before_update = incoming.identity_priority_before_update
        destination.identity_assessment_fingerprint = incoming.identity_assessment_fingerprint
    seen = {digest(o) for o in destination.identity_history}
    for observation in incoming.identity_history:
        key = digest(observation)
        if key not in seen:
            destination.identity_history.append(dict(observation))
            seen.add(key)
    return True


class CandidateIdentitySync:
    def __init__(self, run, state=None):
        self.run, self.state = run, state

    def _writable(self):
        return self.run.status in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}

    def bind(self, request):
        """Freeze a unique exact recorded alias match before the tool executes.

        Neither hypothesis IDs nor molecular name similarity grants a binding.
        The saved binding is checked again when the observation is applied.
        """
        query = _molecule_query(request)
        if not isinstance(query, str) or not query:
            return {}
        candidates = [c for c in self.run.candidates if _exact(query) in {_exact(n) for n in _aliases(c)}]
        if len(candidates) != 1:
            return {"status": "ambiguous" if candidates else "unlinked", "query": query,
                    "reason": "A unique exact recorded candidate alias is required"}
        candidate = candidates[0]
        if candidate.role == "compound_class":
            return {"status": "unlinked", "query": query, "reason": "Candidate is a compound class"}
        return {"status": "verified", "candidate_id": candidate.candidate_id,
                "entity_id": candidate.entity_id or candidate.candidate_id,
                "query": query, "verification": "unique_exact_recorded_alias"}

    def _linked(self, link, request):
        query = _molecule_query(request)
        if not link or link.get("status") != "verified" or link.get("verification") != "unique_exact_recorded_alias":
            return None
        if not query or _exact(query) != _exact(link.get("query", "")):
            return None
        if link.get("entity_id"):
            matches = [c for c in self.run.candidates if link["entity_id"] ==
                       (c.entity_id or c.candidate_id)]
        else:
            matches = [c for c in self.run.candidates if link.get("candidate_id") in
                       {c.candidate_id, *c.original_ids}]
        alias_matches = [c for c in self.run.candidates if _exact(query) in {_exact(n) for n in _aliases(c)}]
        if len(matches) != 1 or len(alias_matches) != 1 or matches[0] is not alias_matches[0]:
            return None
        return matches[0]

    def apply(self, plan, request, result):
        if request.name != "pubchem":
            return {}
        if not self._writable():
            return {"status": "read_only", "reason": "Completed research entities remain immutable"}
        candidate = self._linked(plan.get("candidate_link", {}), request)
        if candidate is None:
            return {"status": "unlinked", "reason": "No unique verified candidate binding"}
        query = _molecule_query(request)
        candidate.entity_id = candidate.entity_id or candidate.candidate_id
        candidate.aliases = list(dict.fromkeys([*candidate.aliases, *declared_aliases(candidate.name)]))
        observation = {"plan_id": plan.get("plan_id", ""), "call_id": result.call_id,
                       "query": query, "status": result.identity.get("status", result.status),
                       "source_ids": [s.source_id for s in result.sources],
                       "previous_identity_status": candidate.identity_status,
                       "previous_candidate_id": candidate.candidate_id}
        if result.status not in {"ok", "truncated"} or result.identity.get("status") != "resolved":
            observation["application_status"] = "not_resolved"
            if candidate.identity_status != "resolved":
                candidate.identity_status = "needs_review"
            self._remember(candidate, observation)
            self.refresh_references()
            return {"status": "not_resolved", "entity_id": candidate.entity_id}
        record = result.records[0] if len(result.records) == 1 else {}
        values = {k: record.get(k, "") for k in ("candidate_id", "smiles", "inchikey", "pubchem_cid")}
        valid = (all(isinstance(v, str) and v for v in values.values()) and
                 values["pubchem_cid"].isdigit() and int(values["pubchem_cid"]) > 0 and
                 result.identity.get("entity_type") == "molecule" and
                 _exact(result.identity.get("query", "")) == _exact(query) and
                 _exact(record.get("name_query", "")) == _exact(query) and
                 values["pubchem_cid"] == result.identity.get("selected_id") and
                 values["inchikey"] == result.identity.get("inchikey") and
                 any(s.provider == "pubchem" and s.source_id == "pubchem:" + values["pubchem_cid"]
                     for s in result.sources))
        if valid:
            from rdkit import Chem
            molecule = Chem.MolFromSmiles(values["smiles"])
            valid = bool(molecule and molecule.GetNumAtoms() and
                         Chem.MolToInchiKey(molecule) == values["inchikey"] and
                         values["candidate_id"] == "mol_" + digest([
                             Chem.MolToSmiles(molecule, isomericSmiles=True), values["inchikey"]])[:16])
        conflict = any(getattr(candidate, k) and getattr(candidate, k) != values[k]
                       for k in ("smiles", "inchikey", "pubchem_cid"))
        if not valid or conflict:
            observation["application_status"] = "conflict" if conflict else "invalid_observation"
            candidate.identity_status = "needs_review"
            self._remember(candidate, observation)
            self.refresh_references()
            return {"status": observation["application_status"], "entity_id": candidate.entity_id,
                    "reason": "Chemical identities conflict or observation failed verification"}
        changed = candidate.identity_status != "resolved" or any(getattr(candidate, k) != v for k, v in values.items())
        if changed:
            if plan.get("origin") != "supervisor_identity":
                # Resolving a late identity cannot reclassify scientific priority
                # when finalize/UI recompute decisions using the same evidence.
                if not candidate.identity_assessment_pending:
                    candidate.identity_priority_before_update = (
                        candidate.decision if candidate.identity_status == "resolved" else "hold")
                    candidate.identity_assessment_fingerprint = candidate_assessment_fingerprint(candidate, self.run.evidence)
                candidate.identity_assessment_pending = True
            observation["previous_identity"] = {k: getattr(candidate, k) for k in values}
            observation["historical_identity_gaps"] = [u for u in candidate.uncertainties if u.startswith("Identity unresolved:")]
            candidate.uncertainties = [u for u in candidate.uncertainties if not u.startswith("Identity unresolved:")]
            if candidate.candidate_id != values["candidate_id"]:
                candidate.original_ids = list(dict.fromkeys([*candidate.original_ids, candidate.candidate_id]))
            for key, value in values.items():
                setattr(candidate, key, value)
            candidate.identity_status = "resolved"
            candidate.identity_revision += 1
        candidate.identity_source_ids = list(dict.fromkeys([*candidate.identity_source_ids,
                                                           *[s.source_id for s in result.sources]]))
        candidate.identity_form = {"isomeric_smiles": candidate.smiles,
                                   "components": candidate.smiles.split(".")}
        observation["application_status"] = "applied" if changed else "unchanged"
        self._remember(candidate, observation)
        self.refresh_references()
        return {"status": observation["application_status"], "entity_id": candidate.entity_id,
                "candidate_id": candidate.candidate_id, "identity_revision": candidate.identity_revision,
                "scientific_assessment": "unchanged"}

    @staticmethod
    def _remember(candidate, observation):
        # Reused/duplicate plans must not manufacture repeated identity revisions.
        key = (observation["call_id"], observation["query"], observation["status"])
        if not any((o.get("call_id"), o.get("query"), o.get("status")) == key for o in candidate.identity_history):
            candidate.identity_history.append(observation)

    def record_initial(self, candidate, identity, source):
        """Use the same verifier for the classic initial connector response."""
        if not any(entity is candidate for entity in self.run.candidates):
            return {"status": "unlinked", "reason": "Candidate is not part of the current run"}
        if not self._writable():
            return {"status": "read_only", "reason": "Completed research entities remain immutable"}
        # Reuse the supervisor observation's call reference, then verify its
        # returned fields through the same path. The adapter is not another call.
        call_id = next((o["call_id"] for o in candidate.identity_history if
                        o.get("application_status") in {"applied", "unchanged"} and
                        _exact(o.get("query", "")) == _exact(candidate.name) and
                        source.source_id in o.get("source_ids", []) and o.get("status") == "resolved"),
                       "initial_identity:" + (candidate.entity_id or candidate.candidate_id))
        request = ToolRequest(name="pubchem", arguments={"name": candidate.name})
        result = ToolResult(tool_name="pubchem", provider="pubchem", parameters=request.arguments,
                            status="ok", retrieved_at=source.retrieved_at, sources=[source],
                            records=[{"name_query": candidate.name, **identity}],
                            identity={"status": "resolved", "entity_type": "molecule", "query": candidate.name,
                                      "selected_id": identity.get("pubchem_cid", ""),
                                      "inchikey": identity.get("inchikey", "")},
                            call_id=call_id)
        # The initial resolver receives a specific current candidate object. This
        # is an explicit association, even before duplicate provisional IDs have
        # been merged. Follow-up plans still require a unique saved alias/ID.
        selected_run = self.run.model_copy(update={"candidates": [candidate]})
        selected_sync = CandidateIdentitySync(selected_run)
        application = selected_sync.apply({"origin": "supervisor_identity", "candidate_link": selected_sync.bind(request)}, request, result)
        self.refresh_references()
        return application

    def refresh_references(self):
        if self.state is None or not self._writable():
            return
        references = []
        latest_cards = {}
        for card in self.state.hypotheses:
            if card.hypothesis_id not in latest_cards or card.version > latest_cards[card.hypothesis_id].version:
                latest_cards[card.hypothesis_id] = card
        for candidate in self.run.candidates:
            latest = [card for card in latest_cards.values() if any(
                _exact(n) in {_exact(a) for a in _aliases(candidate)} for n in card.molecule_names)]
            references.append({"entity_id": candidate.entity_id or candidate.candidate_id,
                "candidate_id": candidate.candidate_id, "name": candidate.name, "aliases": _aliases(candidate),
                "user_name_declarations": [d.model_dump() for d in candidate.user_name_declarations],
                "identity_status": candidate.identity_status, "identity_revision": candidate.identity_revision,
                "identity_assessment_pending": candidate.identity_assessment_pending,
                "identity_priority_before_update": candidate.identity_priority_before_update,
                "source_ids": list(candidate.identity_source_ids),
                "hypothesis_versions": [{"hypothesis_id": h.hypothesis_id, "version": h.version,
                    "historical_identity_gaps": [g for g in h.inference_gaps if
                        any(term in g.casefold() for term in ("identity", "pubchem", "身份"))]}
                    for h in latest],
                "scientific_assessment": "Chemical identity is recorded here; activity, production and research priority have separate assessments"})
        self.state.entity_references = references

