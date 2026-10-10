# -*- coding: utf-8 -*-
"""Read-only: is the STY round-trip difference recompression, or real data loss?

The earlier proof showed the compressed bytes differ after pa800 -> legacy -> pa800.
That alone says nothing: the output may simply be recompressed. This compares the
DECODED payloads of every resource in the bank instead.

Uses pa800_native.sty_container.parse_bank / decompress_bank, which is the strict
lossless parser/rebuilder the PA800 corpus already ships.
"""
import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pa_tool import convert  # noqa: E402

sc, notes = convert.load_setconvert()
if sc is None:
    print("setconvert not reachable")
    raise SystemExit(1)

import pa800_native  # noqa: E402
from pa800_native import sty_container  # noqa: E402
from setconvert import conv  # noqa: E402

WS = HERE.parent
OUT = HERE / "out" / "sty_lossless"
OUT.mkdir(parents=True, exist_ok=True)


def decoded_signature(path: Path):
    """(count, [(kind, payload_len, sha16)]) over DECODED payloads."""
    bank = sty_container.parse_bank(path.read_bytes())
    sig = []
    for res in bank.resources:
        payload = res.payload
        tag = "raw"
        if res.compressed and payload.startswith(b"OC31"):
            try:
                payload = pa800_native.decode_oc31(payload)
                tag = "oc31"
            except Exception as exc:  # noqa: BLE001
                tag = f"decode_fail:{type(exc).__name__}"
        sig.append((res.kind, len(payload), hashlib.sha256(payload).hexdigest()[:16], tag))
    return tuple(sig)


def header_signature(path: Path):
    bank = sty_container.parse_bank(path.read_bytes())
    return tuple(r.header.hex() for r in bank.resources)


src = None
for cand in (WS / "KORG_KOMPLET_2026-10-06").rglob("*.sty"):
    info = convert.fingerprint_sty(cand)
    if info.get("ok") and info.get("family") == "pa800" and info["styles"] > 0:
        src = cand
        break
if src is None:
    print("no pa800 style found")
    raise SystemExit(1)

src_sha_before = hashlib.sha256(src.read_bytes()).hexdigest()
print(f"source: {src.name} ({src.stat().st_size:,} B)")
print(f"  sha256: {src_sha_before}")

sig_src = decoded_signature(src)
hdr_src = header_signature(src)
print(f"\n== source decoded signature ==")
print(f"  resources: {len(sig_src)}")
print(f"  compressed: {sum(1 for r in sig_src if r[3] == 'oc31')}, raw: {sum(1 for r in sig_src if r[3] == 'raw')}")
for row in sig_src[:4]:
    print(f"    {row}")


def compare(label: str, other: Path):
    try:
        sig = decoded_signature(other)
        hdr = header_signature(other)
    except Exception as exc:  # noqa: BLE001
        print(f"\n{label}: parse failed: {type(exc).__name__}: {exc}")
        return None
    print(f"\n== {label} ==")
    print(f"  size      : {other.stat().st_size:,} B")
    print(f"  resources : {len(sig)} (source {len(sig_src)})")
    print(f"  decoded payloads identical to source: {sig == sig_src}")
    print(f"  headers identical to source        : {hdr == hdr_src}")
    if sig != sig_src:
        if len(sig) != len(sig_src):
            print(f"  RESOURCE COUNT DIFFERS: {len(sig)} vs {len(sig_src)}")
        else:
            diff = [i for i, (a, b) in enumerate(zip(sig_src, sig)) if a != b]
            print(f"  differing resources: {len(diff)} of {len(sig_src)}")
            for i in diff[:8]:
                print(f"    #{i} src={sig_src[i]}")
                print(f"          dst={sig[i]}")
    return sig


for dst_family in ("legacy", "pa1x", "pa3x", "pa800"):
    out = OUT / f"{src.stem}__{dst_family}.STY"
    try:
        conv.convert_file(str(src), str(out), dst_family, src_family="pa800")
    except Exception as exc:  # noqa: BLE001
        print(f"\n{dst_family}: conversion failed: {type(exc).__name__}: {exc}")
        continue
    info = convert.fingerprint_sty(out)
    print(f"\n[dst={dst_family}] fingerprint: family={info.get('family')} "
          f"styles={info.get('styles')} dialects={info.get('dialect_counts')}")
    compare(f"pa800 -> {dst_family}", out)

    if dst_family == "legacy":
        back = OUT / f"{src.stem}__legacy__back.STY"
        try:
            conv.convert_file(str(out), str(back), "pa800", src_family="legacy")
            compare("legacy -> pa800 (round trip)", back)
        except Exception as exc:  # noqa: BLE001
            print(f"  back conversion failed: {type(exc).__name__}: {exc}")

print(f"\n== final integrity ==")
src_sha_after = hashlib.sha256(src.read_bytes()).hexdigest()
print(f"  source sha256 before: {src_sha_before}")
print(f"  source sha256 after : {src_sha_after}")
print(f"  unchanged           : {src_sha_after == src_sha_before}")
