# CS Prep

Telegram Mini App для подготовки комментатора к матчам CS2 тир-1.

- **Список матчей** на неделю вперёд по дням, время по Москве. Матчи, которые ты ведёшь, отмечаются микрофоном.
- **Карточка матча**: личные встречи с датами и турнирами, статистика по картам, последние игры на каждой карте, форма команд.
- **Игроки**: составы, у каждого игрока счётчик свежих новостей.
- **Новости за 7 дней** про игроков и команды: Google News (рус. и англ.), HLTV, Cybersport.ru. Нейросеть отсеивает совпадения не по теме и переводит на русский.

## Как это работает

Отдельного сервера нет. Всё бесплатно и живёт на GitHub:

1. Каждые 3 часа GitHub Actions запускает `collector/collect.py`.
2. Скрипт собирает данные и сохраняет их в `docs/data/`.
3. GitHub Pages раздаёт `docs/` как сайт, а бот в Telegram открывает его как Mini App.

## Настройка

1. **Секрет PandaScore**: Settings → Secrets and variables → Actions → New repository secret. Имя `PANDASCORE_TOKEN`, значение — токен из дашборда PandaScore.
2. **Разрешить запись**: Settings → Actions → General → Workflow permissions → *Read and write permissions* → Save.
3. **Сайт**: Settings → Pages → Source: *Deploy from a branch*, Branch: `main`, папка `/docs` → Save. Через минуту появится адрес вида `https://<логин>.github.io/cs-prep/`.
4. **Первый запуск**: Actions → «Обновить данные» → Run workflow.
5. **Бот**: в @BotFather `/mybots` → твой бот → Bot Settings → Configure Mini App → Enable Mini App → вставь адрес из шага 3. Там же в Menu Button можно поставить этот адрес, чтобы кнопка «Матчи» была прямо в чате.

## Настройки сборщика

Переменные можно задать в Settings → Secrets and variables → Actions → Variables:

| Переменная | По умолчанию | Что значит |
|---|---|---|
| `AI_MODEL` | `openai/gpt-4.1-mini` | модель GitHub Models для новостей |

Остальное меняется в начале `collector/collect.py`: уровни турниров (`TIERS`), глубина новостей (`NEWS_DAYS`), список RSS-лент (`RSS_FEEDS`).
