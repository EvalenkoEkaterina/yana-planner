"""Isolate web calculations so a wall-clock timeout stops native solver work too."""

import json
import subprocess
import sys

from .domain import InputError


class CalculationTimeout(InputError):
    """The whole calculation exceeded the web response budget."""


def calculate_with_timeout(scenario, deadline, *, total_timeout=30, **kwargs):
    return _run_worker(dict(scenario=scenario, deadline=deadline, kwargs=kwargs), total_timeout)


def calculate_request_with_timeout(payload, *, total_timeout=30, max_deadline=492):
    return _run_worker(
        dict(operation="request", payload=payload, max_deadline=max_deadline), total_timeout
    )


def calculate_package_with_timeout(
    package, *, search_seconds="auto", total_timeout=30, max_deadline=492
):
    return _run_worker(
        dict(
            operation="package",
            payload=package,
            search_seconds=search_seconds,
            max_deadline=max_deadline,
        ),
        total_timeout,
    )


def calculate_multi_stop_with_timeout(payload, *, total_timeout=30):
    try:
        return _run_worker(dict(operation="multi_stop", payload=payload), total_timeout)
    except CalculationTimeout as exc:
        raise CalculationTimeout(
            f"Расчёт остановлен по общему лимиту {total_timeout:g} с. "
            "Достижение лимита не означает невыполнимость условий. "
            "Можно повторить расчёт с 2 секундами на этап."
        ) from exc


def _run_worker(request, total_timeout):
    try:
        payload = json.dumps(request, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InputError("Условия должны содержать только конечные числа и JSON-значения") from exc
    try:
        response = subprocess.run(
            [sys.executable, "-m", "yana_planner.web_worker"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=total_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        # subprocess.run kills and waits for the child on timeout. No solver is
        # left running in a thread after returning an error to the user.
        raise CalculationTimeout(
            f"Расчёт остановлен: общий лимит {total_timeout:g} с исчерпан. "
            "Условия сохранены в форме. Выберите быстрый поиск (2 с на этап), "
            "проверьте единицы времени и объёма. Если задача допускает, "
            "планируйте более короткий период или меньший состав флота. "
            "Достижение лимита не означает, что допустимого плана нет."
        ) from exc
    try:
        data = json.loads(response.stdout)
    except (ValueError, TypeError) as exc:
        raise InputError("Расчёт не завершился корректно. Введённые условия сохранены.") from exc
    if response.returncode != 0 or "error" in data:
        raise InputError(data.get("error", "Расчёт не завершился корректно"))
    return data["inputs"], data["result"]


def main():
    from .service import calculate, calculate_package, calculate_request

    try:
        payload = json.load(sys.stdin)
        if payload.get("operation") == "multi_stop":
            from .multi_stop_service import calculate_multi_stop_request

            inputs, result = calculate_multi_stop_request(payload["payload"])
        elif payload.get("operation") == "package":
            inputs, result = calculate_package(
                payload["payload"],
                search_seconds=payload["search_seconds"],
                max_deadline=payload["max_deadline"],
            )
        elif payload.get("operation") == "request":
            inputs, result = calculate_request(
                payload["payload"], max_deadline=payload["max_deadline"]
            )
        else:
            inputs, result = calculate(
                payload["scenario"], payload["deadline"], **payload["kwargs"]
            )
        json.dump(
            dict(inputs=inputs, result=result), sys.stdout, ensure_ascii=False, allow_nan=False
        )
    except (InputError, OSError) as exc:
        json.dump(dict(error=str(exc)), sys.stdout, ensure_ascii=False)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
