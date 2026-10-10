"""pa_tool — own PA Manager workbench for KORG PA formats.

Read-only on all inputs. Every finding carries a status (VERIFIED / NEPOTVRDENO /
UNKNOWN) and, where measurable, an offset as evidence.
"""
from __future__ import annotations

__version__ = "0.3.0"

from .status import Finding, Result, Status, worst  # noqa: F401

__all__ = ["Finding", "Result", "Status", "worst", "__version__"]
