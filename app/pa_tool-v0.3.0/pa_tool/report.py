"""Report rendering: every row carries a status and an evidence column.

Nothing is written outside the caller-supplied directory. The renderer never
fabricates a value: it prints exactly what the Result objects contain.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .status import Result, Status


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _escape(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def results_to_md(title: str, results: Iterable[Result], extra: dict | None = None) -> str:
    lines = [f"# {title}", ""]
    lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("Status legend: `VERIFIED` = measured and reproducible · "
                 "`NEPOTVRDENO` = evidence but no exclusivity · "
                 "`UNKNOWN` = no sample/data (reported, never guessed)")
    lines.append("")
    if extra:
        lines.append("## Context")
        lines.append("")
        for k, v in extra.items():
            lines.append(f"- **{k}:** {v}")
        lines.append("")

    for res in results:
        lines.append(f"## {res.name}")
        lines.append("")
        lines.append(f"**Result status:** `{res.status.value}` · **decision:** `{res.decision()}`")
        lines.append("")
        if res.findings:
            lines.append("| key | value | status | evidence |")
            lines.append("|---|---|---|---|")
            for f in res.findings:
                ev = _escape(f.evidence) if f.evidence else ""
                note = f" — {_escape(f.note)}" if f.note else ""
                lines.append(f"| `{f.key}` | {_escape(f.value)} | `{f.status.value}` | {ev}{note} |")
            lines.append("")
        if res.errors:
            lines.append("**Errors:**")
            lines.append("")
            for e in res.errors:
                lines.append(f"- {_escape(e)}")
            lines.append("")
    return "\n".join(lines)


def results_to_json(results: Iterable[Result], extra: dict | None = None) -> dict:
    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": "pa_tool",
        "results": [r.to_dict() for r in results],
    }
    if extra:
        payload["context"] = extra
    return payload


def save_report(out_dir: str | Path, base_name: str, title: str,
                results: Iterable[Result], extra: dict | None = None) -> tuple[Path, Path]:
    """Write <out_dir>/<base_name>.md and .json. Returns (md_path, json_path)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / f"{base_name}.md"
    json_path = out / f"{base_name}.json"
    md_path.write_text(results_to_md(title, results, extra), encoding="utf-8")
    json_path.write_text(
        json.dumps(results_to_json(results, extra), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return md_path, json_path


def integrity_record(paths: Iterable[str | Path]) -> dict:
    """Hash every input so callers can prove nothing was modified."""
    record = {}
    for p in paths:
        p = Path(p)
        if p.is_file():
            record[str(p)] = sha256_file(p)
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    record[str(f)] = sha256_file(f)
    return record
