"""Experimental read-only STY TrackInfo -> USER PCG catalog cross-reference.

Only SELECTOR / CATALOG edges are inspected; PCG oscillator -> KMP -> PCM
references and target-model remapping are explicitly OUT OF SCOPE. The mapping
from MIDI bank selector 64/65 to on-disk user bank 9/10/11 is a candidate,
not a proven per-device allocation; collisions and missing records are retained.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from core.format import Bank, children, node_at  # noqa: E402
from audit_native_sets import CACHED, USER_STY, rar_entries, read_member, zip_entries  # noqa: E402

CANDIDATE_USER_BANK = {(121, 64): "USER01.PCG", (121, 65): "USER02.PCG", (120, 64): "USERDK.PCG"}


def pcg_catalog(files: dict[str, bytes]) -> dict:
    catalog = {}
    for name, raw in files.items():
        if not name.lower().endswith(".pcg"):
            continue
        b = Bank(PurePosixPath(name).name, raw)
        payload = b.nodes[1].payload(b.blob)
        slots: dict[int, list[str]] = defaultdict(list)
        bank_ids = Counter()
        for i, title in enumerate(b.names):
            record = payload[24*i:24*(i+1)]
            bank_ids[f"{record[18]:02X}.{record[19]:02X}"] += 1
            slots[record[20]].append(title)
        catalog[PurePosixPath(name).name.upper()] = {"file": name, "bank_ids": dict(bank_ids),
            "slots": {str(k): v for k,v in slots.items()},
            "colliding_program_numbers": {str(k): v for k,v in slots.items() if len(v)>1},
            "catalog_entries": len(b.names)}
    return catalog


def style_selectors(files: dict[str, bytes]) -> dict:
    ctr = Counter(); banks = Counter(); error = []
    styles = 0; elements = 0; tracks = 0
    # Ignore type-09 STS selector data: separately decoded and joined in later work.
    for name, raw in files.items():
        if not name.lower().endswith(".sty"):
            continue
        b = Bank(PurePosixPath(name).name, raw)
        for i,node in enumerate(b.resources):
            if node.tag[0] != 6:
                continue
            styles += 1
            try:
                d,_ = b._decoded_resource(i)
                root = node_at(d,0)
                ch = children(d,root)
                if root.end != len(d) or len(ch)<3:
                    raise ValueError("not a known Style resource")
                for el in ch[2:]:
                    parts = children(d,el)
                    if len(parts)<2:
                        raise ValueError("Style element has no TrackInfo")
                    rawtracks = parts[1].payload(d)
                    if len(rawtracks)<48:
                        raise ValueError("Style TrackInfo shorter than 8 x 6 bytes")
                    elements += 1
                    for t in range(8):
                        rec = rawtracks[t*6:t*6+6]
                        # Track records documented for both 48-byte and 80/88-byte
                        # family. Do not infer missing element identity from ordinal.
                        key = tuple(rec[1:4]);ctr[key] += 1; tracks += 1
                        banks[(key[0],key[1])] += 1
            except Exception as exc:
                error.append(f"{name} #{i} {b.names[i]}: {exc}")
    return {"styles": styles, "elements": elements, "track_records": tracks,
            "selectors": ctr, "banks": banks, "errors": error}


def analyze(label: str, files: dict[str,bytes]):
    pcg = pcg_catalog(files)
    refs = style_selectors(files)
    user = []
    for (msb,lsb,pc),count in sorted(refs["selectors"].items()):
        fname = CANDIDATE_USER_BANK.get((msb,lsb))
        if fname is None:continue
        record = pcg.get(fname)
        names = record["slots"].get(str(pc),[]) if record else []
        user.append({"msb":msb,"lsb":lsb,"pc":pc,"track_appearances":count,
                     "pcg_file":fname,"pcg_record_names":names,
                     "candidate_state":"unique" if len(names)==1 else "ambiguous" if names else "not_in_available_pcg"})
    return {"label": label,"style_count":refs["styles"],"style_element_count":refs["elements"],
            "track_record_count":refs["track_records"],"unique_selectors":len(refs["selectors"]),
            "bank_usage":[{"msb":a,"lsb":b,"track_appearances":c} for (a,b),c in refs["banks"].most_common()],
            "user_selector_references":user,
            "user_track_appearances":sum(x["track_appearances"] for x in user),
            "unmatched_user_track_appearances":sum(x["track_appearances"] for x in user if x["candidate_state"]!="unique"),
            "pcg_catalogs":{k:{**v,"slots":None} for k,v in pcg.items()},
            "style_errors":refs["errors"],
            "caution":"Tentative STY selector -> USER PCG filename/PC association. Does NOT prove program waveform/KMP/PCM closure, source-model or OS compatibility, nor BKP restore."}


def load_archive(p:Path) -> dict[str,bytes]:
    members = zip_entries(p) if p.suffix.lower()==".zip" else rar_entries(p)
    if any(not e["safe_path"] for e in members):raise ValueError("Unsafe archive path")
    files={}
    for e in members:
        if not e["encrypted"] and e["path"].lower().endswith((".sty",".pcg")):
            files[e["path"]]=read_member(p,e["path"])
    return files


def main():
    sources = [CACHED/"Hammy.SET.zip",CACHED/"Pa1x BABAJA 2011 bosna.SET.rar"]
    reports = [analyze(p.name,load_archive(p)) for p in sources]
    reports.append(analyze("USER01.STY standalone",{"USER01.STY":USER_STY.read_bytes()}))
    (HERE/"STYLE_TO_USER_PCG_CANDIDATES.json").write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding="utf-8")
    for r in reports:
        s=Counter(x["candidate_state"] for x in r["user_selector_references"])
        print(r["label"],"styles",r["style_count"],"elements",r["style_element_count"],
              "track records",r["track_record_count"],"user refs",r["user_track_appearances"],
              "unique selectors",len(r["user_selector_references"]),"statuses",s,
              "unmatched appearances",r["unmatched_user_track_appearances"],"errors",len(r["style_errors"]))
        print(" PCG bankIDs",{k:v["bank_ids"] for k,v in r["pcg_catalogs"].items()})
        print(" missing examples",[x for x in r["user_selector_references"] if x["candidate_state"]!="unique"][:8])


if __name__=="__main__":main()
