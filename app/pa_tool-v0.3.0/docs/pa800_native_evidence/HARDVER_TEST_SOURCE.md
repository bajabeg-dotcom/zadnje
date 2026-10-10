# Pa800 OS 2.02 — uzorci i kontrolisani hardverski test

**Trenutna v0.2 web aplikacija je read-only. Ne učitavaj u instrument nijednu datoteku koju je navodno ona „konvertovala” — takav izlaz ona ne pravi. Nikada ne vraćaj eksperimentalni BKP na jedinu kopiju nastupnih podataka.**

## Minimalni uzorci za dovršavanje native SET/BKP dekodiranja

1. **Iz tvog Pa800 OS 2.02 (27. 7. 2011.)** napravi `Media → Save → All` u **novi** folder, npr. `PA800_OS202_BEFORE.SET`, pa kopiraj čitav `.SET` folder na računar i zapakuj ga u ZIP. Provjeri da u meniju Save All **ručno uključiš PCM i SongBook** (ti podaci mogu biti isključeni po zadanim postavkama). Sačuvaj STYLE, SOUND, MULTISMP, PCM, PERFORM, PAD, SONGBOOK i GLOBAL ako ih instrument nudi. Ne šalji samo `.STY` ili screenshot. Nemoj redigovati/ponovo kodirati native datoteke. Učini to na stanju koje smiješ podijeliti.
2. Ista klavijatura, isto stanje memorije: `Media → Utility → Full Backup Resources`, po mogućnosti prvo **bez kompresije** (`PA800_OS202_UNCOMPRESSED.BKP`), a zatim i **s kompresijom** ako instrument nudi izbor. **Samo napravi kopiju; nemoj koristiti Full Restore.** `.BKP` je drugačiji paket od `.SET` i OS `.pkg`. Ako je datoteka preko 100 MB, podijeli je u dijelove ispod 90 MB i postavi sve dijelove uz uputu kako ih spojiti. Ove datoteke mogu sadržati privatne korisničke podatke — dijeli ih samo tamo gdje želiš.
3. **Par A/B za diferencijalnu provjeru**: napravi prije/poslije kopiju jednog **nevažnog/kopiranog** korisničkog Style-a ili Sounda. Promijeni **samo jedno** poznato svojstvo, npr. ime Style kopije ili dodjelu jednog zvuka u rezervnom slotu. Snimi zasebno `BEFORE.SET` i `AFTER.SET` bez izmjene drugih stvari, i priloži `CHANGELOG.txt`: model, OS, memorija/ekspanzija, datum, konkretan slot, tačno jedna izmjena. Originalni nastupni SET ostavi netaknut.
4. Napiši je li postojeći `Hammy.SET.zip` nastao **direktno na tvom Pa800 OS 2.02** ili drugom Pa modelu; isto važi za samostalni `USER01.STY` (u njemu je v5/15-elemenata, dok dostupni Pa800 Factory STY ima v2/v4/13). Reci koji **izvorni** SET želiš prvi pretvoriti na Pa800: Pa1X BABAJA, Pa1X Redbull ili drugi. Za šifrovani `Pa1x Redbull Novoboki Greek.SET.rar` pošalji **lozinku ili legitimno raspakovani ZIP** samo ako želiš obradu tog seta.

Koristan raspored, ali imena nisu obavezna:

```text
PA800_OS202_EVIDENCE/
  OS_AND_CHANGELOG.txt           # OS 2.02 27.07.2011, model, ekspanzija, opis promjene
  BEFORE.SET/                    # cijeli native folder, uključivo PCM i SongBook
  AFTER_ONE_EDIT.SET/            # samo ako se radi kontrolisani A/B test
  FULL_BACKUP_UNCOMPRESSED.BKP    # stvarni Backup s klavijature, bez restore-a
  FULL_BACKUP_COMPRESSED.BKP      # poželjno dodatno
  SHA256SUMS.txt                  # poželjno
```

**Ne postavljaj OS 2.03 kao zamjenu za 2.02.** Postojeći Drive `pa800_pkg_extracted.zip` ima Pa800 Factory STY/PCG i snažno se poklapa s tekstualnim izvještajem o OS 2.02 (ista pozicija PCMIMAGE/BANK00.BIN na 0xDE0576), ali originalni binarni `Pa800_os_res_v202.pkg` nije dostupan za kriptografsku provjeru; veliki OS2.02 RAR se ne može preuzeti kroz ovaj Drive alat zbog limita od 100 MB.

## Nakon što postoji provjerljiv writer (tek u kasnijem izdanju)

- [ ] Originalni SET i BKP kopirani na zaseban USB; zabilježeni SHA-256.
- [ ] Na računaru: parse → edit poznatog polja → serialize → ponovno otvori → structural i semantic compare; nepoznati listovi i STS/FX očuvani.
- [ ] Provjeri reference STYLE/STS/PRF → PCG → KMP → PCM; nema orphan resursa koji nisu namjerno fabrički fallback.
- [ ] Testiraj **samo jednu banku** na Pa800 iz odvojenog testnog SET-a, ne full BKP restore. Zabilježi učitavanje, nazive, sound/kit, Style playback.
- [ ] Na klavijaturi snimi testni SET, ponovno ga učitaj i uporedi rezultat. Označi PASS/FAIL na osnovu stvarnog opažanja.
- [ ] BKP restore na instrumentu je posljednja faza, zaseban destruktivan test uz siguran povratni plan; ne raditi ga automatski.
