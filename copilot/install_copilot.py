#!/usr/bin/env python3
"""
install_copilot.py — пробросить ВСЕ модели Arena2API в GitHub Copilot (VS Code).

    python3 install_copilot.py                          # все профили
    python3 install_copilot.py --user-profile "C#" "C++" "DELPHI 7"
    python3 install_copilot.py --list-profiles
    python3 install_copilot.py --dry-run
    python3 install_copilot.py --uninstall

Что делает для каждого профиля:
  1. бэкапит chatLanguageModels.json  ->  chatLanguageModels.json.bak-<метка времени>
  2. ПЕРЕЗАПИСЫВАЕТ секцию провайдера «Arena.AI» свежим содержимым
     (чужие группы — OpenRouter, OpenCode Zen и т.д. — не трогает)
  3. добавляет в settings.json "chat.byokUtilityModelDefault": "mainAgent"

Формат группы строго по коду Copilot (extensions/copilot/.../customOAIProvider.ts):
  vendor = "customoai"  (CustomOAI.toLowerCase()) — провайдер OpenAI-совместимых
  эндпоинтов. "customendpoint" — ДРУГОЙ провайдер (Messages API), с ним модели
  из массива models не появляются.
  url у группы НЕ задаётся: при configuration.url провайдер уходит в
  автообнаружение GET <url>/models и полностью игнорирует массив models.
"""
import argparse
import json
import os
import pathlib
import platform
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request

PROVIDER = "Arena.AI"
VENDOR = "customoai"
LEGACY_NAMES = {"Arena.ai (local)", "Arena.ai", "Arena2API"}
LEGACY_VENDORS = {"customendpoint"}          # неправильный vendor из прошлых версий
SETTINGS_KEY = "chat.byokUtilityModelDefault"
SETTINGS_VALUE = "mainAgent"
VARIANTS = ["Code", "Code - Insiders", "VSCodium", "Cursor"]


# --------------------------------------------------------------------- сервер
def http_json(url: str, timeout: int = 30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_models(api: str):
    base = api.rstrip("/")
    try:
        return http_json(base + "/v1/arena/models")["text"], "v1/arena/models"
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit("сервер " + base + " недоступен: " + str(e) +
                         "\nПоднимите:  python3 server.py")
    print("[!] /v1/arena/models нет — беру /v1/models; генераторы картинок "
          "отличить нельзя, в чате они работать не будут", file=sys.stderr)
    d = http_json(base + "/v1/models")
    return [{"id": m["id"], "vision": False} for m in d.get("data", [])], "v1/models"


# --------------------------------------------------------------------- профили
def variant_roots():
    sysname = platform.system().lower()
    home = pathlib.Path.home()
    if sysname == "windows":
        root = pathlib.Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    elif sysname == "darwin":
        root = home / "Library" / "Application Support"
    else:
        root = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return [root / v / "User" for v in VARIANTS]


def read_profile_names(user_dir: pathlib.Path):
    """id профиля -> имя. Имена лежат в state.vscdb (ключ 'userDataProfiles'),
    а не в имени папки: <User>/profiles/<id>/."""
    db = user_dir / "globalStorage" / "state.vscdb"
    if not db.exists():
        return {}
    tmp = None
    try:
        # читаем копию: VS Code держит базу открытой
        fd, tmp = tempfile.mkstemp(suffix=".vscdb")
        os.close(fd)
        shutil.copy2(db, tmp)
        con = sqlite3.connect("file:" + tmp + "?mode=ro", uri=True)
        try:
            row = con.execute("SELECT value FROM ItemTable WHERE key='userDataProfiles'").fetchone()
        finally:
            con.close()
        if not row:
            return {}
        raw = row[0]
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        out = {}
        for p in json.loads(raw):
            loc = p.get("location", "")
            if "://" in str(loc):                 # старые версии писали полный URI
                loc = str(loc).rstrip("/").rsplit("/", 1)[-1]
            if p.get("name") and loc:
                out[str(loc)] = p["name"]
        return out
    except Exception as e:
        print("[!] не прочитал state.vscdb (" + str(e) + ") — имена профилей "
              "будут неизвестны, сопоставление по id папки", file=sys.stderr)
        return {}
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def discover(explicit_user_dirs=None):
    """Список целей: (метка, каталог, имя профиля)."""
    roots = [pathlib.Path(p).expanduser() for p in explicit_user_dirs] if explicit_user_dirs \
        else [d for d in variant_roots() if d.is_dir()]
    targets = []
    for root in roots:
        targets.append((root.parent.name + "/Default", root, "Default"))
        names = read_profile_names(root)
        prof = root / "profiles"
        if prof.is_dir():
            for d in sorted(p for p in prof.iterdir() if p.is_dir()):
                if d.name == "builtin":
                    continue
                targets.append((root.parent.name + "/" + d.name, d, names.get(d.name, d.name)))
    return targets


def select(targets, wanted):
    if not wanted:
        return targets
    want = {w.strip().lower() for w in wanted}
    picked = [t for t in targets if t[2].strip().lower() in want]
    if not picked:
        known = ", ".join(sorted({t[2] for t in targets})) or "—"
        raise SystemExit("профили " + ", ".join(wanted) + " не найдены.\n"
                         "Доступные: " + known + "\n"
                         "Смотреть список:  python3 install_copilot.py --list-profiles")
    missing = want - {t[2].strip().lower() for t in picked}
    if missing:
        print("[!] не найдены профили: " + ", ".join(sorted(missing)), file=sys.stderr)
    return picked


# --------------------------------------------------------------------- запись
def atomic_write(path: pathlib.Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        shutil.move(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def backup(path: pathlib.Path, keep: int):
    """Бэкап моделей профиля. Возвращает имя бэкапа или None."""
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = path.with_name(path.name + ".bak-" + stamp)
    n = 1
    while bak.exists():
        bak = path.with_name(path.name + ".bak-" + stamp + "-" + str(n))
        n += 1
    shutil.copy2(path, bak)
    old = sorted(path.parent.glob(path.name + ".bak-*"), reverse=True)
    for stale in old[keep:]:
        stale.unlink()
    return bak.name


def build_group(args, models):
    url = args.url or args.api.rstrip("/") + "/v1/chat/completions"
    return {
        "name": PROVIDER,
        "vendor": VENDOR,
        "apiKey": args.api_key,
        # url у группы НЕ пишем — см. докстринг
        "models": [
            {
                "id": m["id"],
                "name": m["id"],
                "url": url,
                "toolCalling": not args.no_tools,
                "vision": bool(m.get("vision")),
                "maxInputTokens": args.max_in,
                "maxOutputTokens": args.max_out,
            }
            for m in models
        ],
    }


def overwrite_section(path: pathlib.Path, group, remove=False):
    """Перезаписывает секцию Arena.AI, остальные группы сохраняет как есть."""
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    if raw.strip():
        try:
            groups = json.loads(raw)
        except json.JSONDecodeError as e:
            raise SystemExit(str(path) + ": не JSON (" + str(e) + ") — не трогаю")
        if not isinstance(groups, list):
            raise SystemExit(str(path) + ": корень не массив — не трогаю")
    else:
        groups = []

    kept, dropped = [], 0
    for g in groups:
        if isinstance(g, dict) and (
            g.get("name") == PROVIDER
            or g.get("name") in LEGACY_NAMES
            or (g.get("name") in LEGACY_NAMES | {PROVIDER} and g.get("vendor") in LEGACY_VENDORS)
        ):
            dropped += 1
            continue
        kept.append(g)
    if remove:
        return kept, dropped
    kept.append(group)
    return kept, dropped


def patch_settings(path: pathlib.Path, remove=False):
    if not path.exists():
        if remove:
            return "нет файла"
        atomic_write(path, json.dumps({SETTINGS_KEY: SETTINGS_VALUE}, indent=2) + "\n")
        return "создан"
    raw = path.read_text(encoding="utf-8")
    found = re.search(r'"%s"\s*:\s*"([^"]*)"' % re.escape(SETTINGS_KEY), raw)
    if remove:
        if not found:
            return "ключа нет"
        lines = raw.splitlines(keepends=True)
        for i, ln in enumerate(lines):
            if re.match(r'\s*"%s"\s*:\s*"[^"]*",?\s*$' % re.escape(SETTINGS_KEY), ln):
                del lines[i]
                for j in range(i - 1, -1, -1):
                    prev = lines[j].rstrip()
                    if not prev.strip() or prev.strip().startswith(("//", "/*", "*")):
                        continue
                    if prev.endswith(",") and "".join(lines[j + 1:]).lstrip().startswith("}"):
                        lines[j] = prev[:-1] + lines[j][len(prev):]
                    break
                atomic_write(path, "".join(lines))
                return "ключ удалён"
        return "ключ не отдельной строкой — удалите вручную"
    if found:
        return "уже есть" if found.group(1) == SETTINGS_VALUE else \
            'уже задан как "%s" — не трогаю' % found.group(1)
    m = re.search(r"\{", raw)
    if not m:
        return "без '{' — пропустил"
    ins = m.end()
    if raw[ins:].strip().startswith("}"):
        new = raw[:ins] + '\n  "%s": "%s"\n' % (SETTINGS_KEY, SETTINGS_VALUE) + raw[ins:].lstrip()
    else:
        new = raw[:ins] + '\n  "%s": "%s",' % (SETTINGS_KEY, SETTINGS_VALUE) + raw[ins:]
    atomic_write(path, new)
    return "добавлен"


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(
        description="Пробросить все модели Arena2API в VS Code Copilot (по профилям)")
    ap.add_argument("--api", default=os.environ.get("ARENA_API", "http://127.0.0.1:9090"))
    ap.add_argument("--url", default=None, help="URL chat completions (обычно не нужен)")
    ap.add_argument("--api-key", default="arena-local",
                    help='apiKey группы; можно "${input:chat.lm.secret.XXX}". '
                         "Сервер без API_KEY его не проверяет")
    ap.add_argument("--user-profile", nargs="+", default=None, metavar="NAME",
                    help='только эти профили, например: --user-profile "C#" "C++" "DELPHI 7"')
    ap.add_argument("--user-dir", action="append", default=None,
                    help="каталог пользователя VS Code (можно несколько); иначе ищем сами")
    ap.add_argument("--list-profiles", action="store_true", help="показать найденные профили и выйти")
    ap.add_argument("--models", default=None, help="свой список моделей через запятую (иначе ВСЕ)")
    ap.add_argument("--no-tools", action="store_true", help="toolCalling=false -> только Ask")
    ap.add_argument("--max-in", type=int, default=128000)
    ap.add_argument("--max-out", type=int, default=32768)
    ap.add_argument("--keep-backups", type=int, default=5, help="сколько бэкапов хранить (default 5)")
    ap.add_argument("--no-settings", action="store_true", help="не трогать settings.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    targets = discover(args.user_dir)
    if not targets:
        raise SystemExit("не нашёл ни одного каталога пользователя VS Code.\n"
                         "Проверены: " + ", ".join(str(d) for d in variant_roots()) +
                         "\nУкажите вручную: --user-dir \"...\"")

    if args.list_profiles:
        print("Найдено профилей: " + str(len(targets)))
        for label, d, name in targets:
            lm = d / "chatLanguageModels.json"
            print("  %-28s %-14s %s" % (name, label, lm if lm.exists() else "(файла ещё нет)"))
        return 0

    targets = select(targets, args.user_profile)

    if args.uninstall:
        for label, d, name in targets:
            lm, st = d / "chatLanguageModels.json", d / "settings.json"
            if args.dry_run:
                print("[dry-run] " + name + ": убрать секцию " + PROVIDER)
                continue
            if lm.exists():
                b = backup(lm, args.keep_backups)
                groups, dropped = overwrite_section(lm, None, remove=True)
                atomic_write(lm, json.dumps(groups, ensure_ascii=False, indent=2) + "\n")
                print("%-14s секция %s: %s (бэкап: %s)" %
                      (name, PROVIDER, "удалена" if dropped else "не найдена", b or "—"))
            else:
                print("%-14s chatLanguageModels.json нет" % name)
            if not args.no_settings:
                print("%-14s settings.json: %s" % (name, patch_settings(st, remove=True)))
        return 0

    all_models, source = fetch_models(args.api)
    if args.models:
        want = [m.strip() for m in args.models.split(",") if m.strip()]
        by_id = {m["id"]: m for m in all_models}
        miss = [w for w in want if w not in by_id]
        if miss:
            print("[!] нет на сервере, пропущено: " + ", ".join(miss), file=sys.stderr)
        models = [by_id[w] for w in want if w in by_id]
    else:
        models = all_models
    if not models:
        raise SystemExit("с сервера не пришло ни одной модели — проверьте расширение "
                         "(GET /v1/models)")

    group = build_group(args, models)
    print("моделей: %d (источник %s, vision %d, toolCalling %s)" %
          (len(models), source, sum(1 for m in models if m.get("vision")), not args.no_tools))
    print("профилей: %d -> %s" % (len(targets), ", ".join(t[2] for t in targets)))
    print()

    for label, d, name in targets:
        lm, st = d / "chatLanguageModels.json", d / "settings.json"
        if args.dry_run:
            print("[dry-run] %-14s %s" % (name, "бэкап + перезапись секции " + PROVIDER +
                                           " (%d моделей)" % len(models)))
            continue
        b = backup(lm, args.keep_backups)
        groups, dropped = overwrite_section(lm, group)
        atomic_write(lm, json.dumps(groups, ensure_ascii=False, indent=2) + "\n")
        print("%-14s секция %-9s перезаписана%s | групп: %d | бэкап: %s" %
              (name, PROVIDER, " (старая заменена)" if dropped else "", len(groups), b or "не нужен"))
        if not args.no_settings:
            print("%-14s settings.json: %s" % (name, patch_settings(st)))

    if not args.dry_run:
        print("\nГотово. Полный перезапуск VS Code (не Reload Window), затем пикер моделей -> "
              + PROVIDER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
