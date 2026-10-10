"""Pa Manager resource model.

Everything here comes from the static analysis of the real KORG PA Manager binary
(2.exe), imported into data/manager_resources.json and data/factory_names.json.

The factory name table is parsed **by structure**, not by a printable-ASCII regex:
the earlier regex sweep produced 1329 entries of which 50 were junk, because names
were sliced at arbitrary byte boundaries. The measured record layout fixes that.

Read-only: this module never writes to a resource file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .status import Status

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(name: str) -> dict:
    path = DATA_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"data file missing: {path} (run tools_import_manager.py)")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=None)
def resources_data() -> dict:
    return _load("manager_resources.json")


@lru_cache(maxsize=None)
def factory_names_data() -> dict:
    return _load("factory_names.json")


@dataclass
class Resource:
    index: int
    type: str
    name: str
    size: int
    rva: str
    file_offset: str
    source: str          # "file" or "image"
    sha256: str
    file: str

    @property
    def was_recovered(self) -> bool:
        """True when the payload only existed in the memory image."""
        return self.source == "image"


def resources() -> list[Resource]:
    out = []
    for r in resources_data()["resources"]:
        out.append(Resource(
            index=r["index"],
            type=r["type"],
            name=r["name"],
            size=r["size"],
            rva=r["data_rva"],
            file_offset=r["data_file_offset"],
            source=r["payload_source"],
            sha256=r["sha256"],
            file=r["file"],
        ))
    return out


def resource(name: str) -> Optional[Resource]:
    want = (name or "").strip()
    for r in resources():
        if r.name == want or r.name.upper() == want.upper():
            return r
    return None


def packer_info() -> dict:
    return resources_data()["_meta"]["packer"]


def mapping_summary() -> dict:
    return resources_data()["_meta"]["resource_mapping"]


def how_recovered() -> dict:
    return resources_data()["_meta"]["how_recovered"]


def source_binary() -> dict:
    return resources_data()["_meta"]["source_binary"]


# --------------------------------------------------------------- factory names

def program_table() -> dict:
    return factory_names_data().get("programs", {})


def factory_programs() -> list[dict]:
    return program_table().get("entries", [])


def program_sections() -> list[dict]:
    return program_table().get("sections", [])


def drum_names() -> dict:
    return factory_names_data().get("drum_kits_and_multisamples", {})


def drum_entries() -> list[dict]:
    return drum_names().get("entries", [])


def drum_sample_names() -> list[dict]:
    return drum_names().get("sample_names", [])


def oc_decode_info() -> dict:
    return factory_names_data().get("oc_decode", {})


def drum_decode_detail() -> dict:
    return drum_names().get("decode", {}) or {}


def drum_table_layout() -> dict:
    t = drum_names()
    return {
        "record_size": t.get("record_size"),
        "name_field_size": t.get("name_field_size"),
        "table_start": t.get("table_start"),
        "table_end": t.get("table_end"),
        "records": t.get("records"),
        "distinct_names": t.get("distinct_names"),
        "status": t.get("status"),
        "evidence": t.get("evidence"),
    }


def find_drum_name(fragment: str) -> list[dict]:
    want = (fragment or "").strip().lower()
    if not want:
        return []
    out = [e for e in drum_entries() if want in e["name"].lower()]
    if not out:
        out = [e for e in drum_sample_names() if want in e["text"].lower()]
    return out


def drum_names_status() -> Status:
    s = drum_names().get("status", "UNKNOWN")
    return Status(s) if s in Status.__members__ else Status.UNKNOWN


def container_templates() -> list[dict]:
    return factory_names_data().get("container_templates", {}).get("entries", [])


def embedded_modules() -> list[dict]:
    return factory_names_data().get("embedded_modules", {}).get("entries", [])


def find_factory_name(fragment: str) -> list[dict]:
    """Case-insensitive substring search across the factory program names."""
    want = (fragment or "").strip().lower()
    if not want:
        return []
    return [e for e in factory_programs() if want in e["name"].lower()]


def name_table_layout() -> dict:
    """The measured record layout, for reporting and for tests."""
    t = program_table()
    return {
        "record_size": t.get("record_size"),
        "name_field_size": t.get("name_field_size"),
        "table_start": t.get("table_start"),
        "status": t.get("status"),
        "evidence": t.get("evidence"),
    }


def name_table_status() -> Status:
    t = program_table()
    s = t.get("status", "UNKNOWN")
    return Status(s) if s in Status.__members__ else Status.UNKNOWN


def resource_capacity_check(total_bytes: int) -> dict:
    """Sanity check against the documented per-bank limit.

    The limit of 300 files per bank and 144 folders per SET is documented for SET
    folders. Applying it to the Manager's own resource count is a heuristic, so it
    is reported as NEPOTVRDENO rather than as a verdict.
    """
    return {
        "resource_count": len(resources()),
        "note": "limit od 300 fajlova po banci / 144 foldera dokumentovan je za .SET foldere; "
                "primjena na broj resursa samog Managera je heuristika",
        "status": Status.NEPOTVRDENO.value,
    }


def reload_all() -> None:
    resources_data.cache_clear()
    factory_names_data.cache_clear()
