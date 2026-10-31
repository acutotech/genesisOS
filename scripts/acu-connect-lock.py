#!/usr/bin/env python3
"""Write stage-custom/16-acu-connect/files/requirements.lock from the package's requirements.txt.

    python3 scripts/acu-connect-lock.py

Run it after replacing files/acu-connect-raspberry-pi with a new version of the
package. Every pinned version gets the SHA-256 of each wheel PyPI publishes for
it; the image build installs with pip --require-hashes --only-binary=:all:, so a
file that is not on this list (or a source build) is refused. It also checks that
each version has a wheel the image can use: CPython ACU_PYTHON (3.14), Linux arm64.
Needs network access to pypi.org.
"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.join(HERE, "..", "stage-custom", "16-acu-connect", "files")
REQUIREMENTS = os.path.join(STAGE, "acu-connect-raspberry-pi", "requirements.txt")
LOCK = os.path.join(STAGE, "requirements.lock")
PYTHON = os.environ.get("ACU_PYTHON", "3.14")

HEADER = """\
# acu-connect's Python packages, pinned by hash (pip --require-hashes --only-binary=:all:).
# The versions are the package's requirements.txt; each hash is a wheel PyPI publishes for
# that version (pip installs the one that fits the image: CPython 3.14, Linux arm64).
# Regenerate after updating the package: scripts/acu-connect-lock.py
"""


def usable(filename, cp):
    """A wheel CPython `cp` (e.g. cp314) on Linux arm64 (glibc) can install."""
    if not filename.endswith(".whl"):
        return False
    python, abi, platform = filename[:-4].split("-")[-3:]
    if platform == "any":
        return True
    if "aarch64" not in platform or "musllinux" in platform and "manylinux" not in platform:
        return False
    if abi == "abi3":
        return python.startswith("cp") and int(python[3:] or 0) <= int(cp[3:])
    return python == cp and abi == cp


def main():
    cp = "cp" + PYTHON.replace(".", "")
    out, problems = [HEADER.rstrip("\n")], []
    for line in open(REQUIREMENTS, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if "==" not in line:
            problems.append(f"{line}: not pinned with ==")
            continue
        name, version = (part.strip() for part in line.split("==", 1))
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as res:
            files = json.load(res)["urls"]
        wheels = [f for f in files if f["filename"].endswith(".whl")]
        if not any(usable(f["filename"], cp) for f in wheels):
            problems.append(f"{name}=={version}: no wheel for CPython {PYTHON} on Linux arm64")
        hashes = sorted({f["digests"]["sha256"] for f in wheels})
        out.append(f"{name}=={version} \\")
        out += [f"    --hash=sha256:{h}" + (" \\" if i < len(hashes) - 1 else "") for i, h in enumerate(hashes)]
    if problems:
        sys.exit("acu-connect-lock: " + "; ".join(problems))
    with open(LOCK, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print(f"wrote {os.path.relpath(LOCK)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
