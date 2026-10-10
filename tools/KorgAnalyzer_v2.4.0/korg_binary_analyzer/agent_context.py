from __future__ import annotations

from typing import Any
import json


def _compact_decoder_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the evidence, drop the bulk.

    A real decode carries one entry per resource plus a slot table; that is
    useful in the report and the manifest but would swamp a compact context.
    """
    keep = (
        "decoder", "status", "validation", "container_kind", "container_notes",
        "resource_count", "compressed_resource_count", "raw_resource_count",
        "resource_decode_errors", "style_count", "style_names", "name_agreement",
        "decoded_container", "decoded_container_size", "output_directory", "manifest",
        "probe", "reason", "error",
    )
    compact = {key: result.get(key) for key in keep if key in result}
    names = compact.get("style_names")
    if isinstance(names, list) and len(names) > 60:
        compact["style_names"] = names[:60]
        compact["style_names_truncated"] = len(names)
    return compact


def build_agent_context(report: dict[str, Any], max_chars: int = 12000) -> dict[str, Any]:
    identity = report.get("facts", {}).get("identity", {})
    pe = report.get("evidence", {}).get("pe") or {}
    strings = report.get("evidence", {}).get("strings", [])
    important_strings = [item for item in strings if item.get("categories") or any(word in item.get("content", "").casefold() for word in ("korg", "comp16", "lfo", "pa5x", "decrypt", "compress"))]
    important_strings = [{**item, "content": item.get("content", "")[:160]} for item in important_strings[:80]]
    context = {
        "schema_version": report.get("schema_version", "1.0"),
        "identity": {key: identity.get(key) for key in ("filename", "relative_path", "size", "hashes")},
        "format": report.get("facts", {}).get("formats", []),
        "architecture": pe.get("coff_header"),
        "sections": [{key: section.get(key) for key in ("name", "rva", "virtual_size", "raw_offset", "raw_size", "permissions", "entropy")} for section in pe.get("sections", [])],
        "important_strings": important_strings[:80],
        "signatures": report.get("evidence", {}).get("signatures", [])[:80],
        "entropy_regions": report.get("evidence", {}).get("entropy", {}).get("high_entropy_regions", [])[:40],
        "pointers": report.get("evidence", {}).get("pointer_tables", {}).get("tables", [])[:40],
        "xrefs": report.get("evidence", {}).get("xrefs", [])[:40],
        "embedded_objects": report.get("evidence", {}).get("embedded_objects", [])[:40],
        "compression_candidates": report.get("candidates", {}).get("compression", [])[:40],
        "crypto_candidates": report.get("candidates", {}).get("crypto", {}).get("evidence", [])[:40],
        "korg_relevance": report.get("candidates", {}).get("korg_relevance", [])[:80],
        "decoder_results": [_compact_decoder_result(item) for item in report.get("decoded", {}).get("results", [])[:20]],
        "decoder_probes": report.get("decoded", {}).get("probes", [])[:40],
        "next_actions": report.get("next_actions", [])[:12],
        "limitations": report.get("unknown", [])[:20],
    }
    context_truncated = False
    low_priority = (
        "limitations", "decoder_probes", "decoder_results", "next_actions", "korg_relevance",
        "crypto_candidates", "compression_candidates", "embedded_objects", "xrefs", "pointers",
        "entropy_regions", "signatures", "important_strings", "sections",
    )
    # Trim to at most half of the largest list per pass.  The previous version
    # cut at len*3//4, which for a single-element list deleted nothing while
    # still reporting progress -- that looped forever on any offset large
    # enough to need more than one pass.
    for _ in range(256):
        if len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) <= max_chars:
            break
        shortened = False
        for key in low_priority:
            value = context.get(key)
            if isinstance(value, list) and value:
                if len(value) == 1:
                    context[key] = []
                else:
                    del value[len(value) // 2:]
                context_truncated = True
                shortened = True
                break
        if not shortened:
            break
    if context_truncated:
        context["context_truncated"] = True
        context["target_max_chars"] = max_chars
    return context


def build_agent_prompt(context: dict[str, Any]) -> str:
    payload = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    return (
        "Analyze the following binary forensic evidence. Do not invent undocumented structures. "
        "Separate facts, observations, candidates, and unknowns. Treat high entropy and lexical Korg matches as indicators, not proof. "
        "Recommend the next most useful reverse-engineering step and state what evidence would validate it.\n\n"
        f"Evidence JSON:\n{payload}\n"
    )