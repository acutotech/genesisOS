#!/usr/bin/env python3
"""Import every Genesis Shell module against a stubbed PyGObject.

GTK is not installed on the lint machines, so this cannot run the shell, but it
catches what a real import would catch first: syntax errors, names imported from
the wrong module, and module-level mistakes. Used by `make lint`.
"""
import importlib
import os
import pkgutil
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.join(HERE, "..", "stage-custom", "11-genesis-shell", "files", "genesis-shell")
sys.path.insert(0, os.path.join(HERE, "gi-stub"))
sys.path.insert(0, PACKAGE_DIR)

import genesis_shell  # noqa: E402

failures = 0
names = ["genesis_shell"]
for info in pkgutil.walk_packages(genesis_shell.__path__, "genesis_shell."):
    if ".protocols" in info.name or info.name.endswith("__main__"):
        continue
    names.append(info.name)
for name in names:
    try:
        importlib.import_module(name)
    except Exception:  # noqa: BLE001
        failures += 1
        print(f"FAIL {name}")
        print("    " + "\n    ".join(traceback.format_exc().splitlines()[-3:]))
print(f"{len(names)} modules imported, {failures} failed")
sys.exit(1 if failures else 0)
