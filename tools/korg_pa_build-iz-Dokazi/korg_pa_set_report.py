#!/usr/bin/env python3
"""KORG PA SET Report Generator — generiše HTML/JSON izvještaj SET-a.

Implementira V3 funkciju 'cmdReport' (Generate Report) — generiše
kompletan izvještaj o SET-u sa svim zvukovima, stilovima, drum kitovima,
PAD-ovima, Performance-ima i PCM fajlovima.

Izlaz može biti:
  - HTML izvještaj (pregledan, sa tabelama i pretragom)
  - JSON izvještaj (programski čitljiv)
  - CSV izvještaj (za Excel/Google Sheets)

V3 string: "Save Report" - izvještaj se sačuvava uz SET.
"""

from __future__ import annotations

import csv
import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from korg_pa_database import (
    get_database, INSTRUMENT_CATEGORIES, SoundRecord, DrumKitRecord,
    StyleRecord, ImageRecord,
)


def generate_set_report_html(set_name: str = "PA800 Factory SET") -> str:
    """Generiše kompletan HTML izvještaj o PA800 factory SET-u."""
    db = get_database()
    # Aggregate stats
    cat_counts: dict[str, int] = {}
    for s in db.sounds:
        cat_counts[s.category] = cat_counts.get(s.category, 0) + 1
    # Build HTML
    parts: list[str] = []
    parts.append(f"""<!doctype html>
<html lang="bs">
<head>
<meta charset="utf-8">
<title>{html.escape(set_name)} — KORG PA Manager Report</title>
<style>
body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0a1019; color: #e8eef8; margin: 24px; }}
h1 {{ color: #3e82f7; }} h2 {{ color: #a4b6e0; margin-top: 30px; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 14px; }}
th, td {{ border: 1px solid #2a3a55; padding: 8px 10px; text-align: left; }}
th {{ background: #141e2f; color: #a4b6e0; }}
td {{ background: #0f1722; }}
.cat-pill {{ display: inline-block; padding: 2px 8px; border-radius: 5px; background: #1e2a40; color: #9bb0d0; font-size: 0.85em; }}
.search-box {{ padding: 10px; background: #0f1722; border: 1px solid #40516a; color: #e8eef8; width: 100%; box-sizing: border-box; border-radius: 6px; }}
.stat-card {{ background: #141e2f; border: 1px solid #2a3a55; border-radius: 9px; padding: 16px; display: inline-block; margin: 8px; min-width: 180px; }}
.stat-card strong {{ display: block; color: #3e82f7; font-size: 28px; margin-bottom: 4px; }}
</style>
</head>
<body>
<h1>{html.escape(set_name)} — KORG PA Manager Report</h1>
<p>Kompletan izvještaj svih resursa u SET-u. Generisan iz PA800 OS v202 PKG-a.</p>
""")
    # Stats
    parts.append('<h2>Statistike</h2>')
    parts.append(f'<div class="stat-card"><strong>{len(db.sounds)}</strong>Zvukova</div>')
    parts.append(f'<div class="stat-card"><strong>{len(db.drum_kits)}</strong>Drum Kitova</div>')
    parts.append(f'<div class="stat-card"><strong>{len(db.styles)}</strong>Stilova</div>')
    parts.append(f'<div class="stat-card"><strong>{len(db.images)}</strong>Slika (ikone/wallpaper)</div>')
    # Sounds by category
    parts.append('<h2>Zvukovi po kategoriji</h2>')
    parts.append('<table><tr><th>Kategorija</th><th>Broj</th></tr>')
    for cat in INSTRUMENT_CATEGORIES:
        if cat in cat_counts:
            parts.append(f'<tr><td>{html.escape(cat)}</td><td>{cat_counts[cat]}</td></tr>')
    parts.append('</table>')
    # All sounds table
    parts.append('<h2>Svi zvukovi</h2>')
    parts.append('<input class="search-box" type="text" id="searchSounds" placeholder="Pretraži zvukove..." oninput="filterSounds()">')
    parts.append('<table id="soundsTable"><tr><th>Naziv</th><th>Kategorija</th><th>Varijante</th><th>Izvor</th></tr>')
    for s in db.sounds:
        variants = ", ".join(s.variants) if s.variants else "—"
        parts.append(
            f'<tr class="sound-row" data-name="{html.escape(s.name.lower())}" data-category="{html.escape(s.category)}">'
            f'<td>{html.escape(s.name)}</td>'
            f'<td><span class="cat-pill">{html.escape(s.category)}</span></td>'
            f'<td>{html.escape(variants)}</td>'
            f'<td>{html.escape(s.file_source)}</td></tr>'
        )
    parts.append('</table>')
    # Drum kits
    parts.append('<h2>Drum kitovi</h2>')
    for kit in db.drum_kits:
        parts.append(f'<h3>{html.escape(kit.name)} (bank {kit.bank_msb}/{kit.bank_lsb} prog {kit.program})</h3>')
        if kit.key_map:
            parts.append('<table><tr><th>MIDI Key</th><th>Napomena</th><th>Oscilatora</th><th>Velocity switch</th><th>Sample</th></tr>')
            for midi_key in sorted(kit.key_map.keys()):
                info = kit.key_map[midi_key]
                switches = ", ".join(str(s) for s in info.get("switches", [])) or "(single layer)"
                samples = ", ".join(info.get("samples", [])) or "—"
                parts.append(
                    f'<tr><td>{midi_key}</td><td>{html.escape(info.get("note", ""))}</td>'
                    f'<td>{info.get("oscillators", 1)}</td>'
                    f'<td>{html.escape(switches)}</td>'
                    f'<td>{html.escape(samples)}</td></tr>'
                )
            parts.append('</table>')
    # Styles
    parts.append('<h2>Stilovi</h2>')
    parts.append('<input class="search-box" type="text" id="searchStyles" placeholder="Pretraži stilove..." oninput="filterStyles()">')
    parts.append('<table id="stylesTable"><tr><th>Naziv</th><th>Žanr</th><th>Izvor</th></tr>')
    for s in db.styles:
        parts.append(
            f'<tr class="style-row" data-name="{html.escape(s.name.lower())}" data-genre="{html.escape(s.genre)}">'
            f'<td>{html.escape(s.name)}</td>'
            f'<td><span class="cat-pill">{html.escape(s.genre)}</span></td>'
            f'<td>{html.escape(s.file_source)}</td></tr>'
        )
    parts.append('</table>')
    # Images
    if db.images:
        parts.append('<h2>Ikone i wallpaper</h2>')
        parts.append('<table><tr><th>Naziv</th><th>Veličina</th><th>Tip</th></tr>')
        for img in db.images[:30]:
            parts.append(
                f'<tr><td>{html.escape(img.name)}</td>'
                f'<td>{img.size:,} bytes</td>'
                f'<td>{img.image_type}</td></tr>'
            )
        parts.append('</table>')
        if len(db.images) > 30:
            parts.append(f'<p class="muted">i još {len(db.images) - 30} slika...</p>')
    # JavaScript for filtering
    parts.append("""<script>
function filterSounds() {
  const q = document.getElementById('searchSounds').value.toLowerCase();
  document.querySelectorAll('.sound-row').forEach(row => {
    const name = row.getAttribute('data-name');
    row.style.display = name.includes(q) ? '' : 'none';
  });
}
function filterStyles() {
  const q = document.getElementById('searchStyles').value.toLowerCase();
  document.querySelectorAll('.style-row').forEach(row => {
    const name = row.getAttribute('data-name');
    row.style.display = name.includes(q) ? '' : 'none';
  });
}
</script>
</body></html>""")
    return "".join(parts)


def generate_set_report_json(set_name: str = "PA800 Factory SET") -> dict[str, Any]:
    """Generiše JSON izvještaj o SET-u."""
    db = get_database()
    return {
        "set_name": set_name,
        "summary": {
            "total_sounds": len(db.sounds),
            "total_drum_kits": len(db.drum_kits),
            "total_styles": len(db.styles),
            "total_images": len(db.images),
        },
        "sounds": [{
            "name": s.name, "category": s.category,
            "variants": s.variants, "file_source": s.file_source,
        } for s in db.sounds],
        "drum_kits": [{
            "name": k.name, "bank_msb": k.bank_msb, "bank_lsb": k.bank_lsb,
            "program": k.program, "key_count": len(k.key_map),
            "key_map": {str(k2): v for k2, v in k.key_map.items()},
        } for k in db.drum_kits],
        "styles": [{
            "name": s.name, "genre": s.genre, "file_source": s.file_source,
        } for s in db.styles],
        "images": [{
            "name": img.name, "size": img.size, "type": img.image_type,
        } for img in db.images],
    }


def generate_set_report_csv(output_path: Path, set_name: str = "PA800 Factory SET") -> Path:
    """Generiše CSV izvještaj zvukova."""
    db = get_database()
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Naziv', 'Kategorija', 'Varijante', 'Izvor'])
        for s in db.sounds:
            writer.writerow([s.name, s.category, ", ".join(s.variants), s.file_source])
    return output_path


def main():
    import argparse
    parser = argparse.ArgumentParser(prog="korg_pa_set_report")
    parser.add_argument("--html", type=Path, help="Sačuvaj HTML izvještaj")
    parser.add_argument("--json", type=Path, help="Sačuvaj JSON izvještaj")
    parser.add_argument("--csv", type=Path, help="Sačuvaj CSV izvještaj")
    parser.add_argument("--name", default="PA800 Factory SET")
    args = parser.parse_args()
    outputs = []
    if args.html:
        html_content = generate_set_report_html(args.name)
        args.html.write_text(html_content, encoding='utf-8')
        outputs.append(str(args.html))
    if args.json:
        json_data = generate_set_report_json(args.name)
        args.json.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding='utf-8')
        outputs.append(str(args.json))
    if args.csv:
        generate_set_report_csv(args.csv, args.name)
        outputs.append(str(args.csv))
    if not outputs:
        # Default: print summary
        report = generate_set_report_json(args.name)
        print(f"SET: {report['summary']}")
    else:
        for out in outputs:
            print(f"Saved: {out}")


if __name__ == "__main__":
    main()
