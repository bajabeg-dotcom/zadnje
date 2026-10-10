# Pa800 — kontrolisani hardverski test (priprema)

**Trenutni v0.1 nema native writer.** Ovaj dokument priprema podatke za implementaciju i kasniji test. Nemoj učitavati fajl koji je generisao prototip — takav fajl ovaj build uopšte ne izvozi.

## Potrebno od vlasnika instrumenta

1. Fotografija ili zapis **Pa800 OS verzije**, modela/ekspanzije i vrste memorije (koliko znaš).
2. Kopija vlastitog **testnog SET foldera** zapakovana u ZIP, idealno sa `STYLE`, `PERFORM`, `SOUND` i `PAD` folderima. Prije slanja napravi zasebnu nezavisnu rezervnu kopiju originala.
3. Ako želiš dokazati pojedinačne nepoznate kontrolne bajtove, na klavijaturi napravi dva odvojena snimka istog **nevažnog USER stila**: A = prije izmjene; B = poslije promjene **samo jednog** parametra, npr. imena ili dodjele jednog Sounda. Sačuvaj ih na posebne USB lokacije i zapiši *jedinu* promjenu, OS verziju i korake.
4. Izbjegni rad nad nastupnim SET-om i tvorničkim podacima. Ne vrši potpuni restore dok nisu potvrđeni backup i povratak na staro stanje.

## Kasniji acceptance gate — tek nakon implementacije writer-a

- [ ] Original i izlaz imaju sačuvan SHA-256; original ostaje netaknut.
- [ ] Parse → jedna dozvoljena izmjena → serialize → reopen → validacija svih indeksa, dužina i kontrolnih bajtova.
- [ ] Byte-diff: nepoznati listovi i STS companion se ne mijenjaju.
- [ ] SET na odvojenom USB-u: klavijatura učita banku bez greške.
- [ ] Odabrani Style/Pad/Performance se pojavljuje i svira; poređenje s originalom.
- [ ] Save na klavijaturi, ponovno učitavanje i druga provjera zvuka.
- [ ] Dokumentovane napomene, OS verzija, log i povratni backup.

**Rezultat testa se mora upisati kao PASS/FAIL sa stvarnim hardverskim opažanjem.** Softverski test i ime fajla ne zamjenjuju ovu provjeru.
