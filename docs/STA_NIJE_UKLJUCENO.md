# Šta NIJE uključeno u VirtualPa800 NATIVE 0.2 (i zašto)

## 1. `SETOVI.zip` — 362 MB (Drive ID: `1jgwdw2Rz54wQA_ODlcFB5jvu4soRQyn_`)

**Razlog:** prevelik za automatski transfer (limit alata je 100 MB po fajlu).

**Kako dodati ručno:**

1. Skini `SETOVI.zip` sa Google Drive-a (folder `0AFbaa-xhYYsdUk9PVA`).
2. Ubaci ga u folder `sets/` pored `PA800_KOMPLET_2026-10-05_v2.zip`.
3. (Opciono) osvježi manifest: `sha256sum sets/SETOVI.zip >> MANIFEST_SHA256.txt`

## 2. `kpmv3_runtime.zip` — 49 MB (Drive ID: `1u443iIiVxEOiioZYochfE-rNbhfx8pYD`)

**Razlog:** arhiva sadrži samo JEDAN fajl — `kpmv3_runtime.mdmp` od **179 MB**,
što je Windows crash-dump, a ne podatak koji aplikacija treba. Namjerno izbačen
da paket ne nosi 50–179 MB smeća. Ako ti ipak treba, skini ga sa Drive-a.

## 3. Starije verzije (zamijenjene novijima — nisu potrebne aplikaciji)

| Fajl na Drive-u | Drive ID | Zamijenjen sa (u paketu) |
|---|---|---|
| `KorgPaManager_Complete_v1.1.0.zip` (26,8 MB) | `1WSKTGmXbWBeXeDOzfnBV9EhlEUFJl7kg` | `tools/KorgPaManager_Complete_v1.1.1/` (fix `[Errno 9]`) |
| `KorgAnalyzer_v2.0.0.zip` – `v2.3.0.zip` | vidi Drive pretragu `KorgAnalyzer` | `tools/KorgAnalyzer_v2.4.0/` |
| `PA800_KOMPLET_2026-10-05.zip` (v1, 19,2 MB) | `1Kyb2kJtye9ew5Tz-k4q5Yg5EfhoWYrTQ` | `sets/PA800_KOMPLET_2026-10-05_v2.zip` |
| `New Project (3).zip` | `18Ye4U79yjvcGfOSUWe-1d0ZnPTkAl8oU` | n/a (FL Studio projekat, nije dio app-a) |

Ako nešto od ovoga ipak zatreba, Drive ID-jevi su gore — fajlovi su i dalje na Drive-u.
