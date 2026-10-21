"""Bounded, explicit public views of lightweight tool plans and observations.

Persisted provider snapshots are research evidence, not an export schema. These
pure projections deliberately never copy an arbitrary mapping, provider payload,
prompt, request header or diagnostic into a public result. Missing legacy fields
remain missing, and no scientific conclusion is inferred from a database row.
"""
from __future__ import annotations

import hashlib
import ipaddress
import math
import re
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from .provider_receipts import public_provider_failure

_MISSING = object()
_MAX_RECORDS = 10
_MAX_TRACE = 100
_PROVIDERS = {
    "europepmc_search": "europe_pmc", "opentargets_search": "open_targets",
    "disease_targets": "open_targets", "uniprot": "uniprot", "pubchem": "pubchem",
    "chembl": "chembl", "reactome_pathways": "reactome", "quickgo_annotations": "quickgo",
}
_SENSITIVE = re.compile(
    r"(?i)(\b(?:authorization|api[-_ ]?key|access[-_ ]?token|password|"
    r"raw[-_ ]?prompt|system[-_ ]?prompt|secret|credentials?|stdout|stderr|traceback)\b|"
    r"\bbearer\s+\S+|\btoken\s*[=:]|\bsk-[A-Za-z0-9_-]{8,}|"
    r"\bgh[pousr]_[A-Za-z0-9_]{8,}|\bgithub_pat_[A-Za-z0-9_]{8,}|"
    r"\bAKIA[A-Z0-9]{16}\b|-----BEGIN .*PRIVATE KEY|"
    r"(?:file|vscode)://|/(?:Users|home|private|tmp|var|etc)/|(?:^|\s)[A-Za-z]:[\\/]|"
    r"<\|(?:system|user|assistant)|\[INST\])"
)
_SECRET_KEYS = re.compile(r"(?i)(token|key|secret|password|credential|authorization|prompt)")
_STATUSES = {"ok", "empty", "unavailable", "truncated", "invalid", "budget_exhausted"}
_IDENTITY_STATUSES = {"resolved", "ambiguous", "empty", "unresolved", "mismatch", "truncated", "unavailable"}
_PLAN_STATUSES = _STATUSES | {
    "planned", "pending", "running", "completed", "unresolved", "ambiguous", "skipped",
    "duplicate", "needs_selection", "stopped", "failed", "resolved",
}
_ORIGINS = {"supervisor_identity", "model_gap", "deterministic_validation"}
_HTTP_HOSTS = {
    "www.ebi.ac.uk", "reactome.org", "rest.uniprot.org", "pubchem.ncbi.nlm.nih.gov",
    "api.platform.opentargets.org", "europepmc.org",
}
INVALID_TOOL_PROPOSAL_WARNING = "Invalid structured tool proposal; parameters rejected; no HTTP query executed"


def _text(value: Any, maximum: int = 500) -> Any:
    if not isinstance(value, str) or len(value) > maximum or _SENSITIVE.search(value):
        return _MISSING
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        return _MISSING
    return value


def _choice(value: Any, choices: Any) -> bool:
    return isinstance(value, str) and value in choices


def _scalar(value: Any, maximum: int = 500) -> Any:
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        return value if abs(value) < 10**18 else _MISSING
    if type(value) is float:
        return value if math.isfinite(value) else _MISSING
    return _text(value, maximum)


def _strings(value: Any, maximum: int = 500, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    return [clean for item in value[:limit] if (clean := _text(item, maximum)) is not _MISSING]


def _fields(value: Any, names: set[str], maximum: int = 500) -> dict:
    if not isinstance(value, dict):
        return {}
    return {key: clean for key in sorted(names) if key in value
            and (clean := _scalar(value[key], maximum)) is not _MISSING}


def _put(result: dict, key: str, value: Any, maximum: int = 500) -> None:
    clean = _scalar(value, maximum)
    if clean is not _MISSING:
        result[key] = clean


def _url(value: Any, *, endpoint: bool = False) -> Any:
    clean = _text(value, 1200)
    if clean is _MISSING:
        return _MISSING
    try:
        parts = urlsplit(clean)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.fragment or parts.port not in (None, 443)):
            return _MISSING
        if endpoint and parts.hostname not in _HTTP_HOSTS:
            return _MISSING
        hostname = parts.hostname.lower()
        if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
            return _MISSING
        try:
            if not ipaddress.ip_address(hostname).is_global:
                return _MISSING
        except ValueError:
            pass
        if any(_SECRET_KEYS.search(key) for key, _ in parse_qsl(parts.query)):
            return _MISSING
        if _SENSITIVE.search(unquote(parts.path)) or any(
            _SENSITIVE.search(item) for _, item in parse_qsl(parts.query)
        ):
            return _MISSING
    except ValueError:
        return _MISSING
    return clean


# Only catalog argument names are public. Invalid inputs cannot smuggle extra
# keys into an error result before ToolRegistry has had a chance to validate.
_ARGUMENTS = {
    "europepmc_search": {"query", "limit"},
    "opentargets_search": {"query", "entity", "limit"},
    "disease_targets": {"disease_id", "limit"},
    "uniprot": {"gene_symbol", "species", "uniprot_id", "target_accession", "limit"},
    "pubchem": {"name", "expected_inchikey", "pubchem_cid", "limit"},
    "chembl": {"resource", "chembl_id", "query", "molecule_chembl_id", "target_chembl_id",
               "species", "limit", "page", "molecule_name", "target_symbol", "entity_type",
               "expected_inchikey", "inchikey", "uniprot_id", "target_accession"},
    "reactome_pathways": {"uniprot_id", "taxon_id", "limit", "page"},
    "quickgo_annotations": {"uniprot_id", "taxon_id", "go_id", "evidence_code", "aspect",
                            "limit", "page"},
}
_INTEGER_ARGS = {"limit", "page", "taxon_id", "pubchem_cid"}
_ARG_ENUMS = {
    "entity": {"disease", "target"}, "entity_type": {"molecule", "target"},
    "resource": {"molecule", "target", "activity", "mechanism", "assay"},
    "aspect": {"biological_process", "molecular_function", "cellular_component"},
}
_UNIPROT = r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})(?:-[1-9][0-9]*)?"
_ARG_PATTERNS = {
    "chembl_id": r"CHEMBL[1-9][0-9]*", "molecule_chembl_id": r"CHEMBL[1-9][0-9]*",
    "target_chembl_id": r"CHEMBL[1-9][0-9]*", "uniprot_id": _UNIPROT,
    "target_accession": _UNIPROT, "expected_inchikey": r"[A-Z]{14}-[A-Z]{10}-[A-Z]",
    "inchikey": r"[A-Z]{14}-[A-Z]{10}-[A-Z]", "go_id": r"GO:[0-9]{7}",
    "evidence_code": r"ECO:[0-9]{7}", "gene_symbol": r"[A-Za-z0-9][A-Za-z0-9_.-]*",
    "target_symbol": r"[A-Za-z0-9][A-Za-z0-9_.-]*",
    "disease_id": r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9]+",
    "species": r"[A-Z][A-Za-z]+(?: [A-Za-z][A-Za-z.-]*){1,3}",
}


def _arguments(name: str, value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in sorted(_ARGUMENTS.get(name, set())):
        if key not in value:
            continue
        item = value[key]
        if key in _INTEGER_ARGS:
            maximum = {"limit": 10, "page": 2}.get(key, 3_000_000_000)
            if type(item) is int and 0 < item <= maximum:
                result[key] = item
        elif isinstance(item, str):
            if key in _ARG_ENUMS and not _choice(item, _ARG_ENUMS[key]):
                continue
            if key in _ARG_PATTERNS and not re.fullmatch(_ARG_PATTERNS[key], item):
                continue
            _put(result, key, item, 500 if key == "query" else 200)
    return result


_CHEMBL_FIELDS = {
    "activity_id", "assay_chembl_id", "assay_description", "assay_type", "assay_organism",
    "assay_tax_id", "assay_category", "assay_test_type", "assay_cell_type", "assay_tissue",
    "assay_subcellular_fraction", "assay_strain", "bao_endpoint", "bao_format", "bao_label",
    "molecule_chembl_id", "target_chembl_id", "document_chembl_id", "record_id", "src_id",
    "target_organism", "target_tax_id", "target_type", "target_pref_name", "organism",
    "tax_id", "pref_name", "molecule_type", "structure_type", "molecule_species",
    "standard_type", "standard_relation", "standard_units", "standard_value", "type",
    "relation", "units", "value", "pchembl_value", "activity_comment", "data_validity_comment",
    "potential_duplicate", "standard_flag", "confidence_score", "confidence_description",
    "relationship_type", "action_type", "mechanism_of_action", "direct_interaction",
    "molecular_mechanism", "disease_efficacy", "binding_site_id", "site_id", "variant_id",
    "variant_accession", "variant_sequence", "description", "max_phase", "chirality",
}
_STRUCTURES = {"canonical_smiles", "standard_inchi", "standard_inchi_key"}
_HIERARCHY = {"molecule_chembl_id", "parent_chembl_id", "active_chembl_id"}
_COMPONENT = {"accession", "component_id", "component_type", "component_description",
              "relationship", "organism", "tax_id"}


def _chembl_record(value: Any) -> dict:
    result = _fields(value, _CHEMBL_FIELDS, 1000)
    if not isinstance(value, dict):
        return result
    for field, names in (("molecule_structures", _STRUCTURES), ("molecule_hierarchy", _HIERARCHY)):
        if isinstance(value.get(field), dict):
            result[field] = _fields(value[field], names, 1000)
    for field, names in (
        ("molecule_synonyms", {"molecule_synonym", "synonyms", "syn_type", "synonym_type"}),
        ("mechanism_refs", {"ref_id", "ref_type", "ref_url"}),
    ):
        if isinstance(value.get(field), list):
            result[field] = [_fields(item, names, 300) for item in value[field][:_MAX_RECORDS]
                             if isinstance(item, dict)]
    if isinstance(value.get("target_components"), list):
        components = []
        for item in value["target_components"][:_MAX_RECORDS]:
            if not isinstance(item, dict):
                continue
            component = _fields(item, _COMPONENT)
            for field, names in (
                ("target_component_synonyms", {"component_synonym", "syn_type"}),
                ("target_component_xrefs", {"xref_id", "xref_name", "xref_src_db"}),
            ):
                if isinstance(item.get(field), list):
                    component[field] = [_fields(row, names, 200)
                                        for row in item[field][:_MAX_RECORDS]
                                        if isinstance(row, dict)]
            components.append(component)
        result["target_components"] = components
    return result


def _uniprot_record(value: Any) -> dict:
    result = _fields(value, {"uniprot_id", "primaryAccession", "uniProtkbId", "entryType",
                             "gene_symbol", "species", "taxon_id"})
    if not isinstance(value, dict):
        return result
    if isinstance(value.get("organism"), dict):
        result["organism"] = _fields(value["organism"], {"scientificName", "commonName", "taxonId"})
    if isinstance(value.get("genes"), list):
        genes = []
        for item in value["genes"][:_MAX_RECORDS]:
            if not isinstance(item, dict):
                continue
            gene = {}
            if isinstance(item.get("geneName"), dict):
                gene["geneName"] = _fields(item["geneName"], {"value"}, 100)
            for field in ("synonyms", "orderedLocusNames", "orfNames"):
                if isinstance(item.get(field), list):
                    gene[field] = [_fields(row, {"value"}, 100) for row in item[field][:10]
                                   if isinstance(row, dict)]
            genes.append(gene)
        result["genes"] = genes
    return result


def _quickgo_record(value: Any) -> dict:
    result = _fields(value, {"id", "geneProductId", "symbol", "name", "taxonId", "goId", "goName",
                             "goEvidence", "evidenceCode", "qualifier", "goAspect", "aspect",
                             "reference", "assignedBy", "date", "geneProductType", "extensions"})
    if not isinstance(value, dict):
        return result
    # Some API versions use lists for qualifiers/references. Preserve their
    # negation and evidence context without exposing opaque nested structures.
    for field in ("qualifier", "reference"):
        if isinstance(value.get(field), list):
            result[field] = _strings(value[field], 200)
    if isinstance(value.get("withFrom"), list):
        result["withFrom"] = [
            {"connectedXrefs": [_fields(xref, {"db", "id"}, 200)
                                for xref in row.get("connectedXrefs", [])[:20]
                                if isinstance(xref, dict)]}
            for row in value["withFrom"][:20]
            if isinstance(row, dict) and isinstance(row.get("connectedXrefs"), list)
        ]
    return result


def _opentargets_record(value: Any) -> dict:
    result = _fields(value, {"id", "entity", "name", "description", "disease_id", "disease_name",
                             "score", "approvedSymbol", "approvedName"}, 600)
    if not isinstance(value, dict):
        return result
    target = value.get("target")
    if isinstance(target, dict):
        summary = _fields(target, {"id", "approvedSymbol", "approvedName"})
        if isinstance(target.get("proteinIds"), list):
            summary["proteinIds"] = [_fields(row, {"id", "source"}, 100)
                                     for row in target["proteinIds"][:10] if isinstance(row, dict)]
        result["target"] = summary
    if isinstance(value.get("datatypeScores"), list):
        result["datatypeScores"] = [_fields(row, {"id", "score"}, 100)
                                    for row in value["datatypeScores"][:20]
                                    if isinstance(row, dict)]
    return result


def _record(provider: str, value: Any) -> dict:
    if provider == "chembl":
        return _chembl_record(value)
    if provider == "uniprot":
        return _uniprot_record(value)
    if provider == "quickgo":
        return _quickgo_record(value)
    if provider == "open_targets":
        return _opentargets_record(value)
    if provider == "pubchem":
        return _fields(value, {"name_query", "name", "candidate_id", "cid", "pubchem_cid",
                               "smiles", "inchikey", "canonical_smiles", "isomeric_smiles",
                               "InChIKey", "CID", "SMILES", "IsomericSMILES", "CanonicalSMILES",
                               "rawSMILES", "canonicalSMILES", "raw_smiles"}, 1000)
    if provider == "reactome":
        return _fields(value, {"dbId", "stId", "displayName", "speciesName", "isInferred",
                               "schemaClass", "uniprot_id", "taxon_id"})
    if provider == "europe_pmc":
        return public_tool_source(value)
    return {}


def _pagination(value: Any) -> dict:
    result = _fields(value, {"mode", "page", "limit", "offset", "total_count", "total",
                             "current", "resultsPerPage", "numberOfHits", "has_next",
                             "max_pages", "pages_fetched", "returned_count", "truncated",
                             "has_more"}, 100)
    if isinstance(value, dict) and "next" in value:
        result["has_next"] = bool(value["next"])
        if value["next"] is None:
            result["next"] = None
        elif (clean := _url(value["next"], endpoint=True)) is not _MISSING:
            result["next"] = clean
    return result


def _identity(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    result = _fields(value, {"entity_type", "molecule_chembl_id", "target_chembl_id", "chembl_id",
                             "uniprot_id", "target_accession", "pubchem_cid", "query", "species",
                             "selected_id", "reason", "expected_inchikey", "inchikey", "taxon_id"})
    if _choice(value.get("status"), _IDENTITY_STATUSES):
        result["status"] = value["status"]
    if isinstance(value.get("candidates"), list):
        names = {"id", "entity_type", "selected_id", "molecule_chembl_id", "target_chembl_id",
                 "chembl_id", "uniprot_id", "accession", "target_accession", "pubchem_cid", "cid",
                 "name", "pref_name", "gene_symbol", "molecule_type", "structure_type", "species",
                 "organism", "taxon_id", "tax_id", "inchikey", "reason", "target_type", "parent_id"}
        candidates = []
        for item in value["candidates"][:_MAX_RECORDS]:
            if not isinstance(item, dict):
                continue
            candidate = _fields(item, names)
            if isinstance(item.get("accessions"), list):
                candidate["accessions"] = _strings(item["accessions"], 100, 10)
            for field, fields in (("molecule_structures", _STRUCTURES),
                                  ("molecule_hierarchy", _HIERARCHY)):
                if isinstance(item.get(field), dict):
                    candidate[field] = _fields(item[field], fields, 1000)
            if isinstance(item.get("target_components"), list):
                candidate["target_components"] = [_fields(row, _COMPONENT)
                                                   for row in item["target_components"][:10]
                                                   if isinstance(row, dict)]
            candidates.append(candidate)
        result["candidates"] = candidates
    return result


_HTTP_PARAMETERS = {
    "query", "q", "id", "size", "limit", "offset", "page", "format", "resultType", "pageSize",
    "sort_date", "molecule_chembl_id", "target_chembl_id", "assay_chembl_id",
    "pref_name__icontains", "organism", "target_organism", "assay_organism", "species",
    "geneProductId", "taxonId", "taxonUsage", "goId", "evidenceCode", "aspect", "goUsage",
    "molecule_synonyms__molecule_synonym__iexact", "pref_name__iexact", "target_components__accession",
    "molecule_structures__standard_inchi_key",
    "gene_symbol", "name", "uniprot_id", "taxon_id", "resource", "chembl_id", "disease_id",
}


def _http_parameters(value: Any) -> dict:
    result = _fields(value, _HTTP_PARAMETERS, 500)
    if not isinstance(value, dict):
        return result
    if isinstance(value.get("entities"), list):
        result["entities"] = [item for item in value["entities"][:2]
                              if _choice(item, {"target", "disease"})]
    # Fixed GraphQL source text is not useful in a public trace; only its typed
    # variables are shown. A model/provider's arbitrary query object is omitted.
    if "query" in result and re.match(
        r"\s*(?:query|mutation)\b.*[({]", str(result["query"]), re.DOTALL
    ):
        result.pop("query")
    if isinstance(value.get("variables"), dict):
        variables = value["variables"]
        result["variables"] = _fields(variables, {"q", "id", "size"}, 500)
        if isinstance(variables.get("entities"), list):
            result["variables"]["entities"] = [item for item in variables["entities"][:2]
                                               if _choice(item, {"target", "disease"})]
    return result


def _http_trace(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    trace = []
    for row in value[:_MAX_TRACE]:
        if not isinstance(row, dict):
            continue
        result = _fields(row, {"cache_key", "attempt", "retry", "retry_index", "cache_hit", "outcome",
                               "http_status", "page", "elapsed_seconds", "stop_reason"}, 200)
        if (endpoint := _url(row.get("endpoint"), endpoint=True)) is not _MISSING:
            result["endpoint"] = endpoint
        if _choice(row.get("method"), {"GET", "POST"}):
            result["method"] = row["method"]
        if isinstance(row.get("parameters"), dict):
            result["parameters"] = _http_parameters(row["parameters"])
        trace.append(result)
    return trace


_SEARCH_SCOPE_FIELDS = {
    "total_hits", "query", "page_size", "pagination", "sort", "facet", "retrieved_count",
    "hit_range_start", "hit_range_end", "has_more", "coverage", "pages_fetched", "max_pages",
    "query_origin",
}
_BUDGET_NUMBERS = {
    "llm_calls", "http_attempts", "tool_calls", "input_tokens_reserved", "output_tokens_reserved",
    "input_bytes_reserved", "output_bytes_reserved", "cache_hits", "actual_llm_calls", "llm_cache_hits",
    "literature_queries", "remaining_seconds", "elapsed_seconds", "output_bytes_committed", "output_bytes_received", "output_bytes_inflight",
}
_FAILURE_NUMBERS = {
    "required_bytes", "remaining_bytes", "remaining_seconds", "required_output_bytes",
    "remaining_output_bytes", "required_calls", "remaining_calls", "reserve_calls",
    "reserve_input_bytes", "reserve_output_bytes", "reserve_seconds",
    "required_seconds", "reserved_bytes", "reserved_calls", "reserved_output_bytes", "reserved_seconds",
    "inflight_output_bytes",
}


def public_search_scope(value: Any) -> dict:
    return _fields(value, _SEARCH_SCOPE_FIELDS, 500)


def public_annotation_receipt(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    result = _fields(value, {"schema_version", "provider", "operation", "query_key", "response_sha256",
        "hash_scope", "observed_at", "source_retrieved_at", "cache_origin", "interpretation", "index_status"}, 500)
    result["parameters"] = _fields(value.get("parameters"), {"query", "format", "size", "pageSize",
        "cursorMark", "resultType", "sort_date", "gene_symbol", "species", "q", "entities", "id"}, 4000)
    entities = value.get("parameters", {}).get("entities") if isinstance(value.get("parameters"), dict) else None
    if isinstance(entities, list):
        result["parameters"]["entities"] = [entity for entity in entities[:2]
            if isinstance(entity, str) and entity in {"disease", "target"}]
    result["request_cache_keys"] = [key for key in value.get("request_cache_keys", [])[:20]
        if isinstance(key, str) and re.fullmatch(r"[a-f0-9]{64}", key)] if isinstance(value.get("request_cache_keys"), list) else []
    return result


def public_annotation_metadata(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    if isinstance(value.get("query_receipt"), dict):
        result["query_receipt"] = public_annotation_receipt(value["query_receipt"])
    scope = value.get("identity_scope")
    if isinstance(scope, dict):
        result["identity_scope"] = _fields(scope, {"disease_id", "disease_name", "query_name", "task_disease",
            "species", "run_id", "status", "association_only"}, 1000)
        result["identity_scope"]["identity_query_receipts"] = [public_annotation_receipt(row)
            for row in scope.get("identity_query_receipts", [])[:4] if isinstance(row, dict)] if isinstance(scope.get("identity_query_receipts"), list) else []
    return result


def public_publication_metadata(value: Any) -> dict:
    """Only actual provider metadata is projected; missing authors stay missing."""
    result = _fields(value, {"journal", "selection_reason", "retrieval_facet", "full_text_selected"}, 1000)
    if not isinstance(value, dict):
        return result
    result.update(public_annotation_metadata(value))
    for key in ("authors", "coverage_gaps"):
        if isinstance(value.get(key), list):
            result[key] = _strings(value[key], 1000, 50)
        elif key == "authors" and isinstance(value.get(key), str):
            _put(result, key, value[key], 1000)
    if isinstance(value.get("search_scope"), dict):
        result["search_scope"] = public_search_scope(value["search_scope"])
    types = value.get("publication_types")
    if isinstance(types, dict) and isinstance(types.get("pubType"), list):
        result["publication_types"] = {"pubType": _strings(types["pubType"], 300, 20)}
    return result


def public_budget_failure(value: Any) -> dict:
    """Safe failure chain: sizes/time/phase, never the failed request or diagnosis."""
    if not isinstance(value, dict):
        return {}
    result = _fields(value, {"reason", "stage", "receipt_id", "action_id", "operation_id", "status", "skip_reason", "accounting_unit"}, 300)
    for key in _FAILURE_NUMBERS:
        item = value.get(key)
        if type(item) in (int, float) and 0 <= item < 10**18 and math.isfinite(item):
            result[key] = item
    for key in ("call_allowance", "effective_available_output_bytes"):
        item = value.get(key)
        if type(item) is int and 0 <= item < 10**18:
            result[key] = item
        elif key == "call_allowance" and key in value and item is None:
            result[key] = None
    categories = value.get("output_limit_categories")
    if isinstance(categories, list):
        result["output_limit_categories"] = list(dict.fromkeys(item for item in categories
            if isinstance(item, str) and item in {"single_call", "global", "future_reserved", "parallel"}))
    for key in ("blocked_before_provider", "prior_timeout"):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    provider_failure = public_provider_failure(value.get("provider_failure"))
    if provider_failure:
        result["provider_failure"] = provider_failure
    return result


def public_failure_records(value: Any) -> list[dict]:
    """Collect public failure/skip views without guessing missing action IDs.

    Identified actions merge only identical safe payloads. For old records,
    same-kind top-level and budget containers are saved mirrors: preserve the
    maximum occurrence count, including repeated identical actions in a list.
    Unidentified budget failures and skips remain separate record types.
    """
    import json

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, dict):
        return []
    budget = value.get("budget") if isinstance(value.get("budget"), dict) else {}
    records, identified, mirrored = [], {}, {}

    def key(row):
        return json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False)

    def append(row, kind):
        identity = row.get("receipt_id") or row.get("action_id") or row.get("operation_id")
        marker = (identity, key(row)) if identity else None
        if marker and marker in identified:
            record = identified[marker]
            if kind not in record["record_types"]:
                record["record_types"].append(kind)
            return
        record = {**row, "record_types": [kind]}
        records.append(record)
        if marker:
            identified[marker] = record

    containers = [("budget_failure", value.get("budget_failures")),
                  ("budget_failure", budget.get("budget_failures")),
                  ("budget_failure", [budget["budget_failure"]] if isinstance(budget.get("budget_failure"), dict) else []),
                  ("optional_skip", value.get("optional_skips")),
                  ("optional_skip", budget.get("optional_skips"))]
    for kind, rows in containers:
        occurrences = {}
        previous = mirrored.setdefault(kind, {})
        for raw in rows[:100] if isinstance(rows, list) else []:
            row = public_budget_failure(raw)
            if not row:
                continue
            payload = key(row)
            occurrences[payload] = occurrences.get(payload, 0) + 1
            identified_row = row.get("receipt_id") or row.get("action_id") or row.get("operation_id")
            if not identified_row and occurrences[payload] <= previous.get(payload, 0):
                continue
            append(row, kind)
        for payload, count in occurrences.items():
            previous[payload] = max(previous.get(payload, 0), count)
    for event in value.get("events", [])[:300] if isinstance(value.get("events"), list) else []:
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("budget_failure"), dict):
            row = public_budget_failure(event["budget_failure"])
            if row:
                append(row, "budget_event")
        receipt = public_provider_failure(event.get("provider_failure"))
        if not receipt:
            continue
        public_event = public_tool_event(event)
        operation = public_event.get("operation_id", "")
        stage = public_event.get("title", "")
        matches = [record for record in records if operation and record.get("operation_id") == operation
                   and record.get("provider_failure") == receipt and record.get("stage", "") == stage]
        if len(matches) == 1:
            if "provider_event" not in matches[0]["record_types"]:
                matches[0]["record_types"].append("provider_event")
            continue
        row = {"provider_failure": receipt}
        if operation:
            row["operation_id"] = operation
        if stage:
            row["stage"] = stage
        append(row, "provider_event")
    return records


def public_budget(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in _BUDGET_NUMBERS:
        item = value.get(key)
        if type(item) in (int, float) and 0 <= item < 10**18 and math.isfinite(item):
            result[key] = item
    result.update(_fields(value, {"token_accounting", "provider_billed_tokens", "provider_cost",
                                  "provider_output_cap_enforced"}, 200))
    limits = value.get("limits")
    if isinstance(limits, dict):
        from .models import ResearchBudget
        result["limits"] = {key: limits[key] for key in ResearchBudget.model_fields
                            if type(limits.get(key)) is int and 0 <= limits[key] < 10**18}
    for key in ("budget_failure",):
        if isinstance(value.get(key), dict):
            result[key] = public_budget_failure(value[key])
    for key in ("budget_failures", "optional_skips"):
        if isinstance(value.get(key), list):
            result[key] = [public_budget_failure(row) for row in value[key][:100] if isinstance(row, dict)]
    return result


def _public_user_name_declarations(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [_fields(row, {"label", "full_name", "abbreviation", "source", "source_index", "run_id"}, 500)
            for row in value[:10] if isinstance(row, dict) and row.get("source") == "brief.existing_candidates"
            and type(row.get("source_index")) is int and 0 <= row["source_index"] <= 9]


def public_entity_reference(value: Any) -> dict:
    result = _fields(value, {"entity_id", "candidate_id", "name", "identity_status", "identity_revision",
                             "scientific_assessment", "identity_assessment_pending", "identity_priority_before_update"}, 500)
    if not isinstance(value, dict):
        return result
    for field in ("aliases", "source_ids"):
        result[field] = _strings(value.get(field), 500, 50)
    result["user_name_declarations"] = _public_user_name_declarations(value.get("user_name_declarations"))
    result["hypothesis_versions"] = []
    for row in value.get("hypothesis_versions", [])[:50] if isinstance(value.get("hypothesis_versions"), list) else []:
        if isinstance(row, dict):
            ref = _fields(row, {"hypothesis_id", "version"}, 150)
            ref["historical_identity_gaps"] = _strings(row.get("historical_identity_gaps"), 1000, 50)
            result["hypothesis_versions"].append(ref)
    return result


def public_candidate_identity(value: Any) -> dict:
    result = _fields(value, {"entity_id", "identity_revision", "identity_assessment_pending", "identity_priority_before_update"}, 200)
    if not isinstance(value, dict):
        return result
    for field in ("aliases", "identity_source_ids"):
        result[field] = _strings(value.get(field), 500, 50)
    result["user_name_declarations"] = _public_user_name_declarations(value.get("user_name_declarations"))
    form = value.get("identity_form")
    result["identity_form"] = _fields(form, {"isomeric_smiles"}, 1000)
    if isinstance(form, dict) and isinstance(form.get("components"), list):
        result["identity_form"]["components"] = _strings(form["components"], 1000, 20)
    result["identity_history"] = []
    for row in value.get("identity_history", [])[:100] if isinstance(value.get("identity_history"), list) else []:
        if not isinstance(row, dict):
            continue
        ref = _fields(row, {"plan_id", "call_id", "query", "status", "previous_identity_status",
                            "previous_candidate_id", "application_status", "origin", "acquisition", "source_sha256",
                            "source_retrieved_at", "package_id", "package_version", "package_sha256",
                            "core_preparation", "run_id"}, 500)
        for field in ("source_ids", "historical_identity_gaps"):
            if isinstance(row.get(field), list):
                ref[field] = _strings(row[field], 1000, 50)
        if isinstance(row.get("previous_identity"), dict):
            ref["previous_identity"] = _fields(row["previous_identity"], {"candidate_id", "smiles", "inchikey", "pubchem_cid"}, 1000)
        result["identity_history"].append(ref)
    return result


def public_reuse_seed(value: Any) -> dict:
    return _fields(value, {"parent_run_id", "original_record_sha256", "mode"}, 500)


def public_tool_source(value: Any) -> dict:
    """Expose a SourceRecord citation and bounded scientific metadata, never text."""
    from .source_scope import source_scope_view
    if not isinstance(value, dict):
        return {}
    result = _fields(value, {"source_id", "provider", "title", "retrieved_at", "pmid", "doi",
                             "pmcid"}, 600)
    if (url := _url(value.get("url"))) is not _MISSING:
        result["url"] = url
    if _choice(value.get("evidence_scope"), {"abstract", "full_text", "database", "local_document"}):
        result["evidence_scope"] = value["evidence_scope"]
    result["text"] = ""
    result["metadata"] = {}
    metadata = value.get("metadata")
    provider = value.get("provider")
    if isinstance(metadata, dict) and provider in _PROVIDERS.values():
        public = _fields(metadata, {"scope", "provider_batch_limit", "provider_http_status",
                                    "numberOfHits", "year", "is_open_access", "acquisition_origin", "background_package_id",
                                    "background_version", "background_manifest_sha256", "original_text_sha256",
                                    "original_evidence_scope", "excerpt_start", "excerpt_end", "excerpt_location",
                                    "excerpt_sha256", "excerpt_selection", "inclusion_reason", "evidence_level",
                                    "local_excerpt_only"}, 500)
        public.update(public_annotation_metadata(metadata))
        if provider == "europe_pmc":
            public.update(public_publication_metadata(metadata))
        # Database snapshots from legacy tools have no tool_name. Provider-specific
        # scalar argument names are still known and do not imply execution.
        names = [name for name, candidate in _PROVIDERS.items() if candidate == provider]
        if isinstance(metadata.get("parameters"), dict):
            public["parameters"] = {}
            for name in names:
                public["parameters"].update(_arguments(name, metadata["parameters"]))
        if isinstance(metadata.get("records"), list):
            public["records"] = [_record(provider, row) for row in metadata["records"][:10]
                                 if isinstance(row, dict)]
        if isinstance(metadata.get("pagination"), dict):
            public["pagination"] = _pagination(metadata["pagination"])
        # The original UniProt/PubChem source schema used the identity itself as
        # metadata; project it through the same scientific row allowlist.
        if _choice(provider, {"uniprot", "pubchem"}):
            public.update(_record(provider, metadata))
        result["metadata"] = public
    if isinstance(value.get("text"), str):
        previous = metadata.get("retrieved_text_sha256") if isinstance(metadata, dict) else None
        if (value["text"] == "" and isinstance(metadata, dict)
                and metadata.get("export_text_included") is False
                and isinstance(previous, str) and re.fullmatch(r"[a-f0-9]{64}", previous)):
            result["metadata"]["retrieved_text_sha256"] = previous
        else:
            result["metadata"]["retrieved_text_sha256"] = hashlib.sha256(
                value["text"].encode("utf-8")
            ).hexdigest()
        result["metadata"]["export_text_included"] = False
    if "evidence_scope" in result:
        result["metadata"]["acquisition_origin"] = source_scope_view(value)["acquisition_origin"]
        scope = source_scope_view(result)
        result["metadata"]["source_scope"] = scope
        result["metadata"]["acquisition_origin"] = scope["acquisition_origin"]
    return result


def public_tool_result(value: Any) -> dict:
    """Return an export-safe tool observation without altering its stored record."""
    if not isinstance(value, dict):
        return {}
    name = value.get("tool_name")
    known = _choice(name, _PROVIDERS)
    provider = _PROVIDERS[name] if known else "none"
    result = _fields(value, {"tool_name", "retrieved_at", "call_id", "parent_call_id", "plan_id",
                             "gap_id", "request_count", "cache_hits", "count", "record_count",
                             "stop_reason"}, 300)
    result["provider"] = provider
    result["tool_name"] = name if known else "unregistered_tool"
    if _choice(value.get("status"), _STATUSES):
        result["status"] = value["status"]
    result["parameters"] = _arguments(name, value.get("parameters")) if known else {}
    if "requested_parameters" in value:
        result["requested_parameters"] = _arguments(name, value["requested_parameters"]) if known else {}
    result["records"] = [_record(provider, row) for row in value.get("records", [])[:10]
                         if isinstance(row, dict)] if isinstance(value.get("records"), list) else []
    result["sources"] = [public_tool_source(row) for row in value.get("sources", [])[:10]
                         if isinstance(row, dict)] if isinstance(value.get("sources"), list) else []
    result["warnings"] = _strings(value.get("warnings"), 500, 20)
    if isinstance(value.get("identity"), dict):
        result["identity"] = _identity(value["identity"])
    if isinstance(value.get("http_trace"), list):
        result["http_trace"] = _http_trace(value["http_trace"])
    if isinstance(value.get("pagination"), dict):
        result["pagination"] = _pagination(value["pagination"])
    return result


def public_tool_plan(value: Any) -> dict:
    """Return only executable catalog intent, identity decisions and stable IDs."""
    if not isinstance(value, dict):
        return {}
    result = _fields(value, {"plan_id", "gap_id", "reason", "stop_reason", "duplicate_of"}, 500)
    if _choice(value.get("origin"), _ORIGINS):
        result["origin"] = value["origin"]
    if _choice(value.get("status"), _PLAN_STATUSES):
        result["status"] = value["status"]
    request = value.get("request")
    if isinstance(request, dict) and _choice(request.get("name"), _PROVIDERS):
        result["request"] = {"name": request["name"],
                             "arguments": _arguments(request["name"], request.get("arguments"))}
    for field in ("hypothesis_ids", "call_ids"):
        if isinstance(value.get(field), list):
            result[field] = _strings(value[field], 150, 50)
    if isinstance(value.get("identity"), dict):
        result["identity"] = _identity(value["identity"])
    for field, names in (("candidate_link", {"status", "query", "reason", "candidate_id", "entity_id", "verification"}),
                         ("identity_application", {"status", "entity_id", "candidate_id", "identity_revision", "scientific_assessment", "reason"})):
        if isinstance(value.get(field), dict):
            result[field] = _fields(value[field], names, 500)
    return result


def public_tool_event(value: Any) -> dict:
    """Legacy tool event strings are untrusted, even when the event shape is typed."""
    result = _fields(value, {"event_id", "sequence", "agent_id", "kind", "status", "title",
                             "summary", "created_at", "input_summary", "operation_id",
                             "operation_status"}, 1200)
    for key in ("title", "summary", "input_summary"):
        result.setdefault(key, "")
    if isinstance(value, dict):
        for key in ("artifact_ids", "input_artifact_ids"):
            result[key] = _strings(value.get(key), 200, 100)
        if isinstance(value.get("budget_failure"), dict):
            result["budget_failure"] = public_budget_failure(value["budget_failure"])
        provider_failure = public_provider_failure(value.get("provider_failure"))
        if provider_failure:
            result["provider_failure"] = provider_failure
    return result


def public_tool_task(value: Any) -> str:
    clean = _text(value, 1200)
    return "" if clean is _MISSING else clean


def public_tool_warning(value: str) -> str:
    """Do not echo unvalidated names from historical invalid-proposal warnings."""
    if value.lstrip().casefold().startswith("invalid structured tool proposal"):
        return INVALID_TOOL_PROPOSAL_WARNING
    return value

