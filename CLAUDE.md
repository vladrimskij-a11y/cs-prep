# RIM·CS — памятка для Claude

Бесплатное Telegram Mini App для подготовки комментатора (русский) к матчам CS2 тир-1/тир-2.
Всё должно оставаться бесплатным.

## Правила работы (от владельца)
- Что попросили сделать — сразу в main и на сайт, без отдельных веток.
- Если владелец только спрашивает — отвечать, ничего не менять и не запускать.
- Проверочные (probe) workflow, которые только читают источники, — можно; после — удалить.
- Ключи (PANDASCORE_TOKEN, GIGACHAT_KEY, GEMINI_KEY) только в GitHub Secrets, в чат не просить.
- Не обходить защиту сайтов (HLTV и др.). HLTV: не чаще 1 запроса / 2.5 с, только в полном сборе.
- Отвечать по-русски, коротко, без технических подробностей, если не спросили.

## Устройство
- `docs/index.html` — всё приложение (SPA, один файл). Данные читает из ветки `data`
  через raw.githubusercontent.com (`dfetch`), запасной вариант — `docs/data` в Pages.
- `collector/collect.py` — сборщик. Режимы: полный (HLTV, Liquipedia, нейросеть), `STATS_ONLY=1`, `NEWS_ONLY=1`.
- `collector/save_data.py` — слияние и запись в ветку `data` (orphan, один коммит, force-with-lease).
- Workflow: `collect.yml` (полный, каждые 3 ч, публикует Pages), `stats.yml` (~40 мин), `news.yml` (5 мин), `pages.yml` (при изменении docs/).
- Live: Cloudflare Worker `https://rimcs-live.vladrimskij.workers.dev` → api.bo3.gg (/matches, /games, /games/ID/players_stats), код в `tools/cloudflare-worker.js`; владелец вставляет его сам в редакторе Cloudflare.
- Нейросеть: Gemini (основная), GigaChat (запасная).

## Источники
- PandaScore: расписание, турниры. bo3.gg: матчи, карты, статистика игроков, сетки, коэффициенты.
- HLTV: VRS-рейтинг, страницы команд/игроков (MVP, мейджоры, призовые), /results?team= (список матчей для вето). Страницы матчей и stats — 403.
- dust2.us: вето матча (имя команды в tooltip-span, даты нет — берём день со страницы результатов HLTV).
- Liquipedia: составы (query+revisions, не action=parse).
- Valve GitHub (counter-strike_regional_standings): модель VRS — Glicko RD=75, сырые баунти/сеть соперника × вес турнира.
- Telegram: t.me/s/<канал>, заголовок новости = первая строка поста.

## Проверка
- Песочница не видит внешние сайты — проверять через probe workflow на GitHub.
- Вёрстку проверять Playwright-скриншотом локально (данные из ветки data в /tmp).

## Открытые задачи
- 6 ноября 2026, 09:30 МСК (PRV–NAVI, IEM Beijing): проверить live-блок на вкладке «Игроки» — счёт, раунды, K–D/ADR совпадают с bo3.gg.
- MVP у FURIA и BetBoom: HLTV отдавал 403 на страницы игроков; проверить после полных сборов.
