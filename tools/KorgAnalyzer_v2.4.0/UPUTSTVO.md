# UPUTSTVO — Korg Binary Analyzer

Ovo je vodič za korištenje programa. Program **otvara tvoje Korg fajlove i
pokazuje šta je unutra** — koliko stilova, kako se zovu, koliko resursa, i da li
je svaki resurs ispravan.

**Program NIKADA ne mijenja tvoje fajlove.** Otvara ih samo za čitanje.

---

## 1. Kako pokrenuti (Windows)

1. Raspakuj folder gdje hoćeš (Desktop ili Dokumenti).
2. **Prvi put samo:** dvoklikni **`install.bat`** — pripremi okruženje.
   * Ne traži administratorska prava.
   * Ne mijenja PATH, ne registruje tipove fajlova.
   * Sve ostaje unutar tog foldera.
   * Ako već imaš Python sa Tkinterom, preskočiće instalaciju.
3. **Od sada uvijek:** dvoklikni **`KorgAnalyzer.bat`** → otvara se program.

Ako nešto ne radi, pokreni `KorgAnalyzer.bat` iz Command Prompta pa ćeš vidjeti
poruku o grešci.

---

## 2. Korištenje programa

Kad se otvori prozor, imaš traku sa dugmadima gore:

| Dugme | Šta radi |
|---|---|
| **Otvori fajl...** | Izaberi jedan fajl (.STY, .PCG, .KMP, .PRF, .BKP, .pkg) |
| **Otvori folder...** | Izaberi cijeli folder — obradi sve fajlove odjednom |
| **Dekodiraj i snimi kopije...** | Snimi dekodirane resurse na disk u folder koji izabereš |
| **Snimi izvjestaj...** | Snimi tekstualni izvještaj |
| **Očisti** | Očisti tabelu |

Dok analiza radi, vidiš progres i status dolje lijevo.

### Tabovi

**Pregled** — kratki sažetak.

**Stilovi** — najvažniji tab. Za svaki stil vidiš:

```
Fajl        Slot  Ime iz tabele      Ime iz streama     Poklapanje  Elemenata  Geometrija  Veličina
USER01.STY     0  Najlepša ženo     Najlepša ženo      DA                 7  {'28/80': 7}  44.9 KiB
USER01.STY    16  S.R Gospodin      Sto mi noći        NE                10  {'28/80': 10} 102.2 KiB
```

- **Ime iz tabele** = ime kako ga ti vidiš na klavijaturi (tabela imena u fajlu).
- **Ime iz streama** = ime koje je *stvarno* upisano u sam stil.
- **Poklapanje** = DA ako se poklapaju, NE ako se razlikuju (crveni red).

**Ako vidiš NE** — to znači da je stil kopiran u novi slot i samo je tabela
preimenovana, a unutra je ostalo staro ime. Program ti pokazuje oba imena da
znaš šta je stvarno unutra.

**Resursi** — svaki resurs sa ID-om, veličinom, XOR checksumom i SHA-256.
Sivo = nekompresovan (npr. tabela imena). Crveno = odbijen.

**Izvještaj** — cijeli tekstualni izvještaj, isti kao `Snimi izvještaj`.

---

## 3. Šta znače riječi u programu

| Pojam | Značenje |
|---|---|
| **DECODED_VALIDATED** | Dekodirano i **dokazano ispravno** — veličina se poklapa i XOR checksum prolazi |
| **DECODED_PARTIAL** | Većina resursa OK, neki odbijeni (vidi razlog) |
| **ATTEMPTED_FAILED** | Nije uspjelo — ništa nije prijavljeno kao tačno |
| **VALIDATED** | Nezavisna provjera izlaza prošla |
| **NE / crveni red** | Dva izvora imena se razlikuju — oba su prikazana |

**Zlatno pravilo programa:** nikad ne piše "validated" ako nije provjerio
veličinu **i** checksum. Sve ostalo je "kandidat" ili "odbijeno" — nikad
pogodak.

---

## 4. Šta program može i šta ne može

### MOŽE ✅

- Otvoriti `.STY`, `.PCG`, `.KMP`, `.PRF`, `.SBD`, `.PAD` (native kontejneri)
- Otvoriti `.BKP` backup i `.pkg` pakete (OC20 strane) — Pa1X/Pa800/Pa3X/Pa600 era
- Dekodirati CBarc kompresiju (isti algoritam koji koristi Korg firmware)
- Provjeriti XOR checksum svakog resursa pojedinačno
- Izvući imena stilova i uporediti ih sa tabelom imena
- Pokazati strukturu: elemente, geometriju, verzije
- Uporediti dva fajla
- Obraditi cijeli folder odjednom

### NE MOŽE ❌ (i to je iskreno prikazano)

- **Ne piše** native Korg fajlove — nema snimanja u `.STY`/`.PCG`. Program je
  samo za čitanje.
- **Ne dekodira** Pa4X-era `lfo-pkg` instalatere (Blowfish enkripcija).
- **Ne dekodira** `COMP16` module, `LFO-PKG` instalatere i sirove `PCM` banke.

Ovo nije zato što je program slab — nego zato što se te stvari još ne znaju
dokazati. Program to priznaje umjesto da pogodi.

---

## 5. Provjera da sve radi

Ako nisi siguran da program radi ispravno:

```bash
python -m korg_binary_analyzer selftest
```

Ispisaće 17 provjera. Ako piše **SAMOTEST: USPJEŠAN** — codec radi.

Samotest sam napravi testni fajl u memoriji (ne treba ti nijedan pravi fajl),
dekodira ga, provjeri pipeline i onda proba **pokvariti** fajl da dokaže da
program odbije neispravan ulaz umjesto da pogodi.

---

## 6. Dokazano na tvojim fajlovima

Program je provjeren na **12 pravih Pa3X fajlova** iz `PA3X_CLEAN_v2`:

| Provjera | Rezultat |
|---|---|
| Fajlova dekodirano | **12 / 12** |
| Resursa dekodirano | **914** — svi sa poklapanjem veličine i XOR-a |
| Resursa odbijeno | **0** |
| Stilova pronađeno | **451** |
| Imena se poklapaju | **409 / 451** |
| Vrijeme | ~14 sekundi za sve |

**42 razlike u imenima nisu greške** — to su stilovi koje si kopirao i
preimenovao samo u tabeli. Program ih pokazuje, ne skriva.

---

## 7. Izlazni fajlovi

Podrazumijevano sve ide u `workspace/`:

```
workspace/
├── reports/<run>/     report.txt, report.json, strings.txt, entropy.csv, ...
├── decoded/<ime>/     native.bin, resources/, manifest.json
└── logs/analyzer.log
```

`manifest.json` ima punu tabelu slotova — možeš je otvoriti u Notepad++ ili
učitati u Excel.

Kad koristiš **Dekodiraj i snimi kopije...**, sam biraš folder.

---

## 8. Za programere

```bash
python -m korg_binary_analyzer --help     # sve komande
python -m pytest                          # 49 testova
```

Ključni moduli:

- `korg_binary_analyzer/korg/codec.py` — CBarc + OC31/OC32 + OC20 (pravi algoritam)
- `korg_binary_analyzer/korg/containers.py` — struktura kontejnera + tabela imena
- `korg_binary_analyzer/korg/resources.py` — skener resursa sa dokazima
- `korg_binary_analyzer/korg/decoders/` — dekoderi koji se auto-registruju
- `korg_binary_analyzer/gui.py` — desktop interfejs
- `korg_binary_analyzer/selftest.py` — samotest bez potrebnih fajlova
