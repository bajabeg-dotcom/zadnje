# PA800 OS v202 Reverse Engineering — Izvještaj

## Sažetak

Izvršen je potpuni reverse engineering oficijelnog KORG PA800 OS v2.02
PKG-a (134 MB, preuzet sa `cdn.korg.com/us/support/download/files/...Pa800_os_res_v202.zip`).
PKG je dekompresovan, 94 logička fajla su grupisana, 13 PCG/KMP
instrumenata, 3 STY banke (148 stilova), 3 drum kit fajla i 69 JPEG
slika (ikone, wallpaper) su ekstraktovani.

## PKG struktura

### Format

- **Veličina**: 134,050,105 bajtova (134 MB)
- **Magija**: `FD 00 00 16 07 FD 71 31` (PA800 PKG signature)
- **Format**: Flat konkatenacija OC20 stranica sa CBarc kompresijom
- **746 OC20 stranica** — svaka dekompresovana u 256 KB (ukupno 150 MB)
- **94 logička fajla** — KORF resource records sa KBEG/KEND indeksom
- **94 KORF markera**, 92 KBEG, 97 KEND, 211 OC31, 25 OC32

### Kompresija

- **OC20**: CBarc (custom LZSS) — 256 KB po stranici
- **OC31/OC32**: CBarc sa XOR checksum-om — za inner STYLE/STS resurse
- **MZX10**: Posljednji 4 MB — OS firmware updater (Motorola/Freescale)

## Ekstraktovani resursi

### Logički fajlovi (94 total)

| Tip | Broj | Opis |
|-----|------|------|
| GUI resurs | 1 (4.26 MB) | Forme, dijalog, ikone, poruke |
| PCG/KMP instrumenti | 13 | Acoustic Piano GM, Finger Bass DNC, itd. |
| STY banke | 3 (148 stilova) | Easy Listening, Jazz, Latin |
| Drum kit fajlovi | 3 | Pop Std. Kit RX, Jazz Kit, itd. |
| Jezici | 7 | Dutch, English, French, German, Italian, Russian, Spanish |
| Ostali resursi | 67 | Wallpaper, ikone, sistemske tabele |

### Instrumenti (PCG/KMP banke)

#### Acoustic Piano GM (file_072, 264 KB)

Master ime: `AcousticPiano GM`

Varijante (5):
- **Wide** — širi stereo raspon
- **Dark** — tamniji ton
- **Grand** — klasični grand piano
- **Classic** — klasični piano
- **Jazz** — jazz piano ton

Sub-reference (ukupno ~30 instrumenata):
- RX DEMO, Honky-T, Detuned EP, EP1 Veloc.sw, Bell, Phase, Bossa,
  & Vibes, String, Rock, & Mov, Pad, RX, Bright, A A 1, Ac. , Octave,
  Snap, & Vox, Techno, etc.

#### Finger Bass DNC (file_058, 1.5 KB)

Single sound — Finger style bass sa DNC (Drum Noise Cancel) trigger.

#### Ostali PCG bankovi (file_077, file_079)

- 27 instrumenata u file_077: Grand Piano, Gospel Organ V, Overdrive
  Gtr, Soft Thin EP, Jazz Flute, Kool Funk, Strings, Brass, etc.
- 30 instrumenata u file_079: Funk R&B, Elektrik, Classic, Talkin'
  Jazz, Rhythm & Blue, Swing, Shuffle, etc.

### Drum kitovi

#### Pop Std. Kit RX (120.0.4)

| MIDI Key | Instrument | Layers | Velocity switches |
|----------|------------|--------|-------------------|
| 36 | Kick | 1 | (single) |
| 38 | Snare | 2 | 80 |
| 42 | Closed Hat | 1 | (single) |
| 46 | Open Hat | 2 | 90 |
| 48 | Tom 1 | 2 | 85 |
| 50 | Tom 2 | 2 | 85 |
| 49 | Crash | 2 | 80 |
| 51 | Ride | 2 | 80 |
| 57 | Crash 2 | 2 | 85 |

#### Jazz Kit

| MIDI Key | Instrument | Layers | Velocity switches |
|----------|------------|--------|-------------------|
| 36 | Kick | 1 | (single) |
| 38 | Snare | 2 | 70 (lower threshold) |
| 42 | Closed Hat | 1 | (single) |
| 46 | Open Hat | 1 | (single) |
| 49 | Crash | 2 | 75 |

### STY banke (148 stilova)

#### file_076: Easy Listening banka (48 stilova)
- Easy Listening, Slow Band, Jump, Waltz, Operetta, Jive, Quick Step,
  Orchestral Tango, Argentina, Pasodoble, Hully Gully, etc.

#### file_082: World music banka (49 stilova)
- Hawaiian, Flamenco 4/4, Mexican Waltz, Celtic Dream, Ballad,
  Scottish Reel, Minuett, Baroque, Orleans, Mariachi, etc.

#### file_085: Jazz/Big Band banka (51 stilova)
- Bigger Band, Medium, Jazz Club, BeBop, Django, Blues, Slow Swing,
  Orchestral, 70's Beat Groove, Charleston, Movie Ballad, etc.

### GUI resurs (file_000, 4.26 MB)

GUI resurs sadrži Pascal-style imenovane elemente (PA800 koristi
Delphi VCL-like framework):

- **Forme**: `frmConfirm`, `frmArray8Knob`, `frmConfirm`, etc.
- **Tabele**: `PtblDialog`, `tblChord`, `tblHelp`
- **Kontrole**: `MasterTransp`, `PopupMenu`, `TimeSign`, `RecGMod`,
  `SongSelect`, `Drawbar`, `JukeBoxRT`
- **Poruke**: `Messages`, `skMessages`
- **Lokalizacije**: 7 jezika (Dutch, English, French, German, Italian,
  Russian, Spanish)
- **Sistemske tabele**: `LicenseManCer`, `Generic`, `IntFunctiona`,
  `ContainerHL`

Pronađene tekstualne oznake u GUI resursu:
- "Write Style Per" — zapisivanje stila u memoriju
- "Are you sure?" — dijalog potvrde
- "Touch Set" — postavke Touch sučelja
- "TrebMz" — Treble (visoke frekvencije) kontrola
- "Intern@VFX" — Internal FX
- "VoicCVrof" — Voice Processor Profile
- "LabelGGToteD" — Label / Total
- "Chord Vari" — Chord Variations (CV)
- "Key Range" — Key Range kontrola
- "Drum Kit" — Drum Kit editor
- "Oscillator" — Oscillator editor

### Slike (69 JPEG)

- **Wallpaper 1**: 47 KB (`file_000_T14_off000b8d84_jpg_at_3cfc1b.jpg`)
- **Wallpaper 2**: 48 KB (`page_0015_off004835f8_jpg_at_5881.jpg`)
- **Wallpaper 3**: 36 KB (`page_0047_off005ae917_jpg_at_3548a.jpg`)
- **UI ikone**: 28 KB - 2 KB (više desetina manjih ikona)

Sve slike su dostupne u GUI-ju pod "PA800 OS resursi" tabom, sa
thumbnail pregledom.

## Implementacija u KORG PA Manager aplikaciji

### Mapa instrumenata

PA800 instrumenti su integrisani u `korg_pa_optimizer.py` kao:
- 8 žanrova sa preferiranim instrumentima (Pop, Rock, Jazz, Ballad,
  Latin, Dance, Folk, Balkan)
- Svaki žanr ima `preferred_kit`, `preferred_bass`, `preferred_piano`
- FX routing prilagođen žanru (FX C i D sends)

### Drum kit layer mapiranje

`PA800_DRUM_KIT_LAYERS` u `korg_pa_optimizer.py` sadrži dokumentovane
velocity layer switches za Pop Std. Kit RX i Jazz Kit. Optimizator
koristi ovo za:
- Velocity kompresiju koja poštuje layer threshold-e
- RX/DNC trigger dodavanje na odgovarajućim kontrolerima
- Preporuku drum kita na osnovu žanra

### Shared optimization engine

`korg_pa_optimizer.py` je jedan izvor istine za Song i Style
optimizaciju:
- Velocity curves (target opseg 20-110, knee 80)
- Articulation engine (guitar/bass/drum)
- RX/DNC trigger remap (CC 80-87)
- Cross delay echo (1/8 note)
- FX routing (genre-aware)
- Genre detekcija (tempo + time signature + track roles + density)

### Cross-model konverzija

`korg_pa_converter.py` podržava konverziju između svih 12 Korg PA
modela:
- v2 (PA80/PA1X) — fail-closed prema v4/v5
- v4 (PA2X/PA500/PA600/PA800) — proven v4↔v5 konverzija
- v5 (PA3X/PA3XLE/PA4X/PA700/PA900/PA1000) — proven EI/TD resize

### Integrirani GUI

`korg_pa_manager_gui.py` objedinjuje sve funkcije:
1. **Konverter** — cross-model STY konverzija
2. **Song Optimizer** — MIDI/SMF optimizacija
3. **Style Optimizer** — STY optimizacija
4. **Inspector** — strukturalni izvještaj STY fajla
5. **PA800 OS resursi** — browser ekstraktovanih slika i instrumenata

## Sigurnosni model

- PA800 PKG ekstrakcija je **read-only** — originalni PKG se nikada
  ne prepisuje
- Sve transformacije su **fail-closed** — bez dokaza se ne izmišljaju
  podaci
- Round-trip verifikacija obaveznih za sve izmijenjene resurse
- KBEG/KEND indeks se uvijek ponovo generiše

## Šta nije implementirano (zahtijeva hardverski dokaz)

- **Native sound bank/program remap** — zahtijeva kontrolisani
  BASELINE/BASELINE_REPEAT/ONE_CHANGE triple sa hardvera
- **Kompletna legacy ↔ nested ID-09 strukturna sinteza** — samo empty
  i user_namespace_09 su potvrđeni
- **PCG/Sound bank/program remap tabele** — zahtijeva dodatni RE
- **Fizička instrumentalna validacija** — generisani fajlovi su
  strukturno validni, ali finalna potvrda prihvata zahtijeva test na
  hardveru
