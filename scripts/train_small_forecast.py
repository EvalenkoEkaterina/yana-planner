"""Fit three median corrections using 240 Yana rows; evaluate on 240 later rows."""
import json
from pathlib import Path

import numpy as np

from yana_planner.forecast_model import FEATURE_NAMES, TARGETS, predict

ROOT = Path(__file__).resolve().parents[1]


def main():
    dataset = json.loads((ROOT / 'examples/forecast_small/data.json').read_text())
    rows = dataset['rows']
    train = np.array([r[3:] for r in rows if r[0] == 2024], dtype=float)
    test = np.array([r[3:] for r in rows if r[0] == 2025], dtype=float)
    if train.shape != (240, 10) or test.shape != (240, 10):
        raise ValueError('Expected 240 training and 240 evaluation rows')
    if not np.isfinite(train).all() or not np.isfinite(test).all():
        raise ValueError('Missing data must not be replaced by zero')
    components = {}
    metrics = {}
    for i, target in enumerate(TARGETS):
        offset = float(np.median(train[:, 7+i] - train[:, i]))
        components[target] = dict(kind='median_bias', offset=offset)
        raw, actual = test[:, i], test[:, 7+i]
        corrected = raw + offset
        if target != 'temperature_2m':
            corrected = np.maximum(corrected, 0)
        metrics[target] = dict(raw_mae=float(np.mean(abs(actual-raw))),
                               corrected_mae=float(np.mean(abs(actual-corrected))))
    model = dict(schema_version='1.0', model_id='yana-small-experimental-v1',
                 source_model='ecmwf_ifs025', lead_hours=24, feature_names=FEATURE_NAMES,
                 months=[7], components=components, train_rows=240, evaluation_rows=240,
                 status='experimental_not_operational',
                 scope='Two Yana points, July 1–5 only; generalization unproven',
                 reference='ERA5 reanalysis, not independent observations',
                 evaluation_note='2025 was inspected in earlier research; not a new blind test')
    out = ROOT / 'models/forecast_small'
    out.mkdir(parents=True, exist_ok=True)
    for name, value in [('model.json', model), ('metrics.json', metrics)]:
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    request = json.loads((ROOT / 'examples/forecast_small/input.json').read_text())
    result = predict(model, request)
    (out / 'example_prediction.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(metrics, ensure_ascii=False))


if __name__ == '__main__':
    main()
