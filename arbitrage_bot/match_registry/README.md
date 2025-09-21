# Match Approval Workflow

The bot requires a manual confirmation for every new Pinnacle ↔ Polymarket pairing before it becomes tradeable.

1. When the strategy discovers a potential match, it writes a row to `pending_matches.csv` with the titles and the fuzzy score.
2. При запуске бота вас автоматически спросят в консоли — `y` добавляет запись в `approved_matches.json`, `n` помечает как отклонённую, `Enter`/`s` откладывает решение (повторим через ~30 секунд).
3. При необходимости можно править `approved_matches.json` вручную, структура показана в `approved_matches.sample.json`.

Keeping `approved_matches.json` out of version control avoids leaking identifiers or workflows; copy the sample file and curate it locally for production.
