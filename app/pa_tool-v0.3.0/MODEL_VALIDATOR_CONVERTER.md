# Model Converter + validator.py — ugovor verzije 0.3.0

Datum: 2026-10-06

Ova verzija dodaje traženi validator za svaki dokazani model i svaki poznati tip fajla, te izvršni konverzioni sloj koji piše samo ono za šta postoji provjerljiv ugovor.

## Pokrivenost

`pa_tool/validator.py` trenutno registruje:

- **18 modelskih profila** iz `data/models.json`;
- **32 pravila fajlova/foldera**;
- **10.368 ćelija kompatibilnosti** (`18 × 18 × 32`);
- rekurzivnu provjeru svakog regularnog fajla u `.SET`, `.KST` ili običnom folderu.

### Modeli

Kanonski modeli:

- PA80
- PA1X
- PA2X
- Pa3X
- PA500
- Pa600
- Pa900
- Pa4X
- PA1000
- Pa5X

Dokazani dodatni modeli/varijante:

- PA800
- PA300
- PA700
- PA588
- PA3X Le
- PA1X Pro
- PA1X Pro Elite
- PA4X OS Next

Svaki profil ima RAM podatke, OpenSound SUB/format grupu gdje je dokazana, status, dokaz, bazni model varijante i pravilo za SOUND import.

### Fajlovi i folderi

Pokriveni su svi nastavci iz dokaznog dijalog-filter inventara:

`.STY .STG .PCG .PAD .PDC .PRF .KSC .KMP .KSF .PCM .MID .MIDI .WAV .AIF .AIFF .RX2 .REX .SF2 .MTP .MXP .MPR .QTP .GBL .SBD .SBL .CSC .LNG .BKP .PKG`, plus folderi `.SET` i `.KST` i transportni `.ZIP`.

Poznat nastavak nije automatski PASS. Ako kompletna šema nije dokazana, validator vraća `UNKNOWN` i čuva fajl kao opaque/preserve-only.

## Nove komande

### Pregled svih validatora

```powershell
python run_pa_tool.py validators --models --files
python run_pa_tool.py validators --json
```

### Jedan fajl ili cijeli folder

```powershell
python run_pa_tool.py validate-all Moj.SET --model PA800 --out pa800_validation.json
python run_pa_tool.py validate-all User.KST --model Pa5X --json
python run_pa_tool.py validate-all USER01.PCG --model Pa4X
```

Za MIDI postoje tri odvojena ugovora:

```powershell
python run_pa_tool.py validate-all style.mid --model PA800 --midi-workflow generic
python run_pa_tool.py validate-all style.mid --model PA800 --midi-workflow manager_import
python run_pa_tool.py validate-all style.mid --model PA800 --midi-workflow pa_style_import
```

Razdvajanje je namjerno: nizovi oporavljeni iz PA Managera traže format 1 / jednu traku / 384 PPQN, dok službeni Korg Style Import dokument za Pa800 opisuje format 0 sa jednom trakom. Validator ne spaja ta dva različita workflowa u lažno univerzalno pravilo.

### Matrica između modela

```powershell
python run_pa_tool.py model-matrix STY
python run_pa_tool.py model-matrix PCG --source-model Pa4X --target-model PA800
python run_pa_tool.py model-matrix PCG --source-model Pa4X --target-model Pa5X
```

Mogući nivoi ćelije:

| nivo | značenje |
|---|---|
| `EXACT` | isti profil modela; validirana kopija je izvršiva |
| `PRESERVE_ONLY` | bajtovi se mogu sigurno prenijeti, ali nisu proglašeni konvertovanim |
| `PARTIAL` | postoji djelimičan dokaz ili siguran podskup, ali ne kompletan instrument format |
| `REFUSED` | postoji dokaz da put nije podržan |
| `UNKNOWN` | nema kompletnog writer/remap dokaza; alat ne pogađa |

## Izvršna konverzija

Bez `--execute` komanda pravi samo plan:

```powershell
python run_pa_tool.py model-convert Moj.SET Moj_PA800.SET ^
  --source-model Pa4X --target-model PA800
```

Za stvarni novi izlaz:

```powershell
python run_pa_tool.py model-convert Moj.SET Moj_PA800.SET ^
  --source-model Pa4X --target-model PA800 --execute
```

Nepoznati native resursi se po defaultu prenose bajt-identično i u izvještaju dobijaju `PRESERVE_ONLY`. To nije tvrdnja da će ih ciljni instrument prihvatiti. Ako želiš strogo odbijanje umjesto očuvanja:

```powershell
python run_pa_tool.py model-convert Moj.SET Moj_PA800.SET ^
  --source-model Pa4X --target-model PA800 --execute --no-preserve
```

## Stvarno podržani upisi

### 1. Validirana kopija

- izvor se nikad ne prepisuje;
- postojeći cilj se ne briše niti zamjenjuje;
- SHA-256 kopije mora biti isti;
- folder se gradi u sibling staging direktoriju;
- izlaz se instalira tek poslije validacije;
- hash cijelog izvora prije i poslije mora biti isti.

### 2. WAVE u kanonski format

```powershell
python run_pa_tool.py model-convert sample.wav sample_pa.wav ^
  --source-model Pa4X --target-model PA800 --canonicalize-wave --execute
```

Za nekompresovani PCM WAVE radi stvarnu konverziju na:

- 44.100 Hz
- 16 bit
- mono

To je audio-format konverzija, ne mapiranje Korg Sound/OSC parametara.

### 3. Fiksni vanjski KORF katalog

Prvo se napravi eksplicitni mapping template:

```powershell
python run_pa_tool.py model-convert input.sty output.sty ^
  --source-model Pa4X --target-model PA800 ^
  --mapping-template pa4x_to_pa800_map.json
```

Nakon ručne provjere mape:

```powershell
python run_pa_tool.py model-convert input.sty output.sty ^
  --source-model Pa4X --target-model PA800 ^
  --mapping pa4x_to_pa800_map.json --execute
```

Schema:

```json
{
  "schema": "pa_tool.explicit-model-map.v1",
  "source_model": "Pa4X",
  "target_model": "PA800",
  "catalog": {
    "rename_map": {
      "Old Name": "New Name"
    },
    "prefix": "",
    "suffix": "",
    "style_bank": 5,
    "object_key_map": {
      "4:1:0": "4:2:0"
    }
  }
}
```

Upis je ograničen na dokazane fiksne pozicije u vanjskom katalogu. Guard potvrđuje:

- ista dužina fajla;
- promijenjeni su samo dozvoljeni offseti;
- STY primarni i duplirani zapis ostaju usklađeni;
- izlaz se ponovo parsira;
- read-back bajtovi moraju odgovarati planiranom rezultatu.

**Važno:** ovo ne skenira nasumične četiri bajta kroz kompresovani payload i ne tvrdi da je zatvoren puni STS/PCG/OSC/KMP/PCM graf.

## Šta se namjerno odbija

- PA5X SOUND import (`0x002DD968`);
- `.KST` nazad u stariji `.SET` kada put nije dokazan;
- in-place konverzija;
- postojeći cilj;
- symlinkovi u stablu;
- nepoznat model;
- nevažeći mapping schema/model pair;
- neprovjeren native resurs kada je uključen `--no-preserve`;
- automatska Pa4X → PA800 zamjena Soundova bez eksplicitne i dokazane mape.

## Zašto nema lažnog univerzalnog Pa4X → PA800 konvertera

PA4X i PA800 nemaju dokazano jednaku Sound/PCM adresaciju. Za kompletan konverter i dalje nedostaju:

- puni STS/PRF → PCG → oscillator → KMP → PCM dependency graf;
- dokazano mapiranje svakog factory/user zvuka;
- verzijski writeri za svaki native format;
- OC20 recompression writer sa hardware ugovorom;
- Pa800 OS2.02 Load → Play → Save → Reload test.

Zato alat sada ima kompletnu matricu i stvarne, guardovane podoperacije, ali sve izvan dokazanog ugovora označava `UNKNOWN`, `PARTIAL` ili `PRESERVE_ONLY`.
