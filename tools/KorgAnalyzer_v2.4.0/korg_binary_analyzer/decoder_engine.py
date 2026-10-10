from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path
from typing import Any

from .decoders.base import Decoder
from .decoders.generic import Bzip2Decoder, GzipDecoder, XzDecoder, ZipDecoder

MAX_DECODED_OUTPUT = 512 * 1024 * 1024


class DecoderRegistry:
    def __init__(self, include_korg: bool = True):
        self.decoders: list[Decoder] = [ZipDecoder(), GzipDecoder(), Bzip2Decoder(), XzDecoder()]
        if include_korg:
            package_name = "korg_binary_analyzer.korg.decoders"
            package = importlib.import_module(package_name)
            for module_info in pkgutil.iter_modules(package.__path__):
                module = importlib.import_module(f"{package_name}.{module_info.name}")
                decoder = getattr(module, "DECODER", None)
                if isinstance(decoder, Decoder):
                    self.decoders.append(decoder)

    def probe(self, data: Any) -> list[dict[str, Any]]:
        results = []
        for decoder in self.decoders:
            if not decoder.enabled:
                continue
            try:
                probe = decoder.detect(data)
            except Exception as error:
                probe = {"matched": False, "confidence": 0.0, "evidence": [f"probe error: {type(error).__name__}: {error}"]}
            if probe.get("matched"):
                results.append({
                    "name": decoder.name, "supported_inputs": list(decoder.supported_inputs),
                    "confidence": decoder.confidence(probe), "evidence": probe.get("evidence", []),
                    "description": decoder.describe(),
                    "implementation": "SUPPORTED" if decoder.executable else "PLACEHOLDER_NOT_IMPLEMENTED",
                    "status": "CANDIDATE_ONLY" if not decoder.executable else "NOT_ATTEMPTED",
                })
        for magic, name in ((b"7z\xbc\xaf\x27\x1c", "7z"), (b"Rar!\x1a\x07", "RAR"), (b"\x28\xb5\x2f\xfd", "Zstandard"), (b"\x04\x22\x4d\x18", "LZ4")):
            if bytes(data[:len(magic)]) == magic:
                results.append({"name": name, "supported_inputs": [name.upper()], "confidence": 0.99, "evidence": ["recognized signature; no built-in decoder available"], "implementation": "NOT_SUPPORTED", "status": "NOT_SUPPORTED"})
        return sorted(results, key=lambda item: (-item["confidence"], item["name"]))

    def run(self, source: Path, workspace: Path, data: Any, only: set[str] | None = None) -> list[dict[str, Any]]:
        probes = self.probe(data)
        lookup = {decoder.name: decoder for decoder in self.decoders}
        executable = [item for item in probes if item["implementation"] == "SUPPORTED" and (only is None or item["name"] in only)]
        if not executable:
            if probes:
                return [{**item, "status": "CANDIDATE_ONLY" if item["implementation"] == "PLACEHOLDER_NOT_IMPLEMENTED" else item["status"], "reason": "No implemented decoder justified execution"} for item in probes]
            return [{"decoder": None, "status": "NOT_SUPPORTED", "reason": "No supported decoder matched the input"}]
        selected = executable[0]
        decoder = lookup[selected["name"]]
        output_root = workspace / ("extracted" if decoder.name == "zip" else "decoded")
        result = decoder.decode(source, output_root, MAX_DECODED_OUTPUT)
        result["probe"] = selected
        if result.get("status") == "DECODED_VALIDATED":
            result["validation"] = decoder.validate(result)
            if result["validation"]["status"] != "VALIDATED":
                result["status"] = "DECODED_UNVERIFIED"
        return [result]