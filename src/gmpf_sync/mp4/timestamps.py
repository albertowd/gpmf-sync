"""High-level timestamp extraction for GoPro MP4 files."""
from __future__ import annotations

import datetime as _dt
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import BinaryIO

from .atoms import find_first, walk
from .gpmf import find_recursive, iter_entries
from .gpmf_track import iter_sample_refs, read_sample
from .meta import (
    QT_EPOCH_OFFSET,
    TrackInfo,
    collect_tracks,
    parse_mvhd,
)

UTC = _dt.timezone.utc

# How many GPMF samples to scan before giving up on finding a GPS fix.
MAX_GPS_SCAN_SAMPLES = 32


# ---- result types ----------------------------------------------------------

@dataclass
class StampSource:
    name: str
    epoch: float | None = None
    iso: str | None = None
    detail: dict = field(default_factory=dict)

    @classmethod
    def missing(cls, name: str, reason: str) -> "StampSource":
        return cls(name=name, detail={"missing": reason})

    def is_present(self) -> bool:
        return self.epoch is not None


@dataclass
class TimestampReport:
    file: str
    file_size: int
    sources: dict[str, StampSource]
    selected_source: str | None
    selected_epoch: float | None
    selected_iso: str | None

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "file_size": self.file_size,
            "sources": {k: asdict(v) for k, v in self.sources.items()},
            "selected": {
                "source": self.selected_source,
                "epoch": self.selected_epoch,
                "iso": self.selected_iso,
            },
        }


# ---- helpers ---------------------------------------------------------------

def _epoch_to_iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return _dt.datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def _parse_gpsu(raw: bytes) -> float | None:
    """GPSU is ASCII 'yymmddhhmmss.sss', 16 bytes, UTC."""
    s = raw[:16].decode("ascii", errors="replace").strip("\x00 ")
    if len(s) < 12:
        return None
    try:
        yy = int(s[0:2]); mm = int(s[2:4]); dd = int(s[4:6])
        hh = int(s[6:8]); mi = int(s[8:10]); ss = int(s[10:12])
        frac = 0.0
        if len(s) > 12 and s[12] == ".":
            frac = float("0" + s[12:])
        year = 2000 + yy
        dt = _dt.datetime(year, mm, dd, hh, mi, ss, tzinfo=UTC)
        return dt.timestamp() + frac
    except (ValueError, IndexError):
        return None


def _gps_fix_value(payload: bytes) -> int:
    """GPSF is a single uint32_t."""
    if len(payload) < 4:
        return 0
    return struct.unpack(">I", payload[:4])[0]


# ---- per-source extractors -------------------------------------------------

def _extract_mvhd(f: BinaryIO, moov_atom) -> StampSource:
    mvhd_atom = find_first(f, b"mvhd", start=moov_atom.payload_offset, end=moov_atom.end)
    if mvhd_atom is None:
        return StampSource.missing("mvhd", "atom not found")
    mvhd = parse_mvhd(f, mvhd_atom)
    return StampSource(
        name="mvhd",
        epoch=float(mvhd.creation_unix),
        iso=_epoch_to_iso(mvhd.creation_unix),
        detail={
            "modification_unix": mvhd.modification_unix,
            "duration_seconds": mvhd.duration_seconds,
            "warning": "may be local time without TZ marker on some GoPro firmware",
        },
    )


def _extract_mdhd(f: BinaryIO, tracks: list[TrackInfo]) -> StampSource:
    """First video track's mdhd creation time."""
    vide = next((t for t in tracks if t.handler_type == b"vide" and t.mdhd), None)
    if vide is None:
        return StampSource.missing("mdhd", "no video track with mdhd")
    return StampSource(
        name="mdhd",
        epoch=float(vide.mdhd.creation_unix),
        iso=_epoch_to_iso(vide.mdhd.creation_unix),
        detail={"track": "video", "duration_seconds": vide.mdhd.duration_seconds},
    )


def _extract_gps(f: BinaryIO, tracks: list[TrackInfo]) -> StampSource:
    gpmd = next(
        (t for t in tracks if t.handler_type == b"meta" and t.sample_format == b"gpmd"),
        None,
    )
    if gpmd is None:
        return StampSource.missing("gps", "no gpmd metadata track")

    refs = list(iter_sample_refs(f, gpmd))
    if not refs:
        return StampSource.missing("gps", "gpmd track has no samples")

    best_no_fix: tuple[float, int] | None = None  # (epoch, sample_index)

    for idx, ref in enumerate(refs[:MAX_GPS_SCAN_SAMPLES]):
        data = read_sample(f, ref)
        gpsu = find_recursive(data, b"GPSU")
        if gpsu is None:
            continue
        epoch = _parse_gpsu(gpsu.payload)
        if epoch is None:
            continue
        gpsf = find_recursive(data, b"GPSF")
        fix = _gps_fix_value(gpsf.payload) if gpsf else 0
        if fix >= 2:
            return StampSource(
                name="gps",
                epoch=epoch,
                iso=_epoch_to_iso(epoch),
                detail={"fix": fix, "sample_index": idx, "scanned": idx + 1},
            )
        if best_no_fix is None:
            best_no_fix = (epoch, idx)

    if best_no_fix is not None:
        epoch, idx = best_no_fix
        return StampSource(
            name="gps",
            epoch=epoch,
            iso=_epoch_to_iso(epoch),
            detail={"fix": 0, "sample_index": idx, "scanned": min(len(refs), MAX_GPS_SCAN_SAMPLES),
                    "warning": "GPSU read without GPS fix; clock may be approximate"},
        )

    return StampSource.missing("gps", "no GPSU found in scanned samples")


def _extract_cdat(f: BinaryIO, moov_atom) -> StampSource:
    """CDAT lives in udta/GPMF as KLV. Per docs, it's local-time epoch seconds."""
    udta = find_first(f, b"udta", start=moov_atom.payload_offset, end=moov_atom.end)
    if udta is None:
        return StampSource.missing("cdat", "no udta atom")
    udta_gpmf = find_first(f, b"GPMF", start=udta.payload_offset, end=udta.end)
    if udta_gpmf is None:
        return StampSource.missing("cdat", "no GPMF atom in udta")
    f.seek(udta_gpmf.payload_offset)
    blob = f.read(udta_gpmf.payload_size)
    cdat = find_recursive(blob, b"CDAT")
    if cdat is None or not cdat.payload:
        return StampSource.missing("cdat", "no CDAT key in udta GPMF")
    n = cdat.struct_size
    if n == 8:
        epoch = struct.unpack(">Q", cdat.payload[:8])[0]
    elif n == 4:
        epoch = struct.unpack(">I", cdat.payload[:4])[0]
    else:
        return StampSource.missing("cdat", f"unexpected CDAT struct_size {n}")
    return StampSource(
        name="cdat",
        epoch=float(epoch),
        iso=_epoch_to_iso(epoch),
        detail={"warning": "CDAT is documented as local-time epoch (no TZ)"},
    )


def _extract_camera_info(f: BinaryIO, moov_atom) -> dict:
    """Pull FIRM (firmware), CAME (camera model), MUID/GUMI from udta."""
    info: dict = {}
    udta = find_first(f, b"udta", start=moov_atom.payload_offset, end=moov_atom.end)
    if udta is None:
        return info
    for child in walk(f, start=udta.payload_offset, end=udta.end):
        if child.depth != 0:
            continue
        if child.fourcc in (b"FIRM", b"CAME", b"LENS"):
            f.seek(child.payload_offset)
            raw = f.read(child.payload_size)
            try:
                info[child.fourcc.decode()] = raw.decode("utf-8").strip("\x00").strip()
            except UnicodeDecodeError:
                info[child.fourcc.decode()] = raw.hex()
    return info


# ---- main entry point ------------------------------------------------------

DEFAULT_AUTO_ORDER = ("gps", "mvhd", "mdhd", "cdat")


def extract_timestamps(path: str | Path, source: str = "auto") -> TimestampReport:
    """Extract timestamps from a GoPro MP4 file.

    `source` is one of: "auto", "gps", "mvhd", "mdhd", "cdat", "all".
    `auto` returns the first present source in DEFAULT_AUTO_ORDER.
    `all` populates every source.
    """
    p = Path(path)
    file_size = p.stat().st_size
    sources: dict[str, StampSource] = {}

    with p.open("rb") as f:
        moov = find_first(f, b"moov")
        if moov is None:
            raise ValueError(f"{p}: no moov atom (not an MP4/MOV?)")

        tracks = collect_tracks(f, moov)

        wants = {source} if source not in ("auto", "all") else set(DEFAULT_AUTO_ORDER)

        if "mvhd" in wants:
            sources["mvhd"] = _extract_mvhd(f, moov)
        if "mdhd" in wants:
            sources["mdhd"] = _extract_mdhd(f, tracks)
        if "gps" in wants:
            sources["gps"] = _extract_gps(f, tracks)
        if "cdat" in wants:
            sources["cdat"] = _extract_cdat(f, moov)

        camera = _extract_camera_info(f, moov)

    selected_name: str | None = None
    if source in ("auto", "all"):
        for name in DEFAULT_AUTO_ORDER:
            s = sources.get(name)
            if s and s.is_present():
                selected_name = name
                break
    else:
        s = sources.get(source)
        if s and s.is_present():
            selected_name = source

    selected = sources.get(selected_name) if selected_name else None
    report = TimestampReport(
        file=str(p),
        file_size=file_size,
        sources=sources,
        selected_source=selected_name,
        selected_epoch=selected.epoch if selected else None,
        selected_iso=selected.iso if selected else None,
    )
    if camera:
        # Stash camera info under a dedicated pseudo-source.
        report.sources["_camera"] = StampSource(name="_camera", detail=camera)
    return report
