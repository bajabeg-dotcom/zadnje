from __future__ import annotations

import struct
from typing import Any


def _rva_mapper(data: Any, pe: dict[str, Any]):
    size = len(data)
    sections = pe.get("sections", [])
    size_headers = pe.get("optional_header", {}).get("size_of_headers", 0)

    def rva_to_offset(rva: int, amount: int = 1) -> int | None:
        if rva < size_headers and rva + amount <= min(size_headers, size):
            return rva
        for section in sections:
            start = section["rva"]
            span = max(section["virtual_size"], section["raw_size"])
            relative = rva - start
            if 0 <= relative < span and relative + amount <= section["available_raw_size"]:
                offset = section["raw_offset"] + relative
                if offset + amount <= size:
                    return offset
        return None

    return rva_to_offset


def _c_string(data: Any, offset: int | None, limit: int = 4096) -> str:
    if offset is None or offset < 0 or offset >= len(data):
        return ""
    end = offset
    stop = min(len(data), offset + limit)
    while end < stop and data[end] != 0:
        end += 1
    return bytes(data[offset:end]).decode("utf-8", errors="replace")


def _delay_imports(data: Any, pe: dict[str, Any], rva_to_offset: Any) -> list[dict[str, Any]]:
    directory = pe.get("optional_header", {}).get("data_directories", {}).get("DELAY_IMPORT", {})
    if not directory.get("rva"):
        return []
    base = rva_to_offset(directory["rva"], 32)
    if base is None:
        return []
    image_base = pe["optional_header"]["image_base"]
    pointer_size = 8 if pe["optional_header"]["bitness"] == 64 else 4
    thunk_format = "<Q" if pointer_size == 8 else "<I"
    ordinal_bit = 1 << (pointer_size * 8 - 1)
    result = []
    for descriptor_index in range(min(directory.get("size", 0) // 32, 4096)):
        offset = base + descriptor_index * 32
        if offset + 32 > len(data):
            break
        attributes, name_value, module_value, iat_value, int_value, bound_value, unload_value, timestamp = struct.unpack_from("<IIIIIIII", data, offset)
        if not any((attributes, name_value, module_value, iat_value, int_value, bound_value, unload_value, timestamp)):
            break

        def to_rva(value: int) -> int:
            if not value:
                return 0
            return value if attributes & 1 else value - image_base if value >= image_base else 0

        name_rva = to_rva(name_value)
        name_offset = rva_to_offset(name_rva)
        thunk_rva = to_rva(int_value or iat_value)
        thunk_offset = rva_to_offset(thunk_rva, pointer_size) if thunk_rva else None
        symbols = []
        if thunk_offset is not None:
            for index in range(8192):
                current = thunk_offset + index * pointer_size
                if current + pointer_size > len(data):
                    break
                value = struct.unpack_from(thunk_format, data, current)[0]
                if not value:
                    break
                if value & ordinal_bit:
                    symbols.append({"ordinal": value & 0xFFFF})
                    continue
                hint_name_rva = to_rva(value & (ordinal_bit - 1))
                hint_name_offset = rva_to_offset(hint_name_rva, 2)
                if hint_name_offset is None:
                    symbols.append({"name": "", "status": "UNMAPPED"})
                else:
                    hint = struct.unpack_from("<H", data, hint_name_offset)[0]
                    symbols.append({"name": _c_string(data, hint_name_offset + 2), "hint": hint})
        result.append({
            "dll": _c_string(data, name_offset), "attributes": attributes,
            "timestamp": timestamp, "symbols": symbols[:2048],
        })
    return result


def _resource_metadata(data: Any, pe: dict[str, Any]) -> dict[str, Any]:
    manifests = []
    versions = []
    for resource in pe.get("resources", []):
        offset = resource.get("offset")
        size = resource.get("size", 0)
        resource_type = resource.get("type", "")
        if offset is None or size <= 0 or offset >= len(data):
            continue
        cap = min(size, 4 * 1024 * 1024, len(data) - offset)
        if "MANIFEST" in resource_type:
            raw = bytes(data[offset:offset + min(cap, 1024 * 1024)])
            encoding = "utf-16le" if raw.startswith(b"\xff\xfe") or raw[:2] == b"<\x00" else "utf-8"
            text = raw.decode(encoding, errors="replace").rstrip("\0")
            manifests.append({"path": resource.get("path"), "size": size, "text": text[:100000], "truncated": size > 100000})
        elif "VERSION" in resource_type:
            raw = bytes(data[offset:offset + cap])
            marker = b"\xbd\x04\xef\xfe"
            marker_offset = raw.find(marker)
            record = {"path": resource.get("path"), "size": size, "fixed_file_info_found": marker_offset >= 0}
            if marker_offset >= 0 and marker_offset + 52 <= len(raw):
                fields = struct.unpack_from("<13I", raw, marker_offset)
                file_ms, file_ls = fields[2], fields[3]
                product_ms, product_ls = fields[4], fields[5]
                record["file_version"] = f"{file_ms >> 16}.{file_ms & 0xFFFF}.{file_ls >> 16}.{file_ls & 0xFFFF}"
                record["product_version"] = f"{product_ms >> 16}.{product_ms & 0xFFFF}.{product_ls >> 16}.{product_ls & 0xFFFF}"
                record["evidence"] = ["VS_FIXEDFILEINFO signature and fixed fields parsed"]
            else:
                record["evidence"] = ["Version resource identified; fixed version block not parsed"]
            versions.append(record)
    return {"manifests": manifests, "version_info": versions}


def parse_pe_extensions(data: Any, pe: dict[str, Any]) -> dict[str, Any]:
    mapper = _rva_mapper(data, pe)
    return {
        "delay_imports": _delay_imports(data, pe, mapper),
        "resource_metadata": _resource_metadata(data, pe),
    }