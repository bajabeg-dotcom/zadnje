# Integracija `PA800_Manager_Web_v0.2_NATIVE_SET_FORENSICS`

Datum: 2026-10-06  
Cilj: `pa_tool 0.2.0`  
Izvorna arhiva SHA-256: `bde563f9b8c764d610ff3bb42f2a4fcaf47f7ab7ed2d045f889b240ba3c057f8`

## Odluka o granici

Izvor v0.2 je **read-only forenzički Manager**, iako UI ima preglednike za Style,
PAD, Performance, Sound, Global/mape, arhivu i validaciju. U izvoru **ne postoji**
certificirani OC20 writer, native Save/Convert, BKP pack/Restore niti hardverski
prihvaćen izlaz. Zato su svi stvarno postojeći workflowi, pravila i korisna logika
integrirani, ali native pisanje nije izmišljeno niti otključano.

`encode_oc31_literal()` ostaje samo softverski encoder unutrašnjeg objekta za testni
round-trip. Ne proizvodi instrument-safe OC20/STY fajl.

## Feature-by-feature matrica

| Izvorna funkcija / datoteka | Stanje prije | Odluka | Integrirano mjesto | Status / granica |
|---|---|---|---|---|
| `core/codec.py`: bounded CBarc | Eksterni codec je bio opcionalan | Izvor je samostalan i strože ograničen; preuzet | `pa_tool/native/codec.py`; `container.load_codec()` ga bira prvi | **VERIFIED** na fixtureima; max 16 MiB po resursu |
| OC31/OC32 XOR | Postojao generic trailer-trim put | Oba su korisna: strogi exact-object reader + generic container reader | `native.codec.decode_oc3`; `container.decode_oc_payload` | **VERIFIED**; nema writer tvrdnje |
| OC20 višestrane stranice + checksum/footer | Nije postojao strogi native reader | Preuzet bez slabljenja granica | `native.codec.decode_oc20_file` | **VERIFIED** za fixturee; 5 footer bajtova preserve-only |
| `core/format.py`: root/node/children granice | Postojao samo generic marker scan | Strogi parser je jači za native banke; generic scan ostaje za šire fingerprinting slučajeve | `pa_tool/native/format.py` | **VERIFIED**; read-only |
| Type-05 24-bajtni katalog | Nije bilo native bank join-a | Preuzet | `Bank`, `catalog_names` | **VERIFIED** |
| FE indeks + apsolutne KBEG/KEND adrese | Generic marker scan nije dokazivao indeks | Strogi izvorni parser preuzet | `Bank.__init__` | **VERIFIED** |
| Sparse Style bitmap 13 elemenata | Nije bilo dubokog Style parsera | Preuzet | `Bank._style_detail` | **VERIFIED** na korpusu |
| v5 Style bitmap 15 elemenata | Nije bilo | Preuzet uz originalno upozorenje | `Bank._style_detail` | Struktura čitljiva; **nije** dokaz native Pa800 OS2.02 |
| Višeelementni PAD bitmap | Nije bilo | Preuzet | `Bank._style_detail` | **VERIFIED** strukturno; nepoznata muzička semantika se ne pogađa |
| PatternInfo/CV event framing/EOT/ticks/reference join | Nije bilo | Preuzet | `_parse_cv`, `Bank._style_detail` | **VERIFIED** strukturno; MIDI semantika se ne izmišlja |
| 8 TrackInfo selektora i Factory lookup | Nije bilo u native readeru | Preuzet | `Bank._style_detail`; `native/sounds.py` | **VERIFIED** adresa/ime lookup |
| PRF Release 2 / Release 3 / Drawbar geometrija | Nije bilo | Preuzet | `Bank._performance_detail` | Poznate geometrije čitljive; ostalo `PRESERVE_ONLY` |
| Nepoznati PRF | Nije bilo | Zadržana konzervativna odluka | `coverage=PRESERVE_ONLY` | **REVIEW**, bez lažnih polja |
| 1.003 PA800 Factory Sound bindinga | Postojala druga Manager tabela od 1.325 programa; nije ista tabela/adresni ugovor | Obje tabele ostaju jer nisu duplikati iste semantike | `data/PA800_FACTORY_SOUND_LIST_CC00_CC32_PC.txt`; `native/sounds.py` | Tačno 1.003, duplikati i opseg odbijeni |
| Style TrackInfo → USER01/02/DK PCG kandidati | Nije bilo | Preuzet | `native/set_graph.py` | Kandidatski graf; nije potpuni dependency/remap dokaz |
| PCG bank-ID provjera i kolizije | Nije bilo | Preuzet; collision pregled sačuvan i u research API-ju | `native/set_graph.py`, `native/research.py` | **NEPOTVRDENO** kao semantički remap |
| ZIP path traversal / absolute / drive / NUL guard | Folder inventar je postojao, ne bounded upload ZIP | Preuzet | `native/workspace.py` | **VERIFIED** testovima |
| ZIP symlink, casefold duplicate i entry-count guard | Nije bilo | Preuzet | `Workspace.import_file` | **VERIFIED** |
| ZIP bomb: članski, totalni i ratio limiti | Nije bilo | Preuzet | `Workspace.import_file` | 64 MiB upload, 256 MiB expanded, 128 MiB member, 2.000 entry |
| CRC + SHA-256 svakog nešifriranog člana | Folder hash je postojao, ne ZIP CRC workflow | Preuzet | `Workspace.import_file` | **VERIFIED**; šifrovani članovi nisu dekodirani |
| Inventar PCM/KMP/SongBook/BKP | Nije bilo kroz ZIP | Preuzet | manifest | Samo inventar; BKP nije restore/decode |
| Originalni ZIP/file byte preservation + download | Read-only pravilo je postojalo | Preuzet | `Workspace.original_zip`, `/api/original*` | **VERIFIED** byte-identical testom |
| Deep audit i report | Postojao generički MD/JSON report, ne ovaj native sadržaj | Izvorni native report zadržan; generički report nije zamijenjen | `Workspace.audit/report`; `native-set --out` | Hardware `PENDING` / `NOT_TESTED` ostaje eksplicitno |
| Web REST API | Nije bilo web API-ja | Preuzet i razdvojen backend/UI | `native/workspace.py`, `native/web.py` | Read-only, in-memory session |
| Web UI: Overview | Nije bilo GUI-ja | Preuzet | `pa_tool/gui/web/` | Integrirano |
| Web UI: SET/PCM archive | Nije bilo | Preuzet | isto | Integrirano |
| Web UI: Style Library | Nije bilo | Preuzet | isto | Integrirano |
| Web UI: Performance | Nije bilo | Preuzet | isto | Integrirano |
| Web UI: PAD | Nije bilo | Preuzet | isto | Integrirano |
| Web UI: Sound catalog/PCG | Nije bilo | Preuzet | isto | Integrirano |
| Web UI: Setup & maps | Nije bilo | Preuzet | isto | Integrirano |
| Web UI: validation/audit/report/download | Nije bilo | Preuzet | isto | Integrirano |
| Native editable fields / Save / Convert / BKP Restore | **Ne postoji u izvoru** | Nema koda koji se može pošteno preuzeti; funkcija ostaje zaključana | status/README/UI | **UNKNOWN/PENDING**, ne pogađa se |
| `research/audit_native_sets.py` | Nije bilo | Korisna klasifikacija, safe-path, ZIP/RAR inventory i bounded member read prebačeni | `native/research.py` | RAR zahtijeva lokalni `7z`/`bsdtar`; nije web upload |
| `research/style_selector_graph.py` | Djelimično duplira jači `dependency_candidates` | Inferiorniji ordinalni tok nije dupliciran; collision catalog zadržan, sparse-aware graf je glavni | `native/research.py`, `native/set_graph.py` | Jača implementacija zadržana |
| Statički audit JSON/izvještaji | Nije bilo | Sačuvani kao dokaz, ne kao živa istina za novi upload | `docs/pa800_native_evidence/` | Provenijencija očuvana |
| 6 binarnih fixturea | Nije bilo | Svi preuzeti byte-identično | `data/pa800_native_fixtures/` | SHA-256 poklapanje sa source manifestom |
| 12 source regresija | Nije bilo | Sve prilagođene package importima | `tests/test_pa800_native.py` | 10 prolazi, 2 optional corpus skip |

## Dodatne integracijske odluke

1. `pa_tool.container` nije zamijenjen native parserom. Generic marker/dialect analiza i
   strogi native bank parser imaju različit ugovor; spajanje u jednu heuristiku bi bilo
   slabije i rizičnije.
2. Bundlovani bounded codec je sada prvi izbor. Eksterni `pa800_remap` codec ostaje samo
   kompatibilni fallback.
3. Originalni web `server.py` razdvojen je na `native/workspace.py` i `native/web.py`,
   kako bi ista pravila koristili CLI i browser bez dupliciranja.
4. Environment-specifični `main()` iz istraživačkih skripti nije hardkodiran u runtime.
   Njegova korisna logika je API; statički rezultati i originalni izvještaj su sačuvani.
5. Svi ulazi ostaju read-only. Jedini novi zapisi su eksplicitni JSON izvještaji na
   korisnički zadanoj putanji, uz odbijanje da se prepiše izvorna arhiva.

## Nove komande

```text
python run_pa_tool.py native-bank FILE.STY --list --resource 1 --json
python run_pa_tool.py native-set SET.zip --deep-audit --graph --manifest --out report.json
python run_pa_tool.py native-sounds --find "121.0.0"
python run_pa_tool.py web --host 127.0.0.1 --port 8765
```

## Još uvijek otvoreno — namjerno

- potpun STS/PRF selector → PCG → oscillator → KMP → PCM graf;
- model-target semantički remap;
- versioned OC20/STY/PRF/PCG/KMP/PCM writer;
- preservation test poslije stvarne izmjene;
- BKP vanjski packing/integritet/restore;
- fizički Pa800 OS2.02 `Load → Select → Play → Save → Reload` acceptance.

Bez ovih dokaza alat ne smije tvrditi native konverziju ili siguran restore.
