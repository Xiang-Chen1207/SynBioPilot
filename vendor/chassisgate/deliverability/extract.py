import json
import time
from typing import Protocol

from pydantic import ValidationError

from config import get_settings
from .schema import (
    Candidate,
    Chassis,
    EvidenceObject,
    Judgment,
    PrecursorAvailable,
    ProvenanceStrength,
    RelationType,
    SemanticSource,
)


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str, *, temperature: float) -> str: ...


def build_messages(
    candidate: Candidate,
    chassis: Chassis,
    retrieved_snippets: list[str] | None = None,
    *,
    literature_only: bool = False,
) -> tuple[str, str]:
    relation_values = ", ".join(item.value for item in RelationType)
    provenance_values = ", ".join(item.value for item in ProvenanceStrength)
    evidence_keys = ", ".join(
        key
        for key in EvidenceObject.model_fields
        if key != "actual_producer"
    )
    evidence_example = json.dumps(
        {
            "organism": chassis.name,
            "enzyme_or_pathway": None,
            "relation_type": RelationType.NOT_REPORTED.value,
            "precursor_available": PrecursorAvailable.UNKNOWN.value,
            "provenance_strength": ProvenanceStrength.INFERRED.value,
            "citation_pmid": None,
            "support_score": 0.0,
        },
        separators=(",", ":"),
    )
    reasoning_instruction = (
        "Literature-only mode: use ONLY the retrieved records below. Every informative evidence "
        "object must cite a PMID present in those records. Use the supplied records as the evidence source. "
        "When chassis-conditioned support is missing, use relation_type=not_reported, "
        "raw_confidence=0, and retain missing-report status separately from negative evidence.\n"
        if literature_only
        else
        "Reasoning: use BOTH the retrieved literature AND your biochemical knowledge of the organism's "
        "known metabolism. Assign non_native only with matching pathway evidence — if the organism is well "
        "established to natively produce the molecule, answer denovo (or biotransform). Reserve non_native "
        "for molecules with evidence that their pathway is absent from the chassis.\n"
    )
    system = (
        "You assess chassis-conditioned microbial deliverability: can the given chassis "
        "CURRENTLY produce or biotransform the candidate molecule WITHOUT installing any new "
        "heterologous pathway?\n"
        "Definitions (use exactly):\n"
        "- semantic_source=denovo: the chassis NATIVELY synthesizes the molecule de novo from its own "
        "central metabolism / standard medium, using enzymes encoded in its OWN genome (e.g. a central "
        "fermentation product of that organism).\n"
        "- semantic_source=biotransform: the chassis NATIVELY has the enzyme(s) to convert an externally "
        "supplied precursor into the molecule; de novo synthesis is unavailable.\n"
        "- semantic_source=non_native: the pathway is absent from the chassis; the molecule is made by "
        "other taxa or needs enzymes the chassis lacks.\n"
        "- engineering_required=true ONLY if obtaining the molecule would require introducing a NEW "
        "heterologous enzyme/pathway absent from the chassis. If the chassis natively makes it, false.\n"
        + reasoning_instruction
        + "Express genuine uncertainty with a LOW "
        "raw_confidence; semantic_source follows the pathway evidence.\n"
        "Return only one valid JSON object with keys: semantic_source, precursor_available, "
        "engineering_required, raw_confidence, evidence. "
        "semantic_source in denovo, biotransform, non_native. "
        "precursor_available in available_in_medium, available_by_simple_feeding, requires_custom_synthesis, unknown. "
        f"Each evidence object MUST have keys: {evidence_keys}. "
        f"relation_type must be one of {relation_values}. "
        "evidence.precursor_available must use the same allowed precursor values. "
        f"provenance_strength must be one of {provenance_values}. "
        "support_score must be a number from 0 to 1. "
        f"Example evidence object: {evidence_example}. "
        "Report verifiable citations and evidence with their provenance."
    )
    if retrieved_snippets:
        evidence_text = "\n".join(f"- {snippet}" for snippet in retrieved_snippets)
    else:
        evidence_text = "No retrieved evidence available."

    user = (
        f"Candidate id: {candidate.id}\n"
        f"Candidate name: {candidate.name}\n"
        f"Candidate SMILES: {candidate.smiles or 'unknown'}\n"
        f"Chassis id: {chassis.id}\n"
        f"Chassis name: {chassis.name}\n\n"
        f"Retrieved evidence:\n{evidence_text}\n\n"
        "Judge whether the chassis can deliver this candidate now under the "
        "registered schema. Return JSON only."
    )
    return system, user


def _safe_judgment(candidate: Candidate, chassis: Chassis) -> Judgment:
    return Judgment(
        candidate_id=candidate.id,
        chassis_id=chassis.id,
        evidence=[
            EvidenceObject(
                organism=chassis.name,
                enzyme_or_pathway=None,
                relation_type=RelationType.NOT_REPORTED,
                precursor_available=PrecursorAvailable.UNKNOWN,
                provenance_strength=ProvenanceStrength.INFERRED,
                citation_pmid=None,
                support_score=0.0,
            )
        ],
        semantic_source=SemanticSource.NON_NATIVE,
        precursor_available=PrecursorAvailable.UNKNOWN,
        engineering_required=False,
        raw_confidence=0.0,
    )


def _coerce_enum(value, enum_cls, default):
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except (TypeError, ValueError):
        return default


def parse_judgment(raw_json: str, candidate: Candidate, chassis: Chassis) -> Judgment:
    if not raw_json or not raw_json.strip():
        return _safe_judgment(candidate, chassis)
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return _safe_judgment(candidate, chassis)
    if not isinstance(payload, dict):
        return _safe_judgment(candidate, chassis)

    core_precursor_or_unknown = _coerce_enum(
        payload.get("precursor_available"),
        PrecursorAvailable,
        PrecursorAvailable.UNKNOWN,
    )
    raw_evidence = payload.get("evidence") or []
    if not isinstance(raw_evidence, list):
        raw_evidence = []

    coerced_evidence = []
    for item in raw_evidence:
        if not isinstance(item, dict):
            continue
        try:
            coerced_evidence.append(EvidenceObject.model_validate(item))
            continue
        except ValidationError:
            pass

        citation_id = (
            item.get("citation_pmid")
            or item.get("pmid")
            or item.get("id")
        )
        if citation_id is None and not item.get("organism"):
            continue

        relation_type = _coerce_enum(
            item.get("relation_type"),
            RelationType,
            RelationType.NOT_REPORTED,
        )
        provenance_strength = _coerce_enum(
            item.get("provenance_strength"),
            ProvenanceStrength,
            ProvenanceStrength.INFERRED,
        )
        precursor_available = _coerce_enum(
            item.get("precursor_available"),
            PrecursorAvailable,
            None,
        )
        coerced_evidence.append(
            EvidenceObject(
                organism=str(item.get("organism") or chassis.name),
                enzyme_or_pathway=item.get("enzyme_or_pathway"),
                relation_type=relation_type,
                precursor_available=precursor_available or core_precursor_or_unknown,
                provenance_strength=provenance_strength,
                citation_pmid=str(citation_id) if citation_id is not None else None,
                support_score=0.0,
            )
        )

    judgment_payload = {
        "candidate_id": candidate.id,
        "chassis_id": chassis.id,
        "semantic_source": payload.get("semantic_source"),
        "precursor_available": payload.get("precursor_available"),
        "engineering_required": payload.get("engineering_required", False),
        "raw_confidence": payload.get("raw_confidence", 0.0),
        "evidence": [evidence.model_dump() for evidence in coerced_evidence],
    }
    try:
        return Judgment.model_validate(judgment_payload)
    except ValidationError:
        return _safe_judgment(candidate, chassis)


def extract_judgment(
    client: LLMClient,
    candidate: Candidate,
    chassis: Chassis,
    *,
    temperature: float = 0.7,
    retrieved_snippets: list[str] | None = None,
    literature_only: bool = False,
) -> Judgment:
    system, user = build_messages(
        candidate,
        chassis,
        retrieved_snippets,
        literature_only=literature_only,
    )
    raw = client.complete_json(system, user, temperature=temperature)
    return parse_judgment(raw, candidate, chassis)


def _is_transient_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {408, 409, 429}:
        return True
    if isinstance(status_code, int) and 500 <= status_code <= 599:
        return True
    name = exc.__class__.__name__
    return name in {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
    }


class DeepSeekClient:
    def __init__(
        self,
        settings=None,
        *,
        max_retries: int = 3,
        backoff_seconds: float = 0.5,
        timeout_seconds: float | None = None,
    ):
        from openai import OpenAI

        self.settings = settings or get_settings()
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._client = OpenAI(
            base_url=self.settings.base_url,
            api_key=self.settings.api_key,
            timeout=timeout_seconds,
        )

    def complete_json(self, system: str, user: str, *, temperature: float) -> str:
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self.settings.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
                content = response.choices[0].message.content
                return content or ""
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries or not _is_transient_error(exc):
                    raise
                time.sleep(self.backoff_seconds * (2 ** attempt))
        raise last_error
