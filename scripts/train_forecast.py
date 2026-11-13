"""Predeclared seasonal and basin holdouts; NumPy training, no new dependencies."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from yana_planner.forecast_model import (
    FEATURE_NAMES,
    TARGETS,
    UNITS,
    VARIABLES,
    features,
    fit_ridge,
    predict,
    predict_component,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/forecast"


def metrics(y, p):
    return dict(
        n=len(y),
        mae=float(np.mean(abs(y - p))),
        rmse=float(np.sqrt(np.mean((y - p) ** 2))),
        mean_error=float(np.mean(p - y)),
    )


def adverse(y, target):
    if target == "wind_speed_10m":
        return y >= 8
    if target == "precipitation":
        return y >= 1
    return abs(y) <= 2


def event_metrics(y, p, target):
    cutoff = {"wind_speed_10m": 8, "precipitation": 1, "temperature_2m": 0}[target]
    actual = y <= cutoff if target == "temperature_2m" else y >= cutoff
    predicted = p <= cutoff if target == "temperature_2m" else p >= cutoff
    tp = int(np.sum(actual & predicted))
    fn = int(np.sum(actual & ~predicted))
    fp = int(np.sum(~actual & predicted))
    tn = int(np.sum(~actual & ~predicted))
    return dict(
        scenario_threshold=cutoff,
        threshold_is_navigation_rule=False,
        true_positive=tp,
        false_negative=fn,
        false_positive=fp,
        true_negative=tn,
        recall=tp / (tp + fn) if tp + fn else None,
        precision=tp / (tp + fp) if tp + fp else None,
    )


def block_interval(y, raw, p, times):
    # All points in the same 7-day block travel together in the bootstrap.
    keys = np.array(
        [
            int((datetime.fromisoformat(t) - datetime(2026, 6, 1)).total_seconds() // (7 * 86400))
            for t in times
        ]
    )
    groups = np.unique(keys)
    sums = np.array(
        [
            np.sum(abs(y[keys == g] - raw[keys == g]) - abs(y[keys == g] - p[keys == g]))
            for g in groups
        ]
    )
    counts = np.array([np.sum(keys == g) for g in groups])
    samples = np.random.default_rng(20261113).integers(0, len(groups), (1000, len(groups)))
    gains = sums[samples].sum(axis=1) / counts[samples].sum(axis=1)
    return dict(
        block_days=7,
        blocks=len(groups),
        mae_gain_ci95=np.quantile(gains, [0.025, 0.975]).tolist(),
        interpretation="paired temporal block bootstrap; not independent station validation",
    )


def dataset(config):
    manifest = json.loads((OUT / "manifest.json").read_text())
    if len(manifest) != 48:
        raise ValueError("Expected complete 48-file archive")
    for entry in manifest:
        digest = hashlib.sha256((OUT / "raw" / entry["file"]).read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            raise ValueError("Source checksum changed: " + entry["file"])
    arrays = []
    refs = []
    times = []
    points = []
    basins = []
    years = []
    lat = []
    lon = []
    audit = []
    for year in [config["train_year"], config["validation_year"], config["test_year"]]:
        for point in config["points"]:
            name = f"{point['id']}_{year}"
            f = json.loads((OUT / f"raw/{name}_forecast.json").read_text())["response"]
            r = json.loads((OUT / f"raw/{name}_reference.json").read_text())["response"]
            if f["utc_offset_seconds"] != 0 or r["utc_offset_seconds"] != 0:
                raise ValueError("Expected UTC")
            for v in VARIABLES:
                if f["hourly_units"][v + "_previous_day1"] != UNITS[v]:
                    raise ValueError("Unexpected forecast units")
            for v in TARGETS:
                if r["hourly_units"][v] != UNITS[v]:
                    raise ValueError("Unexpected reference units")
            ft = f["hourly"]["time"]
            rt = r["hourly"]["time"]
            if ft != rt or len(set(ft)) != len(ft) or len(ft) != 2928:
                raise ValueError("Incomplete, duplicate or misaligned time index")
            a = np.column_stack([f["hourly"][v + "_previous_day1"] for v in VARIABLES]).astype(
                float
            )
            y = np.column_stack([r["hourly"][v] for v in TARGETS]).astype(float)
            keep = np.isfinite(a).all(axis=1) & np.isfinite(y).all(axis=1)
            audit.append(
                dict(
                    point=point["id"],
                    year=year,
                    expected=len(a),
                    kept=int(keep.sum()),
                    dropped=int((~keep).sum()),
                    forecast_grid=[f["latitude"], f["longitude"]],
                    reference_grid=[r["latitude"], r["longitude"]],
                )
            )
            n = int(keep.sum())
            arrays.append(a[keep])
            refs.append(y[keep])
            times.extend(np.asarray(ft)[keep].tolist())
            points.extend([point["id"]] * n)
            basins.extend([point["basin"]] * n)
            years.extend([year] * n)
            lat.extend([point["latitude"]] * n)
            lon.extend([point["longitude"]] * n)
    return (
        np.vstack(arrays),
        np.vstack(refs),
        np.array(times),
        np.array(points),
        np.array(basins),
        np.array(years),
        np.array(lat),
        np.array(lon),
        audit,
    )


def main():
    config = json.loads((ROOT / "examples/forecast/experiment.json").read_text())
    a, y, times, points, basins, years, lat, lon, audit = dataset(config)
    x = features(a, times, lat, lon)
    seen = basins != config["held_out_basin"]
    train = (years == config["train_year"]) & seen
    val = (years == config["validation_year"]) & seen
    test = (years == config["test_year"]) & seen
    transfer = (years == config["test_year"]) & ~seen
    assert not (train & val).any() and not (train & test).any() and not (seen & transfer).any()
    components = {}
    selection = {}
    trained_candidates = {}
    for j, target in enumerate(TARGETS):
        raw = a[:, j]
        residual = y[train, j] - raw[train]
        candidates = [dict(kind="raw"), dict(kind="median_bias", offset=float(np.median(residual)))]
        candidates += [fit_ridge(x[train], residual, alpha) for alpha in config["ridge_alphas"]]
        trained_candidates[target] = candidates
        base = metrics(y[val, j], raw[val])
        tail = adverse(y[val, j], target)
        rows = []
        best = candidates[0]
        best_mae = base["mae"]
        for candidate in candidates:
            pred = predict_component(candidate, x[val], raw[val], target)
            score = metrics(y[val, j], pred)
            tail_raw = float(np.mean(abs(y[val, j][tail] - raw[val][tail]))) if tail.any() else None
            tail_mae = float(np.mean(abs(y[val, j][tail] - pred[tail]))) if tail.any() else None
            eligible = (
                score["mae"] <= base["mae"] * 0.99
                and score["rmse"] <= base["rmse"] * 1.05
                and (tail_raw is None or tail_mae <= tail_raw * 1.05)
            )
            rows.append(
                dict(
                    kind=candidate["kind"],
                    alpha=candidate.get("alpha"),
                    **score,
                    adverse_n=int(tail.sum()),
                    adverse_mae=tail_mae,
                    eligible=eligible,
                )
            )
            if eligible and score["mae"] < best_mae:
                best, best_mae = candidate, score["mae"]
        components[target] = best
        selection[target] = dict(
            candidates=rows, chosen=best["kind"], alpha=best.get("alpha"), raw_adverse_mae=tail_raw
        )
    manifest_bytes = (OUT / "manifest.json").read_bytes()
    model = dict(
        schema_version="1.0",
        model_id="northern-weather-calibration-v1",
        source_model=config["forecast_model"],
        lead_hours=24,
        feature_names=FEATURE_NAMES,
        months=config["months"],
        reference="ERA5 reanalysis, not independent station observations",
        components=components,
        automatic_calibration_enabled=any(c["kind"] != "raw" for c in components.values()),
        training_year=2024,
        validation_year=2025,
        held_out_basin=config["held_out_basin"],
        raw_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        intended_use="research only; no navigation permission",
        geographic_scope=config["points"],
    )
    # Freeze coefficients and choices BEFORE any test metrics are computed.
    (OUT / "model.json").write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n")
    (OUT / "trained_candidates.json").write_text(
        json.dumps(
            dict(
                feature_names=FEATURE_NAMES,
                source_model=config["forecast_model"],
                train_year=2024,
                candidates=trained_candidates,
                status="experimental candidates; not approved for automatic correction",
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    (OUT / "selection.json").write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n")
    summaries = {}
    for split, mask in [("test_seen_basins_2026", test), ("test_unseen_indigirka_2026", transfer)]:
        summaries[split] = {}
        for j, target in enumerate(TARGETS):
            raw = a[mask, j]
            actual = y[mask, j]
            pred = predict_component(components[target], x[mask], raw, target)
            base = metrics(actual, raw)
            corrected = metrics(actual, pred)
            by_point = {}
            for point in np.unique(points[mask]):
                q = points[mask] == point
                by_point[point] = dict(
                    raw=metrics(actual[q], raw[q]), model=metrics(actual[q], pred[q])
                )
            summaries[split][target] = dict(
                raw=base,
                model=corrected,
                mae_improvement_pct=100 * (base["mae"] - corrected["mae"]) / base["mae"],
                uncertainty=block_interval(actual, raw, pred, times[mask]),
                raw_events=event_metrics(actual, raw, target),
                model_events=event_metrics(actual, pred, target),
                by_point=by_point,
            )
    report = dict(
        experiment=config,
        audit=audit,
        rows=len(a),
        counts=dict(
            train=int(train.sum()),
            validation=int(val.sum()),
            test_seen=int(test.sum()),
            test_unseen=int(transfer.sum()),
            excluded_heldout_2024_2025=int((~seen & (years != 2026)).sum()),
        ),
        selection=selection,
        test=summaries,
        caveats=[
            "Reference is ERA5, not independent observations; IFS and ERA5 are related ECMWF products.",
            "Adjacent hours and locations are correlated. One test season, eight sites, not all rivers.",
            "No future reanalysis features. Reference is used only as a training/evaluation target.",
            "No navigation safety, visibility, ice, depth or flood prediction.",
            "Precipitation is an hourly accumulation; isolated wet events need separate verification.",
            "Previous Runs API uses fixed lead offsets, not a full archived issuance with all future lead times.",
        ],
    )
    (OUT / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    # Portable example includes only forecasts, no targets or future observations.
    subset = np.flatnonzero(test & (points == "nizhneyansk"))[:24]
    request = dict(
        source_model=config["forecast_model"],
        lead_hours=24,
        timezone="UTC",
        units=UNITS,
        latitude=float(lat[subset[0]]),
        longitude=float(lon[subset[0]]),
        hourly=dict(
            time=times[subset].tolist(),
            **{v: a[subset, i].tolist() for i, v in enumerate(VARIABLES)},
        ),
    )
    (OUT / "example_input.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n"
    )
    (OUT / "example_prediction.json").write_text(
        json.dumps(predict(model, request), ensure_ascii=False, indent=2) + "\n"
    )
    print("Rows / splits:", report["rows"], report["counts"])
    for split, results in summaries.items():
        for target, m in results.items():
            print(
                split,
                target,
                selection[target]["chosen"],
                selection[target]["alpha"],
                "MAE",
                round(m["raw"]["mae"], 4),
                "->",
                round(m["model"]["mae"], 4),
                "gain %",
                round(m["mae_improvement_pct"], 2),
                flush=True,
            )


if __name__ == "__main__":
    main()
