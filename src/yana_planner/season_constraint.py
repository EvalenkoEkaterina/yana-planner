"""Translate a historical calendar boundary to the planner's existing return cutoff."""

from datetime import timedelta

from .domain import InputError
from .navigation import (
    DATE_RULE,
    EVENTS,
    SECTION,
    NavigationError,
    _same_section,
    duration,
    estimate_history,
    moment,
    read_history,
    target_year,
)


def apply_season_constraint(cfg, deadline, spec):
    """Use the unchanged return-cutoff mechanism; preserve allocation objectives."""
    try:
        if not _same_section(spec.get("section", "")):
            raise NavigationError("Граница подготовлена только для участка Усть-Куйга — Нижнеянск")
        year = target_year(spec.get("year", ""))
        start = moment(spec.get("start", ""))
        reserve = duration(spec.get("reserve_h", ""), "Запас до конца сезона")
        event_type = spec.get("event_type", "")
        history = estimate_history(read_history(spec.get("csv_text", "")), year, event_type)
        mode = spec.get("mode", "")
        if mode == "manual":
            boundary = moment(spec.get("manual_boundary", ""))
            if boundary.year != year:
                raise NavigationError("Год границы должен совпадать с годом оценки")
            source = (
                spec.get("manual_source", "").strip() or "Пользовательское значение, не проверено"
            )
        elif mode in {"p20", "median"}:
            boundary = history[mode]
            if boundary is None:
                raise NavigationError(
                    "Нет сопоставимой истории: загрузите CSV или задайте границу вручную"
                )
            source = "Историческая оценка; не гарантия окончания навигации"
        else:
            raise NavigationError("Выберите основание конца сезона")
    except NavigationError as exc:
        raise InputError(str(exc)) from exc
    # Integer-hour planner: round down, never allow a fractional-hour overrun.
    seconds = (boundary - start - reserve).total_seconds()
    season_cutoff = max(0, int(seconds // 3600))
    original_cutoff = cfg["return_cutoff_h"]
    effective = min(original_cutoff, season_cutoff)
    effective_deadline = min(deadline, effective)
    cfg["return_cutoff_h"] = effective
    assessment = {
        "section": SECTION,
        "year": year,
        "event_type": event_type,
        "event_label": EVENTS[event_type],
        "mode": mode,
        "source": source,
        "start": start.isoformat(),
        "boundary": boundary.isoformat(),
        "reserve_h": reserve.total_seconds() / 3600,
        "date_rule": DATE_RULE,
        "n": history["n"],
        "years": history["years"],
        "median": history["median"].isoformat() if history["median"] else None,
        "p20": history["p20"].isoformat() if history["p20"] else None,
        "small_sample": history["small_sample"],
        "excluded": history["excluded"],
        "observations": [
            {
                "year": row.year,
                "date": row.day.isoformat(),
                "source": row.source,
                "event_type": row.event_type,
                "section": row.section,
            }
            for row in history["observations"]
        ],
        "season_return_cutoff_h": season_cutoff,
        "original_return_cutoff_h": original_cutoff,
        "effective_return_cutoff_h": effective,
        "requested_deadline_h": deadline,
        "effective_deadline_h": effective_deadline,
        "season_closed_at_start": seconds <= 0,
        "trip_checks": {},
    }
    cfg["season_assessment"] = assessment
    return effective_deadline


def attach_season_trip_checks(cfg, result):
    """Expose actual returns and latest departures for each computed voyage."""
    assessment = cfg.get("season_assessment")
    if assessment is None:
        return
    start = moment(assessment["start"])
    boundary = moment(assessment["boundary"])
    reserve = timedelta(hours=assessment["reserve_h"])
    groups = (
        {"READY": result["revised_baseline"]["result"], "MILP": result["revised_planner"]["result"]}
        if "initial" in result
        else {"READY": result["baseline"], "MILP": result["planner"]}
    )
    checks = {}
    for method, plan in groups.items():
        checks[method] = []
        for trip in plan.get("trips", []):
            voyage_hours = trip["return_h"] - trip["start_h"]
            returned = start + timedelta(hours=trip["return_h"])
            protected = returned + reserve
            checks[method].append(
                {
                    "vessel_id": trip["vessel_id"],
                    "quantity_t": trip["quantity_t"],
                    "loading_start": (start + timedelta(hours=trip["start_h"])).isoformat(),
                    "return_at": returned.isoformat(),
                    "with_reserve_at": protected.isoformat(),
                    "latest_loading_start": (
                        boundary - reserve - timedelta(hours=voyage_hours)
                    ).isoformat(),
                    "fits": protected <= boundary,
                    "late_hours": max(0, (protected - boundary).total_seconds() / 3600),
                }
            )
    assessment["trip_checks"] = checks
