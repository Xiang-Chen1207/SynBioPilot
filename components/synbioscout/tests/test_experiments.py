import json
from pathlib import Path

import pandas as pd
import pytest

from scientist.experiments import analyze_experiment
from scientist.models import ExperimentSpec


def spec(kind="concentration", **kwargs):
    return ExperimentSpec(
        name="SYNTHETIC illustrative assay",
        kind=kind,
        endpoint="recorded endpoint",
        unit="mg/L",
        qc_passed=True,
        condition="illustrative culture condition",
        **kwargs,
    )


def run(tmp_path, csv_text, experiment_spec):
    path = tmp_path / "recorded.csv"
    path.write_text(csv_text)
    return analyze_experiment(path, experiment_spec, tmp_path / "analysis")


def test_technical_replicates_do_not_inflate_biological_n(tmp_path):
    result = run(
        tmp_path,
        "group,biological_replicate,technical_replicate,value,unit\n"
        "control,c1,1,1,mg/L\ncontrol,c1,2,1,mg/L\n"
        "control,c2,1,3,mg/L\ncontrol,c2,2,3,mg/L\n"
        "sample,s1,1,3,mg/L\nsample,s1,2,5,mg/L\n"
        "sample,s2,1,6,mg/L\nsample,s2,2,6,mg/L\n",
        spec(control_group="control"),
    )
    sample = next(group for group in result["groups"] if group["group"] == "sample")
    assert sample["n_biological"] == 2
    assert sample["mean"] == 5
    assert sample["sd"] == pytest.approx(2**0.5)
    assert result["comparisons"][0]["n_biological"] == 2
    assert result["comparisons"][0]["mean_difference"] == 3
    cleaned = pd.read_csv(result["artifacts"]["cleaned"])
    assert len(cleaned) == 4
    assert cleaned["n_technical"].tolist() == [2, 2, 2, 2]


@pytest.mark.parametrize("value", ["NaN", "inf", "-inf", "bad", ""])
def test_nonfinite_and_malformed_rows_are_rejected_not_dropped(tmp_path, value):
    result = run(
        tmp_path, f"group,biological_replicate,value\nsample,1,2\nsample,2,{value}\n", spec()
    )
    assert result["status"] == "invalid_data"
    assert result["n_input_rows"] == 2
    assert result["n_aggregated_rows"] is None
    assert any("Non-finite or nonnumeric value" in error for error in result["errors"])
    assert Path(result["artifacts"]["input"]).read_text().endswith(f"sample,2,{value}\n")
    assert result["groups"] == []


def test_units_must_match_and_missing_control_is_invalid(tmp_path):
    result = run(tmp_path, "group,biological_replicate,value,unit\nsample,1,2,ug/L\n", spec())
    assert result["status"] == "invalid_data"
    assert "unit disagrees" in result["errors"][0]
    result = run(
        tmp_path, "group,biological_replicate,value\nsample,1,2\n", spec(control_group="control")
    )
    assert result["status"] == "invalid_data"
    assert any("control group" in error for error in result["errors"])


def test_distinct_conditions_are_not_averaged_as_one_experiment(tmp_path):
    result = run(
        tmp_path,
        "group,biological_replicate,value,condition\n"
        "sample,1,2,illustrative culture condition\nsample,1,3,other condition\n",
        spec(),
    )
    assert result["status"] == "invalid_data"
    assert any("split distinct experimental contexts" in error for error in result["errors"])


def test_one_biological_replicate_has_no_confidence_interval(tmp_path):
    result = run(
        tmp_path,
        "group,biological_replicate,value\ncontrol,1,1\nsample,1,3\nsample,1,5\nsample,1,4\n",
        spec(control_group="control"),
    )
    assert result["groups"][1]["n_biological"] == 1
    assert result["groups"][1]["ci95"] is None
    assert result["comparisons"][0]["ci95"] is None
    assert result["comparisons"][0]["direction"] == "unresolved"


def test_growth_auc_uses_recorded_units_and_technical_means(tmp_path):
    result = run(
        tmp_path,
        "group,biological_replicate,technical_replicate,time,value,time_unit\n"
        "sample,1,a,0,0,min\nsample,1,b,0,2,min\n"
        "sample,1,a,60,2,min\nsample,1,b,60,4,min\n"
        "sample,1,a,120,4,min\nsample,1,b,120,6,min\n",
        spec("growth", time_unit="min"),
    )
    assert result["status"] == "measured"
    assert result["replicate_auc"][0]["auc"] == 360
    assert result["auc_unit"] == "mg/L*min"
    assert result["groups"][0]["auc"]["n_biological"] == 1


def test_growth_unsorted_times_and_duplicate_technical_points_fail(tmp_path):
    result = run(
        tmp_path,
        "group,biological_replicate,time,value\nsample,1,0,1\nsample,1,2,2\nsample,1,1,3\n",
        spec("growth"),
    )
    assert result["status"] == "invalid_data"
    assert any("increasing unique times" in error for error in result["errors"])
    result = run(
        tmp_path,
        "group,biological_replicate,technical_replicate,time,value\n"
        "sample,1,a,0,1\nsample,1,a,0,2\nsample,1,a,1,3\n",
        spec("growth"),
    )
    assert result["status"] == "invalid_data"
    assert any("Duplicate technical" in error for error in result["errors"])


def test_unequal_growth_windows_are_not_contrasted(tmp_path):
    result = run(
        tmp_path,
        "group,biological_replicate,time,value\n"
        "control,1,0,1\ncontrol,1,1,2\nsample,1,0,2\nsample,1,2,3\n",
        spec("growth", control_group="control"),
    )
    assert result["comparisons"] == []
    assert any("different recorded time windows" in warning for warning in result["warnings"])


def test_sparse_dose_response_does_not_report_ic50(tmp_path):
    result = run(
        tmp_path,
        "group,biological_replicate,dose,value\nsample,1,0,0\nsample,1,1,30\nsample,1,10,60\n",
        spec("dose_response"),
    )
    assert result["status"] == "insufficient_data"
    fit = result["fits"]["sample"]
    assert fit["ic50"] is None
    assert fit["half_response_dose"] is None
    assert "three biological replicates" in fit["reason"]


def test_sufficient_dose_response_reports_descriptive_midpoint(tmp_path):
    rows = ["group,biological_replicate,technical_replicate,dose,value"]
    for replicate, offset in [("a", -1), ("b", 0), ("c", 1)]:
        for dose in [0, 0.03, 0.1, 0.3, 1, 3, 10, 30]:
            value = 10 + 90 * dose / (1 + dose) + offset
            for technical in ["1", "2"]:
                rows.append(f"sample,{replicate},{technical},{dose},{value}")
    result = run(tmp_path, "\n".join(rows) + "\n", spec("dose_response"))
    fit = result["fits"]["sample"]
    assert result["status"] == "measured"
    assert fit["status"] == "fitted"
    assert fit["half_response_dose"] == pytest.approx(1, rel=0.01)
    assert fit["ic50"] is None
    assert all(point["n_biological"] == 3 for point in result["groups"][0]["points"])


def test_zero_without_detection_limit_or_control_is_unclassified(tmp_path):
    result = run(tmp_path, "group,biological_replicate,value\nsample,1,0\nsample,2,0\n", spec())
    assert result["status"] == "measured"
    assert result["outcome"] == "unclassified"
    assert result["groups"][0]["outcome"] == "unclassified"


def test_nondetection_requires_limit_and_passing_qc(tmp_path):
    values = "group,biological_replicate,value\nsample,1,0.02\nsample,2,0.03\n"
    result = run(tmp_path, values, spec(detection_limit=0.1))
    assert result["status"] == "non_detection"
    assert result["outcome"] == "unclassified"
    assert result["groups"][0]["measurement_status"] == "non_detection"
    failed_spec = spec(detection_limit=0.1).model_copy(update={"qc_passed": False})
    result = run(tmp_path, values, failed_spec)
    assert result["status"] == "qc_failed"
    assert result["outcome"] == "unclassified"
    assert result["groups"][0]["outcome"] == "unclassified"


def test_above_limit_technical_measurement_is_not_hidden_by_average(tmp_path):
    result = run(
        tmp_path,
        "group,biological_replicate,technical_replicate,value\nsample,1,a,0.15\nsample,1,b,0.01\n",
        spec(detection_limit=0.1),
    )
    assert result["groups"][0]["mean"] < 0.1
    assert result["status"] == "measured"
    assert result["groups"][0]["measurement_status"] == "measured"


def test_finite_input_with_numerical_overflow_returns_diagnostics(tmp_path):
    result = run(
        tmp_path, "group,biological_replicate,value\nsample,1,1e308\nsample,2,-1e308\n", spec()
    )
    assert result["status"] == "invalid_data"
    assert any("Numerical analysis failed" in error for error in result["errors"])
    assert result["n_input_rows"] == 2


def test_failed_control_qc_prevents_direction_interpretation(tmp_path):
    values = "group,biological_replicate,value\ncontrol,1,1\ncontrol,2,1\n"
    values += "sample,1,10\nsample,2,10\n"
    failed_spec = spec("activity", control_group="control").model_copy(update={"qc_passed": False})
    result = run(tmp_path, values, failed_spec)
    assert result["status"] == "qc_failed"
    assert result["comparisons"][0]["direction"] == "unclassified_qc_failed"


def test_artifacts_preserve_reproducible_input_and_json(tmp_path):
    result = run(tmp_path, "group,biological_replicate,value\nsample,1,1\nsample,2,2\n", spec())
    for path in result["artifacts"].values():
        assert Path(path).is_file()
    assert json.loads(Path(result["artifacts"]["summary"]).read_text()) == result
    stored_spec = ExperimentSpec.model_validate_json(Path(result["artifacts"]["spec"]).read_text())
    reproduced = analyze_experiment(
        Path(result["artifacts"]["input"]), stored_spec, tmp_path / "again"
    )
    assert reproduced["groups"] == result["groups"]
    assert len(result["provenance"]["input_sha256"]) == 64
    assert "SYNTHETIC" in Path(result["artifacts"]["report"]).read_text()

