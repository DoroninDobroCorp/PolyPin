# Архитектурные заметки: arbitrage_bot

Этот документ отражает текущую модульную структуру проекта `arbitrage_bot/`.

## Компоненты

- **`config.py`** – отвечает за загрузку переменных окружения из `.env`, создание рабочих каталогов (`trade_logs`, `data_cache`, `match_registry`) и хранение основных констант (например, `ARB_RATIO`, список `POLYMARKET_SERIES_IDS`).
- **`state.BotState`** – контейнер оперативных данных: актуальные снапшоты Pinnacle/Polymarket, rolling-истории для логирования окна T-60/T+120, cooldown-кэш, список фоновых задач и paper-позиции.
- **`logging_utils.py`** – настройка `loguru`, подготовка CSV-логов и helper для дампов JSON.
- **`data_sources.py`** –
  - `create_pinnacle_handler(state)` возвращает обработчик WebSocket-сессии, который пишет события Pinnacle в `state.pinnacle_data` и раз в несколько секунд обновляет снапшот `data_cache/pinnacle_data.json`.
  - `poll_polymarket_data(state)` опрашивает публичный API Polymarket каждые 5 секунд (с учётом заданных `series_id`), фильтрует live-события и сохраняет снапшоты в `data_cache/polymarket_data.json`.
- **`matching.py`** – инкапсулирует fuzzy-matching и учет подтверждений:
  - Новые пары Pinnacle ↔ Polymarket попадают в `match_registry/pending_matches.csv`.
  - Торговля разрешается только после добавления соответствия в `match_registry/approved_matches.json` (есть пример `approved_matches.sample.json`).
- **`orderbook.py`** – кэшируемые запросы книги ордеров Polymarket, расчёт доступной ликвидности до порога и оценка потенциального выхода по bid.
- **`trading.py`** – инициализация `py_clob_client`, контроль cooldown, сохранение логов сделок, paper-режим фиксации тейк-профита.
- **`strategy.py`** – основная бизнес-логика: сопоставление событий, расчёт коэффициентов, проверка условий арбитража, глубины ордербука и запуск трейдов.
- **`approvals.py`** – интерактивная очередь подтверждений (CLI-подсказки `y/n/s`, повторный запрос через 30 секунд, начальная загрузка накопившихся pending).
- **`main.py`** – тонкая обвязка: конфигурирует логирование, поднимает WebSocket-сервер и запускает фоновые задачи (стратегия, опрос Polymarket, интерактивные approvals, опциональный paper sell).

## Поток данных

1. Go-парсер (`data/parse_serge`) публикует JSON от Pinnacle в `ws://localhost:8765`.
2. `data_sources.create_pinnacle_handler` читает сообщения, нормализует название матча и обновляет `state.pinnacle_data`.
3. `data_sources.poll_polymarket_data` параллельно опрашивает `https://gamma-api.polymarket.com/events` (серии перечислены в `config.POLYMARKET_SERIES_IDS`) и формирует live-срез `state.polymarket_data`.
4. `strategy.run_strategy`:
   - Для каждого матча Pinnacle ищет лучший матч на Polymarket (fuzzy score ≥ 70).
   - Требует подтверждения через `match_registry/approved_matches.json` (задача `approvals.approval_prompt_loop` ведёт интерактивный CLI-диалог и подскакивает к пользователю по мере появления новых пар).
   - Сопоставляет рынки (moneyline или собранный из бинарных), приводит цены к десятичным коэффициентам.
   - Проверяет правило `O_pm ≥ O_pin × 1.12`, доступную ликвидность и глубину ордербука до пороговой цены.
   - Учитывает cooldown последних сделок и paper-режим (если SELL_MODE ≠ `live`).
   - Вызывает `trading.place_polymarket_trade`, который также инициирует сбор детального лога T-60/T+120.

## Логирование и артефакты

- `trade_logs/trade_<match_id>_<timestamp>.json` – детальный лог сделки (pre/post окно + детали).
- `opportunity_logs/opportunities_changes.csv` – делта-лог потенциальных арбитражей (INFO/ARBITRAGE) с кратким описанием изменений.
- `match_registry/pending_matches.csv` – очередь матчей, ожидающих ручного подтверждения.
- `data_cache/*.json` – «снапшоты» входящих данных, удобны для отладки и анализа.

## Запуск

```bash
python -m arbitrage_bot.main
```

> При запуске напрямую (`python arbitrage_bot/main.py`) сработает fallback, добавляющий корень репозитория в `sys.path`, но предпочтительно использовать форму `-m`.

## Расширения и TODO

- При необходимости можно заменить CSV-пайплайн подтверждения матчей на gRPC/REST сервис или UI.
- В `orderbook.fetch_order_book` пока создаётся новый `AsyncClient` на каждый запрос. При увеличении частоты обращений стоит внедрить пул клиентов с keep-alive.
- Paper-стратегия закрытия позиций реализована минимально – логирует тейк-профиты без стоп-лоссов.
