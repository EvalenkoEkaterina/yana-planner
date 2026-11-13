"""Download a reproducible ERA5 sample with curl's verified TLS; no new dependencies."""

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

OUT = Path(__file__).resolve().parents[1] / "examples/weather"
POINTS = [("nizhneyansk", 71.4403, 136.13585), ("ust_kuyga", 70.00208, 135.54877)]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, lat, lon in POINTS:
        query = urlencode(
            dict(
                latitude=lat,
                longitude=lon,
                start_date="2026-08-09",
                end_date="2026-08-31",
                hourly="wind_speed_10m",
                wind_speed_unit="ms",
                timezone="UTC",
                models="era5",
            )
        )
        url = "https://archive-api.open-meteo.com/v1/archive?" + query
        raw = subprocess.check_output(
            ["curl", "--fail", "--silent", "--show-error", "--max-time", "60", url]
        )
        response = json.loads(raw)
        if response.get("error"):
            raise ValueError(response)
        record = dict(
            source="Open-Meteo / ECMWF ERA5",
            kind="reanalysis",
            url=url,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            license="CC BY 4.0; attribution Open-Meteo and ECMWF ERA5",
            requested_point=dict(name=name, latitude=lat, longitude=lon),
            coordinate_source="https://geocoding-api.open-meteo.com/v1/search",
            raw_sha256=hashlib.sha256(raw).hexdigest(),
            response=response,
        )
        (OUT / (name + ".json")).write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        print(name, len(response["hourly"]["time"]), "hourly records")


if __name__ == "__main__":
    main()
