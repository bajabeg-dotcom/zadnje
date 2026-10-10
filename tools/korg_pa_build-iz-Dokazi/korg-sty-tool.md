# KORG PA800 STY Tool

`korg_sty_tool.py` je multi-model read-only parser/validator, PA800 native STY repacker i semantički diff alat.

## Phase 1 Status

Phase 1 je zatvorena. Završna regresija potvrđuje svih 18 PA800 factory banaka,
870/870 semantički očuvanih resursa i nula promijenjenih pozicija nakon repacka.
Sedam multi-model uzoraka ostaje tolerantni inspect korpus: 395 od 424 resursa
se dekodira, dok se 29 poznatih oštećenih ili nepodržanih resursa prijavljuje
bez prekidanja ostatka analize. Međumodelski writer ostaje fail-closed na
nedokazanim native selector, v2 sinteznim i modelskim remap pravilima.

## GUI

Na Windowsu pokreni `KORG_STY_GUI.bat`. Otvara se lokalni browser GUI koji radi
na `127.0.0.1`; STY fajlovi se ne šalju na internet. GUI podržava inspect,
potvrđeni PA800 native repack i eksperimentalne fail-closed STYLE v4/v5
operacije. Detaljna uputstva su u `korg-sty-gui.md`.

## Inspect

```bash
python3 prism-uploads/analysis/korg_sty_tool.py inspect INPUT.STY --json report.json
```

Provjerava:

- direktni native root ili višestruke factory `OC20` stranice;
- root dužinu i sve child granice;
- `FE/KBEG/KEND` apsolutni indeks;
- `OC31/OC32` CBarc tok, izlaznu veličinu i XOR checksum;
- osnovnu inner STYLE strukturu, naziv i ElementInfo/TrackData geometriju.

Multi-model inspect koristi `KBEG/KEND` indeks kao autoritativnu mapu resursa. Neispravan pojedinačni resurs se prijavljuje kroz `decode_error`, ali ne prekida analizu ostatka banke.

Za `ID-09` Style Performance/STS resurse inspect dodatno daje:

- PA1/PA800 legacy mixer i metadata childove;
- mixer slotove od `261` ili `357` bajtova i imenovane slotove od `167` bajtova;
- povezivanje slotova preko ID-a, uključujući dozvoljene orphan mixer slotove;
- četiri desetobajtna Lower/Upper sound zapisa sa selectorom, mute, volume, pan i FX C/D sendovima;
- rekurzivnu record inventuru novijih Pa3X/Pa4X/Pa700 struktura.

## Repack

```bash
python3 prism-uploads/analysis/korg_sty_tool.py repack INPUT.STY OUTPUT.STY --report repack.json
```

Dekodira svaki `OC31/OC32` resurs, ponovo ga kodira sigurnim literal-only CBarc encoderom, preračunava child dužine i rekonstruiše `FE/KBEG/KEND` offsete.

Izlaz je uvijek direktni native STY. Factory `OC20` footer generator nije izmišljen niti se koristi. Dekodirani resursi se prije upisa porede SHA-256 hashom i moraju ostati identični.

## Semantic Diff

```bash
python3 prism-uploads/analysis/korg_sty_tool.py diff BEFORE.STY AFTER.STY --json diff.json
```

Poredi resurse po položaju i prijavljuje promjene inner verzije, veličine, roota, STYLE naziva, geometrije i SHA-256 dekodiranog sadržaja.

## STS Diff

```bash
python3 prism-uploads/analysis/korg_sty_tool.py sts-diff BEFORE.STY AFTER.STY \
  --pair-by style-name --json sts-diff.json
```

Detaljno poredi `ID-09` Style Performance podatke po ordinalu resursa, fizičkom
STS slotu i keyboard track indeksu. Prijavljuje:

- promjenu naziva slota;
- `volume`, `pan`, `FX C` i `FX D` promjene;
- legacy native, USER ili nested raw selector stanje;
- promjene lossless hashova `ID-07`, `ID-09` detail i `ID-15` companion zapisa.

Komanda je namijenjena kontrolisanom istom STS-u prije i poslije konverzije.
`--pair-by ordinal` koristi redoslijed `ID-09` resursa. `--pair-by style-name`
veže svaki `ID-09` za `ID-06` istog ordinala i uparuje normalizovani STYLE naziv
uz occurrence indeks za duplikate. Prazni ili nečitljivi nazivi ostaju eksplicitno
neupareni, bez ordinalnog nagađanja.

Kada se promijeni nested companion zapis, diff pored SHA-256 vrijednosti daje
tačan broj promijenjenih bajtova i listu `offset/left/right` za `ID-07`, `ID-09`
detail i `ID-15`. Time se one-factor hardverski eksperiment može direktno
pretvoriti u kandidatnu mapu polja bez ručnog hexdump poređenja.

## Controlled Evidence Audit

```bash
python3 prism-uploads/analysis/korg_sty_tool.py evidence-audit \
  BASELINE.STY BASELINE_REPEAT.STY ONE_CHANGE.STY \
  --pair-by style-name --json evidence.json
```

Komanda prvo zahtijeva da dva nezavisna baseline savea imaju nula semantičkih
STS promjena. Zatim zahtijeva da kontrolisani fajl mijenja tačno jednu keyboard
traku. Prihvaćen izvještaj daje STYLE/STS naziv, slot, track, semantičke mixer ili
selector promjene i tačne companion `offset/left/right` bajtove. Time se odbijaju
nestabilni saveovi i eksperimenti u kojima je slučajno promijenjeno više faktora.

## Neutral STYLE Export

```bash
python3 prism-uploads/analysis/korg_sty_tool.py neutral INPUT.STY --json neutral.json
```

Izvozi svaki dekodirani `ID-06` STYLE u zajednički JSON oblik. ElementInfo i TrackData ostaju lossless kroz `raw_hex` i SHA-256.

Neutralni izlaz sada strogo razrješava i pattern pool:

- BE16 broj objekata i globalnih referenci;
- uzastopni reference odsječak svakog CV-a;
- lokalni CV ID, 1-based pattern object indeks i tag `02/03/04/05`;
- dvobajtni CV kontrolni par: pattern class i direktni TrackData indeks `0..7`;
- deklarisanu stream dužinu i sačuvani suffix svakog patterna;
- sirovi pattern suffix, bez izmišljanja njegove semantike.

Tag `02` pripada Drum/Perc porodici, `03` Bassu, `04` normalnom Acc patternu,
a `05` Guitar Mode Acc patternu. CV class je egzaktno `0/1/2/3` za te tagove,
dok drugi kontrolni bajt direktno bira `Drum, Perc, Bass, Acc1..Acc5`.

Nested STS trake dodatno se uparuju sa četiri `ID-09` detail zapisa i četiri
`ID-15` status zapisa po BE32 track indeksu. Ranija hipoteza da `ID-09` offseti
`10..12` predstavljaju `LSB, Program, MSB` selector je odbačena: triplet ima
vrlo mali broj dominantnih vrijednosti kroz veliki broj različitih STS zvukova
i ne podudara se sa dostupnim legacy native selectorima. Polje se zato izvozi
samo kao `unknown_triplet_10_12`.
Stariji nested oblik ima i četiri 78-bajtna `ID-07` companion zapisa, koji se
strukturno dijele na 34-bajtni core i potvrđeni 40-bajtni zero tail.

`ID-09` detail geometrija je lossless razložena na:

- 47-bajtni header sa markerom `5A` ili `61`;
- jedan 36-bajtni i sedam 22-bajtnih parameter blokova, svaki završen sa `5A`;
- 5-bajtni base tail;
- kod 382-bajtnog oblika dva 70-bajtna late-extension bloka sa trailerom
  `track-index 00 FF`.

`ID-15` je potvrđen kao BE32 track indeks, tri rezervisane nule i jedan još
neimenovani status kod. Nijedno od ovih novih polja se ne koristi za selector
remap dok ne postoji kontrolisani prije/poslije dokaz.

Neutralni izlaz uključuje i companion `ID-09` Style Performance/STS resurse:

- legacy PA1/PA800 raspored povezuje imenovane metadata slotove s mixer slotovima;
- četiri keyboard trake izvoze se kao `Lower`, `Upper1`, `Upper2`, `Upper3`;
- poznata legacy sound/mixer polja izvoze se semantički, uz očuvani raw zapis;
- noviji nested raspored strogo pronalazi četiri `ID-03/ID-0A` STS slota;
- svaki nested slot mora imati jedan `ID-0F` naziv i četiri `ID-08` track zapisa;
- BE32 prefiks nested track zapisa mora biti jedinstven indeks `0..3`;
- u 15-bajtnom tijelu offseti `4..7` izvoze potvrđeni mixer quartet `Volume`,
  `Pan`, `FX C send`, `FX D send`;
- selector `0..3` ostaje lossless i klasifikuje se kao empty sentinel, USER
  namespace `09`, native-reference mod `00`, native-reference mod `FF` ili
  unknown, bez nedokazanog međumodelskog remapa;
- selector offset `2` i tail `8..13` moraju biti nula, a terminator `14` mora biti `5A`.

Native-reference tokeni nisu programske adrese: svih 482 mode-`00` zapisa nose
`00 00 00 00`, a svih 220 mode-`FF` zapisa nose `FF 00 00 00`. Bank/program
zato mora biti u drugom companion polju. Parser ih više ne označava kao selector
kandidate i writer ih drži fail-closed.

USER selector porodica ima zatvorenu record-level transformaciju:

```text
legacy: FF FF 02 VOL PAN FXC FXD 09 BANK POSITION
nested: TRACK_BE32 09 BANK 00 POSITION VOL PAN FXC FXD 00x6 5A
```

Transformacija važi samo za namespace `09`; native selector porodice se tu ne
prevode automatski.

Empty selector porodica takođe ima zatvorenu record-level transformaciju:

```text
legacy: FF FF FF VOL PAN FXC FXD 00 00 00
nested: TRACK_BE32 FF FF 00 FF VOL PAN FXC FXD 00x6 5A
```

Svih 36 legacy i 1.180 nested empty zapisa prolazi egzaktni obostrani
round-trip. Nepotvrđena ostaje lokacija native sound adrese iza reference modova
`00` i `FF`.

TrackData model koristi potvrđeni raspored:

- prvih 48 bajtova: osam šestobajtnih track zapisa (`Expression`, MSB, LSB, Program, Low Key, High Key);
- naredna 24 bajta: osam pomoćnih trojki (`RX Noise`, `Floating Trigger`, `Guitar Capo`);
- narednih osam bajtova: packed `NTT/NTR` na `72+t`;
- v5 zapisi imaju dodatnih osam bajtova na `80+t`;
- PA700 varijanta ima još osam bajtova na `88+t`.

Konverzijski gate dozvoljava `80 -> 88` dodavanjem modalne vrijednosti `04`.
Potvrđeni PA700 oblik `96` jednak je `88 + 00 00 00 00 00 00 00 00`.
Downgrade odbija zapis ako dodatak `80..87` nije sav `04` ili dodatak `88..95`
nije sav `00`, osim kada pozivalac eksplicitno dozvoli gubitak.

Bajtovi `80+t` ostaju v5 extension nepoznate semantike. Direktna CV korelacija
je odbacila raniju Track Type/Guitar Mode hipotezu: iste extension vrijednosti
pojavljuju se uz različite tag/class kombinacije. Zato se nestandardna vrijednost
ne uklanja automatski.

ElementInfo konverzija koristi potvrđenu relaciju:

```text
ElementInfo44 = ElementInfo28 + 00 00 0A 00 00 00 00 00 00 00 00 00 00 00 00 01
```

U proširenom korpusu 588/600 zapisa imaju upravo taj suffix. Preostalih 12
PA700 zapisa nose nestandardne vrijednosti i zato ih `44 -> 28` gate odbija.
Smjer `28 -> 44` ostaje reverzibilan, a downgrade se automatski dozvoljava samo
kada suffix odgovara potvrđenoj canonical vrijednosti.

Potvrđene geometrije u dostavljenim uzorcima:

- PA800 inner v2: ElementInfo/TrackData `27/48`;
- PA800 inner v4: ElementInfo/TrackData `28/80`;
- PA3X inner v5: `28/88`;
- PA4X inner v5: kombinacija `28/88` i `44/88`.
- PA700 inner v5: `28/96` u potvrđenom uzorku.

## Conversion Preview

```bash
python3 prism-uploads/analysis/korg_sty_tool.py preview-convert INPUT.STY \
  --target-version 5 --json preview.json
```

Primjenjuje potvrđene inner STYLE transformacije u memoriji, ali namjerno ne piše
međumodelski STY dok `ID-09` i ciljni outer container nisu zatvoreni:

- root v4/v5 i StyleInfo release `2/3`;
- v4 StyleInfo 16-bajtni tail i v5 18-bajtni tail;
- 15-bitni content bitmap na StyleInfo offsetima tail `3..4`;
- v5 indeksni bitmap na tail `16..17`;
- TrackData `80/88` i ElementInfo `28/44` gateovi.

Kod v4→v5 indeksni bitmap dobija vrijednost postojećeg content bitmapa, pa se
sparse lista elemenata zadržava bez izmišljanja praznih elemenata. Kod v5→v4
izbacuju se samo elementi koje content bitmap označava praznim. Nestandardni
TrackData/ElementInfo nastavci se odbijaju osim uz eksplicitni `--allow-lossy`.

## STYLE Conversion Writer

```bash
python3 prism-uploads/analysis/korg_sty_tool.py convert-style INPUT.STY OUTPUT.STY \
  --target-version 5 --resource-position 3 --report convert.json
```

Writer koristi iste stroge v4/v5 gateove kao preview, ponovo kodira izmijenjene
`ID-06` resurse i gradi novi native `FE/KBEG/KEND` indeks. Bez
`--resource-position` pokušava konvertovati sve `ID-06` resurse i prekida prije
pisanja ako je ijedan nepodržan. Opcija se može ponoviti za više poznatih
indeksnih pozicija, što omogućava sigurno zadržavanje inner v2 resursa bez
izmišljanja njihove transformacije.

Inner v2 je read-only neutralizovan: njegov 48-bajtni TrackData sadrži osam
šestobajtnih selector zapisa, ali nema v4 pomoćne trojke ni packed NTT/NTR
bajtove. ElementInfo ima 27 umjesto 28 bajtova. Korpus ne daje jedinstvenu
vrijednost za ta nedostajuća polja, pa writer namjerno odbija v2→v4/v5.

Svaki generisani `OC31/OC32` resurs se odmah ponovo dekodira i mora biti jednak
očekivanom target payloadu. Svi neizabrani indeksirani resursi, uključujući
`ID-09` STS, moraju ostati byte-exact. `--allow-lossy` je potreban za svako
odbacivanje nestandardnog TrackData ili ElementInfo nastavka.

## STS Mixer Patch

```bash
python3 prism-uploads/analysis/korg_sty_tool.py patch-sts INPUT.STY OUTPUT.STY \
  --resource-position 2 --slot-index 0 --track lower \
  --field volume --value 101 --report patch.json
```

Writer podržava samo potvrđena polja `volume`, `pan`, `fx_c_send` i
`fx_d_send`. Radi na legacy i nested `ID-09` rasporedu, ne mijenja selector i
zahtijeva vrijednost `0..127`. Izlaz je direktni native STY, svi outer offseti i
`FE/KBEG/KEND` indeks se ponovo računaju, a novi OC31/OC32 tok se odmah ponovo
dekodira i poredi. Izmjena mora zahvatiti tačno jedan dekodirani bajt.

## Validacija

- 18/18 PA800 factory STYLE bankova uspješno je parsirano i prepakovano u native oblik.
- Svih 870 dekodiranih `ID-06/ID-09` resursa ostalo je semantički identično.
- Jedan puni CLI round-trip i `diff` vraćaju nula promijenjenih resursa.
- Svih sedam dostavljenih modelskih STY uzoraka prolazi indeksni framing parser.
- Pa3X i Pa4X uzorci dekodiraju sve indeksirane STYLE resurse bez codec greške.
- Ostali uzorci zadržavaju pojedinačne `decode_error` zapise; Pa3XLE dodatno ima neusklađenu root dužinu i oštećene potpise.
- Svih 112 inner v4 stilova prolazi egzaktni v4→v5→v4 byte round-trip.
- Stvarni `convert-style` writer je na svih 18 PA800 factory bankova konvertovao svih 105 inner v4 resursa u v5 i nazad uz nula promijenjenih dekodiranih resursa nakon round-tripa; inner v2 resursi ostali su byte-exact.
- Svih 330 inner v2 stilova prolazi neutralni parser: 4.290 ElementInfo27/TrackData48 elemenata, 43.761 pattern objekat i 55.985 potpuno potrošenih CV referenci.
- Selektivni sigurni v5→v4 write potvrđen je na 7 Pa3X, 17 Pa4X i 23 Pa700 resursa; neizabrani STYLE i svi STS resursi ostali su byte-exact.
- Od 129 inner v5 stilova, 80 prolazi sigurni v5→v4 gate; 46 se odbija zbog nestandardnog TrackData nastavka, a 3 zbog nestandardnog PA700 ElementInfo nastavka.
- Svih 447 nestandardnih TrackData polja pripada skupu `01,02,03,14,24,44`; nema slučajnih vrijednosti izvan potvrđenog packed obrasca.
- Pattern pool/CV parser je na 628 stilova tačno potrošio svih 110.085 globalnih referenci prema 77.634 objekta, bez ijednog ostatka.
- Svih 110.085 CV kontrolnih parova ima tačnu dužinu; class `0/1/2/3` bez izuzetka odgovara tagu `02/03/04/05`, a track indeks je uvijek `0..7`.
- Svih 2.256 nested STS traka ima tačno uparen `ID-09` detail i `ID-15` status zapis; detail geometrije su `242` i `382` bajta.
- Svih 18.048 `ID-09` parameter blokova prolazi marker/geometrijsku provjeru; 382-bajtni oblik ima 1.472 validna late-extension bloka.
- Svih 1.520 `ID-07` companion zapisa ima 40-bajtni zero tail, a svih 2.256 `ID-15` zapisa tri rezervisane nule.
- `sts-diff` vraća nula promjena za isti fajl i tačno jedan slot/track/field za kontrolisani jednobajtni `patch-sts` test.
- `sts-diff --pair-by style-name` vraća nula promjena za isti Pa4X fajl i čuva dokazivi naziv svakog uparenog STYLE/STS resursa.
- Svih 637 dekodiranih `ID-09` resursa prolazi neutralni STS model: 496 legacy i 141 nested resurs, ukupno 2.536 slotova i 10.144 keyboard track zapisa.
- Svih 141 nested resursa ima tačno četiri STS slota; svaki slot ima jedan naziv i track indekse `0,1,2,3` bez duplikata.
- Svih 2.256 nested track tijela potvrđuje isti 15-bajtni raspored: selector `0..3`, mixer `4..7`, šest nula i terminator `5A`.
- `patch-sts` round-trip je potvrđen na legacy PA1 i nested PA4X resursu; u oba slučaja mijenja se samo izabrani mixer bajt, selector ostaje identičan i rekonstruisani indeks prolazi strogu provjeru.
- Svi dostupni namespace-`09` USER track zapisi prolaze egzaktni legacy→nested→legacy ili nested→legacy→nested record round-trip.
- Svih 36 legacy i 1.180 nested empty selector zapisa prolazi egzaktni obostrani record round-trip.
- `ID-09` parser prolazi 637 dekodiranih resursa: 496 PA1/PA800 legacy struktura i 141 noviju nested strukturu; legacy skup sadrži 1.972 mixer i 1.944 imenovana slota.

## Granice

- Writer/repacker je trenutno dokazano PA800-orijentisan; inspect je multi-model i tolerantan.
- `convert-style` piše samo strogo parsabilne native kontejnere; oštećeni ili deklarativno neusklađeni uzorci ostaju read-only umjesto automatskog popravljanja.
- Inner STYLE v2 se lossless čita, ali nema dokazano pravilo prelaska u v4/v5 i zato se fail-closed preskače samo eksplicitnim izborom drugih resursa.
- Legacy `ID-09` polja se čitaju semantički, dok se novije nested generacije za sada samo strogo inventarišu.
- Literal-only izlaz je veći od originalnog kompresovanog sadržaja.
- Prihvat generisanog fajla na fizičkom PA800 uređaju još nije potvrđen.
- Alat ne predstavlja međumodelski konverter dok se ne utvrde transformacije iz kontrolisanih prije/poslije parova.