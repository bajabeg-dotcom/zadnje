from __future__ import annotations

import hashlib
import json
import math
import mmap
import os
import re
import struct
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

READ_CHUNK = 1024 * 1024
MAX_STRING_RESULTS = 20000
MAX_STRING_LENGTH = 512
MAX_SIGNATURE_RESULTS = 20000
MAX_POINTER_SCAN = 64 * 1024 * 1024

_PACKAGE_DATA = Path(__file__).resolve().parent / "data"
_SOURCE_DATA = Path(__file__).resolve().parent.parent / "data"
DATA_ROOT = _PACKAGE_DATA if _PACKAGE_DATA.is_dir() else _SOURCE_DATA
SIGNATURE_PATH = DATA_ROOT / "signatures.json"
KORG_STRING_PATH = DATA_ROOT / "korg_strings.json"

_ASCII = re.compile(rb"[\x20-\x7e]{4,}")
_UTF16LE = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")
_UTF16BE = re.compile(rb"(?:\x00[\x20-\x7e]){4,}")
_UTF8 = re.compile(
    rb"(?:[\x20-\x7e]|[\xc2-\xdf][\x80-\xbf]|"
    rb"\xe0[\xa0-\xbf][\x80-\xbf]|[\xe1-\xec\xee\xef][\x80-\xbf]{2}|"
    rb"\xed[\x80-\x9f][\x80-\xbf]|\xf0[\x90-\xbf][\x80-\xbf]{2}|"
    rb"[\xf1-\xf3][\x80-\xbf]{3}|\xf4[\x80-\x8f][\x80-\xbf]{2}){4,}"
)

_KORG_TERMS: dict[str, tuple[str, ...]] = {
    "KORG": ("korg", "pa800", "pa3x", "pa4x", "pa5x"),
    "MIDI": ("midi", "mthd", "mtrk"),
    "STYLE": ("style", "sty"), "SOUND": ("sound", "pcg"),
    "PCM": ("pcm", "waveform"), "OSCILLATOR": ("oscillator", "osc "),
    "LFO": ("lfo",), "ARRANGER": ("arranger",), "DRUM": ("drum",),
    "PERFORMANCE": ("performance", "perform"),
    "FILE": (".bin", ".dat", ".img", ".set", ".bkp"),
    "PATH": ("\\", "/", ":\\"), "DLL": (".dll",), "EXE": (".exe",),
    "COMPRESSION": ("compress", "decompress", "comp16", "lzo", "zlib"),
    "CRYPTO": ("crypt", "encrypt", "decrypt", "aes", "sha256", "md5"),
    "DEBUG": ("debug", "assert", "trace"), "ERROR": ("error", "failed", "invalid", "exception"),
    "UI": ("dialog", "button", "menu", "window"),
    "REGISTRY": ("hkey_", "software\\", "registry"),
}

FORMAT_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x7fELF", "ELF", "ELF magic"),
    (b"\x89PNG\r\n\x1a\n", "PNG", "PNG signature"),
    (b"\xff\xd8\xff", "JPEG", "JPEG start marker"),
    (b"PK\x03\x04", "ZIP", "ZIP local-file header"),
    (b"PK\x05\x06", "ZIP", "ZIP empty-archive end record"),
    (b"Rar!\x1a\x07", "RAR", "RAR signature"),
    (b"7z\xbc\xaf\x27\x1c", "7Z", "7-Zip signature"),
    (b"\x1f\x8b\x08", "GZIP", "GZIP header"),
    (b"BZh", "BZIP2", "BZIP2 header"),
    (b"\xfd7zXZ\x00", "XZ", "XZ header"),
    (b"\x28\xb5\x2f\xfd", "ZSTD", "Zstandard frame magic"),
    (b"\x04\x22\x4d\x18", "LZ4_FRAME", "LZ4 frame magic"),
    (b"SQLite format 3\x00", "SQLITE", "SQLite database signature"),
    (b"RIFF", "RIFF_CANDIDATE", "RIFF container prefix"),
    (b"MThd", "MIDI_CANDIDATE", "Standard MIDI header prefix"),
    (b"%PDF-", "PDF", "PDF signature"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "OLE_CFB", "OLE compound-file signature"),
    (b"\x00\x00\x00\x18ftyp", "ISO_BMFF", "ISO base media file box"),
    (b"{\\rtf", "RTF", "Rich Text Format prefix"),
)


class MappedInput:
    """Read-only memory-mapped input. Empty files use an empty bytes object."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.file: BinaryIO | None = None
        self.data: mmap.mmap | bytes = b""

    def __enter__(self) -> MappedInput:
        self.file = self.path.open("rb")
        if self.path.stat().st_size:
            self.data = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if isinstance(self.data, mmap.mmap):
            self.data.close()
        if self.file is not None:
            self.file.close()


def _digest_file(path: Path, algorithms: tuple[str, ...]) -> dict[str, str]:
    digests = {name: hashlib.new(name, usedforsecurity=False) for name in algorithms}
    with path.open("rb") as stream:
        while block := stream.read(READ_CHUNK):
            for digest in digests.values():
                digest.update(block)
    return {name: digest.hexdigest() for name, digest in digests.items()}


def identity(path: str | os.PathLike[str], data: mmap.mmap | bytes) -> dict[str, Any]:
    source = Path(path)
    stat = source.stat()

    def timestamp(value: float) -> str:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()

    try:
        relative_path = Path(os.path.relpath(source.resolve(), Path.cwd().resolve())).as_posix()
    except ValueError:
        relative_path = source.name
    return {
        "filename": source.name,
        "full_path": str(source.resolve()),
        "relative_path": relative_path,
        "extension": source.suffix.lower(),
        "size": stat.st_size,
        "hashes": _digest_file(source, ("sha256", "sha1", "md5", "sha512")),
        "timestamps_utc": {"modified": timestamp(stat.st_mtime), "ctime_or_creation": timestamp(stat.st_ctime)},
        "timestamp_notes": "ctime_or_creation is creation time on Windows and metadata-change time on Unix-like systems",
        "first_64_hex": bytes(data[:64]).hex(" "),
        "first_256_hex": bytes(data[:256]).hex(" "),
        "last_256_hex": bytes(data[max(0, len(data) - 256):]).hex(" "),
    }


def detect_formats(data: mmap.mmap | bytes, extension: str = "") -> list[dict[str, Any]]:
    from .models import finding

    found: list[dict[str, Any]] = []
    prefix = bytes(data[:16])
    for signature, name, description in FORMAT_SIGNATURES:
        if prefix.startswith(signature):
            confidence = 0.99 if name not in ("RIFF_CANDIDATE", "MIDI_CANDIDATE") else 0.75
            found.append(finding("FORMAT", name, [description, f"signature at offset 0x0: {signature.hex(' ')}"], confidence, "OBSERVED", offset=0))
    if prefix.startswith(b"MZ"):
        if len(data) >= 0x40:
            pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
            if pe_offset + 4 <= len(data) and data[pe_offset:pe_offset + 4] == b"PE\0\0":
                found.append(finding("FORMAT", "PE", [f"MZ prefix and PE\\0\\0 signature at 0x{pe_offset:X}"], 1.0, "VERIFIED", offset=pe_offset))
            else:
                found.append(finding("FORMAT", "PE_CANDIDATE", ["MZ prefix found, but a valid PE signature was not verified"], 0.55, "CANDIDATE", offset=0))
        else:
            found.append(finding("FORMAT", "PE_CANDIDATE", ["Truncated MZ prefix"], 0.35, "CANDIDATE", offset=0))
    if prefix[:4] in (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe"):
        found.append(finding("FORMAT", "MACH_O_32", ["Mach-O 32-bit magic"], 0.99, "OBSERVED", offset=0))
    if prefix[:4] in (b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"):
        found.append(finding("FORMAT", "MACH_O_64", ["Mach-O 64-bit magic"], 0.99, "OBSERVED", offset=0))
    stripped = prefix.lstrip()
    if stripped.startswith((b"<?xml", b"<")) and b">" in stripped:
        found.append(finding("FORMAT", "XML_CANDIDATE", ["Text prefix resembles XML; well-formedness not checked"], 0.6, "CANDIDATE", offset=0))
    if stripped.startswith((b"{", b"[")):
        try:
            json.loads(bytes(data[: min(len(data), 4 * 1024 * 1024)]).decode("utf-8"))
            found.append(finding("FORMAT", "JSON", ["Complete JSON document parsed from the input"], 0.99, "VERIFIED", offset=0))
        except (UnicodeDecodeError, json.JSONDecodeError):
            found.append(finding("FORMAT", "JSON_CANDIDATE", ["Text prefix resembles JSON; complete document was not verified"], 0.4, "CANDIDATE", offset=0))
    if not found:
        found.append(finding("FORMAT", "RAW_BINARY", ["No configured file signature or text structure matched"], 1.0, "OBSERVED", offset=0))
    if extension:
        found.append(finding("EXTENSION_HINT", extension, ["Filename extension only; not proof of file format"], 0.2, "CANDIDATE"))
    return found


def entropy_value(block: bytes | mmap.mmap | memoryview) -> float:
    if not block:
        return 0.0
    counts = Counter(block)
    length = len(block)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _entropy_class(value: float) -> str:
    if value < 3.0:
        return "LOW"
    if value < 6.0:
        return "NORMAL"
    if value < 7.2:
        return "HIGH"
    return "VERY_HIGH"


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"windows": 0, "minimum": 0.0, "maximum": 0.0, "mean": 0.0}
    return {"windows": len(values), "minimum": round(min(values), 4), "maximum": round(max(values), 4), "mean": round(sum(values) / len(values), 4)}


def _entropy_from_counts(counts: Counter[int], length: int) -> float:
    if not length:
        return 0.0
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def entropy_analysis(data: mmap.mmap | bytes, base_window: int = 4096) -> dict[str, Any]:
    length = len(data)
    whole_counts: Counter[int] = Counter()
    sizes = (base_window, 16384, 65536)
    windows: dict[int, list[float]] = {size: [] for size in sizes}
    aggregates = {size: Counter() for size in sizes[1:]}
    aggregate_lengths = {size: 0 for size in sizes[1:]}
    regions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for start in range(0, length, base_window):
        end = min(length, start + base_window)
        counts = Counter(data[start:end])
        window_length = end - start
        whole_counts.update(counts)
        value = _entropy_from_counts(counts, window_length)
        windows[base_window].append(value)
        for size in sizes[1:]:
            aggregates[size].update(counts)
            aggregate_lengths[size] += window_length
            if aggregate_lengths[size] >= size or end == length:
                windows[size].append(_entropy_from_counts(aggregates[size], aggregate_lengths[size]))
                aggregates[size].clear()
                aggregate_lengths[size] = 0
        if value >= 6.8:
            if current and current["end"] == start:
                current["end"] = end
                current["size"] = end - current["start"]
                current["maximum_entropy"] = round(max(current["maximum_entropy"], value), 4)
                current["windows"] += 1
            else:
                current = {
                    "region_id": f"REGION_{len(regions) + 1:04d}", "start": start, "end": end,
                    "size": end - start, "window_size": base_window, "maximum_entropy": round(value, 4),
                    "windows": 1, "classification": _entropy_class(value), "status": "CANDIDATE",
                    "interpretation": "High entropy indicator only; not proof of encryption or compression",
                }
                regions.append(current)
        else:
            current = None
    return {
        "whole_file": {"value": round(_entropy_from_counts(whole_counts, length), 4), "classification": _entropy_class(_entropy_from_counts(whole_counts, length))},
        "window_summaries": {str(size): _summary(values) for size, values in windows.items()},
        "high_entropy_regions": regions[:5000],
    }

def _classify_string(text: str) -> list[str]:
    normalized = text.casefold()
    return [category for category, terms in _KORG_TERMS.items() if any(term in normalized for term in terms)]


def scan_strings(data: mmap.mmap | bytes, limit: int = MAX_STRING_RESULTS) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()

    def collect(pattern: re.Pattern[bytes], encoding: str, keep_ascii: bool = True) -> None:
        for match in pattern.finditer(data):
            start, end = match.start(), match.end()
            raw_length = end - start
            byte_limit = MAX_STRING_LENGTH * (2 if encoding.startswith("utf-16") else 4 if encoding == "utf-8" else 1)
            raw = bytes(data[start:min(end, start + byte_limit)])
            text = raw.decode(encoding, errors="ignore")
            if not keep_ascii and text.isascii():
                continue
            key = (start, raw_length, encoding)
            if key in seen:
                continue
            seen.add(key)
            shown = text[:MAX_STRING_LENGTH]
            results.append({
                "offset": start, "length": raw_length, "encoding": encoding.upper().replace("-", ""),
                "content": shown, "truncated": raw_length > byte_limit or len(text) > MAX_STRING_LENGTH,
                "categories": _classify_string(shown),
            })
            if len(results) >= limit:
                return

    collect(_ASCII, "ascii")
    if len(results) < limit:
        collect(_UTF16LE, "utf-16le")
    if len(results) < limit:
        collect(_UTF16BE, "utf-16be")
    if len(results) < limit:
        collect(_UTF8, "utf-8", keep_ascii=False)
    results.sort(key=lambda item: (item["offset"], item["encoding"]))
    return results[:limit]


def load_signatures() -> list[dict[str, Any]]:
    try:
        entries = json.loads(SIGNATURE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    result = []
    for item in entries:
        try:
            raw = bytes.fromhex(item["bytes"])
            mask = bytes.fromhex(item.get("mask", "ff" * len(raw)))
            if not raw or len(raw) != len(mask):
                continue
            result.append({**item, "raw": raw, "mask_bytes": mask})
        except (KeyError, ValueError, TypeError):
            continue
    return result


def scan_signatures(data: mmap.mmap | bytes, signatures: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    from .models import finding

    signatures = signatures if signatures is not None else load_signatures()
    found: list[dict[str, Any]] = []
    for item in signatures:
        pattern: bytes = item["raw"]
        mask: bytes = item["mask_bytes"]
        if all(byte == 0xFF for byte in mask):
            offset = 0
            while len(found) < MAX_SIGNATURE_RESULTS:
                offset = data.find(pattern, offset)
                if offset < 0:
                    break
                found.append({
                    "name": item["name"], "category": item.get("category", "unknown"),
                    "description": item.get("description", ""), "offset": offset,
                    "matched_bytes": pattern.hex(" "), "confidence": item.get("confidence", 0.99),
                    "status": item.get("status", "OBSERVED"), "source": item.get("source", "built-in signature database"),
                })
                offset += 1
        else:
            width = len(pattern)
            for offset in range(max(0, len(data) - width + 1)):
                if all((data[offset + index] & mask[index]) == (pattern[index] & mask[index]) for index in range(width)):
                    found.append({
                        "name": item["name"], "category": item.get("category", "unknown"),
                        "description": item.get("description", ""), "offset": offset,
                        "matched_bytes": bytes(data[offset:offset + width]).hex(" "),
                        "confidence": item.get("confidence", 0.8), "status": item.get("status", "CANDIDATE"),
                        "source": item.get("source", "built-in signature database"),
                    })
                    if len(found) >= MAX_SIGNATURE_RESULTS:
                        break
        if len(found) >= MAX_SIGNATURE_RESULTS:
            break
    return sorted(found, key=lambda item: item["offset"])


def scan_pointer_tables(data: mmap.mmap | bytes, max_scan_bytes: int = MAX_POINTER_SCAN) -> dict[str, Any]:
    scan_end = min(len(data), max_scan_bytes)
    candidates: list[dict[str, Any]] = []
    for width, endian in ((4, "little"), (4, "big"), (8, "little")):
        run_start: int | None = None
        run_values: list[int] = []
        run_count = 0
        monotonic = True
        previous: int | None = None
        full_end = scan_end - (scan_end % width)
        block_size = (1024 * 1024 // width) * width
        fmt = ("<" if endian == "little" else ">") + ("I" if width == 4 else "Q")
        for block_start in range(0, full_end, block_size):
            block_end = min(full_end, block_start + block_size)
            block = bytes(data[block_start:block_end])
            for index, (value,) in enumerate(struct.iter_unpack(fmt, block)):
                offset = block_start + index * width
                valid = 0x100 <= value < len(data) and value % 4 == 0
                if valid:
                    if run_start is None:
                        run_start = offset
                        run_values = []
                        run_count = 0
                        monotonic = True
                        previous = None
                    if previous is not None and value < previous:
                        monotonic = False
                    previous = value
                    run_count += 1
                    if len(run_values) < 64:
                        run_values.append(value)
                else:
                    if run_count >= 3 and monotonic:
                        candidates.append({
                            "source_offset": run_start, "width_bits": width * 8, "endian": endian,
                            "count": run_count, "targets": run_values, "confidence": round(min(0.78, 0.35 + 0.08 * min(run_count, 5)), 3),
                            "status": "CANDIDATE", "evidence": ["three or more aligned in-file values occur contiguously", "target interpretation is heuristic"],
                        })
                    run_start, run_values, run_count, previous, monotonic = None, [], 0, None, True
                    if len(candidates) >= 1000:
                        break
            if len(candidates) >= 1000:
                break
        if run_count >= 3 and monotonic and len(candidates) < 1000:
            candidates.append({
                "source_offset": run_start, "width_bits": width * 8, "endian": endian,
                "count": run_count, "targets": run_values, "confidence": round(min(0.78, 0.35 + 0.08 * min(run_count, 5)), 3),
                "status": "CANDIDATE", "evidence": ["three or more aligned in-file values occur contiguously", "target interpretation is heuristic"],
            })
    candidates.sort(key=lambda item: (-item["confidence"], item["source_offset"] or 0))
    return {"scan_limit": scan_end, "file_size": len(data), "truncated_scan": scan_end < len(data), "tables": candidates[:256]}


def korg_string_candidates(strings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        database = json.loads(KORG_STRING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        database = []
    terms = [(item["term"].casefold(), item) for item in database if isinstance(item, dict) and item.get("term")]
    result = []
    for item in strings:
        content = item["content"].casefold()
        for term, entry in terms:
            if term in content:
                result.append({
                    "term": entry["term"], "offset": item["offset"], "string": item["content"],
                    "category": entry.get("category", "KORG"), "status": "CANDIDATE", "confidence": 0.35,
                    "source": entry.get("source", "built-in lexical indicator; not format documentation"),
                    "evidence": ["matched a configured lexical indicator", "a string alone does not prove a Korg format"],
                })
    return result[:5000]


def xref_candidates(pointer_tables: dict[str, Any], strings: list[dict[str, Any]], signatures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    targets: dict[int, list[tuple[str, str]]] = {}
    for item in strings:
        targets.setdefault(item["offset"], []).append(("string", item["content"][:80]))
    for item in signatures:
        targets.setdefault(item["offset"], []).append(("signature", item["name"]))
    xrefs = []
    for table in pointer_tables["tables"]:
        for relative_index, target in enumerate(table["targets"]):
            if target in targets:
                for target_type, label in targets[target]:
                    xrefs.append({
                        "source_offset": table["source_offset"] + relative_index * (table["width_bits"] // 8),
                        "target_offset": target, "target_type": target_type, "label": label,
                        "type": "POINTER_TABLE_TARGET_CANDIDATE", "confidence": round(table["confidence"] * 0.85, 3),
                        "evidence": ["target equals an observed string or signature offset", "source belongs to a heuristic pointer-table candidate"],
                        "status": "CANDIDATE",
                    })
    return xrefs[:5000]