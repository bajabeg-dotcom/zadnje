from .base import KorgCandidateDecoder


class SoundDecoder(KorgCandidateDecoder):
    name = "Korg sound slot"
    term = b"SOUND"


DECODER = SoundDecoder()