"""Recognition helpers for Korg style marker text."""

from __future__ import annotations

import re
from typing import Optional

# Meta-event classes that can carry marker-like textual information in Mido.
TEXT_META_TYPES = frozenset(
    {
        "text",
        "copyright",
        "track_name",
        "instrument_name",
        "lyrics",
        "marker",
        "cue_marker",
        "device_name",
    }
)

# Korg documentation expresses the grammar as lower-case EnCVn.  Accepting
# either case while retaining the original text lets the program inspect files
# made by other sequencers without rewriting their spelling.
_KORG_CODE = re.compile(
    r"^(?P<prefix>[vife])(?P<element>[1-9])cv(?P<cv>[1-9])$", re.IGNORECASE
)
_LONG_NAME = re.compile(
    r"^(?P<family>variation|var|intro|ending|end|fill)\s*[-_: ]?\s*"
    r"(?P<element>[1-9])(?:\s*[-_: ]?\s*cv\s*(?P<cv>[1-9]))?$",
    re.IGNORECASE,
)
_PREFIX_FAMILY = {
    "v": "Variation",
    "i": "Intro",
    "f": "Fill",
    "e": "Ending",
}
_LONG_FAMILY = {
    "variation": "Variation",
    "var": "Variation",
    "intro": "Intro",
    "ending": "Ending",
    "end": "Ending",
    "fill": "Fill",
}


def text_from_meta(message: object) -> str:
    """Extract text from a Mido textual MetaMessage without guessing."""

    for attribute in ("text", "name"):
        value = getattr(message, attribute, None)
        if isinstance(value, str):
            return value
    return ""


def classify_marker(text: str) -> tuple[str, Optional[int], Optional[int], bool]:
    """Classify known Korg and human-readable section marker names.

    Returns ``(family, element_number, chord_variation, recognized)``.
    The input text is never normalized in storage; normalization is only used
    to recognize its meaning.
    """

    normalized = text.strip()
    match = _KORG_CODE.fullmatch(normalized)
    if match:
        prefix = match.group("prefix").lower()
        element = int(match.group("element"))
        cv = int(match.group("cv"))
        # Variations on Pa-series style MIDI are explicitly 1..4.  Retain a
        # syntactically similar out-of-range marker, but do not call it proven.
        valid = not (prefix == "v" and element not in range(1, 5))
        if valid:
            return _PREFIX_FAMILY[prefix], element, cv, True

    match = _LONG_NAME.fullmatch(normalized)
    if match:
        family = _LONG_FAMILY[match.group("family").lower()]
        element = int(match.group("element"))
        cv_text = match.group("cv")
        valid = not (family == "Variation" and element not in range(1, 5))
        if valid:
            return family, element, int(cv_text) if cv_text else None, True

    return "Other", None, None, False


def is_marker_like(meta_type: str, text: str) -> bool:
    """Return whether a textual event belongs in the section-marker view."""

    if meta_type in {"marker", "cue_marker"}:
        return True
    return classify_marker(text)[3]
