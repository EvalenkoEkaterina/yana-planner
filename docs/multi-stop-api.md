# API многопунктового расчёта — контракт для Нади

Серверная часть реализована отдельно от старого API READY/MILP. Основную форму,
шаблоны и JS Нади не меняли. Документ фиксирует фактические адреса и формат.

## Создать расчёт

POST `/api/multi-stop/plan`, Content-Type: application/json.
Готовый полный пример: `examples/multi_stop/api-request.json`.

```json
{
  "schema_version": "yana-multi-stop-request/1",
  "scenario": {"...": "содержимое examples/multi_stop/four-recipients.json"},
  "cost_terms": null,
  "search_seconds": 2
}
```

Это сокращённая схема, не исполняемый пример: для запуска берите api-request.json.
scenario соответствует docs/multi-stop-list.md. cost_terms соответствует
multi-stop-economics.md; допустим null или отсутствие. Передавать cost_terms
отдельно, не внутрь scenario. search_seconds — только целое 2 или 10, это лимит
одного этапа, не всего расчёта. По умолчанию 2. Адаптивного auto в этом API нет.
Точки посещения — stops; порядок важности — scenario.receipt_priority.
Все обязательные объёмы/сроки сохраняются. Встречи разных целей не гарантированы.

Ограничения: одно судно, один рейс, 1–8 получателей, фиксированный порядок,
часы от T0 0–492; до 72 интервалов на список окон/запретов и 100 поступлений.
Движение и операции заданы вручную. Нет автоматического расчёта времени из километров.
Географические ссылки в route_reference — метаданные, не автоматическая проверка
проходимости. Event/forecast/withdrawals и неизвестные вычислительные поля отклоняются.
До 1 МиБ тела запроса.

## Ответ после сохранения — HTTP 201

```json
{
  "id": 1,
  "inputs": {
    "schema_version": "yana-multi-stop-request/1",
    "scenario": {},
    "cost_terms": null,
    "settings": {"stage_limit_s": 2, "total_web_timeout_s": 30}
  },
  "result": {
    "schema_version": "yana-multi-stop-result/1",
    "status": "goal_met",
    "goal_met": true,
    "visit_order": [],
    "receipt_priority": [],
    "objective_stop_id": "batagay",
    "primary_receipt_proven": true,
    "lexicographic_optimal": true,
    "receipt_optima": [],
    "plan": {},
    "solver": [],
    "input_sha256": "...",
    "code_version": "..."
  },
  "links": {
    "page": "/multi-stop/runs/1",
    "json": "/multi-stop/runs/1/results.json",
    "api": "/api/multi-stop/runs/1",
    "history": "/multi-stop/history"
  }
}
```

Пустые объекты/списки выше сокращают реальный ответ. plan.deliveries содержит
stop_id, accepted_t, receipt_h, cargo_after_t; ready_h — полная готовность после
возврата/подготовки. starts — времена операций. В costs — полная/известная стоимость,
компоненты, активность/ожидание и missing; при отсутствии cost_terms блока costs нет.
При незаданных ставках status costs = missing_cost_inputs, total_rub=null.
Не подставлять нули и не выдавать known_subtotal_rub за полную сумму.

HTTP 201 означает сохранение, а не выполнение задания! Также сохраняются:

- status=infeasible, goal_met=false, plan=null — доказанная невыполнимость условий.
- status=no_feasible_solution_found, goal_met=false, plan=null — план не найден,
  это не доказательство невыполнимости.
- goal_met=true при lexicographic_optimal=false — допустимый полный план найден,
  но не все этапы доказали оптимальность.

Приоритеты решаются последовательно. receipt_optima[].optimal_given_higher_priorities
— условная оптимальность с фиксацией более важных целей. earliest_last_proven/старый
alias earliest_second_proven true только если последняя приёмка выбрана первой целью.
Нельзя заменять ими статус всех выбранных целей.

## Ошибки

- 400 invalid_input — ошибка запроса/неподдерживаемые поля.
- 408 calculation_timeout — общий лимит вычисления (по умолчанию 30 с).
- 413 request_too_large — тело больше 1 МиБ.
- 500 server_error — не удалось выполнить или сохранить результат.

400/408/500 содержат code, error, result=null, submitted_request. Корректный JSON
запроса возвращается без изменения для восстановления формы. Для невалидного JSON
или нечисловых JSON-констант (NaN) submitted_request=null. При 413 копии запроса нет.
Фронт всегда должен удерживать собственное состояние полей, не полагаться только
на ответ сервера. На ошибках записи истории нет.

Дочерний процесс принудительно останавливается и завершается при исчерпании общего
лимита. Повторная попытка — новый POST. Пока синхронный запрос, без прогресса в
процентах и без фоновой очереди. Обновление сохранённого GET ничего не пересчитывает.

## История и повторное открытие

- GET `/api/multi-stop/runs?limit=100`: `{items:[{id,created_at,status,origin_id,stop_ids,required_t,links}]}`.
  limit — 1–100. Время UTC, новые первыми. История пока отдельная от старого раздела.
- GET `/api/multi-stop/runs/<id>`: полный снимок `{id,created_at,status,inputs,result,code_version,links}`.
- GET `/multi-stop/runs/<id>/results.json`: тот же снимок, скачивание attachment.
- GET `/multi-stop/runs/<id>`: базовый серверный отчёт; Надя может заменить представление
  своим шаблоном после согласования, не меняя адрес/структуру API.
- GET `/multi-stop/history`: базовая страница истории многопунктовых расчётов.
- Неизвестный id: 404 `{code:"not_found",error:"..."}`.

Снимки хранятся в отдельной таблице multi_stop_runs той же SQLite-базы. Старые runs
и маршруты продолжают работать. ID пространств раздельные: multi-stop/1 не то же,
что старый runs/1. inputs и result неизменяемы после сохранения; источники и прочие
переданные метаданные сохраняются в нормализованном scenario. База не пушится.

## Запуск и интеграционная проверка

```sh
source .venv/bin/activate
python -m flask --app yana_planner.web:create_app run --port 5003
```

В другом терминале из корня проекта:

```sh
curl --fail-with-body --json @examples/multi_stop/api-request.json http://127.0.0.1:5003/api/multi-stop/plan
```

Ответ содержит links.page, links.api и links.json — не собирать адрес из старого /runs.
Для JS: fetch с JSON.stringify(payload), прочитать response.json(), отдельно проверить
response.ok, затем result.goal_met и статусы. На время запроса отключить повторный
submit; finally снова разрешить. Тайм-аут браузера не должен быть короче серверного
вычислительного лимита с учётом передачи/сохранения.

Проверить успешный план, неизвестные ставки, невыполнимость, неподдерживаемый ввод,
408, восстановление полей, открытие/скачивание истории без нового POST. Пакет из
примера полностью учебный; новые места выгрузки/расстояния не подтверждены.
