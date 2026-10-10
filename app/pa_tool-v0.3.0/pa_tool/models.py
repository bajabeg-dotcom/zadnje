"""Data layer: loads data/*.json and exposes typed accessors.

Every record in the JSON carries value + status + evidence. Nothing here invents a
value; where the RE findings say UNKNOWN, this layer keeps it UNKNOWN.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from .status import Status

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(name: str) -> dict:
    path = DATA_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"data file missing: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=None)
def models_data() -> dict:
    return _load("models.json")


@lru_cache(maxsize=None)
def tokens_data() -> dict:
    return _load("korg_tokens.json")


@lru_cache(maxsize=None)
def rejections_data() -> dict:
    return _load("rejections.json")


@lru_cache(maxsize=None)
def conversions_data() -> dict:
    return _load("conversions.json")


@lru_cache(maxsize=None)
def pcg_chunks_data() -> dict:
    return _load("pcg_chunks.json")


@dataclass(frozen=True)
class Model:
    ordinal: int
    name: str
    case: str
    table_a: str
    table_b: str

    @property
    def key(self) -> str:
        return self.name.upper()


def canonical_models() -> list[Model]:
    out = []
    for entry in models_data()["canonical_order"]["models"]:
        out.append(Model(
            ordinal=entry["ordinal"],
            name=entry["name"],
            case=entry.get("case", ""),
            table_a=entry.get("table_a", ""),
            table_b=entry.get("table_b", ""),
        ))
    return out


def model_by_name(name: str) -> Optional[Model]:
    if not name:
        return None
    want = name.strip().upper().replace(" ", "")
    for m in canonical_models():
        if m.key == want:
            return m
    return None


def ram_table() -> list[dict]:
    return models_data()["ram"]["entries"]


def ram_for(model: str) -> dict:
    """Return the documented RAM for a model.

    A model may appear in more than one RAM row. Two cases exist and they are NOT
    the same thing:

      * PA80 has both 8 MB and 16 MB as BASE entries. Which one applies is not
        documented, so the answer is reported as NEPOTVRDENO with both candidates
        instead of silently picking one.
      * Pa4X has 400 MB plus a 1536 MB entry that belongs to the 'OS Next'
        variant, so the base entry is the right answer and the variant is kept
        alongside it.
    """
    want = (model or "").strip().upper()
    base: list[int] = []
    variant: list[tuple[int, str]] = []
    for entry in ram_table():
        if any(m.upper() == want for m in entry.get("models", [])):
            if entry.get("variant"):
                variant.append((entry["size_mb"], entry["variant"]))
            else:
                base.append(entry["size_mb"])

    if not base and not variant:
        return {
            "size_mb": None,
            "candidates": [],
            "variants": [],
            "status": Status.UNKNOWN.value,
            "evidence": "models.json ram.entries — model not present in the RAM table",
            "ambiguous": False,
        }

    if len(set(base)) > 1:
        # two documented base sizes: reporting one of them would be a guess
        return {
            "size_mb": None,
            "candidates": sorted(set(base)),
            "variants": sorted(variant),
            "status": Status.NEPOTVRDENO.value,
            "evidence": "models.json ram.entries — two base sizes for this model",
            "ambiguous": True,
        }

    primary = base[0] if base else min(v[0] for v in variant)
    return {
        "size_mb": primary,
        "candidates": sorted(set(base)) or sorted({v[0] for v in variant}),
        "variants": sorted(variant),
        "status": Status.VERIFIED.value,
        "evidence": "models.json ram.entries @ 0x0024DFDC-0x0024E1B4 (PA_MODEL_REQUIREMENTS.md §2.1)",
        "ambiguous": False,
    }


def import_layer() -> list[dict]:
    return models_data()["import_layer"]["entries"]


def sus_for(model: str) -> Optional[str]:
    """OpenSound SUB offset for a model, or None when the model has no own SUB."""
    want = (model or "").strip().upper()
    for entry in import_layer():
        if any(m.upper() == want for m in entry["models"]):
            return entry.get("sub")
    return None


def osc_limits() -> dict:
    return models_data()["osc_limit"]


def variants() -> list[dict]:
    return models_data()["variants"]


def variant_by_name(name: str) -> Optional[dict]:
    want = (name or "").strip().upper().replace(" ", "")
    for entry in variants():
        if entry["name"].upper().replace(" ", "") == want:
            return entry
    return None


def hierarchy() -> dict:
    return models_data()["hierarchy"]


def pcg_chunks() -> dict:
    return pcg_chunks_data()


def container_magic() -> bytes:
    hexstr = tokens_data()["container_magic"]["value_hex"].replace(" ", "")
    return bytes.fromhex(hexstr)


def resource_tokens() -> list[bytes]:
    return [t["token"].encode("ascii") for t in tokens_data()["resource_tokens"]]


def hard_limits() -> list[dict]:
    return rejections_data()["hard_limits"]["entries"]


def limit_for(what: str) -> Optional[int]:
    for entry in hard_limits():
        if entry["what"] == what:
            return entry["limit"]
    return None


def canonical_sample_format() -> dict:
    return rejections_data()["canonical_sample_format"]


def rejections() -> dict:
    return rejections_data()["rejections"]


def extensions_known() -> dict:
    return rejections_data()["extensions_known"]


def conversions() -> dict:
    return conversions_data()


def key_finding() -> dict:
    return conversions_data()["_meta"]["key_finding"]


def directions() -> list[dict]:
    return conversions_data()["directions"]


def metadir_schema() -> dict:
    return tokens_data()["metadir_schema"]


def set_structure() -> dict:
    return tokens_data()["set_structure"]


def kst_identity() -> dict:
    return tokens_data()["kst_identity"]


def reload_all() -> None:
    """Drop caches (used by tests)."""
    models_data.cache_clear()
    tokens_data.cache_clear()
    rejections_data.cache_clear()
    conversions_data.cache_clear()
    pcg_chunks_data.cache_clear()
