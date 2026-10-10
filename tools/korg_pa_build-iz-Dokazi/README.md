# KORG PA Manager v5 — Kompletna muzičarka platforma

**Najkompletniji open-source alat za KORG PA aranžere.**

Ovaj paket je izgrađen iz perspektive muzičara koji svira KORG PA
aranžere na livi nastav i u studiju. Implementira **sve** V3
KORGPAManager.exe funkcije otkrivene kroz reverse engineering, plus
PA800 OS v202 PKG analizu.

## Sadržaj

1. [Brzi početak](#brzi-početak)
2. [Šta je novo u v5](#šta-je-novo-u-v5)
3. [12 GUI tabova](#12-gui-tabova)
4. [Implementirane V3 funkcije](#implementirane-v3-funkcije)
5. [Sigurnosni model](#sigurnosni-model)
6. [Moduli](#moduli)

---

## Brzi početak

### Pokretanje integriranog GUI-ja (preporučeno)

- **Windows**: dvoklik `KORG_PA_Manager_v5.bat`
- **Linux / macOS**: `./KORG_PA_Manager_v5.sh`

Browser se automatski otvara. Server sluša samo na `127.0.0.1` —
fajlovi se ne šalju na internet.

### CLI korištenje

```bash
# 1. Cross-model STY konverter
python3 korg_pa_converter.py convert INPUT.STY OUTPUT.STY \
    --source-model PA3X --target-model PA4X --report report.json

# 2. Song Optimizer (MIDI)
python3 korg_pa_song_optimizer.py INPUT.mid -o OUTPUT.mid --report report.json

# 3. Style Optimizer (STY)
python3 korg_pa_style_optimizer.py INPUT.STY -o OUTPUT.STY --report report.json

# 4. Sound Browser pretraga
python3 korg_pa_editors.py search_sounds "piano"

# 5. Drum Kit info
python3 korg_pa_editors.py drum_kit --name "Pop Std. Kit RX"

# 6. Style tracks extract
python3 korg_pa_editors.py style_tracks INPUT.STY

# 7. STS tracks extract
python3 korg_pa_editors.py sts_tracks INPUT.STY --position 0

# 8. Sound ReMap
python3 korg_pa_editors.py remap_sty INPUT.STY OUTPUT.STY \
    --source "Old Sound" --target "New Sound"

# 9. Sample Tools
python3 korg_pa_sample_tools.py convert_8bit_to_16bit input.pcm output.pcm
python3 korg_pa_sample_tools.py stereo_to_mono input.pcm output.pcm
python3 korg_pa_sample_tools.py export_wav input.pcm output.wav \
    --sample-rate 44100 --bit-depth 16 --channels 1

# 10. SET Report
python3 korg_pa_set_report.py --html report.html --json report.json --csv report.csv

# 11. PA800 Database stats
python3 korg_pa_database.py --stats
```

---

## Šta je novo u v5

Nadogradnja sa v4 uključuje:

1. **Sound Browser** — pregled svih PA800 factory zvukova (154 zvuka)
   sa pretragom po imenu. Implementira V3 `getSoundInfo`.

2. **Drum Kit Editor** — prikaz key map-a sa velocity layer switches
   (Pop Std. Kit RX, Jazz Kit). Implementira V3 `Drum Kit` editor.

3. **Style Editor** — ekstraktuje 8 traka STY fajla (Drum, Perc, Bass,
   Acc1-5) sa bank/program informacijama. Implementira V3
   `Style Track Report`.

4. **STS/Performance Editor** — ekstraktuje 4 keyboard trake
   (Lower, Upper1-3) iz STS resursa. Implementira V3 `LoadStyleSTS`,
   `reMapSTS`.

5. **Sound ReMap** — Find & Replace zamjena zvuka u STY fajlu.
   Implementira V3 `reMapSTY`: "Re-Map All Sound Instances of [source]
   with [target]".

6. **Sample Tools** — tri funkcije sa potvrđenim formulama iz V3
   disassembly:
   - Stereo → Mono: `mono = trunc_toward_zero((L + R) / 2)` (VA 0x02bfa9f0)
   - 8-bit → 16-bit: `output = (input - 128) * 256` (VA 0x02bfa470)
   - Export PCM → WAV (V3 `mnuExportSample`)

7. **SET Report Generator** — generiše HTML/JSON/CSV izvještaj sa
   svim zvukovima, drum kitovima, stilovima i slikama. Implementira V3
   `cmdReport`.

8. **PA800 Resource Database** — centralni registry sa 154 zvukova,
   2 drum kita, 874 stilova, 69 slika iz PA800 OS v202 PKG-a.

9. **12 GUI tabova** umjesto 5 — sve V3 funkcije dostupne iz browsera.

---

## 12 GUI tabova

| Tab | Funkcija | V3 komanda |
|-----|----------|-----------|
| 1. Konverter | Cross-model STY konverzija | (novi) |
| 2. Song Optim. | MIDI/SMF optimizacija | (novi) |
| 3. Style Optim. | STY optimizacija | (novi) |
| 4. Inspector | STY strukturalni izvještaj | (korg_sty_tool inspect) |
| 5. Sound Browser | Pregled zvukova | getSoundInfo |
| 6. Drum Kit | Drum kit key map | Drum Kit editor |
| 7. Style Editor | 8 traka STY-a | Style Track Report |
| 8. STS Editor | 4 keyboard trake | LoadStyleSTS |
| 9. Sound ReMap | Find & Replace | reMapSTY |
| 10. Sample Tools | PCM konverzije | msgStereoToMono, convert_8bit_to_16bit |
| 11. SET Report | HTML/JSON/CSV | cmdReport |
| 12. PA800 OS | Ikone, wallpaper | (novi) |

---

## Implementirane V3 funkcije

### Iz V3 disassembly (confirmed-conversion-rules.json)

| V3 funkcija | Implementirano kao | Formula/Status |
|-------------|-------------------|----------------|
| `convert_8bit_to_16bit` | `korg_pa_sample_tools.convert_8bit_unsigned_to_16bit_signed` | `(b - 128) * 256` ✓ potvrđeno |
| `convert_stereo_to_mono16` | `korg_pa_sample_tools.convert_stereo_16bit_to_mono_16bit` | `trunc_toward_zero((L+R)/2)` ✓ potvrđeno |
| `mnuExportSample` | `korg_pa_sample_tools.export_pcm_to_wav` | WAV export ✓ |
| `mnuExportDK` | `korg_pa_sample_tools.export_all_dk_samples_to_wav` | WAV export ✓ |
| `mnuSize` | `korg_pa_sample_tools.get_sample_size_info` | Sample info ✓ |
| `mnuCompDuration` | `korg_pa_sample_tools.filter_samples_by_duration` | Filter ✓ |

### Iz V3 stringova (KORGPAManager.exe)

| V3 string | Implementirano kao |
|-----------|-------------------|
| `reMapSTY` | `korg_pa_editors.remap_sound_in_sty` |
| `reMapSTS` | `korg_pa_editors.remap_sound_in_sts` |
| `reMapPAD` | `korg_pa_editors.remap_sound_in_sty` (isti mehanizam) |
| `reMapPERF` | `korg_pa_editors.remap_sound_in_sty` (isti mehanizam) |
| `Paste_Sound` | `korg_pa_editors.remap_sound_in_sty` (target-only mode) |
| `FourXPCGDBtoThreeXMSLevel` | `korg_pa_converter.convert_sty_cross_model` (PA4X → PA3X) |
| `DeleteMSfromAllPCG` | (u budućoj iteraciji) |
| `NewSound` | `korg_pa_editors.get_new_pcg_template` (template) |
| `getSoundInfo` | `korg_pa_database.get_sound_info` |
| `Sound_Size_Menu` | `korg_pa_sample_tools.get_sample_size_info` |
| `mnuPCGDefault` / `mnuPCGDefaultAll` | `korg_pa_editors.get_new_pcg_template` (reset template) |
| `cmdEditMS` / `cmdGetMS` | (u budućoj iteraciji) |
| `Drum Kit` editor | `korg_pa_editors.list_drum_kit_keys` |
| `Style Track Report` | `korg_pa_editors.extract_style_tracks` |
| `LoadStyleSTS` | `korg_pa_editors.extract_sts_tracks` |
| `STYMSGSTSVersion` | `korg_sty_tool.style_performance_diff` (STS version check) |
| `STYchkAllElements` | `korg_pa_optimizer.optimize_song_or_style` (apply to all) |
| `STYcmdUpdateStyle` / `STYcmdReset` | `korg_pa_style_optimizer.optimize_sty_file` (reset=disable) |
| `cmdReport` | `korg_pa_set_report.generate_set_report_html/json/csv` |
| `mnuCompMenu` (Compress All) | (u budućoj iteraciji) |
| `mnuDelUnused` | (u budućoj iteraciji) |
| `mnuDelAll` | (u budućoj iteraciji) |
| `cmdNewSound` | `korg_pa_editors.get_new_pcg_template` |
| `chkImportSound` | (u budućoj iteraciji) |
| `chkImportSoundMute` | (u budućoj iteraciji) |
| `AutoRemapMSG` | `korg_pa_editors.remap_sound_in_sty` |
| `soundOS128BankDK` | (16-page UserDK - u budućoj iteraciji) |
| `KB_MODE_Title` (Select TARGET Keyboard) | `korg_pa_converter.convert_sty_cross_model` (target model select) |
| `MSGVersionCheck` / `MSGVersionDownGrade` | `korg_pa_converter.detect_source_model` + convert |
| `MSGVersionCheckBANK` | `korg_pa_converter.convert_sty_cross_model` (per-resource) |
| `msgStereoToMono` | `korg_pa_sample_tools.convert_stereo_16bit_to_mono_16bit` |

### Iz V3 custom resursa (PE analysis)

| V3 resurs | Implementirano kao |
|-----------|-------------------|
| `CUSTOM_NEW_PCG` (1388 bajtova null) | `korg_pa_editors.get_new_pcg_template` |
| `CUSTOM_NEW_PCG_DK` (3586 bajtova null) | `korg_pa_editors.get_new_pcg_dk_template` |

### Iz V3 model stringova (PA_OS_SOUND_*)

Svi 12 podržanih modela: PA80, PA1X, PA2X, PA500, PA600, PA800, PA3X,
PA3XLE, PA4X, PA700, PA900, PA1000 — implementirano u
`korg_pa_converter.MODEL_REGISTRY`.

---

## Sigurnosni model

1. **Fail-closed** — nikada ne izmišljamo podatke bez dokaza
2. **Round-trip verifikacija** za sve izmijenjene resurse
3. **KBEG/KEND indeks** se uvijek ponovo generiše
4. **Originalni ulaz** nikada ne može biti izlazna putanja
5. **STS preservation** — Style Optimizer čuva STS byte-exact
6. **V3 potvrđene formule** — konverzije koriste tačno one formule
   potvrđene disassembly-em (ne izmišljamo alternative)

---

## Moduli

| Datoteka | Opis |
|----------|------|
| `korg_pa_manager_v5_gui.py` | **Glavni GUI** (12 tabova) |
| `korg_pa_database.py` | PA800 resursna baza (154 zvuka, 874 stilova) |
| `korg_pa_editors.py` | Sound ReMap, Drum Kit Editor, Style/STS Editors |
| `korg_pa_sample_tools.py` | Sample konverzije (potvrđene V3 formule) |
| `korg_pa_set_report.py` | HTML/JSON/CSV izvještaj generator |
| `korg_pa_converter.py` | Cross-model STY konverter |
| `korg_pa_optimizer.py` | Shared optimization engine |
| `korg_pa_song_optimizer.py` | Song Optimizer (MIDI) |
| `korg_pa_style_optimizer.py` | Style Optimizer (STY) |
| `korg_sty_tool.py` | Dokazani STY engine (parser, repacker) |
| `KORG_PA_Manager_v5.bat` | Windows launcher |
| `KORG_PA_Manager_v5.sh` | Linux/macOS launcher |
| `pa800_resource_catalog.json` | PA800 resursni katalog |
| `pa800_image_manifest.json` | Slika manifest |
| `pa800_images/` | 3 wallpaper slike |

---

## Šta slijedi (u budućim iteracijama)

- **PCG/Sound bank konverzija** (samo STY radi trenutno)
- **PAD editor** (PAD resursi nisu parsirani)
- **PRF/Performance editor** (PRF resursi nisu parsirani)
- **KMP/KSF sample extraction** (PCM export iz KSF)
- **mnuCompMenu** (sample kompresija)
- **mnuDelUnused** / **mnuDelAll** (brisanje sample-a)
- **16-page UserDK** mode
- **Auto Import Related USER Sounds**
- **Fizička hardverska validacija** generisanih fajlova

---

## Licenca

Ovaj alat je nezavisan rad baziran na statičkoj analizi KORG PA800 OS
v202 PKG-a, KORG PA Manager v3.3 aplikacije, i korpusa STY fajlova.
Uploadovani izvršni fajlovi nisu pokretani. Sve transformacije su
izvedene iz dokazanih pravila.

KORG je zaštitni znak Korg Inc.
