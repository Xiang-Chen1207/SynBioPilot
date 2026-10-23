"""Reproducible, descriptive analysis of recorded laboratory measurements.

The experimental unit is the biological replicate. Technical measurements are
averaged before summaries or confidence intervals are calculated. This module
does not infer molecular identity, efficacy, or a mechanism from assay values.
"""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import shutil
import warnings
from collections import Counter
from itertools import cycle
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy
from scipy.integrate import trapezoid
from scipy.optimize import OptimizeWarning, curve_fit
from scipy.stats import t

from scientist.models import ExperimentSpec


def _stats(values: Any) -> dict[str, Any]:
    values = np.asarray(values, dtype=float)
    n = len(values)
    mean = float(values.mean())
    sd = float(values.std(ddof=1)) if n > 1 else None
    ci = None
    if n > 1:
        margin = float(t.ppf(0.975, n - 1) * sd / np.sqrt(n))
        ci = [mean - margin, mean + margin]
    return {"n_biological": n, "mean": mean, "sd": sd, "ci95": ci}


def _contrast(group: str, values: Any, control: str, baseline: Any) -> dict[str, Any]:
    a, b = np.asarray(values, dtype=float), np.asarray(baseline, dtype=float)
    difference = float(a.mean() - b.mean())
    ci, df = None, None
    if len(a) > 1 and len(b) > 1:
        va, vb = float(a.var(ddof=1) / len(a)), float(b.var(ddof=1) / len(b))
        se = np.sqrt(va + vb)
        denominator = va * va / (len(a) - 1) + vb * vb / (len(b) - 1)
        if denominator:
            df = (va + vb) ** 2 / denominator
            margin = float(t.ppf(0.975, df) * se)
        else:
            margin = 0.0
        ci = [difference - margin, difference + margin]
    direction = "unresolved"
    if ci is not None:
        if ci[0] > 0:
            direction = "higher"
        elif ci[1] < 0:
            direction = "lower"
    return {
        "group": group,
        "control_group": control,
        "n_biological": len(a),
        "n_control_biological": len(b),
        "mean_difference": difference,
        "ci95": ci,
        "welch_degrees_of_freedom": df,
        "relative_difference": difference / float(b.mean()) if float(b.mean()) != 0 else None,
        "direction": direction,
        "method": "unpaired Welch descriptive confidence interval",
        "interpretation": "Higher/lower reports the recorded endpoint relative to its control.",
    }


def _validate(path: Path, spec: ExperimentSpec) -> tuple[pd.DataFrame, list[str]]:
    errors: list[str] = []
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = csv.reader(handle)
            header = next(rows, [])
            duplicated = [name for name, n in Counter(header).items() if n > 1]
            if duplicated:
                errors.append(f"Duplicate CSV columns: {', '.join(duplicated)}")
            for line, row in enumerate(rows, start=2):
                if len(row) != len(header):
                    errors.append(
                        f"CSV record {line} has {len(row)} fields; expected {len(header)}."
                    )
        frame = pd.read_csv(path, dtype=str, keep_default_na=False, skip_blank_lines=False)
    except (
        OSError,
        UnicodeError,
        csv.Error,
        pd.errors.ParserError,
        pd.errors.EmptyDataError,
    ) as exc:
        return pd.DataFrame(), errors + [f"Cannot read CSV: {exc}"]
    required = {"group", "value", "biological_replicate"}
    if spec.kind == "growth":
        required.add("time")
    if spec.kind == "dose_response":
        required.add("dose")
    missing = sorted(required - set(frame.columns))
    if missing:
        errors.append(f"Missing required columns: {', '.join(missing)}")
    if frame.empty:
        errors.append("CSV contains no measurements.")
    if not spec.endpoint.strip() or not spec.unit.strip():
        errors.append("A nonempty recorded endpoint and measurement unit are required.")
    if spec.kind == "growth" and not spec.time_unit.strip():
        errors.append("Growth analysis requires a recorded time unit.")
    if spec.kind == "dose_response" and not spec.dose_unit.strip():
        errors.append("Dose-response analysis requires a recorded dose unit.")
    for column in ("group", "biological_replicate", "technical_replicate"):
        if column in frame:
            frame[column] = frame[column].str.strip()
            bad = frame[column].eq("")
            if bad.any():
                errors.append(f"Empty {column} at CSV records {_records(bad)}.")
    for column in required & {"value", "time", "dose"}:
        if column in frame:
            numeric = pd.to_numeric(frame[column], errors="coerce")
            bad = ~np.isfinite(numeric.to_numpy(dtype=float))
            if bad.any():
                errors.append(f"Non-finite or nonnumeric {column} at CSV records {_records(bad)}.")
            frame[column] = numeric
    for column, expected in (
        ("unit", spec.unit),
        ("time_unit", spec.time_unit),
        ("dose_unit", spec.dose_unit),
    ):
        relevant = column == "unit" or (column == "time_unit" and spec.kind == "growth")
        relevant |= column == "dose_unit" and spec.kind == "dose_response"
        if column in frame and relevant:
            bad = frame[column].str.strip().ne(expected.strip())
            if bad.any():
                errors.append(
                    f"{column} disagrees with declared '{expected}' at records {_records(bad)}."
                )
    for column in ("condition", "chassis_name", "endpoint", "candidate_id"):
        if column in frame:
            expected = getattr(spec, column).strip()
            bad = frame[column].str.strip().ne(expected)
            if bad.any():
                errors.append(
                    f"{column} disagrees with this experiment's declared context at records "
                    f"{_records(bad)}; split distinct experimental contexts before analysis."
                )
    if spec.kind == "dose_response" and "dose" in frame and frame["dose"].lt(0).any():
        errors.append("Doses must be nonnegative; no dose rows were discarded.")
    if missing or errors:
        return frame, errors
    keys = ["group", "biological_replicate"]
    if spec.kind in {"growth", "dose_response"}:
        keys.append("time" if spec.kind == "growth" else "dose")
    if "technical_replicate" in frame:
        duplicate = frame.duplicated(keys + ["technical_replicate"], keep=False)
        if duplicate.any():
            errors.append(
                f"Duplicate technical-replicate records at CSV records {_records(duplicate)}."
            )
    if spec.kind == "growth":
        for (group, replicate), curve in frame.groupby(keys[:2], sort=False):
            times = curve["time"].drop_duplicates().to_numpy(dtype=float)
            if len(times) < 2 or np.any(np.diff(times) <= 0):
                errors.append(
                    f"Growth curve {group}/{replicate} requires at least two increasing unique times "
                    "in recorded order after technical aggregation."
                )
    if spec.control_group and spec.control_group not in set(frame["group"]):
        errors.append(f"Declared control group '{spec.control_group}' is absent.")
    return frame, errors


def _records(mask: Any) -> str:
    indexes = np.flatnonzero(np.asarray(mask)) + 2
    return ", ".join(map(str, indexes[:10])) + (", ..." if len(indexes) > 10 else "")


def _aggregate(frame: pd.DataFrame, kind: str) -> pd.DataFrame:
    keys = ["group", "biological_replicate"]
    if kind == "growth":
        keys.append("time")
    elif kind == "dose_response":
        keys.append("dose")
    return frame.groupby(keys, sort=False, as_index=False).agg(
        value=("value", "mean"),
        n_technical=("value", "size"),
        technical_min=("value", "min"),
        technical_max=("value", "max"),
    )


def _four_parameter(
    dose: Any, response_zero: float, response_infinity: float, midpoint: float, hill: float
) -> Any:
    dose = np.asarray(dose, dtype=float)
    fraction = np.zeros_like(dose)
    positive = dose > 0
    fraction[positive] = 1 / (1 + (midpoint / dose[positive]) ** hill)
    return response_zero + (response_infinity - response_zero) * fraction


def _fit_dose(group: pd.DataFrame, endpoint: str) -> dict[str, Any]:
    points = group.groupby("dose", sort=True)["value"].agg(["mean", "count"])
    result: dict[str, Any] = {
        "status": "insufficient_data",
        "model": "4PL",
        "endpoint": endpoint,
        "ic50": None,
        "half_response_dose": None,
        "interpretation": "This value is a fitted assay-response midpoint; compound-specific IC50 requires its own identity and assay definition.",
    }
    if endpoint.strip().lower() in {"", "unknown", "unspecified"}:
        result["reason"] = "A recorded assay endpoint is required to interpret a dose response."
        return result
    if len(points) < 6 or (points["count"] < 3).any() or (points.index > 0).sum() < 5:
        result["reason"] = (
            "4PL requires at least six doses, five positive doses, and three biological replicates per dose."
        )
        return result
    x, y = points.index.to_numpy(float), points["mean"].to_numpy(float)
    span = float(np.ptp(y))
    if span <= 1e-10 * max(1.0, float(np.abs(y).max())):
        result["reason"] = "The recorded response has insufficient dynamic range for a midpoint."
        return result
    positive = x[x > 0]
    initial = [float(y[0]), float(y[-1]), float(np.exp(np.mean(np.log(positive)))), 1.0]
    lower = [float(y.min() - span), float(y.min() - span), positive.min() / 100, 0.1]
    upper = [float(y.max() + span), float(y.max() + span), positive.max() * 100, 10.0]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", OptimizeWarning)
            parameters, _ = curve_fit(
                _four_parameter, x, y, p0=initial, bounds=(lower, upper), maxfev=30000
            )
    except (ValueError, RuntimeError, OptimizeWarning, FloatingPointError) as exc:
        result["reason"] = f"A stable 4PL fit was not obtained: {exc}"
        return result
    zero, infinity, midpoint, hill = map(float, parameters)
    fractional = np.zeros_like(x)
    fractional[x > 0] = 1 / (1 + (midpoint / x[x > 0]) ** hill)
    if (
        not positive.min() <= midpoint <= positive.max()
        or fractional.min() > 0.2
        or fractional.max() < 0.8
    ):
        result["reason"] = (
            "Recorded doses do not bracket the fitted midpoint and both response plateaus sufficiently."
        )
        return result
    predicted = _four_parameter(x, *parameters)
    residual = float(np.sum((y - predicted) ** 2))
    total = float(np.sum((y - y.mean()) ** 2))
    result.update(
        {
            "status": "fitted",
            "half_response_dose": midpoint,
            "parameters": {"response_zero": zero, "response_infinity": infinity, "hill": hill},
            "r_squared_descriptive": 1 - residual / total,
            "fit_basis": "Equal-weight dose means after biological-replicate aggregation",
            "uncertainty": "No inferential fit interval: repeated doses may share biological replicates.",
        }
    )
    return result


def _analyse(cleaned: pd.DataFrame, spec: ExperimentSpec) -> dict[str, Any]:
    result: dict[str, Any] = {"groups": [], "comparisons": [], "fits": {}, "warnings": []}
    if spec.kind == "growth":
        auc_rows = []
        for (group, replicate), curve in cleaned.groupby(
            ["group", "biological_replicate"], sort=False
        ):
            auc_rows.append(
                {
                    "group": group,
                    "biological_replicate": replicate,
                    "auc": float(trapezoid(curve["value"], curve["time"])),
                    "time_start": float(curve["time"].iloc[0]),
                    "time_end": float(curve["time"].iloc[-1]),
                    "n_time_points": len(curve),
                }
            )
        auc = pd.DataFrame(auc_rows)
        result["replicate_auc"] = auc_rows
        result["auc_unit"] = f"{spec.unit}*{spec.time_unit}"
        intervals = auc[["time_start", "time_end"]].drop_duplicates()
        values_table = auc.rename(columns={"auc": "value"})
        if len(intervals) != 1:
            result["warnings"].append(
                "AUCs cover different recorded time windows; control contrasts were not computed."
            )
        for group, rows in cleaned.groupby("group", sort=False):
            point_stats = []
            for time, point in rows.groupby("time", sort=True):
                point_stats.append({"time": float(time), **_stats(point["value"])})
            group_auc = auc.loc[auc["group"].eq(group)]
            comparable = len(group_auc[["time_start", "time_end"]].drop_duplicates()) == 1
            result["groups"].append(
                {
                    "group": group,
                    "auc": _stats(group_auc["auc"]) if comparable else None,
                    "points": point_stats,
                    "outcome": "unclassified",
                }
            )
        allow_comparison = len(intervals) == 1
    elif spec.kind == "dose_response":
        values_table = cleaned
        allow_comparison = False
        for group, rows in cleaned.groupby("group", sort=False):
            points = [
                {"dose": float(dose), **_stats(point["value"])}
                for dose, point in rows.groupby("dose", sort=True)
            ]
            result["groups"].append({"group": group, "points": points, "outcome": "unclassified"})
            result["fits"][group] = _fit_dose(rows, spec.endpoint)
    else:
        values_table, allow_comparison = cleaned, True
        for group, rows in cleaned.groupby("group", sort=False):
            outcome = "unclassified"
            measurement_status = "measured" if spec.qc_passed else "qc_failed"
            if spec.kind == "concentration" and spec.qc_passed and spec.detection_limit is not None:
                measurement_status = (
                    "non_detection"
                    if rows["technical_max"].lt(spec.detection_limit).all()
                    else "measured"
                )
                if spec.control_group:
                    outcome = measurement_status
            result["groups"].append(
                {
                    "group": group,
                    **_stats(rows["value"]),
                    "outcome": outcome,
                    "measurement_status": measurement_status,
                }
            )
    if spec.control_group and allow_comparison:
        control = values_table.loc[values_table["group"].eq(spec.control_group), "value"]
        for group, rows in values_table.groupby("group", sort=False):
            if group != spec.control_group:
                comparison = _contrast(group, rows["value"], spec.control_group, control)
                comparison["unit"] = result.get("auc_unit", spec.unit)
                if not spec.qc_passed:
                    comparison["direction"] = "unclassified_qc_failed"
                result["comparisons"].append(comparison)
    if not spec.control_group:
        result["warnings"].append(
            "No control was declared; biological outcome remains unclassified."
        )
    if cleaned["value"].lt(0).any():
        result["warnings"].append(
            "Negative recorded values are preserved; assess baseline correction and assay QC."
        )
    return result


def _plot(
    cleaned: pd.DataFrame,
    spec: ExperimentSpec,
    analysis: dict[str, Any],
    path: Path,
    errors: list[str],
) -> None:
    fig, ax = plt.subplots(figsize=(8, 5), layout="constrained")
    if errors:
        ax.text(
            0.5,
            0.5,
            "Invalid input\nNo quantitative analysis",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
        ax.set_axis_off()
    elif spec.kind in {"growth", "dose_response"}:
        dimension = "time" if spec.kind == "growth" else "dose"
        for color, (group, rows) in zip(
            cycle(plt.cm.tab10.colors), cleaned.groupby("group", sort=False)
        ):
            for _, curve in rows.groupby("biological_replicate", sort=False):
                curve = curve.sort_values(dimension)
                ax.plot(curve[dimension], curve["value"], ".-", color=color, alpha=0.3)
            points = rows.groupby(dimension)["value"].agg(["mean", "std", "count"])
            ax.plot(points.index, points["mean"], "o-", color=color, label=str(group))
            fit = analysis.get("fits", {}).get(group, {})
            if fit.get("status") == "fitted":
                positive = points.index[points.index > 0]
                doses = np.geomspace(positive.min(), positive.max(), 150)
                parameters = fit["parameters"]
                ax.plot(
                    doses,
                    _four_parameter(
                        doses,
                        parameters["response_zero"],
                        parameters["response_infinity"],
                        fit["half_response_dose"],
                        parameters["hill"],
                    ),
                    "--",
                    color=color,
                )
        if dimension == "dose" and cleaned["dose"].gt(0).any():
            positive_min = float(cleaned.loc[cleaned["dose"].gt(0), "dose"].min())
            ax.set_xscale("symlog", linthresh=positive_min / 10)
        ax.set_xlabel(
            f"{dimension.capitalize()} ({spec.time_unit if dimension == 'time' else spec.dose_unit})"
        )
        ax.legend()
    else:
        for position, group in enumerate(analysis["groups"]):
            values = cleaned.loc[cleaned["group"].eq(group["group"]), "value"]
            offsets = np.linspace(-0.08, 0.08, len(values)) if len(values) > 1 else [0]
            ax.scatter(position + np.asarray(offsets), values, alpha=0.75)
            ci = group["ci95"]
            error = [[group["mean"] - ci[0]], [ci[1] - group["mean"]]] if ci else None
            ax.errorbar(position, group["mean"], yerr=error, fmt="k_", capsize=5)
        ax.set_xticks(range(len(analysis["groups"])), [g["group"] for g in analysis["groups"]])
        if spec.detection_limit is not None and spec.kind == "concentration":
            ax.axhline(
                spec.detection_limit, color="gray", linestyle="--", label="Recorded detection limit"
            )
            ax.legend()
    if not errors:
        ax.set_ylabel(f"{spec.endpoint} ({spec.unit})")
        ax.grid(alpha=0.15)
    ax.set_title(spec.name + (" — QC failed" if not spec.qc_passed else ""))
    fig.savefig(path, dpi=170)
    plt.close(fig)


def _report(summary: dict[str, Any]) -> str:
    spec = summary["spec"]
    lines = [
        f"# {spec['name']}",
        "",
        f"Status: **{summary['status']}**; outcome: {summary['outcome']}.",
        "",
        f"Recorded endpoint: {spec['endpoint']} ({spec['unit']}).",
        f"Condition: {spec['condition'] or 'not supplied'}; chassis: {spec['chassis_name'] or 'not supplied'}.",
        f"Assay QC passed: {spec['qc_passed']}. {spec['qc_notes']}",
        "",
        (
            "Technical measurements were averaged within each biological replicate and time/dose point. "
            "Confidence intervals use biological replicates; they are descriptive and assume independent "
            "biological sampling. Paired-design status requires experimental-design metadata in addition to replicate identifiers."
        ),
        "",
    ]
    if summary["errors"]:
        lines += ["Input validation failed. No rows were silently excluded.", ""]
        lines += [f"- {error}" for error in summary["errors"]]
    for group in summary.get("groups", []):
        lines += ["", f"## {group['group']}", ""]
        if "points" in group:
            dimension = "time" if spec["kind"] == "growth" else "dose"
            lines += [
                f"| {dimension} | Biological n | Mean | SD | 95% CI |",
                "| --- | ---: | ---: | ---: | --- |",
            ]
            for point in group["points"]:
                lines.append(
                    f"| {point[dimension]:g} | {point['n_biological']} | {point['mean']:.6g} | "
                    f"{_display(point['sd'])} | {_display(point['ci95'])} |"
                )
        else:
            lines.append(
                f"Biological n={group['n_biological']}; mean={group['mean']:.6g}; "
                f"SD={_display(group['sd'])}; 95% CI={_display(group['ci95'])}; "
                f"outcome={group['outcome']}."
            )
        if group.get("auc") is not None:
            lines.append(
                f"AUC mean={group['auc']['mean']:.6g} {summary['auc_unit']}; "
                f"95% CI={_display(group['auc']['ci95'])}."
            )
        elif "auc" in group:
            lines.append(
                "AUC group summary withheld because biological replicates cover different time windows."
            )
        fit = summary.get("fits", {}).get(group["group"])
        if fit:
            lines.append(f"4PL: {fit['status']}. {fit.get('reason', '')}")
            if fit["status"] == "fitted":
                lines.append(
                    f"Descriptive half-response dose={fit['half_response_dose']:.6g} "
                    f"{spec['dose_unit']}; no IC50 is asserted. {fit['uncertainty']}"
                )
    for contrast in summary.get("comparisons", []):
        lines += [
            "",
            (
                f"{contrast['group']} minus {contrast['control_group']}: "
                f"{contrast['mean_difference']:.6g} {contrast['unit']}; "
                f"95% CI={_display(contrast['ci95'])}; direction={contrast['direction']}."
            ),
        ]
    lines += ["", "## Interpretation limits", ""]
    lines += [f"- {item}" for item in summary["limitations"] + summary["warnings"]]
    lines += [
        "",
        (
            "Reproduce from [input.csv](input.csv) and [spec.json](spec.json). "
            "[Cleaned biological measurements](cleaned.csv), [summary](summary.json), "
            "and [plot](analysis.png) accompany this report."
        ),
        "",
    ]
    return "\n".join(lines)


def _display(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, list):
        return "[" + ", ".join(f"{number:.6g}" for number in value) + "]"
    return f"{value:.6g}"


def _finite_result(value: Any) -> bool:
    if isinstance(value, dict):
        return all(_finite_result(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite_result(item) for item in value)
    if isinstance(value, float):
        return bool(np.isfinite(value))
    return True


def analyze_experiment(csv_path: Path, spec: ExperimentSpec, output: Path) -> dict[str, Any]:
    """Analyze a CSV and write the raw input, specification, diagnostics and outputs.

    Required columns: group, value, biological_replicate, plus time for growth or
    dose for dose_response. Optional technical_replicate IDs must be unique within
    each measurement point. Optional unit/time_unit/dose_unit columns must match
    the supplied specification exactly (whitespace is ignored; no unit conversion).
    Validation failures are returned as ``invalid_data`` instead of deleting rows.
    """
    csv_path, output = Path(csv_path), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    spec_data = spec.model_dump(mode="json")
    spec_text = json.dumps(spec_data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    (output / "spec.json").write_text(spec_text, encoding="utf-8")
    input_hash = None
    if csv_path.is_file():
        input_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest()
        if csv_path.resolve() != (output / "input.csv").resolve():
            shutil.copyfile(csv_path, output / "input.csv")
    raw, errors = _validate(csv_path, spec)
    cleaned = raw
    analysis: dict[str, Any] = {"groups": [], "comparisons": [], "fits": {}, "warnings": []}
    if not errors:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", RuntimeWarning)
                cleaned = _aggregate(raw, spec.kind)
                analysis = _analyse(cleaned, spec)
            if not _finite_result(analysis):
                raise FloatingPointError("Non-finite analysis output")
        except (OverflowError, FloatingPointError, RuntimeWarning) as exc:
            errors.append(f"Numerical analysis failed: {exc}. No input rows were discarded.")
            analysis = {"groups": [], "comparisons": [], "fits": {}, "warnings": []}
    cleaned.to_csv(output / "cleaned.csv", index=False)
    status, outcome = "measured", "unclassified"
    if errors:
        status = "invalid_data"
    elif not spec.qc_passed:
        status = "qc_failed"
    elif spec.kind == "dose_response" and not any(
        fit["status"] == "fitted" for fit in analysis["fits"].values()
    ):
        status = "insufficient_data"
    elif spec.kind == "concentration" and spec.detection_limit is not None:
        tested = [group for group in analysis["groups"] if group["group"] != spec.control_group]
        if tested and all(group["measurement_status"] == "non_detection" for group in tested):
            status = "non_detection"
            if spec.control_group:
                outcome = "non_detection"
        elif tested and spec.control_group:
            outcome = "measured"
    if not spec.qc_passed and not errors:
        analysis["warnings"].append(
            "Assay QC status: failed. Measurements are retained; biological interpretation awaits passing QC."
        )
    if "SYNTHETIC" in spec.name.upper():
        analysis["warnings"].append(
            "SYNTHETIC illustrative data; these measurements are not real wet-lab results."
        )
    summary: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "outcome": outcome,
        "spec": spec_data,
        "condition_scope": {
            "condition": spec.condition,
            "chassis_name": spec.chassis_name,
            "candidate_id": spec.candidate_id,
            "endpoint": spec.endpoint,
        },
        "n_input_rows": len(raw),
        "n_aggregated_rows": len(cleaned) if not errors else None,
        "errors": errors,
        **analysis,
        "limitations": [
            "Results apply only to the recorded chassis, condition and endpoint.",
            "Control contrasts report endpoint changes under the recorded assay conditions.",
            "Measurement endpoints, chemical-identity confirmation and mechanism evidence are linked as separate records.",
            "Control availability and resolved differences are reported as separate analysis states.",
            "Values below the supplied detection limit are reported as below detection, with the limit retained.",
            "Non-detection requires every recorded technical value below the supplied detection limit and passing QC.",
        ],
        "provenance": {
            "input_sha256": input_hash,
            "spec_sha256": hashlib.sha256(spec_text.encode()).hexdigest(),
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "matplotlib": matplotlib.__version__,
            "reproduce": "scientist.experiments.analyze_experiment(Path('input.csv'), "
            "ExperimentSpec.model_validate_json(Path('spec.json').read_text()), Path('reproduced'))",
        },
        "artifacts": {
            name: str((output / filename).resolve())
            for name, filename in {
                "input": "input.csv",
                "spec": "spec.json",
                "cleaned": "cleaned.csv",
                "summary": "summary.json",
                "plot": "analysis.png",
                "report": "report.md",
            }.items()
        },
    }
    _plot(cleaned, spec, analysis, output / "analysis.png", errors)
    (output / "report.md").write_text(_report(summary), encoding="utf-8")
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    return summary

