# STATUS — mapa funkcija

Datum: 2026-10-06 · verzija alata: `0.3.0`

Ovaj dokument mapira **svaku funkciju na njen dokaz i status**. Jedini izvor istine
su `data/*.json`; ovdje je pregled.

## Legenda

| status | znači |
|---|---|
| `VERIFIED` | izmjereno i reproducibilno (offset / dužina / hash / broj) |
| `HIGH` | dokaz postoji, isključivost ne |
| `NEPOTVRDENO` | dokaz postoji, ali ne dokazuje tvrdnju |
| `UNKNOWN` | nema uzorka/podatka — alat **prijavljuje**, ne pogađa |

## 1. Implementirano i provjereno

| modul | funkcija | status | dokaz |
|---|---|---|---|
| `container.py` | `is_korg_container` | **VERIFIED** | `01 00 01 14` na 42 fajla + 7 kontejnera |
| `container.py` | `read_header` | **VERIFIED** | u32BE @+4 = `size-8` (6/7 iz Pa Managera `delta=0`) |
| `container.py` | `walk_resource_chain` | **VERIFIED** | `KORF`/`KBEG`/`KEND` na stvarnim uzorcima; `balanced` provjerava uparenost KBEG/KEND |
| `container.py` | `scan_compression` | **VERIFIED** | `OC20`/`OC31`/`OC32` (FAC_ROM_PCG: OC32 ×1323) |
| `container.py` | `split_declared_payload` | **NEPOTVRDENO** | rep iza `KEND` kod 2 fabrička ROM kontejnera |
| `pcg.py` | PA `KORF` / workstation `KORG/PCG1` klasifikacija | **VERIFIED/HIGH** | 4 stvarna PA PCG uzorka + javne chunk strukture; `data/pcg_chunks.json` |
| `pcg.py` | `PBK1`/`PRG1`/`RLP1` semantika | **HIGH** | `PRG1/PBK1` workstation PCG; `RLP1` KMP, nisu univerzalni PA potpisi |
| `detect.py` | `fingerprint_file` | **VERIFIED** | popis ekstenzija iz dijaloga |
| `detect.py` | `detect_set_folder` | **VERIFIED** | 6 uzoraka `.SET` |
| `detect.py` | `detect_kst_folder` | **VERIFIED** | `User.KST` |
| `detect.py` | model iz fajla | **UNKNOWN** | dokazano: model se čita iz `OpenSound SUB` u binarcu |
| `detect.py` | `decode_percent_name` | **VERIFIED** | `6%2F8 Ballad.stg` → `6/8 Ballad.stg` |
| `setfolder.py` | `inventory_set` | **VERIFIED** | brojevi se poklapaju sa diskom (test) |
| `setfolder.py` | `parse_metadir` | **VERIFIED** | **50 stvarnih `metadir.json`** (nov nalaz) |
| `setfolder.py` | `hash_manifest` | **VERIFIED** | sha256 po fajlu |
| `validate.py` | `check_hard_limits` | **VERIFIED** | 6 limita sa offsetima |
| `validate.py` | `check_ram` | **VERIFIED** | RAM tabela 12 unosa; Pa4X bazno 400 MB, varijanta OS Next 1536 MB |
| `validate.py` | `check_locked` | **VERIFIED** | 6 poruka sa offsetima |
| `validate.py` | `check_unsupported` | **VERIFIED** | 9 klasa sadržaja |
| `validate.py` | `check_pa5x_sound_import` | **VERIFIED** | `0x002DD968` |
| `validate.py` | `check_canonical_sample_format` | **VERIFIED** | `0x00250114` |
| `validate.py` | `check_osc_limit` | **UNKNOWN** | namjerno: mapiranje nije dokazivo |
| `validator.py` | 18 modelskih profila | **VERIFIED/HIGH** | svi modeli/varijante iz `models.json`; svaki profil nosi dokaz i status |
| `validator.py` | 32 file/folder pravila | **VERIFIED/PARTIAL** | svaki filter nastavak; opaque šeme ostaju UNKNOWN |
| `validator.py` | rekurzivni `validate_path` | **VERIFIED** | svaki regularni fajl dobija report; symlink/size/duplicate guard |
| `validator.py` | model-pair/file matrica | **VERIFIED kao odluka** | 10.368 ćelija; nepoznati writer ostaje UNKNOWN |
| `model_convert.py` | byte-identična kopija/staging/hash guard | **VERIFIED** | source hash prije/poslije + output hash/read-back |
| `model_convert.py` | PCM WAVE 44.1/16/mono | **VERIFIED softverski** | stdlib PCM decode/resample/encode + validator read-back |
| `model_convert.py` | fiksni vanjski KORF katalog | **PARTIAL/VERIFIED offsets** | samo 16 B name, style bank i eksplicitni ObjectKey u katalogu; ne payload |
| `report.py` | MD + JSON sa statusom i dokazom | **VERIFIED** | svaki red ima `status` + `evidence` |
| `convert/` | planiranje konverzija + odbijanje prije rada | **VERIFIED** | 9 smjerova; odbijanja sa offsetima `0x002DD868`, `0x0024AD90`, `0x002DD968` |
| `convert/` | `resolve_paths` / `load_setconvert` | **VERIFIED** | setconvert + pa800_native nađeni na disku; notes ključi više ne dupliciraju resolve_paths |
| `convert/` | `sty_families` | **VERIFIED** | 6 familija iz `families.py`; `pa4x` koristi 64 B tabelu |
| `convert/` | `fingerprint_sty` | **VERIFIED** | stvarni `.STY` → family + dialekti + `kbeg_ok` |
| `convert/` | `encode_oc31_greedy` shim | **NEPOTVRDENO** | pravi greedy enkoder fali; mapiran na optimalni |
| `container.py` | `decode_oc_payload` | **VERIFIED** | OC32 1.141.260 B dekodirano, trailer 30 B, XOR `0x5b` se poklapa |
| `container.py` | `scan_compression` | **VERIFIED** | offseti `OC20`/`OC31`/`OC32` |
| `manager.py` | inventar resursa Pa Managera | **VERIFIED** | 175 resursa, 126 iz fajla + 49 iz slike, 0 nemapirano |
| `manager.py` | paker Managera | **VERIFIED** | PECompact2, `PEC2` @`0x1C8`, stub @`0x00B7E218` |
| `manager.py` | tabela fabrickih imena | **VERIFIED** | 24-bajtni zapis, 1325 imena, 2 sekcije, indeks sekvencijalan |
| `manager.py` | imena drum kitova (`FAC_ROM_KMP`) | **VERIFIED** | OC32 dekodiran (1.141.260 B, XOR 0x5b), 40-bajtna tabela, 1599 zapisa / 1337 imena |
| `manager.py` | OC32/CBarc dekodiranje | **VERIFIED** | trailer 30 B odbijen, XOR kontrolni bajt se poklapa |
| `manager.py` | imena uzoraka u KMP | **NEPOTVRDENO** | 1092 nakon filtera (od 2297 sirovih ASCII nizova) |
| `manager.py` | ugradjeni PE moduli | **VERIFIED** | 3 modula; ista porodica kao PE1/PE2/PE3 |
| `native/codec.py` | bounded CBarc + OC31/OC32 XOR | **VERIFIED** | source fixture round-trip i checksum regression; bundled codec je prvi izbor |
| `native/codec.py` | OC20 multi-page read + page checksum | **VERIFIED** | BANK01/ROM fixturei; corrupt-checksum test odbija ulaz |
| `native/format.py` | root/type-05/FE/KBEG-KEND indeks | **VERIFIED** | 6 fixture banki; namjerno oštećen indeks se odbija |
| `native/format.py` | sparse 13/15 Style i multi-element PAD bitmap | **VERIFIED/NEPOTVRDENO** | strukturni korpus prolazi; v5 nije dokaz native Pa800 OS2.02 |
| `native/format.py` | CV/PatternInfo/8×TrackInfo | **VERIFIED** | framing, brojači, EOT/ticks i reference; bez izmišljene MIDI semantike |
| `native/format.py` | PRF R2/R3/Drawbar | **VERIFIED** | poznata geometrija; nepoznate varijante `PRESERVE_ONLY` |
| `native/sounds.py` | 1.003 Factory Sound selektora | **VERIFIED** | exact CC00.CC32.PC tabela; opseg i duplikati validirani |
| `native/set_graph.py` | Style TrackInfo → USER PCG kandidat | **NEPOTVRDENO** | candidate-only; ne zatvara STS/PRF/oscillator/KMP/PCM/BKP/remap |
| `native/workspace.py` | bounded ZIP SET inventar | **VERIFIED** | traversal/symlink/duplicate/bomb guard; CRC+SHA; original byte-identičan |
| `native/workspace.py` | PCM/KMP/SongBook/BKP članovi | **VERIFIED** inventar | nisu native dekodiranje, konverzija ili restore |
| `native/workspace.py` | deep audit/report | **VERIFIED** softverski | hardware ostaje `PENDING/NOT_TESTED` |
| `native/web.py` + `gui/web/` | browser UI i API workflowi | **VERIFIED** smoke | Overview, archive, Style, PRF, PAD, Sound, setup, validation |
| `native/research.py` | offline ZIP/RAR audit helpers | **VERIFIED/HOST-DEPENDENT** | ZIP stdlib; RAR traži lokalni `7z`/`bsdtar` |

## 2. Nije implementirano (planirano)

| faza | funkcija | status ulaza | napomena |
|---|---|---|---|
| F3 | `convert` — STY porodice | **VERIFIED** | stvarna konverzija `pa800`→`legacy` izvršena i verifikovana fingerprintom |
| F3 | round-trip identitet STY | **NEPOTVRDENO** | 520.768/527.785 bajtova razlike; rekompresija ili gubitak — nije dokazano |
| F3 | `convert` — planiranje + odbijanja | **VERIFIED** | 0.3.0 izvršava sigurnu kopiju, WAVE i fiksni katalog; puni payload writer ostaje otvoren |
| F3 | `convert` — MG ↔ STD | **VERIFIED** | `0x00243A00/A2C`, roundtrip `0x0042CDE0` |
| F3 | `convert` — downgrade 4X→3X | **VERIFIED** | `0x002CF730` |
| F3 | `convert` — uzorci u 44.1/16/mono | **VERIFIED** | `0x002D9E14/4C/EBC` |
| F3 | `convert` — v5 → v4 | **VERIFIED** | reuse `pa800_remap\v5to4.py` |
| F3 | `convert` — `.SET` → `.KST` | **NEPOTVRDENO** | najveći rizik, posljednja faza |
| F4 | puni STS/PRF→PCG→oscillator→KMP→PCM remap | **NEPOTVRDENO** | integrirani graf pokriva samo STY TrackInfo→USER PCG katalog kandidat |
| Native write | OC20/STY/PRF/PCG/KMP/PCM/SET serializer | **UNKNOWN** | source v0.2 nema instrument-safe writer; ne otključavati bez dokaza |
| BKP | packing/integritet/restore | **UNKNOWN** | stvarni BKP i destruktivni hardware test nedostaju |
| Hardware | Pa800 OS2.02 Load→Play→Save→Reload | **UNKNOWN** | softverski PASS nije device acceptance |
| F5 | `kst` — čitanje/uređivanje | **NEPOTVRDENO** | XML šema rekonstruisana iz fragmenata |
| F5 | `merge` | **NEPOTVRDENO** | tok nije dokazan → `experimental` |

## 3. UNKNOWN — i šta bi ga riješilo

| # | UNKNOWN | šta bi ga riješilo |
|---|---|---|
| A | OSC limit → model (4/5/16/24) | po jedan `.PCG` s PA80 i s PA5X |
| B | indeks starijeg `.SET`-a | `.SET` sa dokumentovanim indeksom; 6 uzoraka ga **nema** |
| C | sadržaj verzijskih tabela | mapiranje verzija (tabela postoji @`0x243F74`) |
| E | enum vrijednosti 0…9 | `Select Case` skok-tabela (sad **HIGH**) |
| G | MERGE tok | sekvenca operacije (375× pojava u kodu) |
| H | `2.exe` konverzijski algoritam | PECompact2 raspakivanje (`.text` entropija 8.000) |
| I | zapis imena u `FAC_ROM_KMP` | **RIJESENO** — OC32 dekodiran, 40-bajtna tabela, 1599 zapisa |
| J | 49 resursa samo u slici | da li postoje i u raspakovanom `2.exe`; zavisi od raspakivanja |
| K | 22-bajtna binarna polja u KMP zapisu | velocity slojevi / key range / pokazivaci — nije raščlanjeno |
| L | STY round-trip nije identičan | poređenje *dekodiranih* payloada; pravi `oc31_fast_encoder.py` |
| M | `InitPx01` fingerprinter vs tabela familija | jednak broj glasova (14/6/6); redoslijed odlučuje |

## 3b. Riješeno prethodnim prolazima

| pitanje | bilo | sada |
|---|---|---|
| `metadir.json` šema | UNKNOWN | **VERIFIED** — 50 stvarnih fajlova |
| `.KST` struktura | NEPOTVRDENO | **VERIFIED** — `User.KST` + 770 `.STG` |
| tabela imena u Manageru (PCG) | regex sa 50 đubre unosa | **VERIFIED** — 24-bajtni zapis, 1325 imena |
| tabela imena u Manageru (KMP) | regex po kompresovanom toku | **VERIFIED** — OC32 dekodiran, 40-bajtni zapis, 1599 imena |
| paker Managera | UNKNOWN | **VERIFIED** — PECompact2 |
| OC32/CBarc dekodiranje | nije rađeno | **VERIFIED** — XOR kontrolni bajt se poklapa |
| `PA588` / `PA3X Le` značenje | UNKNOWN | **HIGH** — dijele SOUND handler s PA500 / PA3X (ne dokazuje iste bajtove) |
| `PBK1`/`PRG1`/`RLP1` | kandidati za PA potpis | **RIJEŠENO** — workstation PCG / KMP tokeni; 4 PA KORF uzorka ih nemaju |

## 4. Rezultati provjere

| provjera | rezultat |
|---|---|
| `python run_tests.py` | **140 testova — OK** (16 skip bez eksternih korpusa/asseta) |
| `python verify_readonly.py` | PASS; eksterni sample pathovi nisu prisutni u clean-room stablu i prijavljeni su kao SKIP |
| native source/regresije | byte-identičan original ZIP, oštećeni indeks ne mijenja source, 6 fixture hashova poklopljeno |
| native demo ZIP smoke | **PASS**, 7 članova, 6 banki, 1.177 resursa, 0 failure, report `NOT_TESTED` hardware |
| source provenance | **25/25** source datoteka accounted, 16 exact-copy SHA-256 poklapanja |
| `python run_pa_tool.py doctor` | 0 grešaka; uključuje 1.003 sound bindinga i 6 fixturea |
| `python run_pa_tool.py models` | 10 modela, RAM + import SUB |
| `python run_pa_tool.py conversions` | 9 smjerova sa statusom |
| `python run_pa_tool.py manager --names` | 175 resursa, 1325 + 1599 fabrickih imena |
| `python proof_sty_conversion.py` | historijski: STY `pa800`→`legacy`; ne dokazuje native hardware acceptance |
| `python prove_defects.py` | **10/10 PASS** — svi defekti verifikovani |

## 5. Nepromjenljivost (dokaz)

`verify_readonly.py` i dalje radi hash-prije/hash-poslije kada su eksterni sample pathovi
prisutni. U ovom clean-room stablu ta dva vanjska patha nisu dostupna i skripta ih
transparentno označava `SKIP`, zatim prijavljuje `PASS: no input file was modified`.

Native v0.2 put ima dodatne samostalne dokaze bez vanjskih pathova:

- svih 6 integriranih fixturea ima isti SHA-256 kao source manifest;
- ZIP test potvrđuje `Workspace.original_zip() == original_bytes`;
- corrupt-index test potvrđuje da odbijeni parse ne mijenja `Bank.source`;
- CLI odbija `--out` ako je ista putanja kao ulazni ZIP;
- web API samo vraća originalne bajtove; nema native write endpoint.

Isti opšti princip ostaje u `TestInputImmutability` kada su eksterni uzorci dostupni.

## 6. Promjene u v0.3.0

| promjena | opis |
|---|---|
| `validator.py` | 18 modelskih profila iz dokaznih tabela; 32 pravila za svaki poznati file/folder tip |
| Svaki fajl | `validate-all` rekurzivno provjerava cijelo stablo i daje zaseban report svakom fajlu |
| Matrica | 10.368 source/target/extension ćelija sa EXACT/PARTIAL/PRESERVE_ONLY/REFUSED/UNKNOWN statusom |
| MIDI | strogi SMF parser i odvojeni `generic`, `manager_import`, `pa_style_import` ugovori |
| Audio | stvarni PCM WAVE upis u dokumentovani 44.1 kHz / 16-bit / mono cilj |
| KORF katalog | eksplicitno mapiran fixed-width upis uz diff guard; kompresovani payload se ne skenira nasumično |
| Izlazna sigurnost | postojeći cilj i in-place upis odbijeni; staging, read-back i source hash prije/poslije |
| Testovi | 140 testova prošlo, 16 skip za nedostupne vanjske korpuse/assete |

## 6a. Promjene u v0.2.0

| promjena | opis |
|---|---|
| Potpuna izvorna matrica | svaki modul/workflow iz `PA800_Manager_Web_v0.2_NATIVE_SET_FORENSICS` upoređen; vidi `INTEGRACIJA_PA800_MANAGER_WEB_V02.md` |
| Native parser | bounded CBarc/OC20/OC31/OC32, type-05, FE/KBEG-KEND, sparse Style/PAD, CV, PRF |
| SET ZIP | CRC/SHA cijelog arhiva, traversal/symlink/duplicate/bomb zaštita, original-byte preservation |
| Sound/graf | 1.003 PA800 selektora i Style→USER PCG kandidatske veze |
| Web | kompletan read-only UI/API integriran u `pa_tool`, pokretanje komandom `web` |
| Research/evidence | offline audit helperi, 6 fixturea, statički JSON rezultati i source izvještaji sačuvani |
| Jači overlap | postojeći generic container/dialect sloj ostaje; strogi native parser je zaseban ugovor; bounded bundled codec je prvi izbor |
| Sigurnosna granica | native Save/Convert/BKP Restore nije postojao u sourceu i ostaje zaključan |

## 7. Promjene u v0.1.2

| promjena | opis |
|---|---|
| PCG dijalekt klasifikator | nova `pcg` komanda razlikuje stvarni PA `01 00 01 14` + `KORF@0x17` od workstation `KORG/PCG1` formata |
| Problem F riješen | `PBK1/PRG1` su workstation PCG chunkovi; `RLP1` je KMP chunk; nisu univerzalni PA PCG potpisi |
| Problem D sužen | PA588 dijeli SOUND handler sa PA500, PA3X Le sa PA3X; status HIGH jer identitet bajtova nije dokazan |
| Dokazi | `data/pcg_chunks.json` čuva veličine, SHA-256, markere, Drive porijeklo i javne izvore |
| Verzija/datum | paket, README/STATUS i CLI sada dosljedno prijavljuju `0.1.2` i 2026-10-06 |

## 8. Popravke u v0.1.1

| popravka | opis |
|---|---|
| ENV-var portabilnost | `PA_TOOL_ASSET_ROOT`, `PA_TOOL_NATIVE_PARENT`, `PA_TOOL_SETCONVERT_PARENT` se provjeravaju PRIJE hardkodiranih pathova |
| `ResourceChain.balanced` | novi property: provjerava da je svaki KBEG uparen sa KEND |
| skip-safe testovi | 5 asset-zavisnih testova sada skip umjesto FAIL |
| 3 nova testa | balanced chain ×2, env override ×1 |
| `proof_sty_lossless.py` bug fix | `sha256(src) == sha256(src)` uvijek true — sada poredi hash prije i poslije konverzije |
| `prove_defects.py` rewrite | 7 starih bugova → 5 defekata + 10 PASS regression testova |
| Defect A riješen | `load_setconvert()` notes koriste `pa800_native_loaded`/`setconvert_loaded` umjesto duplikata `pa800_native_parent`/`setconvert_parent` |
| `.pyc` čišćenje | svi `__pycache__` direktoriji uklonjeni iz projekta |
