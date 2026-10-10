# SPDX-FileCopyrightText: 2016 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT
"""Version metadata for the vendored Mido 1.3.3 runtime.

The upstream module uses the external ``packaging`` project only to expose a
PEP 440 version object.  KorgPaManager vendors Mido for offline MIDI file I/O,
so this tiny compatible value object avoids adding another runtime dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

__version__ = "1.3.3"


@dataclass(frozen=True, order=True)
class VendoredVersion:
    """Small immutable version value with the attributes used by clients."""

    major: int
    minor: int
    micro: int

    @property
    def release(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.micro)

    @property
    def is_prerelease(self) -> bool:
        return False

    @property
    def is_devrelease(self) -> bool:
        return False

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.micro}"


version_info = VendoredVersion(1, 3, 3)
