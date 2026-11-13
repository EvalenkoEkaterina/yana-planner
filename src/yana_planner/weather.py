"""Historical weather replay with explicit scenario rules, separate from the web contract."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import isfinite

from .domain import InputError, InterventionRequired, Scenario, integer, proposals
from .planning import optimize, ready
from .validation import check_schedule, snapshot


def _instant(value):
    try:
        result = datetime.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise InputError("Нужна дата ISO 8601 с часовым поясом") from exc
    if result.utcoffset() is None:
        raise InputError("Дата должна содержать часовой пояс")
    if result.minute or result.second or result.microsecond:
        raise InputError("Начало должно совпадать с целым часом")
    return result.astimezone(timezone.utc)


def _positive(value, label):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
    ):
        raise InputError(f"{label}: нужно положительное конечное число")
    return value


def build_weather_event(records, *, t0, horizon_h, announced_at_h, threshold_ms):
    """Map complete UTC ERA5 hours to future route-wide closures, as a scenario only.

    Each hourly wind value represents [h,h+1) by explicit modelling assumption.
    The maximum across supplied endpoint grid cells is NOT a route-wide measurement.
    Historical data are revealed at a hypothetical announcement, not a real forecast.
    """
    start = _instant(t0)
    horizon = integer(horizon_h, "horizon_h")
    announced = integer(announced_at_h, "announced_at_h")
    threshold = _positive(threshold_ms, "threshold_ms")
    if horizon <= announced or not records:
        raise InputError("Нужны данные и горизонт после объявления")
    maxima = [0.0] * horizon
    sources = []
    for record in records:
        if record.get("kind") != "reanalysis" or not record.get("url"):
            raise InputError("Ожидается реанализ с источником")
        response = record.get("response", {})
        units = response.get("hourly_units", {})
        if response.get("utc_offset_seconds") != 0 or units.get("wind_speed_10m") != "m/s":
            raise InputError("Нужны UTC и скорость ветра в m/s")
        hourly = response.get("hourly", {})
        times, winds = hourly.get("time", []), hourly.get("wind_speed_10m", [])
        if len(times) != len(winds) or len(set(times)) != len(times):
            raise InputError("Несогласованные или повторные часы погоды")
        values = dict(zip(times, winds, strict=True))
        for h in range(horizon):
            stamp = (start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M")
            wind = values.get(stamp)
            if (
                isinstance(wind, bool)
                or not isinstance(wind, (int, float))
                or not isfinite(wind)
                or wind < 0
            ):
                raise InputError(f"Нет корректного ветра для {stamp}; пропуск не равен штилю")
            maxima[h] = max(maxima[h], wind)
        sources.append(
            dict(
                url=record["url"],
                retrieved_at=record.get("retrieved_at"),
                point=record.get("requested_point"),
                kind="reanalysis",
            )
        )
    closures = []
    # A report at h is processed after starts through h, matching the existing engine.
    for h in range(announced + 1, horizon):
        if maxima[h] >= threshold:
            if closures and closures[-1][1] == h:
                closures[-1][1] = h + 1
            else:
                closures.append([h, h + 1])
    return dict(
        type="weather_closure",
        announced_at_h=announced,
        closures=closures,
        evidence=[
            dict(
                from_h=a,
                to_h=b,
                peak_wind_ms=max(maxima[a:b]),
                start_utc=(start + timedelta(hours=a)).isoformat(),
                end_utc=(start + timedelta(hours=b)).isoformat(),
            )
            for a, b in closures
        ],
        hourly_max_wind_ms=maxima,
        t0=start.isoformat(),
        horizon_h=horizon,
        threshold_ms=threshold,
        rule_status="scenario_assumption",
        data_kind="reanalysis",
        forecast_issued_at=None,
        information_policy="historical replay; hypothetical announcement, not archived forecast",
        spatial_assumption="maximum of endpoint grid cells applied to both movement legs",
        sources=sources,
    )


def apply_weather_event(cfg, event, *, at_h):
    """Return a copy; do not reveal future messages or change the core input schema."""
    changed = Scenario.from_dict(cfg).to_dict()
    at = integer(at_h, "at_h")
    if event.get("type") != "weather_closure" or event.get("rule_status") != "scenario_assumption":
        raise InputError("Ожидается явно сценарное погодное ограничение")
    if "rule" in event or "scope" in event or "route_id" in event:
        from .weather_rules import SUPPORTED_SCOPE, validate_rule

        rule = validate_rule(event.get("rule"))
        if event.get("scope") != SUPPORTED_SCOPE or event.get("scope") != rule["scope"]:
            raise InputError("Область действия события не поддержана")
        if event.get("route_id") != rule["route_id"] or cfg.get("route_id") != rule["route_id"]:
            raise InputError("Маршруты правила, события и перевозки должны совпадать")
    announced = integer(event.get("announced_at_h"), "announced_at_h")
    if "t0" in event and _instant(changed.get("t0")) != _instant(event["t0"]):
        raise InputError("T0 погодного события и сценария должны совпадать")
    intervals = event.get("closures")
    if not isinstance(intervals, list):
        raise InputError("closures: нужен список")
    for interval in intervals:
        if not isinstance(interval, list) or len(interval) != 2:
            raise InputError("Нужен интервал [начало, конец)")
        a, b = (integer(x, "closure_h") for x in interval)
        if a <= announced or b <= a:
            raise InputError("Поддержаны только будущие непустые ограничения")
    if at < announced:
        return changed
    merged = []
    for a, b in sorted(deepcopy(changed["closures"]) + deepcopy(intervals)):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    changed["closures"] = merged
    return Scenario.from_dict(changed).to_dict()


def assess_weather(cfg, plan, event, deadline, *, method="ready", time_limit=2):
    """Assess an existing valid plan and replan only if the new closures invalidate it."""
    if method not in ("ready", "milp"):
        raise InputError("method: ready или milp")
    at = integer(event.get("announced_at_h"), "announced_at_h")
    if at >= deadline:
        raise InputError("Объявление должно предшествовать сроку поставки")
    original = check_schedule(cfg, plan, deadline)
    if original["status"] != "valid_simulation":
        raise InputError("Исходное расписание должно быть допустимым")
    state = snapshot(cfg, plan, deadline, at)
    changed = apply_weather_event(cfg, event, at_h=at)
    committed = proposals(state["committed_trips"])
    before = check_schedule(changed, plan, deadline)
    common = dict(
        event=deepcopy(event),
        updated_input=changed,
        committed=committed,
        original=original,
        original_under_weather=before,
        real_admissibility="requires_confirmation",
    )
    if before["status"] == "valid_simulation":
        return dict(**common, action="no_replan_required", result=before, solver=[])
    try:
        candidate = ready(changed, deadline, fixed=committed, decision_h=at + 1)
        logs = []
        if method == "milp":
            candidate, logs = optimize(
                changed,
                deadline,
                candidate,
                fixed=committed,
                decision_h=at + 1,
                time_limit=time_limit,
            )
        result = check_schedule(changed, candidate, deadline)
        return dict(**common, action="replanned", result=result, solver=logs)
    except InterventionRequired as exc:
        return dict(
            **common,
            action="requires_intervention",
            solver=[],
            result=dict(
                status="requires_intervention", accepted_t=None, shortfall_t=None, errors=[str(exc)]
            ),
        )
