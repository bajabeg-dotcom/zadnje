"""File/folder type and model detection.

Rules come from the RE findings:
  * dialog filters = the definitive extension inventory (PA_EDITABLE_PATTERNS §3.1)
  * container magic 01 00 01 14 (korg_tokens.json)
  * .SET / .KST are FOLDERS, not single files (verified on real samples)
  * model fingerprints are the OpenSound SUB groupings + canonical order

Unknown stays unknown: a file that only looks like a KORG file is not claimed to be one.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import container, models
from .status import Status

# Extension inventory. Verified from the UTF-16 dialog filters.
CONTENT_EXTS = {
    ".STY": "style",
    ".STG": "style_pa5x",
    ".PCG": "pcg",
    ".PAD": "pad",
    ".PDC": "pad_pa5x",
    ".PRF": "performance",
    ".KSC": "keyboard_set",
    ".KMP": "multisample",
    ".KSF": "sample",
    ".PCM": "pcm",
    ".MID": "midi",
    ".MIDI": "midi",
    ".WAV": "audio",
    ".AIF": "audio",
    ".AIFF": "audio",
    ".RX2": "rex",
    ".REX": "rex",
    ".SF2": "soundfont",
    ".MTP": "matrix_preset",
    ".MXP": "maxx_audio_preset",
    ".MPR": "midi_preset",
    ".QTP": "qt_preset",
    ".GBL": "global",
    ".SBD": "songbook_db",
    ".SBL": "songbook_list",
    ".CSC": "chord_sequence",
    ".LNG": "language",
    ".BKP": "backup",
    ".PKG": "package",
}

FOLDER_KINDS = {".SET": "set", ".KST": "kst"}

MAGIC_MAP = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"BM", "bmp"),
    (b"MZ", "pe"),
    (b"RIFF", "riff"),
    (b"PK\x03\x04", "zip"),
]

# 01 00 01 14 is checked via models.container_magic() to keep one source of truth.


@dataclass
class FileProfile:
    path: Path
    kind: str = "unknown"
    status: Status = Status.UNKNOWN
    evidence: list[str] = field(default_factory=list)
    container: Optional[container.ContainerReport] = None
    model: Optional[str] = None
    model_status: Status = Status.UNKNOWN
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "status": self.status.value,
            "model": self.model,
            "model_status": self.model_status.value,
            "evidence": list(self.evidence),
            "notes": list(self.notes),
            "container": None if self.container is None else self.container.to_dict(),
        }


def _kind_from_suffix(path: Path) -> tuple[str, Status]:
    suffix = path.suffix.upper()
    if suffix in CONTENT_EXTS:
        return CONTENT_EXTS[suffix], Status.VERIFIED
    return "unknown", Status.UNKNOWN


def fingerprint_file(path: str | Path) -> FileProfile:
    """Inspect one file. Read-only."""
    p = Path(path)
    prof = FileProfile(path=p)

    if not p.is_file():
        prof.notes.append("not a regular file")
        return prof

    kind, st = _kind_from_suffix(p)
    prof.kind = kind
    prof.status = st
    if st is Status.VERIFIED:
        prof.evidence.append(f"extension {p.suffix.upper()} is in the verified dialog filter inventory")

    try:
        with p.open("rb") as stream:
            head = stream.read(64)
    except OSError as exc:
        prof.notes.append(f"cannot read: {exc}")
        prof.status = Status.UNKNOWN
        return prof

    magic = models.container_magic()
    if head[:4] == magic:
        prof.evidence.append("KORG container magic 01 00 01 14 @+0")
        prof.container = container.parse_container(p)
        if prof.kind == "unknown":
            prof.kind = "korg_container"
            prof.status = prof.container.status
        else:
            prof.status = prof.container.status
        if prof.container.header and prof.container.header.declared_ok is False:
            prof.notes.append(
                f"declared payload length differs by {prof.container.header.delta} byte(s) "
                "(documented for the two factory ROM containers)"
            )
        return prof

    for sig, name in MAGIC_MAP:
        if head.startswith(sig):
            prof.notes.append(f"magic matches {name}; not a KORG container")
            if prof.kind == "unknown":
                prof.status = Status.NEPOTVRDENO
            return prof

    if kind == "midi" and head[:4] == b"MThd":
        prof.status = Status.VERIFIED
        prof.evidence.append("MThd header")
    return prof


def _read_metadir(folder: Path) -> Optional[dict]:
    md = folder / "metadir.json"
    if not md.is_file():
        return None
    try:
        with md.open("r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def detect_set_folder(path: str | Path) -> FileProfile:
    """Analyse a .SET folder against the verified structure rules."""
    p = Path(path)
    prof = FileProfile(path=p)

    if not p.is_dir():
        prof.notes.append("not a directory")
        return prof

    prof.kind = "set"
    prof.evidence.append(".SET is a FOLDER (verified on real samples)")

    metadir = _read_metadir(p)
    if metadir is not None:
        prof.evidence.append("metadir.json present")
    else:
        prof.notes.append("metadir.json absent at the top level")

    # sample categories documented for the SET sample
    subdirs = sorted(d.name for d in p.iterdir() if d.is_dir())
    documented = {"GLOBAL", "MULTISMP", "PCM", "SOUND"}
    hits = documented.intersection({s.upper() for s in subdirs})
    if hits:
        prof.evidence.append(f"documented categories present: {', '.join(sorted(hits))}")

    # folder / file budget from the verified limits
    max_folders = models.limit_for("foldera u SET-u")
    max_files = models.limit_for("fajlova po banci")
    folder_count = len(subdirs)
    if max_folders is not None:
        if folder_count > max_folders:
            prof.notes.append(f"folder count {folder_count} exceeds documented limit {max_folders}")
        else:
            prof.evidence.append(f"folder count {folder_count} <= {max_folders}")

    styles = [f for f in p.rglob("*") if f.is_file() and f.suffix.upper() in (".STY", ".STG")]
    if styles:
        prof.evidence.append(f"{len(styles)} style file(s) found")
    if max_files is not None and styles and len(styles) > max_files:
        prof.notes.append(f"style count {len(styles)} exceeds documented per-bank limit {max_files}")

    # Model fingerprint: the RE notes tie model detection to the OpenSound SUB
    # groupings, which live inside the Manager binary, not inside a SET folder.
    # Every style file here is generic, so the model cannot be proven from bytes.
    prof.model = None
    prof.model_status = Status.UNKNOWN
    prof.notes.append(
        "model cannot be proven from SET bytes alone: detection in PA Manager uses the "
        "OpenSound SUB grouping in its own binary"
    )

    prof.status = Status.VERIFIED if prof.evidence else Status.NEPOTVRDENO
    return prof


def detect_kst_folder(path: str | Path) -> FileProfile:
    """Analyse a .KST folder (Pa5X family)."""
    p = Path(path)
    prof = FileProfile(path=p)

    if not p.is_dir():
        prof.notes.append("not a directory")
        return prof

    prof.kind = "kst"
    prof.evidence.append(".KST is a FOLDER with containers (verified in the Pa5X package)")

    known_tops = {"MATRIXPRESET", "MAXXAUDIOPRESET", "MIDIPRESET", "QTPRESET", "SETTINGS", "SONGBOOK"}
    tops = {d.name.upper() for d in p.iterdir() if d.is_dir()}
    hits = known_tops.intersection(tops)
    if hits:
        prof.evidence.append(f"documented containers present: {', '.join(sorted(hits))}")

    if (_read_metadir(p)) is not None:
        prof.evidence.append("metadir.json present")

    prof.model = "Pa5X"
    prof.model_status = Status.HIGH
    prof.notes.append(
        "Pa5X is the documented .KST family (Convert to KORG PA5X .KST @0x003EC1B6); "
        "marked HIGH because a .KST folder does not itself carry a model marker"
    )
    prof.status = Status.VERIFIED if prof.evidence else Status.NEPOTVRDENO
    return prof


def detect(path: str | Path) -> FileProfile:
    """Dispatch by path type, then by suffix."""
    p = Path(path)
    if p.is_dir():
        suffix = p.suffix.upper()
        if suffix == ".SET":
            return detect_set_folder(p)
        if suffix == ".KST":
            return detect_kst_folder(p)
        return FileProfile(path=p, kind="directory", status=Status.UNKNOWN,
                           notes=["directory without a .SET/.KST suffix"])
    return fingerprint_file(p)


def decode_percent_name(name: str) -> str:
    """metadir.json stores '/' as '%2F' (verified). Decode for display."""
    return name.replace("%2F", "/").replace("%2f", "/")


def encode_percent_name(name: str) -> str:
    """Re-encode '/' as '%2F' when writing a metadir.json entry."""
    return name.replace("/", "%2F")
