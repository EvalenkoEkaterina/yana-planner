"""Offline graph-neutral weather contract example."""
import json
from pathlib import Path
from yana_planner.weather_feed import normalize, restrictions

ROOT = Path(__file__).resolve().parents[1]
folder = ROOT / 'examples/weather_feed'
values = normalize(json.loads((folder / 'input.json').read_text()))
rule = json.loads((folder / 'rule.json').read_text())
result = restrictions(values, ['nizhneyansk', 'ust_kuyga'],
    '2025-07-01T00:00:00+00:00', '2025-07-06T00:00:00+00:00', rule)
out = ROOT / 'outputs/weather_feed'
out.mkdir(parents=True, exist_ok=True)
for name, data in [('values.json', values), ('restrictions.json', result)]:
    (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
print(result['status'], 'intervals:', result['closures'])
