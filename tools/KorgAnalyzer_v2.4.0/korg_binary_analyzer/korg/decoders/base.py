from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from ...decoders.base import Decoder
from ..codec import NATIVE_ROOT, decode_oc20_pages, is_oc20_pages
from ..containers import container_kind, style_summary
from ..resources import scan_buffer


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _new_output_dir(root: Path, name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    safe = "".join(character if character.isalnum() or character in "-_. " else "_" for character in name).strip() or "resource"
    base = root / safe
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = root / f"{safe}-{suffix}"
        suffix += 1
    candidate.mkdir()
    return candidate


class KorgContainerDecoder(Decoder):
    """Shared machinery for decoders that work on native Korg containers.

    Subclasses implement :meth:`unwrap` (bytes -> native container bytes) and
    may extend :meth:`summarize`.
    """

    executable = True
    container_label = "Korg resource container"
    output_suffix = ".bin"

    def unwrap(self, data: bytes) -> tuple[bytes, str, list[dict[str, Any]]]:
        """Return ``(native, container_kind, notes)``; raise to refuse the input."""
        raise NotImplementedError

    def detect(self, data: Any) -> dict[str, Any]:
        try:
            native, kind, notes = self.unwrap(bytes(data))
        except Exception as error:
            return {
                "matched": False,
                "confidence": 0.0,
                "evidence": [f"container unwrap refused: {type(error).__name__}: {error}"],
            }
        if native[:4] != NATIVE_ROOT:
            return {
                "matched": False,
                "confidence": 0.0,
                "evidence": ["unwrapped buffer is not a native Korg container"],
            }
        return {
            "matched": True,
            "confidence": 0.94 if kind == "native-container" else 0.9,
            "evidence": [
                f"container kind: {kind}",
                *[str(note) for note in notes],
                "root record header 01 00 01 14 present after unwrapping",
            ],
        }

    def decode(self, source: Path, output_root: Path, max_output_bytes: int) -> dict[str, Any]:
        try:
            raw = source.read_bytes()
        except OSError as error:
            return {"decoder": self.name, "status": "ATTEMPTED_FAILED", "error": str(error)}
        output_dir = _new_output_dir(output_root, source.stem or self.name)
        try:
            native, kind, notes = self.unwrap(raw)
            if native[:4] != NATIVE_ROOT:
                raise ValueError("unwrapped buffer is not a native Korg container")
            if len(native) > max_output_bytes:
                raise ValueError(
                    f"unwrapped container {len(native)} bytes exceeds limit {max_output_bytes}"
                )

            inventory = scan_buffer(native)
            resources = inventory["structured"].get("resources", [])
            raw_resources = inventory["structured"].get("raw_resources", [])
            errors = inventory["structured"].get("errors", [])
            name_directory = inventory["structured"].get("name_directory")

            native_path = output_dir / f"native{self.output_suffix}"
            native_path.write_bytes(native)

            decoded_dir = output_dir / "resources"
            written = 0
            outputs: list[dict[str, Any]] = []
            for item in resources:
                blob = native[item["payload_offset"]:item["payload_offset"] + item["payload_size"]]
                try:
                    from ..codec import decode_oc3x_generic

                    decoded, _ = decode_oc3x_generic(blob)
                except Exception:
                    continue
                if written + len(decoded) > max_output_bytes:
                    break
                name = f"{item['resource_position']:04d}_id{item['id'][2:]}_v{item['version']}_{item['sha256'][:12]}.bin"
                target = decoded_dir / name
                decoded_dir.mkdir(parents=True, exist_ok=True)
                target.write_bytes(decoded)
                written += len(decoded)
                entry = {
                    "position": item["resource_position"],
                    "id": item["id"],
                    "name": name,
                    "size": len(decoded),
                    "sha256": item["sha256"],
                }
                if item["id"] == "0x06":
                    summary = style_summary(decoded)
                    entry["style"] = {
                        "name": summary.get("name"),
                        "inner_version": summary.get("inner_version"),
                        "element_count": summary.get("element_count"),
                        "element_track_geometries": summary.get("element_track_geometries"),
                    }
                outputs.append(entry)

            # The uncompressed id 0x05 name directory is authoritative: it
            # lists every slot, and its order matches the order of the id 0x06
            # style resources in the terminal index.  Aligning the two makes a
            # mismatch visible instead of silently dropping a name.
            directory_names = [
                item["name"] for item in (name_directory or {}).get("names", [])
            ]
            style_entries = [entry for entry in outputs if entry.get("style")]
            slots: list[dict[str, Any]] = []
            for position, entry in enumerate(style_entries):
                decoded_name = entry["style"].get("name")
                directory_name = (
                    directory_names[position] if position < len(directory_names) else None
                )
                slots.append({
                    "slot": position,
                    "directory_name": directory_name,
                    "decoded_name": decoded_name,
                    "agrees": (
                        None if directory_name is None or decoded_name is None
                        else directory_name == decoded_name
                    ),
                    "resource_position": entry["position"],
                    "sha256": entry["sha256"],
                    "size": entry["size"],
                    "inner_version": entry["style"].get("inner_version"),
                    "element_count": entry["style"].get("element_count"),
                    "element_track_geometries": entry["style"].get("element_track_geometries"),
                })
            disagreements = [slot for slot in slots if slot["agrees"] is False]
            style_names = list(dict.fromkeys(
                slot["decoded_name"] or slot["directory_name"]
                for slot in slots
                if slot["decoded_name"] or slot["directory_name"]
            ))

            manifest = {
                "decoder": self.name,
                "source": str(source),
                "source_sha256": _sha256_bytes(raw),
                "source_size": len(raw),
                "container_kind": kind,
                "container_notes": notes,
                "native_size": len(native),
                "native_sha256": _sha256_bytes(native),
                "framing": inventory["structured"].get("framing"),
                "resource_count": len(resources) + len(raw_resources),
                "compressed_resource_count": len(resources),
                "raw_resource_count": len(raw_resources),
                "resource_decode_errors": len(errors),
                "resources": outputs,
                "raw_resources": [
                    {key: item[key] for key in ("resource_position", "id", "payload_offset", "decoded_size", "sha256", "note")}
                    for item in raw_resources
                ],
                "style_names": style_names,
                "style_count": len(slots),
                "slots": slots,
                "name_directory_names": directory_names,
                "name_agreement": {
                    "compared": sum(1 for slot in slots if slot["agrees"] is not None),
                    "agreed": sum(1 for slot in slots if slot["agrees"] is True),
                    "disagreed": len(disagreements),
                    "directory_only": sorted(set(directory_names) - {s["decoded_name"] for s in slots}),
                },
                "raw_magic_probe": {
                    "magic_hits": inventory["raw_magic_probe"]["magic_hits"],
                    "accepted": inventory["raw_magic_probe"]["accepted_count"],
                    "rejected": inventory["raw_magic_probe"]["rejected_count"],
                },
            }
            manifest_path = output_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            return {
                "decoder": self.name,
                "status": "DECODED_VALIDATED" if resources and not errors else (
                    "DECODED_PARTIAL" if resources else "ATTEMPTED_FAILED"
                ),
                "output_directory": str(output_dir),
                "decoded_container": str(native_path),
                "decoded_container_size": len(native),
                "decoded_container_sha256": _sha256_bytes(native),
                "resource_count": len(resources) + len(raw_resources),
                "compressed_resource_count": len(resources),
                "raw_resource_count": len(raw_resources),
                "resource_decode_errors": len(errors),
                "resource_errors": errors,
                "style_names": style_names,
                "style_count": len(slots),
                "slots": slots,
                "name_directory_names": directory_names,
                "name_agreement": {
                    "compared": sum(1 for slot in slots if slot["agrees"] is not None),
                    "agreed": sum(1 for slot in slots if slot["agrees"] is True),
                    "disagreed": len(disagreements),
                    "directory_only": sorted(set(directory_names) - {s["decoded_name"] for s in slots}),
                },
                "outputs": outputs,
                "manifest": str(manifest_path),
            }
        except Exception as error:
            shutil.rmtree(output_dir, ignore_errors=True)
            return {
                "decoder": self.name,
                "status": "ATTEMPTED_FAILED",
                "error": f"{type(error).__name__}: {error}",
                "validation": {
                    "status": "REJECTED",
                    "evidence": ["no resource was written because the container did not unwrap cleanly"],
                },
            }

    def validate(self, result: dict[str, Any]) -> dict[str, Any]:
        container = result.get("decoded_container")
        valid = bool(
            container
            and Path(container).is_file()
            and Path(container).stat().st_size == result.get("decoded_container_size")
            and _sha256_bytes(Path(container).read_bytes()) == result.get("decoded_container_sha256")
        )
        if valid:
            return {
                "status": "VALIDATED",
                "evidence": [
                    "unwrapped container exists and its SHA-256/size match the recorded result",
                    f"{result.get('compressed_resource_count', result.get('resource_count', 0))} compressed resource(s) decoded with declared size + XOR checksum agreement",
                    f"{result.get('raw_resource_count', 0)} indexed resource(s) stored uncompressed",
                    f"{result.get('resource_decode_errors', 0)} resource(s) rejected",
                ],
            }
        return {
            "status": "UNVERIFIED",
            "evidence": ["decoded container missing or hash/size mismatch"],
        }

    def describe(self) -> str:
        return f"Real decoder for {self.container_label}."


class NativeContainerDecoder(KorgContainerDecoder):
    name = "korg-native-container"
    supported_inputs = ("KORG-STY", "KORG-PCG", "KORG-KMP", "KORG-PRF", "KORG-SBD", "KORG-PAD")
    container_label = "native Korg resource container (unwrapped)"
    output_suffix = ".bin"

    def unwrap(self, data: bytes) -> tuple[bytes, str, list[dict[str, Any]]]:
        kind = container_kind(data)
        if data[:4] == NATIVE_ROOT:
            return data, "native-container", ["input already starts with the native root header"]
        raise ValueError(f"not a native Korg container (leading bytes {data[:4].hex(' ')})")


class Oc20PackageDecoder(KorgContainerDecoder):
    name = "korg-oc20-package"
    supported_inputs = ("KORG-OC20",)
    container_label = "OC20 factory package (BKP/pkg flash pages)"
    output_suffix = ".bin"

    def unwrap(self, data: bytes) -> tuple[bytes, str, list[dict[str, Any]]]:
        if not is_oc20_pages(data):
            raise ValueError("input does not begin with an OC20 page")
        native, pages = decode_oc20_pages(data)
        return native, "oc20-factory-package", [f"{len(pages)} OC20 page(s) decoded"]


class KorgCandidateDecoder(Decoder):
    """Lexical indicator only.

    These slots previously carried the whole Korg story.  Now that the CBarc /
    OCxx container layer is implemented, a slot only stays a candidate when the
    codec above genuinely has nothing to bind to: an unknown inner payload
    format (``COMP16`` "module" blobs, ``LFO-PKG`` installer membership, raw
    PCM banks).  A lexical hit is an observation, never a decode.
    """

    term = b""
    name = "korg-candidate"
    supported_inputs: tuple[str, ...] = ()
    executable = False

    def detect(self, data: Any) -> dict[str, Any]:
        needles = {self.term, self.term.lower(), self.term.upper()}
        offsets = [data.find(needle) for needle in needles]
        offsets = [offset for offset in offsets if offset >= 0]
        offset = min(offsets) if offsets else -1
        return {
            "matched": offset >= 0,
            "confidence": 0.3 if offset >= 0 else 0.0,
            "evidence": [
                f"lexical indicator {self.term.decode('ascii', errors='replace')} at 0x{offset:X}; "
                "the inner payload format is not implemented"
            ] if offset >= 0 else [],
        }

    def decode(self, source: Path, output_root: Path, max_output_bytes: int) -> dict[str, Any]:
        return {
            "decoder": self.name,
            "status": "NOT_SUPPORTED",
            "implementation": "PLACEHOLDER_NOT_IMPLEMENTED",
        }

    def validate(self, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "NOT_VALIDATED",
            "evidence": ["no decoder is implemented for this inner payload format"],
        }

    def describe(self) -> str:
        return "Lexical indicator only; the inner payload format is not implemented."


DECODER = NativeContainerDecoder()
