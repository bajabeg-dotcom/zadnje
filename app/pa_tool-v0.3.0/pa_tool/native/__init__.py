"""Evidence-bounded PA800 native resource inspection.

This package integrates the useful, non-inferior logic from
``PA800_Manager_Web_v0.2_NATIVE_SET_FORENSICS``.  It is intentionally read-only:
no API here serializes OC20, STY, PCG, KMP, PCM, SET, or BKP data for an
instrument.  Unknown fields and unsupported geometries remain opaque or
``PRESERVE_ONLY`` rather than being guessed.
"""
from __future__ import annotations

from .codec import FormatError, decode_oc20_file, decode_oc3, encode_oc31_literal
from .format import Bank
from .set_graph import dependency_candidates
from .sounds import load_sound_catalog
from .workspace import UserError, Workspace

__all__ = [
    "Bank",
    "FormatError",
    "decode_oc20_file",
    "decode_oc3",
    "dependency_candidates",
    "encode_oc31_literal",
    "load_sound_catalog",
    "UserError",
    "Workspace",
]
