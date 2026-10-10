# Changelog

## 0.3.0 — 2026-10-06

- Added `pa_tool/validator.py` with 18 evidence-backed model/variant profiles.
- Added 32 rules covering every known PA Manager extension plus SET/KST/ZIP.
- Added recursive per-file tree validation, strict SMF parsing, WAVE/AIFF/SF2 checks, KORG/OC20 checks and bounded ZIP validation.
- Added complete 10,368-cell source-model/target-model/file compatibility matrix.
- Added `validators`, `validate-all`, `model-matrix`, and `model-convert` CLI commands.
- Added executable, source-protected byte-identical copy and preserve-only package paths.
- Added real PCM WAVE conversion to 44.1 kHz / 16-bit / mono.
- Added explicit fixed-width outer KORF catalog edits with byte-difference, length, reparse and read-back guards.
- Added staging output, source tree hashes before/after, target validation and conversion JSON reports.
- Added 26 validator/converter regression and CLI tests; full suite now 140 tests with 16 optional skips.
- Kept universal Pa4X-to-PA800 Sound/PCM remapping refused where a complete dependency graph/writer is not proven.

## 0.2.0 — 2026-10-06

- Integrirana korisna, ne-inferiorna funkcionalnost iz `PA800_Manager_Web_v0.2_NATIVE_SET_FORENSICS.zip`.
- Dodan bundled bounded CBarc/OC20/OC31/OC32 sloj; eksterni codec više nije potreban za osnovno OC dekodiranje.
- Dodan strogi PA800 bank parser: root/type-05/FE indeks, sparse Style/PAD bitmap, PatternInfo/CV/TrackInfo i poznata PRF geometrija.
- Dodan tačan katalog od 1.003 PA800 Factory Sound selektora.
- Dodan candidate-only Style TrackInfo → USER01/USER02/USERDK PCG graf.
- Dodan bounded ZIP/SET workspace: CRC/SHA svakog nešifriranog člana, traversal/symlink/duplicate/bomb zaštita i byte-identičan original.
- Dodane `native-bank`, `native-set`, `native-sounds` i `web` komande.
- Integriran cijeli read-only web UI/API za archive, Style, Performance, PAD, Sound, setup i validation workflowe.
- Dodani offline corpus research helperi, svih 6 source fixturea, 12 prilagođenih source testova i web/CLI integracijski testovi.
- Dodana feature-by-feature odluka `INTEGRACIJA_PA800_MANAGER_WEB_V02.md` i očuvani source izvještaji/statički JSON dokazi.
- Ispravljen nezatvoren file handle u `detect.fingerprint_file`.
- Native Save/Convert/BKP Restore ostaje zaključan: source ga nije implementirao niti hardverski certificirao.

## 0.1.2 — 2026-10-06

- Dodan read-only `pcg` klasifikator za PA `KORF` i workstation `KORG/PCG1` dijalekte.
- Dodan `data/pcg_chunks.json` sa 4 stvarna Drive uzorka, SHA-256 dokazima i izvorima.
- Riješeno značenje `PBK1`/`PRG1`/`RLP1`: workstation PCG / KMP, ne univerzalni PA potpisi.
- PA588 označen kao PA500 SOUND-handler varijanta, PA3X Le kao PA3X varijanta (`HIGH`).
- Dodan reproducibilni `tests/fixtures/USER01.PCG`; broj skipova smanjen sa 20 na 14.
- Ispravljeno izvještavanje `models`: nepoznat PA500 RAM više se ne prikazuje kao VERIFIED; Pa5X bez SUB-a se ne naziva varijantom nego eksplicitno odbijenim SOUND importom.
- Verzija paketa usklađena sa dokumentacijom.
- 96 testova prolazi; 14 se preskače bez velikih eksternih asseta.
