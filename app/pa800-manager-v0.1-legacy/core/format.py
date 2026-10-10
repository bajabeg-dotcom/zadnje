"""Conservative PA800 resource-bank inspector.

Reads documented STYLE, PAD, PRF, SETUP and PCG containers from supplied
factory/user samples. Fields without a documented serializer remain opaque.
The public API does not write native PA800 resources.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .codec import FormatError, decode_oc20_file, decode_oc3

ELEMENT_NAMES = (
    "Variation 1", "Variation 2", "Variation 3", "Variation 4",
    "Intro 1", "Intro 2", "Fill 1", "Fill 2", "Ending 1", "Ending 2",
    "Break", "Intro 3", "Ending 3",
)
TRACK_NAMES = ("Drum", "Perc", "Bass", "Acc 1", "Acc 2", "Acc 3", "Acc 4", "Acc 5")
EXTENSIONS = {".STY": "Style", ".PRF": "Performance", ".PAD": "PAD", ".PCG": "Sound", ".GBL": "Global", ".TBL": "Mape"}
MAX_NODES = 4096


def be32(data: bytes, at: int) -> int:
    if at < 0 or at + 4 > len(data):
        raise FormatError("BE32 polje izlazi izvan objekta")
    return int.from_bytes(data[at:at + 4], "big")


def be16(data: bytes, at: int) -> int:
    if at < 0 or at + 2 > len(data):
        raise FormatError("BE16 polje izlazi izvan objekta")
    return int.from_bytes(data[at:at + 2], "big")


@dataclass(frozen=True)
class Node:
    at: int
    tag: bytes
    size: int

    @property
    def start(self) -> int:
        return self.at + 8

    @property
    def end(self) -> int:
        return self.start + self.size

    def payload(self, blob: bytes) -> bytes:
        return blob[self.start:self.end]


def node_at(blob: bytes, at: int) -> Node:
    if at < 0 or at + 8 > len(blob):
        raise FormatError(f"Nepotpuno zaglavlje objekta na poziciji {at}")
    node = Node(at, blob[at:at + 4], be32(blob, at + 4))
    if node.end > len(blob):
        raise FormatError("Objekt izlazi izvan deklarisane veličine")
    return node


def children(blob: bytes, parent: Node) -> list[Node]:
    position = parent.start
    result = []
    while position < parent.end:
        if len(result) >= MAX_NODES:
            raise FormatError("Previše ugniježđenih resursa")
        child = node_at(blob, position)
        if child.end > parent.end:
            raise FormatError("Dijete izlazi izvan granica roditelja")
        result.append(child)
        position = child.end
    if position != parent.end:
        raise FormatError("Djeca ne ispunjavaju roditeljski objekt")
    return result


def sections(blob: bytes) -> list[Node]:
    """A PRF object consists of successive framed sections, not a root node."""
    result = []
    at = 0
    while at < len(blob):
        if len(result) >= 6:
            raise FormatError("Previše Performance sekcija")
        item = node_at(blob, at)
        result.append(item)
        at = item.end
    if at != len(blob):
        raise FormatError("Performance sekcije se ne završavaju na granici objekta")
    return result


def latin_name(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin1", "replace").strip()


def catalog_names(payload: bytes) -> list[str]:
    if len(payload) % 24:
        raise FormatError("Type-05 katalog nije sastavljen od 24-bajtnih zapisa")
    return [latin_name(payload[pos:pos + 16]) for pos in range(0, len(payload), 24)]


def _parse_cv(blob: bytes, node: Node) -> dict[str, Any]:
    data = node.payload(blob)
    if len(data) < 9:
        raise FormatError("CV zapis je kraći od minimalne geometrije")
    ticks = be32(data, 0)
    stream_size = be32(data, 4)
    tail = 8 + stream_size
    if tail >= len(data):
        raise FormatError("CV event stream izlazi izvan zapisa")
    ref_count = data[tail]
    if len(data) != tail + 1 + 2 * ref_count:
        raise FormatError("CV reference nemaju deklarisanu dužinu")
    refs = [[data[i], data[i + 1]] for i in range(tail + 1, len(data), 2)]
    # CV event payload carries transport/control references; Pattern bytes remain
    # untouched. Count strictly framed events without inventing MIDI semantics.
    stream = data[8:tail]
    at = 0
    absolute_tick = 0
    count = 0
    last_type = None
    while at < len(stream):
        delta = 0
        while at < len(stream) and (stream[at] & 0x80):
            delta = (delta << 7) | (stream[at] & 0x7F)
            at += 1
            if delta > 0x7FFFFFFF:
                raise FormatError("Preveliko vrijeme CV događaja")
        absolute_tick += delta
        if at + 4 > len(stream) or stream[at] != 0x09:
            raise FormatError("Neprepoznato zaglavlje CV događaja")
        last_type = stream[at + 1]
        event_size = (stream[at + 2] << 7) | stream[at + 3]
        at += 4 + event_size
        if at > len(stream):
            raise FormatError("CV događaj izlazi izvan event streama")
        count += 1
        if count > 100000:
            raise FormatError("Previše CV događaja")
    if last_type != 0x2F or absolute_tick != ticks:
        raise FormatError("CV nema valjan EOT ili se TickLength ne poklapa")
    return {"ticks": ticks, "events": count, "references": refs}


class Bank:
    def __init__(self, name: str, source: bytes, sound_catalog: dict[tuple[int, int, int], str] | None = None):
        self.name = Path(name).name
        self.ext = Path(self.name).suffix.upper()
        self.kind = EXTENSIONS.get(self.ext, "Resursi")
        self.source = source
        self.sha256 = sha256(source).hexdigest()
        self.sound_catalog = sound_catalog or {}
        if source[:4] == b"OC20":
            self.blob, self.pages = decode_oc20_file(source)
            self.envelope = "OC20"
        elif source[:4] == bytes.fromhex("01 00 01 14"):
            self.blob = source
            self.pages = []
            self.envelope = "RAW"
        else:
            raise FormatError("Nije prepoznat podržani PA800 resource container")
        root = node_at(self.blob, 0)
        if root.tag != bytes.fromhex("01 00 01 14") or root.end != len(self.blob):
            raise FormatError("Korijen PA800 resursa nema očekivanu oznaku ili dužinu")
        self.nodes = children(self.blob, root)
        if len(self.nodes) < 3 or self.nodes[0].tag[0] != 0x02 or self.nodes[1].tag[0] != 0x05 or self.nodes[-1].tag[0] != 0xFE:
            raise FormatError("Nedostaje header, type-05 katalog ili FE indeks")
        self.resources = self.nodes[2:-1]
        terminal = self.nodes[-1].payload(self.blob)
        if len(terminal) < 12 or terminal[:4] != b"KBEG" or terminal[-8:-4] != b"KEND":
            raise FormatError("KBEG/KEND okvir nije valjan")
        entry_count = be32(terminal, len(terminal) - 4)
        if len(terminal) != 12 + 4 * entry_count:
            raise FormatError("Nevažeća dužina FE indeksa")
        offsets = [be32(terminal, 4 + 4 * i) for i in range(entry_count)]
        actual_offsets = [n.at for n in self.nodes[1:-1]]
        if offsets != actual_offsets:
            raise FormatError("Apsolutne KBEG adrese ne odgovaraju resursima")
        self.names = catalog_names(self.nodes[1].payload(self.blob))
        if len(self.names) != len(self.resources):
            raise FormatError("Broj naziva u katalogu ne odgovara broju resursa")
        self._decoded: dict[int, tuple[bytes, str]] = {}
        self._details: dict[int, dict[str, Any]] = {}

    @property
    def count_by_tag(self) -> dict[str, int]:
        return dict(Counter(f"{item.tag[0]:02X}" for item in self.resources))

    def base_info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "envelope": self.envelope,
            "bytes": len(self.source),
            "decodedBytes": len(self.blob),
            "pages": len(self.pages) if self.pages else 0,
            "resources": len(self.resources),
            "types": self.count_by_tag,
            "sha256": self.sha256,
            "indexValid": True,
            "safety": "READ_ONLY",
        }

    def _decoded_resource(self, index: int) -> tuple[bytes, str]:
        if not 0 <= index < len(self.resources):
            raise FormatError("Resurs ne postoji")
        if index not in self._decoded:
            payload = self.resources[index].payload(self.blob)
            if payload[:4] in (b"OC31", b"OC32"):
                self._decoded[index] = decode_oc3(payload)
            else:
                self._decoded[index] = (payload, "RAW")
        return self._decoded[index]

    def _family(self, node: Node) -> str:
        code = node.tag[0]
        if self.ext == ".STY" and code == 0x06:
            return "Style"
        if self.ext == ".STY" and code == 0x09:
            return "STS companion"
        if self.ext == ".PRF" and code == 0x09:
            return "Performance"
        if self.ext == ".PAD" and code == 0x0C:
            return "PAD"
        if self.ext == ".GBL" and code == 0x0A:
            return "Global"
        if self.ext == ".PCG":
            return "Sound resource"
        return self.kind

    def resource_listing(self) -> list[dict[str, Any]]:
        result = []
        for i, node in enumerate(self.resources):
            payload = node.payload(self.blob)
            codec = payload[:4].decode("ascii") if payload[:4] in (b"OC31", b"OC32") else "RAW"
            item = {
                "index": i,
                "slot": i + 1,
                "name": self.names[i] or f"Resurs {i + 1}",
                "family": self._family(node),
                "tag": node.tag.hex(" ").upper(),
                "codec": codec,
                "bytes": node.size,
            }
            result.append(item)
        return result

    def resource_detail(self, index: int) -> dict[str, Any]:
        if not 0 <= index < len(self.resources):
            raise FormatError("Resurs ne postoji")
        if index in self._details:
            return self._details[index]
        resource = self.resources[index]
        decoded, codec = self._decoded_resource(index)
        base = self.resource_listing()[index]
        detail: dict[str, Any] = dict(base, decodedBytes=len(decoded), decodedSha256=sha256(decoded).hexdigest(), evidence="Struktura/CRC validirani u softveru; izlaz za instrument nije certificiran.")
        if base["family"] in ("Style", "PAD"):
            detail.update(self._style_detail(decoded, base["family"]))
        elif base["family"] == "Performance":
            detail.update(self._performance_detail(decoded))
        else:
            detail["note"] = "Nepoznati listovi ostaju netaknuti; ovaj modul ne nudi binarno uređivanje."
            detail["previewHex"] = decoded[:64].hex(" ").upper()
        self._details[index] = detail
        return detail

    def _style_detail(self, decoded: bytes, family: str) -> dict[str, Any]:
        root = node_at(decoded, 0)
        if root.end != len(decoded):
            raise FormatError("Style/PAD unutrašnji korijen ne pokriva cijeli resurs")
        outer = children(decoded, root)
        expected = 15 if family == "Style" else 3
        if len(outer) < 3 or len(outer) != expected or outer[1].tag[0] != 2:
            raise FormatError("Neočekivana struktura Style/PAD korijena")
        info = outer[0].payload(decoded)
        if not info or 1 + info[0] > len(info):
            raise FormatError("Nevažeća dužina imena u Style/PAD objektu")
        object_name = latin_name(info[1:1 + info[0]])
        pat_nodes = children(decoded, outer[1])
        if not pat_nodes or pat_nodes[0].tag[0] != 1:
            raise FormatError("Nedostaje PatternInfo")
        pat = pat_nodes[0].payload(decoded)
        if len(pat) < 4 or (len(pat) - 4) % 2:
            raise FormatError("Nevažeći PatternInfo")
        pattern_count = be16(pat, 0)
        association_count = be16(pat, 2)
        indices = [be16(pat, at) for at in range(4, len(pat), 2)]
        if pattern_count != len(pat_nodes) - 1 or association_count != len(indices):
            raise FormatError("PatternInfo brojači ne odgovaraju stablu")
        assoc_cursor = 0
        elms = []
        cv_total = 0
        unusual_reference_roles = 0
        for ix, element in enumerate(outer[2:]):
            parts = children(decoded, element)
            if len(parts) < 2:
                raise FormatError("Element nema obavezni Info i TrackInfo")
            track_data = parts[1].payload(decoded)
            if len(track_data) < 48:
                raise FormatError("TrackInfo je kraći od 8 × 6 bajtova")
            tracks = []
            for track_ix in range(8):
                rec = track_data[track_ix * 6:track_ix * 6 + 6]
                msb, lsb, pc = rec[1:4]
                tracks.append({
                    "role": TRACK_NAMES[track_ix],
                    "level": rec[0],
                    "msb": msb, "lsb": lsb, "pc": pc,
                    "low": rec[4], "high": rec[5],
                    "sound": self.sound_catalog.get((msb, lsb, pc)),
                })
            cvs = []
            for cv in parts[2:]:
                if cv.tag[0] != 3:
                    raise FormatError("Neprepoznat CV tip unutar Style/PAD elementa")
                c = _parse_cv(decoded, cv)
                for typ, track in c["references"]:
                    if assoc_cursor >= len(indices):
                        raise FormatError("PatternInfo reference su potrošene prerano")
                    pat_idx = indices[assoc_cursor]
                    assoc_cursor += 1
                    if not 1 <= pat_idx <= pattern_count:
                        raise FormatError("Referenca pokazuje izvan pattern poola")
                    if pat_nodes[pat_idx].tag[0] != typ + 2:
                        raise FormatError("Tip reference ne odgovara pattern tipu")
                    if not ((typ == 0 and track in (0, 1)) or (typ == 1 and track == 2) or (typ in (2, 3) and track in (3, 4, 5, 6, 7))):
                        # This role convention holds for the controlled fixture,
                        # but user Styles may intentionally use a different role.
                        # Preserve and report; don't incorrectly reject the file.
                        unusual_reference_roles += 1
                cvs.append({"number": len(cvs) + 1, "ticks": c["ticks"], "events": c["events"], "references": len(c["references"])})
            cv_total += len(cvs)
            elms.append({
                "name": ELEMENT_NAMES[ix] if family == "Style" and ix < len(ELEMENT_NAMES) else ("PAD element" if family == "PAD" else f"Element {ix + 1}"),
                "infoBytes": parts[0].size,
                "trackBytes": parts[1].size,
                "tracks": tracks,
                "cvs": cvs,
            })
        if assoc_cursor != len(indices):
            raise FormatError("Neiskorištene PatternInfo asocijacije")
        return {
            "innerVersion": root.tag.hex(" ").upper(),
            "objectName": object_name,
            "patterns": pattern_count,
            "associations": association_count,
            "cvCount": cv_total,
            "elementCount": len(elms),
            "elements": elms,
            "warnings": ([f"{unusual_reference_roles} reference imaju neuobičajenu kombinaciju pattern tipa i traga; bajtovi nisu izmijenjeni."] if unusual_reference_roles else []),
        }

    @staticmethod
    def _performance_detail(decoded: bytes) -> dict[str, Any]:
        parts = sections(decoded)
        if len(parts) not in (2, 3):
            raise FormatError("PRF mora imati PerformanceData, opciono Drawbar i CommonInfo")
        variant_map = {1301: "Release 3", 1347: "Release 3 · Drawbar", 1069: "Release 2 · Drum Kit"}
        variant = variant_map.get(len(decoded))
        if variant is None:
            return {"variant": "Nepoznata varijanta", "sections": [{"tag": n.tag.hex(" ").upper(), "bytes": n.size} for n in parts], "note": "Geometrija nije potvrđena; samo čitanje, sirovi bajtovi sačuvani."}
        perf = parts[0].payload(decoded)
        expected_perf = 673 if len(decoded) == 1069 else 865
        expected_common = 380 if len(decoded) == 1069 else 420
        slot_at = 408 if len(decoded) == 1069 else 504
        slot_size = 261 if len(decoded) == 1069 else 357
        if len(perf) != expected_perf or parts[-1].size != expected_common or slot_at + slot_size + 4 != len(perf):
            raise FormatError("PRF Release-2/3 geometrija se ne podudara")
        if len(parts) == 3:
            draw = parts[1].payload(decoded)
            if len(draw) != 38 or draw[:8] != bytes.fromhex("AB CD EF 01 00 00 00 1A") or draw[-4:] != bytes.fromhex("01 AB CD EF"):
                raise FormatError("Digital Drawbar okvir nije potvrđen")
        slot = perf[slot_at:slot_at + slot_size]
        result: dict[str, Any] = {
            "variant": variant,
            "sectionBytes": [n.size for n in parts],
            "slotBytes": slot_size,
            "mixerRecords": 4,
            "globalExtensionBytes": 96 if slot_size == 357 else 0,
            "slotExtensionBytes": 96 if slot_size == 357 else 0,
            "preservation": "Nepoznata polja, FX i detaljni zapisi čuvaju se kao sirovi bajtovi.",
            "mixerHex": [slot[17 + ix * 10:27 + ix * 10].hex(" ").upper() for ix in range(4)],
        }
        if len(parts) == 3:
            body = parts[1].payload(decoded)[8:34]
            result["drawbarCandidates"] = [body[ix] for ix in (3, 5, 7, 9, 11, 13, 15, 17, 18)]
            result["drawbarCaution"] = "Kandidati (0–7), neprovjeren raspored devet UI klizača."
        return result
