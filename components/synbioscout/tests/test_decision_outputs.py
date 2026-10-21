"""Unreviewed drafts and configured exclusions cannot become recommendations."""
import pytest
from fastapi.testclient import TestClient

from scientist.api import create_app
from scientist.evidence import decide_candidates
from scientist.models import (
    CandidateRecord,
    EvidenceRecord,
    LLMSettings,
    ResearchBrief,
    ScientistSettings,
    TargetPackage,
)
from scientist.runtime import JobService


def candidate(**values):
    return CandidateRecord(candidate_id="mol_fixture", name="fixture", identity_status="resolved",
        gate={"gate_status": "success", "gate_class": "A1"}, **values)


def evidence(polarity):
    return EvidenceRecord(evidence_id="ev_fixture", claim="fixture claim", source_id="source_fixture",
        quote="fixture original text span", location="fixture", polarity=polarity, validated_span=True)


def test_unreviewed_literature_is_held_even_with_positive_gate():
    c = candidate(routes=["literature"])
    decide_candidates([c], [])
    assert c.decision == "hold"
    assert "review did not pass" in c.decision_reasons[0]


def test_only_opposing_evidence_is_held():
    c = candidate(routes=["literature"], evidence_ids=["ev_fixture"])
    decide_candidates([c], [evidence("opposes")])
    assert c.decision == "hold"
    assert "not a universal toxicity" in c.decision_reasons[0]


def test_admet_bounds_exclusion_overrides_positive_gate():
    c = candidate(evidence_ids=["ev_fixture"], admet={"admet_status": "success", "selection_pass": False})
    decide_candidates([c], [evidence("supports")])
    assert c.decision == "hold"


def test_computational_exploration_does_not_require_fabricated_literature():
    c = candidate(routes=["target_screening"])
    decide_candidates([c], [])
    assert c.decision == "exploratory"
    assert any("computational exploration" in reason for reason in c.decision_reasons)


@pytest.mark.parametrize("kind", ["protein_pdb", "sequence_fasta", "reference_ligand_sdf"])
def test_target_download_is_project_confined(tmp_path, kind):
    settings = ScientistSettings(workspace=str(tmp_path / "workspace"), llm=LLMSettings(backend="none"))
    service = JobService(settings)
    pid = service.store.create_project("SYNTHETIC download fixture")["project_id"]
    run = service.store.create_run(pid, ResearchBrief(disease="fixture only"))
    directory = service.store.run_dir(pid, run.run_id)
    inside = directory / "fixture.pdb"
    inside.write_text("SYNTHETIC target download test; not a protein structure")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-project fixture")
    run.target_packages = [TargetPackage(package_id="inside", status="needs_review", **{kind: str(inside)}),
                           TargetPackage(package_id="outside", status="needs_review", **{kind: str(outside)})]
    service.store.save_run(run)
    prefix = f"/api/projects/{pid}/runs/{run.run_id}/target-packages"
    with TestClient(create_app(settings, service=service)) as client:
        result = client.get(f"{prefix}/inside/{kind}")
        assert result.status_code == 200
        assert result.content == inside.read_bytes()
        assert client.get(f"{prefix}/outside/{kind}").status_code == 404
        assert client.get(f"{prefix}/inside/private_log").status_code == 404

