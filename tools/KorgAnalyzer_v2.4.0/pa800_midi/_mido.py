"""Import mido from the system or from the vendored copy in ``vendor/``.

The optimizer reuses mido -- it is the MIDI parser this material already
shipped with (``_vendor/mido`` in PA_FULL_KONVERTER_V1).  Nothing new is
written for parsing; if a system mido exists it wins, otherwise the vendored
one is used so the tool stays dependency-free.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_VENDOR = Path(__file__).resolve().parent / "vendor"

_ALIAS = "pa800_midi._mido_vendor"


def _load():
    try:
        return importlib.import_module("mido")
    except ImportError:
        pass

    if _ALIAS in sys.modules:
        return sys.modules[_ALIAS]

    if not _VENDOR.is_dir():
        raise ImportError(
            "mido is not installed and the vendored copy is missing "
            f"({_VENDOR}). Install mido or restore pa800_midi/vendor/mido."
        )

    vendor = str(_VENDOR)
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    return importlib.import_module("mido")


mido = _load()

MidiFile = mido.MidiFile
MidiTrack = mido.MidiTrack
Message = mido.Message
MetaMessage = mido.MetaMessage
bpm2tempo = mido.bpm2tempo
merge_tracks = mido.merge_tracks

__all__ = [
    "mido", "MidiFile", "MidiTrack", "Message", "MetaMessage",
    "bpm2tempo", "merge_tracks",
]
