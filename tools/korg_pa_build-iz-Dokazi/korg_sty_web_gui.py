#!/usr/bin/env python3
"""Local browser GUI for the verified KORG STY engine."""

from __future__ import annotations

import argparse
import html
import json
import secrets
import tempfile
import threading
import webbrowser
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

import korg_sty_gui as gui


MAX_UPLOAD = 512 * 1024 * 1024


def page(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="bs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ margin:0; background:#101722; color:#e8eef8; font-family:Segoe UI,Arial,sans-serif; }}
main {{ max-width:900px; margin:32px auto; padding:0 18px 40px; }}
.card {{ background:#182334; border:1px solid #2b3b52; border-radius:14px; padding:22px; box-shadow:0 12px 35px #0005; }}
h1 {{ margin:0 0 6px; }} h2 {{ margin-top:24px; }}
.muted {{ color:#aebbd0; }} .warning {{ background:#3d2528; border:1px solid #9d4d55; padding:12px; border-radius:9px; }}
label {{ display:block; margin:15px 0 6px; font-weight:600; }}
input[type=file], select {{ width:100%; box-sizing:border-box; padding:11px; color:#e8eef8; background:#0f1722; border:1px solid #40516a; border-radius:8px; }}
.check {{ font-weight:400; display:flex; gap:9px; align-items:flex-start; }}
button,.button {{ display:inline-block; margin-top:18px; padding:11px 18px; border:0; border-radius:8px; background:#3e82f7; color:white; font-weight:700; text-decoration:none; cursor:pointer; }}
.secondary {{ background:#33445c; margin-left:8px; }}
pre {{ white-space:pre-wrap; overflow-wrap:anywhere; background:#0d141e; padding:14px; border-radius:8px; border:1px solid #2b3b52; }}
.ok {{ color:#80d99b; }} .error {{ color:#ff9292; }}
</style>
</head>
<body><main>{body}</main></body></html>""".encode("utf-8")


def form_html(action: str) -> bytes:
    options = "".join(
        f'<option value="{html.escape(value)}">{html.escape(value)}</option>'
        for value in gui.OPERATIONS
    )
    return page("KORG PA800 STY Tool", f"""
<div class="card">
  <h1>KORG PA800 STY Tool</h1>
  <p class="muted">Lokalni browser GUI. Fajl se obrađuje samo na ovom računaru.</p>
  <div class="warning"><b>Važno:</b> prvo napravi backup. PA800 native repack je potvrđena operacija. STYLE v4/v5 konverzija ne prevodi kompletan međumodelski ID-09/STS.</div>
  <form method="post" action="{action}" enctype="multipart/form-data">
    <label>Ulazni STY</label>
    <input type="file" name="sty_file" accept=".sty,.STY" required>
    <label>Operacija</label>
    <select name="operation">{options}</select>
    <label class="check"><input type="checkbox" name="expand_element_info" value="1"> Proširi ElementInfo 28 → 44 canonical nastavkom</label>
    <label class="check"><input type="checkbox" name="allow_lossy" value="1"> Dozvoli lossy režim (nije preporučeno)</label>
    <button type="submit">Pokreni</button>
  </form>
  <h2>Preporuka za PA800</h2>
  <ol><li>Pregledaj / validiraj.</li><li>Ako nema grešaka, pokreni PA800 native repack.</li><li>Preuzmi STY i JSON izvještaj.</li><li>Testiraj uz originalni backup.</li></ol>
</div>""")


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], str, bytes]:
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
        + body
    )
    fields: dict[str, str] = {}
    filename = "INPUT.STY"
    upload = b""
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        if name == "sty_file":
            uploaded_name = (part.get_filename() or filename).replace("\\", "/")
            filename = Path(uploaded_name).name
            upload = payload
        else:
            fields[name] = payload.decode(part.get_content_charset() or "utf-8", "replace")
    if not upload:
        raise ValueError("STY fajl nije uploadovan ili je prazan")
    return fields, filename, upload


def create_server(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    token = secrets.token_urlsafe(18)
    storage = Path(tempfile.mkdtemp(prefix="korg_sty_web_gui_"))
    downloads: dict[str, tuple[Path, str, str]] = {}
    base = f"/{token}"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            print("KORG GUI:", format % args)

        def send_html(self, status: int, content: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.send_response(302)
                self.send_header("Location", base)
                self.end_headers()
                return
            if parsed.path == base:
                self.send_html(200, form_html(f"{base}/run"))
                return
            prefix = f"{base}/download/"
            if parsed.path.startswith(prefix):
                key = parsed.path[len(prefix):]
                item = downloads.get(key)
                if item is None:
                    self.send_error(404)
                    return
                path, content_type, filename = item
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header(
                    "Content-Disposition",
                    f"attachment; filename*=UTF-8''{quote(filename)}",
                )
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_error(404)

        def do_POST(self) -> None:
            if urlparse(self.path).path != f"{base}/run":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_UPLOAD:
                    raise ValueError("Neispravna ili prevelika upload veličina")
                fields, filename, uploaded = parse_multipart(
                    self.headers.get("Content-Type", ""),
                    self.rfile.read(length),
                )
                operation = fields.get("operation", gui.OP_INSPECT)
                job = storage / secrets.token_hex(8)
                job.mkdir()
                source = job / filename
                source.write_bytes(uploaded)
                output, report = gui.default_paths(source, operation)
                result, summary = gui.execute_operation(
                    operation,
                    source,
                    output,
                    report,
                    allow_lossy=fields.get("allow_lossy") == "1",
                    expand_element_info=fields.get("expand_element_info") == "1",
                )
                report_key = secrets.token_urlsafe(12)
                downloads[report_key] = (report, "application/json", report.name)
                links = f'<a class="button" href="{base}/download/{report_key}">Preuzmi JSON izvještaj</a>'
                if output is not None:
                    output_key = secrets.token_urlsafe(12)
                    downloads[output_key] = (output, "application/octet-stream", output.name)
                    links = f'<a class="button" href="{base}/download/{output_key}">Preuzmi izlazni STY</a>' + links
                content = page("Završeno", f"""
<div class="card"><h1 class="ok">Završeno</h1>
<p>{html.escape(summary)}</p>{links}
<a class="button secondary" href="{base}">Nova operacija</a>
<h2>Sažetak</h2><pre>{html.escape(json.dumps(result, indent=2, ensure_ascii=False)[:12000])}</pre></div>""")
                self.send_html(200, content)
            except Exception as error:
                content = page("Greška", f"""
<div class="card"><h1 class="error">Operacija nije izvršena</h1>
<p>{html.escape(str(error))}</p>
<a class="button secondary" href="{base}">Nazad</a></div>""")
                self.send_html(400, content)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_port}{base}"
    return server, url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    arguments = parser.parse_args()
    server, url = create_server(arguments.port)
    print(f"KORG STY GUI: {url}")
    print("Za prekid pritisni Ctrl+C.")
    if not arguments.no_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())