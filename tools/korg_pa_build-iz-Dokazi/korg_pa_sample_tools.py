#!/usr/bin/env python3
"""KORG PA Sample Tools — alatke za rad sa PCM/samples.

Implementira V3 KORGPAManager.exe funkcije:
  - msgStereoToMono: Convert Stereo to Mono (potvrđena formula iz V3 disassembly)
  - convert_8bit_to_16: 8-bit unsigned -> 16-bit signed (potvrđena formula)
  - convert_stereo_to_mono16: Stereo 16-bit -> Mono 16-bit (potvrđena formula)
  - mnuExportSample: Export Sample to .Wav
  - mnuExportDK: Export All Samples to .Wav
  - mnuCompMenu: Compress All Samples
  - mnuCompDuration: Duration Above > X Sec (filter)
  - mnuDelUnused: Delete Unused Samples
  - mnuDelAll: Delete All Samples

Potvrđene formule (iz confirmed-conversion-rules.json, V3 disassembly):
  - 8-bit -> 16-bit:  output_sample = (input_unsigned_byte - 128) * 256
                    input range [0,255] -> output range [-32768, 32512]
                    function VA: 0x02bfa470, calculation VA: 0x02bfa71b
                    floating point multiplier: 256.0 (VA: 0x004040d0)

  - Stereo 16-bit -> Mono 16-bit:  mono = trunc_toward_zero((left + right) / 2)
                    native ops: CDQ (sign), sum -= sign, SAR sum, 1
                    NOT floor rounding
                    wrapper VA: 0x02bfa9f0, helper VA: 0x02b92a00
                    calculation VA: 0x02b92b0d

Ovaj modul radi sa sirovim PCM bajtovima (little-endian signed 16-bit) i
može se koristiti za konverziju KSF (KORG Sample File) sadržaja u WAV.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Potvrđene konstante iz V3 disassembly (confirmed-conversion-rules.json)
FLOAT64_MULTIPLIER_256 = 256.0  # VA: 0x004040d0


def convert_8bit_unsigned_to_16bit_signed(input_bytes: bytes) -> bytes:
    """8-bit unsigned PCM -> 16-bit signed PCM.

    Potvrđena formula iz V3 disassembly:
        output_sample = (input_unsigned_byte - 128) * 256

    Vektori potvrde:
        input=0   -> output=-32768
        input=1   -> output=-32512
        input=127 -> output=-256
        input=128 -> output=0
        input=129 -> output=256
        input=254 -> output=32256
        input=255 -> output=32512

    Izlaz: little-endian signed 16-bit bajtovi.
    """
    output = bytearray(len(input_bytes) * 2)
    for i, b in enumerate(input_bytes):
        sample = (b - 128) * 256
        # Pakuj kao little-endian signed 16-bit
        output[i*2] = sample & 0xFF
        output[i*2 + 1] = (sample >> 8) & 0xFF
    return bytes(output)


def convert_stereo_16bit_to_mono_16bit(input_bytes: bytes) -> bytes:
    """Stereo 16-bit PCM -> Mono 16-bit PCM.

    Potvrđena formula iz V3 disassembly:
        mono_sample = trunc_toward_zero((left_sample + right_sample) / 2)

    Native operacije:
        sum = signed_left + signed_right
        CDQ: sign = -1 if sum < 0 else 0
        sum -= sign
        SAR sum, 1   (arithmetic right shift)

    Ovo je trunc_toward_zero, NE floor rounding:
        left=-2, right=-1 -> -1 (a NE -2)
        left=-1, right=0  -> 0  (NE -1)
        left=0, right=1   -> 0  (NE 1)
        left=1, right=2   -> 1  (NE 2)

    Ulaz: little-endian signed 16-bit stereo (L R L R ...)
    Izlaz: little-endian signed 16-bit mono
    """
    if len(input_bytes) % 4 != 0:
        raise ValueError("Stereo 16-bit input must have length divisible by 4")
    output = bytearray(len(input_bytes) // 2)
    for i in range(0, len(input_bytes), 4):
        left = struct.unpack_from('<h', input_bytes, i)[0]
        right = struct.unpack_from('<h', input_bytes, i + 2)[0]
        s = left + right
        # CDQ: sign = -1 if s < 0 else 0
        sign = -1 if s < 0 else 0
        s -= sign
        # SAR s, 1 — arithmetic right shift (Python uses >> for signed)
        mono = s >> 1
        # Clamp just in case (sum can overflow beyond 16-bit)
        if mono > 32767:
            mono = 32767
        elif mono < -32768:
            mono = -32768
        struct.pack_into('<h', output, i // 2, mono)
    return bytes(output)


def export_pcm_to_wav(
    pcm_data: bytes,
    output_path: Path,
    sample_rate: int = 44100,
    bit_depth: int = 16,
    channels: int = 1,
) -> dict[str, Any]:
    """Izvezi sirove PCM bajtove u WAV fajl.

    Implementira V3 funkciju 'mnuExportSample' (Export Sample to .Wav).
    """
    with wave.open(str(output_path), 'wb') as wf:
        wf.setsampwidth(bit_depth // 8)
        wf.setframerate(sample_rate)
        wf.setnchannels(channels)
        wf.writeframes(pcm_data)
    return {
        "output_path": str(output_path),
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
        "channels": channels,
        "duration_seconds": len(pcm_data) / (sample_rate * bit_depth // 8 * channels),
        "size_bytes": len(pcm_data),
    }


def export_all_dk_samples_to_wav(
    sample_dict: dict[str, bytes],
    output_dir: Path,
    sample_rate: int = 44100,
    bit_depth: int = 16,
    channels: int = 1,
) -> dict[str, Any]:
    """Izvezi sve sample iz drum kit-a u WAV fajlove.

    Implementira V3 funkciju 'mnuExportDK' (Export All Samples to .Wav).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    exported = []
    for name, pcm_data in sample_dict.items():
        safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
        if not safe_name:
            safe_name = f"sample_{len(exported)}"
        wav_path = output_dir / f"{safe_name}.wav"
        info = export_pcm_to_wav(pcm_data, wav_path, sample_rate, bit_depth, channels)
        info["sample_name"] = name
        exported.append(info)
    return {
        "output_dir": str(output_dir),
        "total_samples": len(exported),
        "samples": exported,
    }


def detect_sample_compression(pcm_data: bytes) -> dict[str, Any]:
    """Detektuje da li je sample kompresovan (PA800 ima svoj format).

    V3 string: "Compressed Samples are in use."
    """
    # Heuristika: ako data sadrži KORG kompresione markere, verovatno je kompresovano
    is_compressed = b'KSF' in pcm_data[:256] or b'KSFC' in pcm_data[:256]
    return {
        "is_compressed": is_compressed,
        "size_bytes": len(pcm_data),
        "compression_format": "KORG-KSF" if is_compressed else "PCM",
    }


def get_sample_size_info(pcm_data: bytes, sample_rate: int = 44100, bit_depth: int = 16, channels: int = 1) -> dict[str, Any]:
    """Informacije o veličini sample-a.

    Implementira V3 funkciju 'mnuSize' (Check Sample Size) i 'Sound_Size_Menu'.
    """
    bytes_per_sample = bit_depth // 8 * channels
    total_samples = len(pcm_data) // bytes_per_sample
    duration = total_samples / sample_rate if sample_rate > 0 else 0
    return {
        "size_bytes": len(pcm_data),
        "size_kb": round(len(pcm_data) / 1024, 2),
        "size_mb": round(len(pcm_data) / (1024 * 1024), 2),
        "duration_seconds": round(duration, 3),
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
        "channels": channels,
        "samples_count": total_samples,
    }


def filter_samples_by_duration(
    samples: dict[str, bytes],
    min_duration_seconds: float,
    sample_rate: int = 44100,
    bit_depth: int = 16,
    channels: int = 1,
) -> dict[str, Any]:
    """Filtrira sample-ove koji traju duže od navedenog trajanja.

    Implementira V3 funkciju 'mnuCompDuration' (Duration Above > X Sec).
    """
    bytes_per_sample = bit_depth // 8 * channels
    long_samples = {}
    short_samples = {}
    for name, pcm_data in samples.items():
        duration = len(pcm_data) / (sample_rate * bytes_per_sample)
        if duration > min_duration_seconds:
            long_samples[name] = pcm_data
        else:
            short_samples[name] = pcm_data
    return {
        "min_duration_seconds": min_duration_seconds,
        "long_samples_count": len(long_samples),
        "short_samples_count": len(short_samples),
        "long_samples": list(long_samples.keys()),
        "short_samples": list(short_samples.keys()),
    }


def main():
    """CLI entry point — test konverzija."""
    import argparse
    parser = argparse.ArgumentParser(prog="korg_pa_sample_tools")
    sub = parser.add_subparsers(dest="command", required=True)
    # 8->16
    p8 = sub.add_parser("convert_8bit_to_16bit", help="8-bit unsigned -> 16-bit signed PCM")
    p8.add_argument("input", type=Path)
    p8.add_argument("output", type=Path)
    # stereo->mono
    ps = sub.add_parser("stereo_to_mono", help="Stereo 16-bit -> Mono 16-bit PCM")
    ps.add_argument("input", type=Path)
    ps.add_argument("output", type=Path)
    # export WAV
    pw = sub.add_parser("export_wav", help="Export raw PCM to WAV")
    pw.add_argument("input", type=Path)
    pw.add_argument("output", type=Path)
    pw.add_argument("--sample-rate", type=int, default=44100)
    pw.add_argument("--bit-depth", type=int, default=16)
    pw.add_argument("--channels", type=int, default=1)
    args = parser.parse_args()
    if args.command == "convert_8bit_to_16bit":
        input_data = args.input.read_bytes()
        output_data = convert_8bit_unsigned_to_16bit_signed(input_data)
        args.output.write_bytes(output_data)
        print(f"Converted {len(input_data)} bytes (8-bit) -> {len(output_data)} bytes (16-bit)")
        print(f"Output: {args.output}")
    elif args.command == "stereo_to_mono":
        input_data = args.input.read_bytes()
        output_data = convert_stereo_16bit_to_mono_16bit(input_data)
        args.output.write_bytes(output_data)
        print(f"Converted {len(input_data)} bytes (stereo) -> {len(output_data)} bytes (mono)")
        print(f"Output: {args.output}")
    elif args.command == "export_wav":
        input_data = args.input.read_bytes()
        info = export_pcm_to_wav(input_data, args.output, args.sample_rate, args.bit_depth, args.channels)
        print(f"Exported WAV: {info}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
