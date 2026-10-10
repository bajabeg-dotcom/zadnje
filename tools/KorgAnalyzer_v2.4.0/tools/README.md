# Catalogue maintenance

The manual catalogue in `korg_binary_analyzer/data/manuals.json` is generated,
not hand-written. Re-run these when a vendor adds or moves a document:

```bash
python tools/scrape_korg_manuals.py     # resolves every Korg product page
python tools/scrape_yamaha_roland.py    # Yamaha downloads.html + Roland listing pages
python tools/build_catalog.py           # writes data/manuals.json
```

Requirements: Python 3.10+, standard library only, network access.

What each step does:

* **scrape_korg_manuals.py** — walks the Korg product ids in `PRODUCTS`, lists the
  manual entries on each product page, follows each entry to its download page and
  reads the signed CDN URL out of the `Content-Disposition` parameter. Raw output
  is cached in `korg_manuals_raw.json` so re-runs only fetch what is missing.
* **scrape_yamaha_roland.py** — Yamaha serves PDFs directly from its
  `downloads.html` pages. Roland lists manuals as detail pages whose *listing*
  carries the real title and a language tag, so the listing is parsed first and the
  detail pages are only followed to extract the static PDF URL.
* **build_catalog.py** — applies the shipping policy: English only, real document
  names, accessory and companion-software labelling, and a `depth` field marking
  reference-grade documents. Writes `data/manuals.json`.

The catalogue stores **links only**. No PDF is copied, mirrored or redistributed.
