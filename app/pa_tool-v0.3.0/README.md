# pa_tool — sopstveni PA Manager za KORG PA formate

`pa_tool 0.3.0` je evidence-first radni alat za prepoznavanje, provjeru, forenzičko
čitanje i sigurno izvršavanje dokazanih podskupova konverzija KORG PA resursa. Ne
pogađa nepoznata polja: svaki nalaz ima status, a nedokazane tvrdnje ostaju
`NEPOTVRDENO`, `UNKNOWN` ili `PRESERVE_ONLY`.

Verzija 0.3.0 dodaje `pa_tool/validator.py`: 18 modelskih profila, 32 pravila
fajlova/foldera i 10.368 source-model/target-model/file ćelija. `validate-all`
provjerava svaki fajl u stablu; `model-convert` piše samo validiranu kopiju,
kanonski PCM WAVE ili eksplicitno mapirana fiksna polja vanjskog KORF kataloga.
Detaljan ugovor je u `MODEL_VALIDATOR_CONVERTER.md`.

Ovo izdanje integrira kompletnu korisnu logiku i web workflowe iz
`PA800_Manager_Web_v0.2_NATIVE_SET_FORENSICS.zip`. Taj izvor je **read-only**.
Zato alat sada duboko čita podržane PA800 banke i cijele SET ZIP arhive, ali **ne**
tvrdi da može sigurno generisati native STY/PCG/SET/BKP za instrument.

## Pokretanje

Bundlovani Python može raditi u isolated modu, pa koristi launcher:

```powershell
cd pa_tool

python run_pa_tool.py doctor
python run_pa_tool.py status
python run_pa_tool.py --version
```

### Novi PA800 native forenzički workflow

```powershell
# Strogi OC20/type-05/FE/KBEG-KEND parser jedne banke
python run_pa_tool.py native-bank BANK01.STY --list
python run_pa_tool.py native-bank BANK01.PRF --resource 1 --json

# Siguran inventar cijelog .SET foldera zapakovanog u ZIP
python run_pa_tool.py native-set My.SET.zip
python run_pa_tool.py native-set My.SET.zip --deep-audit --graph
python run_pa_tool.py native-set My.SET.zip --deep-audit --graph --manifest --out out\report.json

# Tačan PA800 katalog od 1.003 CC00.CC32.PC selektora
python run_pa_tool.py native-sounds --find "Acoustic Piano"
python run_pa_tool.py native-sounds --find "121.0.0"

# Integrirani browser UI: Overview, SET/PCM, Style, Performance, PAD,
# Sound, Global/mape i Validation
python run_pa_tool.py web --host 127.0.0.1 --port 8765
```

Za Arena live preview koristi `--host 0.0.0.0`. Web workspace je u memoriji i
zajednički je svim klijentima jednog procesa; privatne SET/BKP podatke analiziraj
lokalno na `127.0.0.1`.

### Ostale komande

```powershell
python run_pa_tool.py models
python run_pa_tool.py conversions
python run_pa_tool.py validators --models --files
python run_pa_tool.py validate-all <fajl-ili-folder> --model PA800 --out validation.json
python run_pa_tool.py model-matrix PCG --source-model Pa4X --target-model PA800
python run_pa_tool.py model-convert <src> <novi-cilj> --source-model Pa4X --target-model PA800
python run_pa_tool.py model-convert <src> <novi-cilj> --source-model Pa4X --target-model PA800 --execute
python run_pa_tool.py detect <putanja>
python run_pa_tool.py pcg <fajl.PCG>
python run_pa_tool.py validate <putanja> --model Pa5X --required-ram-mb 2000
python run_pa_tool.py inventory <SET folder>
python run_pa_tool.py metadir <folder ili metadir.json>
python run_pa_tool.py manager --names
python run_pa_tool.py manager --find korg
python run_pa_tool.py convert --matrix
python run_pa_tool.py convert <src> --direction set_to_kst --dry-run
python run_pa_tool.py convert --assets
python run_pa_tool.py convert --fingerprint <f.sty>
python run_pa_tool.py convert --set-family <SET>
python run_pa_tool.py oc <kontejner>

python run_tests.py
python prove_defects.py
python proof_sty_conversion.py
python verify_readonly.py
```

## Status disciplina

| status | značenje |
|---|---|
| `VERIFIED` | izmjereno i reproducibilno: struktura, offset, dužina, checksum ili hash |
| `HIGH` | dokaz postoji, ali isključivost nije zatvorena |
| `NEPOTVRDENO` | parcijalni dokaz postoji, ali ne dokazuje širu tvrdnju |
| `UNKNOWN` | nema dovoljnog uzorka/podatka; alat ne izmišlja vrijednost |

`PASS` u native auditu znači da je podržana **softverska struktura** prošla. Ne
znači da je datoteka učitana i prihvaćena na fizičkom Pa800.

## Šta 0.3.0 sada provjerava

- bounded CBarc, OC31/OC32 deklarisana veličina i XOR;
- OC20 višestrane stranice, korpusni checksum i prepoznate završne okvire;
- root node, type-05 katalog i apsolutni FE/KBEG/KEND indeks;
- sparse Style bitmape za poznate 13- i 15-elementne porodice;
- višeelementni PAD bitmap bez izmišljanja nepoznate muzičke semantike;
- PatternInfo brojače, CV framing/EOT/tick i reference;
- osam TrackInfo selektora po elementu;
- poznatu PRF Release-2/Release-3/Drawbar geometriju;
- nepoznate PRF varijante kao `PRESERVE_ONLY`;
- 1.003 tačne PA800 Factory Sound adrese;
- kandidatski `Style TrackInfo → USER01/USER02/USERDK.PCG` graf;
- cijeli ZIP manifest: sigurne putanje, symlink/duplicate/bomb limiti, CRC i SHA-256;
- inventar i očuvanje neparsiranih PCM/KMP/SongBook/BKP članova;
- byte-identičan originalni ZIP/file za ponovno preuzimanje;
- deep audit, JSON report i web pregled svih izvornih v0.2 workflowa;
- 18 modelskih validator profila i 32 file/folder pravila;
- rekurzivnu provjeru svakog fajla u `.SET`/`.KST` stablu;
- kompletnu model-pair/file kompatibilnost sa `EXACT/PARTIAL/PRESERVE_ONLY/REFUSED/UNKNOWN` statusom;
- siguran izvršni put za byte-identičnu kopiju, PCM WAVE 44.1/16/mono i eksplicitno mapirana fiksna KORF katalog polja.

## Važne granice

Potpuni native upis je i dalje namjerno zaključan. Verzija 0.3.0 smije pisati samo
fiksna polja dokazanog vanjskog KORF kataloga uz byte-difference guard; to nije
potpuni native writer. Još nisu dokazani:

- potpuni STS/PRF → PCG → oscillator → KMP → PCM dependency graph;
- automatski cross-model Sound/PCM remap bez eksplicitne mape;
- finalni OC20 writer i verzijski STY/PRF/PCG/KMP/PCM writeri;
- BKP vanjski packing, integritet i restore redoslijed;
- fizički Pa800 OS2.02 acceptance test.

`encode_oc31_literal()` je samo softverski round-trip helper za unutrašnji objekt.
Nije instrument-safe STY/OC20 writer.

## Sigurnosne granice ZIP importa

- upload: 64 MiB;
- najviše 2.000 članova;
- najviše 256 MiB ukupno raspakovanih podataka;
- pojedinačni manifest član: 128 MiB;
- pojedinačna parsirana banka: 16 MiB;
- zbir parsiranih banki: 64 MiB;
- odbijaju se traversal/absolute/drive/NUL putanje, symlinkovi, case-insensitive
duplikati i sumnjiv omjer kompresije.

RAR nije web upload format. Offline `pa_tool.native.research` zadržava bounded RAR
inventory preko lokalnih `7z`/`bsdtar` alata; za UI RAR prvo legitimno raspakuj i
prepakuj u ZIP bez izostavljanja PCM/KMP/SongBook članova.

## Struktura

```text
pa_tool/
├─ run_pa_tool.py
├─ pa_tool/
│  ├─ container.py             # generic KORG container + bundled codec adapter
│  ├─ pcg.py                   # PA KORF vs workstation KORG/PCG1
│  ├─ detect.py, setfolder.py, validate.py, report.py
│  ├─ validator.py             # svi modeli/fajlovi + rekurzija + kompletna matrica
│  ├─ model_convert.py         # sigurni izvršni conversion/preserve podskup
│  ├─ manager.py               # nalazi iz ranije analiziranog Pa Manager binarca
│  ├─ convert/                 # starija matrica planiranja i opcionalni eksterni asseti
│  ├─ native/
│  │  ├─ codec.py              # bounded OC20/OC31/OC32
│  │  ├─ format.py             # strogi native bank/Style/PAD/PRF parser
│  │  ├─ set_graph.py          # candidate-only selector graf
│  │  ├─ sounds.py             # 1.003 PA800 selector bindinga
│  │  ├─ workspace.py          # bounded ZIP/original/audit/report
│  │  ├─ research.py           # reproducibilni offline corpus helpers
│  │  └─ web.py                # stdlib HTTP API
│  └─ gui/web/                 # kompletan integrirani v0.2 browser UI
├─ data/
│  ├─ PA800_FACTORY_SOUND_LIST_CC00_CC32_PC.txt
│  └─ pa800_native_fixtures/   # 6 byte-identičnih dokaznih fixturea
├─ docs/pa800_native_evidence/ # izvorni report, manifest i statički JSON dokazi
├─ tests/
└─ INTEGRACIJA_PA800_MANAGER_WEB_V02.md
```

## Pravila

1. Ulazne instrument datoteke su read-only.
2. Izvještaj se piše samo na eksplicitno zadanu novu putanju; izvor se ne prepisuje.
3. Ciljni binarni fajlovi se ne izvršavaju.
4. Runtime koristi samo Python standardnu biblioteku.
5. Bez eksternih asseta alat i dalje radi; nedostupne funkcije prijavljuju `UNKNOWN`.
6. Nejasan model, semantika, writer ili hardware acceptance nikad se ne proglašava
dokazanim na osnovu naziva, UI etikete ili same uspješne dekompresije.

Detaljna odluka za svaki izvorni modul, workflow i overlap nalazi se u
`INTEGRACIJA_PA800_MANAGER_WEB_V02.md`; stanje otvorenih nalaza je u `STATUS.md`.
