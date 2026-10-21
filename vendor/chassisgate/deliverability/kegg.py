"""Small cached KEGG REST adapter for the mechanistic evidence paths.

KEGG is used as a bridge:

    compound -> producing reactions -> KO/EC requirements -> direct precursors

The adapter is deliberately conservative. Network/cache failures produce an
``unavailable`` context; they never become biological negatives.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Callable
from urllib.parse import quote

import requests

from .schema import Candidate


KEGG_ORGANISM_CODES = {
    "ecoli": "eco",
    "bsubtilis": "bsu",
    "scerevisiae": "sce",
    "lplantarum": "lpl",
    "scoelicolor": "sco",
    "pputida": "ppu",
    "llactis": "llm",
    "cglutamicum": "cgl",
    "efaecalis_v583": "efa",
}

_ID_RE = re.compile(r"\b(?:cpd:|rn:|ko:)?([CRK]\d{5})\b")
_KO_RE = re.compile(r"\bK\d{5}\b")
_EC_RE = re.compile(r"\b\d+\.\d+\.\d+\.(?:\d+|-)\b")
_ARROWS = ("<=>", "=>", "<=", "=")
_CURRENCY_COMPOUNDS = {
    "C00001",  # water
    "C00002", "C00008",  # ATP / ADP
    "C00003", "C00004",  # NAD / NADH
    "C00005", "C00006",  # NADPH / NADP
    "C00009", "C00013",  # phosphate / diphosphate
    "C00010",  # CoA
    "C00011",  # CO2
    "C00014",  # ammonia
    "C00080",  # proton
}
_CURATED_CANDIDATE_MAPPINGS = {
    # KEGG stores this target under the generic lactate name, so the exact-name
    # search misses the benchmark candidate spelling.
    "indole-3-lactic acid": "C02043",
    "XGILAAMKEQUXLS-UHFFFAOYSA-N": "C02043",
}


@dataclass
class ParsedEquation:
    produces_target: bool
    direction: str
    precursors: list[str] = field(default_factory=list)


@dataclass
class KeggReactionContext:
    reaction_id: str
    equation: str
    direction: str
    produces_target: bool
    precursor_compound_ids: list[str] = field(default_factory=list)
    ko_ids: list[str] = field(default_factory=list)
    ec_numbers: list[str] = field(default_factory=list)


@dataclass
class KeggCompoundContext:
    compound_id: str | None
    status: str
    mapping_method: str | None = None
    producing_reactions: list[KeggReactionContext] = field(default_factory=list)
    precursor_compound_ids: list[str] = field(default_factory=list)
    candidate_kos: list[str] = field(default_factory=list)
    present_kos: list[str] = field(default_factory=list)
    missing_kos: list[str] = field(default_factory=list)
    supported_reaction_ids: list[str] = field(default_factory=list)
    reaction_coverage: float | None = None
    error: str | None = None


def parse_flat_file(text: str) -> dict[str, list[str]]:
    """Parse KEGG's fixed-width flat-file response without losing continuations."""
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        if raw == "///":
            break
        key = raw[:12].strip()
        value = raw[12:].strip()
        if key:
            current = key
            fields.setdefault(key, []).append(value)
        elif current and value:
            fields[current].append(value)
    return fields


def parse_reaction_equation(equation: str, *, target: str) -> ParsedEquation:
    arrow = next((item for item in _ARROWS if item in equation), None)
    if arrow is None:
        return ParsedEquation(False, "unknown", [])
    left, right = (part.strip() for part in equation.split(arrow, 1))
    left_ids = _compound_ids(left)
    right_ids = _compound_ids(right)
    if arrow in ("<=>", "="):
        if target in right_ids:
            return ParsedEquation(True, "reversible", _without_target(left_ids, target))
        if target in left_ids:
            return ParsedEquation(True, "reversible", _without_target(right_ids, target))
    elif arrow == "=>" and target in right_ids:
        return ParsedEquation(True, "forward", _without_target(left_ids, target))
    elif arrow == "<=" and target in left_ids:
        return ParsedEquation(True, "reverse", _without_target(right_ids, target))
    return ParsedEquation(False, "not_producing", [])


def _compound_ids(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"\bC\d{5}\b", text)))


def _without_target(values: list[str], target: str) -> list[str]:
    return [value for value in values if value != target]


class KeggClient:
    """Cached client for the public KEGG REST API.

    ``transport`` is injectable so unit tests and frozen snapshots do not need
    network access. Cache entries store raw KEGG responses for auditability.
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        base_url: str = "https://rest.kegg.jp",
        offline: bool = False,
        timeout: float = 20.0,
        transport: Callable[[str], str] | None = None,
        id_resolver: Callable[[str], str | None] | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.base_url = base_url.rstrip("/")
        self.offline = offline
        self.timeout = timeout
        self.transport = transport or self._http_get
        self.id_resolver = id_resolver

    def resolve_candidate(self, candidate: Candidate, model=None) -> tuple[str | None, str | None]:
        """Resolve a target, preferring frozen GEM cross-references over name search."""
        mapping_key = candidate.inchikey or _normalize_name(candidate.name)
        cached_mapping = self._candidate_mapping(mapping_key)
        if cached_mapping:
            return cached_mapping["compound_id"], cached_mapping["method"]
        if model is not None:
            from .gem import candidate_metabolite

            metabolite = candidate_metabolite(model, candidate)
            if metabolite is not None:
                annotations = metabolite.annotation or {}
                raw = annotations.get("kegg.compound") or annotations.get("kegg")
                values = [raw] if isinstance(raw, str) else list(raw or [])
                for value in values:
                    match = re.search(r"\bC\d{5}\b", str(value))
                    if match:
                        return self._remember_mapping(
                            mapping_key, match.group(0), "gem_annotation"
                        )
        if candidate.inchikey and self.id_resolver is not None:
            try:
                resolved = self.id_resolver(candidate.inchikey)
            except Exception:
                resolved = None
            if resolved and re.fullmatch(r"C\d{5}", resolved):
                return self._remember_mapping(
                    mapping_key, resolved, "inchikey_cross_reference"
                )
        curated = _CURATED_CANDIDATE_MAPPINGS.get(mapping_key)
        if curated is None and candidate.name:
            curated = _CURATED_CANDIDATE_MAPPINGS.get(_normalize_name(candidate.name))
        if curated:
            return self._remember_mapping(mapping_key, curated, "curated_alias")
        if not candidate.name or self.offline:
            return None, None
        try:
            response = self._request(f"find/compound/{quote(candidate.name)}")
        except Exception:
            return None, None
        query = _normalize_name(candidate.name)
        for line in response.splitlines():
            entry, _, names = line.partition("\t")
            aliases = [_normalize_name(item) for item in names.split(";")]
            match = _ID_RE.search(entry)
            if match and query in aliases:
                return self._remember_mapping(
                    mapping_key, match.group(1), "kegg_exact_name"
                )
        return None, None

    def _candidate_mapping(self, key: str) -> dict | None:
        path = self.cache_dir / "candidate_mappings.json"
        if not key or not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8")).get(key)
        except Exception:
            return None

    def _remember_mapping(
        self, key: str, compound_id: str, method: str
    ) -> tuple[str, str]:
        if key:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self.cache_dir / "candidate_mappings.json"
            try:
                mappings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            except Exception:
                mappings = {}
            mappings[key] = {"compound_id": compound_id, "method": method}
            path.write_text(
                json.dumps(mappings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return compound_id, method

    def compound_context(
        self,
        compound_id: str,
        *,
        organism_code: str | None = None,
    ) -> KeggCompoundContext:
        try:
            links = self._request(f"link/reaction/{compound_id}")
            reaction_ids = _linked_ids(links, prefix="R")
            reactions = [
                reaction
                for reaction in self._reaction_contexts(reaction_ids, compound_id)
                if reaction.produces_target
            ]
            candidate_kos = sorted({ko for reaction in reactions for ko in reaction.ko_ids})
            present = (
                self._organism_kos(organism_code, candidate_kos) if organism_code else []
            )
            supported_reactions = [
                reaction.reaction_id
                for reaction in reactions
                if set(reaction.ko_ids) & set(present)
            ]
            return KeggCompoundContext(
                compound_id=compound_id,
                status="ok",
                producing_reactions=reactions,
                precursor_compound_ids=sorted({
                    precursor
                    for reaction in reactions
                    for precursor in reaction.precursor_compound_ids
                    if precursor not in _CURRENCY_COMPOUNDS
                }),
                candidate_kos=candidate_kos,
                present_kos=sorted(present),
                missing_kos=(
                    sorted(set(candidate_kos) - set(present)) if organism_code else []
                ),
                supported_reaction_ids=sorted(supported_reactions),
                reaction_coverage=(
                    len(supported_reactions) / len(reactions)
                    if reactions and organism_code else None
                ),
            )
        except Exception as exc:
            return KeggCompoundContext(
                compound_id=compound_id,
                status="unavailable",
                error=str(exc),
            )

    def _reaction_contexts(
        self, reaction_ids: list[str], target: str
    ) -> list[KeggReactionContext]:
        contexts: list[KeggReactionContext] = []
        for chunk in _chunks(reaction_ids, 10):
            response = self._request(f"get/{'+'.join(chunk)}")
            for record in _split_records(response):
                fields = parse_flat_file(record)
                entry = " ".join(fields.get("ENTRY", []))
                match = re.search(r"\bR\d{5}\b", entry)
                if match:
                    contexts.append(self._reaction_context(match.group(0), target, fields))
        return contexts

    def _reaction_context(
        self, reaction_id: str, target: str, fields: dict[str, list[str]]
    ) -> KeggReactionContext:
        equation = " ".join(fields.get("EQUATION", []))
        parsed = parse_reaction_equation(equation, target=target)
        orthology = " ".join(fields.get("ORTHOLOGY", []))
        enzyme = " ".join(fields.get("ENZYME", []))
        return KeggReactionContext(
            reaction_id=reaction_id,
            equation=equation,
            direction=parsed.direction,
            produces_target=parsed.produces_target,
            precursor_compound_ids=parsed.precursors,
            ko_ids=sorted(set(_KO_RE.findall(orthology))),
            ec_numbers=sorted(set(_EC_RE.findall(f"{orthology} {enzyme}"))),
        )

    def _organism_kos(self, organism_code: str, ko_ids: list[str]) -> list[str]:
        present: set[str] = set()
        for chunk in _chunks(ko_ids, 10):
            response = self._request(f"link/{organism_code}/{'+'.join(chunk)}")
            present.update(_linked_ids(response, prefix="K"))
        return sorted(present)

    def _request(self, path: str) -> str:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(path.encode("utf-8")).hexdigest()
        cache_path = self.cache_dir / f"{key}.json"
        if cache_path.exists():
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            return payload["response"]
        if self.offline:
            raise RuntimeError(f"KEGG offline cache miss: {path}")
        response = self.transport(path)
        cache_path.write_text(
            json.dumps({"path": path, "response": response}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return response

    def _http_get(self, path: str) -> str:
        response = requests.get(f"{self.base_url}/{path}", timeout=self.timeout)
        response.raise_for_status()
        return response.text


def _linked_ids(text: str, *, prefix: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        for match in _ID_RE.finditer(line):
            value = match.group(1)
            if value.startswith(prefix) and value not in found:
                found.append(value)
    return found


def _chunks(values: list[str], size: int):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _split_records(text: str) -> list[str]:
    return [record.strip() + "\n///\n" for record in text.split("///") if record.strip()]


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())
