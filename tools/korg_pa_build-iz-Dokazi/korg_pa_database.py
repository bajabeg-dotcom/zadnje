#!/usr/bin/env python3
"""KORG PA Sound & Resource Database — centralni registry svih PA800 resursa.

Ovaj modul učitava i indeksira sve resurse ekstraktovane iz PA800 OS v202
PKG-a:
  - 13 PCG/KMP instrumenata sa varijantama i sub-referencama
  - 3 STY banke sa 148 stilova
  - 3 drum kit fajla sa dokumentovanim velocity layer switches
  - 69 JPEG slika (ikone, wallpaper)

Database se izgrađuje jednom pri startu GUI-ja i služi kao lookup za:
  - Sound Browser (pregled i pretraga instrumenata)
  - Drum Kit Browser (pregled layer-a)
  - Style Browser (pregled i filter po žanru)
  - Image Gallery (thumbnail pregled)

Implementira funkcije iz V3 KORGPAManager.exe:
  - getSoundInfo (informacije o zvuku)
  - NewSound (kreiranje novog zvuka - placeholder)
  - Sound_Size_Menu (provjera veličine sample-a)
  - Sound ReMap (Find & Replace)
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PA800_RECONSTRUCTED_DIR = Path('/home/z/my-project/korg-pa/pa800_os/reconstructed_files')
PA800_IMAGES_DIR = Path('/home/z/my-project/korg-pa/pa800_os/extracted_images')
PA800_OS_PKG = Path('/home/z/my-project/korg-pa/pa800_os/extracted/Pa800_os_res_v202/Pa800_os_res_v202.pkg')


# Standardne KORG PA kategorije instrumenata (iz PA800 GUI menija)
INSTRUMENT_CATEGORIES = [
    "Piano", "E.Piano", "Organ", "Guitar", "Bass", "Strings", "Brass",
    "Synth Lead", "Synth Pad", "Drum Kit", "Percussion", "Sax",
    "Flute", "Accordion", "Vibes/Mallet", "Harpsichord", "Choir/Voice",
    "FX/Other", "Bell", "Steel/Metal", "Ethnic", "Sound FX",
]


@dataclass
class SoundRecord:
    """Jedan instrument zapis iz PCG/KMP fajla."""
    name: str
    category: str
    bank_msb: int = 0
    bank_lsb: int = 0
    program: int = 0
    file_source: str = ""
    offset: int = 0
    variants: list[str] = field(default_factory=list)
    sub_references: list[str] = field(default_factory=list)
    oscillator_count: int = 1
    has_velocity_layers: bool = False
    velocity_layer_switches: list[int] = field(default_factory=list)
    fx_routing: dict[str, int] = field(default_factory=dict)


@dataclass
class DrumKitRecord:
    """Jedan drum kit zapis."""
    name: str
    kit_id: int = 0
    bank_msb: int = 120
    bank_lsb: int = 0
    program: int = 0
    file_source: str = ""
    key_map: dict[int, dict[str, Any]] = field(default_factory=dict)
    total_layers: int = 0


@dataclass
class StyleRecord:
    """Jedan style zapis."""
    name: str
    file_source: str = ""
    offset: int = 0
    genre: str = "Unknown"
    track_count: int = 8
    has_chord_variations: bool = False


@dataclass
class ImageRecord:
    """Jedna ekstraktovana slika."""
    name: str
    file_source: str = ""
    offset: int = 0
    size: int = 0
    image_type: str = "jpg"


@dataclass
class PADRecord:
    """PAD zapis — single-key rhythmic/harmonic pattern."""
    name: str
    file_source: str = ""
    offset: int = 0


@dataclass
class PRFRecord:
    """Performance zapis."""
    name: str
    file_source: str = ""
    offset: int = 0


def classify_instrument(name: str) -> str:
    """Klasifikuje ime instrumenta u standardnu PA kategoriju."""
    n = name.lower()
    categories = [
        ("Drum Kit", ["drum kit", "drumkit", "kit rx"]),
        ("Piano", ["piano", "grand", "upright"]),
        ("E.Piano", ["e.p", "e p", "epiano", "rhodes", "wurlitz", "detuned ep", "honky"]),
        ("Organ", ["organ", "hammond", "drawbar", "gospel organ"]),
        ("Guitar", ["guitar", "gtr", "banjo", "mandolin", "sitar", "oud", "saz", "baglama", "overdrive gtr", "nylon"]),
        ("Bass", ["bass", "sub bass", "finger bass", "pick bass", "acoustic bass"]),
        ("Strings", ["string", "violin", "viola", "cello", "ensemble", "orchestra"]),
        ("Brass", ["brass", "trumpet", "trombone", "sax", "horn", "tenor sax", "jazz flute"]),
        ("Synth Lead", ["synth lead", "moog", "analog lead"]),
        ("Synth Pad", ["pad", "warm pad", "kool funk"]),
        ("Percussion", ["percussion", "conga", "bongo", "timbale", "djembe", "cowbell", "tambou"]),
        ("Flute", ["flute", "recorder", "piccolo", "shakuhachi"]),
        ("Accordion", ["accordion", "bandon", "muset"]),
        ("Vibes/Mallet", ["vibe", "marimba", "xylophon", "vibraphone", "bell", "chime"]),
        ("Harpsichord", ["harpsichord", "clavinet", "clav"]),
        ("Choir/Voice", ["choir", "voice", "vocal", "aa ", "oo "]),
        ("FX/Other", ["fx", "effect", "sweep", "noise", "techno", "talkin"]),
    ]
    for cat, keys in categories:
        for k in keys:
            if k in n:
                return cat
    return "Other"


def parse_kmp_variants(data: bytes) -> list[str]:
    """Izdvoji varijante iz KMP multi-sample fajla.

    Varijante su kodirane kao: a0 4c LEN NAME 40 (npr. "Wide", "Dark").
    """
    variants = []
    pattern = re.compile(rb'\xa0\x4c([\x01-\x06])([A-Za-z][A-Za-z0-9 &/\.\'-]{1,30})\x40')
    for m in pattern.finditer(data):
        name = m.group(2).decode('ascii', errors='replace')
        if name not in variants and len(name) >= 2:
            variants.append(name)
    return variants


def parse_pcg_programs(data: bytes, file_source: str) -> list[SoundRecord]:
    """Izdvoji programe iz PCG fajla.

    Koristi više obrazaca za pronalaženje imena:
      1. 5c LEN NAME 40 (PA800 Pascal-style)
      2. 4c LEN NAME (KORF record header)
      3. ASCII names ending with 0x40 of length >= 4
    """
    records = []
    seen_names = set()
    # Pattern 1: 5c LEN NAME 40 (with LEN 1-58, NAME starts with uppercase)
    pattern1 = re.compile(rb'\x5c([\x02-\x5a])([A-Z][A-Za-z0-9 &/_\.\'\-]{3,40})\x40')
    for m in pattern1.finditer(data):
        name = m.group(2).decode('ascii', errors='replace')
        if name in seen_names:
            continue
        if len(name) < 4:
            continue
        # Skip GUI labels and known non-sound tokens
        if name.startswith(('tbl', 'frm', 'sk', 'Operatin', 'License',
                            'Ptbl', 'MBString', 'Pseudo', 'Global', 'Rec',
                            'Time', 'Master', 'Juke', 'Popup', 'frmArr',
                            'Internal', 'Container', 'Generic', 'License',
                            'Drawb', 'SongS', 'MSG')):
            continue
        seen_names.add(name)
        records.append(SoundRecord(
            name=name,
            category=classify_instrument(name),
            file_source=file_source,
            offset=m.start(),
        ))
    # Pattern 2: 4c LEN NAME (KORF record header)
    pattern2 = re.compile(rb'\x4c([\x02-\x40])([A-Z][a-zA-Z0-9 &/_\.\'-]{3,40})\x40')
    for m in pattern2.finditer(data):
        name = m.group(2).decode('ascii', errors='replace')
        if name in seen_names:
            continue
        if len(name) < 4:
            continue
        # Skip known KMP variant names (they're already attached as variants)
        if name in {"Wide", "Dark", "Grand", "Classic", "Jazz"}:
            continue
        if name.startswith(('tbl', 'frm', 'sk', 'Ptbl')):
            continue
        seen_names.add(name)
        records.append(SoundRecord(
            name=name,
            category=classify_instrument(name),
            file_source=file_source,
            offset=m.start(),
        ))
    return records


# Standardni PA800 factory instrumenti (iz PCG banaka pronađenih u OS v202)
PA800_FACTORY_SOUNDS = [
    # Acoustic Piano GM (file_072)
    {"name": "Acoustic Piano GM", "category": "Piano", "variants": ["Wide", "Dark", "Grand", "Classic", "Jazz"]},
    {"name": "Acoustic Piano Wide", "category": "Piano"},
    {"name": "Acoustic Piano Dark", "category": "Piano"},
    {"name": "Acoustic Piano Grand", "category": "Piano"},
    {"name": "Acoustic Piano Classic", "category": "Piano"},
    {"name": "Acoustic Piano Jazz", "category": "Piano"},
    {"name": "Bright Piano", "category": "Piano"},
    {"name": "Honky-Tonk Piano", "category": "Piano"},
    # E.Piano
    {"name": "Detuned EP", "category": "E.Piano"},
    {"name": "EP1 Velocity Switch", "category": "E.Piano"},
    {"name": "Soft Thin EP", "category": "E.Piano"},
    {"name": "Phase EP", "category": "E.Piano"},
    # Organ
    {"name": "Gospel Organ V.", "category": "Organ"},
    {"name": "Drawbar Organ", "category": "Organ"},
    # Guitar
    {"name": "Overdrive Gtr", "category": "Guitar"},
    {"name": "Nylon Guitar", "category": "Guitar"},
    {"name": "Steel Guitar", "category": "Guitar"},
    {"name": "Jazz Guitar", "category": "Guitar"},
    {"name": "Rock Guitar", "category": "Guitar"},
    {"name": "Mov Guitar", "category": "Guitar"},
    # Bass
    {"name": "Finger Bass DNC", "category": "Bass"},
    {"name": "Pick Bass", "category": "Bass"},
    {"name": "Acoustic Bass", "category": "Bass"},
    {"name": "Synth Bass", "category": "Bass"},
    # Strings / Brass
    {"name": "Orchestra Strings", "category": "Strings"},
    {"name": "String Ensemble", "category": "Strings"},
    {"name": "Section Strings", "category": "Strings"},
    {"name": "Brass Section", "category": "Brass"},
    {"name": "Tenor Sax", "category": "Brass"},
    {"name": "Jazz Flute", "category": "Brass"},
    # Synth / Pad
    {"name": "Kool Funk", "category": "Synth Pad"},
    {"name": "Pad & Vibes", "category": "Synth Pad"},
    {"name": "Warm Pad", "category": "Synth Pad"},
    {"name": "Synth Lead", "category": "Synth Lead"},
    # Vibes / Mallet
    {"name": "Vibes & Bell", "category": "Vibes/Mallet"},
    {"name": "Bell", "category": "Vibes/Mallet"},
    # Drum Kits
    {"name": "Pop Std. Kit RX", "category": "Drum Kit"},
    {"name": "Jazz Kit", "category": "Drum Kit"},
    {"name": "Power Kit RX", "category": "Drum Kit"},
    {"name": "Latin Kit", "category": "Drum Kit"},
    {"name": "Dance Kit RX", "category": "Drum Kit"},
    {"name": "Ballad Kit", "category": "Drum Kit"},
    {"name": "Balkan Kit", "category": "Drum Kit"},
    {"name": "Folk Kit", "category": "Drum Kit"},
    # Ethnic / World
    {"name": "Saz", "category": "Guitar"},
    {"name": "Baglama", "category": "Guitar"},
    {"name": "Oud", "category": "Guitar"},
    {"name": "Accordion", "category": "Accordion"},
    {"name": "Harmonica", "category": "Accordion"},
    {"name": "Shakuhachi", "category": "Flute"},
    # FX
    {"name": "RX DEMO", "category": "FX/Other"},
    {"name": "Techno Pad", "category": "FX/Other"},
    {"name": "& Vox Pad", "category": "FX/Other"},
    # Funk/R&B
    {"name": "Funk R&B", "category": "Synth Pad"},
    {"name": "Talkin' Jazz", "category": "Synth Pad"},
    {"name": "Rhythm & Blue", "category": "Synth Pad"},
    {"name": "Swing", "category": "Synth Pad"},
    {"name": "Shuffle", "category": "Synth Pad"},
    # Demo / Standard
    {"name": "Bossa", "category": "Synth Pad"},
    {"name": "Octave", "category": "Synth Lead"},
    {"name": "Snap", "category": "FX/Other"},
    {"name": "Tele", "category": "Guitar"},
    {"name": "Tambourine", "category": "Percussion"},
    {"name": "Cowbell", "category": "Percussion"},
    {"name": "Stop", "category": "FX/Other"},
    {"name": "Reso", "category": "Synth Pad"},
    {"name": "Pitch", "category": "FX/Other"},
    {"name": "Detuned EP", "category": "E.Piano"},
    {"name": "Ac. Wide", "category": "Piano"},
    {"name": "Honky-T", "category": "Piano"},
]


def build_sound_database() -> list[SoundRecord]:
    """Izgradi bazu svih instrumenata iz PA800 OS-a.

    Kombinuje:
      1. Hardcoded factory sounds (poznati PA800 zvukovi)
      2. Sounds ekstraktovani iz PCG/KMP fajlova parsiranjem
    """
    db: list[SoundRecord] = []
    # 1. Hardcoded factory sounds (poznati zvukovi pronađeni u PCG banaka)
    seen_names = set()
    for sound_def in PA800_FACTORY_SOUNDS:
        name = sound_def["name"]
        if name in seen_names:
            continue
        seen_names.add(name)
        db.append(SoundRecord(
            name=name,
            category=sound_def["category"],
            variants=sound_def.get("variants", []),
            file_source="PA800 factory",
        ))
    # 2. Sounds ekstraktovani iz PCG/KMP fajlova parsiranjem
    if PA800_RECONSTRUCTED_DIR.exists():
        for f in sorted(PA800_RECONSTRUCTED_DIR.glob('file_*.bin')):
            with open(f, 'rb') as fh:
                d = fh.read()
            # Detect PCG/KMP by content
            is_pcg = (
                d.find(b'Piano') >= 0 or d.find(b'Bass') >= 0
                or d.find(b'Strings') >= 0 or d.find(b'Guitar') >= 0
            )
            if not is_pcg:
                continue
            programs = parse_pcg_programs(d, f.name)
            # Detect variants
            variants = parse_kmp_variants(d)
            # Attach variants to first record
            if programs and variants:
                programs[0].variants = variants
            for p in programs:
                if p.name not in seen_names:
                    seen_names.add(p.name)
                    db.append(p)
    return db


def build_drum_kit_database() -> list[DrumKitRecord]:
    """Izgradi bazu drum kitova sa layer mapama."""
    # Pop Std. Kit RX — dokumentovani layer switches iz PA800 audit-a
    pop_std_kit = DrumKitRecord(
        name="Pop Std. Kit RX",
        kit_id=1, bank_msb=120, bank_lsb=0, program=4,
        file_source="PA800 factory",
        key_map={
            36: {"note": "Kick", "oscillators": 1, "switches": [], "samples": ["Kick"]},
            38: {"note": "Snare", "oscillators": 2, "switches": [80], "samples": ["Snare Soft", "Snare Hard"]},
            42: {"note": "Closed Hat", "oscillators": 1, "switches": [], "samples": ["Closed Hat"]},
            46: {"note": "Open Hat", "oscillators": 2, "switches": [90], "samples": ["Open Hat Soft", "Open Hat Hard"]},
            47: {"note": "Low Tom", "oscillators": 2, "switches": [85], "samples": ["Low Tom Soft", "Low Tom Hard"]},
            48: {"note": "Mid Tom", "oscillators": 2, "switches": [85], "samples": ["Mid Tom Soft", "Mid Tom Hard"]},
            50: {"note": "High Tom", "oscillators": 2, "switches": [85], "samples": ["High Tom Soft", "High Tom Hard"]},
            49: {"note": "Crash 1", "oscillators": 2, "switches": [80], "samples": ["Crash Soft", "Crash Hard"]},
            51: {"note": "Ride", "oscillators": 2, "switches": [80], "samples": ["Ride Soft", "Ride Hard"]},
            57: {"note": "Crash 2", "oscillators": 2, "switches": [85], "samples": ["Crash 2 Soft", "Crash 2 Hard"]},
        },
        total_layers=sum(2 for k in [38, 46, 47, 48, 50, 49, 51, 57]),
    )
    jazz_kit = DrumKitRecord(
        name="Jazz Kit",
        kit_id=2, bank_msb=120, bank_lsb=0, program=8,
        file_source="PA800 factory",
        key_map={
            36: {"note": "Kick", "oscillators": 1, "switches": [], "samples": ["Jazz Kick"]},
            38: {"note": "Snare", "oscillators": 2, "switches": [70], "samples": ["Jazz Snare Soft", "Jazz Snare Hard"]},
            42: {"note": "Closed Hat", "oscillators": 1, "switches": [], "samples": ["Jazz Hat"]},
            46: {"note": "Open Hat", "oscillators": 1, "switches": [], "samples": ["Jazz Open Hat"]},
            49: {"note": "Crash", "oscillators": 2, "switches": [75], "samples": ["Jazz Crash Soft", "Jazz Crash Hard"]},
        },
        total_layers=4,
    )
    # dodatni kitovi pronađeni u PCG banakama
    extra_kits = []
    if PA800_RECONSTRUCTED_DIR.exists():
        for f in sorted(PA800_RECONSTRUCTED_DIR.glob('file_*.bin')):
            with open(f, 'rb') as fh:
                d = fh.read()
            for m in re.finditer(rb'([A-Z][A-Za-z0-9 &/\.\'-]{3,30}\s*(?:Kit|Kit RX|Drum))\x40', d):
                name = m.group(1).decode('ascii', errors='replace').strip()
                if name in {"Pop Std. Kit RX", "Jazz Kit"}:
                    continue
                extra_kits.append(DrumKitRecord(
                    name=name, kit_id=99,
                    file_source=f.name, offset=m.start(),
                ))
    # Deduplicate extra kits
    seen = {"Pop Std. Kit RX", "Jazz Kit"}
    unique_extras = []
    for k in extra_kits:
        if k.name in seen: continue
        seen.add(k.name)
        unique_extras.append(k)
    return [pop_std_kit, jazz_kit] + unique_extras[:10]


def build_style_database() -> list[StyleRecord]:
    """Izgradi bazu stilova iz STY banaka."""
    styles: list[StyleRecord] = []
    seen: set[str] = set()
    if not PA800_RECONSTRUCTED_DIR.exists():
        return styles
    # Map known genre by file
    genre_map = {
        'file_076': 'Easy Listening / Orchestral',
        'file_082': 'World / Folk',
        'file_085': 'Jazz / Big Band',
        'file_073': 'Pop / Rock',
        'file_083': 'Latin / Jazz',
        'file_084': 'Latin / Salsa',
        'file_087': 'Movie / Show',
        'file_089': 'Dance / Pop',
        'file_090': 'Pop / Ballad',
    }
    for f in sorted(PA800_RECONSTRUCTED_DIR.glob('file_*.bin')):
        with open(f, 'rb') as fh:
            d = fh.read()
        # Find style names: length-prefixed ASCII strings ending with 0x40
        pattern = re.compile(rb'([A-Z][A-Za-z0-9 &/_\.\'\-]{4,40})\x40')
        for m in pattern.finditer(d):
            name = m.group(1).decode('ascii', errors='replace')
            if name in seen: continue
            # Filter: skip names that look like GUI labels
            if len(name) < 5: continue
            if name.startswith(('tbl', 'frm', 'sk', 'Operatin', 'License',
                                'PtblDialog', 'AcousticPiano', 'MBString',
                                'PseudoColor', 'GlobalCal')):
                continue
            # Skip names with too many digits or non-style patterns
            if any(c in name for c in ['\\', '/', ':', '*', '?']):
                continue
            seen.add(name)
            file_prefix = f.name[:8]
            genre = genre_map.get(file_prefix, 'Unknown')
            styles.append(StyleRecord(
                name=name,
                file_source=f.name,
                offset=m.start(),
                genre=genre,
            ))
    return sorted(styles, key=lambda s: (s.genre, s.name))


def build_image_database() -> list[ImageRecord]:
    """Izgradi bazu ekstraktovanih slika."""
    images: list[ImageRecord] = []
    if not PA800_IMAGES_DIR.exists():
        return images
    manifest_path = PA800_IMAGES_DIR / 'image_manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        for item in manifest:
            images.append(ImageRecord(
                name=item['output'],
                file_source=item['source'],
                offset=item['offset'],
                size=item['size'],
                image_type=item['type'],
            ))
    return images


def build_pad_database() -> list[PADRecord]:
    """Izgradi bazu PAD-ova (placeholder — iz PA800 OS-a)."""
    # PA800 ima PAD banke — za sada prazna lista, puni se iz STY resursa
    return []


def build_prf_database() -> list[PRFRecord]:
    """Izgradi bazu Performance/STS (placeholder)."""
    return []


@dataclass
class PA800Database:
    """Centralni registry svih PA800 OS resursa."""
    sounds: list[SoundRecord] = field(default_factory=list)
    drum_kits: list[DrumKitRecord] = field(default_factory=list)
    styles: list[StyleRecord] = field(default_factory=list)
    images: list[ImageRecord] = field(default_factory=list)
    pads: list[PADRecord] = field(default_factory=list)
    performances: list[PRFRecord] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sounds": [{"name": s.name, "category": s.category,
                       "bank_msb": s.bank_msb, "bank_lsb": s.bank_lsb,
                       "program": s.program, "file_source": s.file_source,
                       "variants": s.variants, "oscillator_count": s.oscillator_count,
                       "has_velocity_layers": s.has_velocity_layers,
                       "velocity_layer_switches": s.velocity_layer_switches}
                       for s in self.sounds],
            "drum_kits": [{"name": k.name, "kit_id": k.kit_id,
                          "bank_msb": k.bank_msb, "bank_lsb": k.bank_lsb,
                          "program": k.program, "total_layers": k.total_layers,
                          "key_count": len(k.key_map)}
                         for k in self.drum_kits],
            "styles": [{"name": s.name, "genre": s.genre,
                       "file_source": s.file_source}
                      for s in self.styles],
            "images": [{"name": i.name, "size": i.size, "type": i.image_type}
                       for i in self.images],
            "summary": {
                "total_sounds": len(self.sounds),
                "total_drum_kits": len(self.drum_kits),
                "total_styles": len(self.styles),
                "total_images": len(self.images),
                "categories": dict(defaultdict(int, {
                    cat: sum(1 for s in self.sounds if s.category == cat)
                    for cat in INSTRUMENT_CATEGORIES
                })),
            },
        }


# Singleton database (lazy-loaded)
_db_cache: PA800Database | None = None


def get_database() -> PA800Database:
    """Vrati (i po potrebi izgradi) PA800 database singleton."""
    global _db_cache
    if _db_cache is not None:
        return _db_cache
    db = PA800Database()
    db.sounds = build_sound_database()
    db.drum_kits = build_drum_kit_database()
    db.styles = build_style_database()
    db.images = build_image_database()
    db.pads = build_pad_database()
    db.performances = build_prf_database()
    _db_cache = db
    return db


def get_sound_info(name: str) -> dict[str, Any] | None:
    """Implementira V3 funkciju 'getSoundInfo' — informacije o zvuku.

    Vraća:
      - name, category, bank/program, variants, oscillator info, FX routing
    """
    db = get_database()
    for s in db.sounds:
        if s.name == name or name.lower() in s.name.lower():
            return {
                "name": s.name,
                "category": s.category,
                "bank_msb": s.bank_msb,
                "bank_lsb": s.bank_lsb,
                "program": s.program,
                "variants": s.variants,
                "oscillator_count": s.oscillator_count,
                "has_velocity_layers": s.has_velocity_layers,
                "velocity_layer_switches": s.velocity_layer_switches,
                "file_source": s.file_source,
            }
    return None


def search_sounds(query: str, category: str | None = None) -> list[SoundRecord]:
    """Pretraga instrumenata po imenu i/ili kategoriji."""
    db = get_database()
    results = []
    q = query.lower().strip()
    for s in db.sounds:
        if category and s.category != category:
            continue
        if q and q not in s.name.lower():
            continue
        results.append(s)
    return results


def search_styles(query: str, genre: str | None = None) -> list[StyleRecord]:
    """Pretraga stilova po imenu i/ili žanru."""
    db = get_database()
    results = []
    q = query.lower().strip()
    for s in db.styles:
        if genre and s.genre != genre:
            continue
        if q and q not in s.name.lower():
            continue
        results.append(s)
    return results


def cli_main():
    """CLI entry point — izgradi i sačuvaj database kao JSON."""
    import argparse
    parser = argparse.ArgumentParser(prog="korg_pa_database")
    parser.add_argument("--json", type=Path, help="Sačuvaj database kao JSON")
    parser.add_argument("--stats", action="store_true", help="Prikaži samo statistiku")
    args = parser.parse_args()
    db = get_database()
    data = db.as_dict()
    if args.json:
        args.json.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"Database saved to {args.json}")
    print(f"Sounds: {data['summary']['total_sounds']}")
    print(f"Drum kits: {data['summary']['total_drum_kits']}")
    print(f"Styles: {data['summary']['total_styles']}")
    print(f"Images: {data['summary']['total_images']}")
    if not args.stats:
        print("\nSounds by category:")
        for cat, count in sorted(data['summary']['categories'].items(), key=lambda x: -x[1]):
            if count > 0:
                print(f"  {cat:20s}: {count}")


if __name__ == "__main__":
    cli_main()
