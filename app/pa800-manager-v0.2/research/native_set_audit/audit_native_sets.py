"""Static, no-write audit of user-supplied Korg SET archives.

Operates on explicit local paths; never executes contents or writes instrument files.
Large Drive archives are staged in .cache, excluded from workspace snapshots.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
from core.format import Bank, FormatError, children, node_at  # noqa: E402

# Set PA800_NATIVE_INPUT to a directory holding the archive files before use.
CACHED = Path(os.getenv("PA800_NATIVE_INPUT", str(PROJECT.parent / ".cache/native_set_source")))
USER_STY = Path(os.getenv("PA800_USER_STY", str(PROJECT.parent / "google_drive/USER01.STY")))
OUT = Path(__file__).resolve().parent


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def safe_rel(name: str) -> bool:
    p = PurePosixPath(name.replace("\\", "/"))
    return bool(p.parts) and not p.is_absolute() and ".." not in p.parts and all(x not in ("", ".") for x in p.parts)


def zip_entries(path: Path):
    with ZipFile(path) as z:
        infos = z.infolist()
        assert len(infos) <= 15000
        files = [x for x in infos if not x.is_dir()]
        return [{"path": x.filename, "bytes": x.file_size, "compressed": x.compress_size,
                 "encrypted": bool(x.flag_bits & 1), "unix_mode": (x.external_attr >> 16) & 0xFFFF,
                 "safe_path": safe_rel(x.filename)} for x in files]


def rar_entries(path: Path):
    proc = subprocess.run(["7z", "l", "-slt", str(path)], text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=True)
    entries = []
    for section in proc.stdout.split("\n\n"):
        metadata = dict(line.split(" = ", 1) for line in section.splitlines() if " = " in line)
        if metadata.get("Path") and "Size" in metadata and metadata.get("Folder") != "+":
            name = metadata["Path"]
            entries.append({"path": name, "bytes": int(metadata["Size"]),
                            "compressed": int(metadata.get("Packed Size", 0)),
                            "encrypted": metadata.get("Encrypted") == "+",
                            "safe_path": safe_rel(name), "crc32": metadata.get("CRC")})
    return entries


def read_member(path: Path, name: str, limit=5_000_000) -> bytes:
    if not safe_rel(name):
        raise ValueError("Unsafe member path")
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as z:
            info = z.getinfo(name)
            if info.file_size > limit or (info.flag_bits & 1):
                raise ValueError("Too large or encrypted")
            return z.read(info)
    proc = subprocess.run(["bsdtar", "-xOf", str(path), name], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=60)
    if proc.returncode or len(proc.stdout) > limit:
        raise ValueError("RAR member could not be read safely: " + proc.stderr.decode("utf-8", "replace")[:140])
    return proc.stdout


def classify_resource(path: str, payload: bytes) -> dict:
    output = {"path": path, "size": len(payload), "sha256": sha(payload), "magic": payload[:8].hex(" ")}
    try:
        bank = Bank(PurePosixPath(path).name, payload)
        info = bank.base_info()
        output.update(envelope=info["envelope"], resources=info["resources"], types=info["types"],
                      index_valid=info["indexValid"])
        families: Counter[str] = Counter()
        sizes: Counter[str] = Counter()
        codecs: Counter[str] = Counter()
        errors = []
        for i, node in enumerate(bank.resources):
            try:
                decoded, codec = bank._decoded_resource(i)
                codecs[codec] += 1
                sizes[str(len(decoded))] += 1
                if PurePosixPath(path).suffix.upper() == ".STY" and node.tag[0] == 0x06:
                    inner = node_at(decoded, 0)
                    nodes = children(decoded, inner) if inner.end == len(decoded) else []
                    if len(nodes) < 3 or nodes[1].tag[0] != 2:
                        raise FormatError("Style root/pattern geometry is not the known family")
                    families[f"inner_{inner.tag.hex()}_elements_{len(nodes)-2}"] += 1
                elif PurePosixPath(path).suffix.upper() == ".PCG" and node.tag[0] == 0x10:
                    families[f"program_{decoded[:2].hex()}_type_{node.tag[0]:02X}"] += 1
                else:
                    families[f"type_{node.tag[0]:02X}"] += 1
            except (FormatError, ValueError) as exc:
                errors.append(f"resource#{i}: {exc}")
        output["inner_families"] = dict(sorted(families.items()))
        output["decoded_sizes"] = dict(sizes)
        output["codecs"] = dict(codecs)
        output["decode_errors"] = errors[:20]
        output["decode_error_count"] = len(errors)
    except FormatError as exc:
        output["parse_error"] = str(exc)
    return output


def summarize(archive: Path, selected_extensions=(".sty", ".pcg", ".kmp", ".prf", ".gbl", ".pcm")) -> dict:
    entries = zip_entries(archive) if archive.suffix.lower() == ".zip" else rar_entries(archive)
    result = {"archive": archive.name, "archive_bytes": archive.stat().st_size,
              "archive_sha256": sha(archive.read_bytes()),
              "files": len(entries), "expanded_bytes": sum(x["bytes"] for x in entries),
              "extensions": dict(sorted(Counter(PurePosixPath(x["path"]).suffix.lower() for x in entries).items())),
              "encrypted_files": sum(x["encrypted"] for x in entries),
              "unsafe_paths": [x["path"] for x in entries if not x["safe_path"]],
              "file_manifest": [{"path": x["path"], "bytes": x["bytes"], "encrypted": x["encrypted"]} for x in entries],
              "inspected": []}
    if not result["unsafe_paths"]:
        for item in entries:
            name = item["path"]
            ext = PurePosixPath(name).suffix.lower()
            # The PCM audio sample banks have many members. Inventory all, but
            # inspect the first sample bank as a format-family fingerprint only.
            if ext not in selected_extensions or item["encrypted"] or (ext == ".pcm" and not name.lower().endswith("ram01.pcm")):
                continue
            if item["bytes"] > 5_000_000:
                continue
            raw = read_member(archive, name)
            if len(raw) != item["bytes"]:
                raise AssertionError("Unexpected extracted size for " + name)
            result["inspected"].append(classify_resource(name, raw))
    return result


def main():
    archives = [CACHED / n for n in ("Hammy.SET.zip", "Pa1x BABAJA 2011 bosna.SET.rar",
                                      "Pa1x Redbull Novoboki Greek.SET.rar")]
    result = {"purpose": "Forensic static inventory, not PA800 OS2.02 native conversion",
              "sources": [summarize(p) for p in archives],
              "direct_sty": classify_resource("USER01.STY", USER_STY.read_bytes())}
    with ZipFile(CACHED / "pa800_pkg_extracted.zip") as z:
        factory_paths = [x.filename for x in z.infolist() if x.filename.startswith("STARTUP/ALL/") and x.filename.upper().endswith((".STY", ".PCG", ".PRF", ".KMP", ".TBL"))]
        result["factory_reference"] = {"archive": "pa800_pkg_extracted.zip", "version_status": "Drive filename does not certify OS version",
                                       "files": [classify_resource(n, z.read(n)) for n in factory_paths if n.upper().endswith((".STY", ".PCG", ".PRF", ".KMP"))],
                                       "map_paths": [n for n in factory_paths if n.upper().endswith(".TBL")]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "NATIVE_SET_STATIC_INVENTORY.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Wrote inventory, archives",[(x["archive"], x["files"], x["encrypted_files"], len(x["inspected"])) for x in result["sources"]])
    for s in result["sources"]:
        print(s["archive"], "style bank fingerprints:")
        for row in s["inspected"]:
            if row["path"].lower().endswith((".sty", ".pcg", ".pcm", ".kmp")):
                print(" ", row["path"], row.get("resources"), row.get("inner_families"), "errors", row.get("decode_error_count"), row.get("parse_error"))
    print("Factory files", len(result["factory_reference"]["files"]), "direct", result["direct_sty"]["inner_families"])


if __name__ == "__main__":
    main()
