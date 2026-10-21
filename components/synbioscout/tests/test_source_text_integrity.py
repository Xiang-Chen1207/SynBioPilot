"""SYNTHETIC text-integrity regressions; no research/provider calls."""
from types import SimpleNamespace

import pytest

from scientist.models import SourceRecord
from scientist.sources import BiomedicalSources, clean_text


@pytest.mark.parametrize("inequality", ["< .001", "<0.05", "> 0.69", "&lt; .001", "&gt; 0.69"])
def test_actual_tags_are_removed_while_statistical_inequality_and_following_result_survive(inequality):
    raw = ("<h4>SYNTHETIC Results</h4>Endpoint (p " + inequality + "; d = .96). "
           "No significant difference in secondary outcome.<h4>Conclusions</h4>Mixed findings.")
    expected = inequality.replace("&lt;", "<").replace("&gt;", ">")
    assert clean_text(raw) == ("SYNTHETIC Results Endpoint (p " + expected + "; d = .96). "
        "No significant difference in secondary outcome. Conclusions Mixed findings.")


def test_biochemical_signal_does_not_erase_intervening_null_secondary_endpoints():
    raw = ("<h4>SYNTHETIC Results</h4>Biomarker higher (p < 0.05). "
           "Other biomarker trend (p = 0.09). No significant difference in ratio or DNA damage (ps > 0.16). "
           "<h4>Conclusions</h4>No clinical impact.")
    result = clean_text(raw)
    assert "(p < 0.05). Other biomarker trend (p = 0.09)." in result
    assert "No significant difference in ratio or DNA damage (ps > 0.16)." in result
    assert result.endswith("Conclusions No clinical impact.")


def test_entities_are_decoded_once_after_markup_recognition_without_reparsing_data():
    raw = '<p>SYNTHETIC A &amp; B&nbsp; &lt;comparison&gt; &amp;lt;literal&amp;gt;<br/>next <i>line</i>.</p>'
    assert clean_text(raw) == "SYNTHETIC A & B <comparison> &lt;literal&gt; next line ."
    assert clean_text(None) == ""


def test_offline_connector_abstract_and_xml_body_preserve_opposing_results():
    raw = "<h4>SYNTHETIC Results</h4>Marker p <0.05; no clinical effect (p >0.9).<h4>Conclusions</h4>Uncertain."
    provider = BiomedicalSources(SimpleNamespace(
        get_json=lambda *a, **k: {"resultList": {"result": [{"pmid": "1", "title": "SYNTHETIC trial", "abstractText": raw}]}},
        get_text=lambda *a, **k: "<article><body><sec><title>SYNTHETIC Results</title><p>Marker p &lt;0.05; no clinical effect (p &gt;0.9).</p></sec></body></article>"))
    abstract = provider.search_literature("SYNTHETIC", limit=1)[0]
    assert "Marker p <0.05; no clinical effect (p >0.9)." in abstract.text
    source = SourceRecord(source_id="synthetic:1", provider="europe_pmc", title="SYNTHETIC trial", url="https://example.invalid", retrieved_at="", pmcid="PMC1", metadata={"is_open_access": "Y"})
    full = provider.full_text(source)
    assert "Marker p <0.05; no clinical effect (p >0.9)." in full.text

