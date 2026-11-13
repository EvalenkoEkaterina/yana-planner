"""Enumerate departures satisfying movement, payload and return limits."""

from fractions import Fraction

from yana_planner.domain import InputError, loading_hours


def build_candidates(
    cfg: dict, deadline: int, *, decision_h: int = 0, max_candidates: int = 12000
) -> list[dict]:
    result = []
    calendar = cfg["payload_calendar"]
    for v in cfg["vessels"]:
        rate = Fraction(str(v["load_rate_tph"])) if "load_rate_tph" in v else None
        quantity_limit = min(
            v["capacity_t"],
            cfg["target_t"],
            cfg["initial_source_t"] + sum(a["t"] for a in cfg["arrivals"]),
        )
        for start in range(max(v["available_h"], decision_h), deadline + 1):
            if rate is None:
                durations = [v["load_h"]]
            elif quantity_limit:
                durations = range(
                    v["load_setup_h"] + 1,
                    min(
                        loading_hours(v, quantity_limit),
                        deadline - start - v["up_h"] - v["unload_h"],
                        cfg["return_cutoff_h"] - start - v["up_h"] - v["unload_h"] - v["return_h"],
                    )
                    + 1,
                )
            else:
                durations = []
            for duration in durations:
                le = start + duration
                ar = le + v["up_h"]
                re = ar + v["unload_h"]
                rt = re + v["return_h"]
                rd = rt + v["prep_h"]
                if re > deadline or rt > cfg["return_cutoff_h"]:
                    continue
                if any(
                    max(a, le) < min(b, ar) or max(a, re) < min(b, rt) for a, b in cfg["closures"]
                ):
                    continue
                caps = [
                    p["capacity_t"]
                    for i, p in enumerate(calendar)
                    if max(le, p["from_h"])
                    < min(ar, calendar[i + 1]["from_h"] if i + 1 < len(calendar) else float("inf"))
                ]
                minimum = 1
                cap = min(v["capacity_t"], *caps)
                if rate is not None:
                    transfer = duration - v["load_setup_h"]
                    minimum = ((transfer - 1) * rate.numerator) // rate.denominator + 1
                    cap = min(cap, quantity_limit, (transfer * rate.numerator) // rate.denominator)
                if cap >= minimum:
                    item = dict(
                        vessel_id=v["id"],
                        start_h=start,
                        cap=cap,
                        load_end_h=le,
                        arrival_h=ar,
                        receipt_h=re,
                        return_h=rt,
                        ready_h=rd,
                    )
                    if rate is not None:
                        item["min_t"] = minimum
                    result.append(item)
                    if len(result) > max_candidates:
                        raise InputError(
                            "Слишком много вариантов погрузки. Сократите горизонт или число судов "
                            "для одного расчёта; проверьте единицы скорости (т/ч)."
                        )
    return sorted(result, key=lambda x: (x["start_h"], x["vessel_id"]))
