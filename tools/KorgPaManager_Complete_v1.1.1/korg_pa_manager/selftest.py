"""Built-in dependency-free runtime test for KorgPaManager.

This is intentionally based on the same public core operations used by the GUI.
It proves that the bundled Mido parser can create/read/write an SMF, that edits
change the in-memory document, that Save As validates a new output, and that the
source remains byte-identical.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import tkinter as tk
from mido import Message, MetaMessage, MidiFile, MidiTrack, version_info

from . import __version__
from .core import MidiDocument


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_self_test(*, require_gui: bool | None = None) -> dict[str, Any]:
    """Run a bounded end-to-end test and return a JSON-serializable report."""

    if require_gui is None:
        require_gui = os.name == "nt"

    report: dict[str, Any] = {
        "application": "KorgPaManager",
        "version": __version__,
        "mido": str(version_info),
        "python_tk": str(tk.TkVersion),
        "checks": [],
    }

    tcl = tk.Tcl()
    report["tcl_patchlevel"] = str(tcl.eval("info patchlevel"))
    report["checks"].append("tkinter import and Tcl interpreter")

    if require_gui:
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        report["checks"].append("Tk desktop window creation")
    else:
        report["checks"].append("Tk desktop window creation skipped in headless mode")

    with tempfile.TemporaryDirectory(prefix="KorgPaManager-selftest-") as folder:
        work = Path(folder)
        source = work / "source.mid"
        output = work / "edited.mid"

        midi = MidiFile(type=0, ticks_per_beat=96)
        track = MidiTrack()
        midi.tracks.append(track)
        track.append(MetaMessage("track_name", name="KPM self-test", time=0))
        track.append(MetaMessage("marker", text="v1cv1", time=0))
        track.append(Message("control_change", channel=9, control=0, value=120, time=0))
        track.append(Message("control_change", channel=9, control=32, value=0, time=0))
        track.append(Message("program_change", channel=9, program=4, time=0))
        track.append(Message("note_on", channel=9, note=36, velocity=100, time=24))
        track.append(Message("note_off", channel=9, note=36, velocity=0, time=24))
        track.append(MetaMessage("end_of_track", time=0))
        midi.save(source)
        source_hash = _sha256(source)

        document = MidiDocument()
        document.load(source)
        assignment = document.program_assignments()[0]
        document.edit_program_assignment(
            assignment.ref,
            program=5,
            bank_msb=120,
            bank_lsb=1,
        )
        changed_notes = document.remap_note(channel=9, source_note=36, target_note=38)
        if changed_notes != 2:
            raise RuntimeError(f"expected two remapped note events, got {changed_notes}")
        save_result = document.save_as(output)

        if _sha256(source) != source_hash:
            raise RuntimeError("self-test source MIDI was modified")
        reopened = MidiFile(output, clip=False)
        messages = [message for track in reopened.tracks for message in track]
        if not any(
            message.type == "program_change"
            and message.channel == 9
            and message.program == 5
            for message in messages
        ):
            raise RuntimeError("edited Program Change was not saved")
        if not any(
            message.type == "note_on"
            and message.channel == 9
            and message.note == 38
            and message.velocity > 0
            for message in messages
        ):
            raise RuntimeError("remapped drum note was not saved")

        report["checks"].extend(
            [
                "SMF type-0 creation and strict load",
                "real Program/Bank in-memory edit",
                "note-on and note-off remap",
                "validated atomic Save As",
                "source SHA-256 preservation",
                "strict reopen of edited output",
            ]
        )
        report["saved_bytes"] = save_result.file_size
        report["source_preserved"] = True

    report["status"] = "PASS"
    return report


def main() -> int:
    try:
        report = run_self_test()
    except Exception as exc:  # diagnostic command must return a useful failure
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
