"""Справка /help для Arena2API.

Отдельный модуль: server.py не раздувается, а источник правды один —
help_payload() отдаёт dict, render_html() рисует из него страницу.
"""

TOOL_TAG_OPEN = "<" + "tool_call>"
TOOL_TAG_CLOSE = "</" + "tool_call>"


def help_payload(store, api_key_required: bool) -> dict:
    st = store.status()
    return {
        "name": "Arena2API",
        "version": "1.0.0",
        "summary": (
            "arena.ai -> OpenAI-совместимый API. Клиент шлёт запросы как в OpenAI, "
            "сервер перекладывает их в arena.ai, а куки, reCAPTCHA-токены и список "
            "моделей приходят из браузерного расширения."
        ),
        "status": {
            "server": "ok",
            "extension_connected": st["active"],
            "models_text": st["text_models"],
            "models_image": st["image_models"],
            "recaptcha_tokens": st["v3_tokens"],
            "auth_cookie": st["has_auth"],
            "api_key_required": api_key_required,
            "note": ("extension_connected=false — пуша от расширения не было последние "
                     "120 секунд, чат вернёт 503."),
        },
        "endpoints": _endpoints(),
        "copilot": _copilot(),
        "gotchas": _gotchas(),
    }


def _endpoints() -> list:
    return [
        {
            "method": "POST",
            "path": "/v1/chat/completions",
            "purpose": ("OpenAI-совместимый чат: model, messages, stream, tools, "
                        "temperature, max_tokens."),
            "example": ("curl http://localhost:9090/v1/chat/completions "
                        "-H 'Content-Type: application/json' "
                        "-d '{\"model\":\"Max\",\"messages\":[{\"role\":\"user\","
                        "\"content\":\"Привет\"}],\"stream\":true}'"),
            "notes": [
                "model резолвится нечётко: 'max' и 'arena-ai/Max' дают 'Max'; короткое "
                "имя может попасть не в ту модель — лучше полный publicName.",
                "temperature и max_tokens игнорируются: arena.ai их не принимает.",
                "tools эмулируются: схемы вставляются в промпт, ответ парсится из "
                + TOOL_TAG_OPEN + "..." + TOOL_TAG_CLOSE + ".",
            ],
        },
        {
            "method": "GET",
            "path": "/v1/models",
            "purpose": "Список моделей в формате OpenAI (text + image вместе).",
            "example": "curl http://localhost:9090/v1/models",
            "notes": ["Если расширение не подключено, вернётся заглушка "
                      "'waiting-for-extension'."],
        },
        {
            "method": "GET",
            "path": "/v1/arena/models",
            "purpose": ("Подробный список: текстовые модели отдельно от генераторов "
                        "картинок, у каждой uuid и признак vision."),
            "example": "curl http://localhost:9090/v1/arena/models",
            "notes": ["Именно его использует copilot/install_copilot.py — иначе "
                      "картиночные модели попали бы в чат и сломали его."],
        },
        {
            "method": "GET",
            "path": "/health",
            "purpose": "Состояние сервера и расширения.",
            "example": "curl http://localhost:9090/health",
            "notes": ["Дублируется на '/'."],
        },
        {
            "method": "POST",
            "path": "/v1/extension/push",
            "purpose": ("Сюда расширение шлёт cookies, reCAPTCHA-токены и список "
                        "моделей. Вручную вызывать не нужно."),
            "example": "# расширение делает это само каждые 30 секунд",
            "notes": ["Можно положить модели вручную: tools/models/push_payload.json."],
        },
        {
            "method": "GET",
            "path": "/v1/extension/status",
            "purpose": "Состояние расширения глазами сервера.",
            "example": "curl http://localhost:9090/v1/extension/status",
            "notes": [],
        },
        {
            "method": "GET",
            "path": "/v1/extension/command",
            "purpose": ("Long-poll очередь команд для расширения (запрос "
                        "reCAPTCHA-токена по требованию)."),
            "example": "# использует расширение",
            "notes": [],
        },
    ]


def _copilot() -> dict:
    return {
        "title": "Проброс моделей в GitHub Copilot (VS Code)",
        "why": ("Copilot умеет BYOK-провайдеров через файл chatLanguageModels.json. "
                "Раз arena2api отдаёт OpenAI-совместимый API, все модели можно "
                "прописать туда одним запуском."),
        "command": "python3 copilot/install_copilot.py",
        "flags": [
            ["--user-profile \"C#\" \"C++\" \"DELPHI 7\"",
             "только перечисленные профили VS Code; без флага — все профили"],
            ["--list-profiles", "показать найденные профили и их каталоги"],
            ["--models Max,gpt-5.2", "не все модели, а выбранные"],
            ["--dry-run", "показать, что будет сделано"],
            ["--uninstall", "убрать секцию Arena.AI из профилей"],
            ["--api http://host:9090", "если сервер на другой машине"],
        ],
        "behaviour": [
            "Каждый профиль: сначала бэкап chatLanguageModels.json "
            "(chatLanguageModels.json.bak-<метка времени>, хранится 5 последних).",
            "Секция провайдера «Arena.AI» ПЕРЕЗАПИСЫВАЕТСЯ свежим содержимым; "
            "чужие группы (OpenRouter, OpenCode Zen и т.п.) не трогаются.",
            "В settings.json профиля добавляется "
            "\"chat.byokUtilityModelDefault\": \"mainAgent\" — без него Agent Mode "
            "падает с 'No utility model is configured'.",
        ],
        "config_shape": (
            'vendor: "customoai" — провайдер OpenAI-совместимых эндпоинтов '
            "(CustomOAI.toLowerCase()). \"customendpoint\" — другой провайдер "
            "(Messages API), с ним модели из массива models не появляются. "
            "url задаётся у каждой модели, но НЕ у группы: при url у группы "
            "Copilot уходит в автообнаружение GET <url>/models и игнорирует "
            "список models."
        ),
        "agent_mode": (
            "Agent Mode работает на обычной модели: отдельной агентной модели у "
            "arena.ai нет, сервер всегда шлёт mode='direct-battle'. Инструменты "
            "эмулируются, поэтому каждый ход агента — это один reCAPTCHA-токен "
            "из видимой вкладки arena.ai."
        ),
        "after": "Полный перезапуск VS Code (не Reload Window), затем пикер моделей.",
        "diagnostics": "python3 copilot/diagnose_copilot.py",
    }


def _gotchas() -> list:
    return [
        "Каждый запрос к arena.ai требует свежий reCAPTCHA-токен, его берёт "
        "расширение из ВИДИМОЙ вкладки arena.ai. Нет вкладки — 503.",
        "Инлайн-комплиты Copilot BYOK не затрагивает: свой провайдер работает "
        "только в чате и агентном режиме.",
        "Список моделей живёт в памяти сервера. После перезапуска расширение "
        "запушит его заново само (оно пушится каждые 30 секунд).",
        "Профили VS Code: файл chatLanguageModels.json лежит в каталоге профиля, "
        "а имена профилей — в globalStorage/state.vscdb (ключ userDataProfiles), "
        "не в имени папки.",
    ]


CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;padding:28px;background:#0b1120;color:#e5e7eb;
 font:14px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;max-width:1000px}
h1{font-size:21px;margin:0 0 4px;color:#fff}h2{font-size:15px;margin:26px 0 10px;color:#fff}
.sub{color:#8b95a7;margin:0 0 18px}
.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.card{background:#151d2e;border:1px solid #233047;border-radius:10px;padding:11px 13px}
.card .l{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:#8b95a7}
.card .v{font-size:20px;font-weight:650;color:#fff}
.ok{color:#34d399}.warn{color:#fbbf24}.err{color:#f87171}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:7px 9px;border-bottom:1px solid #1c2739;text-align:left;vertical-align:top}
th{color:#8b95a7;font-size:11px;text-transform:uppercase;letter-spacing:.6px}
code{background:#0d1424;border:1px solid #233047;border-radius:5px;padding:1px 5px;
 font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#9fe8c8}
pre{background:#0d1424;border:1px solid #233047;border-radius:8px;padding:10px;
 overflow:auto;font-size:12px;margin:6px 0}
.m{display:inline-block;min-width:44px;font-weight:700}
.get{color:#60a5fa}.post{color:#34d399}
ul{margin:6px 0;padding-left:20px}li{margin:3px 0}
.note{background:#132033;border:1px solid #24405f;border-radius:10px;padding:10px 12px;
 font-size:12.5px;color:#9fb3cc}
"""


def _esc(x) -> str:
    import html
    return html.escape(str(x))


def render_html(data: dict) -> str:
    st = data["status"]
    p = []
    p.append("<!DOCTYPE html><html lang=ru><meta charset=utf-8>")
    p.append("<title>" + _esc(data["name"]) + " — API</title><style>" + CSS + "</style>")
    p.append("<h1>" + _esc(data["name"]) + " <span class=sub>v" + _esc(data["version"]) + "</span></h1>")
    p.append("<p class=sub>" + _esc(data["summary"]) + "</p>")
    p.append('<div class=grid>')
    cards = [
        ("Расширение", "подключено" if st["extension_connected"] else "нет связи",
         "ok" if st["extension_connected"] else "err"),
        ("Текстовых моделей", st["models_text"], "ok"),
        ("Картиночных", st["models_image"], "ok"),
        ("reCAPTCHA токенов", st["recaptcha_tokens"],
         "ok" if st["recaptcha_tokens"] else "warn"),
        ("API key", "нужен" if st["api_key_required"] else "не нужен", "ok"),
    ]
    for label, val, cls in cards:
        p.append('<div class=card><div class=l>' + _esc(label) + '</div>'
                 '<div class="v ' + cls + '">' + _esc(val) + "</div></div>")
    p.append("</div>")
    if not st["extension_connected"]:
        p.append('<p class=note>' + _esc(st["note"]) + "</p>")
    return "\n".join(p) + _html_rest(data)


def _html_rest(data: dict) -> str:
    p = ["<h2>Эндпоинты</h2><table><tr><th>Метод</th><th>Путь</th><th>Зачем</th></tr>"]
    for e in data["endpoints"]:
        cls = "get" if e["method"] == "GET" else "post"
        p.append("<tr><td><span class='m " + cls + "'>" + _esc(e["method"]) + "</span></td>"
                 "<td><code>" + _esc(e["path"]) + "</code>"
                 + ("<pre>" + _esc(e["example"]) + "</pre>" if e.get("example") else "")
                 + "".join("<div class=note>" + _esc(n) + "</div>" for n in e.get("notes", []))
                 + "</td><td>" + _esc(e["purpose"]) + "</td></tr>")
    p.append("</table>")

    c = data["copilot"]
    p.append("<h2>" + _esc(c["title"]) + "</h2>")
    p.append("<p>" + _esc(c["why"]) + "</p>")
    p.append("<pre>" + _esc(c["command"]) + "</pre>")
    p.append("<table><tr><th>Флаг</th><th>Что делает</th></tr>")
    for flag, desc in c["flags"]:
        p.append("<tr><td><code>" + _esc(flag) + "</code></td><td>" + _esc(desc) + "</td></tr>")
    p.append("</table>")
    p.append("<p><b>Что происходит при запуске</b></p><ul>")
    for b in c["behaviour"]:
        p.append("<li>" + _esc(b) + "</li>")
    p.append("</ul>")
    p.append('<p class=note>' + _esc(c["config_shape"]) + "</p>")
    p.append("<p>" + _esc(c["agent_mode"]) + "</p>")
    p.append("<p>После установки: " + _esc(c["after"]) + " Диагностика: <code>"
             + _esc(c["diagnostics"]) + "</code></p>")

    p.append("<h2>Грабли</h2><ul>")
    for g in data["gotchas"]:
        p.append("<li>" + _esc(g) + "</li>")
    p.append("</ul></html>")
    return "\n".join(p)
