#!/usr/bin/env python3
"""Local-first PA800 Manager web prototype (no external dependencies).

The server reads files in memory and never rewrites the uploaded instrument data.
Use --host 127.0.0.1 locally, or --host 0.0.0.0 for Arena live preview.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import threading
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile, BadZipFile

from core.codec import FormatError, MAX_FILE
from core.format import Bank, EXTENSIONS
from core.sounds import load_sound_catalog

HERE = Path(__file__).resolve().parent
PUBLIC = HERE / "public"
FIXTURES = HERE / "fixtures"
ALLOWED = set(EXTENSIONS)
MAX_ARCHIVE_ENTRIES = 500
MAX_ENTRY = 16 * 1024 * 1024
MAX_TOTAL_EXTRACTED = 64 * 1024 * 1024
SOUND_ENTRIES, SOUND_LOOKUP = load_sound_catalog()


class UserError(Exception):
    pass


class Workspace:
    def __init__(self):
        self.lock = threading.RLock()
        self.files: list[dict] = []
        self.label = ""
        self.source = ""
        self.loaded_at = ""
        self.note = ""
        self.audit_cache: dict | None = None
        self.demo()

    @staticmethod
    def _scan_one(filename: str, data: bytes) -> dict:
        file = {"name": filename, "raw": data, "bank": None, "error": None}
        try:
            file["bank"] = Bank(filename, data, SOUND_LOOKUP)
        except (FormatError, ValueError) as exc:
            file["error"] = str(exc)
        return file

    def _set(self, files: list[tuple[str, bytes]], label: str, source: str, note: str = "") -> None:
        scanned = [self._scan_one(name, data) for name, data in files]
        if not scanned:
            raise UserError("U arhivi nema podržanih PA800 fajlova (STY, PRF, PAD, PCG, GBL, TBL).")
        with self.lock:
            self.files = scanned
            self.label = label
            self.source = source
            self.note = note
            self.loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.audit_cache = None

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
            pairs = []
            uncompressed_total = 0
            try:
                with ZipFile(BytesIO(data)) as archive:
                    infos = archive.infolist()
                    if len(infos) > MAX_ARCHIVE_ENTRIES:
                        raise UserError("Arhiva sadrži previše stavki (maksimalno 500).")
                    for info in infos:
                        if info.is_dir():
                            continue
                        parts = PurePosixPath(info.filename.replace("\\", "/"))
                        if parts.is_absolute() or ".." in parts.parts or not parts.name:
                            raise UserError("Arhiva ima nesigurno ime datoteke")
                        if parts.suffix.upper() not in ALLOWED:
                            continue
                        if info.file_size > MAX_ENTRY:
                            raise UserError("Resurs u arhivi prelazi limit od 16 MiB")
                        uncompressed_total += info.file_size
                        if uncompressed_total > MAX_TOTAL_EXTRACTED:
                            raise UserError("Arhiva prelazi limit od 64 MiB dekomprimovanih podataka")
                        if info.file_size > 1024 * 1024 and info.compress_size < 1024 and info.file_size / max(info.compress_size, 1) > 1000:
                            raise UserError("Sumnjiv omjer kompresije u arhivi")
                        pairs.append((str(parts), archive.read(info)))
            except (BadZipFile, RuntimeError, OSError) as exc:
                raise UserError(f"ZIP arhiva se ne može otvoriti: {exc}") from exc
            if not pairs:
                raise UserError("ZIP ne sadrži STY/PRF/PAD/PCG/GBL/TBL fajlove. Ako ima ugniježđen ZIP, raspakuj ga prvo.")
            self._set(pairs, name, "uploaded-zip", f"{len(pairs)} resursa iz ZIP arhive; nikakva izmjena nije izvršena.")
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

    def audit(self) -> dict:
        with self.lock:
            if self.audit_cache is not None:
                return self.audit_cache
            checked = 0
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
                        for notice in detail.get("warnings", []):
                            if len(warnings) < 100:
                                warnings.append({"file": item["name"], "resource": row["name"], "notice": notice})
                    except (FormatError, ValueError) as exc:
                        failures.append({"file": item["name"], "resource": row["name"], "reason": str(exc)})
            self.audit_cache = {
                "checkedResources": checked, "codecs": codecs, "failures": failures,
                "warnings": warnings, "files": len(self.files),
                "status": "PASS" if not failures else "REVIEW",
                "scope": "OC20 checksum stranica, KBEG/KEND, OC3x XOR, Style/PAD geometrija i poznate PRF varijante. Završnih pet OC20 bajtova i stvarna klavijatura nisu potvrđeni.",
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
            "files": [{key: value for key, value in row.items() if key in ("path", "bytes", "decodedBytes", "pages", "resources", "types", "sha256", "indexValid", "error")} for row in overview["files"]],
            "evidence": ["PA800 master evidence dossier 2026-09-22", "PA800 reverse evidence complete 2026-09-22", "Factory Sound List CC00.CC32.PC 2026-09-22"],
        }


workspace = Workspace()


class Handler(BaseHTTPRequestHandler):
    server_version = "PA800Manager/0.1"

    def log_message(self, fmt, *args):
        print("[web] " + fmt % args, flush=True)

    def send_data(self, data: bytes, mime: str, code: int = 200, filename: str | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if filename:
            safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in filename)
            self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
        self.end_headers()
        self.wfile.write(data)

    def json(self, value: dict | list, code: int = 200, filename: str | None = None) -> None:
        self.send_data(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), "application/json; charset=utf-8", code, filename)

    def error(self, code: int, message: str) -> None:
        self.json({"error": message}, code)

    def do_GET(self) -> None:
        path = urlsplit(self.path)
        params = parse_qs(path.query)
        try:
            if path.path in ("/", "/index.html", "/app.js", "/style.css"):
                file = PUBLIC / ("index.html" if path.path == "/" else path.path.lstrip("/"))
                mime = "text/html; charset=utf-8" if file.suffix == ".html" else ("text/css; charset=utf-8" if file.suffix == ".css" else "text/javascript; charset=utf-8")
                self.send_data(file.read_bytes(), mime)
            elif path.path == "/api/status":
                self.json(workspace.overview())
            elif path.path == "/api/demo":
                self.json(workspace.demo())
            elif path.path == "/api/file":
                index = int(params.get("id", ["0"])[0])
                item = workspace.get(index)
                if item["bank"] is None:
                    self.json({"file": {"id": index, "path": item["name"], "error": item["error"]}, "resources": []})
                else:
                    bank: Bank = item["bank"]
                    self.json({"file": {"id": index, "path": item["name"], **bank.base_info()}, "resources": bank.resource_listing()})
            elif path.path == "/api/resource":
                file_ix = int(params.get("file", ["-1"])[0])
                res_ix = int(params.get("resource", ["-1"])[0])
                item = workspace.get(file_ix)
                if item["bank"] is None:
                    raise UserError("Fajl nema potvrđenu resource strukturu")
                with workspace.lock:
                    self.json(item["bank"].resource_detail(res_ix))
            elif path.path == "/api/sounds":
                query = params.get("q", [""])[0].strip().lower()[:120]
                start = max(0, int(params.get("start", ["0"])[0]))
                matches = [row for row in SOUND_ENTRIES if query in row["name"].lower() or query in f'{row["msb"]}.{row["lsb"]}.{row["pc"]}']
                self.json({"total": len(matches), "start": start, "items": matches[start:start + 80]})
            elif path.path == "/api/audit":
                self.json(workspace.audit())
            elif path.path == "/api/report":
                self.json(workspace.report(), filename="PA800_ANALYSIS_REPORT.json")
            elif path.path == "/api/original":
                index = int(params.get("id", ["-1"])[0])
                item = workspace.get(index)
                self.send_data(item["raw"], "application/octet-stream", filename=Path(item["name"]).name)
            elif path.path == "/api/health":
                self.json({"ok": True, "service": "PA800 Manager", "mode": "read-only"})
            else:
                self.error(404, "Stranica ne postoji")
        except (UserError, FormatError, ValueError) as exc:
            self.error(400, str(exc))
        except OSError:
            self.error(500, "Greška pri čitanju datoteke")

    def do_POST(self) -> None:
        path = urlsplit(self.path)
        if path.path != "/api/open":
            self.error(404, "Nepoznata akcija")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_FILE:
                raise UserError("Prazan ili prevelik upload (limit 64 MiB)")
            filename = parse_qs(path.query).get("name", [""])[0]
            data = self.rfile.read(length)
            if len(data) != length:
                raise UserError("Upload je prekinut")
            self.json(workspace.import_file(filename, data))
        except (UserError, ValueError) as exc:
            self.error(400, str(exc))
        except Exception:
            self.error(500, "Interna greška pri obradi fajla")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"PA800 Manager · http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
