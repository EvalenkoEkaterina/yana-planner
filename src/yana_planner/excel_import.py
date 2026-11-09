"""Read-only adapters for two explicitly supported Входы_модели layouts."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .domain import ConfirmationRequired, InputError, Scenario, integer


def import_excel(
    filename: str,
    *,
    profile: str,
    sim_only: bool,
    deadline: int | None = None,
    return_cutoff: int | None = None,
) -> dict:
    if not sim_only:
        raise ConfirmationRequired("Для импорта учебных допущений явно укажите --sim-only")
    try:
        from openpyxl import load_workbook
    except ImportError as e:
        raise InputError('Для Excel установите пакет: pip install -e ".[excel]"') from e
    try:
        book = load_workbook(filename, data_only=True, read_only=True)
    except (OSError, ValueError) as e:
        raise InputError(f"Не удалось открыть XLSX: {e}") from e
    try:
        if "Входы_модели" not in book.sheetnames:
            raise InputError("Лист Входы_модели не найден")
        sheet = book["Входы_модели"]
        if profile not in ("team-r03", "resource-v1"):
            raise InputError("Неизвестный профиль импорта")
        value_column = 3 if profile == "team-r03" else 11
        cells = {
            str(row[0].value): row[value_column - 1].value
            for row in sheet.iter_rows(max_col=value_column)
            if len(row) >= value_column
            and isinstance(row[0].value, str)
            and re.fullmatch(r"I\d{2}|T0|LIMIT", row[0].value)
        }
        cfg = dict(
            schema_version="1.0",
            id="EXCEL-SIM",
            mode="SIM_ONLY",
            target_t=cells.get("I02"),
            initial_source_t=cells.get("I03"),
            destination_capacity_t=cells.get("I11"),
            withdrawals=[],
            closures=[],
            navigation_assumed=True,
            reception_assumed=True,
            fleet_assumed=True,
            supply_assumed=True,
            timezone=None,
            real_admissibility="requires_confirmation",
            provenance=dict(
                kind="explicit_scenario_import",
                source_filename=Path(filename).name,
                source_sheet="Входы_модели",
                profile=profile,
                permissions="Assumed only because --sim-only was explicitly provided; not real clearance",
            ),
        )
        if cells.get("I01") != "НКТ":
            raise InputError("Адаптер ожидает НКТ; другой продукт не подставляется автоматически")
        if profile == "team-r03":
            if sheet["A20"].value != "Судно" or sheet["A25"].value != "Событие":
                raise InputError("Разметка team-r03 не совпадает")
            cfg["id"] = str(sheet["B3"].value)
            cfg["t0"] = cells.get("T0")
            cfg["vessels"] = [
                dict(
                    id=sheet.cell(r, 1).value,
                    available_h=sheet.cell(r, 2).value,
                    capacity_t=sheet.cell(r, 3).value,
                    load_h=sheet.cell(r, 4).value,
                    up_h=sheet.cell(r, 5).value,
                    unload_h=sheet.cell(r, 6).value,
                    return_h=sheet.cell(r, 7).value,
                    prep_h=sheet.cell(r, 8).value,
                )
                for r in (21, 22)
            ]
            cfg["arrivals"] = [
                dict(h=sheet.cell(r, 2).value, t=sheet.cell(r, 3).value) for r in (26, 27)
            ]
            cfg["payload_calendar"] = [dict(from_h=0, capacity_t=cells.get("LIMIT"))]
            cfg["return_cutoff_h"] = (
                return_cutoff if return_cutoff is not None else cells.get("I32")
            )
            cfg["delivery_deadline_h"] = deadline if deadline is not None else cells.get("I31")
        else:
            if sheet["L35"].value != "SIM-BASE":
                raise ConfirmationRequired(
                    "Профиль resource-v1 требует активный SIM-BASE; BASE не изменяется"
                )

            def records(ref):
                if not isinstance(ref, str) or "!" not in ref:
                    raise InputError("Не задан диапазон набора записей")
                tab, bounds = ref.rsplit("!", 1)
                tab = tab.strip("'")
                if tab not in book.sheetnames:
                    raise InputError(f"Не найден лист {tab}")
                values = list(book[tab][bounds])
                headers = [c.value for c in values[0]]
                return [
                    dict(zip(headers, [c.value for c in row]))
                    for row in values[1:]
                    if any(c.value is not None for c in row)
                ]

            def date(value):
                if isinstance(value, datetime):
                    return value
                if isinstance(value, str):
                    try:
                        return datetime.fromisoformat(value)
                    except ValueError:
                        pass
                raise InputError("Ожидается дата Excel/ISO, не произвольный серийный номер")

            supply = records(cells.get("I04"))
            fleet = records(cells.get("I05"))
            anchor = min(date(r["Дата/время"]) for r in supply)

            def hour(value):
                try:
                    diff = (date(value) - anchor).total_seconds() / 3600
                except TypeError as e:
                    raise InputError(
                        "Нельзя смешивать даты с разными правилами часового пояса"
                    ) from e
                return integer(diff, "час от T0")

            cfg["t0"] = anchor.isoformat()
            cfg["arrivals"] = [
                dict(h=hour(r["Дата/время"]), t=r["Объём, т"])
                for r in supply
                if r.get("Тип события") == "Поступление НКТ"
            ]
            cfg["vessels"] = [
                dict(
                    id=v["ID ресурса"],
                    available_h=hour(v["Доступен с"]),
                    capacity_t=v["Предел партии, т"],
                    load_h=v["Погрузка, ч"],
                    up_h=v["Ход вверх, ч"],
                    unload_h=v["Выгрузка, ч"],
                    return_h=v["Возврат, ч"],
                    prep_h=v["Подготовка, ч"],
                )
                for v in fleet
            ]
            cfg["payload_calendar"] = [dict(from_h=0, capacity_t=cells.get("I07"))]
            # These source fields are unknown in the reviewed workbook. An override
            # is explicit in CLI and recorded, never silently substituted.
            cfg["return_cutoff_h"] = (
                return_cutoff if return_cutoff is not None else cells.get("I32")
            )
            cfg["delivery_deadline_h"] = deadline if deadline is not None else cells.get("I31")
        if cells.get("I12") != 0:
            if cells.get("I12") is None:
                raise ConfirmationRequired("Отбор не задан: неизвестное нельзя считать нулём")
            raise InputError(
                "Ненулевой отбор импортируется отдельным подтверждённым календарём, не постоянной ставкой без периода"
            )
        cfg["delivery_deadline_h"] = integer(cfg["delivery_deadline_h"], "delivery_deadline_h")
        cfg["provenance"]["deadline_override_h"] = deadline
        cfg["provenance"]["return_cutoff_override_h"] = return_cutoff
        return Scenario.from_dict(cfg).to_dict()
    finally:
        book.close()
