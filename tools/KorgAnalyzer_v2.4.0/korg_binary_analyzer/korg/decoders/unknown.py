from .base import KorgCandidateDecoder


class UnknownKorgDecoder(KorgCandidateDecoder):
    name = "Unknown Korg container slot"
    term = b"KORG-UNKNOWN-NO-SIGNATURE"


DECODER = UnknownKorgDecoder()