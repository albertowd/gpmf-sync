"""Streaming MP4/QuickTime atom walker.

Reads only atom headers from disk; never loads payloads (mdat) into memory.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO, Iterator

CONTAINER_ATOMS = frozenset({
    b"moov", b"trak", b"mdia", b"minf", b"stbl",
    b"udta", b"edts", b"dinf",
})


@dataclass(frozen=True)
class Atom:
    fourcc: bytes
    start: int          # absolute file offset of the atom (size field)
    size: int           # total atom size including header
    header_size: int    # 8 for 32-bit size, 16 for 64-bit size
    depth: int

    @property
    def payload_offset(self) -> int:
        return self.start + self.header_size

    @property
    def payload_size(self) -> int:
        return self.size - self.header_size

    @property
    def end(self) -> int:
        return self.start + self.size


def _read_header(f: BinaryIO, end: int) -> tuple[int, bytes, int] | None:
    """Read one atom header. Returns (size, fourcc, header_size) or None at boundary."""
    pos = f.tell()
    if pos + 8 > end:
        return None
    raw = f.read(8)
    if len(raw) < 8:
        return None
    size32, fourcc = struct.unpack(">I4s", raw)
    header_size = 8
    if size32 == 1:
        ext = f.read(8)
        if len(ext) < 8:
            return None
        size = struct.unpack(">Q", ext)[0]
        header_size = 16
    elif size32 == 0:
        size = end - pos
    else:
        size = size32
    return size, fourcc, header_size


def walk(f: BinaryIO, start: int = 0, end: int | None = None, depth: int = 0) -> Iterator[Atom]:
    """Yield atoms in [start, end). Recurses into known container atoms.

    Caller may seek freely between yields; the walker re-seeks before reading
    the next sibling.
    """
    if end is None:
        f.seek(0, 2)
        end = f.tell()

    cursor = start
    while cursor < end:
        f.seek(cursor)
        header = _read_header(f, end)
        if header is None:
            break
        size, fourcc, header_size = header
        if size < header_size or cursor + size > end:
            break

        atom = Atom(fourcc=fourcc, start=cursor, size=size,
                    header_size=header_size, depth=depth)
        yield atom

        if fourcc in CONTAINER_ATOMS:
            yield from walk(
                f,
                start=atom.payload_offset,
                end=atom.end,
                depth=depth + 1,
            )

        cursor = atom.end


def find_first(f: BinaryIO, fourcc: bytes, start: int = 0, end: int | None = None) -> Atom | None:
    """Return the first atom matching `fourcc` (depth-first), or None."""
    for atom in walk(f, start=start, end=end):
        if atom.fourcc == fourcc:
            return atom
    return None


def find_all(f: BinaryIO, fourcc: bytes, start: int = 0, end: int | None = None) -> list[Atom]:
    return [a for a in walk(f, start=start, end=end) if a.fourcc == fourcc]
