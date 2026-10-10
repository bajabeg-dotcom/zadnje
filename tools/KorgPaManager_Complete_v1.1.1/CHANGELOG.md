# Changelog

## 1.1.1 — 2026-10-07

- Fixed Windows `Save As`/startup self-test failure: `os.fsync()` was called on a read-only temporary-file descriptor, which Windows reports as `[Errno 9] Bad file descriptor`.
- The completed temporary MIDI is now reopened as `r+b` for the durability flush; no MIDI bytes are modified by that reopen.
- Added a cross-platform regression test that requires the validation flush to use the Windows-compatible writable handle.
- Full regression suite now contains 15 passing tests.

## 1.1.0 — 2026-10-07

- Added one-click `KorgPaManager.bat` Windows launcher.
- Bundled the official CPython 3.12.10 64-bit offline installer with Tcl/Tk for a private first-run runtime; no admin privileges, PATH changes, or internet are required.
- Vendored complete Mido 1.3.3 source under its MIT license, removing the PyPI runtime dependency.
- Added built-in `--self-test` covering Tk/Tcl, real MIDI creation/editing, atomic validated Save As, strict reopen, and source-hash preservation.
- Added generated demo MIDI and `OPEN_DEMO.bat`.
- Added complete third-party provenance, exact hashes, and license notices.
- Kept the full application source, tests, evidence documentation, and exact project MIT license in the release.

## 1.0.0 — 2026-10-06

- Added complete Tkinter desktop application and modular Mido core.
- Added exact per-track absolute-tick marker scanning and Korg EnCVn recognition.
- Added official MIDI 9–16 / Mido 8–15 style-channel reporting and editing.
- Added raw CC00, CC32, and Program assignment display and editing.
- Added Drum/Percussion activity, effective kit-bank display, custom labels, and exact hit detail.
- Added note remapping and TimeSlice/trigger range transforms.
- Added full-document undo/redo, JSON/CSV exports, and atomic validated Save As.
- Added unit/integration tests and successful real-file round trip for supplied `NEWSONG.MID`.
