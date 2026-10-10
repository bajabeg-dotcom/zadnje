from __future__ import annotations

import hashlib
import json
import shutil
import struct
import zlib
from xml.parsers import expat
from pathlib import Path
from typing import Any

from .binary import MappedInput


def _png_size(data: Any, start: int) -> tuple[int | None, list[str]]:
    if bytes(data[start:start + 8]) != b"\x89PNG\r\n\x1a\n":
        return None, []
    cursor = start + 8
    chunks = 0
    evidence = ["PNG signature matched"]
    while cursor + 12 <= len(data) and chunks < 100000:
        length = struct.unpack_from(">I", data, cursor)[0]
        chunk_end = cursor + 12 + length
        if chunk_end > len(data):
            return None, evidence + ["PNG chunk exceeds input bounds"]
        chunk_type = bytes(data[cursor + 4:cursor + 8])
        crc = zlib.crc32(bytes(data[cursor + 4:cursor + 8]))
        crc_cursor = cursor + 8
        content_end = cursor + 8 + length
        while crc_cursor < content_end:
            next_cursor = min(content_end, crc_cursor + 1024 * 1024)
            crc = zlib.crc32(data[crc_cursor:next_cursor], crc)
            crc_cursor = next_cursor
        stored_crc = struct.unpack_from(">I", data, cursor + 8 + length)[0]
        if crc & 0xFFFFFFFF != stored_crc:
            return None, evidence + [f"PNG {chunk_type.decode('ascii', errors='replace')} CRC mismatch"]
        chunks += 1
        cursor = chunk_end
        if chunk_type == b"IEND":
            return cursor - start, evidence + [f"{chunks} PNG chunks parsed", "chunk CRCs and IEND boundary validated", "IDAT pixel decompression was not performed"]
    return None, evidence + ["PNG IEND not found within bounded scan"]


def _midi_size(data: Any, start: int) -> tuple[int | None, list[str]]:
    if bytes(data[start:start + 4]) != b"MThd" or start + 14 > len(data):
        return None, []
    header_length = struct.unpack_from(">I", data, start + 4)[0]
    if header_length < 6 or start + 8 + header_length > len(data):
        return None, ["MIDI header length is invalid"]
    track_count = struct.unpack_from(">H", data, start + 10)[0]
    cursor = start + 8 + header_length
    for _ in range(min(track_count, 10000)):
        if cursor + 8 > len(data) or bytes(data[cursor:cursor + 4]) != b"MTrk":
            return None, ["MIDI track chunk header is missing or truncated"]
        length = struct.unpack_from(">I", data, cursor + 4)[0]
        cursor += 8 + length
        if cursor > len(data):
            return None, ["MIDI track exceeds input bounds"]
    return cursor - start, ["MThd header and declared MTrk chunk boundaries are in bounds; MIDI event semantics were not checked"]



def _embedded_json(data: Any) -> list[dict[str, Any]]:
    results = []
    markers = (b'{"', b'{ "', b"{\n", b"{\r", b"[{", b'["', b'[ "', b"[\n")
    decoder = json.JSONDecoder()
    attempted = 0
    seen = set()
    for marker in markers:
        cursor = 1
        while attempted < 128:
            offset = data.find(marker, cursor)
            if offset < 0:
                break
            cursor = offset + 1
            if offset == 0 or offset in seen:
                continue
            seen.add(offset)
            attempted += 1
            sample_end = min(len(data), offset + 1024 * 1024)
            try:
                text = bytes(data[offset:sample_end]).decode("utf-8")
                value, end_chars = decoder.raw_decode(text)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(value, (dict, list)):
                continue
            byte_size = len(text[:end_chars].encode("utf-8"))
            results.append({
                "embedded_start": offset, "embedded_signature": "JSON", "possible_size": byte_size,
                "confidence": 0.96, "status": "BOUNDED_VALIDATED",
                "evidence": ["UTF-8 JSON object/array parsed by the standard library", "JSON token boundary provides a bounded embedded object"],
            })
            cursor = offset + max(byte_size, 1)
        if attempted >= 128:
            break
    return results


def _xml_object_size(data: Any, start: int) -> tuple[int | None, list[str]]:
    parser = expat.ParserCreate()
    depth = 0
    root_seen = False
    root_closed = False
    close_index = None

    def on_start(name: str, attributes: dict[str, str]) -> None:
        nonlocal depth, root_seen
        if depth == 0:
            root_seen = True
        depth += 1

    def on_end(name: str) -> None:
        nonlocal depth, root_closed, close_index
        depth -= 1
        if depth == 0 and root_seen:
            root_closed = True
            close_index = parser.CurrentByteIndex

    def reject_doctype(*args: Any) -> None:
        raise expat.ExpatError("DOCTYPE declarations are disabled in embedded XML probes")

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    parser.StartDoctypeDeclHandler = reject_doctype
    if hasattr(parser, "SetParamEntityParsing"):
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    limit = min(len(data), start + 1024 * 1024)
    cursor = start
    while cursor < limit and not root_closed:
        next_cursor = min(limit, cursor + 4096)
        block = bytes(data[cursor:next_cursor])
        try:
            parser.Parse(block, False)
        except expat.ExpatError:
            if not root_closed:
                return None, ["XML-like prefix found, but a well-formed root element was not parsed"]
        cursor = next_cursor
    if not root_closed or close_index is None:
        return None, ["XML root boundary not found within the bounded probe"]
    close_start = start + close_index
    close_end = data.find(b">", close_start, min(limit, cursor + 1))
    if close_end < 0:
        return None, ["XML closing-tag terminator was not found in the bounded probe"]
    return close_end + 1 - start, ["XML parser observed a complete root element with bounded start/end positions"]


def _embedded_xml(data: Any) -> list[dict[str, Any]]:
    results = []
    markers = (b"<",)
    seen = set()
    for marker in markers:
        cursor = 1
        while len(results) < 128:
            offset = data.find(marker, cursor)
            if offset < 0:
                break
            cursor = offset + 1
            if offset == 0 or offset in seen or offset + 1 >= len(data):
                continue
            next_byte = data[offset + 1]
            if not (65 <= next_byte <= 90 or 97 <= next_byte <= 122 or next_byte == 63):
                continue
            seen.add(offset)
            size, evidence = _xml_object_size(data, offset)
            results.append({
                "embedded_start": offset, "embedded_signature": "XML", "possible_size": size,
                "confidence": 0.94 if size else 0.55,
                "status": "BOUNDED_VALIDATED" if size else "CANDIDATE",
                "evidence": [f"XML-like marker at 0x{offset:X}", *evidence],
            })
    return results

def find_embedded(data: Any, signatures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for signature in signatures:
        offset = signature["offset"]
        if offset == 0:
            continue
        name = signature["name"].upper()
        possible_size = None
        evidence = [f"signature {name} found at 0x{offset:X}"]
        status = "CANDIDATE"
        if name == "PNG":
            possible_size, validation = _png_size(data, offset)
            evidence.extend(validation)
            if possible_size:
                status = "BOUNDED_VALIDATED"
        elif name == "RIFF" and offset + 12 <= len(data):
            declared = struct.unpack_from("<I", data, offset + 4)[0]
            if bytes(data[offset + 8:offset + 12]) in (b"WAVE", b"AVI ", b"WEBP") and declared >= 4 and offset + 8 + declared <= len(data):
                possible_size = 8 + declared
                status = "BOUNDED"
                evidence.append(f"RIFF subtype {bytes(data[offset + 8:offset + 12]).decode('ascii', errors='replace')} and declared size are in bounds")
        elif name == "MIDI":
            possible_size, validation = _midi_size(data, offset)
            evidence.extend(validation)
            if possible_size:
                status = "BOUNDED"
        elif name == "JPEG":
            end = data.find(b"\xff\xd9", offset + 3)
            if end >= 0:
                possible_size = end + 2 - offset
                evidence.append("JPEG end marker found; marker structure has not been fully parsed")
        found.append({
            "embedded_start": offset, "embedded_signature": name, "possible_size": possible_size,
            "confidence": 0.99 if status == "BOUNDED_VALIDATED" else 0.75 if possible_size else signature.get("confidence", 0.5),
            "status": status, "evidence": evidence,
        })
    found.extend(_embedded_json(data))
    found.extend(_embedded_xml(data))
    found.sort(key=lambda item: item["embedded_start"])
    return found[:5000]


def extract_validated_embedded(data: Any, embedded: list[dict[str, Any]], output_root: Path, source_sha256: str) -> list[dict[str, Any]]:
    root = output_root / source_sha256[:16]
    outputs: list[dict[str, Any]] = []
    for item in embedded:
        if item["status"] not in ("BOUNDED_VALIDATED", "BOUNDED") or not item.get("possible_size"):
            continue
        if item["embedded_signature"] not in ("PNG", "MIDI", "RIFF", "JSON", "XML"):
            continue
        root.mkdir(parents=True, exist_ok=True)
        extension = {"PNG": ".png", "MIDI": ".mid", "RIFF": ".riff", "JSON": ".json", "XML": ".xml"}[item["embedded_signature"]]
        base_name = f"offset-{item['embedded_start']:x}{extension}"
        path = root / base_name
        suffix = 1
        while path.exists():
            path = root / f"offset-{item['embedded_start']:x}-{suffix}{extension}"
            suffix += 1
        start, end = item["embedded_start"], item["embedded_start"] + item["possible_size"]
        digest = hashlib.sha256()
        with path.open("xb") as output:
            cursor = start
            while cursor < end:
                block = bytes(data[cursor:min(end, cursor + 1024 * 1024)])
                output.write(block)
                digest.update(block)
                cursor += len(block)
        outputs.append({
            "path": str(path), "source_offset": start, "size": end - start,
            "sha256": digest.hexdigest(), "format": item["embedded_signature"],
            "status": "DECODED_VALIDATED" if item["status"] == "BOUNDED_VALIDATED" else "DECODED_UNVERIFIED",
            "evidence": item["evidence"],
        })
    return outputs