#!/usr/bin/env python3
"""Verify every catalogue link still resolves. Network required; read-only.

Reports broken links so a regenerated catalogue can be re-checked before release.

    python tools/verify_links.py            # check all
    python tools/verify_links.py --brand Korg
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from korg_binary_analyzer.manuals import load_catalog, parse_size  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def check(job):
    model, brand, doc = job
    url = doc["direct"]
    try:
        request = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(request, timeout=45) as response:
            length = int(response.headers.get("Content-Length") or 0)
            return (model, brand, doc.get("doc"), url, "OK", length, "")
    except urllib.error.HTTPError as error:
        return (model, brand, doc.get("doc"), url, f"HTTP_{error.code}", 0, "")
    except Exception as error:  # noqa: BLE001
        return (model, brand, doc.get("doc"), url, "ERROR", 0, str(error)[:60])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brand", default=None)
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    catalog = load_catalog()
    jobs = [
        (model, item["brand"], doc)
        for model, item in catalog.get("products", {}).items()
        if not args.brand or item["brand"].casefold() == args.brand.casefold()
        for doc in item.get("docs", [])
    ]
    if not jobs:
        print("Nothing to check.", file=sys.stderr)
        return 2

    print(f"checking {len(jobs)} link(s) ...")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(check, jobs))

    broken = [row for row in results if row[4] != "OK"]
    good = [row for row in results if row[4] == "OK"]
    declared = sum(parse_size(row[2] and doc.get("size")) for row, doc in zip(results, [j[2] for j in jobs]))
    measured = sum(row[5] for row in good)
    print(f"reachable: {len(good)}/{len(results)}")
    print(f"declared total: {measured / 1024**3:.2f} GB (measured from Content-Length)")
    for row in broken:
        print(f"  BROKEN {row[4]:<10} {row[0]:<24} {row[3]}")
        if row[6]:
            print(f"         {row[6]}")
    return 0 if not broken else 1


if __name__ == "__main__":
    raise SystemExit(main())
