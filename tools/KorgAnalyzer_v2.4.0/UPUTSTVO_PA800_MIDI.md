# PA800 MIDI OPTIMIZER / RESTORATOR

Deterministički, dokazima vođen MIDI optimizator za Korg Pa800.
Radi **na tvojoj mašini**, offline. Original nikad ne dira.

---

## 1. Glavno pravilo (i zašto)

**NE MIJENJAJ TICK. NE MIJENJAJ PITCH. NE IZMIŠLJAJ DOKAZ. NE PREPIŠI ORIGINAL.**

Program smije mijenjati samo:

| Smije | Ne smije (nikad automatski) |
|---|---|
| velocity (po dokazu ili tvom pravilu) | tick (quantize, humanize, swing, snap) |
| CC (volume, pan, reverb, chorus...) | pitch (transpose, pitch shift) |
| Program Change / Bank Select | pomicanje note |
| RX / DNC identitet zvuka | izmišljanje dokaza |
| EFX i artikulacija (uz dokaz) | prepisivanje originala |

Za svaku promjenu program pravi **plan** u kojem piše: traka, rola, operacija,
stara vrijednost, nova vrijednost, razlog, dokaz i nivo pouzdanosti.
Prije i poslije se pravi **otisak prstiju svih nota** (tick + pitch + redoslijed).
Ako se otisak razlikuje → `VALIDATION_FAILED` i izlaz se ne smatra važećim.

---

## 2. Dokazi (evidence) i nivoi pouzdanosti

Program ne odlučuje "po osjećaju". Svaka odluka nosi dokaz:

| Izvor | Šta dokazuje |
|---|---|
| **FACTORY STYLE** | kako se svira (groove, pattern, sekcije) |
| **FACTORY SOUND / OSC** | šta je instrument (identitet, banka, program) |
| **RX / DNC** | dokazani remap na artikulacioni zvuk |
| **GOLD** | dodatni stvarni dokaz iz tvog provjerenog materijala |
| **MANUAL** | šta parametar znači (Korg Pa800 Owner's Manual) |
| **DEMO** | samo EFX i artikulacija |
| **HARD LIMIT** | Key Range — nepromjenjivo |
| **USER RULE** | šta ti tražiš (deterministički) |

Nivoi:

- **VERIFIED** — više nezavisnih stvarnih izvora se slaže
- **DOCUMENTED** — zvanična/kurirana tabela to tvrdi
- **STRONG_EVIDENCE** — direktno izmjereno (tvoj gold korpus, factory podaci)
- **CANDIDATE** — moguće, ali **nije dokazano → samo predlog**
- **UNKNOWN** — nema dokaza → **ništa se ne mijenja**

Samo prva tri nivoa se smiju primijeniti automatski.

Prioritet se **ne gleda globalno** nego po pitanju:

| Pitanje | Jači dokaz |
|---|---|
| groove | Factory Style > Gold |
| identitet zvuka | Factory Sound / OSC > Gold |
| balkan strum / pikanterija | Gold može biti jači ako direktno pokazuje |
| RX remap | dokazani RX (factory + demo) |
| EFX | Demo |
| značenje parametra | Manual |

---

## 3. Kako se koristi

### Brzo (jedan fajl)

```bash
python -m pa800_midi analyze  "PAT DO FAJLA.mid"      # samo pogledaj, ništa se ne dira
python -m pa800_midi plan     "PAT DO FAJLA.mid"      # napravi plan, ne primjenjuj
python -m pa800_midi optimize "PAT DO FAJLA.mid"      # plan + primjena dokazanog
```

### Tvoj corpus (folder)

```bash
python -m pa800_midi batch "D:\MIDI PJESME"
```

Dobiješ: zbirni `batch_report.txt`, `batch_report.csv` i `batch_report.json`,
plus po svakom fajlu poseban `report.txt` / `report.json`.
Sve ide u `workspace/pa800_midi/` — originali ostaju netaknuti.

### Key Range — pravilo po tipu instrumenta

Key Range je **HARD LIMIT**: program ga nikad ne krši i nikad ne pomjera notu.
Samo prijavi `KEY_RANGE_CONFLICT` i ti odluči.

Redoslijed (jače gore):

```
1. tvoje pravilo            --key-range "bass=E1-C4,guitar=D2-E6"
2. izmjereni profil zvuka   (npr. Finger Bass RX 28-60 -- dokaz za TAJ zvuk)
3. pravilo po tipu          (klavir A0-C8, bas E1-G4, gitara E2-E6, saksofon A#2-A#5...)
4. bez tvrdnje              (bubnjevi, sintisajzer, FX, nepoznato)
```

Sve što program zna pogledaj ovako:

```bash
python -m pa800_midi key-range
```

Ispiše tabelu po tipu, **koliko tvoje pjesme stvarno sviraju** (iz GOLD korpusa)
i gdje je GOLD dokaz proširio osnovno pravilo (`*`). Primjer iz tvog korpusa:
bas-gitara po tipu je E1-G4, ali tvoje pjesme sviraju D1 u 21 pjesmi — pa je
pravilo prošireno i program te više ne gnjavi lažnim konfliktima.

Promjena pravila:

```bash
python -m pa800_midi optimize "pjesma.mid" --key-range "guitar=D2-E6"
python -m pa800_midi optimize "pjesma.mid" --key-range-file moja_pravila.json
python -m pa800_midi optimize "pjesma.mid" --key-range-strict   # samo fizičko pravilo
python -m pa800_midi key-range --write pa800_midi/data/key_range_rules.json
```

`--key-range-strict` isključuje GOLD dopunu i sudi samo po osnovnom pravilu
(tipa "gitara ne ide ispod E2"). Koristi ga kad hoćeš da vidiš sve moguće
konflikte, bez obzira na tvoj materijal.

### Pravila (ti odlučuješ, program izvršava tačno)

```bash
python -m pa800_midi optimize "pjesma.mid" --rule velocity=-15
python -m pa800_midi optimize "pjesma.mid" --rule cc7=100 --rule cc10=70
python -m pa800_midi optimize "pjesma.mid" --rule velocity=-10 --rule cc91=25
```

Pravila su deterministička:

```
new_velocity = clamp(old_velocity - 15, 1, 127)
CC7 = 100
```

Tick i pitch se i tada ne diraju.

### Zabranjeno je zabranjeno

```bash
python -m pa800_midi optimize "pjesma.mid" --rule transpose=2
```

→ ODBIJENO: `NOTE_IMMUTABILITY (pitch)`. Ako baš hoćeš, moraš svjesno:

```bash
python -m pa800_midi optimize "pjesma.mid" --rule transpose=2 --waive-immutability
```

...i to se u izvještaju vidi kao `WAIVED` (prekršeno pravilo na tvoj zahtjev),
a validacija to prijavi.

### Pitaj lokalnog Qwena

```bash
python -m pa800_midi ask "pjesma.mid" "Ima li konflikata i šta je primijenjeno?"
python -m pa800_midi ask --report workspace/pa800_midi/reports/<run>/report.json "Objasni mi zašto ovaj zvuk"
```

Qwen dobija **samo tvoj izvještaj** i uputstvo da ne izmišlja. Radi offline.

---

## 4. Šta program stvarno zna (i šta ne zna)

### Zna (dokazano na stvarnom materijalu)

- **435 fabrickih Pa800 stilova** u indeksu (18 banki, imena, broj elemenata,
  geometrija, SHA-256) — dekodirano tvojim vlastitim dekoderom.
- **54 demo MIDI fajla** kao EFX/artikulacioni dokaz (npr. `Trumpet DNC`,
  `Nylon Guitar DNC`, `Tenor Sax DNC`).
- **Fabricka tabela zvukova i drum kitova** Pa800 + GM→Pa800 remap tabela.
- **7 izmjerenih profila instrumenata** (key range + velocity):
  Grand Piano RX, Finger Bass RX, Concert Str.RX, Trumpet DNC, Tenor Sax DNC,
  Arabic Accordion, Spanish Guitar.
- **Key Range kao HARD LIMIT** — provjerava se i prije i poslije remapa:
  ako nota ispadne iz opsega ciljnog zvuka, dobiješ `KEY_RANGE_CONFLICT`
  i nota ostaje gdje je.

### Ne zna (i zato to ne tvrdi)

- Notni sadržaj unutar `.STY` stilova nije dekodiran (kompresovani stream),
  pa se groove iz stila ne tvrdi bez izvoza u MIDI.
- Nema native upisivanja u Korg fajlove — izlaz je MIDI.
- Ako nema dokaza, piše `UNKNOWN`. Bolje UNKNOWN nego pogrešan Pa800 izlaz.

---

## 5. Izlazi

| Fajl | Sadržaj |
|---|---|
| `output/<ime>_pa800.mid` | optimizovani MIDI (samo ako je nešto primijenjeno i validirano) |
| `reports/<vrijeme>_<ime>/report.txt` | čitljiv izvještaj na bosanskom |
| `reports/<vrijeme>_<ime>/report.json` | isti podaci za mašinu (i za Qwena) |

Izvještaj uvijek sadrži: **SHA-256 izvora**, **SHA-256 izlaza**, plan,
dokaze, konflikte, rezultat validacije i poštena ograničenja.

---

## 6. Gold — tvoj corpus (830 pjesama)

GOLD je tvoj provjereni materijal. Program iz njega **mjeri** (ne prepisuje):

```bash
python -m pa800_midi index gold "D:\MOJ DOKAZANI MATERIJAL"
```

Za svoj corpus: raspakuj `PA800_GOLD_CORPUS_830.zip` (u istom paketu je i
`GOLD_INDEX.json` s gotovim mjerenjem) i pokreni:

```bash
python -m pa800_midi index gold "C:\GOLD"
```

Mjeri dvije stvari:

1. **po roli** (bass, rhythm_guitar, piano...): medijanu velocity, 10-90%
   raspon, gate i gustinu nota. Trebaju **3+ uzorka** za `STRONG_EVIDENCE`;
   1-2 uzorka su samo `CANDIDATE` (predlog).
2. **po tipu instrumenta**: koji se opseg stvarno svira, koliko nota i u koliko
   pjesama prelazi granicu pravila. To je dokaz koji proširuje pravilo opsega
   — ali samo kad je jasan: najmanje **3 pjesme i 5 nota**. Jedna zalutala
   nota nikad ne mijenja tvrdi limit.

Program **ne prepisuje** iz GOLD-a groove, timing, pitch ni Key Range drugačije
osim tog proširenja (koje uvijek piše u izvještaju, i možeš ga isključiti s
`--key-range-strict`).

Trenutni gold indeks: tvojih **830 fajlova** (817 .mid + 13 .kar) iz
`AUTO_SELECTED_GOLD.zip`, izvor `C:\Users\Baja\Desktop\Midi Bazaa`.

---

## 7. Corpus u zipu (za sebe i za arhivu)

Uz program ide i tvoj GOLD korpus spakovan:

```
PA800_GOLD_CORPUS_830.zip     -- 830 pjesama + GOLD_INDEX.json + README
PA800_GOLD_CORPUS_830.zip.sha256
```

Raspakuj gdje hoćeš i pokreni `index gold` na tu mapu; indeks iz zipa možeš
samo prepisati u `pa800_midi/data/gold_index.json`. Originali se nikad ne diraju.

---

## 8. Factory indeks (ponovno gradjenje)

```bash
python -m pa800_midi index factory "D:\PA800 FACTORY\STYLE" --out pa800_midi/data/factory_index.json
python -m pa800_midi index demo    "D:\DEMO MIDI"
```

Koristi tvoj postojeći binarni dekoder (`python -m korg_binary_analyzer decode`).

---

## 9. Provjera da sve radi

```bash
python -m pa800_midi selftest
```

10 provjera, uključujući: tick nepromijenjen, pitch nepromijenjen, validacija OK,
transpose odbijen, original netaknut.
