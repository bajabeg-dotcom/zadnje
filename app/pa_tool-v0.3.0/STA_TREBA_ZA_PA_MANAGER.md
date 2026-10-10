# Šta treba za PA Manager — popis funkcija, dokaza i statusa

Datum: 2026-10-06
Namjena: za svaku funkciju koju PA Manager ima, reci **šta je za nju potrebno**,
**čime je dokazana** i **šta je njen status**. Bez ovoga se ne može tvrditi da
nešto „radim kao PA Manager".

Legenda statusa:
- **VERIFIED** — izmjereno i reproducibilno (offset / dužina / hash / broj)
- **HIGH** — dokaz postoji, isključivost ne
- **NEPOTVRĐENO** — dokaz postoji ali ne dokazuje tvrdnju
- **UNKNOWN** — nema uzorka/podatka; alat to **prijavljuje**, ne pogađa

---

## 0. Ukupno stanje po funkciji

| # | Funkcija | Potrebno za nju | Dokaz | Status | U `pa_tool` |
|---|---|---|---|---|---|
| 1 | Detekcija tipa fajla | popis ekstenzija iz dijaloga | `PA_EDITABLE_PATTERNS` §3.1 | **VERIFIED** | ✅ `detect` |
| 2 | Detekcija modela | `OpenSound SUB` grupisanje u binarcu | `PA_MANAGER_RULES` §0.1 | **VERIFIED** (u binarcu) / **UNKNOWN** iz samog fajla | ✅ `detect` (prijavljuje UNKNOWN) |
| 3 | Parsiranje kontejnera | magic + dužina + `KORF/KBEG/KEND` | `RE_RUN` + E12 + E13 | **VERIFIED** | ✅ `container` |
| 4 | Inventar `.SET` foldera | kategorije + `metadir.json` | `RE_RUN` + 544 `.SET` | **VERIFIED** | ✅ `setfolder` |
| 5 | `metadir.json` šema | jedan stvarni primjer | **50 fajlova, ovaj prolaz** | **VERIFIED — zatvoreno** | ✅ `setfolder.parse_metadir` |
| 6 | Validacija: hard limiti | brojevi iz nizova | `PA_MODEL_REQUIREMENTS` §1.5 | **VERIFIED** | ✅ `validate` |
| 7 | Validacija: RAM | RAM tabela + mehanizam | `PA_MODEL_REQUIREMENTS` §2.1 | **VERIFIED** | ✅ `validate` |
| 8 | Validacija: zaključano | poruke odbijanja + offseti | `PA_MANAGER_RULES` §1.3 | **VERIFIED** | ✅ `validate` |
| 9 | Validacija: OSC limit | mapiranje limit → model | `PA_MODEL_REQUIREMENTS` §2.3 | **UNKNOWN** (dokazano nedokazivo) | ✅ `validate` (uvijek UNKNOWN) |
| 10 | Kanonski format uzorka | ciljni format | `0x00250114` | **VERIFIED** | ✅ `validate` |
| 11 | Konverzija `.STY` | `setconvert` modul | `samples\NEWNAME_SET\setconvert\` | VERIFIED/N | ⏳ F3 |
| 12 | MG ↔ STD | offseti + roundtrip | `0x00243A00/A2C`, `0x0042CDE0` | **VERIFIED** | ⏳ F3 |
| 13 | Downgrade 4X → 3X | offset | `0x002CF730` | **VERIFIED** | ⏳ F3 |
| 14 | Uzorci u kanonski format | konverteri | `0x002D9E14/4C/EBC` | **VERIFIED** | ⏳ F3 |
| 15 | v5 → v4 | `pa800_remap\v5to4.py` | modul postoji | **VERIFIED** | ⏳ F3 |
| 16 | Remap na tvorničke | sounddb + 4,7 MB matrica | `data\*.json` u remapperu | **VERIFIED** | ⏳ F4 |
| 17 | `.KST` parsiranje | XML šema + `CLIP/PARAMS` | `PA_EDITABLE_PATTERNS` §4 | **NEPOTVRĐENO** | ⏳ F5 |
| 18 | `.KST` upis | ista šema + roundtrip | — | **NEPOTVRĐENO** | ⏳ F5 |
| 19 | MERGE | pun tok operacije | 375× u kodu, tok nedokazan | **NEPOTVRĐENO** | ⏳ F5 (`experimental`) |
| 20 | Izvještaji (MD+JSON) | status + dokaz po redu | ovaj projekat | **VERIFIED** | ✅ `report` |
| 21 | CLI | komande | ovaj projekat | **VERIFIED** | ✅ `cli` |
| 22 | GUI | tkinter + F1–F5 API | — | — | ⏳ F6 |

---

## 1. Šta je **zatvoreno ovim prolazom** (novi dokazi sa tvog računara)

Dva UNKNOWN-a iz RE faze su zatvorena **stvarnim fajlovima**, ne pretpostavkom:

### 1.1 `metadir.json` šema — bilo UNKNOWN → sada VERIFIED

Prije: `PA_MODEL_REQUIREMENTS.md` §4 je tražio „jedan `.SET` folder" da bi se šema dokazala.

Nađeno na disku: **50 `metadir.json` fajlova** u
`E:\Pa5X_UNPACKED\omega_sys\omega_sys\STARTUP\ALL\**`, veličine 143–7.602 B.

Šema (tri polja, sva potvrđena primjerima):

```json
{
  "defaultFileIcon": "sknIcoStyle_Ballad1",
  "subdirs": [ { "dir": "FACTORY", "id": "Fty" } ],
  "files":   [ { "file": "Twilight Ballad.stg", "icon": "sknIcoStyle_Ballad3" } ]
}
```

- `subdirs` — grupne kategorije; koristi se na višem nivou (`STYLE/metadir.json`)
- `files` — lista unutar kategorije; koristi se na nižem (`STYLE/FACTORY/Ballad/metadir.json`)
- `defaultFileIcon` — opciono; koristi se kad zapis nema svoj `icon`
- **Bitan detalj:** `/` u imenu fajla se zapisuje kao `%2F` → `6%2F8 Ballad.stg` = `6/8 Ballad.stg`

Kategorije sa `metadir.json`: **STYLE, PAD, KEYBOARDSET, CHORDSEQUENCE**
Kategorije bez: `SETTINGS, PCMIMAGE, SYSTEM, MATRIXPRESET, AUDIOPLUGINHOST, DEMOSONG, LANG`

### 1.2 `.KST` identitet i struktura — VERIFIED punim primjerom

`E:\Pa5X_UNPACKED\omega_sys\omega_sys\FactoryBackup\User.KST` je **folder** sa:
`MATRIXPRESET, MAXXAUDIOPRESET, MIDIPRESET, QTPRESET, SETTINGS, SONGBOOK`
i tipovima `.MTP .MXP .MPR .QTP .ksc .GBL .SBD .SBL`.

Uz to, u paketu je **770 `.STG`** fajlova u `STYLE\FACTORY\<13 žanrova>\`.

### 1.3 Puni Pa5X fabrički katalog — novi resurs

| kategorija | primjer | broj zapisa u `metadir.json` |
|---|---|---|
| STYLE/Ballad | `Twilight Ballad.stg` | 70 |
| STYLE/Pop | — | (5.537 B metadir) |
| PAD/Acoustic Guitar | `Beat Strum 1.pdc` | 54 |
| KEYBOARDSET/Piano | `Austrian Grand.ksc` | 56 |
| CHORDSEQUENCE/Jazz | `A Night In Tunisia.csc` | 52 |

Ovo je **katalog imena** (za razliku od bajtova kontejnera). Koristi se za
provjeru imena, deduplikaciju i MERGE.

---

## 2. Šta je **i dalje otvoreno** — i zašto

| # | Pitanje | Status | Šta bi ga riješilo |
|---|---|---|---|
| A | OSC limit → koji model (`4/5/16/24`) | **UNKNOWN** | po jedan `.PCG` s PA80 i s PA5X; mapiranje nije u string tabeli |
| B | `metadir.json` u `.SET` (stariji modeli) | **NEPOTVRĐENO** | pravi `.SET` sa `metadir.json` — nađeni uzorci ga **nemaju** na vrhu |
| C | Sadržaj verzijskih tabela | **NEPOTVRĐENO** | tabela postoji (`0x243F74`…), ali mapiranje nije dokazano |
| E | Enum vrijednosti (0…9) | **HIGH** | `Select Case` skok-tabela; barem jedan član ima eksplicitnu vrijednost |
| G | MERGE tok | **NEPOTVRĐENO** | 375× pojava, ali sekvenca nije rekonstruisana |
| H | `2.exe` konverzijski algoritam | **UNKNOWN** | PECompact2 raspakivanje (`.text` entropija 8.000) |

### 2.0 Riješeno 2026-10-06: D i F

- **D — PA588 / PA3X Le:** statički factory dispatch iz Drive artefakta
  `pcg-conversion-followup.json` pokazuje da PA588 bira
  `KORG_PA_MANAGER.PA_OS_SOUND_PA500`, a PA3X Le bira
  `KORG_PA_MANAGER.PA_OS_SOUND_PA3X`. Status je **HIGH**: handler je dokazan,
  identični serijalizovani bajtovi nisu.
- **F — PBK1 / PRG1 / RLP1:** četiri stvarna PA `.PCG` uzorka su `KORF`
  kontejneri i nemaju te tokene. Javni KORG format izvori smještaju `PRG1/PBK1`
  u workstation `KORG/PCG1` PCG dijalekt, a `RLP1` u KMP. Detalji i hash-evi su
  u `RIJESENO_01_PCG_I_VARIJANTE.md` i `data/pcg_chunks.json`.

### 2.1 Bitna napomena o „B" — izmjereno na 6 stvarnih `.SET`-ova

Provjerio sam sve nađene uzorke `.SET`-ova na disku:

| `.SET` | `metadir.json` | kategorije |
|---|---|---|
| `NEWNAME.SET` (radni + Desktop) | **ne** | GLOBAL, MULTISMP, PAD, PCM, PERFORM, SONGBOOK, SOUND, STYLE |
| `BAJAPA800.SET` | **ne** | + GUITARPRESET, KEYBOARDSET, VOICEPRESET |
| `revolucijaa.SET` | **ne** | + STYLE_CONVERTED |
| `111.SET` | **ne** | MULTISMP, SONGBOOK |
| `NoBe.SET` | **ne** | GLOBAL, MULTISMP, PAD, PCM, PERFORM, SONGBOOK, SOUND, STYLE |
| `GOLE ARX .SET` | **ne** | ugniježđen (nested `.SET`) |

Dodatno: pretraga **svih diskova** za `metadir.json` van `Pa5X_UNPACKED` vraća **0 rezultata**.

**Zaključak (VERIFIED nepostojanjem):** `metadir.json` pripada **Pa5X/`.KST` generaciji**.
Stariji `.SET` koristi **drugi indeks** (ili ga uopće nema). `PA_FORMAT_REQUIREMENTS.md` §6
tvrdi „`.SET` mora sadržavati `metadir.json` (UIType, Bank Name)" uz status **HIGH** —
moj nalaz na 6 uzoraka tu tvrdnju **ne potvrđuje**. Zato `pa_tool` za stariji `.SET`
prijavljuje `metadir: absent` kao **NEPOTVRĐENO**, a **ne** kao grešku.

**Bonus nalaz:** `BAJAPA800.SET` (PA800, starija generacija) ima kategorije
`GUITARPRESET`, `KEYBOARDSET`, `VOICEPRESET` koje `NEWNAME.SET` nema. To pokazuje da
se **skup kategorija razlikuje između generacija** — alat zato ne smije tvrditi
fiksnu listu kategorija, nego ih čita sa diska.

Dodatno: `GOLE ARX .SET` je **ugniježđen** (`.SET` unutar `.SET`), što znači da parser
mora podržati rekurziju bez beskonačne petlje.

---

## 3. Šta je za svaku funkciju **tehnički potrebno**

### 3.1 Ulazni podaci

| Funkcija | Potrebno | Imam? |
|---|---|---|
| Detekcija tipa | popis ekstenzija | ✅ 30 ekstenzija u `data/rejections.json` |
| Detekcija kontejnera | magic + layout | ✅ potvrđen |
| Detekcija modela | `OpenSound SUB` | ✅ offseti; ⚠️ radi samo **u binarcu**, ne iz fajla |
| `.SET` inventar | stvarni folderi | ✅ 544 `.SET` + uzorci |
| `.KST` parsiranje | `.KST` folder | ✅ `User.KST` + 770 `.STG` |
| Validacija | tabele odbijanja | ✅ 40+ poruka sa offsetima |
| Konverzija STY | `setconvert` | ✅ modul postoji |
| Remap | sounddb + matrice | ✅ 4,7 MB + 320 KB |
| v5→v4 | `v5to4.py` | ✅ modul postoji |
| MERGE | dokazan tok | ❌ nije dokazan → `experimental` |

### 3.2 Zavisnosti

| Šta | Status |
|---|---|
| Python 3.13 | ✅ (u okruženju `isolated=1` → treba `run_pa_tool.py`) |
| `lxml` | ✅ (za `.KST` XML, F5) |
| `PIL` | ✅ (ikone/BMP) |
| `mido` | ❌ nije instaliran → potreban za F3 (MIDI remap) |
| `pefile`, `capstone` | ❌ nisu instalirani → samo za audit `2.exe` |

**Trenutni `pa_tool` ne traži nijednu dodatnu instalaciju** — sve je standardna
biblioteka. `mido` treba tek kad se radi F3 remap.

---

## 4. Kako se pokreće

```powershell
cd pa_tool
python run_pa_tool.py doctor        # provjeri da data sloj radi
python run_pa_tool.py models        # tabela 10 modela + RAM + import SUB
python run_pa_tool.py conversions   # matrica smjerova konverzije
python run_pa_tool.py status        # šta je implementirano, šta UNKNOWN
python run_pa_tool.py detect <putanja>
python run_pa_tool.py validate <putanja> --model Pa5X --required-ram-mb 2000
python run_pa_tool.py inventory <SET folder>
python run_pa_tool.py metadir <folder ili metadir.json>
python run_tests.py                 # 36 testova
python verify_readonly.py           # dokaz da ulazi nisu mijenjani
```

---

## 6. Dopuna iz `2.exe_pa_analysis` (2026-10-06)

Nalazi iz statičke analize samog **KORG PA Manager** prebačeni su u `pa_tool` kao
`data/manager_resources.json` i `data/factory_names.json`, sa modulom `pa_tool/manager.py`
i komandom `python run_pa_tool.py manager`.

### 6.1 Resursi samog Managera — 175, svi oporavljeni

| metrika | vrijednost | status |
|---|---:|---|
| resursa ukupno | 175 | VERIFIED |
| čitljivo iz bajtova fajla | 126 | VERIFIED |
| oporavljeno iz memorijske slike | **49** | VERIFIED |
| nemapirano | **0** | VERIFIED |

Paker: **PECompact2** (`PEC2` @`0x1C8`, stub @`0x00B7E218`). `.text`:
`virtual_size` 70.770.688 vs `raw_size` 24.860.160, entropija **8.000**.
Zato 49 resursa živi u virtuelnom repu i vidi ih se samo u 1:1 slici.

### 6.2 Tabela fabričkih imena — **struktura, ne regex**

Ovo je najvažnija dopuna. Prethodni `factory_catalogue.csv` nastao je kao
printable-ASCII regex: dao je 1329 unosa od kojih su **50 bili đubre** isečeno na
granici bajta (`(JSULJG3`, `26JS2YIVC`, `4O&NcIG`…).

Izmjerio sam stvarni zapis u `FAC_ROM_PCG`:

```
+0    18 B   ime, null-padded ASCII
+18    1 B   0x04              konstanta
+19    2 B   indeks zapisa, u16 BIG-endian
+21    1 B   flag sekcije      (0x02..0x08)
+22    2 B   0x00 0x00         konstanta
=> korak 24 B
```

Dokaz da je indeks baš tu: `idx 0 → 00 00`, `idx 9 → 00 09`, `idx 546 → 02 22`,
`idx 732 → 02 dc`.

**Rezultat:** 1325 zapisa u dvije sekcije, indeks **strogo sekvencijalan unutar svake**:

| sekcija | zapisa | indeks | od | do |
|---|---:|---|---|---|
| 0 | 1227 | 0..1226 | `AcousticPiano GM` | `Deep Noise` |
| 1 | 98 | 2047..2144 | `Digit.DRAWBARS` | `Synth Kit 3` |

Skok 1226 → 2047 **nije greška** — to je granica sekcije: melodični instrumenti,
pa drum kitovi (potvrđeno imenima na početku druge sekcije: `Standard Kit`, `Room Kit 1`,
`HipHop K…`).

**Zato je regex odbacio 45 pravih imena**, npr. `Accordion 16,8'`, `Cello`, `Dog`,
`DW8000 EP`, `Bells`. Sada su sva tu.

### 6.3 Ostali nalazi iz Managera

| nalaz | vrijednost | status |
|---|---|---|
| kontejner šablone (`NEW_*`, `TIKTOK*`) | 11 | VERIFIED |
| imena drum kitova / multisemplova (`FAC_ROM_KMP`) | 700 | NEPOTVRĐENO |
| ugradjeni PE moduli | 3 | VERIFIED |

Ugrađeni moduli po tipu odgovaraju PE1/PE2/PE3 iz `v5-stream.bin`:

| resurs | veličina | mašina | sekcije |
|---|---:|---|---|
| `res_0036` | 493.256 | x86 | 3, `PECompact2` |
| `res_0041` | 11.264 | x64 | `UPX0`, `UPX1`, `.rsrc` |
| `res_0042` | 856.576 | x64 | 7, REX SDK ×2 |

**Ista porodica modula, ne isti fajlovi** — hash-evi se razlikuju, pa se tvrdi
samo podudarnost tipa/layouta.

### 6.4 Zašto je `FAC_ROM_KMP` NEPOTVRĐENO a `FAC_ROM_PCG` VERIFIED

`FAC_ROM_KMP` **ne koristi** isti 24-bajtni zapis kao `FAC_ROM_PCG`. Za njega su imena
izvučena kao čisti ASCII nizovi, što je slabija metoda — zato nosi **NEPOTVRĐENO**, a ne
VERIFIED. Brojevi su u `factory_names.json`, polje `drum_kits_and_multisamples`.

### 6.4 `FAC_ROM_KMP` — OC32 je razbijen, tabela imena je VERIFIED

**Ovo je bilo NEPOTVRDENO, sada je VERIFIED.**

`FAC_ROM_KMP` ima `OC32` kompresovan payload na `0x44`. Deklarisana veličina je
**1.141.260 B** a fajl ima samo 421.364 B — dakle sadržaj je stvarno kompresovan.

Dekodiranje kodekom iz `pa800_remap/native/codec.py` (`_barc_decompress`):

| korak | rezultat |
|---|---|
| prvi pokušaj | `CBarc left 30 compressed bytes unread` |
| trailer | **30 B** — to je tačno broj koji je dekoder prijavio |
| drugi pokušaj (trim 30) | **1.141.260 B dekodirano = deklarisana veličina** |
| XOR kontrolni bajt | izračunato `0x5b`, u fajlu `0x5b` → **poklapa se** |

Trailer nije smeće: sadrži `KBEG` i `KEND`, dakle terminator lanca.

**Unutar dekodiranih podataka je prava tabela:**

```
+0    18 B   ime, space-padded ASCII
+18   22 B   binarna polja (velocity slojevi, key range, pokazivaci)
=> korak 40 B (0x28)
```

| metrika | vrijednost |
|---|---:|
| zapisa u tabeli | **1599** |
| imenovanih | 1548 |
| praznih slotova (zadržani) | 51 |
| jedinstvenih imena | **1337** |
| raspon tabele | `0xAFF78` .. `0xBF950` |

Prava imena artikulacija koja su sada dostupna:

- `Inch Std1`..`Inch Std6`, `+Rim Gate1 p/mf/f`, `+Rim Plate mf`, `Natural 1a`..`2d`,
  `zz Hi center`, `zz Hi Rim`, `zz Floor`
- sufiksi `p` / `mf` / `f` / `ff` su velocity artikulacije istog doboša

Uz to, u **drugom regionu** (trake uzoraka) izvučeno je **1092 prava imena** tipa
`BD 22 Inch Std1`, `BD Natural1 mf`, `BD 24 inch Open p`.

**Zašto je prvi pokušaj davao 195 imena, a ne 1599:** `FAC_ROM_KMP` **nije**
plaintext kao `FAC_ROM_PCG` — njegov sadržaj je kompresovan, pa je regex po
kompresovanom toku hvatao slučajne bajtove. Zato su prije postojala imena kao
`Natural1+` i `MondoKAP/` (isečena) a nisu postojala `Natural 1a`, `zz Hi center`.
Sada je razlika između ta dva slučaja izmerena, a ne pretpostavljena.

### 6.5 Filter za šum u regionu uzoraka

Sirovi ASCII nizovi iz dekodiranih podataka sadrže i **šum kompresovanog toka**
(`1iDv`, `5Izo`, `NBIy`). Zato se ime prihvata samo ako ima **2+ riječi** i
poklapa se sa KORG rječnikom kitova/artikulacija. Brojevi:

| | |
|---|---:|
| sirovi ASCII nizovi | 2297 |
| nakon filtera | **1092** |

---

## 8. F3 — STY konverzija: dokazano da radi + jedan otvoren nalaz

### 8.1 Zavisnost koja je blokirala konverziju — nađena

`setconvert/conv.py` uvozi `pa800_native`, kog **nema** u workspace-u:

```
from pa800_native.oc31_codec import decode_oc31
from pa800_native.oc31_fast_encoder import encode_oc31_greedy
from pa800_native.oc31_optimal_encoder import encode_oc31
```

Nađen je na disku: `E:\BAJA MUSIC\BAJA_MUSIC_PA800_MANAGER_V1_COMPLETE_2026-09-22\pa800_native\`
(zajedno sa `oc31_codec.py`, `oc31_optimal_encoder.py`, `sty_container.py`, `type09_codec.py`).

**Fali samo `oc31_fast_encoder.py`.** Umjesto da ga prepisujem, mapirao sam to jedno ime
na optimalni enkoder koji postoji:

```
encode_oc31_greedy -> encode_oc31 (optimal encoder)
```

To je **NEPOTVRDENO da daje identične bajtove** kao pravi greedy enkoder — zato je
takav izlaz označen tako, a ne kao VERIFIED.

### 8.2 Šest familija stilova (VERIFIED, postojeći kod)

| family | modeli | tabela imena |
|---|---|---|
| `legacy` | Pa50, Pa60, Pa80 | 24 B |
| `transition` | Pa2X? | 24 B |
| `pa1x` | Pa1X, Pa1X Pro | 24 B |
| `pa800` | Pa800 | 24 B |
| `pa3x` | Pa3X, Pa3X Pro, Pa300, Pa600, Pa900 | 24 B |
| `pa4x` | Pa4X, Pa4X Pro, Pa700, Pa1000 | **64 B** |

### 8.3 Dokaz: stvarna konverzija bajtova

`proof_sty_conversion.py` na stvarnom fajlu:

| | |
|---|---|
| izvor | `FAVORITE01.STY`, 608.333 B |
| fingerprint | family **pa800**, 32 stila, 32 STS, `kbeg_ok=True` |
| konverzija | `pa800` → `legacy` |
| izlaz | 526.515 B |
| fingerprint izlaza | family **legacy**, 32 stila → **konverzija se verifikuje** |
| sha256 ulaza prije/poslije | **identičan** → ulaz nepromijenjen |

### 8.4 OTVOREN NALAZ: round-trip nije identičan

Konverzija nazad (`legacy` → `pa800`) daje **527.785 B** umesto originalnih 608.333 B,
i **520.768 od 527.785 bajtova se razlikuje** (prva razlika na `0x5`).

**To NIJE dokaz da je konverzija pokvarena.** Tri moguća objašnjenja, nijedno još dokazano:

1. izlaz je semantički isti ali **rekompresovan** (novi CBarc/OC31 tok umesto originalnog);
2. `encode_oc31_greedy` shim (v. 8.1) daje drugačije bajtove od originalnog enkodera;
3. konverzija je **stvarno gubitna** između `pa800` i `legacy`.

**Šta bi ga riješilo:** poređenje *dekodiranih* payloada (ne kompresovanih) prije i poslije,
i pravi `oc31_fast_encoder.py` ako postoji na drugom disku.
Zato `pa_tool` NIJE označio `sty_family` kao potpuno VERIFIED — stoji **VERIFIED/N**.

### 8.5 Drugi nalaz: fingerprinter i tabela familija se ne poklapaju

`families.py` kaže da `InitPx01/02` pripadaju **`transition`** familiji (Pa2X kandidat,
„OC32 to OC31 mix"). Fingerprinter ih svrstava u **`legacy`**:

| fajl | očekivano po tabeli | fingerprinter | glasovi |
|---|---|---|---|
| `Initpa01/02/03.STY` | `legacy` | `legacy` ✓ | legacy 14, pa1x 6, transition 6 |
| `InitPx01.STY` | `transition` | **`legacy`** ✗ | legacy 14, pa1x 6, transition 6 |

Fingerprinter ima **jednak broj glasova** na `InitPx01` (14/6/6), pa odluka zavisi od
redoslijeda. Ovo je **NEPOTVRDENO** — ne tvrdim da je fingerprinter pogrešan, samo da se
ne poklapa sa tabelom familija na tom uzorku.

---

## 9. Definicija „gotovo" — trenutno stanje

- [x] novi folder `pa_tool\` postoji i samostalan je
- [x] popis svih funkcija sa dokazom i statusom (ovaj dokument)
- [x] `data\*.json` popunjen iz `.md` nalaza, svaki zapis sa `status` + `evidence`
- [x] `python run_pa_tool.py doctor` prolazi sa 0 grešaka
- [x] `detect` tačno prepoznaje `.SET`/`.PCG`/`.KST`; ulaz nepromijenjen
- [x] `validate` daje PASS/FAIL/UNKNOWN sa offsetom
- [x] 86 testova prolazi bez dodatnih instalacija
- [x] `STATUS.md` mapira svaku funkciju na status
- [x] nalazi iz `2.exe_pa_analysis` prebačeni u `data/` (175 resursa, 1325 + 1599 imena)
- [x] `FAC_ROM_KMP` OC32 razbijen → tabela imena VERIFIED
- [x] OC31/OC32 dekodiranje ugrađeno u `container.py` + CLI komanda `oc`
- [x] `convert` — planiranje + odbijanje prije rada (7 smjerova VERIFIED, 2 odbijena)
- [x] `setconvert` + `pa800_native` integrirani → **stvarna STY konverzija radi**
- [ ] round-trip identitet (v. §8.4) — otvoreno
- [ ] F4 remap, F5 `.KST` edit i MERGE, F6 GUI
