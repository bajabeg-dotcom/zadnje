"""Self-test: prove the codec works before you trust it with real data.

The synthetic container built here mirrors the real layout byte for byte:
a native root record, a terminal ``FE`` record carrying a ``KBEG``/``KEND``
index, an uncompressed fixed-width name directory (id ``0x05``) and paired
``0x06``/``0x09`` resources wrapped in OC31 with a CBarc stream and an XOR
checksum.  The test then runs the full production pipeline over it and checks
that every stage agrees.

Nothing here touches the filesystem except a temporary directory.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from . import __version__
from .decoder_engine import DecoderRegistry
from .korg.codec import NATIVE_ROOT, barc_decompress, decode_oc3x_generic, encode_oc3x, xor_checksum
from .korg.containers import children, parse_name_directory, style_summary

SYNTHETIC_NAMES = ("Testni Narodni", "Kolo Brzi", "Sevdah Spori")


def _record(resource_id: int, payload: bytes, version: int = 0, release: int = 0, flags: int = 0) -> bytes:
    return bytes([resource_id, version, release, flags]) + len(payload).to_bytes(4, "big") + payload


def _inner_style(name: str, element_count: int) -> bytes:
    """Build a decoded style resource whose first child carries the name."""
    encoded = name.encode("latin-1")
    head = bytes([len(encoded)]) + encoded + b"\x00" * 4
    body = _record(0x01, head)
    for index in range(element_count):
        element = _record(0x02, bytes(28)) + _record(0x03, bytes(80))
        body += _record(0x03, element)
    return NATIVE_ROOT + len(body).to_bytes(4, "big") + body


def build_synthetic_container() -> bytes:
    records: list[bytes] = []
    records.append(_record(0x02, bytes(12)))

    directory = b"".join(
        name.encode("latin-1").ljust(48, b"\x00")[:48] for name in SYNTHETIC_NAMES
    )
    records.append(_record(0x05, directory, flags=0x18))

    for index, name in enumerate(SYNTHETIC_NAMES):
        records.append(_record(0x06, encode_oc3x(_inner_style(name, element_count=2 + index), "OC31")))
        records.append(_record(0x09, encode_oc3x(b"companion" + bytes([index]), "OC31"), version=1, release=3))

    indexed_offsets: list[int] = []
    body = b""
    for record in records:
        if record[0] in (0x05, 0x06, 0x09):
            indexed_offsets.append(8 + len(body))
        body += record

    index_payload = b"KBEG"
    for offset in indexed_offsets:
        index_payload += offset.to_bytes(4, "big")
    index_payload += b"KEND" + len(indexed_offsets).to_bytes(4, "big")
    body += _record(0xFE, index_payload)
    return NATIVE_ROOT + len(body).to_bytes(4, "big") + body


def run_selftest(verbose: bool = True, keep: Path | None = None) -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        checks.append((label, bool(condition), detail))
        if verbose:
            print(f"  [{'OK  ' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))

    if verbose:
        print(f"Korg Binary Analyzer {__version__} — samotest")
        print("=" * 62)
        print("1. CBarc / OC31 round trip")

    payload = bytes(range(256)) * 8
    wrapped = encode_oc3x(payload, "OC31")
    decoded, metadata = decode_oc3x_generic(wrapped)
    check("OC31 decode returns the exact payload", decoded == payload, f"{len(decoded)} bytes")
    check("declared size equals produced size", metadata["declared_size"] == metadata["produced_size"])
    check("XOR checksum verified", metadata["checksum"] == xor_checksum(payload))

    stream, consumed = barc_decompress(
        encode_oc3x(b"KORG" * 64, "OC31")[8:],
        256,
    )
    check("CBarc stream consumes exactly the wrapped bytes", len(stream) == 256 and consumed > 0)

    if verbose:
        print("2. Container structure")

    container = build_synthetic_container()
    root_children = children(container)
    # 1 system record + 1 name directory + 3 x (style + companion) + terminal FE
    expected_children = 1 + 1 + 2 * len(SYNTHETIC_NAMES) + 1
    check("root record parses", len(root_children) == expected_children, f"{len(root_children)} records")
    check("terminal FE record present", root_children[-1]["id"] == 0xFE)

    directory_record = next(item for item in root_children if item["id"] == 0x05)
    directory = parse_name_directory(container[directory_record["offset"] + 8:directory_record["end"]])
    check(
        "name directory parses as 48-byte entries",
        bool(directory) and directory["entry_count"] == len(SYNTHETIC_NAMES),
        f"{directory['entry_count'] if directory else 0} entries",
    )

    if verbose:
        print("3. Full decoder pipeline")

    workspace = keep or Path(tempfile.mkdtemp(prefix="korg-selftest-"))
    try:
        sample = workspace / "selftest_sample.STY"
        sample.write_bytes(container)

        registry = DecoderRegistry()
        probes = registry.probe(container)
        check("a supported decoder matches", any(item["implementation"] == "SUPPORTED" for item in probes))

        results = registry.run(sample, workspace / "decoded", container)
        result: dict[str, Any] = results[0] if results else {}
        check("decode status is DECODED_VALIDATED", result.get("status") == "DECODED_VALIDATED", str(result.get("status")))
        check(
            "validation is VALIDATED",
            (result.get("validation") or {}).get("status") == "VALIDATED",
            str((result.get("validation") or {}).get("status")),
        )
        check(
            "all three styles recovered",
            result.get("style_count") == len(SYNTHETIC_NAMES),
            f"{result.get('style_count')} styles",
        )
        check(
            "directory names agree with decoded names",
            (result.get("name_agreement") or {}).get("agreed") == len(SYNTHETIC_NAMES),
            str(result.get("name_agreement")),
        )
        check(
            "no resource was rejected",
            result.get("resource_decode_errors") == 0,
            f"{result.get('resource_decode_errors')} rejected",
        )
        check("source file was not modified", sample.read_bytes() == container)

        summary = style_summary(_inner_style("Testni Narodni", 4))
        check("style summary reads the embedded name", summary.get("name") == "Testni Narodni", str(summary.get("name")))

        if verbose:
            print("4. Corruption is refused, not guessed at")
        corrupt = bytearray(container)
        corrupt[-1] ^= 0xFF  # break the KEND entry count
        corrupt_path = workspace / "selftest_corrupt.STY"
        corrupt_path.write_bytes(bytes(corrupt))
        bad = registry.run(corrupt_path, workspace / "decoded2", bytes(corrupt))
        bad_result = bad[0] if bad else {}
        check(
            "container with a broken index is refused",
            bad_result.get("status") != "DECODED_VALIDATED",
            str(bad_result.get("status")),
        )

        flipped = bytearray(container)
        payload_offset = next(
            item["offset"] + 8 + 12 for item in root_children if item["id"] == 0x06
        )
        flipped[payload_offset] ^= 0xFF  # damage the CBarc stream itself
        flipped_path = workspace / "selftest_damaged.STY"
        flipped_path.write_bytes(bytes(flipped))
        damaged = registry.run(flipped_path, workspace / "decoded3", bytes(flipped))
        damaged_result = damaged[0] if damaged else {}
        check(
            "damaged resource stream is never reported as validated",
            damaged_result.get("status") != "DECODED_VALIDATED",
            str(damaged_result.get("status")),
        )
    finally:
        if keep is None:
            shutil.rmtree(workspace, ignore_errors=True)

    failed = [item for item in checks if not item[1]]
    if verbose:
        print("=" * 62)
        print(f"{len(checks) - len(failed)}/{len(checks)} provjera proslo.")
        print("SAMOTEST: " + ("USPJESAN" if not failed else "NEUSPJESAN"))
    return 0 if not failed else 1


def main() -> int:
    return run_selftest()


if __name__ == "__main__":
    raise SystemExit(main())
