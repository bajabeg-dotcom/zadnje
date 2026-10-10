# Analiza projekta i redoslijed rada

Datum: 2026-10-06 · paket: `pa_tool 0.2.0`

## Početno stanje

Arhiva `pa_tool_improved.zip` je preuzeta sa Google Drivea i raspakovana uz provjeru
putanja. SHA-256 originala:

`0cf5eb6b3da7a60f74e72acfc9592d392d66ab9fda389e4add556f9c2f49ed45`

Prije izmjena:

- 89 testova prolazi, 20 skip bez eksternih uzoraka/asseta;
- `doctor` prolazi;
- projekat je read-only prema ulazima;
- glavne otvorene stavke su A–M iz `STATUS.md` i izvršenje faza F3–F6.

## Kako je projekat shvaćen

1. `data/*.json` je izvor istine i svaki nalaz nosi status/dokaz.
2. `container.py`, `detect.py`, `setfolder.py` i `validate.py` su read-only jezgro.
3. `manager.py` koristi već izvučene dokaze iz PA Manager binarca.
4. `convert/` trenutno pouzdano planira i odbija, ali stvarni STY izvršni sloj zavisi od
   eksternih `setconvert` i `pa800_native` paketa.
5. Sljedeće stavke treba zatvarati samo kad postoje uzorci ili reproduktivan statički dokaz.

## Ovaj prolaz — završeno

Odabrane su stavke **D** i **F** jer su se mogle riješiti postojećim Drive dokazima
bez nagađanja:

- D: PA588 / PA3X Le suženi na njihove stvarne Manager SOUND handler klase (`HIGH`).
- F: `PBK1` / `PRG1` / `RLP1` razdvojeni po formatima; dodan PCG dijalekt klasifikator.

Rezultat:

- 96 testova prolazi;
- 14 skip bez velikih eksternih asseta;
- dodan mali stvarni PCG fixture sa hash dokazom;
- originalni ulazi nisu izmijenjeni.

## Integracijski prolaz v0.2.0 — završeno

Obavezni izvor `PA800_Manager_Web_v0.2_NATIVE_SET_FORENSICS.zip` pregledan je
feature-by-feature, a zatim je sva korisna i ne-inferiorna funkcionalnost unesena u
`pa_tool`:

- bounded CBarc/OC20/OC31/OC32 reader i self-contained generic OC decode;
- strogi native bank parser, sparse Style/PAD, CV/TrackInfo i PRF preserve-only granice;
- 1.003 PA800 Factory Sound adrese;
- Style→USER PCG kandidatski graf;
- bounded cijeli-ZIP inventar s CRC/SHA, safety guardovima i original-byte exportom;
- deep audit/report, kompletan browser UI/API i CLI pristup;
- research helperi, statički dokazi, svih 6 fixturea i svih 12 source testova.

Izvor nema native writer. Zbog toga nisu dodani izmišljeni edit/Save/Convert/BKP
Restore postupci. Potpuna matrica overlap odluka je u
`INTEGRACIJA_PA800_MANAGER_WEB_V02.md`.

## Predloženi naredni redoslijed

1. **Potpuni native dependency graph.** Dokazati STS/PRF selektore, PCG oscillator
   reference, KMP/PCM ID-ove i factory fallback; sadašnji graf je namjerno candidate-only.
2. **Versioned writer dokaz.** Potreban Pa800 OS2.02 `BEFORE.SET` / `AFTER_ONE_EDIT.SET`
   par s tačno jednom poznatom promjenom; zatim parse→edit→serialize→reopen→semantic diff.
3. **BKP ugovor.** Potreban stvarni, originalni compressed i uncompressed BKP sa istog
   instrumenta/stanja; bez toga nema pack/restore tvrdnje.
4. **Hardware acceptance.** Jedna testna banka, pa `Load → Select → Play → Save → Reload`;
   full BKP Restore tek kao posljednji zaseban destruktivni test sa recovery planom.
5. **A/M/C/E.** OSC limit po modelu, InitPx01 tie-break i verzijske/enum tabele ostaju
   odvojene evidence stavke; rješavati samo s označenim uzorcima ili statičkim dispatch dokazom.
6. **G/H — MERGE i PECompact2 algoritam.** Raditi tek nakon zatvaranja pojedinačnih
   formata; ostaju eksperimentalni dok tok nije reproduktivan.

Svaki naredni prolaz treba završiti novim testom, dokaznim dokumentom i novom verzijom
ZIP-a, bez prepisivanja prethodne verzije na Driveu.
