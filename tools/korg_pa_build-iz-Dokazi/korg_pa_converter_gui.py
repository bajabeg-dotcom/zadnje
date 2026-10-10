#!/usr/bin/env python3
"""Local browser GUI for the KORG PA cross-model converter.

This GUI wraps the proven, evidence-based conversion primitives in
``korg_pa_converter`` and the original ``korg_sty_tool`` engine. It runs
as a local HTTP server bound to 127.0.0.1 only — STY files are processed
on the user's machine and never uploaded to any external service.

Supported operations:
  * Inspect / validate  — full structural report for any KORG PA STY file.
  * Detect model        — auto-detect the source KORG PA model profile.
  * PA800 native repack — proven lossless PA800 repack.
  * Cross-model convert — convert a STY between two KORG PA model profiles.

The cross-model converter is fail-closed by default. Resources whose
inner STYLE geometry cannot be safely resized (e.g. v2 -> v4 missing-byte
synthesis) or whose STS layout cannot be safely translated (e.g. native
selector remap) are preserved byte-exact with an explicit warning in the
report. Pass ``--allow-lossy`` to override the safe downgrade gates.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import secrets
import tempfile
import threading
import webbrowser
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

import korg_pa_converter as conv
import korg_sty_tool as sty
import korg_sty_gui as gui


MAX_UPLOAD = 512 * 1024 * 1024

OP_INSPECT = "Pregledaj / validiraj"
OP_DETECT = "Detekcija modela"
OP_REPACK = "PA800 native repack"
OP_CONVERT = "Međumodelska konverzija"
OPERATIONS = (OP_INSPECT, OP_DETECT, OP_REPACK, OP_CONVERT)


def page(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="bs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ margin:0; background:#0e1521; color:#e8eef8; font-family:Segoe UI,Arial,sans-serif; }}
main {{ max-width:980px; margin:32px auto; padding:0 18px 40px; }}
.card {{ background:#16202f; border:1px solid #2a3a55; border-radius:14px; padding:24px; box-shadow:0 12px 35px #0005; margin-bottom:20px; }}
h1 {{ margin:0 0 6px; font-size:1.5em; }} h2 {{ margin-top:28px; font-size:1.15em; }}
.muted {{ color:#aebbd0; }} .warning {{ background:#3d2528; border:1px solid #9d4d55; padding:14px; border-radius:9px; }}
.ok-box {{ background:#1a3a25; border:1px solid #4a9d6a; padding:14px; border-radius:9px; }}
label {{ display:block; margin:15px 0 6px; font-weight:600; }}
input[type=file], select {{ width:100%; box-sizing:border-box; padding:11px; color:#e8eef8; background:#0f1722; border:1px solid #40516a; border-radius:8px; }}
.check {{ font-weight:400; display:flex; gap:9px; align-items:flex-start; }}
button,.button {{ display:inline-block; margin-top:18px; padding:11px 18px; border:0; border-radius:8px; background:#3e82f7; color:white; font-weight:700; text-decoration:none; cursor:pointer; }}
.secondary {{ background:#33445c; margin-left:8px; }}
pre {{ white-space:pre-wrap; overflow-wrap:anywhere; background:#0d141e; padding:14px; border-radius:8px; border:1px solid #2b3b52; max-height:560px; overflow-y:auto; }}
.ok {{ color:#80d99b; }} .error {{ color:#ff9292; }}
.row {{ display:flex; gap:12px; flex-wrap:wrap; }}
.col {{ flex:1 1 280px; }}
.model-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(170px,1fr)); gap:10px; margin-top:12px; }}
.model-card {{ background:#0f1722; border:1px solid #2a3a55; border-radius:9px; padding:10px 12px; font-size:0.92em; }}
.model-card strong {{ display:block; font-size:1.05em; margin-bottom:4px; }}
.tag {{ display:inline-block; padding:2px 7px; border-radius:5px; background:#1e2a40; color:#9bb0d0; font-size:0.8em; margin-right:4px; }}
.tag.l {{ background:#1e3a25; color:#80d99b; }}
.tag.n {{ background:#3d2528; color:#ff9292; }}
.tag.v4 {{ background:#2d3a5d; color:#a4b6e0; }}
.tag.v5 {{ background:#3a2d5d; color:#c4a4e0; }}
.tag.v2 {{ background:#5d4a2d; color:#e0c4a4; }}
</style>
</head>
<body><main>{body}</main></body></html>""".encode("utf-8")


def model_grid_html() -> str:
    cards = []
    for name in sorted(conv.MODEL_REGISTRY.keys()):
        p = conv.MODEL_REGISTRY[name]
        vclass = f"v{p.inner_version}"
        lclass = "l" if p.lineage == "legacy" else "n"
        cards.append(
            f'<div class="model-card">'
            f'<strong>{html.escape(name)}</strong>'
            f'<span class="tag {vclass}">v{p.inner_version}</span> '
            f'<span class="tag {lclass}">{html.escape(p.lineage)}</span><br>'
            f'<span class="muted">EI {p.element_info_length} · TD {p.track_data_length} · STS {html.escape(p.sts_layout)}</span>'
            f'</div>'
        )
    return '<div class="model-grid">' + "".join(cards) + '</div>'


def form_html(action: str) -> bytes:
    options = "".join(
        f'<option value="{html.escape(value)}">{html.escape(value)}</option>'
        for value in OPERATIONS
    )
    model_options = "".join(
        f'<option value="{html.escape(name)}">{html.escape(name)}</option>'
        for name in sorted(conv.MODEL_REGISTRY.keys())
    )
    return page("KORG PA Konverter", f"""
<div class="card">
  <h1>KORG PA Konverter</h1>
  <p class="muted">Lokalni browser GUI. Fajl se obrađuje samo na ovom računaru. Ništa se ne šalje na internet.</p>
  <div class="warning"><b>Važno:</b> prvo napravi backup. Cross-model konverzija je fail-closed: resursi koji ne mogu biti sigurno transformisani se čuvaju byte-exact uz eksplicitno upozorenje.</div>
  <form method="post" action="{action}" enctype="multipart/form-data">
    <label>Ulazni STY fajl</label>
    <input type="file" name="sty_file" accept=".sty,.STY" required>
    <label>Operacija</label>
    <select name="operation" id="op">{options}</select>
    <div id="convert-options" style="display:none;">
      <div class="row">
        <div class="col">
          <label>Izvorni model (ako nije izabran, detekcija se koristi)</label>
          <select name="source_model"><option value="">Auto-detekcija</option>{model_options}</select>
        </div>
        <div class="col">
          <label>Ciljni model</label>
          <select name="target_model" required>{model_options}</select>
        </div>
      </div>
      <label class="check"><input type="checkbox" name="expand_element_info" value="1"> Proširi ElementInfo 28 → 44 canonical nastavkom (za PA4X/PA700 cilj)</label>
      <label class="check"><input type="checkbox" name="allow_lossy" value="1"> Dozvoli lossy režim (odbacuje nepodržane extension vrijednosti pri downgradeu)</label>
    </div>
    <button type="submit">Pokreni</button>
  </form>
  <script>
    const op = document.getElementById('op');
    const convOpts = document.getElementById('convert-options');
    function refresh() {{ convOpts.style.display = (op.value === '{OP_CONVERT}') ? 'block' : 'none'; }}
    op.addEventListener('change', refresh); refresh();
  </script>
</div>
<div class="card">
  <h2>Podržani Korg PA modeli</h2>
  <p class="muted">Format profili izvedeni iz cross-evidence triangulation dokaza i korpus multi-model inspecta.</p>
  {model_grid_html()}
</div>
<div class="card">
  <h2>Preporuka</h2>
  <ol>
    <li><b>Pregledaj / validiraj</b> — potvrdi da je STY fajl ispravan.</li>
    <li><b>Detekcija modela</b> — provjeri koji Korg PA model odgovara tvom fajlu.</li>
    <li><b>PA800 native repack</b> — za PA800 fajlove, gubitak-nula repack provjera.</li>
    <li><b>Međumodelska konverzija</b> — izaberi ciljni model i konvertuj. Uvijek testiraj na instrumentu uz backup.</li>
  </ol>
</div>""")


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], str, bytes]:
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
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


def render_summary(report: dict) -> str:
    if report.get("operation") == "inspect":
        return (
            f"Pregled: {report.get('decoded_resource_count', 0)}/"
            f"{report.get('resource_count', 0)} resursa dekodirano; "
            f"greške: {report.get('decode_error_count', 0)}."
        )
    if report.get("operation") == "detect":
        matches = report.get("matching_models", [])
        return (
            f"Detekcija: izvorni model je v{report.get('detected_inner_version')} "
            f"({report.get('detected_geometry')}), STS={report.get('detected_sts_layout')}; "
            f"matching_models={matches}."
        )
    if report.get("operation") == "repack":
        return f"PA800 native repack: {report.get('semantic_resource_count', 0)} resursa očuvano."
    if report.get("operation") == "convert":
        return (
            f"Konverzija {report.get('source_model')} → {report.get('target_model')}: "
            f"STYLE {report.get('converted_style_count', 0)} konvertovano / "
            f"{report.get('preserved_style_count', 0)} očuvano; "
            f"STS {report.get('converted_sts_count', 0)} konvertovano / "
            f"{report.get('preserved_sts_count', 0)} očuvano; "
            f"decode errors: {report.get('decode_error_count', 0)}."
        )
    return "Operacija završena."


def create_server(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    token = secrets.token_urlsafe(18)
    storage = Path(tempfile.mkdtemp(prefix="korg_pa_gui_"))
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
                operation = fields.get("operation", OP_INSPECT)
                job = storage / secrets.token_hex(8)
                job.mkdir()
                source = job / filename
                source.write_bytes(uploaded)
                raw = uploaded

                report: dict
                output: Path | None = None
                report_path = job / f"{Path(filename).stem}.report.json"

                if operation == OP_INSPECT:
                    inspection = sty.inspect_bytes(raw, filename)
                    report = {"operation": "inspect", **inspection}
                elif operation == OP_DETECT:
                    detection = conv.detect_source_model(raw)
                    report = {"operation": "detect", **detection}
                elif operation == OP_REPACK:
                    packed, repack_report = sty.repack_bytes(raw)
                    output = job / f"{Path(filename).stem}_PA800_NATIVE.STY"
                    output.write_bytes(packed)
                    report = {"operation": "repack", **repack_report}
                elif operation == OP_CONVERT:
                    source_model = fields.get("source_model") or None
                    target_model = fields.get("target_model")
                    if not target_model:
                        raise ValueError("Ciljni model nije izabran")
                    if not source_model:
                        # Auto-detect source model
                        detection = conv.detect_source_model(raw)
                        matches = detection.get("matching_models", [])
                        if not matches:
                            raise ValueError(
                                "Auto-detekcija nije uspjela. Izaberi izvorni model ručno."
                            )
                        source_model = matches[0]
                    converted, convert_report = conv.convert_sty_cross_model(
                        raw,
                        source_model,
                        target_model,
                        allow_lossy=fields.get("allow_lossy") == "1",
                        expand_element_info=fields.get("expand_element_info") == "1",
                    )
                    output = job / f"{Path(filename).stem}_{target_model}.STY"
                    output.write_bytes(converted)
                    report = {"operation": "convert", **convert_report}
                else:
                    raise ValueError(f"Nepoznata operacija: {operation}")

                report_path.write_text(
                    json.dumps(report, indent=2, ensure_ascii=False, default=str)
                )

                summary = render_summary(report)
                report_key = secrets.token_urlsafe(12)
                downloads[report_key] = (report_path, "application/json", report_path.name)
                links = (
                    f'<a class="button" href="{base}/download/{report_key}">Preuzmi JSON izvještaj</a>'
                )
                if output is not None:
                    output_key = secrets.token_urlsafe(12)
                    downloads[output_key] = (output, "application/octet-stream", output.name)
                    links = (
                        f'<a class="button" href="{base}/download/{output_key}">Preuzmi izlazni STY</a>'
                        + links
                    )
                # Build a focused report preview
                preview = {}
                for k in (
                    "operation", "source_model", "target_model",
                    "converted_style_count", "preserved_style_count",
                    "converted_sts_count", "preserved_sts_count",
                    "decode_error_count", "all_converted_resources_verified_round_trip",
                    "matching_models", "detected_inner_version", "detected_geometry",
                    "detected_sts_layout", "semantic_resource_count",
                    "decoded_resource_count", "resource_count",
                ):
                    if k in report:
                        preview[k] = report[k]
                # Sample warnings
                warnings_sample: list[str] = []
                for s in report.get("styles", [])[:3]:
                    for w in s.get("warnings", [])[:2]:
                        warnings_sample.append(f"STYLE@{s.get('resource_position')}: {w}")
                for s in report.get("style_performances", [])[:3]:
                    for w in s.get("warnings", [])[:1]:
                        warnings_sample.append(f"STS@{s.get('resource_position')}: {w}")
                if warnings_sample:
                    preview["warnings_sample"] = warnings_sample

                content = page("Završeno", f"""
<div class="card">
  <h1 class="ok">Završeno</h1>
  <div class="ok-box">{html.escape(summary)}</div>
  <div style="margin-top:14px;">{links}</div>
  <a class="button secondary" href="{base}">Nova operacija</a>
  <h2>Sažetak izvještaja</h2>
  <pre>{html.escape(json.dumps(preview, indent=2, ensure_ascii=False, default=str))}</pre>
  <p class="muted">Kompletan izvještaj je dostupan u JSON fajlu iznad.</p>
</div>""")
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
    print(f"KORG PA Konverter GUI: {url}")
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
