# Архитектурные заметки: arbitrage_bot

Эти заметки описывают, что где и как работает в директории `arbitrage_bot/` и в частности в файле `main.py`.

## Обзор

- Источники данных:
  - Pinnacle (лайв) — через WebSocket-сообщения от внешнего Go-парсера (`data/parse_serge`), слушаем `ws://localhost:8765`.
  - Polymarket — через публичный HTTP API (`https://gamma-api.polymarket.com/events`).
- Основные задачи:
  - `pinnacle_handler()` — принимает события от Pinnacle и кладет в `pinnacle_data`, попутно пишет в `pinnacle_data.json` и накапливает в `pinnacle_history`.
  - `poll_polymarket_data()` — опрашивает Polymarket каждые 5 секунд, фильтрует live-события, кладет в `polymarket_data`, пишет в `polymarket_data.json` и накапливает в `polymarket_history`.
  - `comparison_logic()` — сопоставляет события из Pinnacle и Polymarket, вычисляет арбитраж по правилу и при необходимости инициирует сделку на Polymarket.
  - `place_polymarket_trade()` — оформляет и отправляет ордер через `py_clob_client` и инициирует подробное логирование сделки.
  - `save_trade_log()` — через 120 секунд после сделки сохраняет детальный JSON с окном T-60s/T+120s в `trade_logs/`.
- Точка входа: `main()` — поднимает WebSocket-сервер и запускает конкурентно задачи `comparison_logic()` и `poll_polymarket_data()`.

## Конфигурация и зависимости

- Файл зависимостей: `arbitrage_bot/requirements.txt` (httpx, websockets, loguru, thefuzz, py_clob_client и др.).
- Переменные окружения (`.env`, загружается через `python-dotenv`):
  - `POLY_PRIVATE_KEY` — приватный ключ для подписания ордеров (или `PRIVATE_KEY`).
  - `POLY_SIGNATURE_TYPE` — тип подписи: `1` (Email/Magic), `2` (браузерный кошелёк), пусто — прямой EOA.
  - `POLY_PROXY_ADDRESS` — адрес прокси/фандера для типов 1 и 2 (или `FUNDER_ADDRESS`).
- Инициализация клиента: `get_clob_client()` выбирает режим (proxy или EOA), вызывает `set_api_creds(...)` и кэширует клиент.

ВНИМАНИЕ: `arbitrage_bot/.env` сейчас содержит чувствительные данные. Рекомендуется немедленно:
- Отозвать/заменить приватный ключ, если файл мог быть где-либо опубликован.
- Добавить `.env` в `.gitignore` и хранить только шаблон `.env.example` без секретов.

## Структуры данных и логирование

- Глобальные словари: `pinnacle_data`, `polymarket_data` — текущие снапшоты рынков.
- Очереди-истории: `pinnacle_history`, `polymarket_history` (deque c maxlen=500) — для последующего детального логирования сделок.
- Путь для логов сделок: `trade_logs/`. Файл создаётся на каждую сделку в формате:
  - `trade_<pinnacle_match_id>_<timestamp>.json` с разделами: `trade_details`, `pre_trade_window_60s`, `post_trade_window_120s`.

## Поток данных: Pinnacle

- `pinnacle_handler(websocket)`:
  - Принимает JSON-сообщения, ожидает наличие `MatchId`, `homeName`, `awayName`.
  - Формирует поле `match = "<homeName> vs <awayName>"` и сохраняет объект в `pinnacle_data[MatchId]`.
  - Пишет весь `pinnacle_data` в `pinnacle_data.json` (учесть возможную нагрузку IO).
  - Кладет сырые данные с таймштампом в `pinnacle_history`.

## Поток данных: Polymarket

- `poll_polymarket_data()`:
  - GET `https://gamma-api.polymarket.com/events` c набором `series_id`, `closed=false`, `include_chat=true`.
  - Фильтрует события как live по признакам: `live == true` или наличие `score`/`elapsed`.
  - Обновляет `polymarket_data` и пишет в `polymarket_data.json`.
  - Накапливает снапшоты в `polymarket_history`.
  - Пауза 5 секунд между вызовами.

## Сопоставление событий и рынков

- События: `find_matching_polymarket_event(pin_title)` — ищет лучшее соответствие по `thefuzz.token_sort_ratio`, порог 80%.
- Рынок Moneyline:
  - `find_polymarket_moneyline_market(event)` сначала ищет `sportsMarketType=="moneyline"`.
  - Если нет, использует нечеткое сравнение названия вопроса рынка с `event.title` и выбирает лучшего кандидата при пороге > 95%.
- Fallback-конструктор Moneyline:
  - `build_moneyline_from_binary_markets(event, home_name, away_name)` — собирает `home/draw/away` из отдельных бинарных рынков по ключевым словам в `question`/`groupItemTitle`.
  - Возвращает структуру с `p_yes` (цена YES), `token_id`, `liquidity` и ссылкой на сам рынок.

## Логика сравнения и триггер сделки

- `comparison_logic()` выполняется циклически каждые 2 секунды:
  1. Копирует `pinnacle_data` для безопасной итерации.
  2. Если `TEST_MODE=True` и есть данные Polymarket — пытается сгенерировать фейковый Pinnacle-ивент на основе реального Polymarket (`create_test_pinnacle_event()`), чтобы гарантированно получить арбитражную возможность.
  3. Для каждого Pinnacle-ивента:
     - Находит матч в Polymarket (`find_matching_polymarket_event`).
     - Извлекает Pinnacle-коэффициенты из `Periods[0].Win1x2` в список `pin_odds_list` с элементами `{name, price}` для `home`, `away`, опционально `Draw`.
     - Если найден явный Moneyline-рынок на PM:
       - Берёт `outcomes` и `outcomePrices`.
       - Ищет соответствие `pin_odds_list` ↔ `outcomes` по `fuzz.partial_ratio > 80`.
       - Считает `o_pm = 1 / p_yes` и сравнивает с Pinnacle: арбитраж при `o_pm >= o_pin * 1.12`.
       - Проверяет `check_trade_cooldown(...)`, базируясь на цене `p_yes`.
       - Проверяет ликвидность (`liquidityNum`) и диапазон цены [0.001, 0.999].
       - Берёт `clobTokenIds[i]`, формирует `trade_details` и вызывает `place_polymarket_trade(...)`.
     - Иначе (fallback):
       - Строит moneyline из бинарных рынков `home/draw/away`.
       - По каждому ключу считает арбитраж, проверяет cooldown и ликвидность, оформляет сделку через `place_polymarket_trade(...)`.

- Правило арбитража:
  - `o_pm = 1 / p_yes` — десятичные коэффициенты из цены Polymarket.
  - Триггер, если `o_pm >= o_pin * 1.12` (12% и более выгоднее, чем Pinnacle).

## Исполнение сделки и детальное логирование

- `place_polymarket_trade(trade_details)`:
  - Собирает `OrderArgs(price=p_yes, size=bet_amount_usd, side=BUY, token_id)`.
  - Создает и отправляет ордер через `py_clob_client`.
  - В случае ошибки "lower than the minimum" — помечает как выполненную для целей cooldown (`SKIPPED_MIN_SIZE`).
  - Пишет попытку в кэш `recent_trades[market_id]` и формирует срез истории T-60s.
  - Планирует асинхронную задачу `save_trade_log(...)` для записи полного лога через 120 секунд.

- `save_trade_log(trade_details, pre_trade_history)`:
  - Через 120 секунд после сделки собирает пост-историю T+120s из `pinnacle_history`/`polymarket_history`.
  - Пишет файл в `trade_logs/` с полными данными окружения сделки.

## Cooldown логика

- `check_trade_cooldown(market_id, current_price)`:
  - Окно — 120 секунд.
  - Если в последние 2 минуты была покупка по цене не хуже текущей (т.е. `current_price >= прошлой цене`), новая не открывается.

Замечание: в ветке с явным Moneyline cooldown ключом выступает `market_id` рынка. Это означает, что покупка по одному исходу блокирует и другие исходы того же рынка на 2 минуты. Если нужно строго "по данному исходу", стоит использовать более точный ключ, например `f"{market_id}:{outcome_index}"` или `token_id`.

## TEST_MODE и генерация тестовых событий

- `TEST_MODE=True` включает создание фейкового Pinnacle-события на основе реального рынка Polymarket:
  - Выбирается валидная цена `p_yes` в диапазоне [0.001, 0.999].
  - Вычисляется `o_pm = 1/p_yes` и подбирается `test_pinnacle_odd = o_pm/1.15`, что гарантированно удовлетворяет условию арбитража.
  - Формируется событие с полями `Win1/Win2` в `Periods[0].Win1x2`.

## Файлы и артефакты в папке

- `main.py` — основная логика бота.
- `requirements.txt` — зависимости Python.
- `pinnacle_data.json`/`polymarket_data.json` — актуальные снапшоты данных (обновляются во время работы).
- `trade_logs/` — детальные логи по сделкам.
- `ARCHITECTURE.md` — этот документ с заметками.

## Рекомендации по улучшениям

1) Точность и соответствие cooldown "по исходу":
   - В явном moneyline-случае использовать ключ вида `f"{market_id}:{outcome_index}"` или `token_id` для `recent_trades`, чтобы не блокировать другие исходы рынка.

2) Ликвидность и размер ордера:
   - `liquidityNum` обычно отражает ликвидность всего рынка; желательно проверять доступную ликвидность конкретного `token_id` (по книге ордеров) и учитывать проскальзывание.
   - Можно заранее проверять `order_min_size` из метаданных рынка, чтобы уменьшить число неудачных попыток.

3) Сопоставление матчей/команд:
   - Нормализовать названия (удалять лишние символы, сокращения, названия турниров) перед `fuzz`-сравнением.
   - Снижать/гибко настраивать пороги подобия (80/95) и вести метрики совпадений/ложных срабатываний.

4) Производительность и IO:
   - Запись всего `pinnacle_data.json` при каждом сообщении может быть тяжёлой. Рассмотреть буферизацию/дебаунс или периодическую запись.
   - Ограничить размер `trade_logs` (ротация, архивирование) и добавить housekeeping.

5) Отказоустойчивость:
   - Ввести экспоненциальный backoff для HTTP/WS ошибок.
   - Добавить healthchecks/сердцебиение для Go-парсера и повторные подключения.

6) Контроль рисков:
   - Лимиты на дневной объём, число одновременных позиций, долговечность ордеров.
   - Логирование PnL, мониторинг исполнения, алерты.

7) Наблюдаемость:
   - Использовать структурированное логирование (JSON), выделить уровни/теги для быстрого анализа.
   - Прометей/графана метрики: количество совпадений, арбитражных триггеров, успешных/неуспешных ордеров, задержки API и т.п.

8) Чистота кода:
   - Вынести "поисковую" и "торговую" логику в отдельные модули, добавить тесты на парсинг рынков и вычисление арбитража.
   - Дополнить type hints и docstrings там, где отсутствуют.

## Быстрые подсказки по запуску

- Поднять Go-парсер (`data/parse_serge/`) так, чтобы он слал в `ws://localhost:8765`.
- Настроить `.env` с валидными значениями (не коммитить!).
- Установить зависимости и запустить `python main.py`.

Если понадобится, можно дополнить этот документ схемами и примерами конкретных сообщений из `pinnacle_data.json` и `polymarket_data.json`.
