"""Safe model-conversion executor with explicit compatibility boundaries.

The module performs real writes only where the write contract can be proven:

* same-model and generic-media files can be copied byte-identically;
* PCM WAVE can be converted to the documented 44.1 kHz / 16-bit / mono target;
* selected outer KORF catalog fields can be edited at fixed-width, explicitly
  mapped offsets with a byte-difference guard;
* unsupported resource payloads can be preserved byte-identically in a target
  package, but are labelled PRESERVE_ONLY, not "converted".

No function claims a universal Pa4X-to-PA800 Sound/PCM conversion.  Compressed
musical payloads, STS/FX/EQ/settings, oscillator/KMP/PCM dependency closure and
native hardware acceptance remain outside the proven writer contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import tempfile
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import models
from .validator import (
    COMPAT_EXACT,
    COMPAT_PARTIAL,
    COMPAT_PRESERVE,
    COMPAT_REFUSED,
    COMPAT_UNKNOWN,
    CompatibilityDecision,
    compatibility,
    resolve_model,
    validate_path,
)


MAPPING_SCHEMA = "pa_tool.explicit-model-map.v1"
CATALOG_EXTENSIONS = {".STY", ".PRF", ".PAD", ".PCG", ".PCM"}
KORG_MAGIC = b"\x01\x00\x01\x14"
KORF_OFFSET = 0x17
CATALOG_OFFSET = 0x24
KNOWN_CLASSES = {1, 2, 4, 7, 9}
CATALOG_LAYOUTS = {
    ".STY": (32, 48, True),
    ".PRF": (32, 24, False),
    ".PAD": (32, 24, False),
    ".PCG": (128, 24, False),
}
MAX_AUDIO_BYTES = 128 * 1024 * 1024


class ConversionRefused(RuntimeError):
    """Raised before writing when a conversion has no safe executable path."""


@dataclass(frozen=True)
class CatalogEntry:
    slot: int
    name: str
    offset: int
    class_id: int
    bank_id: int
    object_id: int
    duplicate_offset: Optional[int] = None

    @property
    def key(self) -> str:
        return f"{self.class_id}:{self.bank_id}:{self.object_id}"


@dataclass
class CatalogOptions:
    """Explicit, fixed-width catalog edits.

    ``object_key_map`` keys and values use ``class:bank:object`` decimal form.
    Only outer catalog records are changed; compressed musical references are
    deliberately not scanned or patched.
    """

    rename_map: dict[str, str] = field(default_factory=dict)
    prefix: str = ""
    suffix: str = ""
    style_bank: Optional[int] = None
    object_key_map: dict[str, str] = field(default_factory=dict)

    @property
    def requested(self) -> bool:
        return bool(
            self.rename_map
            or self.prefix
            or self.suffix
            or self.style_bank is not None
            or self.object_key_map
        )


@dataclass
class ConversionOptions:
    source_model: str
    target_model: str
    execute: bool = False
    preserve_unsupported: bool = True
    canonicalize_wave: bool = False
    catalog: CatalogOptions = field(default_factory=CatalogOptions)


@dataclass
class ConvertedFile:
    relative_path: str
    source_sha256: str
    output_sha256: Optional[str]
    compatibility: dict[str, Any]
    operation: str
    status: str
    changes: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModelConversionReport:
    source: str
    target: str
    source_model: str
    target_model: str
    executed: bool
    decision: str = "UNKNOWN"
    files: list[ConvertedFile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_validation: Optional[dict[str, Any]] = None
    output_validation: Optional[dict[str, Any]] = None
    source_tree_sha256_before: Optional[str] = None
    source_tree_sha256_after: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "pa_tool.model-conversion-report.v1",
            **asdict(self),
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.name.encode("utf-8", "surrogateescape"))
        digest.update(bytes.fromhex(_sha256(path)))
        return digest.hexdigest()
    for item in sorted(path.rglob("*"), key=lambda value: value.as_posix().casefold()):
        if item.is_symlink():
            raise ConversionRefused(f"symbolic link is not accepted: {item}")
        if not item.is_file():
            continue
        relative = item.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8", "surrogateescape"))
        digest.update(bytes.fromhex(_sha256(item)))
    return digest.hexdigest()


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        if _sha256(source) != _sha256(temporary):
            raise ConversionRefused("copy verification hash mismatch")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _parse_key(value: str) -> tuple[int, int, int]:
    try:
        parts = tuple(int(part.strip(), 0) for part in value.split(":"))
    except ValueError as exc:
        raise ConversionRefused(f"invalid catalog key {value!r}") from exc
    if len(parts) != 3 or any(not 0 <= part <= 255 for part in parts[:2]) or not 0 <= parts[2] <= 65535:
        raise ConversionRefused(
            f"catalog key must be class:bank:object with byte/byte/u16 values: {value!r}"
        )
    return parts


def _parse_catalog(path: Path, data: bytes) -> list[CatalogEntry]:
    extension = path.suffix.upper()
    if extension not in CATALOG_EXTENSIONS:
        raise ConversionRefused(f"catalog editor does not support {extension}")
    if len(data) < 0x34 or data[:4] != KORG_MAGIC or data[KORF_OFFSET : KORF_OFFSET + 4] != b"KORF":
        raise ConversionRefused(
            "fixed-width catalog editor requires outer 01 00 01 14 container with KORF at 0x17"
        )
    entries: list[CatalogEntry] = []
    if extension == ".PCM":
        if len(data) < 0x24:
            raise ConversionRefused("PCM is too small for its catalog directory")
        directory_size = int.from_bytes(data[0x20:0x24], "big")
        if directory_size <= 0 or directory_size % 24 or CATALOG_OFFSET + directory_size > len(data):
            raise ConversionRefused("PCM directory size is invalid")
        slots, stride, duplicate = directory_size // 24, 24, False
    else:
        slots, stride, duplicate = CATALOG_LAYOUTS[extension]
    for slot in range(slots):
        offset = CATALOG_OFFSET + slot * stride
        if offset + 24 > len(data):
            raise ConversionRefused(
                f"catalog is truncated at slot {slot}; expected {slots} fixed slots"
            )
        raw_name = data[offset : offset + 16]
        name = raw_name.split(b"\x00", 1)[0].decode("latin-1").rstrip(" \x00")
        if not name:
            continue
        if extension == ".PCM":
            class_id, bank_id, object_id = 0, 0, slot
        else:
            class_id = data[offset + 18]
            bank_id = data[offset + 19]
            object_id = int.from_bytes(data[offset + 20 : offset + 22], "little")
            if class_id not in KNOWN_CLASSES:
                # Non-empty unknown slots are not safe to edit by this contract.
                continue
        duplicate_offset = offset + 24 if duplicate else None
        if duplicate_offset is not None:
            if duplicate_offset + 24 > len(data):
                raise ConversionRefused(f"duplicate catalog record is truncated at slot {slot}")
            duplicate_name = (
                data[duplicate_offset : duplicate_offset + 16]
                .split(b"\x00", 1)[0]
                .decode("latin-1")
                .rstrip(" \x00")
            )
            if duplicate_name and duplicate_name != name:
                raise ConversionRefused(
                    f"STY slot {slot} primary/duplicate names differ; automatic edit refused"
                )
        entries.append(
            CatalogEntry(
                slot=slot,
                name=name,
                offset=offset,
                class_id=class_id,
                bank_id=bank_id,
                object_id=object_id,
                duplicate_offset=duplicate_offset,
            )
        )
    if not entries:
        raise ConversionRefused("no editable fixed-width catalog entries were proven")
    return entries


def _name_bytes(value: str) -> bytes:
    encoded = value.encode("latin-1", "strict")
    if len(encoded) > 16:
        raise ConversionRefused(f"catalog name exceeds 16 Latin-1 bytes: {value!r}")
    return encoded.ljust(16, b"\x00")


def _catalog_edit_bytes(
    path: Path,
    data: bytes,
    options: CatalogOptions,
) -> tuple[bytes, list[dict[str, Any]]]:
    entries = _parse_catalog(path, data)
    after = bytearray(data)
    allowed: set[int] = set()
    changes: list[dict[str, Any]] = []
    key_map = {_parse_key(old): _parse_key(new) for old, new in options.object_key_map.items()}

    def patch(offset: int, replacement: bytes) -> None:
        if offset < 0 or offset + len(replacement) > len(after):
            raise ConversionRefused(f"catalog edit outside file at 0x{offset:X}")
        after[offset : offset + len(replacement)] = replacement
        allowed.update(range(offset, offset + len(replacement)))

    for entry in entries:
        new_name = options.rename_map.get(entry.name, entry.name)
        if options.prefix:
            new_name = options.prefix + new_name
        if options.suffix:
            new_name = new_name + options.suffix
        if new_name != entry.name:
            replacement = _name_bytes(new_name)
            patch(entry.offset, replacement)
            if entry.duplicate_offset is not None:
                patch(entry.duplicate_offset, replacement)
            changes.append(
                {
                    "operation": "catalog_name",
                    "slot": entry.slot,
                    "old": entry.name,
                    "new": new_name,
                    "offset": entry.offset,
                }
            )
        if options.style_bank is not None and entry.class_id in {2, 7}:
            if not 0 <= options.style_bank <= 255:
                raise ConversionRefused("style bank must be in the range 0..255")
            if entry.bank_id != options.style_bank:
                patch(entry.offset + 19, bytes([options.style_bank]))
                if entry.duplicate_offset is not None:
                    patch(entry.duplicate_offset + 19, bytes([options.style_bank]))
                changes.append(
                    {
                        "operation": "style_bank",
                        "slot": entry.slot,
                        "old": entry.bank_id,
                        "new": options.style_bank,
                        "offset": entry.offset + 19,
                    }
                )
        mapped = key_map.get((entry.class_id, entry.bank_id, entry.object_id))
        if mapped is not None:
            new_class, new_bank, new_object = mapped
            replacement = bytes([new_class, new_bank]) + new_object.to_bytes(2, "little")
            patch(entry.offset + 18, replacement)
            if entry.duplicate_offset is not None:
                patch(entry.duplicate_offset + 18, replacement)
            changes.append(
                {
                    "operation": "catalog_object_key",
                    "slot": entry.slot,
                    "old": entry.key,
                    "new": f"{new_class}:{new_bank}:{new_object}",
                    "offset": entry.offset + 18,
                }
            )
    changed_positions = {
        index for index, (old, new) in enumerate(zip(data, after)) if old != new
    }
    if not changed_positions.issubset(allowed):
        unexpected = sorted(changed_positions - allowed)[:16]
        raise ConversionRefused(
            f"byte-difference guard detected unexpected changes at {unexpected}"
        )
    output = bytes(after)
    if len(output) != len(data):
        raise ConversionRefused("fixed-width catalog edit changed file length")
    if changes:
        # Reparse and prove the output still satisfies the same catalog contract.
        output_entries = _parse_catalog(path, output)
        if len(output_entries) != len(entries):
            raise ConversionRefused("catalog entry count changed after edit")
    return output, changes


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary.read_bytes() != data:
            raise ConversionRefused("write read-back verification failed")
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _decode_pcm_sample(frame: bytes, width: int) -> int:
    if width == 1:
        return (frame[0] - 128) << 8
    if width == 2:
        return int.from_bytes(frame, "little", signed=True)
    if width == 3:
        raw = int.from_bytes(frame, "little", signed=False)
        if raw & 0x800000:
            raw -= 1 << 24
        return raw >> 8
    if width == 4:
        return int.from_bytes(frame, "little", signed=True) >> 16
    raise ConversionRefused(f"unsupported PCM sample width: {width} bytes")


def _canonical_wave_bytes(source: Path) -> tuple[bytes, list[dict[str, Any]]]:
    if source.stat().st_size > MAX_AUDIO_BYTES:
        raise ConversionRefused(
            f"WAVE exceeds conversion limit of {MAX_AUDIO_BYTES} bytes"
        )
    try:
        with wave.open(str(source), "rb") as wave_in:
            channels = wave_in.getnchannels()
            width = wave_in.getsampwidth()
            rate = wave_in.getframerate()
            frames = wave_in.getnframes()
            compression = wave_in.getcomptype()
            raw = wave_in.readframes(frames)
    except (OSError, EOFError, wave.Error) as exc:
        raise ConversionRefused(f"cannot decode PCM WAVE: {exc}") from exc
    if compression != "NONE":
        raise ConversionRefused(f"compressed WAVE {compression!r} is not supported")
    if channels < 1 or channels > 32 or width not in {1, 2, 3, 4} or rate <= 0:
        raise ConversionRefused("WAVE channel/width/rate values are outside safe limits")
    frame_width = channels * width
    if len(raw) != frames * frame_width:
        raise ConversionRefused("WAVE data length does not match frame metadata")
    mono: list[int] = []
    for frame_index in range(frames):
        frame_start = frame_index * frame_width
        values = [
            _decode_pcm_sample(
                raw[
                    frame_start + channel * width : frame_start + (channel + 1) * width
                ],
                width,
            )
            for channel in range(channels)
        ]
        mono.append(round(sum(values) / channels))
    target_rate = 44_100
    if rate == target_rate:
        converted = mono
    elif not mono:
        converted = []
    else:
        target_frames = max(1, round(len(mono) * target_rate / rate))
        converted = []
        for target_index in range(target_frames):
            source_position = target_index * rate / target_rate
            left = min(len(mono) - 1, int(source_position))
            right = min(len(mono) - 1, left + 1)
            fraction = source_position - left
            value = round(mono[left] * (1.0 - fraction) + mono[right] * fraction)
            converted.append(value)
    pcm = b"".join(
        max(-32768, min(32767, value)).to_bytes(2, "little", signed=True)
        for value in converted
    )
    import io

    output = io.BytesIO()
    with wave.open(output, "wb") as wave_out:
        wave_out.setnchannels(1)
        wave_out.setsampwidth(2)
        wave_out.setframerate(target_rate)
        wave_out.writeframes(pcm)
    changes = [
        {
            "operation": "canonical_wave",
            "source": {
                "sample_rate_hz": rate,
                "bits": width * 8,
                "channels": channels,
                "frames": frames,
            },
            "target": {
                "sample_rate_hz": target_rate,
                "bits": 16,
                "channels": 1,
                "frames": len(converted),
            },
            "evidence": "0x00250114: 44100 kHz / 16 bits / mono",
        }
    ]
    return output.getvalue(), changes


def load_mapping(path: str | os.PathLike[str]) -> CatalogOptions:
    """Load an explicit map; model pair and schema are checked by the caller."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != MAPPING_SCHEMA:
        raise ConversionRefused(
            f"mapping schema must be {MAPPING_SCHEMA!r}, got {payload.get('schema')!r}"
        )
    catalog = payload.get("catalog", {})
    if not isinstance(catalog, dict):
        raise ConversionRefused("mapping catalog field must be an object")
    rename_map = catalog.get("rename_map", {})
    object_key_map = catalog.get("object_key_map", {})
    if not isinstance(rename_map, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in rename_map.items()
    ):
        raise ConversionRefused("rename_map must map strings to strings")
    if not isinstance(object_key_map, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in object_key_map.items()
    ):
        raise ConversionRefused("object_key_map must map strings to strings")
    style_bank = catalog.get("style_bank")
    if style_bank is not None:
        style_bank = int(style_bank)
    return CatalogOptions(
        rename_map=dict(rename_map),
        prefix=str(catalog.get("prefix", "")),
        suffix=str(catalog.get("suffix", "")),
        style_bank=style_bank,
        object_key_map=dict(object_key_map),
    )


def mapping_pair(path: str | os.PathLike[str]) -> tuple[str, str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return str(payload.get("source_model", "")), str(payload.get("target_model", ""))


def _process_file(
    source: Path,
    target: Path,
    relative: str,
    options: ConversionOptions,
) -> ConvertedFile:
    extension = source.suffix.upper()
    cell = compatibility(options.source_model, options.target_model, extension)
    source_hash = _sha256(source)
    notes: list[str] = []
    changes: list[dict[str, Any]] = []
    operation = "plan"
    status = cell.level

    if cell.level == COMPAT_REFUSED:
        raise ConversionRefused(f"{relative}: {cell.reason} ({cell.evidence})")
    if cell.level in {COMPAT_UNKNOWN, COMPAT_PARTIAL} and not options.preserve_unsupported:
        raise ConversionRefused(
            f"{relative}: conversion is {cell.level}; use preserve_unsupported only for a byte-identical carry-over"
        )
    if not options.execute:
        return ConvertedFile(
            relative,
            source_hash,
            None,
            cell.to_dict(),
            "plan_only",
            status,
            notes=[cell.reason],
        )

    if extension == ".WAV" and options.canonicalize_wave:
        output, changes = _canonical_wave_bytes(source)
        _atomic_write_bytes(target, output)
        operation = "canonical_wave"
        status = COMPAT_PARTIAL if options.source_model != options.target_model else COMPAT_EXACT
        notes.append(
            "audio format converted; this does not map model-specific Sound/OSC parameters"
        )
    elif options.catalog.requested and extension in CATALOG_EXTENSIONS:
        output, changes = _catalog_edit_bytes(source, source.read_bytes(), options.catalog)
        if changes:
            _atomic_write_bytes(target, output)
            operation = "fixed_width_catalog_edit"
            status = COMPAT_PARTIAL
            notes.append(
                "only outer catalog fields changed; compressed musical payload and dependencies were not rewritten"
            )
        else:
            _atomic_copy(source, target)
            operation = "byte_identical_copy"
            status = COMPAT_PRESERVE if options.source_model != options.target_model else COMPAT_EXACT
            notes.append("requested catalog rules produced no applicable changes")
    else:
        _atomic_copy(source, target)
        operation = "byte_identical_copy"
        if options.source_model != options.target_model:
            status = COMPAT_PRESERVE
            notes.append(
                "preserved byte-identically; this file is not certified converted for the target model"
            )
    output_hash = _sha256(target)
    if operation == "byte_identical_copy" and output_hash != source_hash:
        raise ConversionRefused(f"{relative}: byte-identical copy hash mismatch")
    return ConvertedFile(
        relative,
        source_hash,
        output_hash,
        cell.to_dict(),
        operation,
        status,
        changes,
        notes,
    )


def _report_decision(files: list[ConvertedFile], errors: list[str]) -> str:
    if errors:
        return "FAIL"
    levels = {item.status for item in files}
    if not files:
        return "UNKNOWN"
    if levels <= {COMPAT_EXACT}:
        return "PASS_EXACT"
    if COMPAT_UNKNOWN in levels:
        return "PARTIAL_WITH_UNKNOWN"
    if COMPAT_PARTIAL in levels:
        return "PARTIAL"
    if COMPAT_PRESERVE in levels:
        return "PRESERVE_ONLY"
    return "PARTIAL"


def convert_path(
    source_path: str | os.PathLike[str],
    target_path: str | os.PathLike[str],
    options: ConversionOptions,
) -> ModelConversionReport:
    """Plan or execute a protected model-conversion package.

    Existing targets are refused.  Folder output is assembled in a sibling
    staging directory and installed only after validation and source-hash checks.
    """

    source = Path(source_path).expanduser()
    target = Path(target_path).expanduser()
    source_profile = resolve_model(options.source_model)
    target_profile = resolve_model(options.target_model)
    if source_profile is None:
        raise ConversionRefused(f"unknown source model: {options.source_model}")
    if target_profile is None:
        raise ConversionRefused(f"unknown target model: {options.target_model}")
    options.source_model = source_profile.name
    options.target_model = target_profile.name
    if not source.exists():
        raise ConversionRefused(f"source does not exist: {source}")
    if source.resolve() == target.resolve():
        raise ConversionRefused("target equals source; in-place conversion is forbidden")
    if target.exists() and options.execute:
        raise ConversionRefused("target already exists; destructive replacement is forbidden")
    automatic_report = target.with_name(target.name + ".conversion.json")
    if source.is_file() and options.execute and automatic_report.exists():
        raise ConversionRefused(
            f"automatic report path already exists: {automatic_report}"
        )

    report = ModelConversionReport(
        source=str(source),
        target=str(target),
        source_model=source_profile.name,
        target_model=target_profile.name,
        executed=options.execute,
    )
    report.source_tree_sha256_before = _tree_hash(source)
    source_validation = validate_path(source, target_model=source_profile.name, deep=True)
    report.source_validation = source_validation.to_dict()
    if source_validation.decision == "FAIL":
        raise ConversionRefused("source validation failed; conversion was not started")

    if not options.execute:
        if source.is_file():
            report.files.append(
                _process_file(source, target, source.name, options)
            )
        else:
            for item in sorted(source.rglob("*")):
                if item.is_symlink():
                    raise ConversionRefused(f"symbolic link is not accepted: {item}")
                if item.is_file():
                    relative = item.relative_to(source).as_posix()
                    report.files.append(
                        _process_file(item, target / relative, relative, options)
                    )
        report.source_tree_sha256_after = _tree_hash(source)
        if report.source_tree_sha256_after != report.source_tree_sha256_before:
            raise ConversionRefused("source changed during dry-run planning")
        report.decision = _report_decision(report.files, report.errors)
        return report

    if source.is_file():
        try:
            report.files.append(_process_file(source, target, source.name, options))
            output_validation = validate_path(
                target, target_model=target_profile.name, deep=True
            )
            report.output_validation = output_validation.to_dict()
            if output_validation.decision == "FAIL":
                raise ConversionRefused("output validation failed")
        except Exception:
            target.unlink(missing_ok=True)
            raise
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{target.stem}.staging-",
                suffix=target.suffix or ".tmp",
                dir=target.parent,
            )
        )
        try:
            for directory in sorted(item for item in source.rglob("*") if item.is_dir()):
                if directory.is_symlink():
                    raise ConversionRefused(
                        f"symbolic link is not accepted: {directory}"
                    )
                (staging / directory.relative_to(source)).mkdir(
                    parents=True, exist_ok=True
                )
            for item in sorted(source.rglob("*")):
                if item.is_symlink():
                    raise ConversionRefused(f"symbolic link is not accepted: {item}")
                if not item.is_file():
                    continue
                relative = item.relative_to(source).as_posix()
                report.files.append(
                    _process_file(item, staging / relative, relative, options)
                )
            report.decision = _report_decision(report.files, report.errors)
            (staging / "conversion_report.json").write_text(
                json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            output_validation = validate_path(
                staging, target_model=target_profile.name, deep=True
            )
            report.output_validation = output_validation.to_dict()
            if output_validation.decision == "FAIL":
                raise ConversionRefused("staged output validation failed")
            os.replace(staging, target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    report.source_tree_sha256_after = _tree_hash(source)
    if report.source_tree_sha256_after != report.source_tree_sha256_before:
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)
        raise ConversionRefused("source hash changed during conversion; output removed")
    report.decision = _report_decision(report.files, report.errors)
    if source.is_file():
        report_path = target.with_name(target.name + ".conversion.json")
        save_conversion_report(report, report_path)
    else:
        # Refresh the report now that source-after and output validation are known.
        save_conversion_report(report, target / "conversion_report.json")
    return report


def save_conversion_report(
    report: ModelConversionReport,
    path: str | os.PathLike[str],
) -> Path:
    """Atomically save a report without overwriting source or output resources."""

    destination = Path(path).expanduser()
    source = Path(report.source)
    target = Path(report.target)
    resolved = destination.resolve()
    if resolved == source.resolve():
        raise ConversionRefused("report path equals the conversion source")
    if target.is_file() and resolved == target.resolve():
        raise ConversionRefused("report path equals the converted output file")
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


def save_mapping_template(
    source_model: str,
    target_model: str,
    path: str | os.PathLike[str],
) -> Path:
    """Create, but never overwrite, an explicit mapping template."""

    destination = Path(path).expanduser()
    if destination.exists():
        raise ConversionRefused(f"mapping template already exists: {destination}")
    payload = mapping_template(source_model, target_model)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def mapping_template(source_model: str, target_model: str) -> dict[str, Any]:
    source = resolve_model(source_model)
    target = resolve_model(target_model)
    if source is None or target is None:
        raise ConversionRefused("source and target models must exist in the registry")
    return {
        "schema": MAPPING_SCHEMA,
        "source_model": source.name,
        "target_model": target.name,
        "catalog": {
            "rename_map": {},
            "prefix": "",
            "suffix": "",
            "style_bank": None,
            "object_key_map": {
                "4:1:0": "4:2:0"
            },
        },
        "warning": (
            "Example ObjectKey entry must be replaced or removed. This map edits only "
            "proven outer catalog fields; it does not rewrite compressed musical payloads."
        ),
    }
