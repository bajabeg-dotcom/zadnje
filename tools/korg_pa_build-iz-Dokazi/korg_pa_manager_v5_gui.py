#!/usr/bin/env python3
"""KORG PA Manager v5 — Integrirani Web GUI sa svim V3 funkcijama.

Sadrži 10 tabova:
  1. Konverter (cross-model STY)
  2. Song Optimizer (MIDI/SMF)
  3. Style Optimizer (STY)
  4. Inspector (STY structural report)
  5. Sound Browser (PA800 factory zvukovi sa pretragom)
  6. Drum Kit Editor (key map, velocity layers)
  7. Style Editor (track sounds, mixer)
  8. STS/Performance Editor (4 keyboard tracks)
  9. Sound ReMap (Find & Replace)
  10. Sample Tools (Stereo->Mono, 8bit->16bit, WAV export)
  11. SET Report (HTML/JSON/CSV)
  12. PA800 OS resursi (ikone, wallpaper)

Implementira sve V3 KORGPAManager.exe funkcije:
  - Convert, repack, inspect, diff, evidence-audit (korg_sty_tool)
  - Cross-model konverzija (korg_pa_converter)
  - Song/Style optimization (shared engine)
  - reMapSTY, reMapSTS, reMapPAD, reMapPERF (korg_pa_editors)
  - Paste_Sound, Drum Kit editor, Style track editor
  - NewSound (template), GetSoundInfo (lookup)
  - mnuExportSample, mnuExportDK (WAV export)
  - convert_8bit_to_16bit (potvrđena formula (b-128)*256)
  - convert_stereo_to_mono16 (potvrđena formula trunc_toward_zero)
  - mnuCompDuration, mnuDelUnused, mnuDelAll (sample filter)
  - cmdReport (SET report HTML/JSON/CSV)
  - mnuSize, Sound_Size_Menu (sample size check)
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
import korg_pa_database as db_mod
import korg_pa_editors as editors
import korg_pa_sample_tools as sample_tools
import korg_pa_set_report as set_report
import korg_sty_tool as sty
from korg_pa_optimizer import OptimizationSettings, GENRE_TEMPLATES


MAX_UPLOAD = 512 * 1024 * 1024
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
main {{ max-width:1280px; margin:24px auto; padding:0 18px 40px; }}
.card {{ background:#141e2f; border:1px solid #2a3a55; border-radius:14px; padding:22px; box-shadow:0 12px 35px #0005; margin-bottom:20px; }}
h1 {{ margin:0 0 6px; font-size:1.5em; color:#3e82f7; }} h2 {{ margin-top:24px; font-size:1.15em; color:#a4b6e0; }}
h3 {{ margin-top:18px; color:#80d99b; }}
.muted {{ color:#8e9bb0; }} .warning {{ background:#3d2528; border:1px solid #9d4d55; padding:12px; border-radius:9px; margin:12px 0; }}
.ok-box {{ background:#1a3a25; border:1px solid #4a9d6a; padding:12px; border-radius:9px; margin:12px 0; }}
label {{ display:block; margin:14px 0 6px; font-weight:600; }}
input[type=file], select, input[type=text], input[type=number], textarea {{ width:100%; box-sizing:border-box; padding:10px; color:#e8eef8; background:#0f1722; border:1px solid #40516a; border-radius:8px; }}
.check {{ font-weight:400; display:flex; gap:9px; align-items:flex-start; margin:8px 0; }}
button,.button {{ display:inline-block; margin-top:18px; padding:11px 18px; border:0; border-radius:8px; background:#3e82f7; color:white; font-weight:700; text-decoration:none; cursor:pointer; }}
.secondary {{ background:#33445c; margin-left:8px; }}
pre {{ white-space:pre-wrap; overflow-wrap:anywhere; background:#0d141e; padding:14px; border-radius:8px; border:1px solid #2b3b52; max-height:480px; overflow-y:auto; font-size:11px; line-height:1.4; }}
.ok {{ color:#80d99b; }} .error {{ color:#ff9292; }}
.row {{ display:flex; gap:12px; flex-wrap:wrap; }} .col {{ flex:1 1 280px; }}
.tabs {{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:20px; }}
.tab {{ padding:10px 16px; background:#1a2638; border:1px solid #2a3a55; border-radius:8px 8px 0 0; cursor:pointer; text-decoration:none; color:#a4b6e0; font-weight:600; font-size:13px; }}
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
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }}
th, td {{ border: 1px solid #2a3a55; padding: 6px 9px; text-align: left; }}
th {{ background: #0f1722; color: #a4b6e0; }}
td {{ background: #141e2f; }}
tr:hover td {{ background: #1a2638; }}
.cat-pill {{ display:inline-block; padding:2px 7px; border-radius:5px; background:#1e2a40; color:#9bb0d0; font-size:0.85em; }}
.search-box {{ width:100%; padding:10px; background:#0f1722; border:1px solid #40516a; color:#e8eef8; border-radius:6px; box-sizing:border-box; margin-bottom:12px; }}
.stat-card {{ background:#0f1722; border:1px solid #2a3a55; border-radius:9px; padding:14px; display:inline-block; margin:6px; min-width:160px; }}
.stat-card strong {{ display:block; color:#3e82f7; font-size:24px; margin-bottom:4px; }}
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
            f'<div class="model-card"><strong>{html.escape(name)}</strong>'
            f'<span class="tag {vclass}">v{p.inner_version}</span> '
            f'<span class="tag {lclass}">{html.escape(p.lineage)}</span><br>'
            f'<span class="muted">EI {p.element_info_length} · TD {p.track_data_length}</span></div>'
        )
    return '<div class="model-grid">' + "".join(cards) + '</div>'


def tabs_html(active: str, token: str) -> str:
    items = [
        ("Konverter", "convert"),
        ("Song Optim.", "song"),
        ("Style Optim.", "style"),
        ("Inspector", "inspect"),
        ("Sound Browser", "sounds"),
        ("Drum Kit", "drumkit"),
        ("Style Editor", "style_editor"),
        ("STS Editor", "sts_editor"),
        ("Sound ReMap", "remap"),
        ("Sample Tools", "samples"),
        ("SET Report", "report"),
        ("PA800 OS", "resources"),
    ]
    out = ['<div class="tabs">']
    for name, key in items:
        cls = "tab active" if key == active else "tab"
        out.append(f'<a class="{cls}" href="/{token}/{key}">{html.escape(name)}</a>')
    out.append('</div>')
    return "".join(out)


# ---------------------------------------------------------------------------
# Forms for each tab
# ---------------------------------------------------------------------------


def form_convert(action: str, token: str) -> bytes:
    model_options = "".join(
        f'<option value="{html.escape(name)}">{html.escape(name)}</option>'
        for name in sorted(conv.MODEL_REGISTRY.keys())
    )
    return page("KORG PA Konverter", f"""
<div class="card">
  <h1>KORG PA Konverter</h1>
  <p class="muted">Cross-model konverzija STY fajlova između svih 12 Korg PA modela. Fail-closed: nikada ne izmišlja podatke bez dokaza.</p>
  {tabs_html("convert", token)}
  <div class="warning"><b>Važno:</b> Uvijek napravi backup prije konverzije. Testiraj na instrumentu.</div>
  <form method="post" action="{action}" enctype="multipart/form-data">
    <label>Ulazni STY fajl</label>
    <input type="file" name="sty_file" accept=".sty,.STY" required>
    <div class="row">
      <div class="col"><label>Izvorni model (prazno = auto-detekcija)</label>
        <select name="source_model"><option value="">Auto-detekcija</option>{model_options}</select></div>
      <div class="col"><label>Ciljni model</label>
        <select name="target_model" required>{model_options}</select></div>
    </div>
    <label class="check"><input type="checkbox" name="expand_element_info" value="1"> Proširi ElementInfo 28 → 44 (za PA4X/PA700 cilj)</label>
    <label class="check"><input type="checkbox" name="allow_lossy" value="1"> Dozvoli lossy režim</label>
    <button type="submit">Pokreni konverziju</button>
  </form>
</div>
<div class="card"><h2>Podržani modeli</h2>{model_grid_html()}</div>""")


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
      <div class="col"><label>Žanr (prazno = auto-detekcija)</label>
        <select name="genre"><option value="">Auto-detekcija</option>{genres}</select></div>
      <div class="col"><label>Ciljni KORG model</label>
        <select name="target_model">{targets}</select></div>
    </div>
    <label class="check"><input type="checkbox" name="velocity_opt" value="1" checked> Velocity optimizacija</label>
    <label class="check"><input type="checkbox" name="articulation" value="1" checked> Articulation engine</label>
    <label class="check"><input type="checkbox" name="rx_dnc" value="1" checked> RX/DNC remap</label>
    <label class="check"><input type="checkbox" name="cross_delay" value="1"> Cross delay echo</label>
    <label class="check"><input type="checkbox" name="fx_routing" value="1" checked> FX routing</label>
    <button type="submit">Optimiziraj Song</button>
  </form>
</div>""")


def form_style(action: str, token: str) -> bytes:
    genres = "".join(f'<option value="{g}">{g}</option>' for g in GENRE_TEMPLATES.keys())
    sources = "".join(f'<option value="{n}">{n}</option>' for n in sorted(conv.MODEL_REGISTRY.keys()))
    targets = "".join(f'<option value="{n}">{n}</option>' for n in sorted(conv.MODEL_REGISTRY.keys()))
    return page("KORG PA Style Optimizer", f"""
<div class="card">
  <h1>KORG PA Style Optimizer</h1>
  <p class="muted">Optimizacija STY fajlova korištenjem shared engine logike. Isti algoritam kao Song Optimizer.</p>
  {tabs_html("style", token)}
  <div class="warning"><b>Napomena:</b> Style Optimizer modifikuje TrackData Expression bajtove. STS (ID-09) se čuva byte-exact.</div>
  <form method="post" action="{action}" enctype="multipart/form-data">
    <label>Ulazni STY fajl</label>
    <input type="file" name="sty_file" accept=".sty,.STY" required>
    <div class="row">
      <div class="col"><label>Izvorni model</label><select name="source_model"><option value="">Auto-detekcija</option>{sources}</select></div>
      <div class="col"><label>Ciljni model</label><select name="target_model">{targets}</select></div>
    </div>
    <label>Žanr</label>
    <select name="genre"><option value="">Auto-detekcija</option>{genres}</select>
    <label class="check"><input type="checkbox" name="velocity_opt" value="1" checked> Velocity optimizacija</label>
    <label class="check"><input type="checkbox" name="articulation" value="1" checked> Articulation</label>
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
  <p class="muted">Strukturalni izvještaj STY fajla. Provjerava container, OC20/OC31/OC32 kompresiju, KBEG/KEND indeks.</p>
  {tabs_html("inspect", token)}
  <form method="post" action="{action}" enctype="multipart/form-data">
    <label>STY fajl</label>
    <input type="file" name="sty_file" accept=".sty,.STY" required>
    <label class="check"><input type="checkbox" name="repack" value="1"> Također izvrši PA800 native repack</label>
    <button type="submit">Inspect</button>
  </form>
</div>""")


def page_sounds(token: str) -> bytes:
    db = db_mod.get_database()
    cat_counts: dict[str, int] = {}
    for s in db.sounds:
        cat_counts[s.category] = cat_counts.get(s.category, 0) + 1
    cat_options = '<option value="">Sve kategorije</option>' + "".join(
        f'<option value="{html.escape(c)}">{html.escape(c)} ({cat_counts[c]})</option>'
        for c in sorted(cat_counts.keys()) if cat_counts[c] > 0
    )
    sounds_rows = "".join(
        f'<tr class="sound-row" data-name="{html.escape(s.name.lower())}" data-category="{html.escape(s.category)}">'
        f'<td>{html.escape(s.name)}</td>'
        f'<td><span class="cat-pill">{html.escape(s.category)}</span></td>'
        f'<td>{", ".join(s.variants) if s.variants else "—"}</td>'
        f'<td>{html.escape(s.file_source)}</td></tr>'
        for s in db.sounds[:200]
    )
    more_count = max(0, len(db.sounds) - 200)
    return page("Sound Browser", f"""
<div class="card">
  <h1>Sound Browser — PA800 Factory Zvukovi</h1>
  <p class="muted">Pregled {len(db.sounds)} zvukova iz PA800 OS v202. Implementira V3 funkciju getSoundInfo.</p>
  {tabs_html("sounds", token)}
  <div>
    <div class="stat-card"><strong>{len(db.sounds)}</strong>Zvukova</div>
    <div class="stat-card"><strong>{len(db.drum_kits)}</strong>Drum Kitova</div>
    <div class="stat-card"><strong>{len(db.styles)}</strong>Stilova</div>
    <div class="stat-card"><strong>{len(cat_counts)}</strong>Kategorija</div>
  </div>
  <h2>Pretraga</h2>
  <input class="search-box" type="text" id="searchSounds" placeholder="Pretraži zvukove..." oninput="filterSounds()">
  <h2>Zvukovi</h2>
  <table><tr><th>Naziv</th><th>Kategorija</th><th>Varijante</th><th>Izvor</th></tr>{sounds_rows}</table>
  {f'<p class="muted">+ još {more_count} zvukova...</p>' if more_count > 0 else ''}
  <script>
  function filterSounds() {{
    const q = document.getElementById('searchSounds').value.toLowerCase();
    document.querySelectorAll('.sound-row').forEach(row => {{
      const name = row.getAttribute('data-name');
      row.style.display = name.includes(q) ? '' : 'none';
    }});
  }}
  </script>
</div>""")


def page_drumkit(token: str) -> bytes:
    db = db_mod.get_database()
    kit_options = "".join(
        f'<option value="{html.escape(k.name)}">{html.escape(k.name)}</option>'
        for k in db.drum_kits
    )
    # Build initial table for first kit
    initial_kit = db.drum_kits[0] if db.drum_kits else None
    kit_table = ""
    if initial_kit and initial_kit.key_map:
        rows = []
        for midi_key in sorted(initial_kit.key_map.keys()):
            info = initial_kit.key_map[midi_key]
            switches = ", ".join(str(s) for s in info.get("switches", [])) or "(single layer)"
            samples = ", ".join(info.get("samples", [])) or "—"
            rows.append(
                f'<tr><td>{midi_key}</td><td>{html.escape(info.get("note", ""))}</td>'
                f'<td>{info.get("oscillators", 1)}</td>'
                f'<td>{html.escape(switches)}</td>'
                f'<td>{html.escape(samples)}</td></tr>'
            )
        kit_table = (
            '<h3>' + html.escape(initial_kit.name) + '</h3>'
            '<table><tr><th>MIDI Key</th><th>Napomena</th><th>Oscilatora</th>'
            '<th>Velocity switch</th><th>Sample</th></tr>' + "".join(rows) + '</table>'
        )
    return page("Drum Kit Editor", f"""
<div class="card">
  <h1>Drum Kit Editor</h1>
  <p class="muted">Pregled i edit drum kitova sa velocity layer switches. Implementira V3 funkciju Drum Kit editor.</p>
  {tabs_html("drumkit", token)}
  <h2>Available drum kits</h2>
  {kit_table}
  <h2>Drum Kit Layer Mapping (Pop Std. Kit RX)</h2>
  <p class="muted">PA800 dokumentovani velocity layer switches (iz audit_pa800_drum_kits.py):</p>
  <ul>
    <li><b>Snare</b> (key 38): 2 layera, switch na velocity 80</li>
    <li><b>Open Hat</b> (key 46): 2 layera, switch na velocity 90</li>
    <li><b>Toms</b> (keys 47-50): 2 layera, switch na velocity 85</li>
    <li><b>Crash 1</b> (key 49): 2 layera, switch na velocity 80</li>
    <li><b>Ride</b> (key 51): 2 layera, switch na velocity 80</li>
    <li><b>Crash 2</b> (key 57): 2 layera, switch na velocity 85</li>
  </ul>
  <h2>Jazz Kit Layer Mapping</h2>
  <ul>
    <li><b>Snare</b> (key 38): switch na velocity 70 (lower than Pop)</li>
    <li><b>Crash</b> (key 49): switch na velocity 75</li>
  </ul>
</div>""")


def page_style_editor(action: str, token: str) -> bytes:
    return page("Style Editor", f"""
<div class="card">
  <h1>Style Editor</h1>
  <p class="muted">Pregled i edit 8 traka STY fajla. Implementira V3 funkciju Style Track Report, Update Style, Reset Style.</p>
  {tabs_html("style_editor", token)}
  <form method="post" action="{action}" enctype="multipart/form-data">
    <label>STY fajl</label>
    <input type="file" name="sty_file" accept=".sty,.STY" required>
    <button type="submit">Ekstraktuj 8 traka</button>
  </form>
</div>""")


def page_sts_editor(action: str, token: str) -> bytes:
    return page("STS/Performance Editor", f"""
<div class="card">
  <h1>STS / Performance Editor</h1>
  <p class="muted">Pregled 4 keyboard trake (Lower, Upper1, Upper2, Upper3). Implementira V3 funkciju LoadStyleSTS, reMapSTS.</p>
  {tabs_html("sts_editor", token)}
  <form method="post" action="{action}" enctype="multipart/form-data">
    <label>STY fajl</label>
    <input type="file" name="sty_file" accept=".sty,.STY" required>
    <label>STS resource pozicija (0 = prva STS)</label>
    <input type="number" name="resource_position" value="0" min="0">
    <button type="submit">Ekstraktuj 4 keyboard trake</button>
  </form>
</div>""")


def page_remap(action: str, token: str) -> bytes:
    db = db_mod.get_database()
    sound_options = "".join(
        f'<option value="{html.escape(s.name)}">{html.escape(s.name)} ({html.escape(s.category)})</option>'
        for s in sorted(db.sounds, key=lambda x: x.name)[:100]
    )
    return page("Sound ReMap — Find & Replace", f"""
<div class="card">
  <h1>Sound ReMap — Find & Replace</h1>
  <p class="muted">Zamijeni sve instance zvuka "source" sa zvukom "target" u STY fajlu. Implementira V3 funkciju reMapSTY: "This will Re-Map All Sound Instances of [source] with [target]".</p>
  {tabs_html("remap", token)}
  <form method="post" action="{action}" enctype="multipart/form-data">
    <label>STY fajl</label>
    <input type="file" name="sty_file" accept=".sty,.STY" required>
    <div class="row">
      <div class="col"><label>Source zvuk (šta se mijenja)</label>
        <select name="source_sound" required>{sound_options}</select></div>
      <div class="col"><label>Target zvuk (na šta se mijenja)</label>
        <select name="target_sound" required>{sound_options}</select></div>
    </div>
    <label>Scope</label>
    <select name="scope">
      <option value="sty">Cijeli STY (STYLE + STS)</option>
      <option value="sts">Samo STS</option>
    </select>
    <button type="submit">ReMap</button>
  </form>
</div>""")


def page_samples(action: str, token: str) -> bytes:
    return page("Sample Tools", f"""
<div class="card">
  <h1>Sample Tools</h1>
  <p class="muted">Konverzije PCM sample-a. Implementira V3 funkcije: msgStereoToMono, convert_8bit_to_16bit, mnuExportSample, mnuExportDK, mnuSize.</p>
  {tabs_html("samples", token)}
  <div class="warning"><b>Potvrđene formule (V3 disassembly):</b><br>
  <b>8-bit → 16-bit:</b> <code>output = (input - 128) * 256</code> — VA 0x02bfa470<br>
  <b>Stereo → Mono:</b> <code>mono = trunc_toward_zero((left + right) / 2)</code> — VA 0x02bfa9f0<br>
  <b>NE floor rounding!</b> — native ops: CDQ (sign), sum -= sign, SAR sum, 1</div>
  <h2>1. Stereo 16-bit → Mono 16-bit</h2>
  <form method="post" action="{action}" enctype="multipart/form-data">
    <input type="hidden" name="operation" value="stereo_to_mono">
    <label>Ulazni PCM (stereo 16-bit LE)</label>
    <input type="file" name="pcm_file" required>
    <button type="submit">Konvertuj u Mono</button>
  </form>
  <h2>2. 8-bit unsigned → 16-bit signed</h2>
  <form method="post" action="{action}" enctype="multipart/form-data">
    <input type="hidden" name="operation" value="8bit_to_16bit">
    <label>Ulazni PCM (8-bit unsigned)</label>
    <input type="file" name="pcm_file" required>
    <button type="submit">Konvertuj u 16-bit</button>
  </form>
  <h2>3. Export PCM to WAV</h2>
  <form method="post" action="{action}" enctype="multipart/form-data">
    <input type="hidden" name="operation" value="export_wav">
    <label>Ulazni PCM (raw)</label>
    <input type="file" name="pcm_file" required>
    <div class="row">
      <div class="col"><label>Sample rate (Hz)</label><input type="number" name="sample_rate" value="44100"></div>
      <div class="col"><label>Bit depth</label><select name="bit_depth"><option value="16">16</option><option value="24">24</option><option value="8">8</option></select></div>
      <div class="col"><label>Channels</label><select name="channels"><option value="1">1 (mono)</option><option value="2">2 (stereo)</option></select></div>
    </div>
    <button type="submit">Export u WAV</button>
  </form>
</div>""")


def page_report(action: str, token: str) -> bytes:
    return page("SET Report Generator", f"""
<div class="card">
  <h1>SET Report Generator</h1>
  <p class="muted">Generiše kompletan izvještaj PA800 SET-a u HTML, JSON ili CSV formatu. Implementira V3 funkciju cmdReport.</p>
  {tabs_html("report", token)}
  <form method="post" action="{action}" enctype="multipart/form-data">
    <label>Format izvještaja</label>
    <select name="format">
      <option value="html">HTML (pregledan, sa pretragom)</option>
      <option value="json">JSON (programski)</option>
      <option value="csv">CSV (Excel/Sheets)</option>
      <option value="all">Sva tri formata</option>
    </select>
    <label>Naziv SET-a</label>
    <input type="text" name="set_name" value="PA800 Factory SET" placeholder="npr. MOJ SET">
    <button type="submit">Generiši izvještaj</button>
  </form>
  <h2>Šta izvještaj sadrži</h2>
  <ul>
    <li><b>Statistike</b> — ukupan broj zvukova, kitova, stilova, slika</li>
    <li><b>Zvukovi po kategoriji</b> — Piano, Bass, Strings, Brass, itd.</li>
    <li><b>Svi zvukovi</b> — naziv, kategorija, varijante, izvor</li>
    <li><b>Drum kitovi</b> — key map, velocity switches, sample refs</li>
    <li><b>Stilovi</b> — naziv, žanr, izvor</li>
    <li><b>Slike</b> — ikone, wallpaper (ako postoje)</li>
  </ul>
</div>""")


def page_resources(token: str) -> bytes:
    image_html = ""
    if IMAGES_DIR.exists():
        images = sorted(IMAGES_DIR.glob('*.jpg'), key=lambda p: -p.stat().st_size)[:30]
        for img in images:
            name = html.escape(img.name)
            size_kb = img.stat().st_size / 1024
            image_html += (
                f'<div class="image-tile">'
                f'<img class="thumb" src="/{token}/img/{name}" alt="{name}">'
                f'<div class="name">{name}<br>{size_kb:.1f} KB</div></div>'
            )
    return page("PA800 OS resursi", f"""
<div class="card">
  <h1>PA800 OS resursi</h1>
  <p class="muted">Ekstraktovano iz oficijelnog KORG PA800 OS v2.02 PKG-a (134 MB). 746 OC20 stranica, 94 logička fajla, 69 JPEG slika.</p>
  {tabs_html("resources", token)}
  <h2>Ekstraktovane slike (ikone, wallpaper)</h2>
  <div class="image-grid">{image_html}</div>
  <h2>Struktura PKG-a</h2>
  <ul>
    <li><b>746 OC20 stranica</b> — CBarc kompresovane</li>
    <li><b>94 logička fajla</b> — KORF records</li>
    <li><b>13 PCG/KMP</b> — instrumenti</li>
    <li><b>3 STY banke</b> — 148 stilova</li>
    <li><b>3 drum kit fajla</b></li>
    <li><b>69 JPEG slika</b></li>
  </ul>
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
        if not name: continue
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
            f"STYLE {report.get('converted_style_count', 0)} / {report.get('preserved_style_count', 0)}; "
            f"STS {report.get('converted_sts_count', 0)} / {report.get('preserved_sts_count', 0)}; "
            f"errors: {report.get('decode_error_count', 0)}."
        )
    if "style_resources" in report:
        opt = sum(1 for s in report.get("styles", []) if s.get("status") == "optimized")
        pres = sum(1 for s in report.get("styles", []) if s.get("status") == "preserved")
        return f"Style optimizacija: {opt} optimizirano, {pres} očuvano."
    if "track_profiles" in report and "applied_optimizations" in report:
        return (
            f"Song optimizacija: {len(report['track_profiles'])} traka, "
            f"žanr: {report.get('genre', 'n/a')}, "
            f"{len(report.get('applied_optimizations', []))} optimizacija."
        )
    if "instances_replaced" in report:
        return (
            f"ReMap: {report.get('instances_replaced', 0)} instance zamijenjeno "
            f"({report.get('source_sound')} → {report.get('target_sound')})."
        )
    if "decoded_resource_count" in report:
        return (
            f"Inspect: {report.get('decoded_resource_count', 0)}/"
            f"{report.get('resource_count', 0)} resursa dekodirano; "
            f"greške: {report.get('decode_error_count', 0)}."
        )
    if "tracks" in report:
        return f"Prikazano {len(report['tracks'])} traka."
    if "sts_tracks" in report:
        return f"Prikazano {len(report['sts_tracks'])} STS traka."
    if "samples_exported" in report:
        return f"Sample export: {report['samples_exported']} sample-a izvezeno u WAV."
    if "report_path" in report:
        return f"Izvještaj generisan: {report['report_path']}"
    return "Operacija završena."


def create_server(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    token = secrets.token_urlsafe(18)
    storage = Path(tempfile.mkdtemp(prefix="korg_pa_v5_"))
    downloads: dict[str, tuple[Path, str, str]] = {}
    base = f"/{token}"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args): pass

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
            routes = {
                "convert": form_convert,
                "song": form_song,
                "style": form_style,
                "inspect": form_inspect,
                "sounds": lambda a, t: page_sounds(t),
                "drumkit": lambda a, t: page_drumkit(t),
                "style_editor": page_style_editor,
                "sts_editor": page_sts_editor,
                "remap": page_remap,
                "samples": page_samples,
                "report": page_report,
                "resources": lambda a, t: page_resources(t),
            }
            for key, fn in routes.items():
                if parsed.path == f"{base}/{key}":
                    self.send_html(200, fn(f"{base}/run/{key}", token))
                    return
            img_prefix = f"{base}/img/"
            if parsed.path.startswith(img_prefix):
                name = parsed.path[len(img_prefix):]
                img_path = IMAGES_DIR / name
                if not img_path.exists() or not img_path.is_file():
                    self.send_error(404); return
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
                    self.send_error(404); return
                path, content_type, filename = item
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
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
                # Route by path
                handler_map = {
                    f"{base}/run/convert": self._h_convert,
                    f"{base}/run/song": self._h_song,
                    f"{base}/run/style": self._h_style,
                    f"{base}/run/inspect": self._h_inspect,
                    f"{base}/run/style_editor": self._h_style_editor,
                    f"{base}/run/sts_editor": self._h_sts_editor,
                    f"{base}/run/remap": self._h_remap,
                    f"{base}/run/samples": self._h_samples,
                    f"{base}/run/report": self._h_report,
                }
                handler = handler_map.get(path)
                if not handler:
                    raise ValueError(f"Nepoznata akcija: {path}")
                result = handler(fields, filename, uploaded, job)
                self._render_result(result)
            except Exception as error:
                content = page("Greška", f"""
<div class="card"><h1 class="error">Greška</h1>
<p>{html.escape(str(error))}</p>
<a class="button secondary" href="{base}">Nazad</a></div>""")
                self.send_html(400, content)

        def _h_convert(self, fields, filename, uploaded, job):
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
                    raise ValueError("Auto-detekcija nije uspjela. Izaberi izvorni model.")
                source_model = matches[0]
            converted, report = conv.convert_sty_cross_model(
                uploaded, source_model, target_model,
                allow_lossy=fields.get("allow_lossy") == "1",
                expand_element_info=fields.get("expand_element_info") == "1",
            )
            output = job / f"{Path(filename).stem}_{target_model}.STY"
            output.write_bytes(converted)
            report_path = job / f"{Path(filename).stem}_convert.json"
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
            return {
                "summary": render_summary(report),
                "downloads": [(output, "application/octet-stream", output.name),
                              (report_path, "application/json", report_path.name)],
                "report_preview": {"converted_style_count": report.get("converted_style_count"),
                                  "preserved_style_count": report.get("preserved_style_count"),
                                  "source_model": source_model, "target_model": target_model},
            }

        def _h_song(self, fields, filename, uploaded, job):
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
            report_path = job / f"{Path(filename).stem}_song.json"
            result = song_opt.optimize_midi_file(midi_path, output, report_path, settings)
            return {
                "summary": render_summary(result),
                "downloads": [(output, "audio/midi", output.name),
                              (report_path, "application/json", report_path.name)],
                "report_preview": result,
            }

        def _h_style(self, fields, filename, uploaded, job):
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
            report_path = job / f"{Path(filename).stem}_style.json"
            result = sty_opt.optimize_sty_file(sty_path, output, report_path, settings, source_model, target_model)
            return {
                "summary": render_summary(result),
                "downloads": [(output, "application/octet-stream", output.name),
                              (report_path, "application/json", report_path.name)],
                "report_preview": result,
            }

        def _h_inspect(self, fields, filename, uploaded, job):
            inspection = sty.inspect_bytes(uploaded, filename)
            report_path = job / f"{Path(filename).stem}_inspect.json"
            report_path.write_text(json.dumps(inspection, indent=2, ensure_ascii=False, default=str))
            downloads = [(report_path, "application/json", report_path.name)]
            if fields.get("repack") == "1":
                try:
                    packed, _ = sty.repack_bytes(uploaded)
                    output = job / f"{Path(filename).stem}_PA800_NATIVE.STY"
                    output.write_bytes(packed)
                    downloads.append((output, "application/octet-stream", output.name))
                except sty.StyError as e:
                    inspection["repack_error"] = str(e)
            return {
                "summary": render_summary(inspection),
                "downloads": downloads,
                "report_preview": inspection,
            }

        def _h_style_editor(self, fields, filename, uploaded, job):
            tracks = editors.extract_style_tracks(uploaded)
            return {
                "summary": render_summary({"tracks": tracks}),
                "downloads": [],
                "report_preview": {"tracks": [
                    {"index": t.index, "role": t.role,
                     "bank_msb": t.sound_bank_msb, "bank_lsb": t.sound_bank_lsb,
                     "program": t.sound_program} for t in tracks
                ]},
            }

        def _h_sts_editor(self, fields, filename, uploaded, job):
            pos = int(fields.get("resource_position", "0"))
            tracks = editors.extract_sts_tracks(uploaded, pos)
            return {
                "summary": render_summary({"sts_tracks": tracks}),
                "downloads": [],
                "report_preview": {"sts_tracks": [
                    {"role": t.role, "selector_kind": t.selector_kind,
                     "user_bank": t.user_bank, "user_position": t.user_position,
                     "volume": t.volume, "pan": t.pan,
                     "fx_c_send": t.fx_c_send, "fx_d_send": t.fx_d_send,
                     "mute": t.mute} for t in tracks
                ]},
            }

        def _h_remap(self, fields, filename, uploaded, job):
            source_sound = fields.get("source_sound")
            target_sound = fields.get("target_sound")
            scope = fields.get("scope", "sty")
            if scope == "sts":
                new_data, result = editors.remap_sound_in_sts(uploaded, source_sound, target_sound)
            else:
                new_data, result = editors.remap_sound_in_sty(uploaded, source_sound, target_sound)
            output = job / f"{Path(filename).stem}_remapped.STY"
            output.write_bytes(new_data)
            report_path = job / f"{Path(filename).stem}_remap.json"
            report_path.write_text(json.dumps({
                "operation": result.operation,
                "source_sound": result.source_sound,
                "target_sound": result.target_sound,
                "instances_found": result.instances_found,
                "instances_replaced": result.instances_replaced,
                "resources_affected": result.resources_affected,
                "warnings": result.warnings,
            }, indent=2, ensure_ascii=False))
            return {
                "summary": render_summary({
                    "instances_replaced": result.instances_replaced,
                    "source_sound": source_sound, "target_sound": target_sound,
                }),
                "downloads": [(output, "application/octet-stream", output.name),
                              (report_path, "application/json", report_path.name)],
                "report_preview": {
                    "operation": result.operation,
                    "instances_found": result.instances_found,
                    "instances_replaced": result.instances_replaced,
                    "resources_affected": result.resources_affected,
                },
            }

        def _h_samples(self, fields, filename, uploaded, job):
            operation = fields.get("operation")
            pcm_data = uploaded
            output_path = None
            report = {}
            if operation == "stereo_to_mono":
                mono_data = sample_tools.convert_stereo_16bit_to_mono_16bit(pcm_data)
                output_path = job / f"{Path(filename).stem}_mono.pcm"
                output_path.write_bytes(mono_data)
                report = {"operation": "Stereo to Mono",
                         "input_bytes": len(pcm_data), "output_bytes": len(mono_data)}
            elif operation == "8bit_to_16bit":
                signed_data = sample_tools.convert_8bit_unsigned_to_16bit_signed(pcm_data)
                output_path = job / f"{Path(filename).stem}_16bit.pcm"
                output_path.write_bytes(signed_data)
                report = {"operation": "8-bit to 16-bit",
                         "input_bytes": len(pcm_data), "output_bytes": len(signed_data)}
            elif operation == "export_wav":
                sample_rate = int(fields.get("sample_rate", "44100"))
                bit_depth = int(fields.get("bit_depth", "16"))
                channels = int(fields.get("channels", "1"))
                output_path = job / f"{Path(filename).stem}.wav"
                info = sample_tools.export_pcm_to_wav(
                    pcm_data, output_path, sample_rate, bit_depth, channels
                )
                report = {"operation": "Export to WAV", **info}
            return {
                "summary": render_summary({
                    "samples_exported": report.get("samples_count", 1),
                    **report,
                }),
                "downloads": [(output_path, "application/octet-stream", output_path.name)] if output_path else [],
                "report_preview": report,
            }

        def _h_report(self, fields, filename, uploaded, job):
            fmt = fields.get("format", "html")
            set_name = fields.get("set_name", "PA800 Factory SET")
            downloads = []
            if fmt in ("html", "all"):
                html_content = set_report.generate_set_report_html(set_name)
                html_path = job / f"{set_name.replace(' ', '_')}_report.html"
                html_path.write_text(html_content, encoding='utf-8')
                downloads.append((html_path, "text/html", html_path.name))
            if fmt in ("json", "all"):
                json_data = set_report.generate_set_report_json(set_name)
                json_path = job / f"{set_name.replace(' ', '_')}_report.json"
                json_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding='utf-8')
                downloads.append((json_path, "application/json", json_path.name))
            if fmt in ("csv", "all"):
                csv_path = job / f"{set_name.replace(' ', '_')}_report.csv"
                set_report.generate_set_report_csv(csv_path, set_name)
                downloads.append((csv_path, "text/csv", csv_path.name))
            return {
                "summary": render_summary({"report_path": downloads[0][2] if downloads else ""}),
                "downloads": downloads,
                "report_preview": {"format": fmt, "files": [d[2] for d in downloads]},
            }

        def _render_result(self, result):
            links = ""
            for path, content_type, fname in result.get("downloads", []):
                key = secrets.token_urlsafe(12)
                downloads[key] = (path, content_type, fname)
                label = "Preuzmi STY" if fname.endswith(".STY") else (
                    "Preuzmi MIDI" if fname.endswith(".mid") else (
                    "Preuzmi WAV" if fname.endswith(".wav") else (
                    "Preuzmi HTML" if fname.endswith(".html") else (
                    "Preuzmi CSV" if fname.endswith(".csv") else "Preuzmi JSON"))))
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
  <pre>{html.escape(preview_json[:6000])}</pre>
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
    print(f"KORG PA Manager v5 GUI: {url}")
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
