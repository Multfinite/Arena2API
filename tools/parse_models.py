#!/usr/bin/env python3
"""
parse_models.py — вытаскивает список моделей из сохранённой страницы arena.ai
(HTML с Next.js flight-данными) и генерирует артефакты для Arena2api-fixed.

Использование:
    python3 parse_models.py <page.html> [--out-dir out]

На входе — любой сохранённый HTML страницы arena.ai (text/direct, coding и т.п.).
На выходе:
    out/models_all.json      все модели как есть (массив)
    out/models_text.json     только текстовые, без дублей имён (для вставки в расширение)
    out/models_image.json    только генераторы картинок
    out/models_report.csv    вся таблица для чтения
    out/arena_models.js      статический список для extension/arena_models.js
    out/add_models_console.js  сниппет для консоли сервис-воркера расширения
    out/push_models.sh       curl-команда, кладёт модели прямо на локальный сервер
"""
import argparse
import csv
import json
import os
import re
import sys


def extract_flight(html: str) -> str:
    """Собирает все self.__next_f.push([1,"..."]) чанки в одну строку."""
    chunks = re.findall(r"<script>self\.__next_f\.push\((.*?)\)</script>", html, re.S)
    out = []
    for c in chunks:
        try:
            arr = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(arr, list) and len(arr) == 2 and isinstance(arr[1], str):
            out.append(arr[1])
    return "".join(out)


def slice_json_array(blob: str, start: int) -> str | None:
    """Возвращает подстроку blob, начинающуюся со '[' в позиции start и
    заканчивающуюся закрывающей его ']'. Строки и экранирование учитываются."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(blob)):
        ch = blob[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return blob[start : i + 1]
    return None


def extract_models(html: str) -> list[dict]:
    blob = extract_flight(html)
    idx = blob.find("initialModels")
    if idx < 0:
        raise SystemExit("initialModels не найден в flight-данных (не та страница?)")
    start = blob.find("[", idx)
    seg = slice_json_array(blob, start)
    if not seg:
        raise SystemExit("не удалось найти конец массива initialModels")
    return json.loads(seg)


# ---------------------------------------------------------------- helpers
def out_caps(m: dict) -> dict:
    c = m.get("capabilities", {}).get("outputCapabilities", {})
    return c if isinstance(c, dict) else {}


def in_caps(m: dict) -> dict:
    c = m.get("capabilities", {}).get("inputCapabilities", {})
    return c if isinstance(c, dict) else {}


def norm(m: dict, keep: tuple = ()) -> dict:
    """Формат, который понимает server.py::_update_models.

    keep — белый список outputCapabilities. Нужно из-за особенности сервера:
    chat_completions считает модель генератором картинок, если её имя есть в
    image_models (modality='image'). У текстовых моделей arena, умеющих
    рисовать (например Max), 'image' в выводе есть, поэтому без обрезки они
    уезжали бы в image-режим. keep=() ничего не фильтрует.
    """
    oc = sorted(out_caps(m))
    if keep:
        oc = [c for c in oc if c in keep]
    ic = sorted(in_caps(m))
    return {
        "id": m.get("id", ""),
        "publicName": m.get("publicName", "") or m.get("displayName", ""),
        "capabilities": {"outputCapabilities": oc, "inputCapabilities": ic},
    }


def dedupe(models: list[dict]) -> list[dict]:
    """Убирает дубликаты publicName. Сервер хранит dict по publicName, поэтому
    при совпадении имён побеждает последний элемент списка — кладём «лучший»
    вариант (userSelectable + есть web в выводе) в конец."""
    def score(m: dict) -> tuple:
        return (
            1 if "web" in out_caps(m) else 0,
            1 if m.get("userSelectable") else 0,
        )

    by_name: dict[str, list[dict]] = {}
    for m in models:
        by_name.setdefault(m.get("publicName", ""), []).append(m)

    result = []
    for name in sorted(by_name):
        group = sorted(by_name[name], key=score)  # лучший -> последний
        result.extend(group)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("--out-dir", default="out")
    args = ap.parse_args()

    models = extract_models(open(args.html, encoding="utf-8", errors="replace").read())
    os.makedirs(args.out_dir, exist_ok=True)
    p = lambda n: os.path.join(args.out_dir, n)

    text = [m for m in models if out_caps(m).get("text")]
    text_dd = dedupe(text)

    # Модель, которая уже есть в текстовом списке, нельзя класть в картиночный:
    # server.py считает имя из image_models генератором картинок и ставит
    # modality="image" (is_image = model_name in store.image_models).
    text_ids = {m["id"] for m in text_dd}
    image = [m for m in models if out_caps(m).get("image") and m["id"] not in text_ids]
    image_dd = dedupe(image)

    json.dump(models, open(p("models_all.json"), "w"), ensure_ascii=False, indent=1)
    json.dump([norm(m, ("text", "web")) for m in text_dd],
              open(p("models_text.json"), "w"), ensure_ascii=False, indent=1)
    json.dump([norm(m, ("image", "video")) for m in image_dd],
              open(p("models_image.json"), "w"), ensure_ascii=False, indent=1)

    with open(p("models_report.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "publicName", "displayName", "organization", "provider",
                    "userSelectable", "output", "input"])
        for m in models:
            w.writerow([m.get("id"), m.get("publicName"), m.get("displayName"),
                        m.get("organization"), m.get("provider"),
                        m.get("userSelectable"),
                        "+".join(sorted(out_caps(m))),
                        "+".join(sorted(in_caps(m)))])

    # Статический список для расширения: текстовые без 'image' в выводе
    # (иначе сервер посчитает их генераторами картинок), плюс генераторы картинок.
    static = [norm(m, ("text", "web")) for m in text_dd] + \
             [norm(m, ("image", "video")) for m in image_dd]
    js = (
        "// Сгенерировано parse_models.py — статический список моделей arena.ai.\n"
        "// Подключается в extension/manifest.json перед injector.js.\n"
        "// injector.js использует его как fallback, если не смог прочитать\n"
        "// window.__next_f на живой странице.\n"
        "window.__ARENA_STATIC_MODELS__ = " + json.dumps(static, ensure_ascii=False) + ";\n"
    )
    open(p("arena_models.js"), "w").write(js)

    # Сниппет для консоли сервис-воркера расширения
    console_js = (
        "// Вставить в консоль сервис-воркера расширения\n"
        "// (chrome://extensions -> Arena2API -> «service worker» / «Проверить представления»)\n"
        "// Кладёт модели в chrome.storage.local, затем перезагрузите вкладку arena.ai\n"
        "// или нажмите Push в попапе расширения.\n"
        "const MODELS = " + json.dumps([norm(m, ("text", "web")) for m in text_dd],
                                ensure_ascii=False) + "\n"
        "chrome.storage.local.set({ customModels: MODELS }, () => {\n"
        "  console.log('customModels saved:', MODELS.length);\n"
        "  chrome.runtime.sendMessage({ type: 'MODELS_UPDATED', models: MODELS },\n"
        "    (r) => console.log('MODELS_UPDATED ->', r, chrome.runtime.lastError || ''));\n"
        "});\n"
    )
    open(p("add_models_console.js"), "w").write(console_js)

    # curl-вариант: положить модели прямо на запущенный сервер
    payload = {"models": [norm(m, ("text", "web")) for m in text_dd]
               + [norm(m, ("image", "video")) for m in image_dd]}
    json.dump(payload, open(p("push_payload.json"), "w"), ensure_ascii=False)
    open(p("push_models.sh"), "w").write(
        "#!/usr/bin/env bash\n"
        "# Кладёт список моделей на локальный сервер в обход расширения.\n"
        "# Работает, пока расширение живо (store.active: пуш не старше 120 с).\n"
        'curl -s -X POST "${SERVER:-http://127.0.0.1:9090}/v1/extension/push" \\\n'
        '  -H "Content-Type: application/json" \\\n'
        '  --data-binary @"$(dirname "$0")/push_payload.json"\n'
    )
    os.chmod(p("push_models.sh"), 0o755)

    print(f"всего моделей            : {len(models)}")
    print(f"уникальных id            : {len({m['id'] for m in models})}")
    print(f"уникальных publicName    : {len({m.get('publicName') for m in models})}")
    print(f"текстовых                : {len(text)} -> после дедупа {len(text_dd)}")
    print(f"генераторов изображений  : {len(image)} -> после дедупа {len(image_dd)}")
    print(f"userSelectable=true      : {sum(1 for m in models if m.get('userSelectable'))}")
    print("артефакты ->", args.out_dir)


if __name__ == "__main__":
    main()
