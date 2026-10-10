"""Local read-only web UI for the integrated PA800 native inspector."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .. import validator
from .codec import FormatError, MAX_FILE
from .format import Bank
from .workspace import SOUND_ENTRIES, UserError, Workspace

PUBLIC = Path(__file__).resolve().parent.parent / "gui" / "web"
workspace = Workspace()

class Handler(BaseHTTPRequestHandler):
    server_version = "pa_tool/0.3.0-PA800-forensics"

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
            elif path.path == "/api/archive":
                self.json(workspace.manifest())
            elif path.path == "/api/dependencies":
                self.json(workspace.graph())
            elif path.path == "/api/original-archive":
                name, raw = workspace.original_zip()
                self.send_data(raw, "application/zip", filename=name)
            elif path.path == "/api/report":
                self.json(workspace.report(), filename="PA800_ANALYSIS_REPORT.json")
            elif path.path == "/api/validators":
                self.json(
                    {
                        "coverage": validator.validator_coverage(),
                        "models": [
                            profile.to_dict()
                            for profile in validator.MODEL_PROFILES.values()
                        ],
                        "fileRules": [
                            rule.to_dict()
                            for rule in validator.FILE_RULES.values()
                        ],
                    }
                )
            elif path.path == "/api/model-matrix":
                extension = params.get("extension", [".STY"])[0][:16]
                source = params.get("source", [""])[0][:64]
                target = params.get("target", [""])[0][:64]
                if source or target:
                    if not source or not target:
                        raise UserError("source i target moraju biti zadani zajedno")
                    cells = [validator.compatibility(source, target, extension)]
                else:
                    cells = validator.compatibility_matrix(extension)
                self.json(
                    {
                        "extension": extension,
                        "cells": [cell.to_dict() for cell in cells],
                    }
                )
            elif path.path == "/api/original":
                index = int(params.get("id", ["-1"])[0])
                item = workspace.get(index)
                self.send_data(item["raw"], "application/octet-stream", filename=Path(item["name"]).name)
            elif path.path == "/api/health":
                self.json(
                    {
                        "ok": True,
                        "service": "pa_tool PA800 Manager",
                        "version": "0.3.0",
                        "mode": "read-only",
                    }
                )
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


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve the browser UI until interrupted."""
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"pa_tool · PA800 native forensics · http://{host}:{port}", flush=True)
    server.serve_forever()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
