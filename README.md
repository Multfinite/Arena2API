# Arena2API — сборка

**Все правки сделаны при помощи Agent Mode в arena.ai**

arena.ai → OpenAI-совместимый API + проброс всех моделей в GitHub Copilot.
Собрано из `kekurttel/Arena2api-fixed` с правками (список моделей берётся с живой
страницы, добавлены `/v1/arena/models` и `/help`) и скриптами для Copilot.

## Состав

```
Arena2API/
├── server.py              прокси FastAPI (+ /v1/arena/models, /help)
├── help_page.py           данные и HTML для /help
├── requirements.txt
├── extension/             расширение Chrome (MV3) — модели с живой страницы
├── extension-firefox/     то же для Firefox (MV2)
├── scripts/uuid-finder.js букмарклет для поиска UUID моделей
├── copilot/               ВСЁ, что касается VS Code Copilot — отдельная папка
│   ├── install_copilot.py   проброс моделей по профилям
│   ├── diagnose_copilot.py  почему модели не появились
│   ├── test_agent_mode.py   e2e-тест агентного пути
│   └── README.md
├── tools/
│   ├── parse_models.py    парсер страницы arena.ai → списки моделей
│   └── models/            сгенерированные списки (csv/json/payload)
├── tests/
│   ├── test_injector.js   тест парсера моделей в расширении
│   └── flight_sample.txt  фикстура (flight-данные страницы)
└── patches/
    └── arena2api-vs-upstream.patch   все правки одним диффом к upstream
```

## Отличия от upstream

Всё, чем эта сборка отличается от `kekurttel/Arena2api-fixed` (коммит `5feabda`),
собрано в `patches/arena2api-vs-upstream.patch` — 18 файлов, разбор по файлам в
`patches/README.md`. Патч наложен на свежий клон upstream и проверен: дерево после
наложения идентично этому проекту.

## Запуск

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python server.py            # http://localhost:9090
```

Переменные окружения: `PORT` (9090), `API_KEY` (если задан — нужен
`Authorization: Bearer <ключ>`), `DEBUG=1`.

Расширение: `chrome://extensions/` → «Режим разработчика» → «Загрузить распакованное»
→ папка `extension/`. Firefox: `about:debugging` → «Загрузить временное дополнение» →
`extension-firefox/manifest.json`. Затем открыть `https://arena.ai/text/direct`
и залогиниться.

## Справка по API

```bash
curl http://localhost:9090/help                # HTML-страница
curl http://localhost:9090/help?format=json    # то же в JSON
```

`/help` описывает все эндпоинты, функцию проброса моделей в Copilot и известные
грабли; статус (число моделей, связь с расширением) подставляется живой.

| Метод | Путь | Зачем |
|---|---|---|
| POST | `/v1/chat/completions` | чат в формате OpenAI (stream, tools) |
| GET | `/v1/models` | список моделей (text+image вместе) |
| GET | `/v1/arena/models` | текстовые отдельно от картиночных + uuid + vision |
| GET | `/health` | состояние сервера и расширения |
| POST | `/v1/extension/push` | сюда расширение шлёт куки, токены, модели |
| GET | `/v1/extension/status` | состояние расширения |
| GET | `/v1/extension/command` | long-poll команд (запрос токена) |
| GET | `/help` | эта справка |

## Модели в Copilot

```bash
python3 copilot/install_copilot.py                            # все профили
python3 copilot/install_copilot.py --user-profile "C#" "C++" "DELPHI 7"
```

Подробности — в `copilot/README.md`.

## Проверки

```bash
node tests/test_injector.js              # 6 проверок парсера на фикстуре (738 моделей)
python3 copilot/test_agent_mode.py       # 13 проверок агентного пути (tool_calls)
```

Оба запускаются без аргументов и без внешних зависимостей: `test_injector.js` гоняет
настоящий `extension/injector.js` в node против сохранённых flight-данных,
`test_agent_mode.py` — настоящий `/v1/chat/completions` на заглушке потока arena.
