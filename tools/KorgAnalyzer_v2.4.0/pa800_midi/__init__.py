"""PA800 MIDI Optimizer / Restorator.

Deterministic, evidence-driven MIDI optimizer for Korg Pa800.

Master rules (hard, see UPUTSTVO_PA800_MIDI.md):

* NOTE-ON TICK and PITCH are immutable.  No quantize, humanize, snap, swing,
  timing shift, transpose or note relocation ever happens by default.
* Key Range is a HARD LIMIT -- a note outside it is reported as
  KEY_RANGE_CONFLICT, never silently repaired.
* Every decision carries evidence and a confidence level.  Only VERIFIED,
  DOCUMENTED and STRONG_EVIDENCE may be applied automatically; CANDIDATE is
  proposed only; UNKNOWN changes nothing.
* The original file is never overwritten.  Output goes to the workspace and
  carries source/output SHA-256 plus the validation result.
"""

__version__ = "1.1.0"

__all__ = ["__version__"]
