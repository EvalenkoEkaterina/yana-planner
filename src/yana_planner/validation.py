"""Independent event reconstruction; never imports solver candidate calculations."""

from __future__ import annotations

from typing import Any

from .domain import ConfirmationRequired, InputError, Scenario, integer, proposals


def check_schedule(
    cfg: dict[str, Any], plan: list[dict[str, Any]], deadline: int
) -> dict[str, Any]:
    try:
        cfg = Scenario.from_dict(cfg).to_dict()
        deadline = integer(deadline, "deadline_h")
        plan = proposals(plan)
    except ConfirmationRequired as e:
        return dict(
            status="requires_confirmation",
            accepted_t=None,
            shortfall_t=None,
            errors=[str(e)],
            real_admissibility="requires_confirmation",
        )
    except InputError as e:
        return dict(
            status="invalid",
            accepted_t=None,
            shortfall_t=None,
            errors=[str(e)],
            real_admissibility="requires_confirmation",
        )
    errors = []
    fleet = {v["id"]: v for v in cfg["vessels"]}

    def intersects(a, b, c, d):
        return max(a, c) < min(b, d)

    trips = []
    for index, p in enumerate(plan):
        if p["vessel_id"] not in fleet:
            errors.append(f"{index}: unknown vessel")
            continue
        v = fleet[p["vessel_id"]]
        s = p["start_h"]
        q = p["quantity_t"]
        le = s + v["load_h"]
        ar = le + v["up_h"]
        re = ar + v["unload_h"]
        rt = re + v["return_h"]
        rd = rt + v["prep_h"]
        if s < v["available_h"]:
            errors.append(f"{index}: unavailable vessel")
        if re > deadline:
            errors.append(f"{index}: receipt after deadline")
        if rt > cfg["return_cutoff_h"]:
            errors.append(f"{index}: return after cutoff")
        limits = cfg["payload_calendar"]
        caps = [
            x["capacity_t"]
            for j, x in enumerate(limits)
            if intersects(
                le,
                ar,
                x["from_h"],
                limits[j + 1]["from_h"] if j + 1 < len(limits) else float("inf"),
            )
        ]
        if not caps or q > min(v["capacity_t"], *caps):
            errors.append(f"{index}: overloaded")
        for a, b in cfg["closures"]:
            if intersects(le, ar, a, b):
                errors.append(f"{index}: loaded movement closed")
            if intersects(re, rt, a, b):
                errors.append(f"{index}: return movement closed")
        trips.append(
            dict(
                trip=index + 1,
                **p,
                load_end_h=le,
                arrival_h=ar,
                receipt_h=re,
                return_h=rt,
                ready_h=rd,
            )
        )
    for i, a in enumerate(trips):
        for b in trips[i + 1 :]:
            if a["vessel_id"] == b["vessel_id"] and intersects(
                a["start_h"], a["ready_h"], b["start_h"], b["ready_h"]
            ):
                errors.append("vessel overlap")
            if intersects(a["start_h"], a["load_end_h"], b["start_h"], b["load_end_h"]):
                errors.append("loading berth overlap")
            if intersects(a["arrival_h"], a["receipt_h"], b["arrival_h"], b["receipt_h"]):
                errors.append("receiving berth overlap")
    if sum(t["quantity_t"] for t in trips) > cfg["target_t"]:
        errors.append("target exceeded")
    events = [(x["h"], 0, "supply", x["t"]) for x in cfg["arrivals"]]
    events += [(x["h"], 2, "withdrawal", x["t"]) for x in cfg["withdrawals"]]
    for t in trips:
        events += [
            (t["start_h"], 3, "load", t["quantity_t"]),
            (t["arrival_h"], 4, "reserve", t["quantity_t"]),
            (t["receipt_h"], 1, "receive", t["quantity_t"]),
        ]
    source = cfg["initial_source_t"]
    supplied = source
    transit = reserved = destination = withdrawn = accepted = 0
    ledger = []
    for h, _, kind, q in sorted(events):
        if kind == "supply":
            source += q
            supplied += q
        elif kind == "load":
            source -= q
            transit += q
        elif kind == "reserve":
            reserved += q
        elif kind == "receive":
            reserved -= q
            transit -= q
            destination += q
            accepted += q
        elif kind == "withdrawal":
            destination -= q
            withdrawn += q
        free = cfg["destination_capacity_t"] - destination - reserved
        if min(source, transit, reserved, destination, free) < 0:
            errors.append(f"{h}: negative stock/capacity")
        if source + transit + destination + withdrawn != supplied:
            errors.append(f"{h}: mass balance")
        ledger.append(
            dict(
                h=h,
                event=kind,
                quantity_t=q,
                source_t=source,
                in_transit_t=transit,
                reserved_t=reserved,
                destination_t=destination,
                free_t=free,
                withdrawn_t=withdrawn,
            )
        )
    valid = not errors
    return dict(
        status="valid_simulation" if valid else "invalid",
        accepted_t=accepted if valid else None,
        shortfall_t=cfg["target_t"] - accepted if valid else None,
        errors=sorted(set(errors)),
        trips=trips,
        ledger=ledger,
        trip_count=len(trips),
        last_receipt_h=max((t["receipt_h"] for t in trips), default=None),
        last_return_h=max((t["return_h"] for t in trips), default=None),
        busy_vessel_h=sum(t["ready_h"] - t["start_h"] for t in trips),
        weighted_mean_receipt_h=sum(t["quantity_t"] * t["receipt_h"] for t in trips) / accepted
        if accepted and valid
        else None,
        real_admissibility="requires_confirmation",
    )


def snapshot(
    cfg: dict[str, Any], plan: list[dict[str, Any]], deadline: int, at_h: int
) -> dict[str, Any]:
    at_h = integer(at_h, "at_h")
    verdict = check_schedule(cfg, plan, deadline)
    if verdict["status"] != "valid_simulation":
        raise InputError("Снимок требует допустимое исходное расписание")
    events = [e for e in verdict["ledger"] if e["h"] <= at_h]
    state = (
        events[-1].copy()
        if events
        else dict(
            source_t=cfg["initial_source_t"],
            in_transit_t=0,
            reserved_t=0,
            destination_t=0,
            free_t=cfg["destination_capacity_t"],
            withdrawn_t=0,
        )
    )
    state["as_of_h"] = at_h
    state["committed_trips"] = [t for t in verdict["trips"] if t["start_h"] <= at_h]
    return state
