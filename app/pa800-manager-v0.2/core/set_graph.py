"""Read-only native Style -> user Sound catalog candidate graph.

This is NOT an audio equivalence or complete conversion graph. It does not
interpret STS/PRF selectors, PCG oscillators, KMP/PCM IDs, or BKP packing.
Unknown variants stay unlinked and require independent model/hardware proof.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import PurePosixPath
from typing import Iterable

from .format import Bank, FormatError

# Observed bank directories in user-supplied archives. Candidate association
# to MIDI CC00/CC32; never sufficient by itself to authorize native writes.
USER_BANK_FILES = {(121, 64): "USER01.PCG", (121, 65): "USER02.PCG", (120, 64): "USERDK.PCG"}
DISK_BANK_IDS = {"USER01.PCG": (4, 9), "USER02.PCG": (4, 10), "USERDK.PCG": (4, 11)}


def dependency_candidates(files: Iterable[tuple[str, Bank]]) -> dict:
    """Summarize address occurrence and tentative USER PCG catalog joins.

    Returns bounded JSON, with no mutation of the input banks. A selector not
    in a USER.PCG is NOT automatically an orphan: the keyboard may fall back to
    factory resources or another bank/state, and this tool does not simulate it.
    """
    banks = list(files)
    pcg: dict[str, dict[int, list[str]]] = {}
    pcg_summary: dict[str, dict] = {}
    duplicate_banks: set[str] = set()
    problems = []
    for path, bank in banks:
        if bank.ext != ".PCG":
            continue
        name = PurePosixPath(path).name.upper()
        if name not in DISK_BANK_IDS:
            continue
        expected = DISK_BANK_IDS[name]
        raw = bank.nodes[1].payload(bank.blob)
        slots: dict[int, list[str]] = defaultdict(list)
        ids = Counter()
        for i, label in enumerate(bank.names):
            record = raw[24 * i:24 * (i + 1)]
            ids[(record[18], record[19])] += 1
            if (record[18], record[19]) == expected:
                slots[record[20]].append(label)
        if name in pcg_summary:
            problems.append(f"Duplirana {name} banka u arhivi; nazivi nisu povezani.")
            duplicate_banks.add(name)
            pcg.pop(name, None)
            pcg_summary[name] = {"path": path, "status": "duplicate", "resources": bank.base_info()["resources"]}
            continue
        if name in duplicate_banks:
            continue
        pcg[name] = slots
        pcg_summary[name] = {
            "path": path, "resources": len(bank.resources),
            "bankIds": [{"id": f"{a:02X}.{b:02X}", "count": count} for (a, b), count in ids.items()],
            "namedProgramSlots": len(slots),
            "catalogBankVerified": len(ids) == 1 and expected in ids,
        }
        if not pcg_summary[name]["catalogBankVerified"]:
            problems.append(f"{name}: neočekivan PCG disk bank ID; selektori nisu pouzdano povezani.")
            pcg.pop(name)
    selectors: Counter[tuple[int, int, int]] = Counter()
    total_styles = total_elements = track_records = 0
    for path, bank in banks:
        if bank.ext != ".STY":
            continue
        for ix, resource in enumerate(bank.resources):
            if resource.tag[0] != 6:
                continue
            try:
                detail = bank.resource_detail(ix)
                total_styles += 1
                for element in detail["elements"]:
                    total_elements += 1
                    for track in element["tracks"]:
                        track_records += 1
                        selectors[(track["msb"], track["lsb"], track["pc"])] += 1
            except (FormatError, ValueError) as exc:
                if len(problems) < 100:
                    problems.append(f"{path}: Style #{ix + 1}: {exc}")
    references = []
    for (msb, lsb, pc), appearances in sorted(selectors.items()):
        disk = USER_BANK_FILES.get((msb, lsb))
        if disk is None:
            continue
        matches = pcg.get(disk, {}).get(pc, [])
        status = "candidate_unique" if len(matches) == 1 else ("candidate_ambiguous" if matches else "not_in_archive_pcg")
        references.append({"msb": msb, "lsb": lsb, "pc": pc, "trackAppearances": appearances,
                           "pcgFile": disk, "candidateNames": matches, "status": status})
    statuses = Counter(r["status"] for r in references)
    return {
        "mode": "READ_ONLY_CANDIDATE_GRAPH", "stylesInspected": total_styles,
        "elementsInspected": total_elements, "trackRecords": track_records,
        "uniqueSelectors": len(selectors),
        "userTrackAppearances": sum(r["trackAppearances"] for r in references),
        "userUniqueSelectors": len(references),
        "candidateUnique": statuses["candidate_unique"],
        "notInArchivePcg": statuses["not_in_archive_pcg"],
        "ambiguous": statuses["candidate_ambiguous"],
        "pcgBanks": pcg_summary, "references": references,
        "warnings": problems,
        "coverage": "Samo STY TrackInfo selector -> mogući USER PCG kataloški slot. STS/PRF, oscillator -> KMP -> PCM, BKP i model-target remap NISU potvrđeni.",
        "caution": "Nedostajući PCG slot nije dokaz neispravnog seta: mogući su factory/default fallback i razlike modela. Nema sigurnog native upisa ili playback certifikacije.",
    }
