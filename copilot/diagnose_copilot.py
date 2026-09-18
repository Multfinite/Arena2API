#!/usr/bin/env python3
"""
diagnose_copilot.py — почему модели не появились в Copilot.

    python3 diagnose_copilot.py
    python3 diagnose_copilot.py --user-profile "C#" "DELPHI 7"

Ничего не меняет: проходит по профилям VS Code, проверяет chatLanguageModels.json,
settings.json и доступность сервера, печатает вердикт и команду починки.
"""
import argparse
import json
import os
import pathlib
import re
import sys
import urllib.request

from install_copilot import PROVIDER, SETTINGS_KEY, VENDOR, discover, select


def ok(t):
    print("  [ok]  " + t)


def bad(t):
    print("  [!]   " + t)


def info(t):
    print("  [..]  " + t)


def check_server(api: str) -> bool:
    print("\nСервер " + api)
    try:
        with urllib.request.urlopen(api + "/health", timeout=5) as r:
            h = json.loads(r.read())
    except Exception as e:
        bad("недоступен: " + str(e) + "  ->  python3 server.py")
        return False
    ok("health ok, моделей " + str(h.get("model_count")))
    if h.get("extension_connected"):
        ok("расширение на связи")
    else:
        bad("расширение НЕ на связи — чат вернёт 503 (модели в пикере при этом будут)")
    try:
        with urllib.request.urlopen(api + "/v1/arena/models", timeout=5) as r:
            ok("/v1/arena/models: " + json.dumps(json.loads(r.read())["counts"]))
    except Exception:
        bad("нет /v1/arena/models — установщик отработает по /v1/models, "
            "но без разделения text/image")
    return True


def check_profile(label: str, name: str, d: pathlib.Path) -> bool:
    print("\n  " + name + "  (" + label + ")")
    healthy = True
    lm, st = d / "chatLanguageModels.json", d / "settings.json"
    if not lm.exists():
        bad("chatLanguageModels.json нет — установщик сюда не писал")
        return False
    try:
        groups = json.loads(lm.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        bad("файл не JSON: " + str(e))
        return False
    if not isinstance(groups, list):
        bad("корень не массив — VS Code такое не читает")
        return False
    info("группы: " + ", ".join(str(g.get("name")) for g in groups if isinstance(g, dict)))

    ours = [g for g in groups if isinstance(g, dict) and g.get("name") == PROVIDER]
    if not ours:
        bad("секции «" + PROVIDER + "» нет")
        return False
    g = ours[0]

    if g.get("vendor") != VENDOR:
        bad("vendor = " + str(g.get("vendor")) + ", а нужен '" + VENDOR + "'. "
            "С 'customendpoint' это другой провайдер (Messages API) — модели из "
            "массива models не появятся. Лечится переустановкой.")
        healthy = False
    else:
        ok("vendor = " + VENDOR)

    if g.get("url"):
        bad("у группы задан url — Copilot уйдёт в автообнаружение GET <url>/models "
            "и проигнорирует список models")
        healthy = False
    else:
        ok("url у группы нет (список models будет прочитан)")

    models = g.get("models") or []
    if not models:
        bad("моделей в секции 0")
        healthy = False
    else:
        ok("моделей: " + str(len(models)))
        m = models[0]
        for field in ("id", "name", "url", "maxOutputTokens"):
            if not m.get(field):
                bad("у модели нет обязательного поля '" + field + "'")
                healthy = False
        if "toolCalling" not in m or "vision" not in m:
            bad("у модели нет toolCalling/vision — Copilot может отбрить запись")
            healthy = False
        if m.get("url"):
            ok("url модели: " + str(m["url"]))
        if not m.get("toolCalling"):
            info("toolCalling=false — в Agent Mode модель не появится, только Ask")

    if st.exists():
        if re.search(r'"%s"\s*:' % re.escape(SETTINGS_KEY), st.read_text(encoding="utf-8")):
            ok("settings.json: " + SETTINGS_KEY + " задан")
        else:
            bad("settings.json: нет " + SETTINGS_KEY + " — Agent Mode упадёт с "
                "'No utility model is configured'")
            healthy = False
    else:
        info("settings.json нет")
    return healthy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("ARENA_API", "http://127.0.0.1:9090"))
    ap.add_argument("--user-profile", nargs="+", default=None, metavar="NAME")
    ap.add_argument("--user-dir", action="append", default=None)
    args = ap.parse_args()

    print("Диагностика Copilot BYOK. Секция: «" + PROVIDER + "», vendor: " + VENDOR)
    srv_ok = check_server(args.api)

    targets = discover(args.user_dir)
    if not targets:
        bad("каталоги VS Code не найдены")
        return 1
    targets = select(targets, args.user_profile)
    print("\nПрофили")
    results = [(n, check_profile(l, n, d)) for l, d, n in targets]

    print("\nВердикт")
    good = [n for n, r in results if r]
    if good:
        ok("конфиг корректен: " + ", ".join(good))
        info("если моделей всё равно не видно — ПОЛНЫЙ перезапуск VS Code "
             "(закрыть все окна), затем чат -> Manage Language Models")
    else:
        bad("ни в одном профиле конфиг не готов")
    if srv_ok:
        print("\nПочинка:  python3 copilot/install_copilot.py"
              + (' --user-profile ' + " ".join('"%s"' % a for a in args.user_profile)
                 if args.user_profile else ""))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
