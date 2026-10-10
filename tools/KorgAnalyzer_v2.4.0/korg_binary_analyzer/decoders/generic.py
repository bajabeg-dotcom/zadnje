from __future__ import annotations

import bz2
import gzip
import hashlib
import lzma
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .base import Decoder

CHUNK_SIZE = 1024 * 1024


def _new_output_dir(root: Path, name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    base = root / name
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = root / f"{name}-{suffix}"
        suffix += 1
    candidate.mkdir()
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(CHUNK_SIZE):
            digest.update(block)
    return digest.hexdigest()


class _StreamingDecoder(Decoder):
    magic = b""
    name = "stream"
    output_name = "decoded.bin"

    def detect(self, data: Any) -> dict[str, Any]:
        matched = bytes(data[:len(self.magic)]) == self.magic
        return {"matched": matched, "confidence": 0.99 if matched else 0.0, "evidence": [f"magic {self.magic.hex(' ')} at offset 0"] if matched else []}

    def _reader(self, source: Path) -> Any:
        raise NotImplementedError

    def decode(self, source: Path, output_root: Path, max_output_bytes: int) -> dict[str, Any]:
        output_dir = _new_output_dir(output_root, source.stem or self.name)
        output_path = output_dir / self.output_name
        written = 0
        try:
            with self._reader(source) as reader, output_path.open("xb") as output:
                while block := reader.read(CHUNK_SIZE):
                    written += len(block)
                    if written > max_output_bytes:
                        raise ValueError(f"decoded output exceeded configured limit of {max_output_bytes} bytes")
                    output.write(block)
            checksum_validated = self.name in ("gzip", "bzip2", "xz")
            return {
                "decoder": self.name, "status": "DECODED_VALIDATED" if checksum_validated else "DECODED_UNVERIFIED",
                "output": str(output_path), "output_size": written, "output_sha256": _sha256(output_path),
                "validation": {"status": "VALIDATED" if checksum_validated else "UNVERIFIED", "evidence": ["stream reached its end without decompression error", "format checksum/trailer was checked by the standard-library decoder"] if checksum_validated else ["decompression completed; no independent integrity checksum was confirmed"]},
            }
        except Exception as error:
            shutil.rmtree(output_dir, ignore_errors=True)
            return {"decoder": self.name, "status": "ATTEMPTED_FAILED", "error": f"{type(error).__name__}: {error}", "validation": {"status": "REJECTED", "evidence": ["decoder did not complete with a validated output"]}}

    def validate(self, result: dict[str, Any]) -> dict[str, Any]:
        output = Path(result["output"]) if result.get("output") else None
        valid = bool(output and output.is_file() and output.stat().st_size == result.get("output_size") and _sha256(output) == result.get("output_sha256"))
        return {"status": "VALIDATED" if valid and result.get("status") == "DECODED_VALIDATED" else "UNVERIFIED", "evidence": ["output exists and SHA-256/size match the recorded result"] if valid else ["output hash/size check failed"]}


class GzipDecoder(_StreamingDecoder):
    name = "gzip"
    supported_inputs = ("GZIP",)
    magic = b"\x1f\x8b\x08"

    def _reader(self, source: Path) -> Any:
        return gzip.open(source, "rb")


class Bzip2Decoder(_StreamingDecoder):
    name = "bzip2"
    supported_inputs = ("BZIP2",)
    magic = b"BZh"

    def _reader(self, source: Path) -> Any:
        return bz2.open(source, "rb")


class XzDecoder(_StreamingDecoder):
    name = "xz/lzma"
    supported_inputs = ("XZ",)
    magic = b"\xfd7zXZ\x00"

    def _reader(self, source: Path) -> Any:
        return lzma.open(source, "rb")


class ZipDecoder(Decoder):
    name = "zip"
    supported_inputs = ("ZIP",)

    def detect(self, data: Any) -> dict[str, Any]:
        prefix = bytes(data[:4])
        matched = prefix in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
        return {"matched": matched, "confidence": 0.99 if matched else 0.0, "evidence": [f"ZIP container marker {prefix.hex(' ')} at offset 0"] if matched else []}

    def decode(self, source: Path, output_root: Path, max_output_bytes: int) -> dict[str, Any]:
        output_dir = _new_output_dir(output_root, source.stem or "archive")
        total_written = 0
        outputs: list[dict[str, Any]] = []
        try:
            with zipfile.ZipFile(source, "r") as archive:
                infos = archive.infolist()
                if sum(info.file_size for info in infos) > max_output_bytes:
                    raise ValueError("declared ZIP expanded size exceeds configured extraction limit")
                for info in infos:
                    normalized = info.filename.replace("\\", "/")
                    relative = PurePosixPath(normalized)
                    if relative.is_absolute() or any(part in ("..", "") for part in relative.parts) or (relative.parts and ":" in relative.parts[0]):
                        raise ValueError(f"unsafe ZIP member path rejected: {info.filename!r}")
                    if info.is_dir():
                        continue
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if mode & 0o170000 == 0o120000:
                        raise ValueError(f"symbolic-link ZIP member rejected: {info.filename!r}")
                    destination = output_dir.joinpath(*relative.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if not destination.resolve().is_relative_to(output_dir.resolve()):
                        raise ValueError(f"ZIP member escaped extraction directory: {info.filename!r}")
                    member_written = 0
                    digest = hashlib.sha256()
                    with archive.open(info, "r") as member, destination.open("xb") as output:
                        while block := member.read(CHUNK_SIZE):
                            member_written += len(block)
                            total_written += len(block)
                            if total_written > max_output_bytes:
                                raise ValueError("streamed ZIP contents exceeded configured extraction limit")
                            digest.update(block)
                            output.write(block)
                    outputs.append({"name": info.filename, "path": str(destination), "size": member_written, "sha256": digest.hexdigest(), "crc32_validated": True})
            status = "DECODED_VALIDATED"
            return {
                "decoder": self.name, "status": status, "output_directory": str(output_dir),
                "output_size": total_written, "outputs": outputs,
                "validation": {"status": "VALIDATED", "evidence": ["ZIP central directory parsed", "every regular member was fully streamed; standard library checked CRCs", "member paths were constrained to the extraction directory"]},
            }
        except Exception as error:
            shutil.rmtree(output_dir, ignore_errors=True)
            return {"decoder": self.name, "status": "ATTEMPTED_FAILED", "error": f"{type(error).__name__}: {error}", "validation": {"status": "REJECTED", "evidence": ["ZIP extraction was incomplete or failed validation; partial outputs were removed"]}}

    def validate(self, result: dict[str, Any]) -> dict[str, Any]:
        valid = result.get("status") == "DECODED_VALIDATED" and all(Path(item["path"]).is_file() and Path(item["path"]).stat().st_size == item["size"] for item in result.get("outputs", []))
        return {"status": "VALIDATED" if valid else "REJECTED", "evidence": ["each extracted member exists with recorded size; member streaming verified ZIP CRCs"] if valid else ["one or more extracted member checks failed"]}