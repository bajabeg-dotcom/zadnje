"""Evidence-qualified resource scanning.

A raw search for ``OC31``/``OC32`` magic bytes produces **false positives**:
record headers inside an already-decompressed stream contain the literal bytes
``"OC31" + u32LE size`` followed by literal names.  Such a "resource" either
runs away while parsing or produces the wrong number of bytes.

This module therefore only accepts a candidate when *all* of these hold:

1. it sits at a byte offset that a structural walk has not already claimed,
2. the CBarc stream produces **exactly** the declared size, and
3. the XOR checksum byte matches the decoded content.

That is the same reality test the instrument firmware applies
(``CChkCmdBG::DecompressData`` compares produced against declared and reports
``BARC decompression fails!`` on a mismatch), so an accepted resource is
evidence, not a guess.  Everything rejected is reported as a candidate with
its reason.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .codec import (
    KorgCodecError,
    NATIVE_ROOT,
    decode_oc3x_generic,
    is_oc3x,
)
from .containers import parse_indexed_native, parse_name_directory, payload as record_payload

OC3X_PATTERN = re.compile(rb"OC3[12]")

#: Cap on how many raw magic hits are probed in one file.
MAX_MAGIC_HITS = 4096


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _spans_overlap(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < other_end and end > other_start for other_start, other_end in spans)


def scan_structured(native: bytes) -> dict[str, Any]:
    """Decode every resource the terminal index addresses."""
    resources: list[dict[str, Any]] = []
    raw_resources: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        indexed, framing = parse_indexed_native(native)
    except KorgCodecError as error:
        return {"status": "STRUCTURE_UNKNOWN", "error": str(error), "resources": [], "errors": []}

    for position, item in enumerate(indexed):
        blob = record_payload(native, item)
        record = {
            "resource_position": position,
            "id": f"{item['id']:#04x}",
            "version": item["version"],
            "release": item["release"],
            "flags": item["flags"],
            "record_offset": item["offset"],
            "payload_offset": item["offset"] + 8,
            "payload_size": len(blob),
        }
        if not is_oc3x(blob):
            # Not every indexed resource is compressed.  The id 0x05 record is
            # an uncompressed fixed-width name directory (40 entries x 48 bytes
            # in a 40-style Pa3X bank), which is itself authoritative evidence.
            raw = {
                **record,
                "kind": "raw",
                "wrapper": None,
                "decoded_size": len(blob),
                "sha256": _sha256(blob),
                "note": "indexed resource that is not an OC31/OC32 wrapper; stored uncompressed",
            }
            directory = parse_name_directory(blob)
            if directory:
                raw["name_directory"] = directory
            raw_resources.append(raw)
            continue
        try:
            decoded, metadata = decode_oc3x_generic(blob)
        except KorgCodecError as error:
            errors.append({**record, "reason": str(error)})
            continue
        resources.append({
            **record,
            "wrapper": metadata["wrapper"],
            "declared_size": metadata["declared_size"],
            "decoded_size": len(decoded),
            "xor_checksum": metadata["checksum"],
            "sha256": _sha256(decoded),
            "trailer_size": metadata["trailer_size"],
        })
    validated = bool(resources) and not errors
    return {
        "status": "DECODED_VALIDATED" if validated else (
            "DECODED_PARTIAL" if resources else "ATTEMPTED_FAILED"
        ),
        "framing": framing,
        "resources": resources,
        "raw_resources": raw_resources,
        "errors": errors,
        "name_directory": next(
            (item["name_directory"] for item in raw_resources if item.get("name_directory")),
            None,
        ),
    }


def scan_magic(native: bytes, claimed: list[tuple[int, int]] | None = None) -> dict[str, Any]:
    """Probe every raw OC3x magic hit and accept only fully validated streams."""
    claimed = claimed or []
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = list(claimed)

    hits = list(OC3X_PATTERN.finditer(native))[:MAX_MAGIC_HITS]
    for match in hits:
        offset = match.start()
        if _spans_overlap(offset, offset + 12, spans):
            rejected.append({
                "offset": offset,
                "reason": "inside a resource span already decoded and validated by the index walk",
            })
            continue
        try:
            decoded, metadata = decode_oc3x_generic(native[offset:])
        except KorgCodecError as error:
            rejected.append({"offset": offset, "reason": str(error)})
            continue
        end = offset + 8 + metadata["stream_size"] + 1
        if _spans_overlap(offset, end, spans):
            rejected.append({
                "offset": offset,
                "reason": "overlaps an earlier validated stream (nested literal)",
            })
            continue
        spans.append((offset, end))
        accepted.append({
            "offset": offset,
            "wrapper": metadata["wrapper"],
            "decoded_size": len(decoded),
            "stream_size": metadata["stream_size"],
            "xor_checksum": metadata["checksum"],
            "sha256": _sha256(decoded),
            "trailer_size": metadata["trailer_size"],
        })
    return {
        "magic_hits": len(hits),
        "accepted": accepted,
        "rejected": rejected,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
    }


def scan_buffer(data: bytes, structured_spans: list[tuple[int, int]] | None = None) -> dict[str, Any]:
    """Full evidence-qualified view of one buffer."""
    result: dict[str, Any] = {
        "is_native_container": data[:4] == NATIVE_ROOT,
        "magic": data[:16].hex(" "),
        "size": len(data),
    }
    if result["is_native_container"]:
        result["structured"] = scan_structured(data)
        claimed = [
            (item["payload_offset"], item["payload_offset"] + item["payload_size"])
            for item in result["structured"].get("resources", [])
        ]
        result["raw_magic_probe"] = scan_magic(data, claimed)
    else:
        result["structured"] = {"status": "NOT_A_NATIVE_CONTAINER", "resources": [], "errors": []}
        result["raw_magic_probe"] = scan_magic(data, structured_spans)
    return result
