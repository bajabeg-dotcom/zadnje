# Korg Binary Forensic Analyzer

A read-only tool that opens real Korg Pa resource files, decodes them with the
actual Korg compression algorithm, and tells you what is inside — with evidence.

It is not a viewer that guesses. Every resource it reports has passed the same
two checks the instrument's own firmware applies:

1. the CBarc stream produced **exactly** the size the container declared, and
2. the XOR checksum byte matches the decoded content.

Anything that fails is reported as a candidate with the reason, never as a fact.

---

## Quick start (Windows)

1. Extract the whole folder somewhere writable (Desktop or Documents).
2. Double-click **`KorgAnalyzer.bat`**.
   * If Python/Tk is missing, run **`install.bat`** once first — it needs no
     administrator rights and changes nothing outside this folder.
3. Click **Otvori fajl...** and pick a `.STY`, `.PCG`, `.KMP`, `.PRF`, `.BKP`
   or `.pkg` file — or **Otvori folder...** to process a whole folder at once.

The program is **read-only**. The input file is opened for reading only and its
SHA-256 is verified before and after analysis.

## Quick start (Linux / macOS)

```bash
python3 -m korg_binary_analyzer gui          # desktop interface
python3 -m korg_binary_analyzer selftest     # verify the codec works
python3 -m pytest                            # run the test suite
```

Tkinter 8.6 or newer is required for the GUI (Debian/Ubuntu: `python3-tk`).
The command line works without it.

## Command line

```text
python -m korg_binary_analyzer gui                     # desktop interface
python -m korg_binary_analyzer analyze  file.sty       # identify + probe
python -m korg_binary_analyzer decode   file.sty       # decode resources
python -m korg_binary_analyzer scan     folder/        # analyse a whole folder
python -m korg_binary_analyzer compare  a.sty b.sty    # diff two files
python -m korg_binary_analyzer report   workspace/     # read a saved report
python -m korg_binary_analyzer hex      file.sty 0x7ac # hex window
python -m korg_binary_analyzer selftest                # codec self-test
```

Generated files go under `workspace/` by default:

```text
workspace/
├── reports/<mode>-<digest>-<timestamp>/   report.txt, report.json, agent context,
│                                          strings.txt, entropy.csv, artifact_manifest.json
├── decoded/<file-stem>/                   native.bin, resources/, manifest.json
└── logs/analyzer.log
```

`decode` writes each decoded resource to its own file plus a `manifest.json`
holding the full slot table. The report lists every style in the file with both
its directory name and the name recovered from the decoded stream, so a
mismatch is visible instead of hidden.

## What is actually implemented

**The codec layer** (`korg_binary_analyzer/korg/`) — this is the real thing:

| Layer | What it is | Status |
|---|---|---|
| **CBarc** | The LZ77 variant Korg's firmware uses (`CChkCmdBG::DecompressData`). Literal tokens, three match-token classes, trailing-literal rule, exact-capacity requirement. | Implemented, validated against factory corpora |
| **OC31 / OC32** | Per-resource wrapper: magic + declared size + CBarc stream + `01 00 00` terminator + XOR checksum. | Implemented, checksum verified per resource |
| **OC20** | Factory-package page wrapper used inside `.BKP` backups and `.pkg` updates (256 KiB pages). | Implemented, pages concatenate back to the native container |
| **Native container** | Root record `01 00 01 14`, a flat record sequence, and the terminal `FE` record carrying a `KBEG`/`KEND` offset index. | Implemented with strict and tolerant walks |
| **Name directory** | The uncompressed id `0x05` record: NUL-padded fixed-width names (48 bytes per entry). | Implemented; aligned slot-by-slot against the decoded streams |

The token grammar was confirmed by disassembling the Pa1X OS and by decoding
factory corpora from Pa1X, Pa800, Pa3X, Pa600 and Pa4X/Pa700/Pa1000 without a
single failure. It is the same layer on every model — only the outer container
changed across generations.

**Still an honest placeholder** (`korg_binary_analyzer/korg/decoders/`):
`COMP16` module blobs, `LFO-PKG` installer membership and raw PCM banks stay
lexical candidates. A lexical hit is an observation, never a decode.

**Generic capability** (unchanged from the original core): streaming hashes,
string scans, entropy summaries, PE32/PE32+ parsing, ZIP/GZIP/BZIP2/XZ
extraction, embedded PNG/MIDI/RIFF/JSON/XML checks, directory scanning,
duplicate grouping, file comparison, bounded hex windows and an agent-context
export.

## Official manual library

The tool ships with a curated, verified catalogue of **247 official manual PDFs
across 58 models** from Korg, Yamaha and Roland — 1.72 GB of manufacturer
documentation. Every entry points at the manufacturer's own download URL; the
scripts in `tools/` resolved them from the official support portals and all 247
were confirmed reachable. Nothing is re-hosted, mirrored or republished.

| Brand | Models | Notes |
|---|---:|---|
| Korg | 34 | Pa1X through Pa5X, including ORIENTAL / MUSIKANT / Serbia variants |
| Yamaha | 14 | Genos2, Genos, Tyros5, PSR-SX / S / A series |
| Roland | 10 | E-A7, BK-5/3/7m, G-70, PRELUDE, VA series |

Documents are classified by **depth**, because that is what actually matters:

* **reference** — explains the format and its contents (Korg User Manual,
  Advanced Edit, Data List; Yamaha Reference Manual, Data List, MIDI
  Implementation; Roland Reference Manual, Style/Tone List, Waveform List);
* **guide** — introductions, quick guides, accessory booklets and companion
  software manuals.

For each Korg model the catalogue also records the **container era** the
analyser decodes, so the reference and the decoder stay aligned:

| Model | Container |
|---|---|
| Pa1X / Pa2X / Pa800 / Pa500 | BKP backup (fb-record) + OC20/OC31/OC32 + CBarc + XOR |
| Pa3X | pkg/upd installer wrapping OC20 pages, same OC31/OC32 payloads |
| Pa600 / Pa900 / Pa300 | pkg/upd with a 16-byte MD5 header and zlib per blob |
| Pa4X / Pa700 / Pa1000 | lfo-pkg installer (Blowfish CBC family key), same resources |

### Using it

```bash
python -m korg_binary_analyzer manuals                          # browse everything
python -m korg_binary_analyzer manuals --search pa5x --details  # one model
python -m korg_binary_analyzer manuals --brand Korg --deep-only # reference docs only
python -m korg_binary_analyzer manuals --search pa3x --dry-run  # preview a download
python -m korg_binary_analyzer manuals --brand Korg --download  # fetch them
```

Downloads land in `manuals/<brand>/<model>/` and resume-safe: an already
complete file is skipped rather than fetched twice. In the GUI there is a
**Prirucnici** tab with a search box, a brand filter, buttons to open a document
in the browser or download one model / the whole library, and reference-grade
documents highlighted in green.

After analysing a file the program also **matches the model and points at its
manuals** — in the report (`OFFICIAL MANUALS:` section) and by selecting the
matching rows in the tab. Matching is deliberately shallow: a model name must
appear in the file name, its folders, or the file's own strings. An unmatched
file gets no reference rather than a plausible-looking wrong one.

## PA800 MIDI Optimizer / Restorator

Deterministic, evidence-driven MIDI optimizer for the Korg Pa800, built on the
user's own factory material and merged into the same package. Full guide:
[`UPUTSTVO_PA800_MIDI.md`](UPUTSTVO_PA800_MIDI.md).

The four hard rules:

1. **NOTE-ON TICK is immutable** -- no quantize, humanize, swing, snap or
   timing shift, on any track.
2. **PITCH is immutable** -- no transpose, no pitch shift, no note relocation.
3. **Key Range is a HARD LIMIT** -- a note outside it is reported as
   `KEY_RANGE_CONFLICT` and left exactly where it is.
4. **The original is never overwritten** -- output goes to `workspace/`, and
   carries source SHA-256, output SHA-256, the transformation plan, the
   evidence report and the validation result.

Only velocity, CC, program/bank, RX identity and (with proof) articulation may
change.  Every decision is a plan row: track, role, operation, old value, new
value, reason, evidence, confidence.  Confidence is `VERIFIED`, `DOCUMENTED`,
`STRONG_EVIDENCE` (may be applied), `CANDIDATE` (proposed only) or `UNKNOWN`
(changes nothing).

Evidence sources: FACTORY STYLE, FACTORY SOUND / OSC, RX/DNC, GOLD, MANUAL,
DEMO (EFX/articulation only), HARD LIMIT and USER RULE -- ranked per question,
not as one global order.

```bash
python -m pa800_midi analyze  song.mid                 # read-only look
python -m pa800_midi plan     song.mid                 # plan, nothing applied
python -m pa800_midi optimize song.mid --rule velocity=-15
python -m pa800_midi batch    "D:\MIDI PJESME"         # whole corpus + CSV/JSON
python -m pa800_midi key-range                         # per-type ranges + GOLD proof
python -m pa800_midi key-range --strict                # physical rules only
python -m pa800_midi selftest                          # 10 hard-rule checks
python -m pa800_midi ask song.mid "ima li konflikata?" # local Qwen
```

**Key Range as a rule per instrument type** (`pa800_midi/keyrange.py`): 23
instrument types with a shipped range, resolved as *user rule > measured
profile of the actual sound > type rule > no claim* (drums, synth, FX and
unknown types never claim a range). The user's own GOLD corpus is used as
concrete evidence: a rule is widened only where at least 3 songs and 5 notes
really play outside it, and every such change is named in the report
(`--key-range-strict` switches that proof off). Rules are editable —
`--key-range "bass=E1-C4,guitar=D2-E6"`, `--key-range-file table.json`, or by
editing `pa800_midi/data/key_range_rules.json`. A conflict is reported
(`KEY_RANGE_CONFLICT`), never repaired: the note does not move.

Bundled evidence: **435 real Pa800 factory styles** (18 banks, decoded with the
existing binary decoder), a demo index for RX/DNC articulation proof, the
Pa800 sound/drum tables, seven measured instrument profiles, the per-type
key-range table, the Korg manual reference and the measured **GOLD index over
the user's own 830-song corpus** (played ranges per instrument type, velocity,
gate, density). Test material ships in `samples/` (4 real demo MIDIs, 2 factory
banks). Everything is verified on real files: 147 tests and 10/10 selftest
checks pass from a clean unzip.

Delivered beside the program: `PA800_GOLD_CORPUS_830.zip` — the whole 830-file
GOLD corpus plus `GOLD_INDEX.json` and a README, so the measurement can be
reproduced or restored in one step.

## Local Qwen assistant (optional)

The analyser can explain what it found, using a **Qwen model running locally
through Ollama**. Nothing is sent anywhere: the model file is downloaded once,
and every answer is generated on the machine. No account, no subscription, no
rate limit. See [`QWEN_UPUTSTVO.md`](QWEN_UPUTSTVO.md) for the step-by-step
Windows guide.

### Install

1. Install Ollama from <https://ollama.com/download/windows> (Next → Next).
2. Run **`POKRENI_QWEN.bat`** once — it checks Ollama, offers the model sizes and
   pulls the one you pick.
3. Open `KorgAnalyzer.bat` → tab **AI (Qwen)**.

Model sizing:

| Model | Download | Needs | Use |
|---|---|---|---|
| `qwen2.5:3b` | ~1.9 GB | 8 GB RAM | quick answers |
| `qwen2.5:7b` | ~4.7 GB | 16 GB RAM | **recommended**; 32K context |
| `qwen2.5:14b` | ~9.0 GB | 32 GB RAM | better reasoning |

Any other Ollama model works too — the dropdown lists whatever is installed.

### How the answers stay grounded

The assistant is not allowed to free-associate about Korg formats. Before every
question the program hands it a digest of **measured facts only** — resource
counts, per-slot names, validation evidence, framing results, entropy readings
and the matched manual. The system prompt then enforces:

* use only the attached report; invent no numbers, offsets, checksums or names;
* when something is not in the report, say so and name the measurement that
  would settle it instead of guessing;
* keep `VALIDATED`, `CANDIDATE` and `UNKNOWN` strictly distinct;
* never propose writing to a Korg file — the tool is read-only.

The evidence digest deliberately excludes the raw resource blob list, so a large
decode cannot crowd the model's context. A test asserts the facts actually
arrive by running a fake Ollama server that echoes back what it was given.

### Command line

```bash
python -m korg_binary_analyzer ai-status                  # is Ollama up?
python -m korg_binary_analyzer ai-pull --list-suggested   # model sizes
python -m korg_binary_analyzer ai-pull qwen2.5:7b         # download
python -m korg_binary_analyzer ask USER01.STY "objasni mi ovaj fajl"
python -m korg_binary_analyzer ask USER01.STY "razlike u imenima?" --save odgovor.txt
python -m korg_binary_analyzer ask workspace/reports/<run>/report.json "provjeri integritet"
```

`ask` accepts a saved `report.json` as well as a file, so you can question an
analysis without decoding again.

## Verified results

The suite and the decode engine were run against a real 12-file Pa3X user bank
corpus:

| Check | Result |
|---|---|
| Files decoded | **12 / 12 `DECODED_VALIDATED`** |
| Resources decoded | **914**, all with declared-size + XOR agreement |
| Resources rejected | **0** |
| Style slots recovered | **451** |
| Name-directory agreement | **409 / 451** matched; the 42 mismatches are reported per slot |
| Wall clock | ~14 s for the whole corpus |

The mismatches are genuine findings, not defects: when a style is copied into a
new slot and only the directory entry is renamed, the embedded stream keeps the
old name. The tool shows both and flags the row.

## Design rules the code follows

* **Read-only.** Sources are opened for reading; SHA-256 is compared before and
  after. Nothing is ever written back to the input.
* **No guessing.** A declared-size mismatch or a bad checksum is a rejection.
  The word "validated" is only used when both checks pass.
* **Raw magic scanning is the fallback, not the method.** Searching bytes for
  `OC31` produces false positives because decoded streams contain those literal
  bytes. The index walk runs first; the raw probe only examines what the index
  did not already explain.
* **Disagreement is data.** When two sources of truth differ, both are printed.

## Tests

```bash
python -m pytest                  # 70 tests
python -m korg_binary_analyzer selftest   # 17 end-to-end checks
```

The self-test builds a synthetic container that mirrors the real layout, then
verifies the round trip, the structure walk, the full decoder pipeline and the
refusal of corrupted input — so it passes on a machine with no sample files at
all.

The manual tests are offline too: they assert the shipped catalogue's host
allow-list, English-only policy, depth classification, per-model coverage, model
matching and download-plan filtering without touching a vendor server.

### Regenerating the catalogue

```bash
python tools/scrape_korg_manuals.py    # resolves every Korg product page
python tools/scrape_yamaha_roland.py   # Yamaha downloads + Roland listing pages
python tools/build_catalog.py          # writes data/manuals.json
```

## Requirements

* Python 3.10 or newer; the core has **no third-party dependencies**.
* Tkinter 8.6+ for the desktop interface only.

Windows: `install.bat` will use an existing Python if it has Tk, otherwise it
installs a private runtime into `runtime\python` — no PATH changes, no file
associations, no administrator rights, nothing outside this folder.

## Known limitations

* Writing native Korg files is **not** implemented and is not claimed.
  The encoding path exists only for the self-test (`encode_oc3x`) and is not
  wired to any file output.
* Pa4X-era `lfo-pkg` installers are Blowfish-encrypted; that layer is not
  implemented here.
* The `COMP16`, `LFO-PKG` and `PCM` inner payload formats are not decoded.
* High entropy is reported as an indicator only — it never implies encryption.

## License

Project source: MIT. See `third_party/` for bundled notices.
