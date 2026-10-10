from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .binary import MappedInput
from .scanner import analyze_file

CHUNK_SIZE = 4096


def _region_entropy(data: Any, start: int, end: int) -> float:
    counts: Counter[int] = Counter()
    for offset in range(start, end, 1024 * 1024):
        counts.update(data[offset:min(end, offset + 1024 * 1024)])
    length = sum(counts.values())
    if not length:
        return 0.0
    import math
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _changed_regions(left: Any, right: Any) -> list[dict[str, Any]]:
    maximum = max(len(left), len(right))
    changed_chunks: list[tuple[int, int]] = []
    for start in range(0, maximum, CHUNK_SIZE):
        end = min(maximum, start + CHUNK_SIZE)
        left_block = bytes(left[start:min(end, len(left))])
        right_block = bytes(right[start:min(end, len(right))])
        if left_block != right_block:
            changed_chunks.append((start, end))
    groups: list[list[int]] = []
    for start, end in changed_chunks:
        if groups and groups[-1][1] == start:
            groups[-1][1] = end
        else:
            groups.append([start, end])
    regions = []
    for index, (start, end) in enumerate(groups[:20000], 1):
        regions.append({
            "region_id": f"DIFF_{index:04d}", "start": start, "end": end, "size": end - start,
            "entropy_before": round(_region_entropy(left, start, min(end, len(left))), 4) if start < len(left) else None,
            "entropy_after": round(_region_entropy(right, start, min(end, len(right))), 4) if start < len(right) else None,
            "possible_interpretation": "Changed byte region; meaning is unknown without format-specific parsing",
            "granularity": "4 KiB comparison blocks",
        })
    return regions


def compare_files(first: str | Path, second: str | Path, workspace: str | Path = "workspace") -> dict[str, Any]:
    left_report = analyze_file(first, workspace)
    right_report = analyze_file(second, workspace)
    left_id = left_report["facts"]["identity"]
    right_id = right_report["facts"]["identity"]
    with MappedInput(first) as left, MappedInput(second) as right:
        regions = _changed_regions(left.data, right.data)
    left_strings = {(item["offset"], item["content"]) for item in left_report["evidence"]["strings"]}
    right_strings = {(item["offset"], item["content"]) for item in right_report["evidence"]["strings"]}
    left_signatures = {(item["offset"], item["name"]) for item in left_report["evidence"]["signatures"]}
    right_signatures = {(item["offset"], item["name"]) for item in right_report["evidence"]["signatures"]}
    return {
        "schema_version": left_report["schema_version"],
        "tool_version": left_report["tool_version"],
        "generated_utc": left_report["generated_utc"],
        "facts": {"identity": left_id, "formats": left_report["facts"]["formats"]},
        "comparison": {
            "file_a": {"path": left_id["full_path"], "size": left_id["size"], "hashes": left_id["hashes"]},
            "file_b": {"path": right_id["full_path"], "size": right_id["size"], "hashes": right_id["hashes"]},
            "same_sha256": left_id["hashes"]["sha256"] == right_id["hashes"]["sha256"],
            "size_delta": right_id["size"] - left_id["size"],
            "changed_regions": regions,
            "changed_region_count": len(regions),
            "same_format_summary": [item.get("fact") for item in left_report["facts"]["formats"]] == [item.get("fact") for item in right_report["facts"]["formats"]],
            "strings_only_a": [item for item in left_report["evidence"]["strings"] if (item["offset"], item["content"]) not in right_strings][:500],
            "strings_only_b": [item for item in right_report["evidence"]["strings"] if (item["offset"], item["content"]) not in left_strings][:500],
            "signatures_only_a": [item for item in left_report["evidence"]["signatures"] if (item["offset"], item["name"]) not in right_signatures],
            "signatures_only_b": [item for item in right_report["evidence"]["signatures"] if (item["offset"], item["name"]) not in left_signatures],
            "pe_sections_a": (left_report.get("evidence", {}).get("pe") or {}).get("sections", []),
            "pe_sections_b": (right_report.get("evidence", {}).get("pe") or {}).get("sections", []),
        },
        "evidence": {"strings": [], "signatures": [], "entropy": left_report["evidence"]["entropy"], "pe": {}, "pointer_tables": {"tables": []}, "embedded_objects": [], "xrefs": []},
        "candidates": {"korg_relevance": [], "compression": [], "crypto": {"status": "NOT_ANALYZED_IN_DIFF", "evidence": []}, "transforms": {"candidates": []}, "structures": []},
        "decoded": {"status": "NOT_ATTEMPTED", "probes": [], "results": []},
        "validated": [],
        "unknown": [],
        "next_actions": [{"priority": "MEDIUM", "action": "Inspect grouped changed regions", "reason": "Difference locations are grouped at 4 KiB block granularity", "evidence": [f"{len(regions)} changed region(s)"], "offset": regions[0]["start"] if regions else None, "confidence": 0.99, "required_tool": "hex viewer / format parser"}],
    }