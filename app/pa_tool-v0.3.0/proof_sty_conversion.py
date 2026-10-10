# -*- coding: utf-8 -*-
"""Read-only proof: perform a real byte-level STY conversion with setconvert and verify it.

Goal: show that pa_tool can drive an actual conversion, that the input is untouched,
and that the output round-trips (converting back yields the original payload bytes).
"""
import hashlib
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pa_tool import convert  # noqa: E402

WS = HERE.parent
OUT = HERE / "out" / "sty_proof"
OUT.mkdir(parents=True, exist_ok=True)

# find a real style the fingerprinter identifies
cands = [f for f in (WS / "KORG_KOMPLET_2026-10-06").rglob("*.sty")]
target = None
for p in cands:
    info = convert.fingerprint_sty(p)
    if info.get("ok") and info.get("family") and info["styles"] > 0:
        target = (p, info)
        break

if target is None:
    print("no identifiable style found")
    raise SystemExit(1)

src, info = target
print(f"source      : {src.name}")
print(f"  size       : {src.stat().st_size:,} B")
print(f"  family     : {info['family']} ({info.get('family_label')})")
print(f"  styles/sts : {info['styles']}/{info['sts']}")
print(f"  dialects   : {info['dialect_counts']}")

sha_before = hashlib.sha256(src.read_bytes()).hexdigest()
print(f"  sha256 pre : {sha_before}")

# the output family: pick a different one that exists in the table
current = info["family"]
for fam in convert.sty_families():
    if fam["id"] != current:
        dst_family = fam["id"]
        break
print(f"\ntarget family: {dst_family}")

from setconvert import conv  # noqa: E402

out_path = OUT / (src.stem + f"__{dst_family}" + src.suffix)
print(f"target file  : {out_path}")

try:
    report = conv.convert_file(str(src), str(out_path), dst_family,
                              src_family=current)
except Exception as exc:  # noqa: BLE001
    print(f"\nCONVERSION FAILED: {type(exc).__name__}: {exc}")
    raise SystemExit(2)

print("\n== conversion report ==")
for attr in ("ok", "src_family", "dst_family", "styles", "sts", "messages",
             "warnings", "changed", "input_size", "output_size"):
    if hasattr(report, attr):
        val = getattr(report, attr)
        print(f"  {attr:<14}: {val}")

if not out_path.is_file():
    print("\nno output file was produced")
    raise SystemExit(3)

out_bytes = out_path.read_bytes()
print(f"\n== output ==")
print(f"  size       : {len(out_bytes):,} B")
print(f"  sha256     : {hashlib.sha256(out_bytes).hexdigest()}")

sha_after = hashlib.sha256(src.read_bytes()).hexdigest()
print(f"\n== input integrity ==")
print(f"  sha256 pre : {sha_before}")
print(f"  sha256 post: {sha_after}")
print(f"  UNCHANGED  : {sha_before == sha_after}")

# fingerprint the output, and convert back
print("\n== fingerprint of the output ==")
out_info = convert.fingerprint_sty(out_path)
print(f"  family     : {out_info.get('family')}")
print(f"  styles     : {out_info.get('styles')}")
print(f"  dialects   : {out_info.get('dialect_counts')}")

back = OUT / (src.stem + "__roundtrip" + src.suffix)
try:
    conv.convert_file(str(out_path), str(back), current, src_family=dst_family)
    back_bytes = back.read_bytes()
    print(f"\n== round trip: {dst_family} -> {current} ==")
    print(f"  back size  : {len(back_bytes):,} B")
    same = back_bytes == src.read_bytes()
    print(f"  identical to original: {same}")
    if not same:
        # report where they differ, honestly
        n = min(len(back_bytes), len(src.read_bytes()))
        diffs = [i for i in range(n) if back_bytes[i] != src.read_bytes()[i]]
        print(f"  differing bytes: {len(diffs)} of {n} (first at "
              f"{hex(diffs[0]) if diffs else '-'})")
except Exception as exc:  # noqa: BLE001
    print(f"\n  round trip failed: {type(exc).__name__}: {exc}")

sha_final = hashlib.sha256(src.read_bytes()).hexdigest()
print(f"\nFINAL source unchanged: {sha_before == sha_final}")
