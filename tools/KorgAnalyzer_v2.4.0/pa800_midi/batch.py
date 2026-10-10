"""Batch mode -- run the pipeline over a whole corpus and summarise it.

This is what the user does once with his own material:

    python -m pa800_midi batch MIDI_FOLDER
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pa800_midi.pipeline import optimize
from pa800_midi.report import OptimizeResult


@dataclass
class BatchSummary:
    source_dir: str
    started: str = field(default_factory=lambda:
                         datetime.now(timezone.utc).isoformat(timespec="seconds"))
    rows: List[Dict[str, Any]] = field(default_factory=list)
    failed: List[Dict[str, str]] = field(default_factory=list)
    total_notes: int = 0
    total_validated: int = 0
    total_conflicts: int = 0
    total_applied: int = 0
    total_proposed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": "pa800_midi",
            "batch": True,
            "source_dir": self.source_dir,
            "started": self.started,
            "file_count": len(self.rows),
            "failed_count": len(self.failed),
            "total_notes": self.total_notes,
            "validated_files": self.total_validated,
            "key_range_conflicts": self.total_conflicts,
            "applied_operations": self.total_applied,
            "proposed_operations": self.total_proposed,
            "files": self.rows,
            "failed": self.failed,
        }


def run_batch(folder: str | Path, workspace: str | Path,
              rules: Optional[List[str]] = None,
              mode: str = "apply", limit: Optional[int] = None,
              factory_index: Optional[str | Path] = None,
              gold_index: Optional[str | Path] = None,
              demo_index: Optional[str | Path] = None,
              style_hint: Optional[str] = None,
              key_ranges=None) -> BatchSummary:
    source = Path(folder)
    files = sorted(p for p in source.rglob("*")
                   if p.is_file() and p.suffix.lower() in (".mid", ".midi", ".kar"))
    if limit:
        files = files[:limit]

    from pa800_midi.pipeline import default_workspace

    workspace = Path(workspace) if workspace else default_workspace()
    summary = BatchSummary(source_dir=str(source))
    for path in files:
        try:
            result: OptimizeResult = optimize(
                path, workspace=workspace, rules=rules, mode=mode,
                factory_index=factory_index, gold_index=gold_index,
                demo_index=demo_index, style_hint=style_hint,
                key_ranges=key_ranges,
            )
        except Exception as exc:                      # pragma: no cover - defensive
            summary.failed.append({"file": path.name, "error": str(exc)[:300]})
            continue

        facts = result.facts
        conflicts = len(facts.conflicts) if facts else 0
        applied = len(result.plan.applied) if result.plan else 0
        proposed = len(result.plan.proposed) if result.plan else 0
        valid = result.validation.ok if result.validation else None

        summary.rows.append({
            "file": path.name,
            "notes": facts.total_notes if facts else 0,
            "tracks": len(facts.tracks) if facts else 0,
            "tracks_with_notes": sum(1 for t in facts.tracks if t.note_count)
            if facts else 0,
            "key_range_conflicts": conflicts,
            "applied": applied,
            "proposed": proposed,
            "validation": result.validation.status if result.validation else "NISTA NIJE MIJENJANO",
            "valid": valid,
            "output": result.output_path or "",
            "report": f"{result.report_dir}/report.json" if result.report_dir else "",
        })
        summary.total_notes += facts.total_notes if facts else 0
        summary.total_conflicts += conflicts
        summary.total_applied += applied
        summary.total_proposed += proposed
        if valid:
            summary.total_validated += 1
    return summary


def write_batch_reports(summary: BatchSummary, directory: Path) -> Dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "batch_report.json"
    csv_path = directory / "batch_report.csv"
    text_path = directory / "batch_report.txt"

    json_path.write_text(json.dumps(summary.to_dict(), indent=2,
                                    ensure_ascii=False), encoding="utf-8")

    fields = ["file", "notes", "tracks", "tracks_with_notes",
              "key_range_conflicts", "applied", "proposed", "validation",
              "valid", "output", "report"]
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in summary.rows:
            writer.writerow(row)

    lines = [
        "PA800 MIDI OPTIMIZER -- BATCH",
        f"izvor   : {summary.source_dir}",
        f"pokrenut: {summary.started}",
        f"fajlova : {len(summary.rows)}  (neuspjelo: {len(summary.failed)})",
        f"nota    : {summary.total_notes}",
        f"konflikata (key range): {summary.total_conflicts}",
        f"primijenjenih operacija: {summary.total_applied}",
        f"predloga (candidate): {summary.total_proposed}",
        f"validiranih izvoza: {summary.total_validated}",
        "",
        "PO FAJLOVIMA",
    ]
    for row in summary.rows:
        lines.append(
            f"  {row['file'][:34]:34s} nota {row['notes']:6d}  "
            f"konfl. {row['key_range_conflicts']:3d}  "
            f"prim. {row['applied']:3d}  pred. {row['proposed']:3d}  "
            f"{row['validation']}"
        )
    if summary.failed:
        lines.append("")
        lines.append("NEUSPJELO")
        for item in summary.failed:
            lines.append(f"  {item['file']}: {item['error'][:90]}")
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "txt": str(text_path)}
