"""KorgPaManager package."""

__version__ = "1.1.1"

from .core import DRUM_CHANNELS, STYLE_CHANNELS, MidiDocument, MidiDocumentError

__all__ = [
    "DRUM_CHANNELS",
    "STYLE_CHANNELS",
    "MidiDocument",
    "MidiDocumentError",
    "__version__",
]
