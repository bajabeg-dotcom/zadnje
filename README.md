# VirtualPa800 NATIVE 0.2 — kompletan paket (2026-10-10)

> Jedan ZIP sa **svim što treba aplikaciji**: glavna app (PA800 Manager Web v0.2),
> pa_tool v0.3.0, KorgAnalyzer, KorgPaManager, **sav GOLD**, **svi stilovi**,
> setovi i dokumentacija. Bez interneta, bez `pip install` — samo Python 3.10+.

**Distribucija:** `VirtualPa800_NATIVE_0.2_[2026-10-10].zip` (GitHub Release v0.2.0, + `.sha256`)
**Brzi start:** dvoklik `POKRENI_VirtualPa800.bat` (Windows) ili `./run.sh` (Linux/mac) →
`http://127.0.0.1:8765`. Detalji: [`CITAJ_ME_PRVO.md`](CITAJ_ME_PRVO.md).

## Sadržaj paketa

| Folder | Sadržaj | Porijeklo |
|---|---|---|
| `app/pa800-manager-v0.2/` | **GLAVNA APP** — PA800 Manager Web v0.2, read-only forenzički manager (Style/PAD/Performance/Sound/Global/validacija/audit/report) | `PA800_Manager_Web_v0.2_NATIVE_SET_FORENSICS.zip` |
| `app/pa_tool-v0.3.0/` | CLI + web backend: native codec, bank parser, set graf, validatori (140 testova OK) | `pa_tool_v0.3.0.zip` |
| `app/pa800-manager-v0.1-legacy/` | Prethodna v0.1, samo za referencu | `PA800_Manager_Web_v0.1_...zip` |
| `tools/KorgAnalyzer_v2.4.0/` | Binarni analizer + PA800 MIDI alati | `KorgAnalyzer_v2.4.0.zip` |
| `tools/KorgPaManager_Complete_v1.1.1/` | MIDI style editor (GUI) + vendored Mido + offline Python installer | `KorgPaManager_Complete_v1.1.1.zip` |
| `tools/korg_pa_build-iz-Dokazi/` | Korg PA converter/manager/optimizer skripte | `Dokazi.zip` (`build/`) |
| `gold/PA800_GOLD_CORPUS_830.zip` | **GOLD korpus: 830 MIDI** (`MIDI/` + README) | Drive, SHA-256 verificiran |
| `gold/AUTO_SELECTED_GOLD.zip` | GOLD izbor, 832 MIDI (flat layout) | Drive |
| `styles/PA800_FACTORY_STYLES_PATTERNS_PADS_REFERENCE.zip` | Factory `BANK01–17.STY` + `USER01.sty` + PAD banke | Drive |
| `styles/STYLE.zip` | `FAVORITE01–10.STY` + `USER01–03.STY` | Drive |
| `styles/Pa3xStyle Wrhunski.zip` | `STYLE/USER01–12.STY` (Pa3x set) | Drive |
| `styles/VALJA_PA800_SONG_STYLES_FINAL_2026-10-03.zip` | VALJA song stilovi `.SET` + dokumentacija + alati | Drive |
| `styles/INIT_STY_OUT.zip` | INIT stilovi (`legacy/`, `pa1x/`, `pa3x/`, …) | Drive |
| `sets/PA800_KOMPLET_2026-10-05_v2.zip` | PA800 KOMPLET set + evidence | Drive |
| `research/1.zip` | Istraživačke skripte + `snd_all` podaci | Drive |
| `research/Dokazi.zip` | Original Dokazi arhive (byte-identičan) | Drive |
| `docs/` | Integracija v0.2, NATIVE SET audit, statički inventar, factory sound lista (1.003), provenijencija | Drive |
| `checksums/` | Originalni `.sha256` fajlovi sa Drive-a | Drive |

Svaki original je verificiran gdje je postojao `.sha256` (GOLD, Analyzer, KorgPaManager: **OK**).
Puna SHA-256 lista svega: [`MANIFEST_SHA256.txt`](MANIFEST_SHA256.txt).
Drive ID-jevi i veličine: [`docs/DRIVE_PROVENIJENCIJA.json`](docs/DRIVE_PROVENIJENCIJA.json).

## Pokretanje

**Windows:** dvoklik `POKRENI_VirtualPa800.bat` → browser se sam otvara na
`http://127.0.0.1:8765`.

**Linux/macOS:**

```bash
./run.sh
```

**pa_tool CLI:**

```bash
cd app/pa_tool-v0.3.0
python run_pa_tool.py native-bank FILE.STY --list --resource 1 --json
python run_pa_tool.py native-set SET.zip --deep-audit --graph --manifest --out report.json
python run_pa_tool.py native-sounds --find "121.0.0"
python run_pa_tool.py web --host 127.0.0.1 --port 8765
```

Zahtjevi: **Python 3.10+**, ništa drugo (`requirements.txt` je namjerno prazan —
sve je standardna biblioteka; KorgPaManager nosi svoj runtime).

## Granice (pročitati!)

- **Read-only.** Nema native Save/Convert/BKP-restore niti hardverski prihvaćenog
  izlaza — ne postoji certificirani writer i ništa se ne izmišlja.
  Vidi `docs/INTEGRACIJA_PA800_MANAGER_WEB_V02.md`.
- Nepoznate strukture su označene `PRESERVE_ONLY`; hardverski acceptance
  (`Load → Select → Play → Save → Reload` na Pa800 OS2.02) je `PENDING`/`NOT_TESTED`.

## Šta nije uključeno

Vidi [`docs/STA_NIJE_UKLJUCENO.md`](docs/STA_NIJE_UKLJUCENO.md): `SETOVI.zip` (362 MB —
prevelik za automatski transfer, dodaje se ručno), `kpmv3_runtime.zip` (samo 179 MB
crash-dump, namjerno izbačen), te starije verzije zamijenjene novijima.

---

*EN summary: all-in-one offline bundle of the PA800 Manager Web v0.2 read-only app,
pa_tool v0.3.0 CLI, KorgAnalyzer, KorgPaManager, the full GOLD MIDI corpus, all style
banks/sets and docs. Stdlib-only, Python 3.10+. Run `POKRENI_VirtualPa800.bat`
(or `./run.sh`) and open http://127.0.0.1:8765.*
