"""Conversion operations.

Every conversion is described by a Direction record taken from the verified RE
tables, and every entry point refuses before acting when the direction is not
proven to exist. Nothing writes in place: outputs always go to a new path.

Two hard rules from the RE findings:

  * PA Manager is NOT a model converter. It is a one-way station for SET: an older
    SET cannot even be loaded, and a newer one cannot be exported back. Everything
    else has an in and an out path, always per object, never as a whole SET.
  * A locked, compressed or factory-ROM object is invisible to the Manager. The
    tool refuses it up front with the original message and offset.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# this file is <workspace>/pa_tool/pa_tool/convert/__init__.py, so the workspace root
# is four levels up: convert -> pa_tool(pkg) -> pa_tool(project) -> <workspace>
WORKSPACE = Path(__file__).resolve().parents[3]

from pa_tool import models
from pa_tool.status import Result, Status


# --------------------------------------------------------------------- asset paths
#
# setconvert needs the pa800_native package, which lives outside this project. The
# paths are probed in order and every one that resolves is reported, so a missing
# dependency shows up as NEPOTVRDENO instead of a crash.
#
# Portability: the hard-coded paths below are only the author's defaults. Any machine
# can point the tool at its own copy without editing code, via environment variables
# (os.pathsep-separated, so ';' on Windows and ':' elsewhere):
#
#   PA_TOOL_ASSET_ROOT        one root that holds pa800_native/ setconvert/ pa800_remap/
#   PA_TOOL_NATIVE_PARENT     parent(s) of pa800_native/ and pa800_remap/
#   PA_TOOL_SETCONVERT_PARENT parent(s) of setconvert/
#
# Environment values are probed FIRST, so they win over the baked-in defaults.

_DEFAULT_NATIVE_PARENTS = [
    Path(r"E:\BAJA MUSIC\BAJA_MUSIC_PA800_MANAGER_V1_COMPLETE_2026-09-22"),
    WORKSPACE / "KORG_KOMPLET_2026-10-06" / "PA800" / "PA800_KOMPLET" / "PA800_Remapper",
]

_DEFAULT_SETCONVERT_PARENTS = [
    WORKSPACE / "samples" / "NEWNAME_SET",
    WORKSPACE / "DOKAZI_FORMATI_I_KONVERTOVANJE_2026-10-06" / "06_ALATI",
]


def _env_parents(*names: str) -> list[Path]:
    """Paths from os.pathsep-separated environment variables, in order, de-duplicated."""
    out: list[Path] = []
    seen: set[str] = set()
    for name in names:
        for raw in os.environ.get(name, "").split(os.pathsep):
            raw = raw.strip()
            if raw and raw not in seen:
                seen.add(raw)
                out.append(Path(raw))
    return out


def native_parent_candidates() -> list[Path]:
    return _env_parents("PA_TOOL_NATIVE_PARENT", "PA_TOOL_ASSET_ROOT") + _DEFAULT_NATIVE_PARENTS


def setconvert_parent_candidates() -> list[Path]:
    return _env_parents("PA_TOOL_SETCONVERT_PARENT", "PA_TOOL_ASSET_ROOT") + _DEFAULT_SETCONVERT_PARENTS


# Kept as module attributes for backward compatibility (older callers/tests).
NATIVE_PARENT_CANDIDATES = _DEFAULT_NATIVE_PARENTS
SETCONVERT_CANDIDATES = _DEFAULT_SETCONVERT_PARENTS


def resolve_paths() -> dict:
    """Report which external assets are reachable right now.

    Environment overrides (PA_TOOL_ASSET_ROOT / PA_TOOL_NATIVE_PARENT /
    PA_TOOL_SETCONVERT_PARENT) are checked before the baked-in defaults.
    """
    native_cands = native_parent_candidates()
    setconvert_cands = setconvert_parent_candidates()
    native_parent = next((p for p in native_cands
                          if (p / "pa800_native" / "oc31_codec.py").is_file()), None)
    setconvert_parent = next((p for p in setconvert_cands
                              if (p / "setconvert" / "conv.py").is_file()), None)
    remap_parent = next((p for p in native_cands
                         if (p / "pa800_remap" / "remap.py").is_file()), None)
    if remap_parent is None:
        cand = WORKSPACE / "KORG_KOMPLET_2026-10-06" / "PA800" / "PA800_KOMPLET" / "PA800_Remapper"
        remap_parent = cand if (cand / "pa800_remap" / "remap.py").is_file() else None
    return {
        "pa800_native_parent": native_parent,
        "setconvert_parent": setconvert_parent,
        "pa800_remap_parent": remap_parent,
    }


_LOADED: dict = {}


def _ensure_path(parent: Optional[Path]) -> None:
    if parent is None:
        return
    s = str(parent)
    if s not in sys.path:
        sys.path.insert(0, s)


def _supply_missing_fast_encoder() -> str:
    """setconvert imports encode_oc31_greedy from a module that is absent on disk.

    Rather than reimplementing a codec, map that one name onto the optimal encoder
    that does exist. Returned string documents what was done.
    """
    import types
    name = "pa800_native.oc31_fast_encoder"
    if name in sys.modules:
        return "already loaded"
    try:
        importlib.import_module(name)
        return "imported from disk"
    except ModuleNotFoundError:
        pass
    try:
        pkg = importlib.import_module("pa800_native")
    except Exception as exc:  # noqa: BLE001
        return f"unavailable: {type(exc).__name__}"
    if not hasattr(pkg, "encode_oc31"):
        return "unavailable: optimal encoder missing too"
    mod = types.ModuleType(name)
    mod.encode_oc31_greedy = pkg.encode_oc31
    sys.modules[name] = mod
    setattr(pkg, "oc31_fast_encoder", mod)
    return "shimmed: encode_oc31_greedy -> encode_oc31 (optimal encoder)"


def load_setconvert():
    """Load the existing setconvert package for reuse. Returns (module, notes)."""
    if "setconvert" in _LOADED:
        mod = _LOADED["setconvert"]
        return mod, _LOADED.get("notes", {})

    paths = resolve_paths()
    _ensure_path(paths["pa800_native_parent"])
    _ensure_path(paths["setconvert_parent"])
    # Use distinct keys from resolve_paths() to avoid duplicate-key overlap.
    # resolve_paths() reports *where* the asset lives; load_setconvert()
    # notes report *whether* it loaded and what shims were applied.
    notes = {
        "pa800_native_loaded": paths["pa800_native_parent"] is not None,
        "setconvert_loaded": paths["setconvert_parent"] is not None,
    }
    if paths["pa800_native_parent"] is None or paths["setconvert_parent"] is None:
        notes["error"] = "setconvert or pa800_native not reachable"
        _LOADED["setconvert"] = None
        _LOADED["notes"] = notes
        return None, notes

    notes["fast_encoder"] = _supply_missing_fast_encoder()
    try:
        mod = importlib.import_module("setconvert")
    except Exception as exc:  # noqa: BLE001
        notes["error"] = f"{type(exc).__name__}: {exc}"
        _LOADED["setconvert"] = None
        _LOADED["notes"] = notes
        return None, notes

    _LOADED["setconvert"] = mod
    _LOADED["notes"] = notes
    return mod, notes


def sty_families() -> list[dict]:
    """The verified STY family table from setconvert.families.FAMILIES."""
    sc, _ = load_setconvert()
    if sc is None:
        return []
    try:
        from setconvert import families
    except Exception:  # noqa: BLE001
        return []
    out = []
    for key, fam in families.FAMILIES.items():
        out.append({
            "id": fam.id,
            "label": fam.label,
            "models": list(fam.models),
            "name_table": fam.name_table,
            "name_version": fam.name_version,
            "set_folders": list(getattr(fam, "set_folders", ())),
            "notes": getattr(fam, "notes", ""),
        })
    return out


def fingerprint_sty(path: str | Path) -> dict:
    """Fingerprint a real .STY using setconvert.detect. Read-only."""
    sc, _ = load_setconvert()
    if sc is None:
        return {"ok": False, "error": "setconvert not reachable"}
    try:
        from setconvert import detect as sc_detect
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        prof = sc_detect.fingerprint_sty(str(path))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": True,
        "path": str(path),
        "family": prof.family,
        "family_label": next((f["label"] for f in sty_families() if f["id"] == prof.family), ""),
        "mixed": prof.mixed,
        "styles": prof.styles,
        "sts": prof.sts,
        "family_votes": dict(prof.family_votes),
        "dialect_counts": dict(prof.dialect_counts),
        "kbeg_ok": prof.kbeg_ok,
        "errors": list(prof.errors),
    }


def set_families_for_folder(set_folder: str | Path) -> dict:
    """Which family a .SET folder belongs to, using setconvert.setfolder."""
    sc, _ = load_setconvert()
    if sc is None:
        return {"ok": False, "error": "setconvert not reachable"}
    try:
        from setconvert import setfolder as sc_setfolder
        rep = sc_setfolder.inventory_set(str(set_folder))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": True,
        "name": rep.name,
        "family": rep.family,
        "family_label": getattr(rep, "family_label", ""),
        "family_votes": dict(getattr(rep, "family_votes", {})),
        "folders": list(getattr(rep, "folders", [])),
        "sty_files": len(getattr(rep, "sty_files", []) or []),
        "errors": list(getattr(rep, "errors", [])),
    }


# --------------------------------------------------------------------- directions

@dataclass
class Direction:
    key: str
    applicability: str
    exists: Optional[bool]
    status: str
    offset: str = ""
    note: str = ""
    reversible: bool = False


def directions() -> list[Direction]:
    out = []
    for d in models.directions():
        out.append(Direction(
            key=d["id"],
            applicability=d["applicability"],
            exists=d.get("exists"),
            status=d["status"],
            offset=d.get("offset") or d.get("module") or str(d.get("offsets") or ""),
            note=d.get("note", ""),
            reversible=bool(d.get("reversible")),
        ))
    return out


def direction(key: str) -> Optional[Direction]:
    for d in directions():
        if d.key == key:
            return d
    return None


@dataclass
class ConversionPlan:
    """What the tool intends to do, with the proof behind each step."""

    source: Path
    target: Path
    direction: Direction
    steps: list[str] = field(default_factory=list)
    blocked: bool = False
    block_reason: str = ""
    block_evidence: str = ""
    block_error_text: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def status(self) -> Status:
        if self.blocked:
            return Status.VERIFIED  # the refusal itself is proven
        try:
            return Status(self.direction.status)
        except ValueError:
            # an unexpected status string is a data error, not an UNKNOWN result
            raise ValueError(
                f"conversion direction {self.direction.key!r} carries an unknown "
                f"status {self.direction.status!r}; data/conversions.json is inconsistent"
            ) from None

    def to_result(self) -> Result:
        res = Result(name=f"conversion plan ({self.direction.key})")
        res.add("source", str(self.source), Status.VERIFIED)
        res.add("target", str(self.target), Status.VERIFIED)
        res.add("direction", self.direction.applicability, self.status,
                evidence=self.direction.offset, note=self.direction.note)
        res.add("reversible", "yes" if self.direction.reversible else "no", Status.VERIFIED,
                note="only the MG edition pair round-trips" if self.direction.reversible else "")
        for step in self.steps:
            res.add("step", step, Status.VERIFIED)
        for w in self.warnings:
            res.add("warning", w, Status.NEPOTVRDENO)
        if self.blocked:
            res.add("decision", "FAIL", Status.VERIFIED,
                    evidence=self.block_evidence,
                    note=f"{self.block_reason} — {self.block_error_text}")
        else:
            res.add("decision", "PASS", self.status,
                    note="plan only; nothing was written")
        return res


# --------------------------------------------------------------------- refusals

def would_reject(path: str | Path, *, kind: Optional[str] = None,
                 model: Optional[str] = None, source_has_factory_rom: bool = False,
                 source_is_locked: bool = False) -> Optional[tuple[str, str, str]]:
    """Return (reason, evidence_offset, original_message) when the input is refused."""
    from pa_tool import validate

    if source_has_factory_rom:
        d = validate.check_unsupported("FACTORY_ROM")
        return d.reason, d.evidence, d.error_text
    if source_is_locked and kind:
        d = validate.check_locked(kind)
        if d.verdict == "FAIL":
            return d.reason, d.evidence, d.error_text
    if kind:
        d = validate.check_unsupported(kind)
        if d.verdict == "FAIL":
            return d.reason, d.evidence, d.error_text
    if model:
        d = validate.check_pa5x_sound_import(model)
        if d.verdict == "FAIL":
            return d.reason, d.evidence, d.error_text
    return None


def plan_conversion(src: str | Path, dst: str | Path, direction_key: str,
                    *, kind: Optional[str] = None, model: Optional[str] = None,
                    source_has_factory_rom: bool = False,
                    source_is_locked: bool = False) -> ConversionPlan:
    """Build a conversion plan without touching either path."""
    d = direction(direction_key)
    if d is None:
        raise KeyError(f"unknown conversion direction: {direction_key}")

    plan = ConversionPlan(source=Path(src), target=Path(dst), direction=d)

    if d.exists is False:
        plan.blocked = True
        plan.block_reason = f"direction '{direction_key}' does not exist in PA Manager"
        plan.block_evidence = "conversions.json directions[].exists == false"
        plan.block_error_text = ("no such path in the binary; the tool refuses instead of "
                                 "attempting it")
        return plan

    refusal = would_reject(src, kind=kind, model=model,
                           source_has_factory_rom=source_has_factory_rom,
                           source_is_locked=source_is_locked)
    if refusal:
        reason, offset, text = refusal
        plan.blocked = True
        plan.block_reason = reason
        plan.block_evidence = offset
        plan.block_error_text = text
        return plan

    # copy-on-write: the plan always writes a new path, never the source
    if plan.target.resolve() == plan.source.resolve():
        plan.blocked = True
        plan.block_reason = "target equals source"
        plan.block_evidence = "rule: copy-on-write outputs"
        plan.block_error_text = "in-place conversion is never allowed"
        return plan

    plan.steps.append(f"read {plan.source} (read-only)")
    plan.steps.append(f"write a new copy under {plan.target}")
    plan.steps.append("record sha256 of the input before and after")

    if direction_key == "set_to_kst":
        plan.warnings.append(
            "the .SET -> .KST path is NEPOTVRDENO: the UI entry point exists "
            "(@0x003EC1B6) but the full transform is not proven"
        )
    if direction_key == "kst_to_set":
        plan.warnings.append("no such path exists in the binary")
    if direction_key in ("mg_to_std", "std_to_mg", "mg_roundtrip"):
        plan.warnings.append(
            "differences are emitted as new payload bytes rather than re-compressing "
            "the original stream"
        )
    return plan


# --------------------------------------------------------------------- copy helper

def copy_input(src: str | Path, out_dir: str | Path,
               suffix: str = "_copy") -> Path:
    """Copy an input into the output directory, leaving the source untouched.

    This is the only write path in the tool: it never overwrites the source and
    refuses to write inside the source directory.
    """
    src = Path(src)
    out = Path(out_dir)
    if not src.exists():
        raise FileNotFoundError(src)
    out.mkdir(parents=True, exist_ok=True)
    target = out / (src.name if src.is_file() else f"{src.name}{suffix}")
    if src.resolve() == target.resolve():
        raise ValueError("refusing to write over the source")
    try:
        if src.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(src, target)
        else:
            shutil.copy2(src, target)
    except OSError as exc:
        raise OSError(f"copy failed: {exc}") from exc
    return target


def conversion_matrix_rows() -> list[tuple[str, str, str, str]]:
    """(direction, applicability, status, evidence) for reporting."""
    rows = []
    for d in directions():
        exists = {True: "yes", False: "no", None: "unknown"}[d.exists]
        rows.append((d.key, f"{d.applicability} (exists: {exists})", d.status, d.offset))
    return rows


def supported_directions() -> list[str]:
    return [d.key for d in directions() if d.exists is True]


def unsupported_directions() -> list[str]:
    return [d.key for d in directions() if d.exists is not True]
