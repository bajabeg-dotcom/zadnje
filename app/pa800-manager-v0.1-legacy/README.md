# Baja Music · PA800 Manager (web, v0.1)

**Radna web aplikacija za sigurnu inspekciju Korg Pa800 resursa.** Izvorni fajlovi se ne mijenjaju. Projekt je izgrađen iz početka prema korisnikovom [master dosjeu](https://drive.google.com/file/d/1LsnzGuCCHoJamjqj9rzuXcVhexGIJH7b/view?usp=drivesdk), [tehničkom dosjeu](https://drive.google.com/file/d/1FFgMaDrz473NP6GUw0a1kBcZtqD9e7eq/view?usp=drivesdk) i [paketu dokaza 22. 9. 2026.](https://drive.google.com/file/d/1DqGLGL6f454HpJ501oivwWw6JtOHJ5b2/view?usp=drivesdk).

> **Nije „sve završeno”:** ovo je testirana *read-only* osnova, a ne native editor siguran za učitavanje u instrument. Aplikacija neće izvesti nov STY/PRF/BKP niti simulirati provjeru na hardveru. Sljedeći rad: dokazati OC20 izlazni okvir, implementirati writer uz ponovno otvaranje/diff/backup, zatim stvarno testirati na korisnikovom Pa800.

## Šta sada zaista radi

- Učita `.STY`, `.PRF`, `.PAD`, `.PCG`, `.GBL`, `.TBL` ili ZIP pripremljen od `.SET` / `ALL` foldera; samo podržane datoteke unutar ZIP-a se analiziraju.
- Čita jednu ili više OC20 stranica uz provjeru potvrđenog 8-bitnog zbira svake stranice, Korg root, `KBEG/KEND` indeks, 24-bajtni katalog, te OC31/OC32 sadržaj uz provjeru XOR-a. Ostalih pet završnih OC20 bajtova još nema potvrđenu semantiku.
- Za Style i PAD provjerava PatternInfo, asocijacije, 8×6 TrackInfo i geometriju Chord Variation; pokazuje svih 13 Style elemenata i sound selektore po tragu. Neuobičajene, ali strukturno valjane reference ostaju upozorenje, ne lažno odbacivanje.
- Za PRF razlikuje Release-2, Release-3 i Drawbar varijantu, uz strogo očuvanje neimenovanih listova. PCG/SETUP/PRGMAPS imaju siguran pregled resursa i njihovih provenijencija.
- Ima pretragu **1.003** Factory Sound adresa (`CC00.CC32.PC`). Nepoznata ili korisnička adresa ostaje nepoznata, bez nagađanja.
- Izvozi **izvještaj u JSON-u** i preuzima originalne, neizmijenjene bajtove učitanih fajlova. Validacija provjerava sve trenutno učitane resurse.
- Podaci iz vlastitog uvoza ostaju u memoriji procesa, nisu poslani na Google Drive. **Ne dijelite link live previewa** ako tu učitate privatne SET-ove; za lokalni privatni rad pokrenite server na svom računaru.

## Testirani korpus

Na arhivi `ALL_FACTORY_EXTRACTED_SOURCE.zip` iz korisnikovog Drivea: **64 podržane banke, 435 Style, 411 PAD i 264 Performance resursa**; dubinski pregled je obuhvatio **2.645 resursa, 0 strukturnih grešaka**. Dio neobičnih pattern/track kombinacija ostaje označen upozorenjem i nije mijenjan. Tvornički zvukovi su izdvojeni iz zasebnog dokaznog popisa sa 1.003 adrese.

Uključen demonstracijski podskup iz iste arhive: `BANK01.STY`, `BANK01.PRF`, `Bank01.pad`, `ROM.PCG`, `PRGMAPS.TBL` i `SETUP.GBL`. To je **demonstracija**: nije korisnikov testni SET i ne dokazuje ispravan native upis na instrument.

## Pokretanje

Potreban je Python **3.10+**; nema instalacije vanjskih paketa.

```bash
cd pa800-manager
python server.py --host 127.0.0.1 --port 8765
```

Otvori **http://127.0.0.1:8765** u pregledniku. Za testove:

```bash
python -m unittest discover -s tests -v
```

Ako imaš raspakovano Factory `ALL` stablo, opcionalno:

```bash
PA800_FACTORY_CORPUS=/putanja/do/ALL python -m unittest discover -s tests -v
```

Na Windowsu ovu varijablu možeš postaviti kroz `set PA800_FACTORY_CORPUS=C:\putanja\do\ALL` pa pokrenuti `python -m unittest discover -s tests -v`.

## Granice i sljedeća faza

| Područje | Ovaj build | Da bi se proglasilo završenim |
|---|---|---|
| STY / PAD | Čitač i strukturna provjera | Native pisanje, algoritam OC20 footera, re-open i validirani edit testovi |
| Performance / PRF | Verzijski pregled | Sigurni dokazani setteri, backup/undo i provjera na instrumentu |
| Sound / PCG / PRGMAPS | Browser + Factory adrese | Dependency graph za konkretni korisnički SET i bezbjedno remapiranje |
| SET / BKP | ZIP/ALL inspekcija | Potvrđen BKP pack/restore ugovor; **ne izmišljati** ga |
| MIDI / Style import | Nije ugrađen | Provjeren Style↔SMF most i clone-on-write, ne generički MIDI heuristički import |
| Fizički Pa800 | Nije testiran | Load → Select → Play → Save → Reload na odvojenom testnom SET-u |

Za sljedeći korak sa tvojim instrumentom vidi [HARDVER_TEST.md](HARDVER_TEST.md). **Ne radi restore neslužbeno generisanog BKP-a ili prototipnog STY-a na jedinoj kopiji svojih podataka.**

## Struktura

- `server.py` — stdlib HTTP server i API; limit 64 MiB.
- `core/codec.py` — bounded OC20/OC31/OC32 dekoder; testni inner OC31 literal enkoder.
- `core/format.py` — strukturni PA800 čitač i validator.
- `core/sounds.py`, `data/` — potvrđeni Factory selektori.
- `public/` — responzivni web interfejs bez vanjskih skripti/CDN-a.
- `fixtures/`, `tests/` — podskup korisnikove arhive i regresijski testovi.

### Zašto trenutno nema native save dugmeta?

Dokazni Python skript reproducira inner OC31 writer, ali u ovom izdanju **nije** dokazano kako bezbjedno obnoviti finalni OC20 footer i cijeli instrument-accepted izlaz. Izmjena koja prođe lokalni parser nije ista stvar kao potvrđen Load/Play/Save na Pa800. Umjesto rizičnog izvoza, build poštuje eksplicitni read-only režim.
