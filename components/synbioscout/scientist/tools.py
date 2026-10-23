"""Small, read-only biological tools with strict contracts and public API allowlists.

The registry deliberately contains no model inference, arbitrary URLs, shell execution,
screening, docking, ADMET, or molecular dynamics. All network activity goes through
the caller's BiomedicalSources HTTP client, including its cache and shared budgets.
"""
from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .http import DataSourceError
from .models import SourceRecord
from .sources import BiomedicalSources, IdentityResolutionError
from .store import digest, utc_now

CHEMBL_URL = "https://www.ebi.ac.uk/chembl/api/data"
REACTOME_URL = "https://reactome.org/ContentService/data"
QUICKGO_URL = "https://www.ebi.ac.uk/QuickGO/services/annotation/search"
JSON_HEADERS = {"Accept": "application/json"}
UNIPROT_PATTERN = r"^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})(?:-[1-9][0-9]*)?$"
SPECIES_PATTERN = r"^[A-Z][A-Za-z]+(?: [A-Za-z][A-Za-z.-]*){1,3}$"
CHEMBL_PATTERN = r"^CHEMBL[1-9][0-9]*$"
REACTOME_SPECIES = {
    9606: "Homo sapiens", 10090: "Mus musculus", 10116: "Rattus norvegicus",
    7227: "Drosophila melanogaster", 6239: "Caenorhabditis elegans",
    7955: "Danio rerio", 4932: "Saccharomyces cerevisiae", 9031: "Gallus gallus",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class ToolRequest(_Strict):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(_Strict):
    tool_name: str
    provider: str
    parameters: dict[str, Any]
    status: Literal["ok", "empty", "unavailable", "truncated", "invalid", "budget_exhausted"]
    retrieved_at: str
    records: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[SourceRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    request_count: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    call_id: str = ""
    parent_call_id: str = ""
    plan_id: str = ""
    gap_id: str = ""
    requested_parameters: dict[str, Any] = Field(default_factory=dict)
    identity: dict[str, Any] = Field(default_factory=dict)
    http_trace: list[dict[str, Any]] = Field(default_factory=list)
    pagination: dict[str, Any] = Field(default_factory=dict)
    record_count: int = Field(default=0, ge=0)
    stop_reason: str = ""


class _Limit(_Strict):
    limit: int = Field(default=5, ge=1, le=10)


class _Page(_Limit):
    page: int = Field(default=1, ge=1, le=2)


class LiteratureArguments(_Limit):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=3, ge=1, le=10)


class OpenTargetsArguments(_Limit):
    query: str = Field(min_length=2, max_length=250)
    entity: Literal["disease", "target"]


class DiseaseArguments(_Limit):
    disease_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9]+$", max_length=80)


class UniProtArguments(_Strict):
    gene_symbol: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", max_length=80)
    species: str = Field(default="Homo sapiens", pattern=SPECIES_PATTERN, max_length=100)


class PubChemArguments(_Strict):
    name: str = Field(min_length=1, max_length=200)


class ChEMBLArguments(_Page):
    resource: Literal["molecule", "target", "activity", "mechanism", "assay"]
    chembl_id: str | None = Field(default=None, pattern=CHEMBL_PATTERN, max_length=40)
    query: str | None = Field(default=None, min_length=2, max_length=150)
    molecule_chembl_id: str | None = Field(default=None, pattern=CHEMBL_PATTERN, max_length=40)
    target_chembl_id: str | None = Field(default=None, pattern=CHEMBL_PATTERN, max_length=40)
    species: str | None = Field(default=None, pattern=SPECIES_PATTERN, max_length=100)
    inchikey: str | None = Field(default=None, pattern=r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
    target_accession: str | None = Field(default=None, pattern=UNIPROT_PATTERN, max_length=20)

    @model_validator(mode="after")
    def constrained_resource(self):
        if self.resource in {"molecule", "target"}:
            if sum(bool(v) for v in (self.chembl_id, self.query, self.inchikey,
                                     self.target_accession)) != 1:
                raise ValueError("molecule/target requires exactly one identity selector")
            if self.inchikey and self.resource != "molecule":
                raise ValueError("InChIKey only selects molecule identities")
            if self.target_accession and (self.resource != "target" or not self.species):
                raise ValueError("target accession requires target resource and species")
            if self.molecule_chembl_id or self.target_chembl_id:
                raise ValueError("use chembl_id for molecule/target records")
        elif self.resource == "assay":
            if bool(self.chembl_id) == bool(self.target_chembl_id):
                raise ValueError("assay requires exactly one chembl_id or target_chembl_id")
            if self.query or self.molecule_chembl_id:
                raise ValueError("assay only accepts its ID or a target ID")
        else:
            if not (self.molecule_chembl_id or self.target_chembl_id):
                raise ValueError("activity/mechanism requires a molecule or target ID")
            if self.query or self.chembl_id:
                raise ValueError("activity/mechanism does not accept query/chembl_id")
        if self.resource not in {"molecule", "target"} and (self.inchikey or self.target_accession):
            raise ValueError("identity selectors cannot query experimental records")
        if self.species and self.resource in {"molecule", "mechanism"}:
            raise ValueError("this resource does not support a verifiable species filter")
        return self


class ReactomeArguments(_Page):
    uniprot_id: str = Field(pattern=UNIPROT_PATTERN, max_length=20)
    taxon_id: int = Field(default=9606, ge=1, le=3_000_000_000)

    @model_validator(mode="after")
    def supported_taxon(self):
        if self.taxon_id not in REACTOME_SPECIES:
            raise ValueError("taxon_id is not in the supported, species-checked Reactome allowlist")
        return self


class QuickGOArguments(_Page):
    uniprot_id: str = Field(pattern=UNIPROT_PATTERN, max_length=20)
    taxon_id: int = Field(default=9606, ge=1, le=3_000_000_000)
    go_id: str | None = Field(default=None, pattern=r"^GO:[0-9]{7}$")
    evidence_code: str | None = Field(default=None, pattern=r"^ECO:[0-9]{7}$")
    aspect: Literal["biological_process", "molecular_function", "cellular_component"] | None = None


_TOOLS = {
    "europepmc_search": (
        "europe_pmc", LiteratureArguments,
        "检索文献摘要和 DOI/PMID；全文获取通过独立工具执行，读取范围随来源记录。",
    ),
    "opentargets_search": (
        "open_targets", OpenTargetsArguments,
        "解析疾病或靶点名称；同名结果是待核对的身份线索。",
    ),
    "disease_targets": (
        "open_targets", DiseaseArguments,
        "检索疾病—靶点关联及其数据来源，关联分数按数据库定义记录。",
    ),
    "uniprot": (
        "uniprot", UniProtArguments,
        "按基因符号及物种核对唯一蛋白身份，保留原始 UniProt 数据。",
    ),
    "pubchem": (
        "pubchem", PubChemArguments,
        "核对唯一化学身份与 SMILES/InChIKey；活性与生产能力通过对应评估记录。",
    ),
    "chembl": (
        "chembl", ChEMBLArguments,
        "有界查询分子、靶点、assay、bioactivity 或 mechanism；保留单位、关系符号和物种。",
    ),
    "reactome_pathways": (
        "reactome", ReactomeArguments,
        "查已核对物种的 UniProt—通路归属，本地返回最多两页注释记录。",
    ),
    "quickgo_annotations": (
        "quickgo", QuickGOArguments,
        "按蛋白与 taxon 查询 GO 注释，保留 qualifier、ECO、GO evidence 和参考来源。",
    ),
}
TOOL_NAMES = frozenset(_TOOLS)


def _rows(value: Any, field: str | None = None) -> list[dict[str, Any]]:
    if field is not None:
        if not isinstance(value, dict) or field not in value:
            raise DataSourceError("Provider response is missing its record collection")
        value = value[field]
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise DataSourceError("Provider returned a malformed record collection")
    return value


class _ProviderSchemaError(DataSourceError):
    """A known provider identity field has an invalid JSON shape."""


def _chembl_identity_schema(resource: str, rows: list[dict[str, Any]]) -> None:
    """Validate nested fields before accessing or saving an identity snapshot.

    Optional/null provider fields may describe an incomplete identity, which the
    identity matcher keeps unresolved. Incorrect containers or scalar types are
    response failures, never identity evidence or successful empty collections.
    Only the documented fields read by this adapter are inspected here.
    """
    def strings(value, fields):
        if any(value.get(field) is not None and not isinstance(value[field], str)
               for field in fields):
            raise _ProviderSchemaError("ChEMBL identity scalar has an invalid JSON type")

    def object_field(row, field, fields):
        value = row.get(field)
        if value is None:
            return
        if not isinstance(value, dict):
            raise _ProviderSchemaError("ChEMBL identity object has an invalid JSON type")
        strings(value, fields)

    def object_list(row, field, fields):
        value = row.get(field)
        if value is None:
            return
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise _ProviderSchemaError("ChEMBL identity collection has an invalid JSON type")
        for item in value:
            strings(item, fields)

    for row in rows:
        strings(row, (resource + "_chembl_id", "pref_name", "organism", "target_type"))
        object_field(row, "molecule_structures", ("standard_inchi_key", "canonical_smiles"))
        object_field(row, "molecule_hierarchy", ("molecule_chembl_id", "parent_chembl_id"))
        object_list(row, "molecule_synonyms", ("molecule_synonym", "syn_type"))
        object_list(row, "target_components", ("accession", "component_type"))


def _counter(http, name):
    value = getattr(http, name, 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _is_documented_no_mapping(exc: DataSourceError) -> bool:
    """Reactome specifically documents HTTP 404 for no identifier/pathway mappings."""
    response = getattr(exc.__cause__, "response", None)
    return response is not None and response.status_code == 404


class ToolRegistry:
    """Expose only fixed, typed public-data operations, never arbitrary endpoints."""

    def __init__(self, sources: BiomedicalSources, *, identity_sources=None):
        self.sources = sources
        self.identity_sources = identity_sources or sources

    def catalog(self) -> list[dict[str, Any]]:
        catalog = [{"name": name, "provider": provider, "description": description,
                 "parameters_schema": args.model_json_schema(), "read_only": True,
                 "network_only": True, "max_records": 10}
                for name, (provider, args, description) in _TOOLS.items()]
        from .tool_plans import ChEMBLIntent
        next(t for t in catalog if t["name"] == "chembl").update(
            plan_parameters_schema=ChEMBLIntent.model_json_schema(),
            planning="For activity/mechanism without IDs, propose molecule_name or target_symbol/species. "
                     "Supervisor compiler resolves a unique identity before record queries; ambiguity stops the plan.")
        return catalog

    def execute(self, request: ToolRequest) -> ToolResult:
        provider = _TOOLS.get(request.name, ("none",))[0]
        result = ToolResult(tool_name=request.name, provider=provider,
                            parameters=request.arguments, status="invalid", retrieved_at=utc_now())
        if request.name not in _TOOLS:
            result.warnings = ["Unknown tool; only catalog entries may be called"]
            return result
        try:
            arguments = _TOOLS[request.name][1].model_validate(request.arguments)
        except ValidationError as exc:
            known_fields = _TOOLS[request.name][1].model_fields
            # Extra-field names are untrusted inputs too. Do not echo a prompt,
            # credential or local path supplied as an arbitrary dictionary key.
            result.warnings = ["Invalid parameter " + ".".join(
                str(part) if part in known_fields or isinstance(part, int) else "unrecognized_field"
                for part in e["loc"]) + ": " + e["msg"]
                for e in exc.errors(include_input=False)]
            return result
        result.parameters = arguments.model_dump(exclude_none=True)
        before = {name: _counter(self.sources.http, name)
                  for name in ("request_count", "cache_hits")}
        trace_start = len(getattr(self.sources.http, "request_events", []))
        try:
            getattr(self, "_" + request.name)(arguments, result)
        except IdentityResolutionError as exc:
            result.status = "empty" if exc.identity_status == "empty" else "unavailable"
            result.identity = {"status": exc.identity_status, "candidates": exc.candidates,
                               "reason": "Identity remains unresolved; no entity was selected"}
            if request.name == "uniprot":
                result.identity.update(entity_type="target", query=arguments.gene_symbol, species=arguments.species)
            elif request.name == "pubchem":
                result.identity.update(entity_type="molecule", query=arguments.name)
            result.stop_reason = "identity_" + exc.identity_status
            result.warnings.append("Identity remains unresolved; explicit identity selection required")
        except _ProviderSchemaError:
            result.status = "unavailable"
            result.records, result.sources = [], []
            result.identity = {"status": "unavailable", "entity_type": arguments.resource,
                               "selected_id": "", "candidates": [],
                               "reason": "Provider identity response failed schema validation"}
            result.stop_reason = "provider_schema"
            result.warnings.append("Provider identity response failed schema validation; no entity selected")
        except (RuntimeError, ValueError, TypeError, KeyError, TimeoutError) as exc:
            # Budget callbacks can originate outside this module. Recognize their explicit
            # budget message, but never turn unavailable services into successful no-hits.
            message = str(exc).lower()
            exhausted = "budget" in message and ("exhaust" in message or "exceed" in message)
            result.status = "budget_exhausted" if exhausted else "unavailable"
            result.records = []
            result.sources = []
            result.warnings.append("Shared request budget exhausted" if exhausted else
                                   "Provider request/response failed: " + type(exc).__name__)
        finally:
            result.request_count = max(0, _counter(self.sources.http, "request_count") -
                                       before["request_count"])
            result.cache_hits = max(0, _counter(self.sources.http, "cache_hits") -
                                    before["cache_hits"])
            result.http_trace = getattr(self.sources.http, "request_events", [])[trace_start:]
            result.record_count = len(result.records)
            if not result.stop_reason:
                result.stop_reason = {"truncated": "return_limit", "empty": "no_records",
                                      "unavailable": "provider_failure",
                                      "budget_exhausted": "shared_budget"}.get(result.status, "")
        return result

    def _database(self, result, records, url, *, metadata=None, truncated=False):
        result.records = records
        result.status = "truncated" if records and truncated else ("ok" if records else "empty")
        value = {"parameters": result.parameters, "records": records,
                 "scope": "database_annotation_not_intervention_or_production_proof",
                 **(metadata or {})}
        result.sources = [self.sources.database_source(
            result.provider + ":tool:" + digest([result.tool_name, value])[:20],
            f"{result.provider} {result.tool_name} bounded query", value, url, result.provider)]
        if truncated:
            result.warnings.append("Bounded subset only; more records/pages are not fetched")

    def _europepmc_search(self, args, result):
        sources = self.sources.search_literature(args.query, args.limit)
        # No implicit full-text fetch. Each source is an abstract-level snapshot.
        sources = sources[:args.limit]
        result.sources = sources
        result.records = [source.model_dump() for source in sources]
        scope = sources[0].metadata.get("search_scope", {}) if sources else {}
        if not isinstance(scope, dict) or not scope:
            saved = getattr(self.sources, "last_literature_scope", {})
            # A previous query's envelope cannot supply this query's coverage.
            scope = saved if isinstance(saved, dict) and saved.get("query") == args.query else {}
        total = scope.get("total_hits")
        total = total if type(total) is int and total >= 0 else None
        has_more = scope.get("has_more")
        known = type(has_more) is bool or total is not None
        truncated = (has_more is True or (total is not None and total > len(sources))) if known else len(sources) >= args.limit
        result.status = "truncated" if truncated else ("ok" if sources else "empty")
        result.warnings = ["Source scope: abstracts; query scope and returned records accompany each result"]
        if truncated and known:
            result.warnings.append("Bounded search page only; additional reported hits/pages were not fetched")
        elif truncated and not known:
            result.warnings.append("Result limit reached; total literature coverage is unknown")
        elif not known:
            result.warnings.append("Returned-batch scope recorded; total literature coverage: unknown")

    def _opentargets_search(self, args, result):
        rows = _rows(self.sources.search_entity(args.query, args.entity))
        cap = min(args.limit, 5)  # Existing connector intentionally fetches one five-hit batch.
        self._database(result, rows[:cap], "https://platform.opentargets.org/",
                       metadata={"provider_batch_limit": 5}, truncated=len(rows) >= cap)
        result.warnings.append("Name matches are identity leads, not disease/target validation")

    def _disease_targets(self, args, result):
        disease = self.sources.disease_targets(args.disease_id, args.limit)
        if not isinstance(disease, dict):
            raise DataSourceError("Malformed disease response")
        rows = _rows(disease.get("associatedTargets", {}).get("rows", []))
        records = [{"disease_id": disease.get("id", args.disease_id),
                    "disease_name": disease.get("name", ""), **row} for row in rows[:args.limit]]
        self._database(result, records, "https://platform.opentargets.org/disease/" + args.disease_id,
                       truncated=len(rows) >= args.limit)
        result.warnings.append("Disease-target association scores and sources are recorded; intervention evidence has its own study records")

    def _uniprot(self, args, result):
        accession, source = self.identity_sources.resolve_target(args.gene_symbol, args.species)
        result.records = [{"uniprot_id": accession, **source.metadata}]
        result.sources = [source]
        result.status = "ok"
        result.identity = {"status": "resolved", "entity_type": "target", "query": args.gene_symbol,
                           "species": args.species, "selected_id": accession}
        result.warnings.append("Protein identity, species and functional annotations are returned with their sources")

    def _pubchem(self, args, result):
        identity, source = self.identity_sources.resolve_molecule(args.name)
        result.records = [{"name_query": args.name, **identity}]
        result.sources = [source]
        result.status = "ok"
        result.identity = {"status": "resolved", "entity_type": "molecule", "query": args.name,
                           "selected_id": identity["pubchem_cid"], "inchikey": identity["inchikey"]}
        result.warnings.append("Chemical identity only; activity and chassis production remain untested")

    def _chembl(self, args, result):
        params = {"limit": args.limit, "offset": (args.page - 1) * args.limit}
        if args.chembl_id:
            id_field = {"molecule": "molecule_chembl_id", "target": "target_chembl_id",
                        "assay": "assay_chembl_id"}[args.resource]
            params[id_field] = args.chembl_id
        if args.query:
            params["q"] = args.query
        if args.inchikey:
            params["molecule_structures__standard_inchi_key"] = args.inchikey
        if args.target_accession:
            params["target_components__accession"] = args.target_accession
        for name in ("molecule_chembl_id", "target_chembl_id"):
            if getattr(args, name):
                params[name] = getattr(args, name)
        if args.species:
            params[{"target": "organism", "activity": "target_organism",
                    "assay": "assay_organism"}[args.resource]] = args.species
        endpoint = CHEMBL_URL + "/" + args.resource + ("/search.json" if args.query else ".json")
        data = self.sources.http.get_json(endpoint, params=params, headers=JSON_HEADERS)
        collection = {"molecule": "molecules", "target": "targets", "activity": "activities",
                      "mechanism": "mechanisms", "assay": "assays"}[args.resource]
        rows = _rows(data, collection)
        if args.resource in {"molecule", "target"}:
            _chembl_identity_schema(args.resource, rows)
        expected_ids = {name: getattr(args, name) for name in
                        ("molecule_chembl_id", "target_chembl_id") if getattr(args, name)}
        if args.chembl_id:
            expected_ids[id_field] = args.chembl_id
        if any(row.get(field) != identifier for row in rows
               for field, identifier in expected_ids.items()):
            raise DataSourceError("ChEMBL returned mismatched/unverifiable entity IDs")
        if args.species:
            field = {"target": "organism", "activity": "target_organism",
                     "assay": "assay_organism"}[args.resource]
            if any(row.get(field) != args.species for row in rows):
                raise DataSourceError("ChEMBL returned unverifiable/mismatched organism")
        page_meta = data.get("page_meta")
        if not isinstance(page_meta, dict):
            raise DataSourceError("ChEMBL response lacks pagination metadata")
        total = page_meta.get("total_count")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise DataSourceError("ChEMBL response lacks a valid total count")
        truncated = bool(page_meta.get("next")) or len(rows) > args.limit or (
            isinstance(total, int) and total > args.limit)
        if args.resource in {"molecule", "target"} and total != len(rows):
            truncated = True  # a partial identity list cannot establish uniqueness
        self._database(result, rows[:args.limit], endpoint + "?" + urlencode(params),
                       metadata={"pagination": page_meta}, truncated=truncated)
        result.pagination = {"page": args.page, "limit": args.limit, "total_count": total,
                             "has_more": bool(page_meta.get("next")), "mode": "provider_offset"}
        if args.resource in {"molecule", "target"}:
            self._chembl_identity(args, result, rows, truncated)
        result.warnings.append("Assay type, organism, value/relation and units are preserved; "
                               "assays are reported separately by endpoint, unit and experimental system")
        if args.query:
            result.warnings.append("Name-search matches are candidate chemical identities; resolution status is recorded separately")

    def _chembl_identity(self, args, result, rows, truncated):
        """Selection requires a complete, exact identity; search ranking never selects."""
        field = args.resource + "_chembl_id"
        candidates, matches = [], []
        for row in rows[:args.limit]:
            structures = row.get("molecule_structures") or {}
            components = row.get("target_components") or []
            candidates.append({"id": row.get(field), "name": row.get("pref_name"),
                               "species": row.get("organism"), "target_type": row.get("target_type"),
                               "inchikey": structures.get("standard_inchi_key"),
                               "parent_id": (row.get("molecule_hierarchy") or {}).get("parent_chembl_id"),
                               "accessions": [c.get("accession") for c in components]})
            if args.query:
                synonyms = [s.get("molecule_synonym") for s in (row.get("molecule_synonyms") or [])]
                names = [row.get("pref_name", ""), *synonyms]
                exact = any(isinstance(n, str) and n.casefold() == args.query.casefold() for n in names)
            else:
                exact = row.get(field) == args.chembl_id if args.chembl_id else True
            if args.resource == "molecule":
                key = structures.get("standard_inchi_key") or ""
                exact = exact and bool(re.fullmatch(r"[A-Z]{14}-[A-Z]{10}-[A-Z]", key))
                from rdkit import Chem
                molecule = Chem.MolFromSmiles(structures.get("canonical_smiles") or "")
                exact = exact and molecule is not None and bool(molecule.GetNumAtoms())
                if exact:
                    exact = Chem.MolToInchiKey(molecule) == key
                if args.inchikey:
                    exact = exact and key == args.inchikey
            else:
                exact = exact and row.get("target_type") == "SINGLE PROTEIN" and len(components) == 1
                # A label/component count alone cannot establish protein identity.
                # Unknown or non-protein components and missing accessions remain
                # unresolved even for an explicitly supplied ChEMBL target ID.
                component = components[0] if len(components) == 1 else {}
                exact = exact and component.get("component_type") == "PROTEIN"
                exact = exact and bool(re.fullmatch(UNIPROT_PATTERN, component.get("accession") or ""))
                if args.target_accession:
                    exact = exact and component.get("accession") == args.target_accession
                exact = exact and bool(args.species) and row.get("organism") == args.species
            if exact and re.fullmatch(CHEMBL_PATTERN, str(row.get(field, ""))):
                matches.append(row)
        status = ("truncated" if truncated or args.page != 1 else "empty" if not rows else
                  "ambiguous" if len(rows) > 1 else "resolved" if len(matches) == 1 else "unresolved")
        result.identity = {"status": status, "entity_type": args.resource,
                           "query": args.query or args.inchikey or args.target_accession or args.chembl_id,
                           "species": args.species, "candidates": candidates,
                           "selected_id": matches[0][field] if status == "resolved" else "",
                           "reason": "Complete exact unique identity" if status == "resolved" else
                           ("Target requires one PROTEIN component, a valid UniProt accession and matching species; "
                            "identity candidates or constructs need review" if args.resource == "target" else
                            "No unique complete exact identity; names, forms, species or constructs need review")}

    def _reactome_pathways(self, args, result):
        endpoint = REACTOME_URL + "/mapping/UniProt/" + args.uniprot_id + "/pathways"
        params = {"species": str(args.taxon_id)}
        try:
            payload = self.sources.http.get_json(endpoint, params=params, headers=JSON_HEADERS)
        except DataSourceError as exc:
            if not _is_documented_no_mapping(exc):
                raise
            self._database(result, [], endpoint + "?" + urlencode(params),
                           metadata={"provider_http_status": 404,
                                     "scope": "no_mapping_for_this_query_not_global_absence"})
            result.warnings.append("Reactome returned its documented no-mapping response; "
                                   "query coverage and provider mapping scope are retained with this empty result")
            return
        rows = _rows(payload)
        expected = REACTOME_SPECIES[args.taxon_id]
        if any(row.get("speciesName") != expected for row in rows):
            raise DataSourceError("Reactome returned unverifiable/mismatched species")
        start = (args.page - 1) * args.limit
        selected = rows[start:start + args.limit]
        self._database(result, selected, endpoint + "?" + urlencode(params),
                       metadata={"pagination": {"mode": "local_slice_of_mapping_response",
                                                "page": args.page, "limit": args.limit,
                                                "total_count": len(rows)}},
                       truncated=len(rows) > len(selected))
        result.pagination = {"mode": "local_slice_of_mapping_response", "page": args.page,
                             "limit": args.limit, "total_count": len(rows)}
        result.warnings.append("Pathway membership is reported with matched species, sources and "
                               "inferred flags")

    def _quickgo_annotations(self, args, result):
        params = {"geneProductId": "UniProtKB:" + args.uniprot_id,
                  "taxonId": args.taxon_id, "taxonUsage": "exact",
                  "limit": args.limit, "page": args.page}
        for field, key in (("go_id", "goId"), ("evidence_code", "evidenceCode"),
                           ("aspect", "aspect")):
            if getattr(args, field):
                params[key] = getattr(args, field)
        if args.go_id:
            params["goUsage"] = "exact"
        data = self.sources.http.get_json(QUICKGO_URL, params=params, headers=JSON_HEADERS)
        rows = _rows(data, "results")
        if any(row.get("taxonId") != args.taxon_id or
               row.get("geneProductId") != params["geneProductId"] for row in rows):
            raise DataSourceError("QuickGO returned unverifiable/mismatched gene product or taxon")
        for argument, field in (("go_id", "goId"), ("evidence_code", "evidenceCode"),
                                ("aspect", "goAspect")):
            expected = getattr(args, argument)
            if expected and any(row.get(field) != expected for row in rows):
                raise DataSourceError("QuickGO returned records outside the requested exact filter")
        total = data.get("numberOfHits")
        page_info = data.get("pageInfo")
        if not isinstance(total, int) or not isinstance(page_info, dict):
            raise DataSourceError("QuickGO response lacks pagination metadata")
        self._database(result, rows[:args.limit], QUICKGO_URL + "?" + urlencode(params),
                       metadata={"pagination": page_info, "numberOfHits": total},
                       truncated=total > len(rows[:args.limit]))
        result.pagination = {"page": args.page, "limit": args.limit, "total_count": total,
                             "mode": "provider_page"}
        result.warnings.append("GO qualifiers (including negation), evidence codes and references "
                               "are retained as the source and scope of each functional annotation")

