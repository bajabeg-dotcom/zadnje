from .base import KorgCandidateDecoder


class LfoPackageDecoder(KorgCandidateDecoder):
    name = "Korg LFO-PKG slot"
    term = b"LFO-PKG"


DECODER = LfoPackageDecoder()