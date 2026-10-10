# KorgPaManager 1.1.1 Complete Offline test report

Date: 2026-10-07

## Automated regression suite

Command:

```bash
python -m pytest -q
```

Result: **15 passed**.

Covered behavior:

- Korg marker grammar and exact absolute ticks
- official style-channel mapping
- effective CC00/CC32/Program extraction
- real in-memory Program Change and bank edits
- undo and redo
- section-aware Drum/Percussion activity
- note-on/note-off remapping with tick preservation
- TimeSlice/trigger-range note, tick, and velocity transforms
- marker add/edit/delete timing reconstruction
- full-channel swapping
- source-protected, validated Save As
- custom-label sidecars
- JSON and CSV reports
- vendored Mido/application versions
- generated demo SMF
- exact offline Python installer SHA-256
- launcher safety options and complete license/notices inventory
- Windows-compatible writable temporary-file handle for the `fsync` durability step

## Windows Errno 9 regression

The 1.1.0 Windows self-test exposed `[Errno 9] Bad file descriptor` because the
validation path reopened the completed temporary MIDI as read-only (`rb`) and
then called `os.fsync()`. Windows requires a writable descriptor for this
operation. Version 1.1.1 reopens the file as `r+b`, performs the durability
flush, then strictly reparses and compares the MIDI event stream as before.
The reopen does not modify bytes. A regression test simulates Windows rejecting
`fsync` on a non-writable descriptor and fails if `rb` is reintroduced.

## Built-in runtime self-test

Command:

```bash
python -m korg_pa_manager --self-test
```

Result: **PASS**.

The self-test created an SMF type 0 document, loaded it strictly, changed a real
CC00/CC32/Program assignment in memory, remapped matching Drum note-on/note-off
events, wrote through the atomic validated Save As path, reopened the result,
and confirmed that the source SHA-256 did not change.

Tkinter 8.6 and Tcl 8.6.16 imported successfully. GUI window creation is
required by this same self-test when it runs on Windows; it was skipped in the
headless Linux build environment because no display server is present.

## Clean installation check

The project was installed into an empty target directory with:

```bash
python -m pip install --no-deps --no-build-isolation --target <empty> .
```

Result: **PASS**.

The clean target contained both `korg_pa_manager` and bundled `mido`. From that
isolated target, `python -m korg_pa_manager --version` returned 1.1.1 and the
built-in self-test passed without downloading a runtime dependency.

## Complete offline Windows payload

Bundled official file:

```text
runtime_installer/python-3.12.10-amd64.exe
```

- Source: `https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe`
- SHA-256: `67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb`
- MD5: `5eddb0b6f12c852725de071ae681dde4` (matches the Python release page)
- Size: 26,964,224 bytes
- Installer manifest contains the embedded CPython core, standard library,
  executable and Tcl/Tk payloads used by the launcher's offline setup.

`KorgPaManager.bat` requests a private per-user runtime without administrator
rights, PATH changes, file associations, global launcher, pip, documentation,
or test suite. It explicitly includes Tcl/Tk and runs the built-in self-test
before opening the GUI.

## Supplied real-file validation retained from 1.0.0

Input: `NEWSONG.MID`

- Source SHA-256: `5c15ae3b9bf8f45b0d344976716f86c67fd445e3e121255158b3278e279837c4`
- SMF type: 0
- Tracks: 1
- Ticks per quarter note: 384
- Decoded messages: 27,797
- Style-channel Program Change events found: 8
- Active Drum/Percussion keys found: 36

The source was loaded, one in-memory Program Change was changed from 34 to 35,
a separate output was written and reopened with 27,797 messages, and the source
SHA-256 remained unchanged. The temporary output was removed.
