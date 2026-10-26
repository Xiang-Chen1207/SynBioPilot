"""Offline contract tests: all papers, claims and identities below are fixtures."""

import copy
import json

import pytest
import requests
from rdkit import Chem

from scientist.llm import InferenceError
from scientist.models import (
    ExecutionStatus,
    ResearchBrief,
    ResearchBudget,
    ScientistSettings,
    SourceRecord,
)
from scientist.research import ResearchEngine
from scientist.store import ProjectStore, digest

LITERATURE_QUOTE = "The illustrative compound changed the recorded cellular assay endpoint."
TARGET_QUOTE = "The illustrative gene was associated with the recorded disease phenotype."
OPPOSING_QUOTE = "A separate illustrative study found no improvement in its assay endpoint."


def source(identifier, text, *, scope="abstract"):
    return SourceRecord(
        source_id=identifier,
        provider="offline_fixture",
        title="SYNTHETIC research fixture",
        url="https://example.invalid/offline-fixture",
        retrieved_at="2026-01-01T00:00:00Z",
        text=text,
        evidence_scope=scope,
        metadata={"synthetic": True},
    )


class FakeSources:
    def __init__(self, *, identity_failures=(), paper_count=2, no_text=False):
        self.identity_failures = set(identity_failures)
        self.searches = []
        self.full_reads = []
        self.molecule_resolutions = []
        self.target_resolutions = []
        self.papers = [source("fixture:paper:1", "" if no_text else LITERATURE_QUOTE)]
        if paper_count > 1:
            self.papers += [source("fixture:paper:2", "" if no_text else OPPOSING_QUOTE)]
        self.papers += [
            source(f"fixture:paper:{i}", "Additional synthetic source text only.")
            for i in range(3, paper_count + 1)
        ]
        self.no_text = no_text

    def search_entity(self, text, entity):
        assert entity == "disease"
        return [{"id": "FIXTURE_DISEASE", "name": text}]

    def disease_targets(self, disease_id, limit):
        assert disease_id == "FIXTURE_DISEASE"
        return {"text": "" if self.no_text else TARGET_QUOTE}

    def database_source(self, key, title, value, url):
        return source(key, value["text"], scope="database")

    def search_literature(self, query, limit):
        self.searches.append((query, limit))
        # Return more than requested to test the engine's own hard cap.
        return copy.deepcopy(self.papers)

    def full_text(self, record):
        self.full_reads.append(record.source_id)

    def resolve_target(self, symbol, species):
        self.target_resolutions.append((symbol, species))
        if symbol in self.identity_failures:
            raise ValueError("Synthetic ambiguous protein identity")
        return "FIXTURE_ACCESSION", source(
            "fixture:identity:" + symbol, "Synthetic target identity.", scope="database"
        )

    def resolve_molecule(self, name):
        self.molecule_resolutions.append(name)
        if name in self.identity_failures:
            raise ValueError("Synthetic ambiguous chemical identity")
        # Valid structures/keys exercise the production identity verifier. These
        # identifiers and labels remain SYNTHETIC, without a PubChem request.
        unique_names = list(dict.fromkeys(self.molecule_resolutions))
        index = unique_names.index(name)
        molecule = Chem.MolFromSmiles("C" * (index + 2) + "O")
        canonical, key = Chem.MolToSmiles(molecule, isomericSmiles=True), Chem.MolToInchiKey(molecule)
        cid = str(100001 + index)
        identity = {"candidate_id": "mol_" + digest([canonical, key])[:16],
                    "smiles": canonical, "inchikey": key, "pubchem_cid": cid}
        identity_source = source("pubchem:" + cid, "SYNTHETIC chemical identity.", scope="database")
        identity_source.provider = "pubchem"
        return identity, identity_source


def evidence(key, identifier, quote, *, polarity="supports"):
    return {
        "key": key,
        "source_id": identifier,
        "claim": "Synthetic descriptive claim only.",
        "quote": quote,
        "location": "fixture paragraph 1",
        "polarity": polarity,
        "study_system": "synthetic cells",
        "condition": "synthetic condition",
        "endpoint": "synthetic endpoint",
        "limitations": ["Offline synthetic fixture only."],
    }


def target(symbol="FIXTURE1", keys=("target",)):
    return {
        "gene_symbol": symbol,
        "desired_direction": "inhibit",
        "rationale": "Fixture hypothesis.",
        "related_cells": [],
        "pathways": [],
        "evidence_keys": list(keys),
        "uncertainties": [],
    }


def candidate(name="Illustrative compound", keys=("molecule",), relations=()):
    return {
        "name": name,
        "evidence_keys": list(keys),
        "target_relations": list(relations),
        "rationale": "Independent literature route fixture.",
        "uncertainties": [],
    }


def default_draft():
    return {
        "evidence": [
            evidence("molecule", "fixture:paper:1", LITERATURE_QUOTE),
            evidence("target", "ot:disease:FIXTURE_DISEASE", TARGET_QUOTE),
        ],
        "targets": [target()],
        "candidates": [candidate()],
        "unresolved_questions": [],
    }


class FakeLLM:
    def __init__(
        self,
        settings,
        directory,
        max_calls,
        *,
        draft,
        verdicts,
        fail_review=False,
        link_review="supported",
    ):
        self.max_calls = max_calls
        self.calls = self.cache_hits = 0
        self.draft = draft
        self.verdicts = verdicts
        self.fail_review = fail_review
        self.link_review = link_review
        self.requests = []

    def complete(self, system, user, schema):
        if self.calls >= self.max_calls:
            raise InferenceError("Configured LLM call budget exhausted")
        self.calls += 1
        payload = json.loads(user)
        self.requests.append((schema["title"], payload))
        if schema["title"] == "PlanDraft":
            return {
                "disease_english": "Synthetic disease",
                "phenotype_english": "Fixture phenotype",
                "searches": [f"synthetic query {i}" for i in range(5)],
                "research_questions": ["Independent routes?"],
                "assumptions": [],
            }
        if schema["title"] == "SynthesisDraft":
            return copy.deepcopy(self.draft)
        if schema["title"] == "ReviewDraft":
            if self.fail_review:
                raise InferenceError("Synthetic review service failure")
            response = {
                "reviews": [
                    {
                        "evidence_id": item["evidence_id"],
                        "verdict": self.verdicts.get(item["source_id"], "supported_by_source"),
                        "reason": "Offline fixture verdict only.",
                    }
                    for item in payload["evidence"]
                ]
            }
            if self.link_review != "omitted":
                mapping = payload["evidence_key_mapping"]
                supplied_ids = {item["evidence_id"] for item in payload["evidence"]}

                def ids(keys):
                    related = {mapping[key] for key in keys if key in mapping}
                    return sorted(
                        supplied_ids - related if self.link_review == "unrelated" else related
                    )

                response["target_links"] = [
                    {
                        "gene_symbol": item["gene_symbol"],
                        "direction_supported": True,
                        "evidence_ids": ids(item["evidence_keys"]),
                        "reason": "Synthetic link review.",
                    }
                    for item in payload["targets"]
                ]
                response["molecule_links"] = [
                    {
                        "name": item["name"],
                        "gene_symbol": relation["gene_symbol"],
                        "relation_supported": True,
                        "evidence_ids": ids(item["evidence_keys"]),
                        "reason": "Synthetic link review.",
                    }
                    for item in payload["candidates"]
                    for relation in item["target_relations"]
                ]
            return response
        raise AssertionError("Unexpected inference schema")


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Offline research tests must never contact external services")

    monkeypatch.setattr(requests.sessions.Session, "request", fail)


def execute(
    tmp_path,
    *,
    draft=None,
    sources=None,
    budget=None,
    verdicts=None,
    fail_review=False,
    link_review="supported",
):
    store = ProjectStore(tmp_path / "workspace")
    project = store.create_project("SYNTHETIC offline research tests")
    run = store.create_run(
        project["project_id"],
        ResearchBrief(
            disease="Synthetic disease",
            phenotype="Fixture phenotype",
            budget=budget or ResearchBudget(),
        ),
    )
    instances = []

    def factory(settings, directory, max_calls):
        llm = FakeLLM(
            settings,
            directory,
            max_calls,
            draft=draft or default_draft(),
            verdicts=verdicts or {},
            fail_review=fail_review,
            link_review=link_review,
        )
        instances.append(llm)
        return llm

    sources = sources or FakeSources()
    engine = ResearchEngine(store, ScientistSettings(), sources=sources, llm_factory=factory)
    result = engine.execute(run.project_id, run.run_id, prepare_targets=False)
    return result, store, sources, instances[0]


def test_two_routes_retain_literature_candidate_without_target_binding(tmp_path):
    result, store, sources, llm = execute(tmp_path)
    assert result.status == ExecutionStatus.COMPLETED
    assert len(result.targets) == len(result.candidates) == 1
    # This fixture's target quotation is only a disease association in a
    # database snapshot. Even its synthetic reviewer cannot make that an
    # independently supported intervention direction.
    assert result.targets[0].desired_direction == "unknown"
    assert result.targets[0].uniprot_id == "FIXTURE_ACCESSION"
    molecule = result.candidates[0]
    assert molecule.routes == ["literature"]
    assert molecule.target_relations == {}
    assert molecule.evidence_ids and molecule.identity_status == "resolved"
    assert molecule.decision == "exploratory"
    assert len(result.evidence) == 2
    assert all(item.validated_span for item in result.evidence)
    assert llm.calls == 3
    assert sources.molecule_resolutions == ["Illustrative compound"]
    assert store.load_run(result.project_id, result.run_id).evidence == result.evidence


def test_invented_quote_and_unknown_source_never_reach_entailment_review(tmp_path):
    draft = default_draft()
    draft["evidence"] += [
        evidence(
            "invented", "fixture:paper:1", "This quotation never appears in the fixture source."
        ),
        evidence("unknown", "fixture:missing", LITERATURE_QUOTE),
    ]
    draft["candidates"] += [candidate("Unsupported compound", keys=("invented", "unknown"))]
    result, store, _, llm = execute(tmp_path, draft=draft)
    assert len(result.evidence) == 2
    reviewed = next(payload for title, payload in llm.requests if title == "ReviewDraft")
    assert len(reviewed["evidence"]) == 2
    unsupported = next(item for item in result.candidates if item.name == "Unsupported compound")
    assert unsupported.evidence_ids == []
    assert any("Candidate citation review failed" in item for item in unsupported.uncertainties)
    path = store.run_dir(result.project_id, result.run_id) / "evidence_span_rejections.json"
    assert len(json.loads(path.read_text())) == 2


@pytest.mark.parametrize("verdict", ["overstated", "unsupported", "needs_review"])
def test_review_rejections_remove_evidence_and_downgrade_claims(tmp_path, verdict):
    draft = default_draft()
    draft["candidates"][0]["target_relations"] = [{"gene_symbol": "FIXTURE1", "relation": "direct"}]
    result, _, _, _ = execute(
        tmp_path,
        draft=draft,
        verdicts={"fixture:paper:1": verdict, "ot:disease:FIXTURE_DISEASE": verdict},
    )
    assert result.status == ExecutionStatus.PARTIAL
    assert result.evidence == []
    assert result.targets[0].evidence_ids == []
    assert result.targets[0].desired_direction == "unknown"
    assert result.candidates[0].evidence_ids == []
    assert list(result.candidates[0].target_relations.values()) == ["unknown"]


def test_review_service_failure_leaves_no_accepted_evidence(tmp_path):
    result, _, _, _ = execute(tmp_path, fail_review=True)
    assert result.status == ExecutionStatus.PARTIAL
    assert result.evidence == []
    assert any("review incomplete" in warning for warning in result.warnings)


def test_identity_failures_are_preserved_for_review_not_fabricated(tmp_path):
    result, _, _, _ = execute(
        tmp_path, sources=FakeSources(identity_failures={"Illustrative compound", "FIXTURE1"})
    )
    molecule = result.candidates[0]
    assert result.status == ExecutionStatus.PARTIAL
    assert molecule.identity_status == "needs_review"
    assert molecule.smiles == molecule.inchikey == molecule.pubchem_cid == ""
    assert molecule.decision == "hold"
    assert result.targets[0].uniprot_id == ""
    assert any("Identity unresolved" in item for item in result.targets[0].uncertainties)
    assert next(step for step in result.steps if step.step_id == "identity").status == "partial"


@pytest.mark.parametrize("link_review", ["omitted", "unrelated"])
def test_entity_links_require_separate_review_with_related_accepted_evidence(tmp_path, link_review):
    draft = default_draft()
    draft["candidates"][0]["target_relations"] = [{"gene_symbol": "FIXTURE1", "relation": "direct"}]
    result, _, _, _ = execute(tmp_path, draft=draft, link_review=link_review)
    assert len(result.evidence) == 2
    assert result.targets[0].evidence_ids
    assert result.candidates[0].evidence_ids
    assert result.targets[0].desired_direction == "unknown"
    assert list(result.candidates[0].target_relations.values()) == ["unknown"]


def test_explicitly_reviewed_molecule_relation_can_be_retained(tmp_path):
    draft = default_draft()
    draft["candidates"][0]["target_relations"] = [
        {"gene_symbol": "FIXTURE1", "relation": "parallel"}
    ]
    result, _, _, _ = execute(tmp_path, draft=draft)
    assert list(result.candidates[0].target_relations.values()) == ["parallel"]


def test_budget_caps_retrieval_targets_candidates_and_deep_reads(tmp_path):
    draft = default_draft()
    draft["targets"] += [target("FIXTURE2"), target("FIXTURE3")]
    draft["candidates"] += [candidate("Other compound"), candidate("Third compound")]
    budget = ResearchBudget(
        max_targets=1, literature_max_candidates=1, max_searches=1, max_papers=2
    )
    result, _, sources, _ = execute(
        tmp_path, draft=draft, sources=FakeSources(paper_count=9), budget=budget
    )
    assert len(sources.searches) == 1
    papers = [
        record for record in result.sources if record.evidence_scope in {"abstract", "full_text"}
    ]
    assert len(papers) == 2
    assert len(sources.full_reads) == 2
    assert len(result.targets) == len(result.candidates) == 1
    assert sources.molecule_resolutions == ["Illustrative compound"]


def test_llm_budget_exhaustion_in_review_returns_partial_without_evidence(tmp_path):
    result, _, _, llm = execute(tmp_path, budget=ResearchBudget(max_llm_calls=2))
    assert result.status == ExecutionStatus.PARTIAL
    assert llm.calls == result.usage["llm_calls"] == 2
    assert result.evidence == []
    assert any("budget exhausted" in warning for warning in result.warnings)


def test_opposing_evidence_is_preserved_with_its_scope(tmp_path):
    draft = default_draft()
    draft["evidence"].append(
        evidence("opposition", "fixture:paper:2", OPPOSING_QUOTE, polarity="opposes")
    )
    draft["candidates"][0]["evidence_keys"].append("opposition")
    result, _, _, _ = execute(tmp_path, draft=draft)
    opposing = next(item for item in result.evidence if item.polarity == "opposes")
    assert opposing.condition == "synthetic condition"
    assert opposing.endpoint == "synthetic endpoint"
    assert opposing.evidence_id in result.candidates[0].evidence_ids
    assert any("Conflicting/opposing" in reason for reason in result.candidates[0].decision_reasons)


def test_outputs_and_unverified_memory_are_written_for_partial_run(tmp_path):
    result, store, _, _ = execute(tmp_path, fail_review=True)
    directory = store.run_dir(result.project_id, result.run_id)
    for filename in [
        "input.json",
        "run.json",
        "plan.json",
        "sources.json",
        "synthesis_draft.json",
        "evidence_span_rejections.json",
        "evidence.json",
        "targets.json",
        "candidates.json",
        "report.md",
    ]:
        assert (directory / filename).is_file()
    assert json.loads((directory / "run.json").read_text())["status"] == "partial"
    assert json.loads((directory / "evidence.json").read_text()) == []
    assert store.artifact_path(result.project_id, result.report_path) == directory / "report.md"
    memories = store.search_memory(result.project_id, "Synthetic disease")
    assert len(memories) == 1
    assert memories[0]["verified"] is False
    assert memories[0]["source_run_id"] == result.run_id
    assert memories[0]["metadata"]["scope"] == "research_predictions_not_experimental_facts"


def test_retrieved_records_without_text_fail_synthesis_as_partial(tmp_path):
    result, _, _, llm = execute(tmp_path, sources=FakeSources(no_text=True))
    assert result.status == ExecutionStatus.PARTIAL
    assert result.evidence == result.targets == result.candidates == []
    assert llm.calls == 1
    assert any("Source retrieval is empty" in error for error in result.errors)
    assert next(step for step in result.steps if step.step_id == "synthesize").status == "failed"

