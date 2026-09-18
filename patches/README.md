# patches/ — что изменено относительно upstream

Один патч, который превращает чистый репозиторий
[`kekurttel/arena2api-fixed`](https://github.com/kekurttel/arena2api-fixed) в этот проект.

| | |
|---|---|
| upstream | `https://github.com/kekurttel/arena2api-fixed` |
| базовый коммит | `5feabdae396df834944c476571cade9617db5182` |
| размер | 2321 строка, 18 файлов |

## Как наложить

```bash
git clone https://github.com/kekurttel/arena2api-fixed && cd arena2api-fixed
patch -p1 -i /путь/к/Arena2API/patches/arena2api-vs-upstream.patch
```

Флаг `-i` обязателен: без него `patch` читает сам патч из stdin и молча завершается
с кодом 0, ничего не сделав.

## Состав

| Файл | Что сделано |
|---|---|
| `server.py` | +47/−0: эндпоинт `GET /v1/arena/models` (текст/картинки/vision отдельно), роут `/help`, импорт `HTMLResponse` и `help_page` |
| `help_page.py` | новый: единый источник правды для `/help` — HTML и JSON из одного словаря |
| `extension/injector.js`, `extension-firefox/injector.js` | ±96: парсинг списка моделей из живой страницы, а не из зашитого дампа |
| `extension/arena_models.js`, `extension-firefox/arena_models.js` | новые: статический fallback-список |
| `extension/manifest.json`, `extension-firefox/manifest.json` | `arena_models.js` добавлен в `content_scripts` (в `world: MAIN` до `injector.js`) |
| `copilot/` | новая папка: `install_copilot.py`, `diagnose_copilot.py`, `test_agent_mode.py`, `README.md` — всё, что касается GitHub Copilot |
| `tools/parse_models.py` | новый: парсер дампа страницы в JSON/CSV |
| `tests/` | новые: `test_injector.js` (6 проверок парсера), `flight_sample.txt` (фикстура) |
| `README.md` | переписан под собранный проект |
| `extension-firefox/content.js.bak`, `uuid-finder.md` | удалены: бэкап-мусор и дубль `scripts/uuid-finder.js` |

## Чего в патче нет

`tools/models/*` — дампы моделей (~30 тыс. строк JSON/CSV). Это артефакты, они
лежат в проекте готовыми и пересоздаются из вашего дампа страницы:

```bash
python3 tools/parse_models.py /путь/к/direct.html
```

## Проверено

Патч наложен на свежий клон upstream `5feabda` (`patch -p1 -i`, 0 отклонённых ханков,
0 файлов `.rej`/`.orig`), затем дерево сверено с этим проектом через `diff -r`:
**идентично** (кроме самих `patches/` и `tools/models/`, которые в патч не входят).
MD5 всех пяти изменённых файлов после наложения совпадает с рабочими копиями.
