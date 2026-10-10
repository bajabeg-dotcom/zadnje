from .base import KorgCandidateDecoder


class PcmDecoder(KorgCandidateDecoder):
    name = "Korg PCM slot"
    term = b"PCM"


DECODER = PcmDecoder()