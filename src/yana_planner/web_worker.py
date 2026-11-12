"""Isolate web calculations so a wall-clock timeout stops native solver work too."""

import json
import subprocess
import sys

from .domain import InputError


class CalculationTimeout(InputError):
    """The whole calculation exceeded the web response budget."""


def calculate_with_timeout(scenario, deadline, *, total_timeout=30, **kwargs):
    payload = json.dumps(
        dict(scenario=scenario, deadline=deadline, kwargs=kwargs),
        ensure_ascii=False,
        allow_nan=False,
    )
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
    from .service import calculate

    try:
        payload = json.load(sys.stdin)
        inputs, result = calculate(payload["scenario"], payload["deadline"], **payload["kwargs"])
        json.dump(
            dict(inputs=inputs, result=result), sys.stdout, ensure_ascii=False, allow_nan=False
        )
    except (InputError, OSError) as exc:
        json.dump(dict(error=str(exc)), sys.stdout, ensure_ascii=False)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
