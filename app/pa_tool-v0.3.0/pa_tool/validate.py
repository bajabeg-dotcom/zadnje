"""Pre-import validation: what PA Manager would accept or refuse.

The rules are the verified rejection tables and hard limits. The tool refuses
BEFORE a conversion instead of attempting something documented as impossible.

Deliberate design choice: checks whose mapping is not provable stay UNKNOWN.
`osc_limit` is the canonical example — four limit strings exist, but which model
owns which limit could not be established from the binary, so it always returns
UNKNOWN rather than a guess.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import container, models
from .status import Result, Status


@dataclass
class Decision:
    verdict: str  # PASS / FAIL / UNKNOWN
    reason: str = ""
    evidence: str = ""
    error_text: str = ""

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "evidence": self.evidence,
            "error_text": self.error_text,
        }


def check_hard_limits(observed: dict[str, int]) -> Result:
    """Compare observed counts against the verified hard limits."""
    res = Result(name="hard limits")
    for what, value in observed.items():
        limit = models.limit_for(what)
        if limit is None:
            res.add(f"limit:{what}", value, Status.UNKNOWN,
                    evidence="no documented limit for this quantity")
            continue
        ok = value <= limit
        res.add(
            f"limit:{what}", f"{value} <= {limit}" if ok else f"{value} > {limit}",
            Status.VERIFIED,
            evidence=f"hard_limits '{what}'",
            note="" if ok else "exceeds the documented limit",
        )
    return res


def check_canonical_sample_format(sample_rate: Optional[int], bits: Optional[int],
                                  channels: Optional[int]) -> Result:
    """44100 Hz / 16 bit / mono is the only fully described target format."""
    res = Result(name="canonical sample format")
    want = models.canonical_sample_format()
    pairs = [
        ("sample_rate_hz", sample_rate, want["sample_rate_hz"], "44100 kHz / 16 bits / mono @0x00250114"),
        ("bits", bits, want["bits"], "44100 kHz / 16 bits / mono @0x00250114"),
        ("channels", channels, want["channels"], "44100 kHz / 16 bits / mono @0x00250114"),
    ]
    for key, got, exp, ev in pairs:
        if got is None:
            res.add(key, "not provided", Status.UNKNOWN, evidence="value not supplied")
        elif int(got) == int(exp):
            res.add(key, got, Status.VERIFIED, evidence=ev)
        else:
            res.add(key, f"{got} != {exp}", Status.VERIFIED, evidence=ev,
                    note="must be converted before import")
    return res


def check_locked(kind: str) -> Decision:
    """Anything locked is invisible to the Manager."""
    table = models.rejections()["locked"]["entries"]
    want = (kind or "").upper()
    for entry in table:
        if entry.get("class", "").upper() == want:
            return Decision("FAIL", f"locked {kind} is not supported",
                            entry["offset"], entry["text"])
    return Decision("PASS", f"no documented lock rejection for class {kind!r}")


def check_unsupported(kind: str) -> Decision:
    """Rejections by content class."""
    table = models.rejections()["content_class"]["entries"]
    want = (kind or "").upper()
    for entry in table:
        if entry.get("kind", "").upper() == want:
            return Decision("FAIL", f"{kind} is rejected by content class",
                            entry["offset"], entry["text"])
    return Decision("PASS", f"no documented content-class rejection for {kind!r}")


def check_pa5x_sound_import(model: str) -> Decision:
    """PA5X SOUND import is explicitly refused."""
    if (model or "").strip().upper() == "PA5X":
        entry = next(e for e in models.rejections()["content_class"]["entries"]
                     if e.get("kind") == "PA5X_SOUND")
        return Decision("FAIL", "PA5X SOUND import is refused",
                        entry["offset"], entry["text"])
    return Decision("PASS", "model is not PA5X")


def check_osc_limit(model: str) -> Result:
    """Always UNKNOWN.

    Four limit strings (4/5/16/24) exist, but the mapping to models is not
    provable from the binary: the bytes after each string are the length of the
    NEXT string, not a model id, and no model name appears in that range. The RE
    findings close this as UNKNOWN, so the tool reports UNKNOWN.
    """
    info = models.osc_limits()
    res = Result(name=f"OSC limit ({model or 'unspecified model'})")
    res.add(
        "osc_limit", "UNKNOWN", Status.UNKNOWN,
        evidence=f"data/korg_tokens.json osc_limit; candidates: "
                 f"{', '.join(str(c['value']) for c in info['candidates'])}",
        note=(f"not provable from the binary — disproof: {info['disproof']}; "
              f"would be resolved by: {info['would_resolve']}"),
    )
    return res


def check_ram(model: str, required_mb: Optional[int] = None) -> Result:
    """Compare a required size against the documented RAM for a model."""
    res = Result(name=f"RAM ({model})")
    info = models.ram_for(model)
    available = info.get("size_mb")
    if info.get("ambiguous"):
        res.add("available_ram_mb", f"ambiguous: {info['candidates']}", Status.NEPOTVRDENO,
                evidence=info.get("evidence", ""),
                note="two documented base sizes; which applies is not proven")
    elif available is None:
        res.add("available_ram_mb", "UNKNOWN", Status.UNKNOWN, evidence=info.get("evidence", ""),
                note="model not present in the documented RAM table")
    else:
        extra = f" (variants {info['variants']})" if info.get("variants") else ""
        res.add("available_ram_mb", f"{available}{extra}", Status.VERIFIED,
                evidence=info["evidence"])

    if required_mb is None:
        res.add("required_ram_mb", "not provided", Status.UNKNOWN, evidence="value not supplied")
    elif available is not None and not info.get("ambiguous"):
        ok = required_mb <= available
        res.add("fits", "yes" if ok else "no", Status.VERIFIED,
                evidence="RAM table + supplied requirement",
                note="" if ok else "exceeds available RAM -> would be refused (msgNoRam)")
    elif info.get("ambiguous"):
        # a fit cannot be decided when the available size itself is undecided
        worst = max(info["candidates"])
        ok = required_mb <= worst
        res.add("fits", "yes" if ok else "no", Status.NEPOTVRDENO,
                evidence=f"against the largest documented size {worst} MB",
                note="the model has two documented RAM sizes, so this is not conclusive")
    else:
        res.add("fits", "UNKNOWN", Status.UNKNOWN, evidence="available RAM unknown")
    return res


def check_container(path: str | Path) -> Result:
    """Structural validation of a KORG container file."""
    res = Result(name=f"container structure ({Path(path).name})")
    report = container.parse_container(path)
    if not report.is_container:
        res.add("magic", "absent", Status.VERIFIED, evidence=f"first 4 bytes != 01 00 01 14 ({report.error})")
        res.add("decision", "FAIL", Status.VERIFIED, evidence="not a KORG container")
        return res

    res.add("magic", "01 00 01 14", Status.VERIFIED, evidence="korg_tokens.json container_magic.value_hex")
    header = report.header
    if header and header.declared_ok:
        res.add("payload_length", header.payload_length, Status.VERIFIED,
                evidence="u32 big-endian @+4 equals file_size - 8 (6/7 containers measured in Pa Manager)")
    elif header:
        res.add("payload_length", header.payload_length, Status.NEPOTVRDENO,
                evidence=f"u32 big-endian @+4; delta {header.delta} vs file_size - 8",
                note="documented for the two factory ROM containers inside Pa Manager")
    res.add("resource_chain", "KORF/KBEG/KEND" if report.chain.ok else "incomplete",
            Status.VERIFIED if report.chain.ok else Status.NEPOTVRDENO,
            evidence=f"KORF x{len(report.chain.korf_offsets)}, "
                     f"KBEG x{len(report.chain.kbeg_offsets)}, "
                     f"KEND x{len(report.chain.kend_offsets)}")
    if report.compression.any:
        res.add("compressed_payloads",
                f"OC20={report.compression.oc20} OC31={report.compression.oc31} OC32={report.compression.oc32}",
                Status.VERIFIED, evidence="compression magics counted in the file")
    res.add("decision", "PASS", Status.VERIFIED, evidence="structure matches the verified layout")
    return res


def build_import_report(path: str | Path, model: Optional[str] = None,
                        required_ram_mb: Optional[int] = None,
                        kind: Optional[str] = None) -> list[Result]:
    """Full pre-import validation for one path."""
    p = Path(path)
    results = [check_container(p)] if p.is_file() else []
    if model:
        results.append(check_pa5x_sound_import(model))
        results.append(check_ram(model, required_ram_mb))
        results.append(check_osc_limit(model))
    if kind:
        results.append(Result(name=f"locked check ({kind})"))
        d = check_locked(kind)
        results[-1].add("decision", d.verdict, Status.VERIFIED, evidence=d.evidence,
                        note=d.reason + (f" — {d.error_text}" if d.error_text else ""))
    return results
