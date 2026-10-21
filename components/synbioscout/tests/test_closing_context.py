"""SYNTHETIC reversible Meta-only encoding; no provider, network or history access."""
import copy
import json

import pytest

from scientist.closing_context import compact_closing_context, expand_closing_context
from scientist.context import accepted_evidence_index


def fixture_context():
    quotes = ["SYNTHETIC rats had a changed endpoint; this was not a human intervention. " * 5,
              "SYNTHETIC human observation showed no clinical improvement or causal intervention effect. " * 4]
    evidence = [{"evidence_id": f"e{i}", "source_id": f"s{i}", "quote": quote,
        "claim": "SYNTHETIC source statement only", "location": "SYNTHETIC Results paragraph",
        "study_system": "rats" if i == 1 else "human observational study", "condition": "SYNTHETIC condition",
        "endpoint": "SYNTHETIC endpoint", "polarity": "supports" if i == 1 else "opposes",
        "validated_span": True, "limitations": ["SYNTHETIC exposure unknown; no patient recommendation"]}
        for i, quote in enumerate(quotes, 1)]
    cards, claims, associations, reviews = [], [], [], []
    shared_gap = "SYNTHETIC exposure unknown; no patient recommendation"
    for version in (1, 2):
        claim = f"SYNTHETIC narrow question version {version}: " + "No human benefit has been established. " * 3
        condition = f"SYNTHETIC condition v{version}; " + "strain and delivery exposure remain unknown; " * 4
        endpoint = f"SYNTHETIC endpoint v{version}; do not substitute a biomarker for a clinical endpoint."
        quoted_gap = "SYNTHETIC original quote: " + quotes[version - 1] + " Interpretation remains uncertain."
        cards.append({"hypothesis_id": "h", "version": version, "claim": claim,
            "validation_question": f"SYNTHETIC validation question {version}", "condition": condition,
            "endpoint": endpoint, "evidence_ids": [f"e{version}"], "opposing_evidence_ids": ["e2"],
            "inference_gaps": [shared_gap, quoted_gap, shared_gap], "chassis": {"gaps": [shared_gap]},
            "unrecognized_scientific_field": {"negative": "SYNTHETIC unsupported extension must remain unknown"}})
        for kind, text in (("main", claim), ("intervention", "Literal prefix: " + condition + " / " + endpoint + " literal suffix.")):
            cid = f"c:v{version}:{kind}"
            claims.append({"claim_id": cid, "hypothesis_id": "h", "version": version, "kind": kind,
                "text": text, "evidence_ids": [f"e{version}"], "gaps": [shared_gap, quoted_gap],
                "relation_schema_version": 1, "assessment_scope": "inference", "state": "unknown"})
            associations.append({"claim_id": cid, "hypothesis_id": "h", "version": version,
                "claim_kind": kind, "review_id": f"review:{cid}", "evidence_id": f"e{version}",
                "source_id": f"s{version}", "polarity": evidence[version - 1]["polarity"],
                "verdict": "unknown", "relation_schema_version": 1, "assessment_scope": "inference",
                "claim_relation": "unassessed", "relation_basis": "legacy_global_polarity", "query_ids": []})
        reviews.append({"hypothesis_id": "h", "version": version, "verdict": "needs_evidence",
                        "required_evidence": [shared_gap], "objections": [quoted_gap]})
    sources = [{"source_id": f"s{i}", "url": f"https://example.invalid/synthetic/{i}",
        "title": f"SYNTHETIC source {i}", "scope": "local_excerpt", "original_scope": "full_text",
        "acquisition_origin": "saved_reuse", "original_excerpt_start": 100,
        "source_scope": {"original_scope": "full_text", "current_scope": "local_excerpt", "local_excerpt": True,
            "original_window": {"start": 100, "end": 100 + len(quote)}, "acquisition": "reused",
            "acquisition_origin": "saved_reuse", "fresh_fulltext": False},
        "windows": [{"start": 0, "end": len(quote), "original_start": 100,
                     "original_end": 100 + len(quote), "text": quote}],
        **({"nullable": None, "typed_marker": True} if i == 1 else {"typed_marker": 1})}
        for i, quote in enumerate(quotes, 1)]
    candidate = {"entity_id": "entity:1", "candidate_id": "candidate:1", "name": "SYNTHETIC molecule",
                 "aliases": ["SYNTHETIC alias"], "uncertainties": [shared_gap],
                 "identity_form": {"stereochemistry": "SYNTHETIC specified form"}}
    reference = {**copy.deepcopy(candidate), "source_ids": ["identity:1"],
                 "hypothesis_versions": [{"hypothesis_id": "h", "version": 1}, {"hypothesis_id": "h", "version": 2}]}
    return {"anchor": {"disease": "SYNTHETIC disease"}, "task": {"limitations": [shared_gap]},
        "accepted_evidence": evidence,
        "global_accepted_evidence_index": accepted_evidence_index(evidence, linked_evidence_ids=["e1", "e2"],
                                                                  claim_applicability=associations),
        "hypotheses": cards, "literature_landscape": {"claims": claims, "limitations": [shared_gap]},
        "reviewed_evidence_associations": associations, "reviews": reviews, "sources": sources,
        "candidates": [candidate], "entity_references": [reference], "comparisons": [],
        "instructions": "SYNTHETIC no new scientific adjudication"}


def windows(context):
    return {source["source_id"]: source["windows"] for source in context["sources"]}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def test_full_scientific_context_roundtrips_exactly_with_audited_local_windows():
    context = fixture_context()
    original = copy.deepcopy(context)
    compact = compact_closing_context(context)
    encoded = copy.deepcopy(compact)
    expanded = expand_closing_context(compact, source_windows=windows(context))
    assert expanded == original and context == original and compact == encoded
    assert json.dumps(expanded, sort_keys=True) == json.dumps(original, sort_keys=True)
    assert type(expanded["sources"][0]["typed_marker"]) is bool
    assert type(expanded["sources"][1]["typed_marker"]) is int
    assert expanded["sources"][0]["nullable"] is None and "nullable" not in expanded["sources"][1]


def test_quotes_are_direct_complete_single_occurrences_and_existing_card_fields_remain_direct():
    context = fixture_context()
    compact = compact_closing_context(context)
    for before, after in zip(context["accepted_evidence"], compact["accepted_evidence"], strict=True):
        assert after["quote"] == before["quote"]
        assert sum(value.count(before["quote"]) for value in strings(compact)) == 1
    for before, after in zip(context["hypotheses"], compact["hypotheses"], strict=True):
        for key in ("hypothesis_id", "version", "claim", "validation_question", "evidence_ids", "opposing_evidence_ids"):
            assert after[key] == before[key]
    assert "dictionary_columns" not in json.dumps(compact)
    assert isinstance(compact["accepted_evidence"], list)


def test_expansion_without_local_windows_never_invents_full_source_text():
    context = fixture_context()
    expanded = expand_closing_context(compact_closing_context(context))
    for source in expanded["sources"]:
        assert source["windows"] == [] and source["source_window_audit"]
        assert source["source_scope"]["current_scope"] == "local_excerpt"
        assert source["source_scope"]["fresh_fulltext"] is False
    for key in ("accepted_evidence", "global_accepted_evidence_index", "hypotheses", "reviews",
                "candidates", "entity_references", "reviewed_evidence_associations", "literature_landscape"):
        assert expanded[key] == context[key]


def test_local_window_hash_rejects_same_length_changed_raw_text():
    context = fixture_context()
    local = copy.deepcopy(windows(context))
    local["s1"][0]["text"] = "X" + local["s1"][0]["text"][1:]
    with pytest.raises(ValueError, match="Source window audit mismatch"):
        expand_closing_context(compact_closing_context(context), source_windows=local)


def test_exact_versions_and_literal_claim_differences_are_preserved():
    context = fixture_context()
    compact = compact_closing_context(context)
    serialized = json.dumps(compact)
    assert '"text_ref"' in serialized and '"text_parts"' in serialized
    expanded = expand_closing_context(compact, source_windows=windows(context))
    claims = expanded["literature_landscape"]["claims"]
    assert claims[1]["text"].startswith("Literal prefix: SYNTHETIC condition v1")
    assert claims[3]["text"].startswith("Literal prefix: SYNTHETIC condition v2")
    assert all(c["text"].endswith(" literal suffix.") for c in (claims[1], claims[3]))


def test_identity_matching_is_unique_and_field_exact_including_negative_annotations():
    context = fixture_context()
    context["entity_references"][0]["name"] = "SYNTHETIC alternative salt"
    context["entity_references"][0]["identity_form"] = {"stereochemistry": "SYNTHETIC unresolved form"}
    compact = compact_closing_context(context)
    reference = compact["entity_references"][0]
    assert reference["name"] == "SYNTHETIC alternative salt"
    assert reference["identity_form"] == {"stereochemistry": "SYNTHETIC unresolved form"}
    assert expand_closing_context(compact, source_windows=windows(context)) == context
    context["candidates"].append(copy.deepcopy(context["candidates"][0]))
    assert "candidate_ref" not in compact_closing_context(context)["entity_references"][0]


def test_reencoding_is_idempotent_and_empty_context_collections_remain_valid():
    compact = compact_closing_context(fixture_context())
    assert compact_closing_context(compact) == compact
    context = {"sources": [], "accepted_evidence": [], "global_accepted_evidence_index": [],
        "reviewed_evidence_associations": [], "hypotheses": [], "reviews": [], "candidates": [],
        "entity_references": [], "literature_landscape": None}
    assert expand_closing_context(compact_closing_context(context), source_windows={}) == context

