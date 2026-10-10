# KorgPaManager 1.1.1 Complete Offline

KorgPaManager is a complete, write-capable Python/Tkinter application for inspecting and editing **Standard MIDI Files** used in Korg Pa style workflows. It uses the bundled, MIT-licensed Mido 1.3.3 source for SMF parsing and writing, so no Python package download is required.

The Windows 64-bit release includes the official CPython 3.12.10 offline installer with Tcl/Tk. Double-click `KorgPaManager.bat`; if no usable Python/Tk runtime exists, the launcher prepares a private runtime in `runtime/python`, runs an end-to-end self-test, and opens the GUI. It does not change PATH, register `.py` associations, or require administrator privileges.

Version 1.1.1 fixes the Windows `[Errno 9] Bad file descriptor` startup/self-test failure from 1.1.0. The temporary MIDI durability flush now uses a Windows-compatible writable handle before strict reopen and semantic comparison.

The program edits a MIDI document in memory and saves a separately named, validated copy. It does **not** write native Korg STY, PCG, or SET binary files.

## Main capabilities

- Loads SMF type 0, 1, or 2 files with contextual error handling.
- Accumulates each track's delta times to retain exact absolute tick positions.
- Finds Korg section names in textual meta events, including lower-case forms such as:
  - `v1cv1` through Variation 4 forms
  - `i1cv1` for Intros
  - `f1cv1` for Fills
  - `e1cv1` for Endings
- Retains and displays unknown `marker` and `cue_marker` text rather than silently discarding it.
- Adds, edits, moves, and deletes marker events.
- Reports the official Korg style channel layout:

  | MIDI channel | Mido channel | Role |
  |---:|---:|---|
  | 9 | 8 | Bass |
  | 10 | 9 | Drums |
  | 11 | 10 | Percussion |
  | 12 | 11 | Acc 1 |
  | 13 | 12 | Acc 2 |
  | 14 | 13 | Acc 3 |
  | 15 | 14 | Acc 4 |
  | 16 | 15 | Acc 5 |

- Moves or swaps complete style channels while preserving event ticks.
- Reports and edits exact `CC00.CC32.Program` assignments using raw MIDI values from 0 through 127.
- Adds or deletes Program Change assignments.
- Shows Drum and Percussion activity by channel and key, including:
  - MIDI note number and scientific note name
  - optional General MIDI percussion reference label
  - custom user label
  - effective CC00, CC32, and Program values
  - hit count and velocity range
  - first and last absolute tick
  - active section marker
  - every individual note-on tick, track, message position, velocity, and bank/program state
- Remaps note-on and matching note-off keys, plus poly-pressure keys, without altering event ticks.
- Provides a TimeSlice/trigger range tool for note offset, absolute tick offset, and velocity scaling.
- Provides full-document undo and redo.
- Exports JSON analysis and Drum/Percussion CSV reports.
- Saves optional custom labels in an adjacent `.kpm-labels.json` sidecar.

## Safety model

KorgPaManager never performs an implicit Save over the loaded source.

`Save edited copy as…` performs these steps:

1. Refuses the original resolved source path and same-file aliases.
2. Writes the in-memory MIDI to a temporary file in the selected output directory.
3. Reopens that temporary file with Mido using strict, non-clipping parsing.
4. Validates SMF type, track structure, ticks-per-quarter, and all delta times.
5. Compares the decoded event stream with the intended in-memory event stream.
6. Atomically installs the validated output with `os.replace`.

If any write or validation step fails, the temporary file is removed and no validated output is installed.

## Evidence discipline and limitations

- Bank and program values are shown exactly as MIDI data. KorgPaManager does not invent Pa4X-to-PA800 sound mappings.
- General MIDI percussion names are marked **reference only**. A Pa800 Drum Kit can assign a different ROM or RAM sample, and potentially multiple velocity-switched layers, to each key.
- The actual sound therefore depends on the selected kit, note, velocity, and the keyboard's loaded resources.
- A note pattern can be edited as possible TimeSlice/slice-trigger data, but a Standard MIDI File alone does not prove that Korg Time Slice generated it.
- The TimeSlice tool never alters audio samples; it edits MIDI trigger notes, their ticks, and velocities.
- Native Korg binary writing is outside this application's claims.

See [`docs/EVIDENCE.md`](docs/EVIDENCE.md) for source and overlap-review details.

## Requirements

### Complete Windows release

- 64-bit Windows 8.1, 10, or 11
- no preinstalled Python package and no internet connection required
- enough write access to the extracted application folder for the one-time private runtime setup

The bundled runtime installer is the official, Authenticode-signed CPython 3.12.10 64-bit offline installer from python.org. Its exact SHA-256 and license are recorded in `third_party/NOTICE.md`.

### Source launch on Linux or macOS

- Python 3.10 or later
- Tkinter 8.6 or later

Mido 1.3.3 is included in the source tree. On Debian/Ubuntu Linux, Tkinter may require the system package `python3-tk`.

## Install and run

### Windows — normal user workflow

1. Extract the entire ZIP to Desktop, Documents, or another writable folder.
2. Double-click `KorgPaManager.bat`.
3. Wait for the one-time offline Python/Tk setup if needed.
4. The launcher runs `--self-test` and then opens the GUI.

`OPEN_DEMO.bat` opens the bundled 152-byte generated demo SMF. `CHECK_KorgPaManager.bat` installs/checks the runtime and shows the complete self-test result without opening the editor. Diagnostic files are kept in `logs/` and never mixed with the user's MIDI files.

You can also pass a MIDI file from Command Prompt:

```bat
KorgPaManager.bat "C:\path\to\style.mid"
```

### Linux or macOS

```bash
cd KorgPaManager
chmod +x run.sh
./run.sh
```

Or use the source entry point directly:

```bash
python3 -m korg_pa_manager --self-test
python3 run_korg_pa_manager.py
```

Optional editable installation remains available and has no PyPI runtime dependency:

```bash
python -m pip install -e .
korg-pa-manager
```

## Typical workflow

1. Choose **Open MIDI**.
2. Review the **Overview** and **Style Channels** tabs.
3. Check **Markers / Sections** for exact section ticks and spelling.
4. Review **Bank / Program** for exact CC00, CC32, and Program values.
5. In **Drum, Percussion & TimeSlice**, select a key to inspect every trigger event.
6. Use **Remap selected note** for one key, or **TimeSlice range transform** for a controlled batch edit.
7. Add verified custom labels if you know the selected kit's actual element names.
8. Use **Save edited copy as…** and choose a new filename.
9. Optionally export a JSON evidence report or CSV note table.

All edit operations remain in memory until Save As. Undo and redo are available from the toolbar, menu, and keyboard shortcuts.

## Project layout

```text
KorgPaManager/
├── KorgPaManager.bat    # one-click Windows launcher + runtime verification
├── OPEN_DEMO.bat        # opens the generated demo MIDI
├── START_HERE.txt       # short end-user instructions
├── runtime_installer/
│   └── python-3.12.10-amd64.exe  # official offline CPython/Tk installer
├── mido/                # bundled Mido 1.3.3 source
├── korg_pa_manager/
│   ├── __init__.py
│   ├── __main__.py
│   ├── core.py          # MIDI editing, analysis, undo, and atomic Save As
│   ├── gm_drums.py      # optional GM reference labels
│   ├── gui.py           # complete Tkinter interface
│   ├── markers.py       # Korg marker recognition
│   ├── models.py        # typed result models
│   └── selftest.py      # end-to-end offline runtime test
├── examples/
│   └── KorgPaManager_Demo_Style.mid
├── third_party/         # Mido and Python notices/licenses
├── docs/EVIDENCE.md
├── tests/
├── LICENSE
├── pyproject.toml
└── run_korg_pa_manager.py
```

## Tests

The runtime self-test needs no test framework:

```bash
python -m korg_pa_manager --self-test
```

The developer regression suite can additionally be run with Pytest:

```bash
python -m pip install pytest
python -m pytest
```

The checks cover Tk/Tcl availability, SMF creation and strict reopen, marker ticks and grammar, style-channel mapping, bank/program editing, note activity and section association, note remapping, TimeSlice-range transforms, channel swaps, undo/redo, custom-label sidecars, report export, source preservation, validated Save As, bundled runtime provenance, and offline distribution completeness.

A real-world test was also completed with the supplied `NEWSONG.MID`: SMF type 0, one track, 384 ticks per quarter note, 27,797 decoded messages. KorgPaManager parsed it and wrote a validated semantic round-trip copy without touching the source.

## License

KorgPaManager source: MIT, exactly as stated in [`LICENSE`](LICENSE).
Bundled Mido and CPython notices/licenses are preserved in [`third_party`](third_party/NOTICE.md).
