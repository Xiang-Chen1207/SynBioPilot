"""Deterministic, bounded execution plans over the eight read-only tools.

Models propose gaps. This compiler resolves identities and validates parameters;
it never runs a free-form ReAct loop or chooses the first search candidate.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from .store import digest
from .tools import (
    CHEMBL_PATTERN,
    SPECIES_PATTERN,
    ChEMBLArguments,
    ToolRequest,
    ToolResult,
    _Page,
)


class ChEMBLIntent(_Page):
    resource: Literal["molecule", "target", "activity", "mechanism", "assay"]
    chembl_id: str | None = Field(default=None, pattern=CHEMBL_PATTERN, max_length=40)
    molecule_chembl_id: str | None = Field(default=None, pattern=CHEMBL_PATTERN, max_length=40)
    target_chembl_id: str | None = Field(default=None, pattern=CHEMBL_PATTERN, max_length=40)
    molecule_name: str | None = Field(default=None, min_length=1, max_length=200)
    target_symbol: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", max_length=80)
    species: str | None = Field(default=None, pattern=SPECIES_PATTERN, max_length=100)
    query: str | None = Field(default=None, min_length=2, max_length=150)
    entity_type: Literal["molecule", "target"] | None = None

    @model_validator(mode="after")
    def compatible(self):
        if self.resource == "molecule" and (self.target_symbol or self.target_chembl_id or
                                           self.molecule_chembl_id or self.species):
            raise ValueError("molecule identity only accepts molecule name or chembl_id")
        if self.resource == "target" and (self.molecule_name or self.molecule_chembl_id or self.target_chembl_id):
            raise ValueError("target identity only accepts target symbol or chembl_id")
        if self.resource == "assay" and (self.molecule_name or self.molecule_chembl_id or self.query):
            raise ValueError("assay requires assay ID or target selector")
        if self.chembl_id and (self.resource in {"activity", "mechanism"} or
                              self.molecule_name or self.target_symbol or
                              self.molecule_chembl_id or self.target_chembl_id):
            raise ValueError("chembl_id cannot be combined with other identities or experimental resources")
        if self.entity_type and not self.query:
            raise ValueError("entity_type only disambiguates a query")
        if self.entity_type and self.resource in {"molecule", "target"} and self.entity_type != self.resource:
            raise ValueError("query entity_type must match its identity resource")
        if self.molecule_name and self.molecule_chembl_id or self.target_symbol and self.target_chembl_id:
            raise ValueError("Provide a name or ID per entity, not both")
        if self.query and (self.molecule_name or self.target_symbol or self.chembl_id):
            raise ValueError("query cannot be combined with another name selector")
        # Resolve the query's entity before conversion. A typed experimental query
        # may constrain the other entity by ID, but cannot overwrite its own ID.
        query_entity = self.entity_type or (self.resource if self.resource in {"molecule", "target"} else None)
        if self.query and ((query_entity == "molecule" and self.molecule_chembl_id) or
                           (query_entity == "target" and self.target_chembl_id)):
            raise ValueError("Provide a query or ID per entity, not both")
        # Mechanism has no species filter. Species is meaningful here only when
        # it is checked during target identity resolution before the record query.
        if self.resource == "mechanism" and self.species and not (
            self.target_symbol or self.target_chembl_id or (self.query and query_entity == "target")
        ):
            raise ValueError("mechanism species requires a target identity selector")
        if self.target_symbol and not self.species:
            raise ValueError("Protein symbol resolution requires explicit species")
        return self


class ToolScheduler:
    """Every atomic step uses the caller's ledger and durable tool executor."""

    def __init__(self, execute, plans: list[dict[str, Any]], persist=lambda: None,
                 *, bind_candidate=None, apply_identity=None):
        self.execute, self.plans, self.persist = execute, plans, persist
        self.bind_candidate, self.apply_identity = bind_candidate, apply_identity
        self.memo: dict[str, ToolResult] = {}
        self.plan_keys: dict[str, str] = {}

    def create(self, request, *, origin="model_gap", reason="Evidence gap requires a bounded query",
               hypothesis_ids=(), gap_id=""):
        key = digest(request.model_dump())
        plan_id = "plan_" + digest([len(self.plans), origin, key])[:12]
        plan = {"plan_id": plan_id, "gap_id": gap_id or "gap_" + digest([origin, key])[:12],
                "origin": origin, "reason": reason, "hypothesis_ids": list(hypothesis_ids),
                "request": request.model_dump(), "status": "planned", "call_ids": [],
                "identity": {}, "stop_reason": "", "duplicate_of": self.plan_keys.get(key, "")}
        if self.bind_candidate:
            plan["candidate_link"] = self.bind_candidate(request)
        self.plans.append(plan)
        if plan["duplicate_of"]:
            plan.update(status="skipped", stop_reason="duplicate_plan")
        else:
            self.plan_keys[key] = plan_id
        self.persist()
        return plan

    def _call(self, plan, request, parent="", identity=None):
        key = digest(request.model_dump())
        if key in self.memo:
            result = self.memo[key]
        else:
            result = self.execute(request, plan_id=plan["plan_id"], gap_id=plan["gap_id"],
                                  parent_call_id=parent, identity=identity)
            self.memo[key] = result
        if result.call_id and result.call_id not in plan["call_ids"]:
            plan["call_ids"].append(result.call_id)
        if result.identity:
            plan["identity"] = result.identity
        if self.apply_identity:
            application = self.apply_identity(plan, request, result)
            if application:
                plan["identity_application"] = application
        self.persist()
        return result

    def _identity(self, plan, request, parent=""):
        result = self._call(plan, request, parent)
        if result.status not in ("ok", "truncated") or result.identity.get("status") != "resolved":
            plan.update(status="unresolved" if result.status in ("ok", "truncated", "empty") or
                        result.identity.get("status") in {"ambiguous", "mismatch", "unresolved"} else result.status,
                        stop_reason=result.stop_reason or "identity_" + result.identity.get("status", "unresolved"))
            return None
        return result

    def run(self, plan):
        if plan["status"] == "skipped":
            # A previously observed identity may now have an explicitly bound
            # candidate. Reuse its observation without another provider call.
            request = ToolRequest.model_validate(plan["request"])
            result = self.memo.get(digest(request.model_dump()))
            if result is not None and self.apply_identity:
                application = self.apply_identity(plan, request, result)
                if application:
                    plan["identity_application"] = application
                self.persist()
            return None
        plan["status"] = "running"
        self.persist()
        request = ToolRequest.model_validate(plan["request"])
        try:
            if request.name != "chembl":
                result = self._call(plan, request)
            else:
                result = self._chembl(plan, request)
            if result is not None:
                plan.update(status=result.status, stop_reason=result.stop_reason)
            return result
        finally:
            self.persist()

    def _chembl(self, plan, request):
        # The legacy low-level schema continues to reject missing IDs. Name-bearing
        # intents are compiled into separate validated identity and record steps.
        try:
            args = ChEMBLIntent.model_validate(request.arguments)
        except ValidationError:
            return self._call(plan, request)  # real executor records invalid, no network
        names = args.molecule_name or args.target_symbol
        if args.query and args.resource not in {"molecule", "target"}:
            if not args.entity_type:
                plan.update(status="unresolved", stop_reason="identity_type_required",
                            identity={"status": "unresolved", "query": args.query,
                                      "reason": "Specify molecule_name or target_symbol/species; entity type is unknown"})
                return None
            if args.entity_type == "target" and not args.species:
                plan.update(status="unresolved", stop_reason="species_required")
                return None
            compiled = args.model_dump(exclude={"query", "entity_type"}, exclude_none=True)
            compiled["molecule_name" if args.entity_type == "molecule" else "target_symbol"] = args.query
            try:
                args = ChEMBLIntent.model_validate(compiled)
            except ValidationError:
                return self._call(plan, request)  # invalid converted selectors never reach HTTP
            names = True
        if args.resource in {"molecule", "target"} and not names:
            # Direct bounded identity lookup remains available as before.
            raw = args.model_dump(exclude_none=True, exclude={"entity_type"})
            return self._call(plan, ToolRequest(name="chembl", arguments=raw))
        parent = ""
        ids = {}
        for entity, name, identifier in (
            ("molecule", args.molecule_name, args.molecule_chembl_id),
            ("target", args.target_symbol, args.target_chembl_id),
        ):
            if not (name or identifier):
                continue
            if name:
                probe = ToolRequest(name="pubchem" if entity == "molecule" else "uniprot",
                                    arguments={"name": name} if entity == "molecule" else
                                    {"gene_symbol": name, "species": args.species})
                resolved = self._identity(plan, probe, parent)
                if resolved is None:
                    return None
                parent = resolved.call_id
                selector = ({"inchikey": resolved.identity.get("inchikey")} if entity == "molecule"
                            else {"target_accession": resolved.identity["selected_id"], "species": args.species})
            else:
                selector = {"chembl_id": identifier}
                if entity == "target":
                    selector["species"] = args.species
            resolved = self._identity(plan, ToolRequest(name="chembl", arguments={
                "resource": entity, **selector, "limit": 5, "page": 1}), parent)
            if resolved is None:
                return None
            parent = resolved.call_id
            ids[entity + "_chembl_id"] = resolved.identity["selected_id"]
        if args.resource in {"molecule", "target"}:
            return resolved if ids else self._call(plan, request)
        parameters = {"resource": args.resource, "limit": args.limit, "page": args.page, **ids}
        if args.chembl_id:
            parameters["chembl_id"] = args.chembl_id
        if args.species and args.resource != "mechanism":
            parameters["species"] = args.species
        try:
            ChEMBLArguments.model_validate(parameters)
        except ValidationError:
            return self._call(plan, request, parent)
        plan["identity"] = {**plan["identity"], **ids}
        result = self._call(plan, ToolRequest(name="chembl", arguments=parameters), parent,
                            identity=plan["identity"])
        # The experiment rows remain annotations with assay context, not inferred
        # intervention efficacy, chassis yield or numerical PKPD parameters.
        return result

