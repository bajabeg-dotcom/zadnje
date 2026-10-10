#!/usr/bin/env python3
"""Resolve official Yamaha and Roland arranger manual PDFs.

Yamaha serves PDFs straight from /files/download/... links on the product's
downloads.html page.  Roland lists manuals as detail pages that embed the final
static PDF URL, so those need one extra hop.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
YAM = "https://usa.yamaha.com"
ROL = "https://www.roland.com"

YAMAHA_MODELS = [
    ("Genos2", "arranger_workstations/genos2"),
    ("Genos", "arranger_workstations/genos"),
    ("Tyros5", "arranger_workstations/tyros5"),
    ("Tyros4", "arranger_workstations/tyros4"),
    ("PSR-SX900", "arranger_workstations/psr-sx900"),
    ("PSR-SX700", "arranger_workstations/psr-sx700"),
    ("PSR-SX600", "arranger_workstations/psr-sx600"),
    ("PSR-S975", "arranger_workstations/psr-s975"),
    ("PSR-S775", "arranger_workstations/psr-s775"),
    ("PSR-S970", "arranger_workstations/psr-s970"),
    ("PSR-S770", "arranger_workstations/psr-s770"),
    ("PSR-A5000", "arranger_workstations/psr-a5000"),
    ("PSR-A3000", "arranger_workstations/psr-a3000"),
    ("PSR-I500", "portable_keyboards/psr-i500"),
    ("PSR-EW425", "portable_keyboards/psr-ew425"),
]

ROLAND_MODELS = [
    "e-a7", "ea-7", "bk-5", "bk-3", "bk-7m", "g-70", "prelude",
    "e-09", "em-55", "va-7", "va-76", "e-96", "e-86",
]

YAM_KEEP = re.compile(r"(om|rm|dl|precl|_ig_|owners?_manual|reference|data.?list)", re.I)
YAM_DROP = re.compile(r"(_fr|_de|_es|_it|_pt|_ru|_jp|_zh|_nl|_sv|_da|_fi|_no|_pl|_tr|_kr)_", re.I)


def get(url: str, attempts: int = 3) -> str:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            if error.code in (404, 403):
                raise
            last = error
        except Exception as error:  # noqa: BLE001
            last = error
        time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def yamaha(model: str, path: str) -> list[dict[str, str]]:
    url = f"{YAM}/products/musical_instruments/keyboards/{path}/downloads.html"
    try:
        src = get(url)
    except Exception as error:  # noqa: BLE001
        return [{"title": f"{model}: page unavailable", "error": str(error)}]
    entries: list[dict[str, str]] = []
    for match in re.finditer(r'href="(/files/download/[^"]+\.pdf)"[^>]*>(.*?)</a>', src, re.S):
        rel, inner = match.group(1), match.group(2)
        title = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner))).strip()
        size = re.search(r"\[([\d.,]+\s*[KMG]B)\]", title)
        entries.append({
            "title": f"{model}/{title.split('[')[0].strip()}",
            "size": size.group(1) if size else None,
            "direct": YAM + rel,
            "filename": rel.rsplit("/", 1)[-1],
        })
    return entries


def roland_detail(rel: str) -> str | None:
    """Resolve one manual detail page to its static PDF URL."""
    try:
        src = get(ROL + rel)
    except Exception:  # noqa: BLE001
        return None
    pdf = re.search(r'(https://static\.roland\.com/[^"\']+\.pdf)', src)
    return pdf.group(1) if pdf else None


def roland(slug: str) -> list[dict[str, str]]:
    """The listing page carries the real title and language tag, so read it there."""
    url = f"{ROL}/global/support/by_product/{slug}/owners_manuals/"
    try:
        src = get(url)
    except Exception:  # noqa: BLE001
        return []
    pattern = re.compile(
        rf'href="(/global/support/by_product/{re.escape(slug)}/owners_manuals/[0-9a-f-]{{36}}/)"'
        r'(.*?)</li>',
        re.S,
    )
    found: list[dict[str, str]] = []
    for match in pattern.finditer(src):
        rel, block = match.group(1), match.group(2)
        text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", block))).strip()
        if not text:
            continue
        # Titles read like: "[English] E-A7 Owner's Manual" or "[2 languages] Patch List".
        # The tag can sit anywhere in the extracted text, so search rather than anchor.
        language = re.search(r"\[([^\]]{2,20})\]", text)
        lang = language.group(1).strip() if language else ""
        title = re.sub(r"\[[^\]]{2,20}\]", "", text).strip(" >").strip()
        title = re.sub(r"^[A-Z0-9\-]{2,12}>\s*", "", title).strip()
        found.append({"rel": rel, "title": title, "language": lang})
    entries: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        urls = list(pool.map(lambda item: roland_detail(item["rel"]), found))
    for item, pdf in zip(found, urls):
        if not pdf:
            continue
        entries.append({
            "title": f"{slug.upper()}/{item['title']}",
            "language": item["language"],
            "direct": pdf,
            "filename": pdf.rsplit("/", 1)[-1],
        })
    return entries


def main() -> None:
    out = {"yamaha": {}, "roland": {}}

    def do_yamaha(item: tuple[str, str]) -> tuple[str, list[dict[str, str]]]:
        model, path = item
        print(f"yamaha {model}", flush=True)
        return model, yamaha(model, path)

    with ThreadPoolExecutor(max_workers=5) as pool:
        for model, entries in pool.map(do_yamaha, YAMAHA_MODELS):
            out["yamaha"][model] = entries
            print(f"  -> {model}: {len(entries)}", flush=True)

    def do_roland(slug: str) -> tuple[str, list[dict[str, str]]]:
        print(f"roland {slug}", flush=True)
        return slug, roland(slug)

    with ThreadPoolExecutor(max_workers=5) as pool:
        for slug, entries in pool.map(do_roland, ROLAND_MODELS):
            if entries:
                out["roland"][slug] = entries
            print(f"  -> {slug}: {len(entries)}", flush=True)

    Path("yamaha_roland_raw.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    for brand, models in out.items():
        total = sum(len(v) for v in models.values())
        direct = sum(1 for v in models.values() for e in v if e.get("direct"))
        print(f"{brand}: {len(models)} models, {total} entries, {direct} direct PDF")


if __name__ == "__main__":
    main()
