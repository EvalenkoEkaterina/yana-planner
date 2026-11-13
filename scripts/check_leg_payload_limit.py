"""One static section limit checked against actual remaining cargo before departure."""

import json
from copy import deepcopy
from pathlib import Path

from yana_planner.multi_stop import plan_multi_stop

ROOT = Path(__file__).resolve().parents[1]


def run():
    base = json.loads((ROOT / "examples/multi_stop/two-recipients.json").read_text())
    out = ROOT / "output/leg-payload-limit"
    out.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Лимит груза на участке перед отправлением",
        "",
        "**Лимиты учебные, не реальные ограничения осадки Яны.** Судно загружает 600 т, отдаёт 200 т в Усть-Куйге и везёт 400 т в Батагай. Объёмы и времена также учебные.",
        "",
        "| Участок | На борту перед отправлением, т | Лимит, т | Результат |",
        "|---|---:|---:|---|",
    ]
    for i, (leg, cap) in enumerate([("leg1", 600), ("leg1", 500), ("leg2", 450), ("leg2", 350)]):
        cfg = deepcopy(base)
        cfg["leg_payload_limits"] = [
            dict(
                leg_id=leg,
                max_onboard_t=cap,
                status="scenario_assumption",
                source="Учебный пример статического лимита; не выведен из уровня воды или осадки",
            )
        ]
        result = plan_multi_stop(cfg)
        check = result["departure_checks"][0]
        (out / f"case-{i}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        legname = "Нижнеянск → Усть-Куйга" if leg == "leg1" else "Усть-Куйга → Батагай"
        lines.append(
            f"| {legname} | {check['onboard_t']} | {cap} | {'Допустимый план найден' if result['goal_met'] else 'Невыполнимо: превышен лимит'} |"
        )
    lines += [
        "",
        "Проверяется фактический остаток на борту: на первом плече 600 т, на втором 400 т. Поэтому лимит второго плеча 450 т не запрещает исходную погрузку 600 т — до него уже выгружены 200 т.",
        "",
        "Лимит постоянный. Ожидание его не меняет; запрещённый рейс не отправляется и партии автоматически не уменьшаются. Лимит действует на всё выбранное плечо между остановками. Для отдельного подучастка нужен дополнительный узел либо следующий контракт участков.",
        "",
        "Это проверка выполнимости по явно заданной массе груза. Уровень воды, осадка, запас глубины, реальные допуски и обратный путь не выводятся из этого числа. Возврат в модели пустой, разбивки его пути на участки пока нет.",
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print("4 проверки выполнены; итог в output/leg-payload-limit/report.md")


if __name__ == "__main__":
    run()
