from __future__ import annotations

import os
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION, __version__
from .agent_context import build_agent_context
from .binary import (
    MappedInput, detect_formats, entropy_analysis, identity, korg_string_candidates,
    scan_pointer_tables, scan_signatures, scan_strings, xref_candidates,
)
from .decoder_engine import DecoderRegistry
from .embedded import find_embedded
from .pe import PEParseError, parse_pe
from .pe_extra import parse_pe_extensions
from .signals import crypto_candidates, next_actions, structure_candidates, transform_candidates


def _log(workspace: Path, path: Path, stage: str, result: str, error: str | None = None) -> None:
    log_path = workspace / "logs" / "analyzer.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    message = f"[{timestamp}] FILE={path.as_posix()} STAGE={stage} RESULT={result}"
    if error:
        message += f" ERROR={error.replace(chr(10), ' ')[:500]}"
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(message + "\n")


def _compression_candidates(signatures: list[dict[str, Any]], formats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    known = []
    compression_names = {"GZIP", "BZIP2", "XZ", "Zstandard", "LZ4 frame"}
    for hit in signatures:
        if hit["name"] in compression_names:
            known.append({
                "format": hit["name"], "offset": hit["offset"], "status": "CANDIDATE",
                "confidence": hit["confidence"], "evidence": [f"known stream signature at 0x{hit['offset']:X}"],
                "decoder_attempted": False,
            })
    for item in formats:
        label = item.get("fact", "")
        if label in ("GZIP", "BZIP2", "XZ", "ZSTD", "LZ4_FRAME") and not any(hit.get("format") == label for hit in known):
            known.append({"format": label, "offset": 0, "status": "CANDIDATE", "confidence": item["confidence"], "evidence": item["evidence"], "decoder_attempted": False})
    return known[:5000]


def _manual_references(source: Path, report: dict[str, Any]) -> dict[str, Any]:
    """Point at the official manual for whatever model this file belongs to.

    Matching is deliberately shallow: the model string must appear in the file
    name, its folder names, the file's own strings, or the format signature.
    A guess would be worse than nothing here, so an unmatched file returns an
    empty reference list rather than a plausible-looking one.
    """
    from .manuals import catalogue_size, human_size, load_catalog, products as catalog_products

    catalog = catalog_products(load_catalog())
    if not catalog:
        return {"status": "CATALOG_EMPTY", "matches": []}

    haystack_parts = [source.name, source.parent.name, source.parent.parent.name]
    strings = report.get("evidence", {}).get("strings", [])
    if isinstance(strings, list):
        haystack_parts.extend(
            item.get("content", "") for item in strings[:400] if isinstance(item, dict)
        )
    for finding in report.get("facts", {}).get("formats", []):
        haystack_parts.append(str(finding.get("fact", "")))
    haystack = " ".join(haystack_parts).casefold().replace(" ", "")

    matches = []
    for name, item in sorted(catalog.items(), key=lambda pair: -len(pair[0])):
        key = name.casefold().replace(" ", "")
        if key and key in haystack:
            matches.append({
                "model": name,
                "brand": item["brand"],
                "family": item.get("family"),
                "container_era": item.get("container_era"),
                "product_page": item.get("product_page"),
                "documents": [
                    {
                        "doc": doc.get("doc"),
                        "depth": doc.get("depth"),
                        "size": doc.get("size"),
                        "url": doc.get("direct"),
                    }
                    for doc in item.get("docs", [])
                ],
            })
        if len(matches) >= 4:
            break

    return {
        "status": "MATCHED" if matches else "NO_MODEL_MATCH",
        "library_size": human_size(catalogue_size()),
        "library_note": "Official manufacturer download links; nothing is re-hosted.",
        "matches": matches,
    }


def _hex_window(data: Any, offset: int, before: int = 128, after: int = 512) -> dict[str, Any]:
    start = max(0, offset - before)
    end = min(len(data), offset + after)
    chunk = bytes(data[start:end])
    lines = []
    for index in range(0, len(chunk), 16):
        row = chunk[index:index + 16]
        hex_part = " ".join(f"{byte:02X}" for byte in row)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in row)
        lines.append(f"{start + index:08X}  {hex_part:<47}  {ascii_part}")
    return {"offset": offset, "start": start, "end": end, "hex": "\n".join(lines)}


def analyze_file(path: str | os.PathLike[str], workspace: str | os.PathLike[str] = "workspace") -> dict[str, Any]:
    source = Path(path)
    work = Path(workspace)
    work.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        raise FileNotFoundError(f"Input is not a readable regular file: {source}")
    warnings: list[dict[str, Any]] = []
    _log(work, source, "identity", "START")
    try:
        with MappedInput(source) as mapped:
            data = mapped.data
            file_identity = identity(source, data)
            _log(work, source, "identity", "COMPLETE")
            formats = detect_formats(data, source.suffix.lower())
            _log(work, source, "format_detection", "COMPLETE")
            unsupported_formats = [item["fact"] for item in formats if item.get("kind") == "FORMAT" and item.get("fact") not in ("PE", "PE_CANDIDATE", "RAW_BINARY")]
            if unsupported_formats:
                warnings.append({"stage": "known_format_parser", "status": "UNSUPPORTED", "message": f"Detailed semantic parser not implemented for: {', '.join(unsupported_formats)}; see any separate decoder result for stream validation"})
            strings = scan_strings(data)
            _log(work, source, "strings", f"COMPLETE:{len(strings)}")
            entropy = entropy_analysis(data)
            _log(work, source, "entropy", "COMPLETE")
            signatures = scan_signatures(data)
            _log(work, source, "signatures", f"COMPLETE:{len(signatures)}")
            pointers = scan_pointer_tables(data)
            _log(work, source, "pointer_scan", f"COMPLETE:{len(pointers['tables'])}")
            embedded = find_embedded(data, signatures)
            _log(work, source, "embedded_scan", f"COMPLETE:{len(embedded)}")
            xrefs = xref_candidates(pointers, strings, signatures)
            korg = korg_string_candidates(strings)
            registry = DecoderRegistry()
            decoder_probes = registry.probe(data)
            pe = None
            if any(item.get("fact") == "PE" for item in formats):
                try:
                    pe = parse_pe(data)
                    pe.update(parse_pe_extensions(data, pe))
                    _log(work, source, "pe_parse", "COMPLETE")
                except (PEParseError, ValueError, struct.error) as error:
                    warnings.append({"stage": "pe_parse", "status": "ERROR", "message": f"{type(error).__name__}: {error}"})
                    _log(work, source, "pe_parse", "ERROR", str(error))
            raw_integer_views = _integer_views(data)
            compression = _compression_candidates(signatures, formats)
            crypto = crypto_candidates(strings, signatures, entropy, pe)
            transforms = transform_candidates(data)
            evidence = {
                "format_detections": formats,
                "pe": pe,
                "strings": strings,
                "entropy": entropy,
                "signatures": signatures,
                "pointer_tables": pointers,
                "xrefs": xrefs,
                "embedded_objects": embedded,
                "raw_integer_views": raw_integer_views,
            }
            candidates = {
                "korg_relevance": korg,
                "compression": compression,
                "crypto": crypto,
                "transforms": transforms,
                "structures": structure_candidates(pointers),
            }
            report: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "tool_version": __version__,
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "facts": {"identity": file_identity, "formats": formats},
                "evidence": evidence,
                "candidates": candidates,
                "decoded": {"status": "NOT_ATTEMPTED", "probes": decoder_probes, "results": []},
                "validated": [],
                "unknown": warnings,
                "next_actions": [],
            }
            report["next_actions"] = next_actions(report)
            offsets = []
            if pe and pe.get("entry_point", {}).get("file_offset") is not None:
                offsets.append(pe["entry_point"]["file_offset"])
            offsets.extend(item["offset"] for item in signatures[:8])
            offsets.extend(item["embedded_start"] for item in embedded[:8])
            report["evidence"]["hex_windows"] = [_hex_window(data, offset) for offset in dict.fromkeys(offsets) if 0 <= offset < len(data)][:20]
            report["manuals"] = _manual_references(source, report)
            report["agent_context"] = build_agent_context(report)
            return report
    except (PermissionError, OSError) as error:
        _log(work, source, "analysis", "ERROR", str(error))
        raise


def _integer_views(data: Any, prefix_size: int = 64) -> dict[str, Any]:
    available = min(len(data), prefix_size)
    result: dict[str, Any] = {"bytes_covered": available, "interpretation": "Numeric views of prefix only; values are not asserted to be offsets or fields"}
    for width in (1, 2, 4, 8):
        for endian in ("little", "big"):
            count = available // width
            fmt = ("<" if endian == "little" else ">") + {1: "B", 2: "H", 4: "I", 8: "Q"}[width]
            values = [value[0] for value in struct.iter_unpack(fmt, bytes(data[:count * width]))]
            result[f"u{width * 8}_{endian}"] = [{"offset": index * width, "value": value} for index, value in enumerate(values)]
    return result


def scan_directory(path: str | os.PathLike[str], workspace: str | os.PathLike[str] = "workspace") -> dict[str, Any]:
    root = Path(path)
    work = Path(workspace)
    if not root.is_dir():
        raise NotADirectoryError(f"Input is not a directory: {root}")
    excluded = work.resolve()
    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_directories = []
        for directory in sorted(directories):
            child = current_path / directory
            if child.is_symlink():
                continue
            try:
                if child.resolve() == excluded or excluded in child.resolve().parents:
                    continue
            except OSError:
                continue
            kept_directories.append(directory)
        directories[:] = kept_directories
        for filename in sorted(filenames):
            source = current_path / filename
            if source.is_symlink() or not source.is_file():
                continue
            try:
                report = analyze_file(source, work)
                identity_data = report["facts"]["identity"]
                fmt = next((item["fact"] for item in report["facts"]["formats"] if item.get("kind") == "FORMAT"), "RAW_BINARY")
                item = {
                    "path": source.relative_to(root).as_posix(), "size": identity_data["size"],
                    "sha256": identity_data["hashes"]["sha256"], "extension": identity_data["extension"],
                    "format": fmt, "korg_candidate_count": len(report["candidates"]["korg_relevance"]),
                    "embedded_count": len(report["evidence"]["embedded_objects"]),
                    "high_entropy_region_count": len(report["evidence"]["entropy"]["high_entropy_regions"]),
                    "interesting": bool(report["candidates"]["korg_relevance"] or report["evidence"]["embedded_objects"] or (report.get("evidence", {}).get("pe") or {}).get("overlay")),
                }
                items.append(item)
            except Exception as error:
                entry = {"path": source.relative_to(root).as_posix(), "status": "ERROR", "error": f"{type(error).__name__}: {error}"}
                errors.append(entry)
                _log(work, source, "directory_scan", "ERROR", str(error))
    hash_groups: dict[str, list[str]] = {}
    family_groups: dict[str, list[str]] = {}
    type_groups: dict[str, list[str]] = {}
    for item in items:
        hash_groups.setdefault(item["sha256"], []).append(item["path"])
        type_groups.setdefault(item["format"], []).append(item["path"])
        parent = Path(item["path"]).parent.as_posix()
        family_groups.setdefault(parent, []).append(item["path"])
    duplicates = [{"sha256": digest, "paths": paths} for digest, paths in hash_groups.items() if len(paths) > 1]
    families = [{"family_key": parent, "relationship": "same-directory family candidate", "paths": paths} for parent, paths in family_groups.items() if len(paths) > 1]
    return {
        "schema_version": SCHEMA_VERSION, "tool_version": __version__, "generated_utc": datetime.now(timezone.utc).isoformat(),
        "root": str(root.resolve()), "files_scanned": len(items), "errors_count": len(errors),
        "files": items, "errors": errors,
        "grouped_by_type": {key: len(value) for key, value in sorted(type_groups.items())},
        "duplicate_groups": duplicates, "family_candidates": families,
        "interesting_files": [item for item in items if item["interesting"]],
        "notes": ["Symlinks and the output workspace subtree are skipped.", "Same-directory membership is a family candidate, not a dependency claim."],
    }