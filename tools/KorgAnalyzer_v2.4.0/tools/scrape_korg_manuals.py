#!/usr/bin/env python3
"""Build the manual catalogue by resolving official Korg download pages.

Reads each product page, lists its manual entries, then resolves each entry to
the direct CDN PDF URL.  Results are cached in korg_manuals_raw.json.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BASE = "https://www.korg.com"

PRODUCTS: dict[str, str] = {
    "1/184": "Pa1X", "1/185": "Pa1X Pro", "1/255": "Pa1X Pro Elite",
    "1/186": "Pa2X Pro",
    "0/307": "Pa3X", "1/297": "Pa3X MUSIKANT", "0/366": "Pa3X Le", "1/298": "Pa3X Le MUSIKANT",
    "0/90": "Pa600", "0/305": "Pa600QT", "1/299": "Pa600 MUSIKANT",
    "1/193": "Pa800",
    "0/89": "Pa900", "1/301": "Pa900 MUSIKANT", "1/302": "Pa900 TK",
    "0/516": "Pa4X", "0/572": "Pa4X ORIENTAL", "1/286": "Pa4X MUSIKANT",
    "0/756": "Pa700", "0/759": "Pa700 ORIENTAL", "0/834": "Pa700 RD",
    "0/758": "Pa1000",
    "0/895": "Pa5X", "0/958": "Pa5X ORIENTAL", "1/448": "Pa5X MUSIKANT",
    "0/359": "Pa300", "1/294": "Pa300 Serbia Edition",
    "1/188": "Pa500", "1/189": "Pa500 ORIENTAL", "1/190": "Pa588",
    "1/187": "Pa50", "0/312": "Pa50SD", "1/191": "Pa60", "1/192": "Pa80",
}

# Only English-language, documentation-grade entries are resolved.
KEEP = re.compile(
    r"(owner'?s manual|user manual|reference manual|advanced edit|quick guide|"
    r"easy start|data list|performance guide|songbook editor|comparison chart|"
    r"new features|addendum|operation guide|parameters guide|voice list|style list)",
    re.I,
)
DROP = re.compile(r"\((french|german|italian|spanish|deutsch|français|español|italiano|chinese|turkish|portuguese|russian|japanese|korean)\)", re.I)

_print_lock = threading.Lock()
_cache_lock = threading.Lock()


def get(url: str, attempts: int = 3) -> str:
    last = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45) as response:
                return response.read().decode("utf-8", "replace")
        except Exception as error:  # noqa: BLE001
            last = error
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def list_manuals(cat: str, pid: str) -> list[dict[str, str]]:
    url = f"{BASE}/us/support/download/product/{cat}/{pid}/"
    src = get(url)
    found: list[dict[str, str]] = []
    for match in re.finditer(
        rf'href="(/us/support/download/manual/{cat}/{pid}/(\d+)/)"[^>]*>(.*?)</a>', src, re.S
    ):
        rel, mid, inner = match.group(1), match.group(2), match.group(3)
        text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", inner))).strip()
        date = re.search(r"(\d{4}\.\d{2}\.\d{2})", text)
        size = re.search(r"(PDF|ZIP|EXE)\s*:\s*([\d.,]+\s*[KMG]B)", text, re.I)
        title = text.split(" " + (date.group(1) if date else "\0"))[0].strip()
        found.append({
            "page": BASE + rel,
            "title": title,
            "date": date.group(1) if date else None,
            "size": size.group(2) if size else None,
            "kind": size.group(1).upper() if size else None,
        })
    return found


def resolve(entry: dict[str, str]) -> dict[str, str]:
    if entry.get("kind") != "PDF":
        return entry
    try:
        src = get(entry["page"])
    except Exception as error:  # noqa: BLE001
        entry["error"] = str(error)
        return entry
    direct = re.search(r'href="(https://cdn\.korg\.com/[^"]+\.pdf[^"]*)"', src)
    if direct:
        from urllib.parse import unquote
        raw_href = html.unescape(direct.group(1))
        # The real filename rides along in the signed Content-Disposition
        # parameter, percent-encoded twice; decode before reading it.
        decoded = unquote(raw_href)
        filename = re.search(r"filename\*?=(?:UTF-8'')?([^&;]+)", decoded)
        if filename:
            entry["filename"] = unquote(filename.group(1)).strip("'\" ")
        entry["direct"] = raw_href.split("?")[0]
    return entry


def main() -> None:
    cache_path = Path("korg_manuals_raw.json")
    cache: dict[str, list[dict[str, str]]] = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())

    def work(item: tuple[str, str]) -> tuple[str, list[dict[str, str]]]:
        key, name = item
        cat, pid = key.split("/")
        with _print_lock:
            print(f"listing {name}", flush=True)
        entries = list_manuals(cat, pid)
        keep = [e for e in entries if KEEP.search(e["title"]) and not DROP.search(e["title"])]
        with ThreadPoolExecutor(max_workers=6) as pool:
            keep = list(pool.map(resolve, keep))
        return key, keep

    keys = [k for k in PRODUCTS if k not in cache]
    with ThreadPoolExecutor(max_workers=5) as pool:
        for key, entries in pool.map(work, [(k, PRODUCTS[k]) for k in keys]):
            cache[key] = entries
            cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
            resolved = sum(1 for e in entries if e.get("direct"))
            with _print_lock:
                print(f"  -> {PRODUCTS[key]}: {len(entries)} entries, {resolved} direct PDF", flush=True)

    total = sum(len(v) for v in cache.values())
    direct = sum(1 for v in cache.values() for e in v if e.get("direct"))
    print(f"\nTOTAL: {len(cache)} products, {total} entries, {direct} resolved direct PDF URLs")


if __name__ == "__main__":
    main()
