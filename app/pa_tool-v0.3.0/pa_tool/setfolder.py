"""`.SET` / `.KST` folder inventory.

Counts what is actually on disk and compares it against the verified limits. Nothing
is written; the input folder is only read.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import models
from .status import Status


@dataclass
class FolderEntry:
    name: str
    count: int
    bytes_: int


@dataclass
class SetReport:
    path: Path
    name: str
    categories: list[FolderEntry] = field(default_factory=list)
    metadir: bool = False
    lo_files: int = 0
    by_extension: dict[str, int] = field(default_factory=dict)
    total_bytes: int = 0
    status: Status = Status.NEPOTVRDENO
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "name": self.name,
            "metadir": self.metadir,
            "categories": [c.__dict__ for c in self.categories],
            "loose_files": self.lo_files,
            "by_extension": dict(sorted(self.by_extension.items())),
            "total_bytes": self.total_bytes,
            "status": self.status.value,
            "notes": list(self.notes),
        }


def inventory_set(path: str | Path) -> SetReport:
    """Walk a .SET folder and inventory it. Read-only."""
    p = Path(path)
    rep = SetReport(path=p, name=p.name)

    if not p.is_dir():
        rep.notes.append("not a directory")
        rep.status = Status.UNKNOWN
        return rep

    rep.metadir = (p / "metadir.json").is_file()
    if rep.metadir:
        rep.notes.append("metadir.json present at the top level")
    else:
        rep.notes.append("metadir.json absent at the top level")

    for child in sorted(p.iterdir()):
        if child.is_dir():
            files = [f for f in child.rglob("*") if f.is_file()]
            rep.categories.append(FolderEntry(
                name=child.name,
                count=len(files),
                bytes_=sum(f.stat().st_size for f in files),
            ))

    for f in sorted(p.rglob("*")):
        if f.is_file():
            rep.lo_files += 1
            rep.total_bytes += f.stat().st_size
            ext = f.suffix.upper() or "<none>"
            rep.by_extension[ext] = rep.by_extension.get(ext, 0) + 1

    # budget checks against the verified limits
    max_folders = models.limit_for("foldera u SET-u")
    max_files = models.limit_for("fajlova po banci")
    if max_folders is not None:
        if len(rep.categories) > max_folders:
            rep.notes.append(f"{len(rep.categories)} folders exceeds the documented limit {max_folders}")
        else:
            rep.notes.append(f"{len(rep.categories)} folders <= {max_folders}")
    if max_files is not None:
        style_files = sum(v for k, v in rep.by_extension.items() if k in (".STY", ".STG"))
        if style_files > max_files:
            rep.notes.append(f"{style_files} style files exceeds the per-bank limit {max_files}")

    rep.status = Status.VERIFIED if (rep.metadir and rep.by_extension) else Status.NEPOTVRDENO
    return rep


def hash_manifest(path: str | Path, algorithm: str = "sha256") -> dict:
    """sha256 for every file under a folder — used to prove inputs are untouched."""
    import hashlib

    p = Path(path)
    out = {}
    if not p.is_dir():
        return out
    for f in sorted(p.rglob("*")):
        if f.is_file():
            h = hashlib.new(algorithm)
            with f.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            out[str(f)] = h.hexdigest()
    return out


def parse_metadir(path: str | Path) -> dict:
    """Read one metadir.json. Returns the raw object plus decoded file names."""
    p = Path(path)
    if p.is_dir():
        p = p / "metadir.json"
    if not p.is_file():
        return {"found": False, "path": str(p)}
    try:
        with p.open("r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {"found": True, "path": str(p), "error": str(exc)}

    from .detect import decode_percent_name
    decoded = None
    if isinstance(data.get("files"), list):
        decoded = [
            dict(e, file=decode_percent_name(str(e.get("file", ""))))
            for e in data["files"] if isinstance(e, dict)
        ]
    return {
        "found": True,
        "path": str(p),
        "has_subdirs": isinstance(data.get("subdirs"), list),
        "has_files": isinstance(data.get("files"), list),
        "default_file_icon": data.get("defaultFileIcon"),
        "subdir_count": len(data.get("subdirs", [])) if isinstance(data.get("subdirs"), list) else 0,
        "file_count": len(data.get("files", [])) if isinstance(data.get("files"), list) else 0,
        "files_raw": data.get("files") if isinstance(data.get("files"), list) else None,
        "files_decoded": decoded,
    }
