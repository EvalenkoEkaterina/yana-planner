"""Bounded weather experiment worker; input data remain offline."""
import json
import sys
from pathlib import Path

from .domain import proposals
from .planning import compare
from .weather import assess_weather
from .weather_rules import event_from_rule


def calculate(threshold):
    root = Path(__file__).resolve().parents[2]
    cfg = json.loads((root / 'examples/r03.json').read_text())
    rule = json.loads((root / 'examples/weather_rules/yana_wind.json').read_text())
    if threshold not in (8, 10, 12):
        raise ValueError('Допустимые пороги: 8, 10, 12 м/с')
    rule['threshold'] = threshold
    cfg.update(t0='2026-08-10T08:00:00+10:00', timezone='Asia/Vladivostok',
               route_id=rule['route_id'])
    records = [json.loads((root / f'examples/weather/{name}.json').read_text())
               for name in rule['point_ids']]
    event = event_from_rule(records, rule, route_id=cfg['route_id'], t0=cfg['t0'],
                            horizon_h=492, announced_at_h=60)
    initial = compare(cfg, 328, time_limit=2)
    runs = {method: assess_weather(cfg, proposals(initial[key]['trips']), event,
                                  328, method=method, time_limit=2)
            for key, method in [('baseline', 'ready'), ('planner', 'milp')]}
    return dict(threshold_ms=threshold, event=event, runs=runs,
                initial=initial, scenario=cfg, deadline_h=328)


if __name__ == '__main__':
    json.dump(calculate(json.load(sys.stdin)['threshold']), sys.stdout,
              ensure_ascii=False, allow_nan=False)
