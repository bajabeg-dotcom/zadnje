#!/usr/bin/env python3
"""Proof that pa_tool never modifies its inputs.

Hashes every file under a sample folder, runs the read-only commands, then hashes
again and compares. Any difference is a failure.

Usage:  python verify_readonly.py
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLES = [
    HERE.parent / "samples" / "NEWNAME_SET" / "NEWNAME.SET",
    Path(r"E:\Pa5X_UNPACKED\omega_sys\omega_sys\STARTUP\ALL\STYLE\FACTORY\Ballad"),
]

# single files the tool also touches (fingerprint path)
SAMPLE_FILES = []
for _base in (HERE.parent / "KORG_KOMPLET_2026-10-06").rglob("*.sty"):
    SAMPLE_FILES.append(_base)
    if len(SAMPLE_FILES) >= 2:
        break


def hash_tree(root: Path) -> dict[str, str]:
    out = {}
    if root.is_file():
        out[str(root)] = hashlib.sha256(root.read_bytes()).hexdigest()
        return out
    for f in sorted(root.rglob("*")):
        if f.is_file():
            h = hashlib.sha256()
            with f.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            out[str(f)] = h.hexdigest()
    return out


def run(*args: str) -> int:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, str(HERE / "run_pa_tool.py"), *args],
                       capture_output=True, env=env)
    return r.returncode


def main() -> int:
    ok = True
    for f in SAMPLE_FILES:
        before = hash_tree(f)
        run("convert", "--fingerprint", str(f))
        after = hash_tree(f)
        print(f"\n{f}")
        print(f"  files hashed : {len(before)}")
        print(f"  changed      : {sum(1 for k in before if before.get(k) != after.get(k))}")
        if before != after:
            ok = False
            print("  RESULT: MODIFIED")
        else:
            print("  RESULT: unchanged")

    for sample in SAMPLES:
        if not sample.exists():
            print(f"SKIP (missing): {sample}")
            continue
        before = hash_tree(sample)
        if sample.is_dir():
            run("detect", str(sample))
            run("inventory", str(sample))
        else:
            run("detect", str(sample))
            run("validate", str(sample))
        run("metadir", str(sample))
        after = hash_tree(sample)

        changed = [k for k in before if before.get(k) != after.get(k)]
        added = [k for k in after if k not in before]
        removed = [k for k in before if k not in after]

        print(f"\n{sample}")
        print(f"  files hashed : {len(before)}")
        print(f"  changed      : {len(changed)}")
        print(f"  added        : {len(added)}")
        print(f"  removed      : {len(removed)}")
        for k in changed[:5]:
            print(f"    CHANGED {k}")
        for k in added[:5]:
            print(f"    ADDED   {k}")
        for k in removed[:5]:
            print(f"    REMOVED {k}")
        if changed or added or removed:
            ok = False
        else:
            print("  RESULT: inputs unchanged (sha256 identical before/after)")

    print("\n" + ("PASS: no input file was modified" if ok else "FAIL: inputs were modified"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
