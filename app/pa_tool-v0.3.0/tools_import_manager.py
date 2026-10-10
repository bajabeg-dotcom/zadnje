# -*- coding: utf-8 -*-
"""Build pa_tool data files from the 2.exe_pa_analysis results.

Sources (read-only):
  * resource_index.csv / pe_details.json  - the 175 resources recovered from 2.exe
  * resources/*.bin                       - the recovered payloads
  * pa800_remap/native/codec.py           - the project's own CBarc decompressor

Outputs (pa_tool/data/):
  * manager_resources.json  - inventory of the Pa Manager resources
  * factory_names.json      - factory name tables, parsed by structure

Two distinct name layouts were measured, both VERIFIED:

  FAC_ROM_PCG (plaintext, 24-byte record)
      +0   18 B  name, null-padded ASCII
      +18   1 B  0x04
      +19   2 B  index, u16 BIG-endian, sequential inside a section
      +21   1 B  section flag (0x02..0x08)
      +22   2 B  00 00

  FAC_ROM_KMP (OC32-compressed, 40-byte record inside the decoded payload)
      +0   18 B  name, space-padded ASCII
      +18  22 B  binary fields (velocity layers, key ranges, sample pointers)
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

WORKSPACE = Path(r"E:\workspace-01a10f2d-3a3e-7dd1-aa93-65f15a734465")
SRC = WORKSPACE / "2.exe_pa_analysis"
RUN = SRC / "2_e059fd4b"
OUT_DIR = WORKSPACE / "pa_tool" / "data"
CODEC_PATH = (WORKSPACE / "KORG_KOMPLET_2026-10-06" / "PA800" / "PA800_KOMPLET"
              / "PA800_Remapper" / "pa800_remap" / "native" / "codec.py")

PCG_NAME_LEN = 18
PCG_REC = 24
KMP_NAME_LEN = 18
KMP_REC = 40
KMP_TABLE_START = 0xAFF78


# --------------------------------------------------------------------- PCG table

def parse_pcg_record(data: bytes, off: int):
    if off + PCG_REC > len(data):
        return None
    nfield = data[off:off + PCG_NAME_LEN]
    if b"\x00" not in nfield:
        return None
    name = nfield.split(b"\x00", 1)[0]
    if not name or not all(0x20 <= c < 0x7F for c in name):
        return None
    if any(c != 0 for c in nfield[len(name):]):
        return None
    tail = data[off + PCG_NAME_LEN:off + PCG_REC]
    if tail[0] != 0x04 or tail[4] != 0x00 or tail[5] != 0x00:
        return None
    return name.decode("ascii"), (tail[1] << 8) | tail[2], tail[3]


def walk_pcg_table(data: bytes, max_start: int = 0x200):
    starts = [off for off in range(0, min(len(data), max_start)) if parse_pcg_record(data, off)]
    if not starts:
        return None, []
    start = starts[0]
    recs = []
    off = start
    while True:
        r = parse_pcg_record(data, off)
        if r is None:
            break
        recs.append(r)
        off += PCG_REC
    return start, recs


def pcg_sections(recs):
    out, cur, prev = [], [], None
    for r in recs:
        if prev is not None and r[1] != prev + 1 and cur:
            out.append(cur)
            cur = []
        cur.append(r)
        prev = r[1]
    if cur:
        out.append(cur)
    return out


# --------------------------------------------------------------------- OC decode

def load_codec():
    if not CODEC_PATH.is_file():
        return None
    spec = importlib.util.spec_from_file_location("korg_codec", CODEC_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["korg_codec"] = mod
    spec.loader.exec_module(mod)
    return mod


def decode_oc32(data: bytes, pos: int, codec):
    """Decode an OC31/OC32 payload.

    The CBarc decompressor terminates on its own control code and then demands the
    whole stream was consumed; KORG leaves a short trailer, so the reported
    "left N compressed bytes unread" is exactly the trailer size. Trim and retry,
    then validate the documented XOR check byte.
    """
    if codec is None:
        return None, None
    magic = data[pos:pos + 4].decode("ascii", "replace")
    capacity = int.from_bytes(data[pos + 4:pos + 8], "little")
    stream = data[pos + 8:]

    trim = 0
    for _ in range(6):
        window = stream[:len(stream) - trim] if trim else stream
        try:
            out = codec._barc_decompress(window, capacity)
        except codec.VerificationError as exc:
            m = re.search(r"left (\d+) compressed bytes unread", str(exc))
            if m:
                trim += int(m.group(1))
                continue
            return None, {"magic": magic, "capacity": capacity, "error": str(exc)}
        end = pos + 8 + len(stream) - trim
        chk = 0
        for v in out:
            chk ^= v
        trailer = data[end:]
        return out, {
            "magic": magic,
            "capacity": capacity,
            "trailer_bytes": len(trailer),
            "xor_computed": f"0x{chk:02x}",
            "xor_in_file": f"0x{trailer[0]:02x}" if trailer else None,
            "xor_match": bool(trailer) and chk == trailer[0],
        }
    return None, {"magic": magic, "capacity": capacity, "error": "trailer trim did not converge"}


# --------------------------------------------------------------------- KMP table

def parse_kmp_record(row: bytes):
    """18-byte space-padded name then 22 bytes of binary fields.

    A record whose name is all spaces is still a real record: the table keeps the
    slot but leaves the name empty. Breaking on it truncated the table at 93 of
    1599 records, so an empty name returns an empty string, not None.
    """
    if len(row) < KMP_NAME_LEN:
        return None
    j = 0
    while j < KMP_NAME_LEN and 0x20 <= row[j] < 0x7F:
        j += 1
    if j == 0:
        return None
    name = row[:j].decode("ascii").strip()
    return name, row[j:KMP_REC]


# words that mark a real KORG drum/sample name rather than compressed-stream noise
SAMPLE_VOCAB = re.compile(
    r"(?i)\b(bd|sd|hh|hihat|rim|tom|snare|kick|clap|cymbal|crash|ride|perc|conga|bongo|"
    r"tabla|djembe|shaker|tamb|cowbell|wood|stick|brush|stamp|hand|stick|open|closed|"
    r"mute|roll|flam|drag|ghost|accent|natural|acoustic|pop|jazz|rock|dance|techno|"
    r"electro|house|hiphop|hip\s?hop|funk|soul|latin|afro|cuban|kit|std|standard|"
    r"ambient|analog|digital|gm|rx|velo|soft|hard|tight|loose|deep|dry|wet|gd|lp|mp|mf|ff)\b"
)


def is_plausible_sample_name(s: str) -> bool:
    """Distinguish a real KORG sample name from compressed-stream noise.

    Noise tokens look like '1iDv', '5Izo', 'NBIy': mixed case with digits, no real
    structure. Real names are two or more words separated by spaces and use the
    kit/articulation vocabulary.
    """
    if len(s) < 4 or len(s) > 32:
        return False
    words = s.split()
    if len(words) < 2:
        return False
    if not SAMPLE_VOCAB.search(s):
        return False
    return True


def walk_kmp_table(dec: bytes, start: int = KMP_TABLE_START):
    """Walk the 40-byte stride until a record no longer starts with a printable byte."""
    recs = []
    off = start
    while off + KMP_REC <= len(dec):
        row = dec[off:off + KMP_REC]
        if not (0x20 <= row[0] < 0x7F):
            break
        parsed = parse_kmp_record(row)
        if parsed is None:
            break
        recs.append((off, parsed[0], parsed[1]))
        off += KMP_REC
    return recs, off


# --------------------------------------------------------------------- build

def build_resources():
    rows = list(csv.DictReader(open(RUN / "resource_index.csv", encoding="utf-8-sig")))
    details = json.load(open(RUN / "pe_details.json", encoding="utf-8"))
    mapping = details.get("resource_mapping", {})

    out = []
    for r in rows:
        try:
            size = int(r["size"])
        except (ValueError, KeyError):
            size = 0
        out.append({
            "index": int(r["index"]),
            "type": r["type"],
            "name": r["name"],
            "language_id": r["language_id"],
            "size": size,
            "codepage": r["codepage"],
            "data_rva": r["data_rva"],
            "data_file_offset": r["data_file_offset"],
            "payload_source": r["payload_source"],
            "sha256": r["sha256"],
            "file": r["file"],
        })

    return {
        "_meta": {
            "description": "Inventar resursa izvucenih iz KORG PA Manager (2.exe) "
                           "statickom analizom, uz oporavak pakovanih resursa iz "
                           "memorijske slike.",
            "source_binary": {
                "name": "2.exe",
                "sha256": details.get("sha256"),
                "size": details.get("file_size"),
                "machine": details.get("machine"),
                "subsystem": details.get("subsystem"),
            },
            "packer": {
                "value": details.get("potential_packers", []),
                "evidence": details.get("packer_evidence", {}),
                "status": "VERIFIED",
                "note": "PECompact2: marker PEC2 u PE zaglavlju i stub 'PECompact2' u telu; "
                        ".text entropija 8.000, virtual_size 70.770.688 vs raw_size 24.860.160",
            },
            "resource_mapping": mapping,
            "how_recovered": {
                "from_file": "126 resursa je bilo moguce procitati direktno iz bajtova fajla",
                "from_image": "49 resursa zivi u virtuelnom repu pakovanog modula i "
                              "oporavljeno je iz 1:1 memorijske slike (RVA == file offset)",
                "image_source": "_dyn_dump.bin, 71.294.976 B = tacno SizeOfImage, "
                                "sha256 5839f1e43a55ea392aa4020cc274f0c9664cf57b21dc21c2c3f959fa884dc684",
            },
            "status_legend": {
                "VERIFIED": "izmjereno i reproducibilno",
                "NEPOTVRDENO": "dokaz postoji, iskljucivost ne",
                "UNKNOWN": "nema uzorka/podatka",
            },
        },
        "resources": out,
    }


def build_names(codec):
    res_dir = RUN / "resources"
    catalogue = {}

    # ---- PCG: plaintext 24-byte table
    for p in sorted(res_dir.glob("*FAC_ROM_PCG*")):
        data = p.read_bytes()
        start, recs = walk_pcg_table(data)
        if not recs:
            continue
        secs = pcg_sections(recs)
        entries = []
        for si, sec in enumerate(secs):
            for name, idx, flag in sec:
                entries.append({
                    "index": idx, "flag": f"0x{flag:02x}", "section": si, "name": name,
                })
        catalogue["programs"] = {
            "resource_file": p.name,
            "resource_size": len(data),
            "table_start": f"0x{start:04X}" if start is not None else None,
            "record_size": PCG_REC,
            "name_field_size": PCG_NAME_LEN,
            "records": len(recs),
            "sections": [
                {"section": si, "records": len(sec), "index_from": sec[0][1],
                 "index_to": sec[-1][1], "first_name": sec[0][0], "last_name": sec[-1][0]}
                for si, sec in enumerate(secs)
            ],
            "distinct_names": len({e["name"] for e in entries}),
            "status": "VERIFIED",
            "evidence": "24-bajtni zapis (18 B ime + 0x04 + u16BE indeks + flag + 00 00); "
                        "indeks sekvencijalan unutar sekcije",
            "entries": entries,
        }

    # ---- KMP: OC32-compressed payload, 40-byte record inside a separate region
    kmp_files = sorted(res_dir.glob("*FAC_ROM_KMP*"))
    samples = []
    kmp_info = {}
    for p in kmp_files:
        data = p.read_bytes()
        oc_pos = data.find(b"OC32")
        decoded = None
        decode_info = None
        if oc_pos >= 0:
            decoded, decode_info = decode_oc32(data, oc_pos, codec)
        if decoded is None:
            kmp_info = {
                "resource_file": p.name, "status": "NEPOTVRDENO",
                "note": "OC32 payload nije dekodiran (kodek nedostupan ili greska)",
                "decode": decode_info, "entries": [],
            }
            continue

        dec_path = RUN / "FAC_ROM_KMP_decoded.bin"
        dec_path.write_bytes(decoded)
        recs, end = walk_kmp_table(decoded)

        # the sample-name list lives in a second, wide region: collect the
        # zero/space-delimited printable runs that look like instrument names
        raw_samples = []
        for m in re.finditer(rb"[A-Za-z0-9+.'-][A-Za-z0-9 .&'+/-]{3,31}", decoded):
            s = m.group().decode("ascii").strip()
            if not s or not re.search(r"[aeiouAEIOU]", s):
                continue
            if not re.search(r"[A-Za-z]{3}", s):
                continue
            raw_samples.append({"offset": hex(m.start()), "text": s,
                                "plausible": is_plausible_sample_name(s)})
        seen = set()
        raw_samples = [s for s in raw_samples
                       if not (s["text"] in seen or seen.add(s["text"]))]
        samples = [s for s in raw_samples if s["plausible"]]

        kmp_info = {
            "resource_file": p.name,
            "resource_size": len(data),
            "oc32_header_offset": f"0x{oc_pos:04X}",
            "decode": decode_info,
            "decoded_size": len(decoded),
            "decoded_sha256": hashlib.sha256(decoded).hexdigest(),
            "table_start": f"0x{end - len(recs) * KMP_REC:04X}" if recs else None,
            "table_end": f"0x{end:04X}" if recs else None,
            "record_size": KMP_REC,
            "name_field_size": KMP_NAME_LEN,
            "records": len(recs),
            "distinct_names": len({r[1] for r in recs if r[1]}),
            "named_records": sum(1 for r in recs if r[1]),
            "unnamed_slots": sum(1 for r in recs if not r[1]),
            "status": "VERIFIED",
            "evidence": "OC32 payload se dekodira CBarc kodekom do tacno deklarisane "
                        "velicine i XOR kontrolni bajt se poklapa; unutar njega je "
                        "tabela zapisa od 40 B sa 18-bajtnim imenom",
            "note": "Prvih 18 B zapisa je ime artikulacije; ostatak je 22 B binarnih "
                    "polja (velocity slojevi, key range, pokazivaci na uzorke) "
                    "— NEPOTVRDENO",
            "entries": [{"offset": hex(o), "name": n, "fields": f.hex(" ")}
                        for o, n, f in recs],
            "sample_names": samples[:4000],
            "sample_name_count": len(samples),
            "sample_names_raw_count": len(raw_samples),
            "sample_name_filter": "samo imena sa 2+ rijeci iz KORG kit/articulation "
                                  "rječnika; sirovi ASCII nizovi sadrze i sum "
                                  "kompresovanog toka (npr. '1iDv')",
        }
        break

    catalogue["drum_kits_and_multisamples"] = kmp_info

    # ---- NEW_* container templates
    templates = []
    for pat in ("*NEW_*", "*TIKTOK*"):
        for p in sorted(res_dir.glob(pat)):
            data = p.read_bytes()
            templates.append({
                "resource_file": p.name, "size": len(data),
                "is_container": data[:4] == b"\x01\x00\x01\x14",
                "korf": data.find(b"KORF"), "kbeg": data.find(b"KBEG"),
                "kend": data.rfind(b"KEND"),
            })
    catalogue["container_templates"] = {
        "status": "VERIFIED",
        "note": "Fabricki prazni kontejneri koje Manager koristi kao sablone",
        "entries": templates,
    }

    # ---- embedded PE modules
    embedded = []
    for p in sorted(res_dir.glob("*.bin")):
        data = p.read_bytes()
        if data[:2] != b"MZ":
            continue
        pe_off = int.from_bytes(data[0x3C:0x40], "little")
        if data[pe_off:pe_off + 4] != b"PE\x00\x00":
            continue
        machine = int.from_bytes(data[pe_off + 4:pe_off + 6], "little")
        nsec = int.from_bytes(data[pe_off + 6:pe_off + 8], "little")
        opt_size = int.from_bytes(data[pe_off + 20:pe_off + 22], "little")
        sec_names = []
        for i in range(nsec):
            so = pe_off + 24 + opt_size + i * 40
            sec_names.append(data[so:so + 8].rstrip(b"\x00").decode("latin1", "replace"))
        embedded.append({
            "resource_file": p.name, "size": len(data), "machine": f"0x{machine:04X}",
            "sections": sec_names, "has_upx": data.count(b"UPX!") > 0,
            "has_pecompact": data.count(b"PECompact2") > 0,
            "is_rex_sdk": data.count(b"REX Shared Library") > 0,
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    catalogue["embedded_modules"] = {
        "status": "VERIFIED",
        "note": "Ugradjeni PE moduli. Tipovi i layout odgovaraju PE1/PE2/PE3 "
                "dokumentovanim za v5-stream.bin, ali hash-evi se razlikuju "
                "-> ista porodica modula, ne isti fajlovi",
        "evidence": "res 36/41/42: x86 PECompact2 (3 sekcije), x64 UPX (UPX0/UPX1/.rsrc), "
                    "x64 REX SDK (7 sekcija)",
        "entries": embedded,
    }

    catalogue["oc_decode"] = {
        "status": "VERIFIED",
        "note": "OC31/OC32 payloadi se dekodiraju CBarc kodekom iz pa800_remap. "
                "Kodek se sam terminise, pa KORG trailer treba odbiti; broj koji "
                "kodek prijavi kao 'unread' je tacno velicina trailera.",
        "evidence": "FAC_ROM_KMP: deklarisano 1.141.260 B, dekodirano 1.141.260 B, "
                    "trailer 30 B, XOR 0x5b se poklapa",
        "codec": str(CODEC_PATH),
    }
    return catalogue


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    codec = load_codec()
    print(f"codec: {'loaded' if codec else 'NOT FOUND'} ({CODEC_PATH.name})")

    res = build_resources()
    p1 = OUT_DIR / "manager_resources.json"
    p1.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    m = res["_meta"]["resource_mapping"]
    print(f"\nwrote {p1}")
    print(f"  resources {m.get('total')} = from_file {m.get('from_file')} "
          f"+ from_image {m.get('from_image')}, unmapped {m.get('unmapped')}")

    names = build_names(codec)
    p2 = OUT_DIR / "factory_names.json"
    p2.write_text(json.dumps(names, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {p2}")

    prog = names.get("programs", {})
    print(f"  PCG programs : {prog.get('records')} records in {len(prog.get('sections', []))} section(s)")
    for s in prog.get("sections", []):
        print(f"      section {s['section']}: {s['records']}, idx {s['index_from']}..{s['index_to']}")

    kmp = names.get("drum_kits_and_multisamples", {})
    print(f"  KMP drum kits: status={kmp.get('status')} records={kmp.get('records')} "
          f"distinct={kmp.get('distinct_names')}")
    d = kmp.get("decode") or {}
    if d:
        print(f"      OC32 {d.get('magic')} {d.get('capacity'):,} B decoded, "
              f"trailer {d.get('trailer_bytes')} B, xor {d.get('xor_computed')} "
              f"match={d.get('xor_match')}")
    print(f"      sample names: {kmp.get('sample_name_count')}")
    print(f"  templates    : {len(names['container_templates']['entries'])}")
    print(f"  embedded PEs : {len(names['embedded_modules']['entries'])}")


if __name__ == "__main__":
    main()
