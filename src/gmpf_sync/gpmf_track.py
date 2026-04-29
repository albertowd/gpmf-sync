"""Resolve GPMF samples within an MP4 file: file-offset and size of each KLV payload."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO, Iterator

from .mp4_meta import (
    TrackInfo,
    parse_co64,
    parse_stco,
    parse_stsc,
    parse_stsz,
)


@dataclass(frozen=True)
class SampleRef:
    file_offset: int
    size: int


def iter_sample_refs(f: BinaryIO, track: TrackInfo) -> Iterator[SampleRef]:
    """Yield (file_offset, size) for each sample in `track`.

    Resolves stco/co64 + stsz + stsc into a stream of sample references.
    """
    if track.stco_atom is not None:
        chunk_offsets = parse_stco(f, track.stco_atom)
    elif track.co64_atom is not None:
        chunk_offsets = parse_co64(f, track.co64_atom)
    else:
        return

    if track.stsz_atom is None:
        return
    uniform_size, sample_sizes = parse_stsz(f, track.stsz_atom)

    if track.stsc_atom is None:
        # Default: 1 sample per chunk
        stsc = [(1, 1, 1)]
    else:
        stsc = parse_stsc(f, track.stsc_atom)
    if not stsc:
        stsc = [(1, 1, 1)]

    sample_idx = 0
    chunk_count = len(chunk_offsets)
    # stsc entries describe runs of chunks; expand lazily.
    for run_idx, (first_chunk, samples_per_chunk, _) in enumerate(stsc):
        next_first_chunk = stsc[run_idx + 1][0] if run_idx + 1 < len(stsc) else chunk_count + 1
        for chunk_idx_1based in range(first_chunk, next_first_chunk):
            chunk_idx = chunk_idx_1based - 1
            if chunk_idx >= chunk_count:
                return
            offset = chunk_offsets[chunk_idx]
            for _ in range(samples_per_chunk):
                if uniform_size:
                    size = uniform_size
                else:
                    if sample_idx >= len(sample_sizes):
                        return
                    size = sample_sizes[sample_idx]
                yield SampleRef(file_offset=offset, size=size)
                offset += size
                sample_idx += 1


def read_sample(f: BinaryIO, ref: SampleRef) -> bytes:
    f.seek(ref.file_offset)
    return f.read(ref.size)
