from __future__ import annotations

import importlib.util
import math
import mmap
import re
import struct
from collections import Counter
from typing import Any


class PEParseError(ValueError):
    pass


DIRECTORY_NAMES = (
    "EXPORT", "IMPORT", "RESOURCE", "EXCEPTION", "SECURITY", "BASERELOC", "DEBUG", "ARCHITECTURE",
    "GLOBALPTR", "TLS", "LOAD_CONFIG", "BOUND_IMPORT", "IAT", "DELAY_IMPORT", "CLR", "RESERVED",
)


def _entropy_region(data: mmap.mmap | bytes, start: int, size: int) -> float:
    counts: Counter[int] = Counter()
    end = min(len(data), start + size)
    for offset in range(start, end, 1024 * 1024):
        counts.update(data[offset:min(end, offset + 1024 * 1024)])
    total = sum(counts.values())
    if not total:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def parse_pe(data: mmap.mmap | bytes) -> dict[str, Any]:
    size = len(data)

    def need(offset: int, length: int, label: str) -> None:
        if offset < 0 or length < 0 or offset + length > size:
            raise PEParseError(f"Truncated PE while reading {label} at 0x{offset:X}")

    def u16(offset: int, label: str) -> int:
        need(offset, 2, label)
        return struct.unpack_from("<H", data, offset)[0]

    def u32(offset: int, label: str) -> int:
        need(offset, 4, label)
        return struct.unpack_from("<I", data, offset)[0]

    if size < 0x40 or bytes(data[:2]) != b"MZ":
        raise PEParseError("Missing or truncated DOS header")
    pe_offset = u32(0x3C, "DOS e_lfanew")
    need(pe_offset, 24, "PE and COFF headers")
    if bytes(data[pe_offset:pe_offset + 4]) != b"PE\0\0":
        raise PEParseError("PE signature not found at DOS e_lfanew")

    coff = pe_offset + 4
    machine, number_sections = struct.unpack_from("<HH", data, coff)
    timestamp, symbol_table, symbol_count = struct.unpack_from("<III", data, coff + 4)
    optional_size, characteristics = struct.unpack_from("<HH", data, coff + 16)
    optional = coff + 20
    need(optional, optional_size, "Optional Header")
    if optional_size < 2:
        raise PEParseError("Optional Header is empty")
    magic = u16(optional, "Optional Header magic")
    if magic == 0x10B:
        bitness, image_base_offset, count_offset, directories_offset, pointer_size = 32, 28, 92, 96, 4
        image_base = struct.unpack_from("<I", data, optional + image_base_offset)[0] if optional_size >= 32 else 0
    elif magic == 0x20B:
        bitness, image_base_offset, count_offset, directories_offset, pointer_size = 64, 24, 108, 112, 8
        image_base = struct.unpack_from("<Q", data, optional + image_base_offset)[0] if optional_size >= 32 else 0
    else:
        raise PEParseError(f"Unsupported Optional Header magic 0x{magic:04X}")
    if optional_size < count_offset + 4:
        raise PEParseError("Optional Header is too short for data-directory count")

    entry_rva = u32(optional + 16, "AddressOfEntryPoint")
    section_alignment = u32(optional + 32, "SectionAlignment")
    file_alignment = u32(optional + 36, "FileAlignment")
    size_image = u32(optional + 56, "SizeOfImage")
    size_headers = u32(optional + 60, "SizeOfHeaders")
    subsystem = u16(optional + 68, "Subsystem")
    dll_characteristics = u16(optional + 70, "DllCharacteristics")
    directory_count = min(u32(optional + count_offset, "NumberOfRvaAndSizes"), 16)
    available_directories = max(0, (optional_size - directories_offset) // 8)
    directory_count = min(directory_count, available_directories)
    directories: dict[str, dict[str, int]] = {}
    for index in range(directory_count):
        rva, directory_size = struct.unpack_from("<II", data, optional + directories_offset + index * 8)
        directories[DIRECTORY_NAMES[index]] = {"rva": rva, "size": directory_size}

    section_start = optional + optional_size
    need(section_start, number_sections * 40, "Section Table")
    sections = []
    for index in range(number_sections):
        offset = section_start + index * 40
        raw_name = bytes(data[offset:offset + 8]).split(b"\0", 1)[0]
        name = raw_name.decode("ascii", errors="replace")
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from("<IIII", data, offset + 8)
        section_characteristics = struct.unpack_from("<I", data, offset + 36)[0]
        raw_end = raw_offset + raw_size
        mapped_raw_size = max(0, min(raw_size, size - raw_offset)) if raw_offset <= size else 0
        permissions = "".join(("R" if section_characteristics & 0x40000000 else "", "W" if section_characteristics & 0x80000000 else "", "X" if section_characteristics & 0x20000000 else ""))
        section = {
            "name": name, "rva": virtual_address, "virtual_size": virtual_size,
            "raw_offset": raw_offset, "raw_size": raw_size, "available_raw_size": mapped_raw_size,
            "permissions": permissions or "-", "characteristics": section_characteristics,
            "entropy": round(_entropy_region(data, raw_offset, mapped_raw_size), 4),
            "truncated": mapped_raw_size != raw_size,
        }
        section["raw_end"] = raw_end
        sections.append(section)

    def rva_to_offset(rva: int, amount: int = 1) -> int | None:
        if rva < size_headers and rva + amount <= min(size_headers, size):
            return rva
        for section in sections:
            start = section["rva"]
            span = max(section["virtual_size"], section["raw_size"])
            if start <= rva < start + span:
                relative = rva - start
                if relative + amount <= section["available_raw_size"]:
                    candidate = section["raw_offset"] + relative
                    if candidate + amount <= size:
                        return candidate
        return None

    def c_string(offset: int, maximum: int = 4096) -> str:
        if offset < 0 or offset >= size:
            return ""
        stop = min(size, offset + maximum)
        end = offset
        while end < stop and data[end] != 0:
            end += 1
        return bytes(data[offset:end]).decode("utf-8", errors="replace")

    entry_offset = rva_to_offset(entry_rva, 1) if entry_rva else None
    entry_bytes = bytes(data[entry_offset:min(size, entry_offset + 256)]) if entry_offset is not None else b""
    entry_section = next((item["name"] for item in sections if item["rva"] <= entry_rva < item["rva"] + max(item["virtual_size"], item["raw_size"])), None)
    executable_entry = next((item for item in sections if item["name"] == entry_section and "X" in item["permissions"]), None)

    imports: list[dict[str, Any]] = []
    imp_dir = directories.get("IMPORT", {})
    imp_off = rva_to_offset(imp_dir.get("rva", 0), 20) if imp_dir.get("rva") else None
    if imp_off is not None:
        for descriptor_index in range(4096):
            off = imp_off + descriptor_index * 20
            if off + 20 > size:
                break
            original_thunk, _, _, name_rva, first_thunk = struct.unpack_from("<IIIII", data, off)
            if not any((original_thunk, name_rva, first_thunk)):
                break
            name_off = rva_to_offset(name_rva, 1)
            dll_name = c_string(name_off) if name_off is not None else ""
            thunk_rva = original_thunk or first_thunk
            thunk_off = rva_to_offset(thunk_rva, pointer_size) if thunk_rva else None
            imported = []
            if thunk_off is not None:
                fmt = "<Q" if pointer_size == 8 else "<I"
                ordinal_flag = 1 << (pointer_size * 8 - 1)
                for thunk_index in range(8192):
                    item_off = thunk_off + thunk_index * pointer_size
                    if item_off + pointer_size > size:
                        break
                    value = struct.unpack_from(fmt, data, item_off)[0]
                    if value == 0:
                        break
                    if value & ordinal_flag:
                        imported.append({"ordinal": value & 0xFFFF})
                    else:
                        hint_name = rva_to_offset(value & (ordinal_flag - 1), 2)
                        imported.append({"name": c_string(hint_name + 2) if hint_name is not None else "", "hint": u16(hint_name, "Import hint") if hint_name is not None else None})
            imports.append({"dll": dll_name, "thunk_rva": thunk_rva, "symbols": imported[:2048]})

    exports: list[dict[str, Any]] = []
    exp_dir = directories.get("EXPORT", {})
    exp_off = rva_to_offset(exp_dir.get("rva", 0), 40) if exp_dir.get("rva") else None
    export_meta = None
    if exp_off is not None:
        fields = struct.unpack_from("<IIHHIIIIIII", data, exp_off)
        _, _, _, _, name_rva, ordinal_base, function_count, name_count, functions_rva, names_rva, ordinals_rva = fields
        dll_off = rva_to_offset(name_rva, 1)
        fn_off = rva_to_offset(functions_rva, min(function_count, 100000) * 4) if function_count else None
        nm_off = rva_to_offset(names_rva, min(name_count, 100000) * 4) if name_count else None
        ord_off = rva_to_offset(ordinals_rva, min(name_count, 100000) * 2) if name_count else None
        export_meta = {"dll_name": c_string(dll_off) if dll_off is not None else "", "ordinal_base": ordinal_base, "function_count": function_count, "name_count": name_count}
        if nm_off is not None and ord_off is not None:
            for index in range(min(name_count, 50000)):
                name_rva_item = u32(nm_off + index * 4, "Export name RVA")
                ordinal_index = u16(ord_off + index * 2, "Export ordinal index")
                name_offset = rva_to_offset(name_rva_item, 1)
                function_rva = u32(fn_off + ordinal_index * 4, "Export function RVA") if fn_off is not None and ordinal_index < function_count else 0
                exports.append({"name": c_string(name_offset) if name_offset is not None else "", "ordinal": ordinal_base + ordinal_index, "rva": function_rva, "forwarder": bool(exp_dir.get("rva", 0) <= function_rva < exp_dir.get("rva", 0) + exp_dir.get("size", 0))})

    resources: list[dict[str, Any]] = []
    resource_dir = directories.get("RESOURCE", {})
    resource_root = rva_to_offset(resource_dir.get("rva", 0), 16) if resource_dir.get("rva") else None
    resource_names: list[str] = []
    resource_types = {1: "CURSOR", 2: "BITMAP", 3: "ICON", 4: "MENU", 5: "DIALOG", 6: "STRING", 10: "RCDATA", 14: "GROUP_ICON", 16: "VERSION", 24: "MANIFEST"}
    if resource_root is not None:
        visited = 0

        def walk_resources(relative: int, path: list[str], depth: int) -> None:
            nonlocal visited
            if depth > 3 or visited >= 5000:
                return
            directory_offset = resource_root + relative
            if directory_offset + 16 > size:
                return
            named_count, id_count = struct.unpack_from("<HH", data, directory_offset + 12)
            entry_count = min(named_count + id_count, 4096)
            for child_index in range(entry_count):
                visited += 1
                entry_offset = directory_offset + 16 + child_index * 8
                if entry_offset + 8 > size:
                    return
                name_value, target_value = struct.unpack_from("<II", data, entry_offset)
                if name_value & 0x80000000:
                    name_relative = name_value & 0x7FFFFFFF
                    name_offset = resource_root + name_relative
                    if name_offset + 2 <= size:
                        char_count = u16(name_offset, "Resource name length")
                        end = min(size, name_offset + 2 + char_count * 2)
                        label = bytes(data[name_offset + 2:end]).decode("utf-16le", errors="replace")
                    else:
                        label = "<invalid-name>"
                else:
                    resource_id = name_value & 0xFFFF
                    label = resource_types.get(resource_id, str(resource_id)) if depth == 0 else str(resource_id)
                next_path = path + [label]
                if target_value & 0x80000000:
                    walk_resources(target_value & 0x7FFFFFFF, next_path, depth + 1)
                    continue
                data_entry = resource_root + target_value
                if data_entry + 16 > size:
                    continue
                data_rva, data_size, codepage, _ = struct.unpack_from("<IIII", data, data_entry)
                payload_offset = rva_to_offset(data_rva, min(data_size, 1)) if data_size else rva_to_offset(data_rva, 0)
                resource = {"path": next_path, "type": next_path[0] if next_path else "UNKNOWN", "rva": data_rva, "offset": payload_offset, "size": data_size, "codepage": codepage}
                resources.append(resource)
                resource_names.append("/".join(next_path))

        walk_resources(0, [], 0)

    security = directories.get("SECURITY", {"rva": 0, "size": 0})
    certificate = None
    cert_offset, cert_size = security["rva"], security["size"]
    if cert_offset and cert_size:
        certificate = {"file_offset": cert_offset, "size": cert_size, "in_bounds": cert_offset + cert_size <= size}

    overlay_start = max([size_headers, *(section["raw_end"] for section in sections)], default=size_headers)
    overlay = None
    if overlay_start < size:
        overlay = {"start": overlay_start, "size": size - overlay_start, "end": size, "includes_certificate_table": bool(certificate and certificate["file_offset"] >= overlay_start)}

    debug_entries = []
    debug_directory = directories.get("DEBUG", {})
    debug_off = rva_to_offset(debug_directory.get("rva", 0), 28) if debug_directory.get("rva") else None
    if debug_off is not None:
        count = min(debug_directory.get("size", 0) // 28, 1024)
        for index in range(count):
            fields = struct.unpack_from("<IIHHIIII", data, debug_off + index * 28)
            _, timestamp_item, major, minor, debug_type, data_size, data_rva, data_pointer = fields
            debug_entries.append({"timestamp": timestamp_item, "major": major, "minor": minor, "type": debug_type, "size": data_size, "rva": data_rva, "file_offset": data_pointer})

    tls = None
    tls_directory = directories.get("TLS", {})
    tls_size = 24 if bitness == 32 else 40
    tls_off = rva_to_offset(tls_directory.get("rva", 0), tls_size) if tls_directory.get("rva") else None
    if tls_off is not None:
        fmt = "<IIIIII" if bitness == 32 else "<QQQQII"
        tls_values = struct.unpack_from(fmt, data, tls_off)
        tls = {"raw_fields": [int(value) for value in tls_values]}

    relocations = []
    reloc_directory = directories.get("BASERELOC", {})
    reloc_off = rva_to_offset(reloc_directory.get("rva", 0), 8) if reloc_directory.get("rva") else None
    if reloc_off is not None:
        cursor = reloc_off
        end = min(size, reloc_off + reloc_directory.get("size", 0))
        while cursor + 8 <= end and len(relocations) < 10000:
            page_rva, block_size = struct.unpack_from("<II", data, cursor)
            if block_size < 8 or cursor + block_size > end:
                break
            entries = (block_size - 8) // 2
            for index in range(entries):
                item = u16(cursor + 8 + index * 2, "Relocation entry")
                relocations.append({"page_rva": page_rva, "type": item >> 12, "offset": item & 0xFFF, "target_rva": page_rva + (item & 0xFFF)})
                if len(relocations) >= 10000:
                    break
            cursor += block_size

    packer_candidates = []
    suspicious_sections = [section for section in sections if re.search(r"upx|aspack|pec|themida|vmp|packed", section["name"], re.I)]
    if suspicious_sections:
        packer_candidates.append({"name": "packer-like section naming", "evidence": [f"section names: {', '.join(section['name'] for section in suspicious_sections)}"], "confidence": 0.55})
    high_entropy = [section for section in sections if section["raw_size"] >= 1024 and section["entropy"] >= 7.2]
    if high_entropy:
        packer_candidates.append({"name": "high-entropy PE section(s)", "evidence": [f"sections: {', '.join(section['name'] for section in high_entropy)}", "high entropy is not proof of packing or encryption"], "confidence": 0.4})
    if overlay and overlay["size"] >= 4096:
        packer_candidates.append({"name": "large overlay", "evidence": [f"overlay size: {overlay['size']} bytes"], "confidence": 0.25})
    if entry_rva and not executable_entry:
        packer_candidates.append({"name": "entry point outside executable section", "evidence": [f"entry RVA 0x{entry_rva:X}", f"mapped section: {entry_section or 'none'}"], "confidence": 0.55})
    if len(packer_candidates) >= 2:
        packer_status = "LIKELY"
    elif packer_candidates:
        packer_status = "CANDIDATE"
    else:
        packer_status = "NOT_DETECTED"

    disassembly: dict[str, Any] = {"status": "NOT_AVAILABLE", "reason": "Capstone is not installed or entry point could not be mapped"}
    if entry_offset is not None and importlib.util.find_spec("capstone"):
        try:
            import capstone
            mode = capstone.CS_MODE_64 if bitness == 64 else capstone.CS_MODE_32
            engine = capstone.Cs(capstone.CS_ARCH_X86, mode)
            instructions = [{"address": item.address, "mnemonic": item.mnemonic, "operands": item.op_str} for item in engine.disasm(entry_bytes, image_base + entry_rva, count=64)]
            disassembly = {"status": "CANDIDATE_ANALYSIS", "instructions": instructions}
        except Exception as error:  # Optional dependency failures must not stop parsing.
            disassembly = {"status": "UNAVAILABLE", "reason": str(error)}

    machine_names = {0x14C: "x86", 0x8664: "x64", 0x1C0: "ARM", 0xAA64: "ARM64", 0x1C4: "ARMNT"}
    return {
        "status": "PARSED",
        "dos_header": {"magic": "MZ", "pe_offset": pe_offset},
        "coff_header": {
            "machine": machine, "machine_name": machine_names.get(machine, "UNKNOWN"), "number_of_sections": number_sections,
            "timestamp_unix": timestamp, "symbol_table_offset": symbol_table, "symbol_count": symbol_count,
            "characteristics": characteristics,
        },
        "optional_header": {
            "magic": magic, "bitness": bitness, "image_base": image_base, "entry_point_rva": entry_rva,
            "section_alignment": section_alignment, "file_alignment": file_alignment, "size_of_image": size_image,
            "size_of_headers": size_headers, "subsystem": subsystem, "dll_characteristics": dll_characteristics,
            "data_directories": directories,
        },
        "sections": sections,
        "entry_point": {
            "rva": entry_rva, "file_offset": entry_offset,
            "virtual_address": image_base + entry_rva if entry_rva else None, "section": entry_section,
            "outside_executable_section": bool(entry_rva and not executable_entry),
            "hex": entry_bytes.hex(" "), "disassembly": disassembly,
        },
        "imports": imports,
        "exports": {"metadata": export_meta, "symbols": exports},
        "resources": resources,
        "resource_names": resource_names,
        "tls": tls,
        "relocations": relocations,
        "debug_directory": debug_entries,
        "security_directory": certificate,
        "overlay": overlay,
        "packer_detection": {"status": packer_status, "candidates": packer_candidates},
        "rva_to_offset_available": True,
    }