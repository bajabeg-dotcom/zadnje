# VirtualPa800 NATIVE 0.2 — BRZI START (2 minute)

Datum paketa: **2026-10-10**

## Šta je ovo?

Jedan paket sa SVIM što treba aplikaciji:

- **GLAVNA APP:** PA800 Manager Web **v0.2** (read-only forenzički manager: Style / PAD / Performance / Sound / Global / validacija)
- **CLI + web backend:** pa_tool **v0.3.0** (140 testova prolazi)
- **Alati:** KorgAnalyzer **v2.4.0**, KorgPaManager Complete **v1.1.1**
- **GOLD:** PA800_GOLD_CORPUS_830 (830 MIDI) + AUTO_SELECTED_GOLD
- **STILOVI:** factory BANK01–17, USER/FAVORITE banke, Pa3x set, VALJA song stilovi, INIT stilovi
- **SETOVI:** PA800_KOMPLET_2026-10-05_v2
- **DOKUMENTACIJA:** integracija, audit, inventar, factory sound lista

Nije potreban internet. Nije potreban `pip install`. Samo **Python 3.10+**.

## Pokretanje (Windows)

1. Dvoklik na **`POKRENI_VirtualPa800.bat`**
2. Otvoriće se browser na `http://127.0.0.1:8765`
3. U interfejsu otvoriš `.STY` / `.SET` (zip) / `.PCG` / `.PRF` / `.PAD` fajl i pregledaš ga

## Pokretanje (Linux / macOS)

```bash
chmod +x run.sh
./run.sh
# pa otvori http://127.0.0.1:8765
```

## Komandna linija (pa_tool)

```bash
cd app/pa_tool-v0.3.0
python run_pa_tool.py native-bank NEKI.STY --list --resource 1 --json
python run_pa_tool.py native-set NEKI_SET.zip --deep-audit --graph --manifest --out izvestaj.json
python run_pa_tool.py native-sounds --find "121.0.0"
python run_pa_tool.py web --host 127.0.0.1 --port 8765
```

## Važna granica (pošteno)

Ovo je **read-only forenzički manager**. Native pisanje (Save / Convert / BKP restore /
instrument-safe izlaz) **nije uključeno** jer ne postoji certificirani writer — detalji u
`docs/INTEGRACIJA_PA800_MANAGER_WEB_V02.md`. Ništa se ne izmišlja: nepoznato je
eksplicitno označeno kao `PRESERVE_ONLY` / `PENDING` / `NOT_TESTED`.

## Provjera integriteta

```bash
sha256sum -c MANIFEST_SHA256.txt
```

## Šta NIJE u paketu (i zašto)

Vidi `docs/STA_NIJE_UKLJUCENO.md` — ukratko: `SETOVI.zip` (362 MB, prevelik za automatski
transfer — skini ručno sa Drive-a), `kpmv3_runtime.zip` (samo crash-dump od 179 MB, smeće),
i starije verzije koje su zamijenjene novijima.
