# Baja Music · PA800 Manager — native SET forenzika v0.2

**Web aplikacija za pregled stvarnih `.SET` arhiva i Pa800 resursa, bez izmjena instrument-datoteka.** Ovo izdanje je prošireni **read-only** temelj za budući native konverter. **Ne može** sada pretvarati ili generisati `.SET`, `.STY`, `.PCG`, `.BKP`; ne tvrdi 100% kompatibilnost s korisnikovim **Pa800 OS 2.02 od 27. 7. 2011.** Dosadašnja odvojena SMF izdanja nisu zamjena za ovaj native zadatak.

## Šta je stvarno dodano u v0.2

- Uvoz `.SET` foldera zapakovanog u ZIP; inventar **svakog** člana, uključujući `STYLE/*.STY`, `SOUND/*.PCG`, `PCM/*.PCM`, `MULTISMP/*.KMP`, `PERFORM/*.PRF`, PAD, SONGBOOK i GLOBAL. Svaki nešifrirani član dobija ZIP CRC provjeru i SHA-256; izvorni ZIP se može preuzeti **bajt-identično**. Prikaz BKP-a kao arhivskog člana **nije** BKP dekodiranje ili restore.
- Čitač banki provjerava KBEG/KEND offsete, type-05 katalog, OC20 stranice i OC31/OC32 XOR za podržane resurse. Za stvarne sparse Style banke sada koristi **StyleInfo bitmap** za mapiranje prisutnih elemenata umjesto pretpostavke da svih 13 uvijek postoji. Testiran je i v5 izgled od 15 elemenata, bez tvrdnje da je v5 native Pa800 OS 2.02. PAD bitmap dozvoljava više unutrašnjih elemenata; nepoznate semantike ostaju očuvane.
- Read-only graf `Style TrackInfo → mogući USER01/USER02/USERDK.PCG kataloški slot`. To **nije** kompletna Sound/PCM zavisnost: STS/PRF, oscillator → KMP → PCM, model-target remap i BKP packing još nisu obuhvaćeni. Nedostajući slot u ZIP-u nije automatski pokvaren zvuk — moguć je factory/default fallback.
- Neprepoznate, ali ispravno uokvirene PRF varijante ostaju **PRESERVE_ONLY / REVIEW**, a ne izmišljeno dekodirani parametri. Izvještaj jasno odvaja checksum/strukturu od semantike i testa instrumenta.

## Provjera na stvarnim korisnikovim uzorcima

- `Hammy.SET.zip`: **149** datoteka, **12 STY**, **3 PCG**, **99 PCM**, **1 KMP**, **20 PRF**. Pročitana su sva **384 Style** resursa i 3.272 Style elementa; od **1.496** resursa analiziranih banki 3 PRF imaju nepoznatu ili parcijalnu geometriju i ostaju preserve-only. ZIP CRC je prošao. Pa800 OS 2.02 porijeklo seta **nije potvrđeno**.
- `Pa1x BABAJA 2011 bosna.SET.rar`: statički raspakovan za forenzičke testove; **140** datoteka, **3 STY**, **3 PCG**, **99 PCM**, **1 KMP**. Svih **96 Style** i **105 PAD** resursa je strukturno dekodirano, uključujući 9 PAD objekata sa po dva elementa; **139/181 PRF** objekata je preserve-only zbog nepoznate geometrije. App za sada prima ZIP, ne RAR; ovaj test je rađen nakon lokalnog prepakovanja u ZIP bez mijenjanja sadržaja.
- `Pa1x Redbull Novoboki Greek.SET.rar`: 56 članova je **šifrovano**; bez lozinke su dostupni samo nazivi i veličine. Nije tvrđeno da je sadržaj dekodiran.
- Samostalni `USER01.STY`: 40 Style resursa sa v5/15-elemenata i korisničkim sound selektorima; nema pratećeg PCG/KMP/PCM u istoj datoteci. Model porijekla treba potvrditi.

Ove brojke označavaju **strukturno čitanje**. Nijedna izmijenjena native datoteka nije napravljena, učitana niti potvrđena na fizičkom Pa800.

## Pokretanje

Potreban je Python **3.10+**, bez vanjskih paketa:

```bash
cd pa800-manager
python server.py --host 127.0.0.1 --port 8765
# zatim otvori http://127.0.0.1:8765
python -m unittest discover -s tests -v
```

U aplikaciji izaberi **SET / PCM inventar** nakon uvoza ZIP-a. Uvoz ima limit **64 MiB ZIP**, **256 MiB dekomprimovanog inventara**, **16 MiB pojedinačne dekodirane banke**; veći SET/BKP nisu podržani ovim prototipom. RAR prvo raspakuj lokalno i napravi ZIP od `.SET` foldera; **ne uklanjaj/izostavljaj PCM, KMP ili SongBook**.

**Privatnost:** lokalni server čuva podatke samo u memoriji, ali je radni prostor zajednički svim klijentima tog procesa. Za privatne SET/BKP koristi `127.0.0.1` na svom računaru i **ne dijeli live-preview adresu**. Nijedan upload nije automatski poslan na Drive.

## Zašto nema native Save/Restore dugmeta?

Da bi se konverzija cijelog seta proglasila funkcionalnom, treba dokazati verzijski `.STY/.PRF/.PCG/.KMP/.PCM` writer, potpun dependency graph i BKP outer packing/restore ugovor. Svaki izlaz mora sačuvati originalni backup i nepoznate bajtove, proći **Save → Reopen → structural/semantic diff**, te tek zatim na fizičkom Pa800 **Load → Select → Play → Save → Reload**. BKP restore može prebrisati instrument; prototip ga ne pokreće.

Za tačnu listu uzoraka vidi `HARDVER_TEST.md` i `research/native_set_audit/REPORT.md`. Dokazni PDF ima snažne softverske tvrdnje o STY writeru, ali ovaj kod **još nema potvrđen finalni OC20 writer ni dokaz hardware acceptance**. Zato je native upis namjerno zaključan.

## Struktura

- `server.py` — stdlib lokalni web server/API; ZIP streaming CRC/SHA manifest i granice resursa.
- `core/codec.py`, `core/format.py` — konzervativni OC20/OC31/OC32 i resource reader; sparse Style/PAD bitmap.
- `core/set_graph.py` — kandidatske veze Style selektora i USER PCG kataloga (bez pisanja).
- `core/sounds.py`, `data/` — 1.003 potvrđene Factory Sound adrese za lookup.
- `public/` — web UI bez CDN-a; `fixtures/` i `tests/` — dokazni podskup i regresije.
