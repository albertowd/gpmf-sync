"""Parse mvhd, mdhd, hdlr, stsd, stco/co64, stsz, stsc atoms."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO

from .atoms import Atom, walk

# Seconds between QuickTime epoch (1904-01-01 UTC) and Unix epoch (1970-01-01 UTC).
QT_EPOCH_OFFSET = 2_082_844_800


@dataclass(frozen=True)
class MovieHeader:
    creation_unix: int
    modification_unix: int
    timescale: int
    duration: int

    @property
    def duration_seconds(self) -> float:
        return self.duration / self.timescale if self.timescale else 0.0


def _read_version_flags(f: BinaryIO) -> int:
    return f.read(1)[0]  # version; flags discarded


def parse_mvhd(f: BinaryIO, atom: Atom) -> MovieHeader:
    f.seek(atom.payload_offset)
    version = _read_version_flags(f)
    f.read(3)  # flags
    if version == 1:
        creation, modification, timescale, duration = struct.unpack(">QQIQ", f.read(28))
    else:
        creation, modification, timescale, duration = struct.unpack(">IIII", f.read(16))
    return MovieHeader(
        creation_unix=creation - QT_EPOCH_OFFSET,
        modification_unix=modification - QT_EPOCH_OFFSET,
        timescale=timescale,
        duration=duration,
    )


def parse_mdhd(f: BinaryIO, atom: Atom) -> MovieHeader:
    """Same layout as mvhd up through duration."""
    return parse_mvhd(f, atom)


def parse_hdlr_type(f: BinaryIO, atom: Atom) -> bytes:
    """Returns the 4-byte handler_type (e.g. b'meta', b'vide', b'soun')."""
    f.seek(atom.payload_offset + 8)  # skip version+flags(4) + pre_defined(4)
    return f.read(4)


def parse_stsd_first_format(f: BinaryIO, atom: Atom) -> bytes:
    """Returns the FourCC of the first sample-description entry (e.g. b'gpmd')."""
    f.seek(atom.payload_offset + 4)  # skip version+flags
    entry_count = struct.unpack(">I", f.read(4))[0]
    if entry_count == 0:
        return b""
    # Each entry: 4 bytes size + 4 bytes format + ...
    f.read(4)  # entry size
    return f.read(4)


def parse_stco(f: BinaryIO, atom: Atom) -> list[int]:
    """Read 32-bit chunk offsets."""
    f.seek(atom.payload_offset + 4)
    count = struct.unpack(">I", f.read(4))[0]
    if count == 0:
        return []
    return list(struct.unpack(f">{count}I", f.read(count * 4)))


def parse_co64(f: BinaryIO, atom: Atom) -> list[int]:
    """Read 64-bit chunk offsets."""
    f.seek(atom.payload_offset + 4)
    count = struct.unpack(">I", f.read(4))[0]
    if count == 0:
        return []
    return list(struct.unpack(f">{count}Q", f.read(count * 8)))


def parse_stsz(f: BinaryIO, atom: Atom) -> tuple[int, list[int]]:
    """Returns (uniform_size, sizes). If uniform_size != 0, all samples are that size."""
    f.seek(atom.payload_offset + 4)
    sample_size, sample_count = struct.unpack(">II", f.read(8))
    if sample_size != 0:
        return sample_size, []
    if sample_count == 0:
        return 0, []
    sizes = list(struct.unpack(f">{sample_count}I", f.read(sample_count * 4)))
    return 0, sizes


def parse_stsc(f: BinaryIO, atom: Atom) -> list[tuple[int, int, int]]:
    """Returns list of (first_chunk, samples_per_chunk, sample_description_index)."""
    f.seek(atom.payload_offset + 4)
    count = struct.unpack(">I", f.read(4))[0]
    if count == 0:
        return []
    raw = f.read(count * 12)
    out = []
    for i in range(count):
        out.append(struct.unpack(">III", raw[i * 12:(i + 1) * 12]))
    return out


@dataclass(frozen=True)
class TrackInfo:
    trak_atom: Atom
    handler_type: bytes
    sample_format: bytes
    mdhd: MovieHeader | None
    stco_atom: Atom | None
    co64_atom: Atom | None
    stsz_atom: Atom | None
    stsc_atom: Atom | None


def collect_tracks(f: BinaryIO, moov: Atom) -> list[TrackInfo]:
    """Walk all trak atoms within moov; collect their key sub-atoms."""
    tracks: list[TrackInfo] = []
    for trak in walk(f, start=moov.payload_offset, end=moov.end):
        if trak.fourcc != b"trak" or trak.depth != 0:
            continue
        handler = b""
        sample_format = b""
        mdhd = None
        stco_atom = co64_atom = stsz_atom = stsc_atom = None

        for child in walk(f, start=trak.payload_offset, end=trak.end):
            if child.fourcc == b"hdlr":
                handler = parse_hdlr_type(f, child)
            elif child.fourcc == b"mdhd":
                mdhd = parse_mdhd(f, child)
            elif child.fourcc == b"stsd":
                sample_format = parse_stsd_first_format(f, child)
            elif child.fourcc == b"stco":
                stco_atom = child
            elif child.fourcc == b"co64":
                co64_atom = child
            elif child.fourcc == b"stsz":
                stsz_atom = child
            elif child.fourcc == b"stsc":
                stsc_atom = child

        tracks.append(TrackInfo(
            trak_atom=trak,
            handler_type=handler,
            sample_format=sample_format,
            mdhd=mdhd,
            stco_atom=stco_atom,
            co64_atom=co64_atom,
            stsz_atom=stsz_atom,
            stsc_atom=stsc_atom,
        ))
    return tracks
