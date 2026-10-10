# Riješeno 01 — PCG chunk ID-ovi i varijante PA588 / PA3X Le

Datum: 2026-10-06  
Verzija: `pa_tool 0.1.2`

Ovaj prolaz koristi stvarne fajlove sa korisnikovog Google Drivea i javnu tehničku
dokumentaciju. Zaključci su namjerno uži od podataka: tamo gdje je dokazana samo
Managerova interna klasa, ne tvrdi se identičan format bajtova.

## 1. Problem F: `PBK1` / `PRG1` / `RLP1`

### 1.1 Stvarni PA `.PCG` uzorci

| fajl | bajtova | SHA-256 | nalaz |
|---|---:|---|---|
| `USER01.PCG` | 493 | `accc652bfa0bcb81f5eb2f8cd0433fa26ccb95f7344e9196dd59c563cd35cc2b` | `01 00 01 14`; `KORF@0x17`; `OC31@0x44`; `KBEG@0x1d9`; `KEND@0x1e5`; deklarisana dužina tačna |
| `USERDK.PCG` | 1.986 | `288d9490cc3b5c12cf7ca253ec1ebc10af66411b3d7d1e2cdeaf9a5b6a375469` | `01 00 01 14`; `KORF@0x17`; `OC31@0x44`; `KBEG@0x7ae`; `KEND@0x7ba`; deklarisana dužina tačna |
| `ROM.PCG` | 131.072 | `146192e689134ac01e7f8948c57a3ce86225753637d226cf6b038043fefddbe2` | Pa1X fragment; `KORF@0x17`; 4×`OC31`; 258×`OC32` |
| `ROM_full.PCG` | 698.158 | `575bd834cb9b0fe5e7f048412d03d8be9d82c6e82fb49fbdbd7a73554a1df06b` | agregat: 10×`KORF`, 21×`OC31`, 824×`OC32` |

U sva četiri uzorka: `PBK1=0`, `PRG1=0`, `RLP1=0`.

**Zaključak (VERIFIED za ovaj korpus):** KORG PA `.PCG` fajlovi ovdje su `KORF`
kontejneri. `PBK1/PRG1` nisu univerzalni potpisi PA `.PCG` fajla.

### 1.2 Šta tokeni stvarno znače

Javno dokumentovani KORG workstation PCG dijalekt koristi zaglavlje `KORG`, zatim
`PCG1`, pa `PRG1` kao grupu programskih banaka i `PBK1` kao PCM programsku banku.
To je drugi dijalekt pod istom `.PCG` ekstenzijom.

`RLP1` je dokumentovan za `.KMP`: relative-parameter chunk sa 18 bajtova po uzorku.
Nije PCG potpis.

Izvori:

- https://korgforums.com/forum/phpBB3/viewtopic.php?t=50398
- https://forums.karma-lab.com/forum/showthread.php?t=4922
- https://www.manualslib.com/manual/675036/Korg-Tr.html?page=262

### 1.3 Šta ostaje ograničeno

PA Manager zaista sadrži poruke:

- `Missing ProgramsBank Chunk (PBK1).` @ `0x0024F824`
- `Missing ProgramsBank Chunk (PRG1).` @ `0x0024F880`

To dokazuje granu koja očekuje te chunkove, ali ne dokazuje da su oni dio PA `KORF`
dijalekta. Nova komanda `pcg` zato prvo klasifikuje dijalekt, a tek onda objašnjava
tokene.

## 2. Problem D: PA588 / PA3X Le

Drive artefakt `pcg-conversion-followup.json` sadrži statički rekonstruisanu factory
dispatch rutinu:

| varijanta | flag | grana | odabrana SOUND klasa | status |
|---|---|---|---|---|
| `PA588` | `0x2f07382` | `0x26a8590` | `KORG_PA_MANAGER.PA_OS_SOUND_PA500` | **HIGH** |
| `PA3X Le` | `0x2f0737a` | `0x26a824a` | `KORG_PA_MANAGER.PA_OS_SOUND_PA3X` | **HIGH** |

**Zaključak:** u PA Manageru se PA588 tretira kao PA500 varijanta na nivou SOUND
handlera, a PA3X Le kao PA3X varijanta. Status je `HIGH`, ne `VERIFIED` za format,
jer dijeljenje klase ne dokazuje identične serijalizovane bajtove niti potpunu
kompatibilnost svih parametara.

## 3. Implementacija

Dodano:

- `data/pcg_chunks.json` — uzorci, hash-evi, dijalekti, semantika tokena i izvori;
- `pa_tool/pcg.py` — read-only klasifikator;
- `python run_pa_tool.py pcg <fajl.PCG> [--json]`;
- testovi za PA KORF, KORG/PCG1, chunk semantiku, hash dokaze i CLI;
- `models.json` sada opisuje PA588 i PA3X Le kao `HIGH` varijante s tačnom handler klasom.

Ni jedan ulazni fajl nije izmijenjen.
