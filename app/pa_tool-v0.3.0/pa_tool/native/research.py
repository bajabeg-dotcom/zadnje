"""Reproducible, no-write helpers for the imported PA800 forensic evidence.

The normal application accepts bounded ZIP uploads.  These helpers retain the
source project's offline corpus-audit logic, including optional RAR inventory
through a locally installed ``7z``/``bsdtar``.  They never execute archive
contents and never write instrument files.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any
from zipfile import ZipFile

from .format import Bank, FormatError, children, node_at
from .set_graph import dependency_candidates


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def safe_rel(name: str) -> bool:
    """Return whether an archive member is a safe relative POSIX path."""
    path = PurePosixPath(name.replace("\\", "/"))
    return (
        bool(path.parts)
        and not path.is_absolute()
        and ".." not in path.parts
        and all(part not in ("", ".") for part in path.parts)
        and ":" not in path.parts[0]
        and "\x00" not in name
    )


def zip_entries(path: str | Path, max_entries: int = 15_000) -> list[dict[str, Any]]:
    """Inventory ZIP metadata without extracting members to disk."""
    path = Path(path)
    with ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > max_entries:
            raise ValueError(f"Archive has more than {max_entries} entries")
        return [
            {
                "path": item.filename,
                "bytes": item.file_size,
                "compressed": item.compress_size,
                "encrypted": bool(item.flag_bits & 1),
                "unix_mode": (item.external_attr >> 16) & 0xFFFF,
                "safe_path": safe_rel(item.filename),
            }
            for item in infos
            if not item.is_dir()
        ]


def rar_entries(path: str | Path, timeout: int = 60) -> list[dict[str, Any]]:
    """Inventory RAR metadata through 7z when explicitly used offline."""
    proc = subprocess.run(
        ["7z", "l", "-slt", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=True,
    )
    entries = []
    for section in proc.stdout.split("\n\n"):
        metadata = dict(
            line.split(" = ", 1) for line in section.splitlines() if " = " in line
        )
        if metadata.get("Path") and "Size" in metadata and metadata.get("Folder") != "+":
            name = metadata["Path"]
            entries.append(
                {
                    "path": name,
                    "bytes": int(metadata["Size"]),
                    "compressed": int(metadata.get("Packed Size", 0)),
                    "encrypted": metadata.get("Encrypted") == "+",
                    "safe_path": safe_rel(name),
                    "crc32": metadata.get("CRC"),
                }
            )
    return entries


def read_member(
    archive_path: str | Path,
    name: str,
    limit: int = 5_000_000,
    timeout: int = 60,
) -> bytes:
    """Read one bounded, unencrypted member from ZIP or RAR without disk extraction."""
    path = Path(archive_path)
    if not safe_rel(name):
        raise ValueError("Unsafe member path")
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            info = archive.getinfo(name)
            if info.file_size > limit or (info.flag_bits & 1):
                raise ValueError("Member is too large or encrypted")
            return archive.read(info)  # ZipFile validates CRC while reading.
    proc = subprocess.run(
        ["bsdtar", "-xOf", str(path), name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if proc.returncode or len(proc.stdout) > limit:
        message = proc.stderr.decode("utf-8", "replace")[:140]
        raise ValueError("RAR member could not be read safely: " + message)
    return proc.stdout


def classify_resource(path: str, payload: bytes) -> dict[str, Any]:
    """Produce the source audit's bounded resource-family fingerprint."""
    output: dict[str, Any] = {
        "path": path,
        "size": len(payload),
        "sha256": sha256_bytes(payload),
        "magic": payload[:8].hex(" "),
    }
    try:
        bank = Bank(PurePosixPath(path).name, payload)
        info = bank.base_info()
        output.update(
            envelope=info["envelope"],
            resources=info["resources"],
            types=info["types"],
            index_valid=info["indexValid"],
        )
        families: Counter[str] = Counter()
        sizes: Counter[str] = Counter()
        codecs: Counter[str] = Counter()
        errors = []
        for index, node in enumerate(bank.resources):
            try:
                decoded, codec = bank._decoded_resource(index)
                codecs[codec] += 1
                sizes[str(len(decoded))] += 1
                suffix = PurePosixPath(path).suffix.upper()
                if suffix == ".STY" and node.tag[0] == 0x06:
                    inner = node_at(decoded, 0)
                    nodes = children(decoded, inner) if inner.end == len(decoded) else []
                    if len(nodes) < 3 or nodes[1].tag[0] != 2:
                        raise FormatError("Style root/pattern geometry is not the known family")
                    families[f"inner_{inner.tag.hex()}_elements_{len(nodes) - 2}"] += 1
                elif suffix == ".PCG" and node.tag[0] == 0x10:
                    families[f"program_{decoded[:2].hex()}_type_{node.tag[0]:02X}"] += 1
                else:
                    families[f"type_{node.tag[0]:02X}"] += 1
            except (FormatError, ValueError) as exc:
                errors.append(f"resource#{index}: {exc}")
        output.update(
            inner_families=dict(sorted(families.items())),
            decoded_sizes=dict(sizes),
            codecs=dict(codecs),
            decode_errors=errors[:20],
            decode_error_count=len(errors),
        )
    except FormatError as exc:
        output["parse_error"] = str(exc)
    return output


def summarize_archive(
    archive_path: str | Path,
    selected_extensions: tuple[str, ...] = (".sty", ".pcg", ".kmp", ".prf", ".gbl", ".pcm"),
    member_limit: int = 5_000_000,
) -> dict[str, Any]:
    """Reproduce the source static inventory for an explicit local archive."""
    archive = Path(archive_path)
    entries = zip_entries(archive) if archive.suffix.lower() == ".zip" else rar_entries(archive)
    result: dict[str, Any] = {
        "archive": archive.name,
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": sha256_bytes(archive.read_bytes()),
        "files": len(entries),
        "expanded_bytes": sum(item["bytes"] for item in entries),
        "extensions": dict(
            sorted(Counter(PurePosixPath(item["path"]).suffix.lower() for item in entries).items())
        ),
        "encrypted_files": sum(item["encrypted"] for item in entries),
        "unsafe_paths": [item["path"] for item in entries if not item["safe_path"]],
        "file_manifest": [
            {"path": item["path"], "bytes": item["bytes"], "encrypted": item["encrypted"]}
            for item in entries
        ],
        "inspected": [],
    }
    if result["unsafe_paths"]:
        return result
    for item in entries:
        name = item["path"]
        ext = PurePosixPath(name).suffix.lower()
        if (
            ext not in selected_extensions
            or item["encrypted"]
            or item["bytes"] > member_limit
            or (ext == ".pcm" and not name.lower().endswith("ram01.pcm"))
        ):
            continue
        raw = read_member(archive, name, member_limit)
        if len(raw) != item["bytes"]:
            raise ValueError("Unexpected extracted size for " + name)
        result["inspected"].append(classify_resource(name, raw))
    return result


def pcg_catalog(files: dict[str, bytes]) -> dict[str, Any]:
    """Expose PCG bank IDs, slots, and program-number collisions for research."""
    catalog: dict[str, Any] = {}
    for name, raw in files.items():
        if not name.lower().endswith(".pcg"):
            continue
        bank = Bank(PurePosixPath(name).name, raw)
        payload = bank.nodes[1].payload(bank.blob)
        slots: dict[int, list[str]] = defaultdict(list)
        bank_ids: Counter[str] = Counter()
        for index, title in enumerate(bank.names):
            record = payload[24 * index:24 * (index + 1)]
            bank_ids[f"{record[18]:02X}.{record[19]:02X}"] += 1
            slots[record[20]].append(title)
        catalog[PurePosixPath(name).name.upper()] = {
            "file": name,
            "bank_ids": dict(bank_ids),
            "slots": {str(key): value for key, value in slots.items()},
            "colliding_program_numbers": {
                str(key): value for key, value in slots.items() if len(value) > 1
            },
            "catalog_entries": len(bank.names),
        }
    return catalog


def analyze_selector_candidates(files: dict[str, bytes]) -> dict[str, Any]:
    """Run the stronger integrated sparse-aware selector graph over raw files."""
    banks = []
    failures = []
    for path, raw in files.items():
        try:
            banks.append((path, Bank(PurePosixPath(path).name, raw)))
        except (FormatError, ValueError) as exc:
            failures.append(f"{path}: {exc}")
    result = dependency_candidates(banks)
    if failures:
        result["warnings"] = failures + result["warnings"]
    return result
