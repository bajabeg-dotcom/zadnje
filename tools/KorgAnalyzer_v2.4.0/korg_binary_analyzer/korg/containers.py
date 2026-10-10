"""Native Korg resource-container structure walking.

A native Korg resource container (``.STY``, ``.PCG``, ``.KMP``, ``.PRF``,
``.SBD``, ``.PAD`` and the flash pages inside ``.BKP``/``.pkg``) is a flat
sequence of self-describing records::

    [id u8][version u8][release u8][flags u8][payload_length u32BE][payload...]

The first four bytes of the file are the root record header
(``01 00 01 14``), and the last record (id ``0xFE``) carries a ``KBEG``/``KEND``
index listing the byte offsets of every resource that a librarian is allowed to
address.  Resource payloads are OC31/OC32 wrapped CBarc streams.

This module is read-only: it validates instead of repairing, and it reports
what it could not establish rather than guessing.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .codec import INDEXED_IDS, NATIVE_ROOT, TERMINAL_ID, KorgCodecError, be32

#: Record ids that carry user-addressable resources.
STYLE_ID = 0x06
SYSTEM_ID = 0x09
SOUND_ID = 0x05

#: Track roles in Korg's documented style channel layout.
TRACK_ROLES = ("drum", "perc", "bass", "acc1", "acc2", "acc3", "acc4", "acc5")


def children(data: bytes, offset: int = 0) -> list[dict[str, int]]:
    """Parse the child records of the container whose header starts at *offset*."""
    if offset + 8 > len(data):
        raise KorgCodecError("container header outside buffer")
    end = offset + 8 + be32(data, offset + 4)
    if end > len(data):
        raise KorgCodecError("container exceeds buffer")
    result: list[dict[str, int]] = []
    position = offset + 8
    while position < end:
        if position + 8 > end:
            raise KorgCodecError("truncated child header")
        length = be32(data, position + 4)
        child_end = position + 8 + length
        if child_end > end:
            raise KorgCodecError("child exceeds parent")
        result.append({
            "offset": position,
            "end": child_end,
            "id": data[position],
            "version": data[position + 1],
            "release": data[position + 2],
            "flags": data[position + 3],
            "length": length,
        })
        position = child_end
    if position != end:
        raise KorgCodecError("child traversal did not end at parent boundary")
    return result


def payload(data: bytes, item: dict[str, int]) -> bytes:
    return data[item["offset"] + 8:item["end"]]


def record_sequence(data: bytes, limit: int | None = None) -> list[dict[str, int]]:
    """Parse a flat record sequence that fills *data* exactly."""
    result: list[dict[str, int]] = []
    position = 0
    while position < len(data):
        if limit is not None and len(result) >= limit:
            break
        if position + 8 > len(data):
            raise KorgCodecError("truncated record sequence header")
        length = be32(data, position + 4)
        end = position + 8 + length
        if end > len(data):
            raise KorgCodecError("record sequence exceeds buffer")
        result.append({
            "offset": position,
            "end": end,
            "id": data[position],
            "version": data[position + 1],
            "release": data[position + 2],
            "flags": data[position + 3],
            "length": length,
        })
        position = end
    return result


def parse_native(native: bytes) -> list[dict[str, int]]:
    """Strict parse: root header, terminal ``FE`` record and ``KBEG``/``KEND`` index."""
    if native[:4] != NATIVE_ROOT:
        raise KorgCodecError("unexpected native root magic")
    if be32(native, 4) != len(native) - 8:
        raise KorgCodecError("native root length mismatch")
    outer = children(native)
    if not outer or outer[-1]["id"] != TERMINAL_ID:
        raise KorgCodecError("missing FE index record")
    terminal = outer[-1]
    index = native[terminal["offset"] + 8:terminal["end"]]
    if index[:4] != b"KBEG" or index[-8:-4] != b"KEND":
        raise KorgCodecError("invalid FE/KBEG/KEND index")
    offsets = [be32(index, position) for position in range(4, len(index) - 8, 4)]
    expected = [item["offset"] for item in outer if item["id"] in INDEXED_IDS]
    if offsets != expected or be32(index, len(index) - 4) != len(offsets):
        raise KorgCodecError("FE index does not match resource offsets")
    return outer


def parse_indexed_native(native: bytes) -> tuple[list[dict[str, int]], dict[str, Any]]:
    """Tolerant parse: locate the terminal ``KBEG``/``KEND`` index and the span it addresses.

    Unlike :func:`parse_native` this does not require the outer walk to be
    perfect, so it also works on containers whose outer framing deviates.
    """
    if native[:4] != NATIVE_ROOT:
        raise KorgCodecError("unexpected native root magic")
    kbeg = native.rfind(b"KBEG")
    kend = native.rfind(b"KEND")
    if kbeg < 0 or kend <= kbeg or kend + 8 > len(native):
        raise KorgCodecError("missing terminal KBEG/KEND index")
    offsets = [be32(native, position) for position in range(kbeg + 4, kend, 4)]
    count = be32(native, kend + 4)
    if count != len(offsets):
        raise KorgCodecError(
            f"KEND count {count} does not match {len(offsets)} index entries"
        )
    terminal_start = kbeg - 8
    if terminal_start < 8:
        raise KorgCodecError("terminal FE record overlaps the root header")
    if not offsets or offsets != sorted(offsets) or offsets[-1] >= terminal_start:
        raise KorgCodecError("invalid indexed resource offsets")
    result: list[dict[str, int]] = []
    mismatches = 0
    for index, offset in enumerate(offsets):
        end = offsets[index + 1] if index + 1 < len(offsets) else terminal_start
        if offset + 8 > end:
            raise KorgCodecError("indexed resource header exceeds span")
        declared = be32(native, offset + 4)
        span_length = end - offset - 8
        if declared != span_length:
            mismatches += 1
        result.append({
            "offset": offset,
            "end": end,
            "id": native[offset],
            "version": native[offset + 1],
            "release": native[offset + 2],
            "flags": native[offset + 3],
            "length": span_length,
            "declared_length": declared,
        })
    framing = {
        "declared_root_length": be32(native, 4),
        "actual_root_length": len(native) - 8,
        "root_length_matches": be32(native, 4) == len(native) - 8,
        "index_offset": kbeg,
        "terminal_record_offset": terminal_start,
        "index_entries": len(offsets),
        "resource_length_mismatches": mismatches,
        "resource_id_histogram": {
            f"{key:#04x}": value
            for key, value in sorted(Counter(item["id"] for item in result).items())
        },
    }
    return result, framing


def parse_name_directory(blob: bytes, entry_size: int = 48) -> dict[str, Any] | None:
    """Parse a fixed-width, uncompressed resource-name directory.

    The id ``0x05`` resource of a style container is a plain table of
    NUL-padded, fixed-width names: a 1920 byte record on a 40-style Pa3X bank
    is exactly 40 entries at a 48 byte stride.  A layout is only accepted when
    the stride divides the buffer *and* a clear majority of the slots hold
    printable text, so a coincidental zero-filled block is never reported as a
    name table.
    """
    if entry_size <= 0 or len(blob) < entry_size or len(blob) % entry_size:
        return None
    count = len(blob) // entry_size
    names: list[dict[str, Any]] = []
    populated = 0
    for index in range(count):
        slot = blob[index * entry_size:(index + 1) * entry_size]
        raw = slot.split(b"\x00", 1)[0]
        text = raw.decode("latin-1", errors="replace").strip()
        if text and all(character.isprintable() for character in text):
            populated += 1
            names.append({"index": index, "offset": index * entry_size, "name": text})
    if not names or populated * 2 < count:
        return None
    return {
        "entry_size": entry_size,
        "entry_count": count,
        "populated": populated,
        "names": names,
    }


def container_kind(data: bytes) -> str:
    """Classify a buffer by its leading structure, without decoding."""
    if data[:4] == NATIVE_ROOT:
        return "native-container"
    if data[:4] == b"OC20":
        return "oc20-factory-package"
    if data[:4] in (b"OC31", b"OC32"):
        return "oc3x-resource"
    if data[:4] == b"PK\x03\x04":
        return "zip"
    return "unknown"


def style_name(decoded: bytes) -> str | None:
    """Read the leading name string from a decoded style resource."""
    try:
        root = children(decoded)
    except KorgCodecError:
        return None
    if not root:
        return None
    first = root[0]
    start = first["offset"] + 8
    if start >= first["end"]:
        return None
    name_length = decoded[start]
    if start + 1 + name_length > first["end"]:
        return None
    raw = decoded[start + 1:start + 1 + name_length]
    try:
        text = raw.decode("latin-1")
    except UnicodeDecodeError:
        return None
    text = text.strip()
    if not text or not any(character.isprintable() for character in text):
        return None
    return text


def style_summary(decoded: bytes) -> dict[str, Any]:
    """Structural summary of a decoded style resource."""
    summary: dict[str, Any] = {
        "decoded_size": len(decoded),
        "root": decoded[:4].hex(" "),
    }
    try:
        root = children(decoded)
    except KorgCodecError as error:
        summary["structure_error"] = str(error)
        return summary
    summary["root_child_ids"] = [f"{item['id']:#04x}" for item in root]
    name = style_name(decoded)
    if name is not None:
        summary["name"] = name
    if root:
        summary["inner_version"] = root[0]["version"]
        summary["inner_release"] = root[0]["release"]
    elements = [item for item in root if item["id"] == 0x03]
    summary["element_count"] = len(elements)
    geometries: Counter[str] = Counter()
    for element in elements:
        try:
            element_children = children(decoded, element["offset"])
        except KorgCodecError:
            continue
        if len(element_children) >= 2:
            geometries[
                f"{element_children[0]['length']}/{element_children[1]['length']}"
            ] += 1
    summary["element_track_geometries"] = dict(geometries)
    return summary
