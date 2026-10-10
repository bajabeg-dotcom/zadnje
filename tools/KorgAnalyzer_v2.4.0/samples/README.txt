PA800 MIDI OPTIMIZER -- TEST MATERIJAL
======================================

Ovi fajlovi su tu da mozes odmah probati program bez trazenja svog materijala.
Sve je iz tvog vlastitog Drive materijala (demo_midis.zip i
PA800_FACTORY_STYLES_PATTERNS_PADS_REFERENCE.zip).

MIDI/  (pravi Korg demo snimci)
  GIPSY.MID          -- balkan/gypsy, 1864 note, 8 kanala
  ETHNO.MID          -- etno, 1425 nota
  E.Piano2 RX.MID    -- dokaz da je RX preset stvarno sviran
  Tenor Sax DNC.MID  -- dokaz za DNC artikulaciju (Sax DNC preset)

FACTORY/  (prave Pa800 factory banke stilova)
  BANK01.STY         -- 18 stilova (Standard 8 Beat, Guitar Beat, ...)
  BANK12.STY         -- sadrzi 'Gipsy Dance'

PRAVILA OPSEGA (Key Range)
--------------------------
Key Range je HARD LIMIT -- program samo prijavi KEY_RANGE_CONFLICT, nikad ne
pomjera notu. Pravilo po tipu instrumenta + dokaz iz tvog GOLD korpusa (830
pjesama) pogledaj sa:      python -m pa800_midi key-range
Svoje pravilo zadaj sa:   --key-range "guitar=D2-E6"

KAKO PROBATI
------------
1. POKRENI_PA800_MIDI.bat  (Windows)  ili:
   python -m pa800_midi analyze samples/MIDI/GIPSY.MID
2. Za provjeru na stvarnoj fabrici (izgradi svjez factory indeks iz banaka):
   python -m pa800_midi index factory samples/FACTORY --workdir /tmp/fi
3. Optimizacija:
   python -m pa800_midi optimize samples/MIDI/GIPSY.MID

Rezultati (MIDI + report.txt + report.json) idu u workspace/pa800_midi/.
ORIGINALI SE NIKAD NE MIJENJAJU.
