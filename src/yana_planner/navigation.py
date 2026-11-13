"""Historical closing-date estimates and a single round-trip deadline check."""

import calendar
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction

LOCAL_TIME = timezone(timedelta(hours=10))
SECTION = "Усть-Куйга — Нижнеянск"
EVENTS = {
    "navigation_aids_removed": "Закрытие судоходной обстановки",
    "traffic_ended": "Фактическое прекращение движения",
}
COLUMNS = ["year", "date", "section", "event_type", "source"]
DATE_RULE = "Дата без времени означает начало указанного дня: 00:00, UTC+10."


class NavigationError(ValueError):
    """An input error that can be displayed without changing user inputs."""


@dataclass(frozen=True)
class Observation:
    year: int
    day: date
    section: str
    event_type: str
    source: str


def target_year(value):
    try:
        year = int(str(value))
    except (TypeError, ValueError) as exc:
        raise NavigationError("Год оценки: целое число от 1900 до 2100") from exc
    if not 1900 <= year <= 2100:
        raise NavigationError("Год оценки: от 1900 до 2100")
    return year


def read_history(text):
    if not text.strip():
        return []
    if len(text.encode("utf-8")) > 1_000_000:
        raise NavigationError("CSV: размер не более 1 МБ")
    first = text.lstrip("\ufeff").splitlines()[0]
    reader = csv.DictReader(
        io.StringIO(text.lstrip("\ufeff")), delimiter=";" if ";" in first else ","
    )
    if reader.fieldnames != COLUMNS:
        raise NavigationError(
            "CSV: нужны столбцы year,date,section,event_type,source именно в таком порядке"
        )
    rows = []
    try:
        for line, row in enumerate(reader, 2):
            if line > 2001:
                raise NavigationError("CSV: не более 2000 наблюдений")
            if None in row or any(value is None or not value.strip() for value in row.values()):
                raise NavigationError(f"CSV, строка {line}: заполните все пять столбцов")
            year = target_year(row["year"].strip())
            day = date.fromisoformat(row["date"].strip())
            if day.isoformat() != row["date"].strip() or day.year != year:
                raise NavigationError(
                    f"CSV, строка {line}: дата YYYY-MM-DD должна соответствовать году"
                )
            if row["event_type"].strip() not in EVENTS:
                raise NavigationError(
                    f"CSV, строка {line}: неизвестный event_type; см. шаблон и пояснение"
                )
            rows.append(
                Observation(
                    year,
                    day,
                    row["section"].strip(),
                    row["event_type"].strip(),
                    row["source"].strip(),
                )
            )
    except (ValueError, csv.Error) as exc:
        if isinstance(exc, NavigationError):
            raise
        raise NavigationError(f"CSV: некорректная дата или структура строки: {exc}") from exc
    return rows


def _same_section(section):
    return section.casefold() in {
        SECTION.casefold(),
        "усть-куйга - нижнеянск",
        "усть-куйга – нижнеянск",
    }


def _quantile(days, fraction, year):
    # Normalize month/day to a leap reference year: leap years must not shift autumn dates.
    numbers = sorted((date(2000, d.month, d.day) - date(2000, 1, 1)).days for d in days)
    position = (len(numbers) - 1) * fraction
    lower = position.numerator // position.denominator
    upper = min(lower + 1, len(numbers) - 1)
    value = numbers[lower] + (numbers[upper] - numbers[lower]) * (position - lower)
    seconds = int(value * 86400)
    reference = datetime(2000, 1, 1) + timedelta(seconds=seconds)
    # A Feb-29 estimate maps to Feb-28 in a non-leap target year, retaining its time.
    day = min(reference.day, calendar.monthrange(year, reference.month)[1])
    return reference.replace(year=year, day=day, tzinfo=LOCAL_TIME)


def estimate_history(rows, year, event_type):
    year = target_year(year)
    if event_type not in EVENTS:
        raise NavigationError("Выберите тип события")
    selected, excluded = {}, {"section": 0, "event": 0, "year": 0, "duplicates": 0}
    observations = []
    for row in rows:
        if not _same_section(row.section):
            excluded["section"] += 1
        elif row.event_type != event_type:
            excluded["event"] += 1
        elif row.year >= year:
            excluded["year"] += 1
        elif row.year in selected:
            if selected[row.year] != row.day:
                raise NavigationError(
                    f"За {row.year} год указаны разные даты одного события: уточните наблюдение"
                )
            excluded["duplicates"] += 1
        else:
            selected[row.year] = row.day
            observations.append(row)
    days = list(selected.values())
    return {
        "n": len(days),
        "years": sorted(selected),
        "observations": sorted(observations, key=lambda row: row.year),
        "excluded": excluded,
        "median": _quantile(days, Fraction(1, 2), year) if days else None,
        "p20": _quantile(days, Fraction(1, 5), year) if days else None,
        "small_sample": len(days) < 5,
    }


def moment(text):
    try:
        value = datetime.fromisoformat(text)
        return (
            value.replace(tzinfo=LOCAL_TIME)
            if value.utcoffset() is None
            else value.astimezone(LOCAL_TIME)
        )
    except (ValueError, TypeError) as exc:
        raise NavigationError(
            "Укажите календарную дату и время; дата без времени принимается как 00:00 UTC+10"
        ) from exc


def duration(value, label):
    try:
        if isinstance(value, bool) or not str(value).strip():
            raise ValueError
        number = Decimal(str(value).strip().replace(",", "."))
        if not number.is_finite() or not 0 <= number <= 87600:
            raise ValueError
        microseconds = number * 3_600_000_000
        if microseconds != microseconds.to_integral_value():
            raise ValueError
        return timedelta(microseconds=int(microseconds))
    except (ValueError, InvalidOperation, OverflowError) as exc:
        raise NavigationError(
            f"{label}: укажите от 0 до 87600 часов, не оставляйте поле пустым"
        ) from exc


def check_voyage(departure, boundary, outbound, unloading, inbound, reserve):
    parts = [
        duration(v, label)
        for v, label in zip(
            (outbound, unloading, inbound, reserve),
            ("Движение туда", "Выгрузка", "Обратное движение", "Запас времени"),
        )
    ]
    try:
        travel = sum(parts[:3], timedelta())
        total = travel + parts[3]
        returned = departure + travel
        protected = departure + total
        latest = boundary - total
        margin = boundary - protected
    except OverflowError as exc:
        raise NavigationError("Получившаяся дата выходит за поддерживаемый календарь") from exc
    return {
        "fits": protected <= boundary,
        "return_at": returned,
        "with_reserve_at": protected,
        "latest_departure": latest,
        "late_hours": max(0, -margin.total_seconds() / 3600),
        "margin_hours": max(0, margin.total_seconds() / 3600),
        "total_hours": total.total_seconds() / 3600,
    }
