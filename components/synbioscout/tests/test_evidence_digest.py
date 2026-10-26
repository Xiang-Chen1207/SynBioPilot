"""SYNTHETIC evidence strata; publication counts are not efficacy or replication."""
import copy
import hashlib
import io
import json
import zipfile
from pathlib import Path

from test_candidate_assessment_support_scope import reviewed_case
from test_research_flow import hypothesis, setup_flow, source

from scientist.background import load_background_package
from scientist.candidate_assessments import assessment_cards
from scientist.evidence_digest import (
    build_evidence_digest,
    hypothesis_groups,
    persist_evidence_digest,
)
from scientist.exports import export_document, export_research, markdown_bytes
from scientist.models import CandidateRecord, EvidenceRecord, ExecutionStatus, ResearchBrief
from scientist.research_public import public_research_flow
from scientist.store import ProjectStore


def citation(eid, s, *, polarity, system="SYNTHETIC children", condition="SYNTHETIC adjunctive treatment", endpoint="SYNTHETIC associated endpoint"):
    return EvidenceRecord(evidence_id=eid, source_id=s.source_id, claim="SYNTHETIC scoped result", quote=s.text,
        location="SYNTHETIC complete continuous results paragraph", polarity=polarity,
        study_system=system, condition=condition, endpoint=endpoint, validated_span=True)


def test_duplicate_publication_snapshots_do_not_multiply_trials_or_erase_opposition():
    engine, _ = setup_flow()
    a = source(text="Illustrative compound improved one endpoint in the SYNTHETIC assay.",
        metadata={"publication_types":{"pubType":["Randomized Controlled Trial"]}})
    b = source("europepmc:1", provider="europe_pmc", text="Illustrative compound did not improve the other endpoint in the SYNTHETIC assay.",
        metadata={"publication_types":{"pubType":["Randomized Controlled Trial"]}})
    review = source("pubmed:2", pmid="2", doi="10.0000/review", text="The SYNTHETIC review reported mixed prior study results.",
        metadata={"publication_types":{"pubType":["Meta-Analysis"]}})
    engine.run.sources = [a,b,review]
    engine.run.candidates = [CandidateRecord(candidate_id="synthetic_compound",name="Illustrative compound")]
    engine.run.evidence = [citation("synthetic_support",a,polarity="supports"),
        citation("synthetic_oppose",b,polarity="opposes",endpoint="SYNTHETIC core endpoint"),
        citation("synthetic_mixed",review,polarity="mixed",system="unknown",condition="unknown")]
    before = engine.run.model_dump_json()
    result = build_evidence_digest(engine.run)
    assert result["counts"]["selected_publications"] == 2
    assert result["counts"]["randomized_trial_publications"] == 1
    assert result["counts"]["review_publications"] == 1
    assert result["counts"]["accepted_citations"] == 3
    assert result["counts"]["accepted_publications"] == 2
    assert result["counts"]["polarity_citations"] == {"supports":1,"opposes":1,"mixed":1,"context":0}
    trial = next(p for p in result["publications"] if p["source_design"] == "randomized_controlled_trial")
    assert set(trial["source_ids"]) == {a.source_id,b.source_id}
    assert {c["polarity"] for c in trial["accepted_citations"]} == {"supports","opposes"}
    assert {c["endpoint"] for c in trial["accepted_citations"]} == {"SYNTHETIC core endpoint","SYNTHETIC associated endpoint"}
    assert all(c["explicit_candidate_mentions"] == ["Illustrative compound"] for c in trial["accepted_citations"])
    assert all(c["intervention_mode"] == "adjunctive" for c in trial["accepted_citations"])
    assert "quote" not in json.dumps(result)
    assert engine.run.model_dump_json() == before


def test_provider_type_and_requested_population_never_upgrade_experimental_system():
    engine, _ = setup_flow()
    engine.run.brief.population = "human children"
    a = source(text="SYNTHETIC experiment in VPA mice and cells.",metadata={"publication_types":{"pubType":["Randomized Controlled Trial"]}})
    b = source("pubmed:2",pmid="2",doi="10.0000/unknown",title="Human randomized trial words in a title are not design metadata.",
        text="SYNTHETIC system not reported.")
    engine.run.sources = [a,b]
    engine.run.evidence = [citation("synthetic_mouse",a,polarity="context",system="VPA mice and cultured cells"),
        citation("synthetic_unknown",b,polarity="context",system="unknown",condition="unknown")]
    result = build_evidence_digest(engine.run)
    assert result["publications"][0]["accepted_citations"][0]["system_layer"] == "mixed_or_unspecified_systems"
    assert result["publications"][1]["accepted_citations"][0]["system_layer"] == "unknown"
    assert result["publications"][1]["source_design"] == "unknown"
    assert result["publications"][1]["accepted_citations"][0]["intervention_mode"] == "unknown"


def test_reference_identity_coverage_separates_selection_access_and_accepted_citations():
    engine, _ = setup_flow()
    selected = source(text="SYNTHETIC selected negative result.")
    unselected = source("pubmed:2",pmid="2",doi="10.0000/two",text="SYNTHETIC available unused result.")
    unavailable = source("pubmed:3",pmid="3",doi="10.0000/three",text="")
    engine.run.sources = [selected]
    result = persist_evidence_digest(engine.run,[selected,unselected,unavailable])
    coverage = {r["source_id"]:r for r in result["reference_coverage"]}
    assert coverage[selected.source_id]["selection_status"] == "selected"
    assert coverage[selected.source_id]["citation_status"] == "unreviewed_or_unaccepted"
    assert coverage[unselected.source_id]["selection_status"] == "not_selected"
    assert coverage[unselected.source_id]["access_status"] == "available"
    assert coverage[unavailable.source_id]["access_status"] == "no_access"
    engine.run.evidence = [citation("synthetic_negative",selected,polarity="opposes")]
    result = persist_evidence_digest(engine.run)
    assert result["reference_coverage"][0]["citation_status"] == "accepted"
    assert result["reference_coverage"][0]["accepted_evidence_ids"] == ["synthetic_negative"]
    assert len(engine.state.research_flow.reference_coverage) == 3
    engine.run.status = ExecutionStatus.COMPLETED
    before = engine.run.model_dump_json()
    persist_evidence_digest(engine.run,[source("pubmed:4",pmid="4",doi="10.0000/new")])
    assert engine.run.model_dump_json() == before


def test_frozen_package_five_trial_reference_identities_are_accounted_for_without_accepted_evidence():
    root = Path(__file__).resolve().parents[1]
    paths = sorted(p for p in (root/"knowledge/nac_asd_v1").rglob("*") if p.is_file())
    hashes = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    brief = ResearchBrief(disease="ASD",research_mode="co_scientist",background_package_id="nac_asd_v1",existing_candidates=["NAC"])
    package = load_background_package(root,brief)
    engine, _ = setup_flow()
    engine.run.brief = brief
    engine.run.sources = package.sources[:2]
    result = persist_evidence_digest(engine.run,package.sources)
    references = result["reference_coverage"]
    core = [r for r in references if r["declared_evidence_level"] == "human_randomized_controlled_trial"]
    assert len(core) == 5 and len(references) == 6
    assert {r["source_id"] for r in core} == {"pmid:22342106","pmid:23886027","pmid:25580916","pmid:27103982","pmid:27316706"}
    assert sum(r["selection_status"] == "selected" for r in references) == 2
    assert sum(r["selection_status"] == "not_selected" for r in references) == 4
    assert all(r["citation_status"] == "unreviewed_or_unaccepted" for r in references)
    assert all(r["local_excerpt_only"] for r in references)
    assert next(r for r in references if r["source_id"] == "pmid:23886027")["original_source_scope"] == "full_text"
    assert all(r["source_scope"] == "abstract" for r in references)
    assert engine.run.evidence == []
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == expected for p,expected in hashes.items())


def test_exact_duplicate_proposals_are_grouped_but_mechanism_endpoint_negation_and_opposition_survive():
    candidate = CandidateRecord(candidate_id="synthetic",name="Illustrative compound",aliases=["IC"])
    original = hypothesis("one")
    original.evidence_ids = ["synthetic_support"]
    repeated = original.model_copy(deep=True,update={"hypothesis_id":"h_repeated","title":"Another generation view","molecule_names":["IC"]})
    alternatives = [original.model_copy(deep=True,update={"hypothesis_id":"h_"+key,key:value}) for key,value in [
        ("mechanism","another mechanism"),("endpoint","another endpoint"),("claim","not " + original.claim),
        ("opposing_evidence_ids",["synthetic_opposition"]),("prediction","measured change = -1")]]
    distinct_sign = alternatives[-1].model_copy(deep=True,update={"hypothesis_id":"h_positive","prediction":"measured change = +1"})
    cards = [original,repeated,*alternatives,distinct_sign]
    before = [c.model_dump() for c in cards]
    groups = hypothesis_groups(cards,[candidate])
    assert len(groups) == len(cards)-1
    assert groups[0]["hypothesis_ids"] == [original.hypothesis_id,repeated.hypothesis_id]
    assert all(g["duplicates_retained"] for g in groups)
    assert [c.model_dump() for c in cards] == before


def test_old_gap_text_gets_claim_scope_and_never_overwrites_current_support():
    engine, flow, h = reviewed_case(explicit_entity=True)
    assert flow.main_reviewed(h)
    h.inference_gaps.append("SYNTHETIC old unscoped statement")
    before = engine.run.model_dump_json()
    card = assessment_cards(engine.run)[0]
    assert card["scientific_status"] == "source_claim_supported_pending_validation"
    stale = [g for g in card["unknowns"] if "没有通过来源/片段/主张归属校验的引用" in g]
    assert stale and all(" / main / " not in g and " / unknown / 当前缺口]" in g for g in stale)
    assert any(n["text"] == "SYNTHETIC old unscoped statement" and n["scope_status"] == "recorded_scope_unconfirmed"
               for n in card["recorded_gap_notes"])
    assert any("原记录未定范围，未重新裁定" in g for g in card["unknowns"])
    assert next(c for c in card["scoped_claim_gaps"] if c["kind"] == "main")["state"] == "supported"
    assert engine.run.model_dump_json() == before


def test_digest_export_retains_coverage_and_rejects_private_receipt_fields(tmp_path):
    engine, _, _ = reviewed_case(explicit_entity=True)
    persist_evidence_digest(engine.run,engine.run.sources)
    store = ProjectStore(tmp_path)
    project = store.create_project("SYNTHETIC digest export")
    engine.run.project_id = project["project_id"]
    doc = export_document(engine.run)
    assert doc["evidence_digest"]["counts"]["accepted_citations"] == 1
    assert doc["co_scientist"]["research_flow"]["reference_coverage"][0]["citation_status"] == "accepted"
    raw = copy.deepcopy(doc["co_scientist"]["research_flow"])
    raw["reference_coverage"][0]["private_prompt"] = "PRIVATE_SENTINEL"
    assert "PRIVATE_SENTINEL" not in json.dumps(public_research_flow(raw))
    archive_bytes, _, _ = export_research(store,engine.run,"zip")
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert json.loads(archive.read("evidence_digest.json"))["reference_coverage"][0]["citation_status"] == "accepted"
    assert "证据分层与参考覆盖" in markdown_bytes(engine.run).decode()


def test_same_title_is_not_independent_identity_and_omitted_public_bodies_keep_recorded_opposition():
    engine, _ = setup_flow()
    a = source("pubmed:one",pmid="1",doi="10.0000/one",text="SYNTHETIC paper result.")
    b = source("pubmed:two",pmid="2",doi="10.0000/two",text="SYNTHETIC another paper result.")
    database = source("database:one",provider="pubchem",scope="database",pmid="",doi="",text="")
    engine.run.sources = [a,b,database]
    e = EvidenceRecord(evidence_id="synthetic_db_opposition",source_id=database.source_id,
        claim="SYNTHETIC accepted observation",quote="SYNTHETIC continuous database observation saved before public-body omission.",
        location="SYNTHETIC saved field",polarity="opposes",validated_span=True)
    engine.run.evidence = [e,e.model_copy(update={"evidence_id":"synthetic_unaccepted","validated_span":False})]
    result = build_evidence_digest(engine.run)
    assert result["counts"]["selected_publications"] == 2
    assert result["counts"]["accepted_citations"] == 1
    assert result["counts"]["polarity_citations"]["opposes"] == 1
    assert result["counts"]["accepted_publications"] == 0
    assert any(p["kind"] == "database" and p["accepted_citations"][0]["evidence_id"] == e.evidence_id
               for p in result["publications"])


def test_normal_generation_consumes_digest_without_an_additional_model_stage(tmp_path):
    from test_coscientist import execute
    _, _, run, control = execute(tmp_path)
    generation = [payload for schema,payload,_ in control.requests if schema == "GenerationDraft"]
    assert len(generation) == 2
    for payload in generation:
        assert payload["evidence_digest"]["version"] == "evidence-digest-1"
        indexed = {e["evidence_id"] for p in payload["evidence_digest"]["publications"] for e in p["accepted_citations"]}
        assert indexed == {e["evidence_id"] for e in payload["accepted_evidence"]}
        assert all(e["quote"] for e in payload["accepted_evidence"])
    reflection = [payload for schema,payload,_ in control.requests if schema == "ReflectionDraft"]
    assert reflection and all("polarity" in e and "study_system" in e and e["quote"]
        for payload in reflection for e in payload["accepted_evidence"])
    assert all(schema != "EvidenceDigestDraft" for schema,_,_ in control.requests)
    assert run.co_scientist.clusters and all(group["duplicates_retained"] for group in run.co_scientist.clusters)

