"""Use saved calibration: python scripts/predict_forecast.py model.json input.json output.json."""

import argparse
import json
from pathlib import Path

from yana_planner.forecast_model import predict


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = predict(json.loads(args.model.read_text()), json.loads(args.input.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
