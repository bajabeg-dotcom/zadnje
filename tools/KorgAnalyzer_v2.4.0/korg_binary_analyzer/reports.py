from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_context import build_agent_context, build_agent_prompt


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def render_text(report: dict[str, Any]) -> str:
    identity = report.get("facts", {}).get("identity", {})
    formats = report.get("facts", {}).get("formats", [])
    pe = report.get("evidence", {}).get("pe") or {}
    entropy = report.get("evidence", {}).get("entropy", {})
    comparison = report.get("comparison")
    lines = [
        "=" * 64,
        "KORG BINARY FORENSIC ANALYZER",
        "=" * 64,
        f"FILE: {identity.get('filename', 'unknown')}",
        f"PATH: {identity.get('full_path', 'unknown')}",
        f"SIZE: {identity.get('size', 0)} bytes",
        f"SHA256: {identity.get('hashes', {}).get('sha256', 'unknown')}",
        "",
        "FORMAT FACTS / OBSERVATIONS:",
    ]
    if formats:
        for item in formats:
            lines.append(f"  {item.get('fact')} [{item.get('status')}, confidence {item.get('confidence')}]")
    else:
        lines.append("  No format findings")
    if pe:
        lines.extend([
            f"PE: {pe.get('coff_header', {}).get('machine_name')} / {pe.get('optional_header', {}).get('bitness')}-bit",
            f"ENTRY POINT: RVA {pe.get('entry_point', {}).get('rva')!r}; file offset {pe.get('entry_point', {}).get('file_offset')!r}",
            f"SECTIONS: {len(pe.get('sections', []))}; IMPORT MODULES: {len(pe.get('imports', []))}; EXPORTS: {len(pe.get('exports', {}).get('symbols', []))}",
            f"OVERLAY: {pe.get('overlay') or 'none detected'}",
            f"PACKER STATUS: {pe.get('packer_detection', {}).get('status', 'UNKNOWN')}",
        ])
    lines.extend([
        f"FILE ENTROPY: {entropy.get('whole_file', {}).get('value', 0)} ({entropy.get('whole_file', {}).get('classification', 'UNKNOWN')})",
        "NOTE: High entropy is not proof of encryption.",
        "",
        "FACTS:",
        f"  String records: {len(report.get('evidence', {}).get('strings', []))}",
        f"  Source integrity: {report.get('source_integrity_check', {}).get('status', 'not rechecked')}",
        "",
        "EVIDENCE:",
        f"  Signature hits: {len(report.get('evidence', {}).get('signatures', []))}",
        f"  Embedded-object candidates: {len(report.get('evidence', {}).get('embedded_objects', []))}",
        f"  Pointer-table candidates: {len(report.get('evidence', {}).get('pointer_tables', {}).get('tables', []))}",
        f"  High-entropy regions: {len(entropy.get('high_entropy_regions', []))} (indicator only)",
        "",
        "CANDIDATES:",
    ])
    candidate_groups = report.get("candidates", {})
    for item in candidate_groups.get("korg_relevance", [])[:30]:
        lines.append(f"  KORG {item.get('term')} at 0x{item.get('offset', 0):X}: {item.get('status')} ({item.get('confidence')})")
    for item in candidate_groups.get("compression", [])[:30]:
        lines.append(f"  COMPRESSION {item.get('format')} at 0x{item.get('offset', 0):X}: {item.get('status')}")
    crypto = candidate_groups.get("crypto", {})
    lines.append(f"  CRYPTO: {crypto.get('status', 'UNKNOWN')} ({len(crypto.get('evidence', []))} evidence item(s)); verified algorithm: {crypto.get('verified_algorithm')}")
    for item in report.get("decoded", {}).get("probes", [])[:30]:
        lines.append(f"  DECODER {item.get('name')}: {item.get('status')} [{item.get('implementation')}]")
    lines.extend(["", "DECODED:"])
    results = report.get("decoded", {}).get("results", [])
    if not results:
        lines.append("  No decoder was executed")
    for item in results:
        lines.append(f"  {item.get('decoder')}: {item.get('status')} — {item.get('output') or item.get('output_directory') or item.get('reason') or item.get('error', '')}")
        if item.get("resource_count"):
            lines.append(
                f"    resources: {item['resource_count']}"
                f" (compressed {item.get('compressed_resource_count', 0)},"
                f" raw {item.get('raw_resource_count', 0)},"
                f" rejected {item.get('resource_decode_errors', 0)})"
            )
        evidence = (item.get("validation") or {}).get("evidence", [])
        for entry in evidence:
            lines.append(f"    check: {entry}")
        slots = item.get("slots") or []
        if slots:
            agreement = item.get("name_agreement") or {}
            lines.append(
                f"    styles: {len(slots)}"
                + (
                    f" (name directory agrees on {agreement.get('agreed', 0)}/{agreement.get('compared', 0)})"
                    if agreement.get("compared") else ""
                )
            )
            for slot in slots[:200]:
                flag = "" if slot.get("agrees") is not False else "  <-- name directory differs"
                lines.append(
                    f"      [{slot['slot']:>3}] {slot.get('directory_name') or '-'} | "
                    f"stream: {slot.get('decoded_name') or '-'} | elements {slot.get('element_count') or 0}{flag}"
                )
    lines.extend(["", "VALIDATED:"])
    validated = report.get("validated", [])
    if not validated:
        lines.append("  No decoded output has validated status")
    for item in validated:
        lines.append(f"  {item.get('decoder')}: DECODED_VALIDATED — {item.get('output') or item.get('output_directory') or item.get('path', '')}")
    lines.extend(["", "UNKNOWN:"])
    if report.get("unknown"):
        for item in report["unknown"][:50]:
            lines.append(f"  [{item.get('status', 'UNKNOWN')}] {item.get('stage', 'analysis')}: {item.get('message', item)}")
    else:
        lines.append("  None")
    lines.extend(["", "NEXT ACTIONS:"])
    for index, item in enumerate(report.get("next_actions", [])[:20], start=1):
        lines.append(f"  {index}. [{item.get('priority')}] {item.get('action')} — {item.get('reason')}")
        lines.append(f"     Evidence: {'; '.join(item.get('evidence', []))}")
    manuals = report.get("manuals")
    if manuals:
        lines.extend(["", "OFFICIAL MANUALS:"])
        if manuals.get("matches"):
            for item in manuals["matches"]:
                lines.append(f"  {item['brand']} {item['model']} — {item.get('product_page') or ''}")
                if item.get("container_era"):
                    lines.append(f"      format note: {item['container_era']}")
                for doc in item.get("documents", [])[:12]:
                    marker = "REF " if doc.get("depth") == "reference" else "    "
                    lines.append(
                        f"      {marker}{str(doc.get('doc')):<28} {str(doc.get('size') or '?'):>9}  {doc.get('url')}"
                    )
                lines.append("      (REF = reference-grade documentation)")
        else:
            lines.append("  No catalogue model name matched this file; no reference offered.")
        lines.append(f"  Library: {manuals.get('library_size')} across Korg / Yamaha / Roland arranger manuals.")
        lines.append("  Fetch with: python -m korg_binary_analyzer manuals --search <model> --download")
    if comparison:
        lines.extend(["", "COMPARE:", f"  File B: {comparison['file_b']['path']}", f"  Same SHA256: {comparison['same_sha256']}", f"  Changed regions: {comparison['changed_region_count']}"])
        for region in comparison.get("changed_regions", [])[:20]:
            lines.append(f"  0x{region['start']:X}-0x{region['end']:X} ({region['size']} bytes; 4 KiB grouped region)")
    lines.extend(["", "All parser conclusions are evidence-qualified; candidate does not mean verified.", "=" * 64, ""])
    return "\n".join(lines)


def _unique_run_directory(workspace: Path, command: str, digest: str) -> Path:
    parent = workspace / "reports"
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = parent / f"{command}-{digest[:12]}-{stamp}"
    target = base
    index = 1
    while target.exists():
        target = parent / f"{base.name}-{index}"
        index += 1
    target.mkdir()
    return target


def write_bundle(report: dict[str, Any], workspace: Path, command: str) -> Path:
    digest = report.get("facts", {}).get("identity", {}).get("hashes", {}).get("sha256", "unknown")
    output_dir = _unique_run_directory(workspace, command, digest)
    report["artifacts"] = {"directory": str(output_dir), "manifest": "artifact_manifest.json"}
    (output_dir / "report.json").write_text(_json_text(report), encoding="utf-8")
    (output_dir / "report.txt").write_text(render_text(report), encoding="utf-8")
    evidence = report.get("evidence", {})
    candidates = report.get("candidates", {})
    sidecars = {
        "sections.json": evidence.get("pe", {}).get("sections", []) if evidence.get("pe") else [],
        "signatures.json": evidence.get("signatures", []),
        "pointers.json": evidence.get("pointer_tables", {}),
        "xref.json": evidence.get("xrefs", []),
        "embedded.json": evidence.get("embedded_objects", []),
        "decoded.json": report.get("decoded", {}),
        "structures.json": candidates.get("structures", []),
        "actions.json": report.get("next_actions", []),
    }
    for name, content in sidecars.items():
        (output_dir / name).write_text(_json_text(content), encoding="utf-8")
    with (output_dir / "strings.txt").open("w", encoding="utf-8", newline="\n") as stream:
        for item in evidence.get("strings", []):
            stream.write(f"0x{item['offset']:08X}\t{item['encoding']}\t{item['length']}\t{','.join(item['categories'])}\t{item['content']}\n")
    with (output_dir / "entropy.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("region_id", "start", "end", "size", "window_size", "maximum_entropy", "classification", "status"))
        for item in evidence.get("entropy", {}).get("high_entropy_regions", []):
            writer.writerow((item["region_id"], item["start"], item["end"], item["size"], item["window_size"], item["maximum_entropy"], item["classification"], item["status"]))
    context = build_agent_context(report)
    (output_dir / "agent_context.json").write_text(_json_text(context), encoding="utf-8")
    (output_dir / "agent_prompt.txt").write_text(build_agent_prompt(context), encoding="utf-8")
    hashes = {path.name: _sha256(path) for path in sorted(output_dir.iterdir()) if path.is_file()}
    manifest = {"schema_version": report.get("schema_version", "1.0"), "source_sha256": digest, "artifacts": hashes, "manifest_self_hash": "omitted to avoid recursive hashing"}
    (output_dir / "artifact_manifest.json").write_text(_json_text(manifest), encoding="utf-8")
    return output_dir


def write_scan_bundle(report: dict[str, Any], workspace: Path) -> Path:
    digest = hashlib.sha256(report.get("root", "").encode("utf-8", errors="replace")).hexdigest()
    output_dir = _unique_run_directory(workspace, "scan", digest)
    (output_dir / "report.json").write_text(_json_text(report), encoding="utf-8")
    lines = [
        "KORG BINARY FORENSIC ANALYZER — DIRECTORY SCAN",
        f"ROOT: {report.get('root')}",
        f"FILES: {report.get('files_scanned')} scanned; {report.get('errors_count')} errors",
        "",
        "GROUPED BY TYPE:",
    ]
    for kind, count in report.get("grouped_by_type", {}).items():
        lines.append(f"  {kind}: {count}")
    lines.extend(("", "DUPLICATES:"))
    for group in report.get("duplicate_groups", []):
        lines.append(f"  SHA256 {group['sha256']}: {', '.join(group['paths'])}")
    lines.extend(("", "INTERESTING FILES:"))
    for item in report.get("interesting_files", []):
        lines.append(f"  {item['path']} — {item['format']}; Korg indicators={item['korg_candidate_count']}; embedded={item['embedded_count']}")
    lines.extend(("", "ERRORS:"))
    for item in report.get("errors", []):
        lines.append(f"  {item['path']}: {item['error']}")
    (output_dir / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    hashes = {path.name: _sha256(path) for path in sorted(output_dir.iterdir()) if path.is_file()}
    (output_dir / "artifact_manifest.json").write_text(_json_text({"schema_version": report["schema_version"], "artifacts": hashes}), encoding="utf-8")
    return output_dir