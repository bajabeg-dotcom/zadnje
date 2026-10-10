"""Complete evidence-aware model and file validator registry.

This module is the high-level ``validator.py`` requested for every model and
known PA Manager file class.  It does not turn missing evidence into a PASS:

* every model known to ``data/models.json`` receives a profile;
* every extension in the verified dialog-filter inventory receives a rule;
* folders are traversed safely and every regular file gets its own report;
* supported structures receive strict parsers;
* opaque/proprietary structures are reported as ``UNKNOWN``, never accepted by
  extension alone;
* model-to-model compatibility is a complete matrix whose cells can be
  EXACT, PRESERVE_ONLY, PARTIAL, REFUSED, or UNKNOWN.

Inputs are read-only.  Reports are written only to a new explicit path.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from . import container, detect, models, pcg
from .status import Status


PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"
INFO = "INFO"

COMPAT_EXACT = "EXACT"
COMPAT_PARTIAL = "PARTIAL"
COMPAT_PRESERVE = "PRESERVE_ONLY"
COMPAT_REFUSED = "REFUSED"
COMPAT_UNKNOWN = "UNKNOWN"

MAX_FILES = 10_000
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_SINGLE_FILE = 512 * 1024 * 1024


def _normalise_model(value: str) -> str:
    return "".join(character for character in (value or "").upper() if character.isalnum())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ModelProfile:
    """One model/variant validation profile."""

    key: str
    name: str
    canonical: bool
    ordinal: Optional[int]
    base_model: Optional[str]
    ram_mb: Optional[int]
    ram_candidates: tuple[int, ...]
    ram_status: str
    import_sub: Optional[str]
    format_group: str
    sound_import: str
    status: str
    evidence: tuple[str, ...]
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ram_candidates"] = list(self.ram_candidates)
        data["evidence"] = list(self.evidence)
        return data


@dataclass(frozen=True)
class FileRule:
    """Validation policy for one extension or folder suffix."""

    extension: str
    kind: str
    structure: str
    import_known: bool
    export_known: bool
    status: str
    evidence: str
    note: str = ""

    @property
    def is_folder(self) -> bool:
        return self.structure == "FOLDER"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    verdict: str
    message: str
    evidence: str = ""
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileValidationReport:
    path: str
    relative_path: str
    kind: str = "unknown"
    extension: str = ""
    target_model: Optional[str] = None
    source_sha256: Optional[str] = None
    size: Optional[int] = None
    findings: list[ValidationFinding] = field(default_factory=list)
    children: list["FileValidationReport"] = field(default_factory=list)

    def add(
        self,
        code: str,
        verdict: str,
        message: str,
        evidence: str = "",
        value: Any = None,
    ) -> ValidationFinding:
        finding = ValidationFinding(code, verdict, message, evidence, value)
        self.findings.append(finding)
        return finding

    @property
    def decision(self) -> str:
        verdicts = [finding.verdict for finding in self.findings]
        verdicts.extend(child.decision for child in self.children)
        if FAIL in verdicts:
            return FAIL
        if UNKNOWN in verdicts:
            return UNKNOWN
        return PASS

    @property
    def file_count(self) -> int:
        return (1 if self.size is not None else 0) + sum(child.file_count for child in self.children)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "relative_path": self.relative_path,
            "kind": self.kind,
            "extension": self.extension,
            "target_model": self.target_model,
            "source_sha256": self.source_sha256,
            "size": self.size,
            "decision": self.decision,
            "file_count": self.file_count,
            "findings": [finding.to_dict() for finding in self.findings],
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(frozen=True)
class CompatibilityDecision:
    source_model: str
    target_model: str
    extension: str
    level: str
    executable: bool
    reason: str
    evidence: str
    losses: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["losses"] = list(self.losses)
        return data


def _model_names_from_data() -> set[str]:
    names = {model.name for model in models.canonical_models()}
    for entry in models.ram_table():
        names.update(entry.get("models", []))
    for entry in models.import_layer():
        names.update(entry.get("models", []))
    for entry in models.variants():
        names.add(entry["name"])
    return names


def _base_model_for(name: str) -> Optional[str]:
    key = _normalise_model(name)
    explicit = {
        "PA1XPRO": "PA1X",
        "PA1XPROELITE": "PA1X",
        "PA3XLE": "Pa3X",
        "PA4XOSNEXT": "Pa4X",
        "PA588": "PA500",
    }
    return explicit.get(key)


def _format_group_for(name: str, import_sub: Optional[str]) -> str:
    if import_sub:
        return f"opensound:{import_sub}"
    key = _normalise_model(name)
    if key == "PA5X":
        return "pa5x-kst"
    base = _base_model_for(name)
    if base:
        base_sub = models.sus_for(base)
        if base_sub:
            return f"opensound:{base_sub}"
    # PA800 deliberately remains an evidence-level family member.  It has no
    # own OpenSound SUB, and the existing evidence does not prove byte identity
    # with PA2X or PA500.
    for family in models.hierarchy().get("families", []):
        if any(_normalise_model(member) == key for member in family.get("members", [])):
            return "hierarchy:" + "/".join(family["members"])
    return "unproven"


def build_model_profiles() -> dict[str, ModelProfile]:
    """Build profiles for every model and variant present in project evidence."""

    canonical = {_normalise_model(item.name): item for item in models.canonical_models()}
    variant_rows = {
        _normalise_model(item["name"]): item for item in models.variants()
    }
    profiles: dict[str, ModelProfile] = {}
    for name in sorted(_model_names_from_data(), key=lambda item: _normalise_model(item)):
        key = _normalise_model(name)
        canon = canonical.get(key)
        variant = variant_rows.get(key)
        ram = models.ram_for(name)
        import_sub = models.sus_for(name)
        base = _base_model_for(name)
        evidence: list[str] = []
        if canon:
            evidence.append(
                f"canonical model tables: A={canon.table_a}, B={canon.table_b}"
            )
        if ram.get("evidence"):
            evidence.append(ram["evidence"])
        if import_sub:
            evidence.append(f"OpenSound SUB {import_sub}")
        if variant:
            evidence.append(
                variant.get("evidence") or variant.get("evidence_offset", "variant table")
            )
        if not evidence:
            evidence.append("models.json evidence inventory")

        status = "VERIFIED" if canon or (variant and variant.get("status") == "VERIFIED") else "HIGH"
        if ram.get("ambiguous") or (variant and variant.get("status") == "HIGH"):
            status = "HIGH"
        sound_import = "REFUSED" if key == "PA5X" else "ALLOWED_BY_HANDLER"
        if import_sub is None and key != "PA5X":
            sound_import = "UNPROVEN_HANDLER"
        profile = ModelProfile(
            key=key,
            name=name,
            canonical=canon is not None,
            ordinal=canon.ordinal if canon else None,
            base_model=base,
            ram_mb=ram.get("size_mb"),
            ram_candidates=tuple(ram.get("candidates", [])),
            ram_status=ram.get("status", Status.UNKNOWN.value),
            import_sub=import_sub,
            format_group=_format_group_for(name, import_sub),
            sound_import=sound_import,
            status=status,
            evidence=tuple(evidence),
            note=(variant or {}).get("note", ""),
        )
        profiles[key] = profile
    return profiles


MODEL_PROFILES = build_model_profiles()


def resolve_model(name: str) -> Optional[ModelProfile]:
    return MODEL_PROFILES.get(_normalise_model(name))


def _file_structure(extension: str) -> str:
    extension = extension.upper()
    structures = {
        ".SET": "FOLDER",
        ".KST": "FOLDER",
        ".STY": "KORG_RESOURCE",
        ".PCG": "PCG",
        ".PAD": "KORG_RESOURCE",
        ".PRF": "KORG_RESOURCE",
        ".GBL": "KORG_RESOURCE",
        ".KMP": "KORG_OR_CHUNKED",
        ".KSF": "KORG_OR_CHUNKED",
        ".PCM": "KORG_OR_CHUNKED",
        ".MID": "MIDI",
        ".MIDI": "MIDI",
        ".WAV": "WAVE",
        ".AIF": "AIFF",
        ".AIFF": "AIFF",
        ".RX2": "REX",
        ".REX": "REX",
        ".SF2": "SOUNDFONT",
        ".PKG": "PACKAGE",
    }
    return structures.get(extension, "OPAQUE_PROPRIETARY")


def build_file_rules() -> dict[str, FileRule]:
    """Build one rule for every verified file-filter extension plus SET/KST."""

    known = models.extensions_known()
    imports = {item.upper() for item in known.get("import", [])}
    exports = {item.upper() for item in known.get("export", [])}
    rules: dict[str, FileRule] = {}
    for extension, kind in sorted(detect.CONTENT_EXTS.items()):
        structure = _file_structure(extension)
        status = "VERIFIED" if structure not in {"OPAQUE_PROPRIETARY", "KORG_OR_CHUNKED"} else "PARTIAL"
        rules[extension] = FileRule(
            extension=extension,
            kind=kind,
            structure=structure,
            import_known=extension in imports,
            export_known=extension in exports,
            status=status,
            evidence="rejections.json extensions_known + detect.py dialog-filter inventory",
            note=(
                "extension is known, but the complete payload schema is not proven"
                if structure == "OPAQUE_PROPRIETARY"
                else ""
            ),
        )
    for extension, kind in detect.FOLDER_KINDS.items():
        rules[extension] = FileRule(
            extension=extension,
            kind=kind,
            structure="FOLDER",
            import_known=extension in imports,
            export_known=extension in exports,
            status="VERIFIED",
            evidence="verified SET/KST folder identity",
        )
    # ZIP is an accepted transport for bounded native SET analysis, not a Korg
    # instrument format.  Keeping it explicit prevents it from being mistaken
    # for an unknown resource.
    rules[".ZIP"] = FileRule(
        extension=".ZIP",
        kind="archive",
        structure="ZIP",
        import_known=False,
        export_known=False,
        status="VERIFIED",
        evidence="native.workspace bounded ZIP transport",
        note="transport archive; not itself an instrument resource format",
    )
    return rules


FILE_RULES = build_file_rules()


def file_rule(value: str | Path) -> Optional[FileRule]:
    text = str(value)
    suffix = Path(text).suffix.upper()
    if text.upper() in FILE_RULES:
        suffix = text.upper()
    return FILE_RULES.get(suffix)


def validator_coverage() -> dict[str, Any]:
    return {
        "models": len(MODEL_PROFILES),
        "model_names": [profile.name for profile in MODEL_PROFILES.values()],
        "file_rules": len(FILE_RULES),
        "extensions": sorted(FILE_RULES),
        "pair_cells": len(MODEL_PROFILES) * len(MODEL_PROFILES) * len(FILE_RULES),
    }


def _validate_model(
    report: FileValidationReport,
    model_name: Optional[str],
    rule: Optional[FileRule],
    required_ram_mb: Optional[int],
) -> Optional[ModelProfile]:
    if not model_name:
        report.add(
            "MODEL_NOT_SUPPLIED",
            UNKNOWN,
            "target model was not supplied; model compatibility cannot be certified",
            "model detection from resource bytes is not proven",
        )
        return None
    profile = resolve_model(model_name)
    if profile is None:
        report.add(
            "MODEL_UNKNOWN",
            FAIL,
            f"model {model_name!r} is not in the evidence-backed model registry",
            "data/models.json",
        )
        return None
    report.target_model = profile.name
    report.add(
        "MODEL_PROFILE",
        PASS,
        f"using validator profile {profile.name}",
        "; ".join(profile.evidence),
        profile.to_dict(),
    )
    if required_ram_mb is not None:
        if profile.ram_mb is not None:
            verdict = PASS if required_ram_mb <= profile.ram_mb else FAIL
            report.add(
                "MODEL_RAM",
                verdict,
                f"required {required_ram_mb} MB; documented base RAM {profile.ram_mb} MB",
                "; ".join(profile.evidence),
            )
        elif profile.ram_candidates:
            report.add(
                "MODEL_RAM_AMBIGUOUS",
                UNKNOWN,
                f"required {required_ram_mb} MB; model has candidates {list(profile.ram_candidates)}",
                "; ".join(profile.evidence),
            )
        else:
            report.add(
                "MODEL_RAM_UNKNOWN",
                UNKNOWN,
                "required RAM was supplied, but available model RAM is not proven",
                "; ".join(profile.evidence),
            )
    if rule and profile.sound_import == "REFUSED" and rule.extension in {
        ".PCG",
        ".KMP",
        ".KSF",
        ".PCM",
    }:
        report.add(
            "PA5X_SOUND_REFUSAL",
            FAIL,
            "PA5X SOUND import is explicitly refused by the recovered Manager path",
            "0x002DD968: PA5X Sounds have been detected and will not be loaded.",
        )
    elif rule and not rule.import_known:
        report.add(
            "IMPORT_PATH_NOT_PROVEN",
            UNKNOWN,
            f"{rule.extension} is known to the UI, but no general import contract is proven",
            rule.evidence,
        )
    return profile


def _validate_midi(path: Path, report: FileValidationReport, workflow: str) -> None:
    try:
        data = path.read_bytes()
    except OSError as exc:
        report.add("MIDI_READ", FAIL, f"cannot read MIDI: {exc}")
        return
    if len(data) < 14 or data[:4] != b"MThd":
        report.add("MIDI_HEADER", FAIL, "missing MThd header")
        return
    header_length = int.from_bytes(data[4:8], "big")
    if header_length < 6 or 8 + header_length > len(data):
        report.add("MIDI_HEADER_LENGTH", FAIL, f"invalid MThd length {header_length}")
        return
    format_type, track_count, division = struct.unpack_from(">HHH", data, 8)
    report.add(
        "MIDI_HEADER",
        PASS if format_type in (0, 1, 2) and track_count > 0 else FAIL,
        f"SMF type {format_type}, tracks {track_count}, division 0x{division:04X}",
        "Standard MIDI File MThd structure",
        {"format": format_type, "tracks": track_count, "division": division},
    )
    if division & 0x8000:
        report.add(
            "MIDI_DIVISION",
            UNKNOWN,
            "SMPTE time division is structurally valid but not proven for PA Manager import",
            "PA Manager PPQN messages @0x002D6FFC",
        )
        ppqn = None
    else:
        ppqn = division
        if ppqn <= 0:
            report.add("MIDI_PPQN", FAIL, "PPQN must be positive")
        elif workflow == "manager_import" and ppqn != 384:
            report.add(
                "MIDI_PPQN_MANAGER",
                FAIL,
                f"PPQN {ppqn} differs from the recovered Manager target of 384",
                "0x002D7038: Please convert to the supported PPQN of 384.",
            )
        else:
            report.add(
                "MIDI_PPQN",
                PASS,
                f"PPQN {ppqn}",
                "SMF division; Manager target 384 is workflow-specific",
            )
    if workflow == "manager_import" and not (format_type == 1 and track_count == 1):
        report.add(
            "MIDI_MANAGER_LAYOUT",
            FAIL,
            "recovered Manager import requires format 1 with a single track",
            "0x002D6F14 and 0x002D6F8C",
        )
    elif workflow == "pa_style_import" and not (format_type == 0 and track_count == 1):
        report.add(
            "MIDI_STYLE_LAYOUT",
            FAIL,
            "official Pa style import requires SMF type 0 with one track",
            "Korg Pa-series Import/Export SMF tutorial",
        )

    cursor = 8 + header_length
    parsed_tracks = 0
    while cursor + 8 <= len(data) and parsed_tracks < track_count:
        if data[cursor : cursor + 4] != b"MTrk":
            report.add(
                "MIDI_TRACK_CHUNK",
                FAIL,
                f"expected MTrk at offset 0x{cursor:X}",
            )
            return
        length = int.from_bytes(data[cursor + 4 : cursor + 8], "big")
        start = cursor + 8
        end = start + length
        if end > len(data):
            report.add(
                "MIDI_TRACK_TRUNCATED",
                FAIL,
                f"track {parsed_tracks} declares {length} bytes past end of file",
            )
            return
        try:
            event_count, has_eot = _scan_midi_track(data[start:end])
        except ValueError as exc:
            report.add("MIDI_EVENT_STREAM", FAIL, f"track {parsed_tracks}: {exc}")
            return
        limit = models.limit_for("dogadjaja po MIDI traci")
        verdict = PASS if limit is None or event_count <= limit else FAIL
        report.add(
            "MIDI_TRACK",
            verdict,
            f"track {parsed_tracks}: {event_count} events, EOT={'yes' if has_eot else 'no'}",
            f"event limit {limit}" if limit is not None else "SMF event parse",
            {"track": parsed_tracks, "events": event_count, "end_of_track": has_eot},
        )
        if not has_eot:
            report.add(
                "MIDI_EOT",
                UNKNOWN,
                f"track {parsed_tracks} has no explicit End-of-Track meta event",
            )
        parsed_tracks += 1
        cursor = end
    if parsed_tracks != track_count:
        report.add(
            "MIDI_TRACK_COUNT",
            FAIL,
            f"header declares {track_count} tracks but {parsed_tracks} were parsed",
        )
    elif cursor != len(data):
        report.add(
            "MIDI_TRAILING_BYTES",
            UNKNOWN,
            f"{len(data) - cursor} byte(s) remain after declared tracks",
        )


def _read_vlq(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if position >= len(data):
            raise ValueError("truncated variable-length quantity")
        byte = data[position]
        position += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, position
    raise ValueError("variable-length quantity exceeds four bytes")


def _scan_midi_track(data: bytes) -> tuple[int, bool]:
    position = 0
    running_status: Optional[int] = None
    event_count = 0
    has_eot = False
    while position < len(data):
        _, position = _read_vlq(data, position)
        if position >= len(data):
            raise ValueError("event is missing a status/data byte")
        first = data[position]
        if first & 0x80:
            status = first
            position += 1
            if 0x80 <= status <= 0xEF:
                running_status = status
            else:
                running_status = None
        else:
            if running_status is None:
                raise ValueError("running-status data appears without channel status")
            status = running_status
        if 0x80 <= status <= 0xEF:
            size = 1 if status >> 4 in (0xC, 0xD) else 2
            # If running status was used, the first data byte remains at position.
            if position + size > len(data):
                raise ValueError("truncated channel message")
            if any(byte & 0x80 for byte in data[position : position + size]):
                raise ValueError("channel data byte has high bit set")
            position += size
        elif status == 0xFF:
            if position >= len(data):
                raise ValueError("truncated meta event")
            meta_type = data[position]
            position += 1
            length, position = _read_vlq(data, position)
            if position + length > len(data):
                raise ValueError("truncated meta payload")
            if meta_type == 0x2F:
                if length != 0:
                    raise ValueError("End-of-Track meta event has non-zero length")
                has_eot = True
            position += length
        elif status in (0xF0, 0xF7):
            length, position = _read_vlq(data, position)
            if position + length > len(data):
                raise ValueError("truncated SysEx payload")
            position += length
        else:
            raise ValueError(f"system status 0x{status:02X} is not valid in an SMF track")
        event_count += 1
    return event_count, has_eot


def _validate_wave(path: Path, report: FileValidationReport) -> None:
    try:
        with wave.open(str(path), "rb") as source:
            channels = source.getnchannels()
            width = source.getsampwidth()
            rate = source.getframerate()
            frames = source.getnframes()
            compression = source.getcomptype()
    except (wave.Error, OSError, EOFError) as exc:
        report.add("WAVE_STRUCTURE", FAIL, f"invalid/unreadable WAVE: {exc}")
        return
    report.add(
        "WAVE_STRUCTURE",
        PASS if compression == "NONE" else UNKNOWN,
        f"{rate} Hz, {width * 8} bit, {channels} channel(s), {frames} frames, {compression}",
        "RIFF/WAVE fmt and data chunks",
        {
            "sample_rate_hz": rate,
            "bits": width * 8,
            "channels": channels,
            "frames": frames,
            "compression": compression,
        },
    )
    canonical = models.canonical_sample_format()
    matches = (
        rate == canonical["sample_rate_hz"]
        and width * 8 == canonical["bits"]
        and channels == canonical["channels"]
        and compression == "NONE"
    )
    report.add(
        "WAVE_CANONICAL_TARGET",
        PASS if matches else UNKNOWN,
        "matches 44100 Hz / 16 bit / mono target"
        if matches
        else "does not match the fully documented 44100 Hz / 16 bit / mono target; conversion is required for that workflow",
        "0x00250114",
    )


def _validate_aiff(path: Path, report: FileValidationReport) -> None:
    try:
        data = path.read_bytes()
    except OSError as exc:
        report.add("AIFF_READ", FAIL, f"cannot read AIFF: {exc}")
        return
    if len(data) < 12 or data[:4] != b"FORM" or data[8:12] not in {b"AIFF", b"AIFC"}:
        report.add("AIFF_HEADER", FAIL, "missing FORM/AIFF or FORM/AIFC header")
        return
    declared = int.from_bytes(data[4:8], "big") + 8
    report.add(
        "AIFF_HEADER",
        PASS if declared <= len(data) else FAIL,
        f"{data[8:12].decode('ascii')} container, declared {declared} bytes, actual {len(data)}",
        "AIFF FORM structure",
    )
    position = 12
    found_comm = False
    while position + 8 <= len(data):
        chunk = data[position : position + 4]
        size = int.from_bytes(data[position + 4 : position + 8], "big")
        payload = position + 8
        end = payload + size
        if end > len(data):
            report.add("AIFF_CHUNK", FAIL, f"chunk {chunk!r} exceeds file size")
            return
        if chunk == b"COMM" and size >= 18:
            channels = int.from_bytes(data[payload : payload + 2], "big")
            frames = int.from_bytes(data[payload + 2 : payload + 6], "big")
            bits = int.from_bytes(data[payload + 6 : payload + 8], "big")
            found_comm = True
            report.add(
                "AIFF_COMM",
                PASS,
                f"{bits} bit, {channels} channel(s), {frames} frames; 80-bit rate retained as raw evidence",
                "AIFF COMM chunk",
                {
                    "channels": channels,
                    "frames": frames,
                    "bits": bits,
                    "sample_rate_extended_hex": data[payload + 8 : payload + 18].hex(),
                },
            )
        position = end + (size & 1)
    if not found_comm:
        report.add("AIFF_COMM", FAIL, "COMM chunk was not found")


def _validate_soundfont(path: Path, report: FileValidationReport) -> None:
    try:
        head = path.read_bytes()[:12]
    except OSError as exc:
        report.add("SF2_READ", FAIL, f"cannot read SoundFont: {exc}")
        return
    valid = len(head) == 12 and head[:4] == b"RIFF" and head[8:12] == b"sfbk"
    report.add(
        "SF2_RIFF",
        PASS if valid else FAIL,
        "RIFF/sfbk SoundFont container" if valid else "missing RIFF/sfbk signature",
        "SoundFont 2 container signature",
    )


def _validate_rex(path: Path, report: FileValidationReport) -> None:
    try:
        head = path.read_bytes()[:16]
    except OSError as exc:
        report.add("REX_READ", FAIL, f"cannot read REX: {exc}")
        return
    if head[:4] in {b"RIFF", b"CAT ", b"FORM"}:
        report.add(
            "REX_CONTAINER",
            PASS,
            f"container signature {head[:4].decode('ascii', 'replace')}",
        )
    else:
        report.add(
            "REX_CONTAINER",
            UNKNOWN,
            "REX extension is known, but this build has no proven full REX parser/signature contract",
            "REX import is platform-gated @0x00251F48",
        )


def _validate_zip(path: Path, report: FileValidationReport) -> None:
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            names: set[str] = set()
            expanded = 0
            for info in archive.infolist():
                normal = info.filename.replace("\\", "/")
                parts = Path(normal).parts
                if normal.startswith("/") or ".." in parts or "\x00" in normal:
                    report.add(
                        "ZIP_PATH",
                        FAIL,
                        f"unsafe archive member path: {info.filename!r}",
                    )
                    return
                folded = normal.casefold()
                if folded in names:
                    report.add(
                        "ZIP_DUPLICATE",
                        FAIL,
                        f"case-insensitive duplicate path: {normal}",
                    )
                    return
                names.add(folded)
                expanded += info.file_size
                if info.file_size > MAX_SINGLE_FILE:
                    report.add(
                        "ZIP_MEMBER_LIMIT",
                        FAIL,
                        f"member {normal} exceeds {MAX_SINGLE_FILE} bytes",
                    )
                    return
            report.add(
                "ZIP_STRUCTURE",
                PASS,
                f"{len(names)} safe member(s), {expanded} expanded bytes",
                "bounded path and size inventory",
            )
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        report.add("ZIP_STRUCTURE", FAIL, f"invalid/unreadable ZIP: {exc}")


def _validate_container(path: Path, report: FileValidationReport, deep: bool) -> None:
    parsed = container.parse_container(path)
    if not parsed.is_container:
        report.add(
            "KORG_CONTAINER",
            FAIL,
            f"KORG container magic is absent: {parsed.error or 'first bytes differ'}",
            "01 00 01 14",
        )
        return
    report.add(
        "KORG_CONTAINER",
        PASS,
        "KORG container magic present",
        "01 00 01 14 @+0",
    )
    if parsed.header is not None:
        report.add(
            "KORG_DECLARED_LENGTH",
            PASS if parsed.header.declared_ok else UNKNOWN,
            f"declared payload {parsed.header.payload_length}; delta {parsed.header.delta}",
            "u32 big-endian @+4 versus file size - 8",
        )
    report.add(
        "KORG_RESOURCE_CHAIN",
        PASS if parsed.chain.ok else UNKNOWN,
        f"KORF={len(parsed.chain.korf_offsets)}, KBEG={len(parsed.chain.kbeg_offsets)}, KEND={len(parsed.chain.kend_offsets)}",
        "literal resource-chain markers",
    )
    if parsed.compression.any:
        report.add(
            "KORG_COMPRESSION",
            PASS,
            f"OC20={parsed.compression.oc20}, OC31={parsed.compression.oc31}, OC32={parsed.compression.oc32}",
            "literal compression headers",
        )
    if deep and path.suffix.upper() in {".STY", ".PRF", ".PAD", ".PCG", ".GBL", ".TBL"}:
        try:
            from .native.format import Bank, FormatError

            bank = Bank(path.name, path.read_bytes(), {})
            report.add(
                "NATIVE_DEEP_PARSE",
                PASS,
                f"{len(bank.resources)} framed resource(s), envelope {bank.envelope}",
                "bounded OC20/root/type-05/FE/KBEG-KEND parser",
            )
        except (OSError, ValueError, FormatError) as exc:
            report.add(
                "NATIVE_DEEP_PARSE",
                UNKNOWN,
                f"strict PA800 parser did not accept this variant: {exc}",
                "variant may be valid for another model; no model is inferred",
            )


def _validate_pcg(path: Path, report: FileValidationReport, deep: bool) -> None:
    classification = pcg.classify_pcg(path)
    if classification.error:
        report.add("PCG_DIALECT", FAIL, classification.error)
        return
    report.add(
        "PCG_DIALECT",
        PASS if classification.status in {Status.VERIFIED, Status.HIGH} else UNKNOWN,
        f"dialect {classification.dialect}: {classification.label}",
        "; ".join(classification.evidence),
        classification.to_dict(),
    )
    if classification.dialect == "pa_korf":
        _validate_container(path, report, deep)


def _validate_one_file(
    path: Path,
    relative_path: str,
    target_model: Optional[str],
    required_ram_mb: Optional[int],
    deep: bool,
    midi_workflow: str,
) -> FileValidationReport:
    extension = path.suffix.upper()
    rule = FILE_RULES.get(extension)
    report = FileValidationReport(
        path=str(path),
        relative_path=relative_path,
        kind=rule.kind if rule else "unknown",
        extension=extension,
    )
    try:
        stat = path.stat()
    except OSError as exc:
        report.add("FILE_STAT", FAIL, f"cannot stat file: {exc}")
        return report
    report.size = stat.st_size
    if path.is_symlink():
        report.add("FILE_SYMLINK", FAIL, "symbolic links are not accepted as resources")
        return report
    if not path.is_file():
        report.add("FILE_TYPE", FAIL, "path is not a regular file")
        return report
    if stat.st_size > MAX_SINGLE_FILE:
        report.add(
            "FILE_SIZE_LIMIT",
            FAIL,
            f"file exceeds validator limit of {MAX_SINGLE_FILE} bytes",
        )
        return report
    if stat.st_size == 0:
        report.add("FILE_EMPTY", FAIL, "file is empty")
        return report
    try:
        report.source_sha256 = _sha256_file(path)
    except OSError as exc:
        report.add("FILE_HASH", FAIL, f"cannot hash file: {exc}")
        return report
    report.add(
        "FILE_HASH",
        PASS,
        f"SHA-256 {report.source_sha256}",
        "streamed over source without modification",
    )
    if path.name.lower() == "metadir.json":
        report.kind = "metadir"
        report.add(
            "FILE_RULE",
            PASS,
            "metadir.json uses the recovered Pa5X directory-index schema",
            "50 Pa5X metadir.json evidence files",
        )
        _validate_model(report, target_model, None, required_ram_mb)
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("top level is not an object")
            for key in ("subdirs", "files"):
                if key in payload and not isinstance(payload[key], list):
                    raise ValueError(f"{key} must be an array")
            report.add(
                "METADIR_JSON",
                PASS,
                "metadir.json is valid and uses the recovered object/array shape",
                "50 Pa5X metadir.json evidence files",
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            report.add("METADIR_JSON", FAIL, f"invalid metadir.json: {exc}")
        return report
    if rule is None:
        report.add(
            "FILE_EXTENSION",
            UNKNOWN,
            f"extension {extension or '(none)'} is not in the verified PA Manager inventory",
        )
        _validate_model(report, target_model, None, required_ram_mb)
        return report
    report.add(
        "FILE_RULE",
        PASS,
        f"{extension} -> {rule.kind}, structure policy {rule.structure}",
        rule.evidence,
        rule.to_dict(),
    )
    _validate_model(report, target_model, rule, required_ram_mb)

    if rule.structure == "MIDI":
        _validate_midi(path, report, midi_workflow)
    elif rule.structure == "WAVE":
        _validate_wave(path, report)
    elif rule.structure == "AIFF":
        _validate_aiff(path, report)
    elif rule.structure == "SOUNDFONT":
        _validate_soundfont(path, report)
    elif rule.structure == "REX":
        _validate_rex(path, report)
    elif rule.structure == "ZIP":
        _validate_zip(path, report)
    elif rule.structure == "PCG":
        _validate_pcg(path, report, deep)
    elif rule.structure == "KORG_RESOURCE":
        try:
            head = path.read_bytes()[:4]
        except OSError as exc:
            report.add("KORG_READ", FAIL, f"cannot read resource: {exc}")
        else:
            if head == models.container_magic():
                _validate_container(path, report, deep)
            elif head == b"OC20":
                if deep:
                    try:
                        from .native.format import Bank, FormatError

                        bank = Bank(path.name, path.read_bytes(), {})
                        report.add(
                            "PA800_OC20",
                            PASS,
                            f"OC20 bank with {len(bank.resources)} resource(s)",
                            "bounded native PA800 parser",
                        )
                    except (OSError, ValueError, FormatError) as exc:
                        report.add("PA800_OC20", FAIL, f"invalid OC20 resource: {exc}")
                else:
                    report.add(
                        "PA800_OC20",
                        PASS,
                        "OC20 envelope detected; use deep validation for resource framing",
                    )
            else:
                report.add(
                    "KORG_RESOURCE_HEADER",
                    FAIL,
                    f"expected KORG container or OC20 envelope, found {head.hex(' ')}",
                )
    elif rule.structure == "KORG_OR_CHUNKED":
        try:
            head = path.read_bytes()[:16]
        except OSError as exc:
            report.add("RESOURCE_READ", FAIL, f"cannot read resource: {exc}")
        else:
            if head[:4] == models.container_magic():
                _validate_container(path, report, deep)
            elif head[:4] in {b"OC20", b"OC31", b"OC32", b"KORG", b"RIFF"}:
                report.add(
                    "RESOURCE_SIGNATURE",
                    PASS,
                    f"recognized chunk/envelope signature {head[:4]!r}",
                    "format-specific full semantics may remain unproven",
                )
            else:
                report.add(
                    "RESOURCE_SIGNATURE",
                    UNKNOWN,
                    "extension is supported, but no proven signature/parser matched",
                )
    elif rule.structure == "PACKAGE":
        try:
            head = path.read_bytes()[:4]
        except OSError as exc:
            report.add("PACKAGE_READ", FAIL, f"cannot read package: {exc}")
        else:
            if head == b"PK\x03\x04":
                _validate_zip(path, report)
            else:
                report.add(
                    "PACKAGE_STRUCTURE",
                    UNKNOWN,
                    "PKG extension is known, but the package payload contract is not proven",
                )
    else:
        report.add(
            "OPAQUE_PAYLOAD",
            UNKNOWN,
            f"{extension} is inventoried and preserved, but no complete safe parser is proven",
            rule.evidence,
        )
    return report


def _validate_folder_identity(
    path: Path,
    report: FileValidationReport,
    target_model: Optional[str],
) -> None:
    rule = FILE_RULES.get(path.suffix.upper())
    if rule:
        report.kind = rule.kind
        report.extension = rule.extension
        report.add(
            "FOLDER_RULE",
            PASS,
            f"{rule.extension} is a folder format",
            rule.evidence,
        )
        profile = detect.detect(path)
        report.add(
            "FOLDER_IDENTITY",
            PASS if profile.status in {Status.VERIFIED, Status.HIGH} else UNKNOWN,
            f"detected {profile.kind}",
            "; ".join(profile.evidence),
            profile.to_dict(),
        )
        _validate_model(report, target_model, rule, None)
    else:
        report.kind = "directory"
        report.add(
            "FOLDER_RULE",
            UNKNOWN,
            "directory has no .SET/.KST suffix; children are still validated",
        )
        _validate_model(report, target_model, None, None)


def validate_path(
    path: str | os.PathLike[str],
    *,
    target_model: Optional[str] = None,
    required_ram_mb: Optional[int] = None,
    deep: bool = True,
    midi_workflow: str = "generic",
) -> FileValidationReport:
    """Validate one file or every file below a folder.

    ``midi_workflow`` can be ``generic``, ``manager_import``, or
    ``pa_style_import``.  The distinction is necessary because recovered PA
    Manager messages and the official Pa style-import contract target different
    SMF layouts.
    """

    if midi_workflow not in {"generic", "manager_import", "pa_style_import"}:
        raise ValueError("midi_workflow must be generic, manager_import, or pa_style_import")
    source = Path(path).expanduser()
    if source.is_file():
        return _validate_one_file(
            source,
            source.name,
            target_model,
            required_ram_mb,
            deep,
            midi_workflow,
        )
    root_report = FileValidationReport(
        path=str(source),
        relative_path=".",
        extension=source.suffix.upper(),
    )
    if not source.exists():
        root_report.add("PATH_MISSING", FAIL, "path does not exist")
        return root_report
    if source.is_symlink():
        root_report.add("FOLDER_SYMLINK", FAIL, "symbolic-link roots are not accepted")
        return root_report
    if not source.is_dir():
        root_report.add("PATH_TYPE", FAIL, "path is neither a regular file nor a directory")
        return root_report
    _validate_folder_identity(source, root_report, target_model)

    files: list[Path] = []
    total_bytes = 0
    folded_paths: set[str] = set()
    try:
        for candidate in sorted(source.rglob("*")):
            if candidate.is_symlink():
                child = FileValidationReport(
                    path=str(candidate),
                    relative_path=candidate.relative_to(source).as_posix(),
                )
                child.add("TREE_SYMLINK", FAIL, "symbolic links are not accepted")
                root_report.children.append(child)
                continue
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(source).as_posix()
            folded = relative.casefold()
            if folded in folded_paths:
                child = FileValidationReport(
                    path=str(candidate), relative_path=relative
                )
                child.add(
                    "TREE_DUPLICATE",
                    FAIL,
                    "case-insensitive duplicate relative path",
                )
                root_report.children.append(child)
                continue
            folded_paths.add(folded)
            files.append(candidate)
            total_bytes += candidate.stat().st_size
            if len(files) > MAX_FILES:
                root_report.add(
                    "TREE_FILE_LIMIT",
                    FAIL,
                    f"tree exceeds {MAX_FILES} files",
                )
                return root_report
            if total_bytes > MAX_TOTAL_BYTES:
                root_report.add(
                    "TREE_SIZE_LIMIT",
                    FAIL,
                    f"tree exceeds {MAX_TOTAL_BYTES} total bytes",
                )
                return root_report
    except OSError as exc:
        root_report.add("TREE_SCAN", FAIL, f"cannot inventory directory: {exc}")
        return root_report

    root_report.add(
        "TREE_INVENTORY",
        PASS,
        f"{len(files)} regular file(s), {total_bytes} bytes",
        "bounded recursive inventory",
    )
    for candidate in files:
        root_report.children.append(
            _validate_one_file(
                candidate,
                candidate.relative_to(source).as_posix(),
                target_model,
                required_ram_mb,
                deep,
                midi_workflow,
            )
        )
    return root_report


def _resource_extension(extension: str) -> bool:
    return extension in {
        ".SET",
        ".KST",
        ".STY",
        ".STG",
        ".PCG",
        ".PAD",
        ".PDC",
        ".PRF",
        ".KSC",
        ".KMP",
        ".KSF",
        ".PCM",
        ".GBL",
        ".BKP",
        ".PKG",
    }


def compatibility(
    source_model: str,
    target_model: str,
    extension: str,
) -> CompatibilityDecision:
    """Return one evidence-backed cell of the complete conversion matrix."""

    source = resolve_model(source_model)
    target = resolve_model(target_model)
    extension = extension.upper()
    if extension and not extension.startswith("."):
        extension = "." + extension
    rule = FILE_RULES.get(extension)
    if source is None or target is None:
        return CompatibilityDecision(
            source_model,
            target_model,
            extension,
            COMPAT_REFUSED,
            False,
            "source or target model is not in the validator registry",
            "data/models.json",
        )
    if rule is None:
        return CompatibilityDecision(
            source.name,
            target.name,
            extension,
            COMPAT_UNKNOWN,
            False,
            "file extension is outside the verified inventory",
            "detect.CONTENT_EXTS",
        )
    if source.key == target.key:
        return CompatibilityDecision(
            source.name,
            target.name,
            extension,
            COMPAT_EXACT,
            True,
            "same model profile; validated copy is executable",
            "; ".join(source.evidence),
        )
    if extension in {".MID", ".MIDI", ".WAV", ".AIF", ".AIFF", ".RX2", ".REX", ".SF2"}:
        return CompatibilityDecision(
            source.name,
            target.name,
            extension,
            COMPAT_PRESERVE,
            True,
            "generic media can be preserved, but model-specific sound assignments are not automatically mapped",
            rule.evidence,
            ("factory/user sound references may require manual remapping",),
        )
    if target.key == "PA5X" and extension in {".PCG", ".KMP", ".KSF", ".PCM"}:
        return CompatibilityDecision(
            source.name,
            target.name,
            extension,
            COMPAT_REFUSED,
            False,
            "PA5X SOUND import is explicitly refused by the recovered Manager path",
            "0x002DD968",
        )
    if extension == ".SET" and target.key == "PA5X":
        return CompatibilityDecision(
            source.name,
            target.name,
            extension,
            COMPAT_UNKNOWN,
            False,
            ".SET -> .KST entry point exists, but the complete transformation is not proven",
            "0x003EC1B6",
        )
    if extension == ".KST" and target.key != "PA5X":
        return CompatibilityDecision(
            source.name,
            target.name,
            extension,
            COMPAT_REFUSED,
            False,
            ".KST -> older .SET path is absent in the recovered Manager",
            "conversions.json kst_to_set exists=false",
        )
    if (
        source.format_group == target.format_group
        and source.format_group.startswith("opensound:")
    ):
        return CompatibilityDecision(
            source.name,
            target.name,
            extension,
            COMPAT_PARTIAL,
            False,
            "models share an OpenSound handler, but byte identity and complete compatibility are not proven",
            f"shared {source.format_group}",
            ("requires explicit resource mapping and instrument acceptance test",),
        )
    if source.key == "PA4X" and target.key == "PA3X" and extension in {".PCG", ".KMP", ".PCM"}:
        return CompatibilityDecision(
            source.name,
            target.name,
            extension,
            COMPAT_PARTIAL,
            False,
            "a named Pa4X -> Pa3X conversion path exists, but its byte algorithm is not integrated",
            "0x002CF730",
            ("STS/FX/EQ/settings may be lost",),
        )
    if _resource_extension(extension):
        return CompatibilityDecision(
            source.name,
            target.name,
            extension,
            COMPAT_UNKNOWN,
            False,
            "no complete instrument-safe cross-model writer/remap is proven for this pair and resource class",
            "STATUS.md native_write/remap open items",
            (
                "factory sound positions can differ",
                "STS/FX/EQ/settings may differ",
                "PCM/KMP dependencies are not closed",
            ),
        )
    return CompatibilityDecision(
        source.name,
        target.name,
        extension,
        COMPAT_PRESERVE,
        True,
        "opaque non-resource file can be preserved byte-identically only",
        rule.evidence,
    )


def compatibility_matrix(extension: str) -> list[CompatibilityDecision]:
    """Return every source/target cell for one file extension."""

    profiles = list(MODEL_PROFILES.values())
    return [
        compatibility(source.name, target.name, extension)
        for source in profiles
        for target in profiles
    ]


def save_report(
    report: FileValidationReport,
    path: str | os.PathLike[str],
) -> Path:
    """Atomically write a JSON validation report without overwriting its input."""

    destination = Path(path).expanduser()
    source = Path(report.path)
    if destination.resolve() == source.resolve():
        raise ValueError("refusing to overwrite the validated source with its report")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report.to_dict(), handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination
