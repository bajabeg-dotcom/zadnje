"""Bounded, read-only workspace for PA800 files and SET ZIP archives.

This is the integrated backend from PA800 Manager Web v0.2.  Uploads are held
in memory, original bytes are preserved, and no instrument file is rewritten.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
import stat
import threading
from zipfile import BadZipFile, ZipFile

from .codec import FormatError, MAX_FILE
from .format import Bank, EXTENSIONS
from .set_graph import dependency_candidates
from .sounds import load_sound_catalog

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT_ROOT / "data" / "pa800_native_fixtures"
ALLOWED = set(EXTENSIONS)
MAX_ARCHIVE_ENTRIES = 2000
MAX_ENTRY = 16 * 1024 * 1024
MAX_TOTAL_EXTRACTED = 64 * 1024 * 1024
MAX_MANIFEST_EXPANDED = 256 * 1024 * 1024
MAX_MANIFEST_MEMBER = 128 * 1024 * 1024
SOUND_ENTRIES, SOUND_LOOKUP = load_sound_catalog()

class UserError(Exception):
    pass


class Workspace:
    def __init__(self, load_demo: bool = True):
        self.lock = threading.RLock()
        self.files: list[dict] = []
        self.label = ""
        self.source = ""
        self.loaded_at = ""
        self.note = ""
        self.archive_manifest: dict | None = None
        self.original_archive: bytes | None = None
        self.graph_cache: dict | None = None
        self.audit_cache: dict | None = None
        if load_demo:
            self.demo()

    @staticmethod
    def _scan_one(filename: str, data: bytes) -> dict:
        file = {"name": filename, "raw": data, "bank": None, "error": None}
        try:
            file["bank"] = Bank(filename, data, SOUND_LOOKUP)
        except (FormatError, ValueError) as exc:
            file["error"] = str(exc)
        return file

    def _set(self, files: list[tuple[str, bytes]], label: str, source: str, note: str = "",
             manifest: dict | None = None, original_archive: bytes | None = None) -> None:
        scanned = [self._scan_one(name, data) for name, data in files]
        if not scanned and manifest is None:
            raise UserError("Nema podržanih PA800 fajlova (STY, PRF, PAD, PCG, GBL, TBL).")
        with self.lock:
            self.files = scanned
            self.label = label
            self.source = source
            self.note = note
            self.archive_manifest = manifest
            self.original_archive = original_archive
            self.loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.audit_cache = None
            self.graph_cache = None

    def demo(self) -> dict:
        files = [(p.name, p.read_bytes()) for p in (
            FIXTURES / "BANK01.STY", FIXTURES / "BANK01.PRF",
            FIXTURES / "Bank01.pad", FIXTURES / "ROM.PCG",
            FIXTURES / "PRGMAPS.TBL", FIXTURES / "SETUP.GBL",
        )]
        self._set(files, "PA800 · Factory demo", "demo", "Demonstracijski podaci iz dostavljenog PA800 factory korpusa (22. 9. 2026).")
        return self.overview()

    def import_file(self, filename: str, data: bytes) -> dict:
        if not filename or len(filename) > 255:
            raise UserError("Nevažeće ime fajla")
        if len(data) > MAX_FILE:
            raise UserError("Prevelik fajl (limit 64 MiB)")
        name = Path(filename).name
        if name.lower().endswith(".zip"):
            pairs: list[tuple[str, bytes]] = []
            member_rows = []
            uncompressed_total = parsed_total = 0
            seen: set[str] = set()
            kinds = Counter()
            set_roots: set[str] = set()
            try:
                with ZipFile(BytesIO(data)) as archive:
                    infos = archive.infolist()
                    if len(infos) > MAX_ARCHIVE_ENTRIES:
                        raise UserError("Arhiva sadrži previše stavki (limit 2.000).")
                    for info in infos:
                        normalized = info.filename.replace("\\", "/")
                        parts = PurePosixPath(normalized)
                        if (parts.is_absolute() or ".." in parts.parts or not parts.name
                                or not parts.parts or ":" in parts.parts[0] or "\x00" in normalized
                                or len(normalized) > 512):
                            raise UserError("Arhiva ima nesigurno ime datoteke")
                        if stat.S_ISLNK(info.external_attr >> 16):
                            raise UserError("ZIP sadrži simboličku vezu; nije podržana")
                        if info.is_dir():
                            continue
                        key = normalized.casefold()
                        if key in seen:
                            raise UserError("Duplirana putanja u arhivi; ne mogu pouzdano odrediti resurse")
                        seen.add(key)
                        if info.file_size < 0 or info.file_size > MAX_MANIFEST_MEMBER:
                            raise UserError("Jedna datoteka u arhivi prelazi sigurnosni limit od 128 MiB")
                        uncompressed_total += info.file_size
                        if uncompressed_total > MAX_MANIFEST_EXPANDED:
                            raise UserError("SET prelazi limit od 256 MiB raspakovanih podataka")
                        if info.file_size > 1024 * 1024 and info.file_size / max(info.compress_size, 1) > 1000:
                            raise UserError("Sumnjiv omjer kompresije u arhivi")
                        for comp in parts.parts:
                            if comp.upper().endswith(".SET"):
                                set_roots.add(comp)
                                break
                        suffix = parts.suffix.upper() or "(bez ekstenzije)"
                        kinds[suffix] += 1
                        encrypted = bool(info.flag_bits & 1)
                        digest = None
                        if not encrypted:
                            # Reading through ZipFile.open checks each CRC, even
                            # for PCM/SongBook/BKP entries not parsed as a bank.
                            h = sha256()
                            with archive.open(info, "r") as member:
                                while chunk := member.read(128 * 1024):
                                    h.update(chunk)
                            digest = h.hexdigest()
                        if suffix in ALLOWED and not encrypted:
                            if info.file_size > MAX_ENTRY:
                                raise UserError("Resource bank u arhivi prelazi limit od 16 MiB")
                            parsed_total += info.file_size
                            if parsed_total > MAX_TOTAL_EXTRACTED:
                                raise UserError("Za dekodiranje je potrebno više od 64 MiB resursa")
                            pairs.append((str(parts), archive.read(info)))
                        member_rows.append({"path": str(parts), "bytes": info.file_size,
                                            "packedBytes": info.compress_size, "extension": suffix,
                                            "sha256": digest, "crcVerified": not encrypted,
                                            "encrypted": encrypted, "bankParsed": suffix in ALLOWED and not encrypted})
            except (BadZipFile, RuntimeError, OSError, NotImplementedError) as exc:
                raise UserError(f"ZIP arhiva se ne može otvoriti ili potvrditi CRC: {exc}") from exc
            if not member_rows:
                raise UserError("ZIP je prazan; pošalji ZIP sa sadržajem .SET foldera")
            manifest = {
                "archiveName": name, "archiveBytes": len(data), "archiveSha256": sha256(data).hexdigest(),
                "fileCount": len(member_rows), "expandedBytes": uncompressed_total,
                "parsedBankCount": len(pairs), "setRoots": sorted(set_roots),
                "extensions": dict(sorted(kinds.items())), "members": member_rows,
                "bkpPresent": kinds.get(".BKP", 0) > 0,
                "encryptedFiles": sum(row["encrypted"] for row in member_rows),
                "notice": "Svi nešifrirani članovi imaju potvrđen CRC i SHA-256. PCM/KMP/SongBook/BKP su samo inventarisani; ovo nije native konverzija ni BKP restore.",
            }
            self._set(pairs, name, "uploaded-zip", f"{len(member_rows)} datoteka; {len(pairs)} banki dekodirano. Originalna ZIP arhiva sačuvana je bajt-identično u memoriji.", manifest, data)
        elif Path(name).suffix.upper() in ALLOWED:
            self._set([(name, data)], name, "uploaded-file", "Original je sačuvan bajt-po-bajt u sesiji; dostupan za ponovno preuzimanje.")
        else:
            raise UserError("Tip fajla nije podržan. Koristi STY, PRF, PAD, PCG, GBL, TBL ili ZIP od SET/ALL foldera.")
        return self.overview()

    def overview(self) -> dict:
        with self.lock:
            files = []
            counts = {"styles": 0, "performances": 0, "pads": 0, "soundResources": 0, "files": len(self.files), "validFiles": 0}
            for idx, item in enumerate(self.files):
                bank: Bank | None = item["bank"]
                if bank:
                    counts["validFiles"] += 1
                    stats = bank.base_info()
                    counts["styles"] += bank.count_by_tag.get("06", 0) if bank.ext == ".STY" else 0
                    counts["performances"] += bank.count_by_tag.get("09", 0) if bank.ext == ".PRF" else 0
                    counts["pads"] += bank.count_by_tag.get("0C", 0) if bank.ext == ".PAD" else 0
                    counts["soundResources"] += len(bank.resources) if bank.ext == ".PCG" else 0
                else:
                    stats = {"name": item["name"], "kind": "Nepoznat", "bytes": len(item["raw"]), "resources": 0, "safety": "UNSUPPORTED"}
                files.append({"id": idx, **stats, "path": item["name"], "error": item["error"]})
            return {
                "label": self.label, "source": self.source, "loadedAt": self.loaded_at,
                "note": self.note, "counts": counts, "files": files,
                "archiveSummary": ({key: self.archive_manifest[key] for key in (
                    "archiveName", "archiveBytes", "archiveSha256", "fileCount", "expandedBytes",
                    "parsedBankCount", "setRoots", "extensions", "bkpPresent", "encryptedFiles",
                )} if self.archive_manifest is not None else None),
                "soundCatalogCount": len(SOUND_ENTRIES),
                "writeSupported": False,
                "deviceCertified": False,
                "notice": "Strukturni pregled je softverski validiran; native upis i fizički PA800 nisu certificirani.",
            }

    def get(self, index: int) -> dict:
        with self.lock:
            if not 0 <= index < len(self.files):
                raise UserError("Fajl nije pronađen")
            return self.files[index]

    def graph(self) -> dict:
        with self.lock:
            if self.graph_cache is None:
                self.graph_cache = dependency_candidates(
                    (item["name"], item["bank"]) for item in self.files if item["bank"] is not None
                )
            return self.graph_cache

    def manifest(self) -> dict:
        with self.lock:
            if self.archive_manifest is None:
                raise UserError("Uvezi ZIP koji sadrži .SET/ALL folder za potpun inventar datoteka")
            return self.archive_manifest

    def original_zip(self) -> tuple[str, bytes]:
        with self.lock:
            if self.original_archive is None:
                raise UserError("Nije učitana originalna ZIP arhiva")
            return self.label, self.original_archive

    def audit(self) -> dict:
        with self.lock:
            if self.audit_cache is not None:
                return self.audit_cache
            checked = 0
            preserve_only = 0
            codecs: dict[str, int] = {}
            failures = []
            warnings = []
            for file_ix, item in enumerate(self.files):
                bank: Bank | None = item["bank"]
                if bank is None:
                    failures.append({"file": item["name"], "reason": item["error"]})
                    continue
                for res_ix, row in enumerate(bank.resource_listing()):
                    try:
                        detail = bank.resource_detail(res_ix)
                        checked += 1
                        codec = detail["codec"]
                        codecs[codec] = codecs.get(codec, 0) + 1
                        if detail.get("coverage") == "PRESERVE_ONLY":
                            preserve_only += 1
                            if len(warnings) < 100:
                                warnings.append({"file": item["name"], "resource": row["name"], "notice": detail["note"]})
                        for notice in detail.get("warnings", []):
                            if len(warnings) < 100:
                                warnings.append({"file": item["name"], "resource": row["name"], "notice": notice})
                    except (FormatError, ValueError) as exc:
                        failures.append({"file": item["name"], "resource": row["name"], "reason": str(exc)})
            self.audit_cache = {
                "checkedResources": checked, "preserveOnly": preserve_only, "codecs": codecs, "failures": failures,
                "warnings": warnings, "files": len(self.files),
                "status": "PASS" if checked and not failures and not preserve_only else "REVIEW",
                "scope": "Učitane STY/PRF/PAD/PCG/GBL/TBL banke: OC20 checksum, KBEG/KEND, OC3x XOR, poznata Style/PAD/PRF geometrija. PCM/KMP/SongBook/BKP samo ZIP CRC+SHA inventar. Nepoznata polja i hardware nisu potvrđeni.",
                "hardware": "PENDING",
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            return self.audit_cache

    def report(self) -> dict:
        overview = self.overview()
        return {
            "title": "PA800 Manager · Izvještaj provjere",
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "workspace": overview["label"], "source": overview["source"],
            "mode": "READ_ONLY", "physicalDeviceAcceptance": "NOT_TESTED",
            "deepAudit": self.audit_cache if self.audit_cache is not None else "NOT_RUN",
            "safety": "Izvorni fajlovi nisu izmijenjeni. OC20 završni footer nije semantički protumačen; zato nema native upisa.",
            "counts": overview["counts"],
            "archiveSummary": overview["archiveSummary"],
            "nativeGraph": {key: value for key, value in self.graph().items() if key not in ("references",)},
            "files": [{key: value for key, value in row.items() if key in ("path", "bytes", "decodedBytes", "pages", "resources", "types", "sha256", "indexValid", "error")} for row in overview["files"]],
            "evidence": ["PA800 master evidence dossier 2026-09-22", "PA800 reverse evidence complete 2026-09-22", "Factory Sound List CC00.CC32.PC 2026-09-22"],
        }

