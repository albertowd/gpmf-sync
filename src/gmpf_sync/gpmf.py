"""GPMF (GoPro Metadata Format) KLV parser.

Spec: GPMF is a 32-bit-aligned Key-Length-Value stream. Each entry is:
    4 bytes: FourCC key
    1 byte:  type char (ASCII; 0x00 means "nested container")
    1 byte:  struct size (bytes per sample)
    2 bytes: repeat count (big-endian)
    N bytes: data, where N = struct_size * repeat, padded up to 4-byte alignment.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class GpmfEntry:
    key: bytes
    type_char: int      # 0 for nested
    struct_size: int
    repeat: int
    payload: bytes      # raw, big-endian, unpadded

    @property
    def is_nested(self) -> bool:
        return self.type_char == 0

    @property
    def total_size(self) -> int:
        n = self.struct_size * self.repeat
        return 8 + ((n + 3) & ~3)


def iter_entries(data: bytes, offset: int = 0, end: int | None = None) -> Iterator[GpmfEntry]:
    """Iterate top-level KLV entries in [offset, end)."""
    if end is None:
        end = len(data)
    i = offset
    while i + 8 <= end:
        key = data[i:i + 4]
        type_char = data[i + 4]
        struct_size = data[i + 5]
        repeat = struct.unpack(">H", data[i + 6:i + 8])[0]
        n = struct_size * repeat
        if i + 8 + n > end:
            return
        payload = data[i + 8:i + 8 + n]
        yield GpmfEntry(key=key, type_char=type_char,
                        struct_size=struct_size, repeat=repeat,
                        payload=payload)
        i += 8 + ((n + 3) & ~3)


def find_recursive(data: bytes, target: bytes) -> GpmfEntry | None:
    """Depth-first search for `target` FourCC, descending into nested containers."""
    for entry in iter_entries(data):
        if entry.key == target:
            return entry
        if entry.is_nested:
            found = find_recursive(entry.payload, target)
            if found is not None:
                return found
    return None


def find_all_recursive(data: bytes, target: bytes) -> list[GpmfEntry]:
    out: list[GpmfEntry] = []
    for entry in iter_entries(data):
        if entry.key == target:
            out.append(entry)
        if entry.is_nested:
            out.extend(find_all_recursive(entry.payload, target))
    return out
