"""Download auditable fixed-lead forecasts and ERA5 without extra dependencies."""

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]


def main():
    config = json.loads((ROOT / "examples/forecast/experiment.json").read_text())
    out = ROOT / "outputs/forecast/raw"
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for year in [config["train_year"], config["validation_year"], config["test_year"]]:
        for point in config["points"]:
            for kind in ("forecast", "reference"):
                suffix = "_previous_day1" if kind == "forecast" else ""
                variables = config["variables"] if kind == "forecast" else config["targets"]
                base = (
                    "https://previous-runs-api.open-meteo.com/v1/forecast"
                    if kind == "forecast"
                    else "https://archive-api.open-meteo.com/v1/archive"
                )
                params = dict(
                    latitude=point["latitude"],
                    longitude=point["longitude"],
                    start_date=f"{year}-06-01",
                    end_date=f"{year}-09-30",
                    hourly=",".join(v + suffix for v in variables),
                    wind_speed_unit="ms",
                    timezone="UTC",
                    models=config["forecast_model"]
                    if kind == "forecast"
                    else config["reference_model"],
                )
                url = base + "?" + urlencode(params)
                path = out / f"{point['id']}_{year}_{kind}.json"
                if path.exists():
                    record = json.loads(path.read_text())
                    if record["url"] != url:
                        raise ValueError("Cached request differs: " + str(path))
                else:
                    raw = subprocess.check_output(
                        [
                            "curl",
                            "--fail",
                            "--silent",
                            "--show-error",
                            "--retry",
                            "2",
                            "--retry-delay",
                            "2",
                            "--max-time",
                            "50",
                            url,
                        ]
                    )
                    response = json.loads(raw)
                    if response.get("error") or "hourly" not in response:
                        raise ValueError(response)
                    record = dict(
                        url=url,
                        retrieved_at=datetime.now(timezone.utc).isoformat(),
                        raw_sha256=hashlib.sha256(raw).hexdigest(),
                        point=point,
                        year=year,
                        kind=kind,
                        response=response,
                        attribution="Open-Meteo; ECMWF IFS / ERA5; CC BY 4.0",
                    )
                    path.write_text(
                        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                    )
                hourly = record["response"]["hourly"]
                count = len(hourly["time"])
                missing = {
                    v: sum(x is None for x in values) for v, values in hourly.items() if v != "time"
                }
                manifest.append(
                    dict(
                        file=path.name,
                        url=url,
                        point=point["id"],
                        year=year,
                        kind=kind,
                        rows=count,
                        missing=missing,
                        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    )
                )
                print(point["id"], year, kind, count, missing, flush=True)
                (out.parent / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
                )


if __name__ == "__main__":
    main()
