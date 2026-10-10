#!/usr/bin/env python3
"""KORG PA Manager — Integrated Web GUI for the full PA toolkit.

This GUI unifies:
  - KORG PA Converter (cross-model STY conversion between PA80/PA1X/
    PA2X/PA500/PA600/PA800/PA3X/PA3XLE/PA4X/PA700/PA900/PA1000)
  - KORG PA Song Optimizer (MIDI/SMF optimization with shared engine)
  - KORG PA Style Optimizer (STY optimization with the same shared engine)
  - PA800 Inspector (full STY structural report)
  - PA800 native repack
  - Model detection
  - PA800 OS resource browser (explore extracted PKG resources)

The shared optimization engine in `korg_pa_optimizer.py` is the single
source of truth: Song and Style optimizers apply identical logic for
velocity curves, articulation, RX/DNC remap, FX routing, and cross
delay. A song optimized with the Song Optimizer can be used as the
basis for a style, and a style optimized with the Style Optimizer can
be played back as a song.
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
import korg_pa_song_optimizer as song_opt
import korg_pa_style_optimizer as sty_opt
import korg_sty_tool as sty
from korg_pa_optimizer import OptimizationSettings, GENRE_TEMPLATES


MAX_UPLOAD = 512 * 1024 * 1024

# PA800 OS resource images (extracted from PKG)
IMAGES_DIR = Path('/home/z/my-project/korg-pa/pa800_os/extracted_images')


def page(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="bs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ margin:0; background:#0a1019; color:#e8eef8; font-family:Segoe UI,Arial,sans-serif; }}
main {{ max-width:1180px; margin:24px auto; padding:0 18px 40px; }}
.card {{ background:#141e2f; border:1px solid #2a3a55; border-radius:14px; padding:22px; box-shadow:0 12px 35px #0005; margin-bottom:20px; }}
h1 {{ margin:0 0 6px; font-size:1.5em; color:#3e82f7; }} h2 {{ margin-top:24px; font-size:1.15em; color:#a4b6e0; }}
.muted {{ color:#8e9bb0; }} .warning {{ background:#3d2528; border:1px solid #9d4d55; padding:12px; border-radius:9px; margin:12px 0; }}
.ok-box {{ background:#1a3a25; border:1px solid #4a9d6a; padding:12px; border-radius:9px; margin:12px 0; }}
label {{ display:block; margin:14px 0 6px; font-weight:600; }}
input[type=file], select, input[type=text] {{ width:100%; box-sizing:border-box; padding:10px; color:#e8eef8; background:#0f1722; border:1px solid #40516a; border-radius:8px; }}
.check {{ font-weight:400; display:flex; gap:9px; align-items:flex-start; margin:8px 0; }}
button,.button {{ display:inline-block; margin-top:18px; padding:11px 18px; border:0; border-radius:8px; background:#3e82f7; color:white; font-weight:700; text-decoration:none; cursor:pointer; }}
.secondary {{ background:#33445c; margin-left:8px; }}
pre {{ white-space:pre-wrap; overflow-wrap:anywhere; background:#0d141e; padding:14px; border-radius:8px; border:1px solid #2b3b52; max-height:480px; overflow-y:auto; font-size:11px; line-height:1.4; }}
.ok {{ color:#80d99b; }} .error {{ color:#ff9292; }}
.row {{ display:flex; gap:12px; flex-wrap:wrap; }}
.col {{ flex:1 1 280px; }}
.tabs {{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:20px; }}
.tab {{ padding:10px 16px; background:#1a2638; border:1px solid #2a3a55; border-radius:8px 8px 0 0; cursor:pointer; text-decoration:none; color:#a4b6e0; font-weight:600; }}
.tab.active {{ background:#3e82f7; color:white; border-color:#3e82f7; }}
.model-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:8px; margin-top:12px; }}
.model-card {{ background:#0f1722; border:1px solid #2a3a55; border-radius:9px; padding:10px 12px; font-size:0.9em; }}
.model-card strong {{ display:block; font-size:1.05em; margin-bottom:4px; color:#3e82f7; }}
.tag {{ display:inline-block; padding:2px 7px; border-radius:5px; background:#1e2a40; color:#9bb0d0; font-size:0.78em; margin-right:4px; }}
.tag.l {{ background:#1e3a25; color:#80d99b; }}
.tag.n {{ background:#3d2528; color:#ff9292; }}
.tag.v4 {{ background:#2d3a5d; color:#a4b6e0; }}
.tag.v5 {{ background:#3a2d5d; color:#c4a4e0; }}
.tag.v2 {{ background:#5d4a2d; color:#e0c4a4; }}
img.thumb {{ max-width:120px; max-height:80px; margin:4px; border:1px solid #2a3a55; border-radius:4px; }}
.image-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:10px; margin-top:14px; }}
.image-tile {{ text-align:center; padding:6px; background:#0f1722; border-radius:6px; }}
.image-tile .name {{ font-size:0.78em; color:#8e9bb0; word-break:break-all; margin-top:4px; }}
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


def tabs_html(active: str, token: str) -> str:
    items = [
        ("Konverter", "convert"),
        ("Song Optimizer", "song"),
        ("Style Optimizer", "style"),
        ("Inspector", "inspect"),
        ("PA800 OS resursi", "resources"),
    ]
    out = ['<div class="tabs">']
    for name, key in items:
        cls = "tab active" if key == active else "tab"
        out.append(f'<a class="{cls}" href="/{token}/{key}">{html.escape(name)}</a>')
    out.append('</div>')
    return "".join(out)


def form_convert(action: str, token: str) -> bytes:
    model_options = "".join(
        f'<option value="{html.escape(name)}">{html.escape(name)}</option>'
        for name in sorted(conv.MODEL_REGISTRY.keys())
    )
    return page("KORG PA Konverter", f"""
<div class="card">
  <h1>KORG PA Konverter</h1>
  <p class="muted">Cross-model konverzija STY fajlova između svih potvrđenih Korg PA modela. Fail-closed: nikada ne izmišlja podatke bez dokaza.</p>
  {tabs_html("convert", token)}
  <div class="warning"><b>Važno:</b> Uvijek napravi backup prije konverzije. Testiraj na instrumentu.</div>
  <form method="post" action="{action}" enctype="multipart/form-data">
    <label>Ulazni STY fajl</label>
    <input type="file" name="sty_file" accept=".sty,.STY" required>
    <div class="row">
      <div class="col">
        <label>Izvorni model (prazno = auto-detekcija)</label>
        <select name="source_model"><option value="">Auto-detekcija</option>{model_options}</select>
      </div>
      <div class="col">
        <label>Ciljni model</label>
        <select name="target_model" required>{model_options}</select>
      </div>
    </div>
    <label class="check"><input type="checkbox" name="expand_element_info" value="1"> Proširi ElementInfo 28 → 44 (za PA4X/PA700 cilj)</label>
    <label class="check"><input type="checkbox" name="allow_lossy" value="1"> Dozvoli lossy režim (odbacuje nepodržane extension vrijednosti)</label>
    <button type="submit">Pokreni konverziju</button>
  </form>
</div>
<div class="card">
  <h2>Podržani Korg PA modeli</h2>
  {model_grid_html()}
</div>""")


def form_song(action: str, token: str) -> bytes:
    genres = "".join(f'<option value="{g}">{g}</option>' for g in GENRE_TEMPLATES.keys())
    targets = "".join(f'<option value="{n}">{n}</option>' for n in sorted(conv.MODEL_REGISTRY.keys()))
    return page("KORG PA Song Optimizer", f"""
<div class="card">
  <h1>KORG PA Song Optimizer</h1>
  <p class="muted">Optimizacija MIDI/SMF fajlova korištenjem shared engine logike. Identična logika kao Style Optimizer.</p>
  {tabs_html("song", token)}
  <form method="post" action="{action}" enctype="multipart/form-data">
    <label>Ulazni MIDI/SMF fajl</label>
    <input type="file" name="midi_file" accept=".mid,.midi,.MID,.MIDI" required>
    <div class="row">
      <div class="col">
        <label>Žanr (prazno = auto-detekcija)</label>
        <select name="genre"><option value="">Auto-detekcija</option>{genres}</select>
      </div>
      <div class="col">
        <label>Ciljni KORG model</label>
        <select name="target_model">{targets}</select>
      </div>
    </div>
    <label class="check"><input type="checkbox" name="velocity_opt" value="1" checked> Velocity optimizacija (kompresija dynamike)</label>
    <label class="check"><input type="checkbox" name="articulation" value="1" checked> Articulation engine (guitar/bass/drum)</label>
    <label class="check"><input type="checkbox" name="rx_dnc" value="1" checked> RX/DNC trigger remap</label>
    <label class="check"><input type="checkbox" name="cross_delay" value="1"> Cross delay echo</label>
    <label class="check"><input type="checkbox" name="fx_routing" value="1" checked> FX routing (A/B/C/D sends)</label>
    <button type="submit">Optimiziraj Song</button>
  </form>
  <h2>Šta optimizator radi</h2>
  <ul>
    <li><b>Velocity kompresija</b> — mapira stvarni opseg u ciljani (default 20-110), sa koljenom na 80</li>
    <li><b>Articulation engine</b> — guitar (beat accent, ±2 varijacija), bass (root note accent), drum (downbeat accent, off-beat ghost)</li>
    <li><b>RX/DNC remap</b> — dodaje CC 80-87 trigger events za RX slojeve</li>
    <li><b>Cross delay</b> — dodaje quiet echoes na 1/8 note, pan u suprotnu stranu</li>
    <li><b>FX routing</b> — preporučuje FX A/B/C/D sends na osnovu žanra i role trake</li>
  </ul>
</div>""")


def form_style(action: str, token: str) -> bytes:
    genres = "".join(f'<option value="{g}">{g}</option>' for g in GENRE_TEMPLATES.keys())
    sources = "".join(f'<option value="{n}">{n}</option>' for n in sorted(conv.MODEL_REGISTRY.keys()))
    targets = "".join(f'<option value="{n}">{n}</option>' for n in sorted(conv.MODEL_REGISTRY.keys()))
    return page("KORG PA Style Optimizer", f"""
<div class="card">
  <h1>KORG PA Style Optimizer</h1>
  <p class="muted">Optimizacija STY fajlova korištenjem shared engine logike. Isti algoritam kao Song Optimizer — samo ulaz je STY umjesto MIDI.</p>
  {tabs_html("style", token)}
  <div class="warning"><b>Napomena:</b> Style Optimizer modifikuje TrackData Expression bajtove u inner STYLE resursima. STS (ID-09) resursi se čuvaju byte-exact (fail-closed za native selector remap).</div>
  <form method="post" action="{action}" enctype="multipart/form-data">
    <label>Ulazni STY fajl</label>
    <input type="file" name="sty_file" accept=".sty,.STY" required>
    <div class="row">
      <div class="col">
        <label>Izvorni model (prazno = auto-detekcija)</label>
        <select name="source_model"><option value="">Auto-detekcija</option>{sources}</select>
      </div>
      <div class="col">
        <label>Ciljni model</label>
        <select name="target_model">{targets}</select>
      </div>
    </div>
    <label>Žanr (prazno = auto-detekcija)</label>
    <select name="genre"><option value="">Auto-detekcija</option>{genres}</select>
    <label class="check"><input type="checkbox" name="velocity_opt" value="1" checked> Velocity optimizacija</label>
    <label class="check"><input type="checkbox" name="articulation" value="1" checked> Articulation engine</label>
    <label class="check"><input type="checkbox" name="rx_dnc" value="1" checked> RX/DNC remap</label>
    <label class="check"><input type="checkbox" name="cross_delay" value="1"> Cross delay</label>
    <label class="check"><input type="checkbox" name="fx_routing" value="1" checked> FX routing</label>
    <button type="submit">Optimiziraj Style</button>
  </form>
</div>""")


def form_inspect(action: str, token: str) -> bytes:
    return page("KORG PA Inspector", f"""
<div class="card">
  <h1>KORG PA Inspector</h1>
  <p class="muted">Strukturalni izvještaj STY fajla. Provjerava container, OC20/OC31/OC32 kompresiju, KBEG/KEND indeks, inner STYLE geometriju i STS layout.</p>
  {tabs_html("inspect", token)}
  <form method="post" action="{action}" enctype="multipart/form-data">
    <label>STY fajl</label>
    <input type="file" name="sty_file" accept=".sty,.STY" required>
    <label class="check"><input type="checkbox" name="repack" value="1"> Također izvrši PA800 native repack</label>
    <button type="submit">Inspect</button>
  </form>
</div>""")


def page_resources(token: str) -> bytes:
    # List extracted PA800 OS images
    image_html = ""
    if IMAGES_DIR.exists():
        images = sorted(IMAGES_DIR.glob('*.jpg'), key=lambda p: -p.stat().st_size)[:30]
        for img in images:
            name = html.escape(img.name)
            size_kb = img.stat().st_size / 1024
            image_html += (
                f'<div class="image-tile">'
                f'<img class="thumb" src="/{token}/img/{name}" alt="{name}">'
                f'<div class="name">{name}<br>{size_kb:.1f} KB</div>'
                f'</div>'
            )
    return page("PA800 OS resursi", f"""
<div class="card">
  <h1>PA800 OS resursi</h1>
  <p class="muted">Ekstraktovani resursi iz oficijelnog KORG PA800 OS v2.02 PKG-a (134 MB). Sadrži 746 OC20 stranica, 94 logička fajla, 69 JPEG slika (ikone, wallpaper).</p>
  {tabs_html("resources", token)}
  <h2>Ekstraktovane slike (ikone, wallpaper)</h2>
  <div class="image-grid">{image_html}</div>
  <h2>Struktura PKG-a</h2>
  <ul>
    <li><b>746 OC20 stranica</b> — CBarc kompresovane, dekompresovane u 150 MB</li>
    <li><b>94 logička fajla</b> — KORF resource records sa KBEG/KEND indeksom</li>
    <li><b>13 PCG/KMP fajlova</b> — instrument definicije sa varijantama</li>
    <li><b>3 STY banke</b> — 148 ukupno stilova</li>
    <li><b>3 drum kit fajla</b> — sa layer mapiranjem</li>
    <li><b>69 JPEG slika</b> — UI ikone, wallpaper, knob/slider grafički elementi</li>
  </ul>
  <h2>Dostupni podaci o instrumentima</h2>
  <p>PCG banke uključuju Acoustic Piano GM (varijante: Wide, Dark, Grand, Classic, Jazz), Finger Bass DNC, i druge. Svaki multi-sample sadrži osc, velocity layer i FX routing parametre.</p>
  <h2>Dostupni drum kitovi</h2>
  <p>Pop Std. Kit RX (120.0.4) — dokumentovani velocity layer switches za snare (80), open hat (90), toms (85), crash (80), ride (80).<br>
  Jazz Kit — lower thresholds za jazz snare (70), crash (75).</p>
</div>""")


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, str], str, bytes]:
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    )
    fields: dict[str, str] = {}
    filename = "INPUT"
    upload = b""
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        if part.get_filename():
            uploaded_name = (part.get_filename() or filename).replace("\\", "/")
            filename = Path(uploaded_name).name
            upload = payload
        else:
            fields[name] = payload.decode(part.get_content_charset() or "utf-8", "replace")
    return fields, filename, upload


def render_summary(report: dict) -> str:
    if "converted_style_count" in report:
        return (
            f"Konverzija {report.get('source_model')} → {report.get('target_model')}: "
            f"STYLE {report.get('converted_style_count', 0)} konvertovano / "
            f"{report.get('preserved_style_count', 0)} očuvano; "
            f"STS {report.get('converted_sts_count', 0)} / "
            f"{report.get('preserved_sts_count', 0)}; "
            f"errors: {report.get('decode_error_count', 0)}."
        )
    if "style_resources" in report:
        opt = sum(1 for s in report.get("styles", []) if s.get("status") == "optimized")
        pres = sum(1 for s in report.get("styles", []) if s.get("status") == "preserved")
        return (
            f"Style optimizacija: {opt} resursa optimizirano, {pres} očuvano. "
            f"Žanr: {report.get('genre', 'n/a')}."
        )
    if "track_profiles" in report:
        return (
            f"Song optimizacija: {len(report['track_profiles'])} traka, "
            f"žanr: {report.get('genre', 'n/a')}, "
            f"{len(report.get('applied_optimizations', []))} optimizacija primijenjeno."
        )
    if "decoded_resource_count" in report:
        return (
            f"Inspect: {report.get('decoded_resource_count', 0)}/"
            f"{report.get('resource_count', 0)} resursa dekodirano; "
            f"greške: {report.get('decode_error_count', 0)}."
        )
    return "Operacija završena."


def create_server(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    token = secrets.token_urlsafe(18)
    storage = Path(tempfile.mkdtemp(prefix="korg_pa_manager_"))
    downloads: dict[str, tuple[Path, str, str]] = {}
    base = f"/{token}"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            pass  # silence

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
                self.send_header("Location", f"{base}/convert")
                self.end_headers()
                return
            if parsed.path == f"{base}/convert":
                self.send_html(200, form_convert(f"{base}/run/convert", token))
                return
            if parsed.path == f"{base}/song":
                self.send_html(200, form_song(f"{base}/run/song", token))
                return
            if parsed.path == f"{base}/style":
                self.send_html(200, form_style(f"{base}/run/style", token))
                return
            if parsed.path == f"{base}/inspect":
                self.send_html(200, form_inspect(f"{base}/run/inspect", token))
                return
            if parsed.path == f"{base}/resources":
                self.send_html(200, page_resources(token))
                return
            img_prefix = f"{base}/img/"
            if parsed.path.startswith(img_prefix):
                name = parsed.path[len(img_prefix):]
                img_path = IMAGES_DIR / name
                if not img_path.exists() or not img_path.is_file():
                    self.send_error(404)
                    return
                data = img_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(data)
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
            path = urlparse(self.path).path
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_UPLOAD:
                    raise ValueError("Neispravna ili prevelika upload veličina")
                fields, filename, uploaded = parse_multipart(
                    self.headers.get("Content-Type", ""),
                    self.rfile.read(length),
                )
                job = storage / secrets.token_hex(8)
                job.mkdir()
                # Determine operation by path
                if path == f"{base}/run/convert":
                    result = self._handle_convert(fields, filename, uploaded, job)
                elif path == f"{base}/run/song":
                    result = self._handle_song(fields, filename, uploaded, job)
                elif path == f"{base}/run/style":
                    result = self._handle_style(fields, filename, uploaded, job)
                elif path == f"{base}/run/inspect":
                    result = self._handle_inspect(fields, filename, uploaded, job)
                else:
                    raise ValueError(f"nepoznata akcija: {path}")
                self._render_result(result, job)
            except Exception as error:
                content = page("Greška", f"""
<div class="card"><h1 class="error">Operacija nije izvršena</h1>
<p>{html.escape(str(error))}</p>
<a class="button secondary" href="{base}">Nazad</a></div>""")
                self.send_html(400, content)

        def _handle_convert(self, fields, filename, uploaded, job):
            source_path = job / filename
            source_path.write_bytes(uploaded)
            source_model = fields.get("source_model") or None
            target_model = fields.get("target_model")
            if not target_model:
                raise ValueError("Ciljni model nije izabran")
            if not source_model:
                detection = conv.detect_source_model(uploaded)
                matches = detection.get("matching_models", [])
                if not matches:
                    raise ValueError("Auto-detekcija nije uspjela. Izaberi izvorni model ručno.")
                source_model = matches[0]
            converted, report = conv.convert_sty_cross_model(
                uploaded, source_model, target_model,
                allow_lossy=fields.get("allow_lossy") == "1",
                expand_element_info=fields.get("expand_element_info") == "1",
            )
            output = job / f"{Path(filename).stem}_{target_model}.STY"
            output.write_bytes(converted)
            report_path = job / f"{Path(filename).stem}_convert_report.json"
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            return {
                "summary": render_summary({"**placeholder**": 0, **report}),
                "downloads": [(output, "application/octet-stream", output.name),
                              (report_path, "application/json", report_path.name)],
                "report_preview": self._build_preview(report, kind="convert"),
            }

        def _handle_song(self, fields, filename, uploaded, job):
            midi_path = job / filename
            midi_path.write_bytes(uploaded)
            settings = OptimizationSettings(
                velocity_optimization=fields.get("velocity_opt") == "1",
                articulation_enabled=fields.get("articulation") == "1",
                rx_dnc_enabled=fields.get("rx_dnc") == "1",
                cross_delay_enabled=fields.get("cross_delay") == "1",
                fx_routing_enabled=fields.get("fx_routing") == "1",
                genre_override=fields.get("genre") or None,
                target_model=fields.get("target_model", "PA800"),
            )
            output = job / f"{Path(filename).stem}_optimized.mid"
            report_path = job / f"{Path(filename).stem}_optimization_report.json"
            result = song_opt.optimize_midi_file(midi_path, output, report_path, settings)
            return {
                "summary": render_summary(result),
                "downloads": [(output, "audio/midi", output.name),
                              (report_path, "application/json", report_path.name)],
                "report_preview": self._build_preview(result, kind="song"),
            }

        def _handle_style(self, fields, filename, uploaded, job):
            sty_path = job / filename
            sty_path.write_bytes(uploaded)
            settings = OptimizationSettings(
                velocity_optimization=fields.get("velocity_opt") == "1",
                articulation_enabled=fields.get("articulation") == "1",
                rx_dnc_enabled=fields.get("rx_dnc") == "1",
                cross_delay_enabled=fields.get("cross_delay") == "1",
                fx_routing_enabled=fields.get("fx_routing") == "1",
                genre_override=fields.get("genre") or None,
                target_model=fields.get("target_model", "PA800"),
            )
            source_model = fields.get("source_model") or None
            target_model = fields.get("target_model", "PA800")
            output = job / f"{Path(filename).stem}_optimized.STY"
            report_path = job / f"{Path(filename).stem}_style_report.json"
            result = sty_opt.optimize_sty_file(
                sty_path, output, report_path, settings, source_model, target_model
            )
            return {
                "summary": render_summary(result),
                "downloads": [(output, "application/octet-stream", output.name),
                              (report_path, "application/json", report_path.name)],
                "report_preview": self._build_preview(result, kind="style"),
            }

        def _handle_inspect(self, fields, filename, uploaded, job):
            sty_path = job / filename
            sty_path.write_bytes(uploaded)
            inspection = sty.inspect_bytes(uploaded, filename)
            report_path = job / f"{Path(filename).stem}_inspect.json"
            report_path.write_text(json.dumps(inspection, indent=2, ensure_ascii=False, default=str))
            downloads = [(report_path, "application/json", report_path.name)]
            if fields.get("repack") == "1":
                try:
                    packed, repack_report = sty.repack_bytes(uploaded)
                    output = job / f"{Path(filename).stem}_PA800_NATIVE.STY"
                    output.write_bytes(packed)
                    repack_report_path = job / f"{Path(filename).stem}_repack_report.json"
                    repack_report_path.write_text(json.dumps(repack_report, indent=2, ensure_ascii=False, default=str))
                    downloads.append((output, "application/octet-stream", output.name))
                    downloads.append((repack_report_path, "application/json", repack_report_path.name))
                except sty.StyError as e:
                    inspection["repack_error"] = str(e)
            return {
                "summary": render_summary(inspection),
                "downloads": downloads,
                "report_preview": self._build_preview(inspection, kind="inspect"),
            }

        def _build_preview(self, report, kind="convert"):
            """Build a compact preview dict for the HTML summary."""
            preview = {}
            keys_to_copy = {
                "convert": ["source_model", "target_model", "converted_style_count",
                            "preserved_style_count", "converted_sts_count",
                            "preserved_sts_count", "decode_error_count",
                            "all_converted_resources_verified_round_trip"],
                "song": ["genre", "track_profiles", "applied_optimizations"],
                "style": ["source_model", "target_model", "style_resources",
                          "sts_resources", "styles"],
                "inspect": ["resource_count", "decoded_resource_count",
                            "decode_error_count", "input_container"],
            }.get(kind, [])
            for k in keys_to_copy:
                if k in report:
                    val = report[k]
                    if isinstance(val, list) and len(val) > 8:
                        preview[k] = val[:8] + [f"... +{len(val)-8} more"]
                    else:
                        preview[k] = val
            return preview

        def _render_result(self, result, job):
            links = ""
            for path, content_type, fname in result.get("downloads", []):
                key = secrets.token_urlsafe(12)
                downloads[key] = (path, content_type, fname)
                label = "Preuzmi STY" if fname.endswith(".STY") else (
                    "Preuzmi MIDI" if fname.endswith(".mid") else "Preuzmi JSON izvještaj"
                )
                links += f'<a class="button" href="{base}/download/{key}">{label}</a>'
            preview = result.get("report_preview", {})
            preview_json = json.dumps(preview, indent=2, ensure_ascii=False, default=str)
            content = page("Završeno", f"""
<div class="card">
  <h1 class="ok">Završeno</h1>
  <div class="ok-box">{html.escape(result["summary"])}</div>
  <div style="margin-top:14px;">{links}</div>
  <a class="button secondary" href="{base}">Nazad</a>
  <h2>Sažetak izvještaja</h2>
  <pre>{html.escape(preview_json[:8000])}</pre>
  <p class="muted">Kompletan izvještaj je dostupan u JSON fajlu.</p>
</div>""")
            self.send_html(200, content)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_port}{base}/convert"
    return server, url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server, url = create_server(args.port)
    print(f"KORG PA Manager GUI: {url}")
    print("Za prekid pritisni Ctrl+C.")
    if not args.no_browser:
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
