# copilot/ — проброс моделей Arena2API в GitHub Copilot

Отдельная папка: здесь только то, что касается VS Code Copilot. Сервер и расширение
лежат на уровень выше и от этих скриптов не зависят.

## Одна команда

```bash
python3 copilot/install_copilot.py                     # все профили VS Code
python3 copilot/install_copilot.py --user-profile "C#" "C++" "DELPHI 7"
python3 copilot/install_copilot.py --list-profiles     # что найдено
python3 copilot/diagnose_copilot.py                    # если моделей не видно
```

После установки — ПОЛНЫЙ перезапуск VS Code (закрыть все окна, не `Reload Window`),
затем в чате пикер моделей → **Arena.AI**.

## Что делает установщик

Для каждого профиля:

1. **Бэкап** `chatLanguageModels.json` → `chatLanguageModels.json.bak-<метка времени>`
   (хранится 5 последних, `--keep-backups N`).
2. **Перезапись секции `Arena.AI`** свежим содержимым. Чужие группы
   (OpenRouter, OpenCode Zen и т.п.) не трогаются. Повторный запуск не плодит дубли.
3. В `settings.json` профиля добавляется `"chat.byokUtilityModelDefault": "mainAgent"`
   — без него Agent Mode падает с `No utility model is configured for 'copilot-utility-small'`.
   Файл не переформатируется: комментарии и хвостовые запятые выживают.

### Профили

Имена профилей VS Code хранятся не в имени папки, а в
`<User>/globalStorage/state.vscdb` (таблица `ItemTable`, ключ `userDataProfiles`),
поэтому установщик читает именно её. `--user-profile` принимает имена; без флага
обрабатываются все профили, включая дефолтный. Каталог пользователя определяется
сам (Windows `%APPDATA%\Code\User`, macOS `~/Library/Application Support/Code/User`,
Linux `~/.config/Code/User`, плюс Insiders/VSCodium/Cursor); можно задать вручную
через `--user-dir`.

## Формат записи — и почему именно такой

```json
{
  "name": "Arena.AI",
  "vendor": "customoai",
  "apiKey": "arena-local",
  "models": [
    { "id": "Max", "name": "Max",
      "url": "http://127.0.0.1:9090/v1/chat/completions",
      "toolCalling": true, "vision": true,
      "maxInputTokens": 128000, "maxOutputTokens": 32768 }
  ]
}
```

Две вещи, из-за которых модели раньше не появлялись (проверено по исходникам
`extensions/copilot/src/extension/byok/vscode-node/`):

| Ошибка | Почему ломается |
|---|---|
| `"vendor": "customendpoint"` | это **другой** провайдер — CustomEndpoint для Messages API. Для OpenAI Chat Completions нужен `customoai` (`CustomOAI.toLowerCase()`), иначе массив `models` не читается тем провайдером |
| `url` на уровне группы | `getAllModels()` при `configuration.url` уходит в автообнаружение `GET <url>/models` и **полностью игнорирует** массив `models`; к тому же `resolveModelCapabilities` для неизвестных моделей возвращает `undefined`, и они отбрасываются |

`url` поэтому задан у каждой модели, а у группы его нет. `apiKey` можно заменить на
`"${input:chat.lm.secret.XXX}"` через `--api-key` — сервер без `API_KEY` его не проверяет.

## Флаги

| Флаг | Что делает |
|---|---|
| `--user-profile "C#" "C++"` | только эти профили |
| `--list-profiles` | список найденных профилей и их файлов |
| `--models Max,gpt-5.2` | не все модели, а выбранные |
| `--user-dir <путь>` | каталог пользователя вручную (можно несколько) |
| `--api http://host:9090` | сервер на другой машине |
| `--api-key ...` | значение `apiKey` группы |
| `--keep-backups N` | сколько бэкапов хранить (по умолчанию 5) |
| `--no-tools` | `toolCalling: false` → только Ask-режим |
| `--max-in / --max-out` | лимиты токенов |
| `--no-settings` | не трогать `settings.json` |
| `--dry-run` | показать, ничего не меняя |
| `--uninstall` | убрать секцию `Arena.AI` (с бэкапом) |

## Agent Mode

Отдельной «агентной модели» у arena.ai нет: в данных страницы (`initialModels`,
1074 записи) нет модели-оркестратора, есть обычные модели с `rankByModality`
(`chat / webdev / image / search`). Сервер всегда шлёт `mode: "direct-battle"`,
так что агентом работает обычная модель + инструменты Copilot.

Инструменты эмулируются: схемы вставляются в промпт, ответ модели парсится из <tool_call>...</tool_call>`, а клиенту отдаются нормальные OpenAI `tool_calls`.

Что это значит на практике:

* Agent Mode работает, но надёжность вызовов зависит от того, слушается ли модель
  формата. Берите сильные модели (`Max`, `claude-opus-4-8`, `gpt-5.2`).
* Каждый ход агента — один reCAPTCHA-токен из видимой вкладки arena.ai (таймаут 15 с).
  Много ходов → медленнее и возможен 429. Вкладка arena.ai должна быть открыта.
* `temperature` сервер игнорирует (Copilot шлёт `0.1`, для reasoning-моделей у прямых
  провайдеров это обычно 400 — здесь такого нет).
* Инлайн-комплиты Copilot BYOK не затрагивает — только чат и агентный режим.

## Проверено запуском кода

| Проверка | Результат |
|---|---|
| `--list-profiles` на стенде с профилями `C#`, `C++`, `DELPHI 7` | имена прочитаны из `state.vscdb`, 4 цели (включая Default) |
| Установка во все профили | в каждом секция `Arena.AI`: `vendor=customoai`, 494 модели, `url` у группы отсутствует, чужие группы (OpenRouter, OpenCode Zen) целы |
| Бэкапы | созданы в каждом профиле, внутри — исходные 2 группы |
| Повторный запуск | «старая заменена», групп по-прежнему 3, дублей нет, бэкапов стало 2 |
| `--user-profile "C#" "DELPHI 7"` | затронуты только они; файлы `C++` и Default побайтово не изменились |
| Несуществующий профиль | понятная ошибка со списком доступных |
| `--uninstall` одного профиля | секция удалена, ключ из `settings.json` удалён, комментарии целы |
| `settings.json` с комментариями и хвостовой запятой | ключ добавлен/удалён, JSONC парсится |
| `diagnose_copilot.py` | находит отсутствие секции и неверный `vendor` |
| `test_agent_mode.py` | 13/13 PASS: `tool_calls` с `id`/`type`/аргументами, `finish_reason: tool_calls`, `modality: chat`, `modelAId` = UUID `Max`, второй ход с результатом инструмента |

Не проверено: живой диалог в VS Code — нужны ваш браузер с сессией arena.ai и
установленный Copilot.
