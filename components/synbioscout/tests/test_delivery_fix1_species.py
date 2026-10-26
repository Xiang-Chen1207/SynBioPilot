"""SYNTHETIC FIX1 study-object gate; no universal species-recognition claim."""
import copy

import pytest
from test_claim_relative_observation import QUOTE, adjudicate, observation_case

from scientist.candidate_assessments import assessment_cards


@pytest.mark.parametrize("animal", [
    "juvenile Wistar rodents", "zebrafish Danio rerio", "mice", "Sprague-Dawley rats", "hamsters",
    "rabbits", "guinea pigs", "rhesus macaques", "nonhuman primates", "dogs", "pigs",
    "Drosophila melanogaster", "Caenorhabditis elegans", "Xenopus laevis", "chicken embryos",
    "unknown vertebrate model", "an uncharacterized experimental system", "model organisms not reported",
])
def test_background_human_rationale_does_not_authorize_animal_or_unknown_observation(animal):
    quote=f"SYNTHETIC IC improved behavior in {animal}."
    engine,flow,h,s,e,claim,review=observation_case(quote=quote)
    s.text=f"SYNTHETIC human disease rationale; experiment used {animal}. " + quote
    before=copy.deepcopy(e.model_dump())
    flow.ingest([s])
    adjudicate(engine,flow,h,review)
    assert claim.state != "supported"
    assert flow.main_review_state(h) != "source_observation_supported"
    assert assessment_cards(engine.run)[0]["observation_support"] == []
    assert assessment_cards(engine.run)[0]["scientific_status"] == "needs_evidence"
    assert e.model_dump() == before


@pytest.mark.parametrize("system", [
    "human cell cultures", "human organoids", "human tissue explants ex vivo", "HEK293 cells",
    "HeLa cultures", "induced pluripotent stem cells", "in vitro preparations", "in silico models",
    "Saccharomyces cerevisiae cultures", "Escherichia coli cultures",
])
def test_human_word_in_cell_or_nonparticipant_system_does_not_identify_a_human_trial(system):
    quote=f"SYNTHETIC IC changed a marker in {system}."
    engine,flow,h,s,_,claim,review=observation_case(quote=quote)
    s.text=f"SYNTHETIC human condition background. This experiment used {system}. " + quote
    flow.ingest([s])
    adjudicate(engine,flow,h,review)
    assert claim.state != "supported"
    assert assessment_cards(engine.run)[0]["observation_support"] == []


@pytest.mark.parametrize("prefix", [
    "Background: human disease rationale only. ",
    "Previous studies enrolled 31 human children. ",
    "A prior trial in patients provides background only. ",
    "Methods: No human patients were enrolled. ",
    "Methods: Human patients were not enrolled. ",
    "Future work: human children will be recruited. ",
    "A planned clinical trial will recruit patients. ",
    "This model may be useful for human patients. ",
    "Human patients are the eventual intended population. ",
    "Human disease background; the study organism is unknown. ",
    "Earlier patients were randomized. This experiment used an uncharacterized system. ",
    "Methods: 31 patients were enrolled. This experiment used unknown model organisms. ",
])
def test_background_historical_negated_planned_or_conflicting_objects_abstain(prefix):
    engine,flow,h,s,_,claim,review=observation_case()
    s.text=prefix+QUOTE
    flow.ingest([s])
    adjudicate(engine,flow,h,review)
    assert claim.state != "supported"
    assert assessment_cards(engine.run)[0]["observation_support"] == []


@pytest.mark.parametrize("prefix", [
    "Methods: Thirty-one patients were enrolled and randomized to IC or placebo. ",
    "Methods: We enrolled 31 children and randomized them to the study groups. ",
    "This study was a randomized controlled trial of IC in youth with the condition. ",
    "Methods: The trial recruited human volunteers to compare IC with placebo. ",
    "方法：本研究纳入了31名儿童并随机分组。 ",
])
def test_explicit_current_human_study_objects_allow_only_the_exact_source_observation(prefix):
    engine,flow,h,s,e,claim,review=observation_case()
    s.text=prefix+QUOTE
    flow.ingest([s])
    adjudicate(engine,flow,h,review)
    assert claim.state == "supported"
    assert flow.main_review_state(h) == "source_observation_supported"
    assert not flow.main_reviewed(h)
    card=assessment_cards(engine.run)[0]
    assert card["observation_support"] and card["scientific_status"] == "needs_evidence"
    assert card["opposing_evidence_ids"] == [e.evidence_id]


@pytest.mark.parametrize("brief_species,anchor_species", [
    ("Mus musculus", "Homo sapiens"), ("Homo sapiens", "Mus musculus"),
    ("Homo sapiens", "unknown"), ("unknown", "Homo sapiens"),
])
def test_task_species_and_frozen_anchor_cannot_drift_to_unlock_observation(brief_species,anchor_species):
    engine,flow,h,_,_,claim,review=observation_case()
    engine.run.brief.species=brief_species
    engine.state.anchor.species=anchor_species
    adjudicate(engine,flow,h,review)
    assert claim.state != "supported"
    assert assessment_cards(engine.run)[0]["observation_support"] == []


@pytest.mark.parametrize("object_name", ["Hydra vulgaris", "Schmidtea mediterranea", "unlisted platform ZX-47"])
def test_unrecognized_experimental_objects_do_not_borrow_a_separate_human_enrollment_sentence(object_name):
    engine,flow,h,s,_,claim,review=observation_case()
    s.text="Methods: 31 human patients were enrolled. This experiment used " + object_name + ". " + QUOTE
    flow.ingest([s])
    adjudicate(engine,flow,h,review)
    assert claim.state != "supported"
    assert assessment_cards(engine.run)[0]["observation_support"] == []


@pytest.mark.parametrize("denial", ["No human patients were enrolled", "Human patients were not enrolled", "No human subjects participated"])
def test_conflicting_affirmative_and_denied_current_participation_abstains(denial):
    engine,flow,h,s,_,claim,review=observation_case()
    s.text="Methods: 31 human patients were enrolled. " + denial + ". " + QUOTE
    flow.ingest([s])
    adjudicate(engine,flow,h,review)
    assert claim.state != "supported"
    assert assessment_cards(engine.run)[0]["observation_support"] == []


@pytest.mark.parametrize("background", [
    "Background: Clinical studies inform human disease research. Thirty-one patients were enrolled. Results: ",
    "SYNTHETIC human disease rationale. Thirty-one patients were enrolled. Results: ",
])
def test_background_section_patient_numbers_do_not_become_current_study_population(background):
    engine,flow,h,s,_,claim,review=observation_case()
    s.text=background+QUOTE
    flow.ingest([s])
    adjudicate(engine,flow,h,review)
    assert claim.state != "supported"
    assert assessment_cards(engine.run)[0]["observation_support"] == []


@pytest.mark.parametrize("species,subjects", [("Mus musculus","mice"),("Rattus norvegicus","rats")])
def test_explicit_matching_nonhuman_study_remains_only_a_nonhuman_source_report(species,subjects):
    engine,flow,h,s,e,claim,review=observation_case()
    engine.run.brief.species=engine.state.anchor.species=species
    e.study_system=review.evidence_relations[0].study_system=species
    s.text=f"Methods: Twelve {subjects} were assigned to IC or placebo. " + QUOTE
    flow.ingest([s])
    adjudicate(engine,flow,h,review)
    assert claim.state == "supported"
    assert flow.main_review_state(h) == "source_observation_supported"
    assert not flow.main_reviewed(h)
    assert assessment_cards(engine.run)[0]["scientific_status"] == "needs_evidence"

