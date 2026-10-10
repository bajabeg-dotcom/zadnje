from .base import KorgCandidateDecoder


class Comp16Decoder(KorgCandidateDecoder):
    name = "Korg COMP16 slot"
    term = b"COMP16"


DECODER = Comp16Decoder()