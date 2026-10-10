# KORG PA800 STY GUI

GUI koristi isti provjereni engine iz `korg_sty_tool.py` i ne izvršava uploadovane EXE fajlove. Glavni launcher otvara lokalni browser GUI na `127.0.0.1`; fajlovi se ne šalju na internet.

## Pokretanje na Windowsu

1. Instalirati standardni Python 3.
2. Držati `korg_sty_web_gui.py`, `korg_sty_gui.py` i `korg_sty_tool.py` u istom folderu.
3. Pokrenuti `KORG_STY_GUI.bat` dvostrukim klikom.

Browser se automatski otvara. Crni terminalski prozor treba ostaviti otvoren dok koristiš GUI.

## Preporučeni PA800 postupak

1. Izabrati originalni `.STY` fajl.
2. Pokrenuti **Pregledaj / validiraj**.
3. Ako nema grešaka, izabrati **PA800 native repack**.
4. Sačuvati original kao backup i testirati novi `_PA800_NATIVE.STY` na instrumentu.

## Eksperimentalna konverzija

Opcije `STYLE → PA800 inner v4` i `STYLE → inner v5` mijenjaju samo potvrđene
`ID-06` STYLE strukture. Companion `ID-09/STS` ostaje byte-exact iz izvornog
fajla jer potpuna međumodelska transformacija još nije dokazana. GUI zato traži
dodatnu potvrdu i označava rezultat kao hardverski nepotvrđen.

Konverzija je fail-closed: ako jedan STYLE resurs nije podržan, izlaz se ne piše.
Originalni ulaz nikada se ne smije koristiti kao izlazna putanja.

## Izvještaji

Svaka operacija generiše JSON izvještaj. Njega treba sačuvati uz izlazni STY,
posebno ako instrument odbije fajl ili primijetiš pogrešan zvuk/STS postavku.