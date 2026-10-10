#!/usr/bin/env python3
"""Turn the scraped Korg/Yamaha/Roland raw data into the shipping catalogue.

Produces ``korg_binary_analyzer/data/manuals.json``: a curated, verified list of
official manual PDFs plus the reference facts the analyzer can use.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).parent
OUT = Path("/home/user/project/korg_binary_analyzer/data/manuals.json")

KORG_FAMILY = {
    "Pa1X": ("Pa1X", "2003"), "Pa1X Pro": ("Pa1X", "2003"), "Pa1X Pro Elite": ("Pa1X", "2003"),
    "Pa2X Pro": ("Pa2X", "2008"),
    "Pa800": ("Pa800", "2007"),
    "Pa500": ("Pa500", "2008"), "Pa500 ORIENTAL": ("Pa500", "2008"), "Pa588": ("Pa500", "2008"),
    "Pa3X": ("Pa3X", "2011"), "Pa3X MUSIKANT": ("Pa3X", "2011"),
    "Pa3X Le": ("Pa3X", "2013"), "Pa3X Le MUSIKANT": ("Pa3X", "2013"),
    "Pa600": ("Pa600", "2012"), "Pa600QT": ("Pa600", "2012"), "Pa600 MUSIKANT": ("Pa600", "2012"),
    "Pa900": ("Pa900", "2013"), "Pa900 MUSIKANT": ("Pa900", "2013"), "Pa900 TK": ("Pa900", "2013"),
    "Pa4X": ("Pa4X", "2015"), "Pa4X ORIENTAL": ("Pa4X", "2015"), "Pa4X MUSIKANT": ("Pa4X", "2015"),
    "Pa700": ("Pa700", "2017"), "Pa700 ORIENTAL": ("Pa700", "2017"), "Pa700 RD": ("Pa700", "2017"),
    "Pa1000": ("Pa1000", "2017"),
    "Pa300": ("Pa300", "2013"), "Pa300 Serbia Edition": ("Pa300", "2013"),
    "Pa5X": ("Pa5X", "2022"), "Pa5X ORIENTAL": ("Pa5X", "2022"), "Pa5X MUSIKANT": ("Pa5X", "2022"),
    "Pa80": ("Pa80", "2000"), "Pa60": ("Pa80", "2000"), "Pa50": ("Pa80", "2000"), "Pa50SD": ("Pa80", "2000"),
}

# Korg's own container layer, oldest to newest — the ladder this analyzer decodes.
KORG_CONTAINER_ERA = {
    "Pa1X": "BKP backup (fb-record) + OC20/OC31/OC32 + CBarc + XOR",
    "Pa2X": "same resource layer as Pa1X",
    "Pa800": "same resource layer as Pa1X",
    "Pa500": "same resource layer as Pa1X",
    "Pa3X": "pkg/upd installer wrapping OC20 pages, same OC31/OC32 payloads",
    "Pa600": "pkg/upd with a 16-byte MD5 header and zlib per blob, same resources",
    "Pa900": "same as Pa600",
    "Pa4X": "lfo-pkg installer (Blowfish CBC family key), same OC31/OC32 payloads",
    "Pa700": "same as Pa4X",
    "Pa1000": "same as Pa4X",
    "Pa300": "same as Pa600",
    "Pa5X": "new-generation container, PA-manager tooling",
    "Pa80": "pre-OC era, DISK/INDX update files",
}

#: Depth matters: a 1650-page Pa5X User Manual is reference-grade, while a
#: Yamaha "Owner's Manual" is an introduction and its "Reference Manual" is the
#: deep one.  The depth is therefore decided per brand, not per document name.
KORG_REFERENCE_DOCS = {
    "User Manual", "Owner's Manual", "Advanced Edit / Reference", "Data List",
    "Parameters Guide", "Pa Series Comparison Chart", "MIDI Implementation",
    "SongBook Editor Guide",
}
YAM_REFERENCE_DOCS = {
    "Reference Manual", "Data List", "MIDI Implementation",
    "Pre-installed Expansion Contents List", "MIDI Song File Editing",
}

YAM_DOC = [
    (r"owners?_manual|_om_", "Owner's Manual"),
    (r"reference_manual|_rm_", "Reference Manual"),
    (r"_dl_|data.?list", "Data List"),
    (r"precl", "Pre-installed Expansion Contents List"),
    (r"_fi_|firmware", "Firmware Installation Guide"),
    (r"_ig_", "Installation Guide"),
    (r"_mi_|midi_implementation", "MIDI Implementation"),
    (r"easy_upgrade", "Easy Upgrade Guide"),
    (r"midi_song_file_editing", "MIDI Song File Editing"),
    (r"conversion", "Pack Conversion Guide"),
    (r"pack_?data", "Pack Data List"),
]
YAM_DROP = re.compile(r"(brochure|catalog|_de\.|_fr\.|_es\.|_it\.|_cz\.|_hu\.|_ro\.|_ita\.|connect|usb_display|adapter_list|release_date|announcement|PKB)", re.I)
#: Standalone tools that ship alongside many instruments; they are not the
#: instrument manual and must be labelled as such.
YAM_SOFTWARE = [
    (r"audio_phraser", "Software: Audio Phraser"),
    (r"mega_enhancer", "Software: MegaEnhancer"),
    (r"midi_song_to_style", "Software: MIDI Song to Style"),
    (r"expansion_manager", "Software: Yamaha Expansion Manager"),
]

#: Accessories that ship with their own booklet; they are kept (they are real
#: official documents) but must not be mistaken for the instrument manual.
KORG_ACCESSORY = [
    (r"paas|speaker", "Accessory: PaAS Speaker"),
    (r"hdik|hd kit", "Accessory: HDIK-3 HD Kit"),
    (r"st-sv1|stand", "Accessory: ST-SV1 Stand"),
    (r"xvp-10|exp-2", "Accessory: Expression Pedal"),
    (r"vif4", "Accessory: VIF4 Video Interface"),
    (r"mp3", "Accessory: Dual MP3 Board"),
    (r"hard disk installation", "Accessory: Hard Disk Kit"),
    (r"battery", "Accessory: Battery Addendum"),
    (r"installing the music stand", "Accessory: Music Stand"),
]

KORG_DOC = [
    (r"user manual", "User Manual"),
    (r"owner'?s manual", "Owner's Manual"),
    (r"reference manual|advanced edit", "Advanced Edit / Reference"),
    (r"quick guide", "Quick Guide"),
    (r"easy start", "Easy Start Guide"),
    (r"data list|factory data list", "Data List"),
    (r"performance guide", "Performance Guide"),
    (r"songbook editor", "SongBook Editor Guide"),
    (r"comparison chart", "Pa Series Comparison Chart"),
    (r"new features|addendum|upgrade", "OS Update / Addendum"),
    (r"parameters guide", "Parameters Guide"),
]

ROLAND_KEEP = re.compile(
    r"owner|reference|manual|patch.?(list|data)|data.?list|midi|waveform|"
    r"style.{0,3}(tone|list)|parameter|addendum|_om|_rm|_mi|_eng\d|_ver\d",
    re.I,
)
ROLAND_DROP = re.compile(
    r"\b(french|german|italian|spanish|chinese|japanese|korean|portuguese|russian)\b|"
    r"^(?!english)(?:[a-z]{4,}) languages$",
    re.I,
)


#: Entries that name a language in the title are only kept when that language is
#: English; the catalogue is English-only so the document list stays meaningful.
NON_ENGLISH = re.compile(
    r"\b(french|français|german|deutsch|italian|italiano|spanish|español|"
    r"chinese|turkish|portuguese|russian|japanese|korean|dutch|polish)\b",
    re.I,
)


def korg_doc(title: str) -> str:
    low = title.lower()
    for pattern, label in KORG_ACCESSORY:
        if re.search(pattern, low):
            return label
    for pattern, label in KORG_DOC:
        if re.search(pattern, low):
            return label
    return "Manual"


def yamaha_doc(filename: str) -> str | None:
    low = filename.lower()
    for pattern, label in YAM_DOC:
        if re.search(pattern, low):
            return label
    return None


def main() -> None:
    korg_raw = json.loads((HERE / "korg_manuals_raw.json").read_text())
    yr_raw = json.loads((HERE / "yamaha_roland_raw.json").read_text())

    products = {}
    from scrape_korg_manuals import PRODUCTS

    for key, name in PRODUCTS.items():
        entries = korg_raw.get(key, [])
        if not entries:
            continue
        family, generation = KORG_FAMILY.get(name, (name, "unknown"))
        docs = []
        for entry in entries:
            if not entry.get("direct"):
                continue
            if NON_ENGLISH.search(entry["title"]) and "(english)" not in entry["title"].lower():
                continue
            if re.search(r"(quick guide|user manual)\s+(english|multilanguage)$", entry["title"], re.I):
                pass  # explicit English wording, keep
            docs.append({
                "doc": korg_doc(entry["title"]),
                "size": entry.get("size"),
                "date": entry.get("date"),
                "direct": entry["direct"],
                "page": entry.get("page"),
                "filename": entry.get("filename") or entry["direct"].rsplit("/", 1)[-1],
            })
        docs.sort(key=lambda d: (
            d["doc"].startswith("Accessory"),
            d["doc"] != "User Manual",
            d["doc"] != "Owner's Manual",
            d["doc"],
        ))
        if docs:
            for doc in docs:
                doc["depth"] = "reference" if doc["doc"] in KORG_REFERENCE_DOCS else "guide"
            products[name] = {
                "brand": "Korg", "family": family, "generation": generation,
                "container_era": KORG_CONTAINER_ERA.get(family, ""),
                "product_page": re.sub(r"/manual/(\d+)/(\d+)/.*$", r"/product/\1/\2/", entries[0].get("page", "")),
                "docs": docs,
            }

    # Yamaha: keep only real documentation, derive the doc type from the filename.
    for model, entries in yr_raw["yamaha"].items():
        docs = []
        for entry in entries:
            filename = entry.get("filename", "")
            if YAM_DROP.search(filename):
                continue
            label = None
            for pattern, software_label in YAM_SOFTWARE:
                if re.search(pattern, filename, re.I):
                    label = software_label
                    break
            label = label or yamaha_doc(filename)
            if not label:
                continue
            docs.append({
                "doc": label, "size": entry.get("size"), "date": None,
                "direct": entry["direct"],
                "page": f"https://usa.yamaha.com/products/musical_instruments/keyboards/arranger_workstations/{model.lower()}/downloads.html",
                "filename": filename,
            })
        # De-duplicate identical document types pointing at the same file
        seen = set()
        unique = []
        for doc in docs:
            key = (doc["doc"], doc["direct"])
            if key not in seen:
                seen.add(key)
                unique.append(doc)
        unique.sort(key=lambda d: (d["doc"] != "Owner's Manual", d["doc"]))
        for doc in unique:
            doc["depth"] = (
                "reference"
                if doc["doc"] in YAM_REFERENCE_DOCS and not doc["doc"].startswith("Software:")
                else "guide"
            )
        if unique:
            products[model] = {
                "brand": "Yamaha", "family": model, "generation": "",
                "container_era": "Yamaha uses .STY/.MID/.PRS files (not Korg containers)",
                "product_page": f"https://usa.yamaha.com/products/musical_instruments/keyboards/arranger_workstations/{model.lower()}/downloads.html",
                "docs": unique,
            }

    for slug, entries in yr_raw["roland"].items():
        docs = []
        for entry in entries:
            filename = entry.get("filename", "")
            title = entry.get("title", "").split("/", 1)[-1]
            language = (entry.get("language") or "").strip()
            if language and language.casefold() not in ("english", "1 language"):
                continue
            # Belt and braces: some listings omit the tag but the filename still
            # carries a two-letter language marker (g/f/i/s/chs/k/j).
            if language.casefold() != "english" and re.search(
                r"_(g|f|i|s|chs|k|j|p|n|d)\d{2,3}_[a-z]?\.pdf$", filename, re.I
            ):
                continue
            if ROLAND_DROP.search(filename) or ROLAND_DROP.search(title):
                continue
            if not ROLAND_KEEP.search(filename) and not ROLAND_KEEP.search(title):
                continue
            low = f"{title} {filename}".casefold()
            if "midi implementation" in low or re.search(r"_mi\b|_mi\.", low):
                label = "MIDI Implementation"
            elif "waveform" in low or "patch list" in low:
                label = "Waveform / Patch List"
            elif "style" in low and "list" in low:
                label = "Style / Tone List"
            elif "addendum" in low:
                label = "OS Update / Addendum"
            elif "reference" in low:
                label = "Reference Manual"
            elif "data" in low and "list" in low:
                label = "Data List"
            else:
                label = "Owner's Manual"
            docs.append({
                "doc": label, "size": None, "date": None, "direct": entry["direct"],
                "page": f"https://www.roland.com/global/support/by_product/{slug}/owners_manuals/",
                "filename": filename,
                "source_title": title or None,
            })
        seen = set()
        unique = []
        for doc in docs:
            key = (doc["doc"], doc["direct"])
            if key not in seen:
                seen.add(key)
                unique.append(doc)
        for doc in unique:
            doc["depth"] = "reference"
        if unique:
            products[slug.upper()] = {
                "brand": "Roland", "family": slug.upper(), "generation": "",
                "container_era": "Roland uses .STL/.UPG format on these models",
                "product_page": f"https://www.roland.com/global/support/by_product/{slug}/owners_manuals/",
                "docs": unique,
            }

    payload = {
        "schema": "korg-analyzer-manuals/1",
        "note": "Official manufacturer download links only. Nothing here is re-hosted.",
        "products": products,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False))

    by_brand: dict[str, int] = {}
    total_docs = 0
    for name, product in products.items():
        by_brand[product["brand"]] = by_brand.get(product["brand"], 0) + 1
        total_docs += len(product["docs"])
    print(f"wrote {OUT}")
    print(f"products: {len(products)}  {by_brand}")
    print(f"documents: {total_docs}")
    for name, product in sorted(products.items()):
        print(f"  {product['brand']:<7} {name:<24} {len(product['docs']):>2} docs")


if __name__ == "__main__":
    main()
