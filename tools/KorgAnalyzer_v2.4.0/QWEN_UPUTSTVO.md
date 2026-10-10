# Lokalni Qwen asistent — uputstvo

Program može da ti **objasni** šta nađe u fajlu — preko lokalnog Qwen modela.
Sve radi **na tvom računaru**. Ništa se ne šalje na internet, nema naloga, nema
pretplate, nema ograničenja koliko puta pitaš.

---

## 1. Šta ti treba

| | Minimum | Preporučeno |
|---|---|---|
| RAM | 8 GB | 16 GB |
| Mjesta na disku | ~6 GB | ~10 GB |
| Model | `qwen2.5:3b` | `qwen2.5:7b` |
| Grafika | nije potrebna (radi na procesoru) | NVIDIA sa 8 GB+ radi mnogo brže |

Ako imaš **8 GB RAM-a ili manje** → uzmi `qwen2.5:3b`.
Ako imaš **16 GB RAM-a** → uzmi `qwen2.5:7b` (preporučeno).
Ako imaš **32 GB RAM-a** → možeš `qwen2.5:14b`.

---

## 2. Instalacija — tri koraka

### Korak 1: instaliraj Ollamu

1. Otvori **https://ollama.com/download/windows**
2. Skini `OllamaSetup.exe` i pokreni ga
3. Klikni Next → Install → Finish

Ollama se poslije instalacije **sama pokreće** i radi u pozadini (vidjećeš malu
ikonu pored sata). Ne treba ti terminal.

### Korak 2: pusti program da provjeri

Dvoklikni **`POKRENI_QWEN.bat`** u folderu programa.

Skripta će:
- naći Ollamu,
- pokazati koje modele imaš,
- ako nemaš Qwen — pitati te koji tag hoćeš i skinuti ga,
- na kraju pokazati da li program vidi model.

Prvi download traje **5–20 minuta** zavisno od brzine interneta. Nakon toga više
nikad ne treba internet.

### Korak 3: koristi

1. Dvoklikni **`KorgAnalyzer.bat`**
2. **Otvori fajl...** → izaberi svoj `.STY`
3. Idi na tab **AI (Qwen)**
4. Klikni neko od gotovih dugmadi ili ukucaj svoje pitanje i klikni **Pitaj**

---

## 3. Gotova pitanja (dugmad)

| Dugme | Šta radi |
|---|---|
| **Objasni ukratko** | Koliko stilova, da li su resursi validni, ima li nešto sumnjivo |
| **Objasni razlike u imenima** | Zašto se kod nekih slotova tabela i stream razlikuju — koje tačno i šta to znači |
| **Provjeri integritet** | Da li je svaki resurs prošao veličinu i XOR checksum |
| **Šta mogu dalje** | Konkretne sljedeće provjere i koji priručnik to objašnjava |
| **Za laika** | Isto ali jednostavnim jezikom, bez tehničkih termina |

Možeš i sam pisati, npr:
- „Koji stil ima najviše elemenata i zašto to znači da je složeniji?"
- „Objasni mi razliku između resursa 0x06 i 0x09."
- „Da li je ovaj fajl pravljen za Pa3X ili je konvertovan?"

---

## 4. Kako da znaš da odgovor nije izmišljen

Ovo je najvažniji dio i zato je ugrađen u sistem.

**Model ima pravo koristiti SAMO podatke iz izvještaja** koji je program napravio
za tvoj fajl. Ne vidi internet, ne zna Korg format napamet — dobija tvoje brojeve.

U uputstvu koje model dobija piše:

1. Koristi isključivo podatke iz priloženog izvještaja. Ne izmišljaj brojeve,
   offsete, checksume, imena stilova.
2. Ako podatak nije u izvještaju — reci *„to nije u ovom izvještaju"* i navedi
   KOJI bi podatak to pokazao. **Ne nagađaj.**
3. Razlikuj tri nivoa i nikad ih ne miješaj:
   - **VALIDATED** = dekoder je dokazao (veličina se poklapa I XOR prolazi)
   - **CANDIDATE** = znak postoji, ali nije dokazano
   - **UNKNOWN** = nema podataka
4. Ne predlaži mijenjanje Korg fajlova — program je read-only.

**Zato ako model počne da izmišlja, to je greška modela, a ne programa.** Pitanje
mu postavi drugačije, konkretnije: „navedi broj iz izvještaja".

---

## 5. Komandna linija (ako voliš)

```bash
python -m korg_binary_analyzer ai-status              # da li Ollama radi
python -m korg_binary_analyzer ai-pull --list-suggested
python -m korg_binary_analyzer ai-pull qwen2.5:7b     # skini model
python -m korg_binary_analyzer ask USER01.STY "objasni mi ovaj fajl"
python -m korg_binary_analyzer ask USER01.STY "koje su razlike u imenima?" --save odgovor.txt
python -m korg_binary_analyzer ask workspace/reports/.../report.json "provjeri integritet"
```

Zadnja komanda je korisna: možeš pitati o **već snimljenom** izvještaju bez
ponovnog dekodiranja.

---

## 6. Ako nešto ne radi

| Problem | Rješenje |
|---|---|
| U tabu piše „Ollama ne radi" | Otvori Start → Ollama. Ili u Command Promptu: `ollama serve` |
| „nema nijedan model" | Klikni **Preuzmi model ...** u tabu, ili pusti `POKRENI_QWEN.bat` |
| Odgovara jako sporo | Manji model: `ollama pull qwen2.5:3b` pa ga izaberi u padajućem meniju |
| Prekida se u pola odgovora | Klikni **Zaustavi** pa pitaj kraće pitanje |
| Ponavlja se / piše gluposti | Klikni **Očisti** (briše historiju) i pitaj konkretnije |
| Ne pomaže ništa | `python -m korg_binary_analyzer ai-status` — pokaže tačno šta ne radi |

---

## 7. Koliko to zauzima

| | |
|---|---|
| Ollama program | ~1.5 GB |
| qwen2.5:3b | ~1.9 GB |
| qwen2.5:7b | ~4.7 GB |
| Modeli se čuvaju u | `C:\Users\<tvoje ime>\.ollama\models` |

Modeli ostaju na disku. Skidaš ih **jednom**, poslije radi bez interneta.

Za brisanje modela koji ti ne treba: `ollama rm qwen2.5:3b`

---

## 8. Zašto baš Qwen 2.5

- **Razumije naš jezik.** Qwen 2.5 je posebno dobar za bosanski/srpski/hrvatski
  u odnosu na svoju veličinu.
- **32K konteksta** na 7b — stane cijeli izvještaj sa svim slotovima.
- **Radi na procesoru.** Nije ti potrebna skupa grafika.
- **Apache 2.0 licenca** — smiješ ga koristiti kako hoćeš, i privatno i poslovno.

Ako imaš jaču mašinu, program radi i sa `qwen3`, `qwen2.5-coder` i bilo kojim
drugim modelom iz Ollame — samo ga izaberi u padajućem meniju.
