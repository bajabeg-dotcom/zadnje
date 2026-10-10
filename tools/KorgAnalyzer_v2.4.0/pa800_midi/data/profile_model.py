"""InstrumentProfile -- copied from the existing midi_optimizer
(core/models.py) so the data tables can be reused without pulling in that
package's GUI-oriented core module.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict


@dataclass
class InstrumentProfile:
    name: str
    category: str
    cc00: int
    cc32: int
    program: int

    preferred_volume: int = 100
    preferred_pan: int = 64
    preferred_reverb: int = 24
    preferred_chorus: int = 0

    velocity_base: int = 80
    velocity_min: int = 55
    velocity_max: int = 110
    velocity_rand: int = 10

    recommended_fx: Dict[str, Any] = field(default_factory=dict)
    live_bias: Dict[str, Any] = field(default_factory=dict)
    studio_bias: Dict[str, Any] = field(default_factory=dict)

    playable_range_low: int = 0
    playable_range_high: int = 127

    def to_dict(self):
        return asdict(self)

    @property
    def key(self):
        return (self.cc00, self.cc32, self.program)
