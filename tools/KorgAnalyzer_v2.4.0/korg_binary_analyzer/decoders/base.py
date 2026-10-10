from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class Decoder(ABC):
    name = "unnamed"
    supported_inputs: tuple[str, ...] = ()
    enabled = True
    executable = True

    @abstractmethod
    def detect(self, data: Any) -> dict[str, Any]:
        """Return matched, confidence, and evidence without modifying input."""

    @abstractmethod
    def decode(self, source: Path, output_root: Path, max_output_bytes: int) -> dict[str, Any]:
        """Decode to a new output path; implementations must never write source."""

    @abstractmethod
    def validate(self, result: dict[str, Any]) -> dict[str, Any]:
        """Return validation status and evidence for generated outputs."""

    def describe(self) -> str:
        return self.name

    def confidence(self, probe: dict[str, Any]) -> float:
        return float(probe.get("confidence", 0.0))