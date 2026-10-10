# Evidence and implementation decisions

This ledger separates verified behavior from descriptive conveniences and unsupported assumptions.

## Official sources

### Korg Pa-series Import/Export Standard MIDI File tutorial

Source: <https://www.korg.com/download/global/paseries/tutorials/ImportExportSMF.pdf>

Applied facts:

- Style parts use MIDI channels 9–16:
  - Bass 9
  - Drum 10
  - Percussion 11
  - Acc 1–5 on 12–16
- Mido exposes those channels as zero-based values 8–15.
- Style chord-variation names use lower-case `EnCVn` forms.
- CC00, CC32, and Program Change identify sounds at the start of a chord variation.
- The documented Pa800 style-import workflow uses SMF type 0.

KorgPaManager can inspect other valid SMF types, but it does not claim they meet every Pa800 style-import constraint.

### Korg Pa800 Advanced Edit manual

Source: <https://cdn.korg.com/us/support/download/files/4ec419fed5c2247621b42ec46ddfc18d.pdf?filename=Pa800_AE_E.pdf&response-content-disposition=inline&response-content-type=application%2Fpdf>

Applied facts:

- A Drum Kit assigns percussion samples by key.
- A key can use velocity-switched layers.
- Layer parameters can include ROM/RAM sample selection, sample offset, reverse, level, transpose, tune, filter, and envelope controls.
- Bank identity is represented by CC00, CC32, and Program values in the 0–127 domain.

Resulting design rule: the GUI displays exact note and bank/program evidence. A GM name is explicitly marked as a reference and never asserted to be the active Korg sample.

### Mido Standard MIDI File documentation

Source: <https://mido.readthedocs.io/en/stable/files/midi.html>

Applied facts:

- `MidiFile` reads, edits, and saves complete Standard MIDI Files.
- Message times inside a track are integer delta ticks.
- Exact absolute ticks therefore require accumulating each track independently.
- SMF type 0 contains exactly one track.

KorgPaManager never uses direct `MidiFile` iteration for exact tick analysis because that iteration is intended for playback timing.

## Supplied Drive evidence

The following files were inspected during development:

- `NEWSONG.MID`
  - SHA-256: `5c15ae3b9bf8f45b0d344976716f86c67fd445e3e121255158b3278e279837c4`
  - Valid SMF type 0
  - One track
  - 384 ticks per quarter note
  - 27,797 decoded messages in the Mido round-trip test
- `midi_io.py`
  - SHA-256: `65523b5f3c3faf857a1a25535d006bc94f683b3a60a8a50f30b941f128ea8905`
  - Confirmed the useful absolute-tick and bank/program extraction approach, but it depended on unrelated project models and was not copied as a module.
- `midi-analyze.json`
  - SHA-256: `50ecd7cf848a1c28d9a390a2ea2d30aa40f1a17d0c1adca4927d4458b91143ef`
  - Supplied analysis evidence for bank/program and Drum/Percussion activity.
- `KORG_PA800_FULL_EDITOR_DATA_v1.zip`
  - SHA-256: `34def580ff38b49386fd5c7b8c686c9ff15606e0c5052b1633d57247bb7fc7f0`
  - Confirmed a 128-key Drum Kit model with variable layers and editable sample/layer parameters.
  - Did not supply a proven universal Korg note-to-element-name table.

## Overlap review

### `MIDI_Enhancer_Pro_v1.0.0.zip`

The package contains Mido-based parsing, generic GM drum names, tempo/groove analysis, and PA800-themed documentation. Its general ideas overlap with MIDI parsing and presentation, but KorgPaManager uses its own event-accurate implementation because:

- Drum identity must come from the event's MIDI channel, not a track-index heuristic.
- Exact ticks must be accumulated per track.
- GM percussion names must remain non-authoritative.
- Edits must modify the loaded in-memory SMF and pass a strict write/reopen comparison.

### `PA_SET_Converter.zip`

The package primarily handles native SET/KORF/OC resource structures and converter forensics. It did not provide a better Standard MIDI marker, channel, note-remap, undo, or atomic-save implementation for this application's scope. Native binary conversion code was therefore not duplicated in KorgPaManager.

### Existing PA800 Manager / pa_tool work

The previously integrated manager project remains the stronger implementation for native SET inventory, evidence tables, catalogs, and read-only forensic views. KorgPaManager does not replace those capabilities or claim a native STY/PCG/SET writer. It implements the missing write-capable Standard MIDI workflow instead.

## TimeSlice decision

Drive full-text search found MIDI/groove-related packages, but no filename explicitly identified a standalone `TimeSLICE` asset, and ordinary MIDI events contain no universal provenance flag proving Time Slice generation.

KorgPaManager therefore:

- reports exact trigger keys, velocities, ticks, intervals, sections, and effective bank/program state;
- provides a neutral trigger-range transform for slice-note offset, timing offset, and velocity scaling;
- preserves note-on and corresponding note-off key changes;
- does not label a sequence “TimeSlice-generated” as a fact unless the user supplies that provenance.

## Cross-model sound decision

Pa4X and PA800 bank/program identities are not assumed equivalent. KorgPaManager exposes and preserves raw CC00, CC32, and Program values. It performs no automatic cross-model sound conversion.

## Save validation decision

The source path is retained as protected provenance. Save As writes a sibling temporary file, reopens it with `clip=False`, validates the structure, compares the complete decoded message stream with memory, and only then uses atomic replacement for the chosen output. This provides a real write path while keeping the original input unchanged.
