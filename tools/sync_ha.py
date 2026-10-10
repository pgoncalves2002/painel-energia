#!/usr/bin/env python3
"""Copia o núcleo e a interface do painel para dentro da integração e do add-on do Home Assistant.

A integração (custom_components/painel_energia) precisa ser uma pasta completa, que se copia sozinha
para o Home Assistant. Por isso ela carrega uma cópia de app/ (núcleo) e de app/static/ (interface).
Rode depois de alterar esses arquivos:

    python3 tools/sync_ha.py            # copia
    python3 tools/sync_ha.py --check    # só confere (usado pelos testes)
"""
from __future__ import annotations

import filecmp
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
INTEGRATION = os.path.join(ROOT, "custom_components", "painel_energia")
ADDON = os.path.join(ROOT, "painel_energia_addon")
CORE_MODULES = ["__init__.py", "fields.py", "parser.py", "energy.py", "timeutil.py", "voltage.py", "store.py",
                "analytics.py", "ha_discovery.py", "service.py", "solar.py"]


def pairs():
    for name in CORE_MODULES:
        yield os.path.join(APP, name), os.path.join(INTEGRATION, "core", name)
    static = os.path.join(APP, "static")
    for base, dirs, files in os.walk(static):
        dirs.sort()
        for name in sorted(files):
            if name == ".DS_Store":
                continue
            src = os.path.join(base, name)
            yield src, os.path.join(INTEGRATION, "frontend", os.path.relpath(src, static))
    # o add-on leva o aplicativo inteiro
    for base, dirs, files in os.walk(APP):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if name == ".DS_Store" or name.endswith(".pyc"):
                continue
            src = os.path.join(base, name)
            yield src, os.path.join(ADDON, "app", os.path.relpath(src, APP))
    yield os.path.join(ROOT, "requirements.txt"), os.path.join(ADDON, "requirements.txt")


def expected_files():
    return {dst for _src, dst in pairs()}


def stale():
    out = []
    for folder in (os.path.join(INTEGRATION, "core"), os.path.join(INTEGRATION, "frontend"), os.path.join(ADDON, "app")):
        for base, _dirs, files in os.walk(folder):
            if "__pycache__" in base:
                continue
            for name in files:
                path = os.path.join(base, name)
                if path not in expected_files() and name != ".DS_Store":
                    out.append(path)
    return out


def version_ok() -> bool:
    ns = {}
    exec(open(os.path.join(APP, "__init__.py"), encoding="utf-8").read(), ns)
    manifest = json.load(open(os.path.join(INTEGRATION, "manifest.json"), encoding="utf-8"))
    addon = open(os.path.join(ADDON, "config.yaml"), encoding="utf-8").read()
    return manifest.get("version") == ns["__version__"] and ('version: "%s"' % ns["__version__"]) in addon


def main() -> int:
    check = "--check" in sys.argv
    diffs = []
    for src, dst in pairs():
        if not os.path.exists(dst) or not filecmp.cmp(src, dst, shallow=False):
            diffs.append(os.path.relpath(dst, ROOT))
            if not check:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
    old = stale()
    for path in old:
        diffs.append(os.path.relpath(path, ROOT) + " (sobrando)")
        if not check:
            os.remove(path)
    if not version_ok():
        print("A versão no manifest.json da integração ou no config.yaml do add-on difere de app/__init__.py")
        return 1
    if check and diffs:
        print("Desatualizados (rode python3 tools/sync_ha.py):\n  " + "\n  ".join(diffs))
        return 1
    print("%d arquivo(s) %s" % (len(diffs), "copiado(s)/removido(s)" if not check else "diferentes"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
