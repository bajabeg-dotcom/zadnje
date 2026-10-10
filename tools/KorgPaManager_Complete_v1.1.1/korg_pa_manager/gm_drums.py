"""MIDI note naming and optional General MIDI percussion reference labels.

The GM labels in this module are descriptive references only.  A Korg Pa800
Drum Kit can assign a different sample, and potentially multiple velocity
layers, to each key.  KorgPaManager therefore never presents these labels as
the actual contents of a selected Korg kit.
"""

from __future__ import annotations

GM_PERCUSSION_REFERENCE: dict[int, str] = {
    35: "Acoustic Bass Drum",
    36: "Bass Drum 1",
    37: "Side Stick",
    38: "Acoustic Snare",
    39: "Hand Clap",
    40: "Electric Snare",
    41: "Low Floor Tom",
    42: "Closed Hi-Hat",
    43: "High Floor Tom",
    44: "Pedal Hi-Hat",
    45: "Low Tom",
    46: "Open Hi-Hat",
    47: "Low-Mid Tom",
    48: "Hi-Mid Tom",
    49: "Crash Cymbal 1",
    50: "High Tom",
    51: "Ride Cymbal 1",
    52: "Chinese Cymbal",
    53: "Ride Bell",
    54: "Tambourine",
    55: "Splash Cymbal",
    56: "Cowbell",
    57: "Crash Cymbal 2",
    58: "Vibraslap",
    59: "Ride Cymbal 2",
    60: "Hi Bongo",
    61: "Low Bongo",
    62: "Mute Hi Conga",
    63: "Open Hi Conga",
    64: "Low Conga",
    65: "High Timbale",
    66: "Low Timbale",
    67: "High Agogo",
    68: "Low Agogo",
    69: "Cabasa",
    70: "Maracas",
    71: "Short Whistle",
    72: "Long Whistle",
    73: "Short Guiro",
    74: "Long Guiro",
    75: "Claves",
    76: "Hi Wood Block",
    77: "Low Wood Block",
    78: "Mute Cuica",
    79: "Open Cuica",
    80: "Mute Triangle",
    81: "Open Triangle",
}

_PITCHES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def midi_note_name(note: int) -> str:
    """Return scientific pitch notation using MIDI 60 == C4."""

    if not 0 <= note <= 127:
        raise ValueError("MIDI note must be in the range 0..127")
    octave = note // 12 - 1
    return f"{_PITCHES[note % 12]}{octave}"


def gm_reference_label(note: int) -> str:
    """Return a GM percussion reference label, or an empty string."""

    return GM_PERCUSSION_REFERENCE.get(note, "")
