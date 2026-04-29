"""Cross-format timestamp comparison.

Given a mixed list of files (MP4 GoPro footage, TCX activity files, RaceChrono
v3 CSV logs), produce a normalized first-timestamp per file and compute the
offset of each non-reference file relative to a chosen reference. The offset
tells you how to align the files inside another tool (NLE, telemetry overlay,
etc.) — it does not modify any file.

GoPro MP4s can carry several creation timestamps (`gps`, `mvhd`, `mdhd`,
`cdat`) which sometimes disagree due to GoPro's local-time-as-UTC firmware
quirk. When they differ, we surface every distinct candidate so the caller
can pick the timezone interpretation that matches the other files.

Convention (mirrors the gpmf-sync-info Deno reference):
- If the reference (GoPro) timestamp is **later** than the other file, the
  other file started *before* the GoPro. To align, you must **trim** the
  beginning of the other file by the delta.
- If the reference is **earlier** than the other file, the other file
  started *after* the GoPro. To align, you must **offset** (delay) the
  other file by the delta.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .external import rc_csv, tcx
from .mp4.timestamps import DEFAULT_AUTO_ORDER, extract_timestamps

UTC = _dt.timezone.utc


# Extension → kind. Kept lowercase; lookups normalise.
_EXT_KIND = {
    ".mp4": "mp4",
    ".mov": "mp4",
    ".tcx": "tcx",
    ".csv": "csv",
}


@dataclass
class Candidate:
    """One distinct timestamp interpretation for a file.

    For MP4s, multiple candidates can coexist (e.g. ``mvhd`` and ``cdat``
    differing by a whole-hour TZ offset). For TCX/CSV there is at most one.
    """
    source: str
    epoch: float
    iso: str


@dataclass
class FileTimestamp:
    file: str
    kind: str  # "mp4", "tcx", "csv", or "unknown"
    epoch: float | None = None       # primary (first present in DEFAULT_AUTO_ORDER for MP4)
    iso: str | None = None
    primary_source: str | None = None  # "gps"/"mvhd"/"mdhd"/"cdat", "tcx", "csv"
    candidates: list[Candidate] = field(default_factory=list)  # ALL distinct (incl. primary)
    detail: dict = field(default_factory=dict)

    def is_present(self) -> bool:
        return self.epoch is not None


@dataclass
class AltDelta:
    """A delta computed against an *alternative* reference candidate."""
    reference_source: str
    reference_epoch: float
    reference_iso: str
    delta_seconds: float
    action: str  # "aligned", "trim", "offset"


@dataclass
class SyncEntry:
    """Per-file row in the sync report.

    ``delta_seconds`` is positive when the file started *after* the primary
    reference (apply an offset to it), negative when it started *before*
    (trim its head). ``alternatives`` lists the same delta computed against
    each non-primary reference candidate — only populated when the
    reference has alternatives that disagree with the primary.
    """
    file: str
    kind: str
    epoch: float | None
    iso: str | None
    primary_source: str | None
    delta_seconds: float | None
    action: str | None  # "reference", "trim", "offset", "aligned", or None
    alternatives: list[AltDelta] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


@dataclass
class SyncReport:
    reference_file: str | None
    reference_primary_source: str | None
    reference_epoch: float | None
    reference_iso: str | None
    reference_alternatives: list[Candidate] = field(default_factory=list)  # excludes primary
    entries: list[SyncEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "reference": {
                "file": self.reference_file,
                "primary_source": self.reference_primary_source,
                "epoch": self.reference_epoch,
                "iso": self.reference_iso,
                "alternatives": [asdict(c) for c in self.reference_alternatives],
            },
            "entries": [asdict(e) for e in self.entries],
        }


def _epoch_to_iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return _dt.datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def _classify(path: Path) -> str:
    return _EXT_KIND.get(path.suffix.lower(), "unknown")


def _action_for_delta(delta: float) -> str:
    if delta == 0:
        return "aligned"
    return "offset" if delta > 0 else "trim"


def _mp4_candidates(report) -> list[Candidate]:
    """Collect distinct timestamp candidates from a TimestampReport.

    Sources are visited in DEFAULT_AUTO_ORDER (gps, mvhd, mdhd, cdat). When
    two sources resolve to the same epoch (commonly mvhd == mdhd), the
    later source is dropped so the user only sees genuinely distinct
    timezone interpretations.
    """
    seen: dict[float, Candidate] = {}
    out: list[Candidate] = []
    for name in DEFAULT_AUTO_ORDER:
        s = report.sources.get(name)
        if s is None or not s.is_present():
            continue
        # Round to 1ms so we don't treat 14:48:53.000 and 14:48:53.0000001 as different.
        key = round(s.epoch, 3)
        if key in seen:
            continue
        c = Candidate(source=name, epoch=s.epoch, iso=s.iso)
        seen[key] = c
        out.append(c)
    return out


def read_first_timestamp(path: str | Path) -> FileTimestamp:
    """Dispatch by extension and return the file's first/representative timestamp."""
    p = Path(path)
    kind = _classify(p)

    if kind == "mp4":
        try:
            # Pull every available source so we can surface alternatives.
            report = extract_timestamps(p, source="all")
        except (FileNotFoundError, ValueError) as e:
            return FileTimestamp(file=str(p), kind=kind, detail={"error": str(e)})

        candidates = _mp4_candidates(report)
        if not candidates:
            return FileTimestamp(file=str(p), kind=kind, detail={"missing": "no usable source"})

        primary = candidates[0]
        return FileTimestamp(
            file=str(p), kind=kind,
            epoch=primary.epoch, iso=primary.iso,
            primary_source=primary.source,
            candidates=candidates,
        )

    if kind == "tcx":
        try:
            epoch = tcx.first_timestamp(p)
        except OSError as e:
            return FileTimestamp(file=str(p), kind=kind, detail={"error": str(e)})
        if epoch is None:
            return FileTimestamp(file=str(p), kind=kind, detail={"missing": "no <Id> found"})
        iso = _epoch_to_iso(epoch)
        return FileTimestamp(
            file=str(p), kind=kind, epoch=epoch, iso=iso, primary_source="tcx",
            candidates=[Candidate(source="tcx", epoch=epoch, iso=iso)],
        )

    if kind == "csv":
        try:
            epoch = rc_csv.first_timestamp(p)
        except OSError as e:
            return FileTimestamp(file=str(p), kind=kind, detail={"error": str(e)})
        if epoch is None:
            return FileTimestamp(
                file=str(p), kind=kind,
                detail={"missing": "no numeric epoch in column 0 (expected RaceChrono v3 CSV)"},
            )
        iso = _epoch_to_iso(epoch)
        return FileTimestamp(
            file=str(p), kind=kind, epoch=epoch, iso=iso, primary_source="csv",
            candidates=[Candidate(source="csv", epoch=epoch, iso=iso)],
        )

    return FileTimestamp(file=str(p), kind="unknown", detail={"missing": f"unsupported extension {p.suffix!r}"})


def _pick_reference(stamps: list[FileTimestamp]) -> FileTimestamp | None:
    """First MP4 with a timestamp wins; otherwise first file with any timestamp."""
    for s in stamps:
        if s.kind == "mp4" and s.is_present():
            return s
    for s in stamps:
        if s.is_present():
            return s
    return None


def build_sync_report(files: list[str | Path]) -> SyncReport:
    stamps = [read_first_timestamp(p) for p in files]
    ref = _pick_reference(stamps)

    if ref is None:
        return SyncReport(
            reference_file=None, reference_primary_source=None,
            reference_epoch=None, reference_iso=None,
            reference_alternatives=[],
            entries=[
                SyncEntry(
                    file=s.file, kind=s.kind, epoch=s.epoch, iso=s.iso,
                    primary_source=s.primary_source,
                    delta_seconds=None, action=None,
                    detail=dict(s.detail),
                ) for s in stamps
            ],
        )

    primary_candidate = ref.candidates[0] if ref.candidates else Candidate(
        source=ref.primary_source or "?", epoch=ref.epoch, iso=ref.iso or "",
    )
    alt_candidates = ref.candidates[1:] if len(ref.candidates) > 1 else []

    entries: list[SyncEntry] = []
    for s in stamps:
        if not s.is_present():
            entries.append(SyncEntry(
                file=s.file, kind=s.kind, epoch=s.epoch, iso=s.iso,
                primary_source=s.primary_source,
                delta_seconds=None, action=None,
                detail=dict(s.detail),
            ))
            continue

        if s is ref:
            entries.append(SyncEntry(
                file=s.file, kind=s.kind, epoch=s.epoch, iso=s.iso,
                primary_source=s.primary_source,
                delta_seconds=0.0, action="reference",
                detail=dict(s.detail),
            ))
            continue

        delta = s.epoch - primary_candidate.epoch
        alts = [
            AltDelta(
                reference_source=c.source,
                reference_epoch=c.epoch,
                reference_iso=c.iso,
                delta_seconds=s.epoch - c.epoch,
                action=_action_for_delta(s.epoch - c.epoch),
            )
            for c in alt_candidates
        ]
        entries.append(SyncEntry(
            file=s.file, kind=s.kind, epoch=s.epoch, iso=s.iso,
            primary_source=s.primary_source,
            delta_seconds=delta, action=_action_for_delta(delta),
            alternatives=alts,
            detail=dict(s.detail),
        ))

    return SyncReport(
        reference_file=ref.file,
        reference_primary_source=primary_candidate.source,
        reference_epoch=primary_candidate.epoch,
        reference_iso=primary_candidate.iso,
        reference_alternatives=alt_candidates,
        entries=entries,
    )


def format_delta(delta_seconds: float) -> str:
    """Format a signed seconds delta as ``[-]HH:MM:SS.mmm``."""
    sign = "-" if delta_seconds < 0 else ""
    total_ms = int(round(abs(delta_seconds) * 1000))
    hours, rem_ms = divmod(total_ms, 3600 * 1000)
    minutes, rem_ms = divmod(rem_ms, 60 * 1000)
    seconds, millis = divmod(rem_ms, 1000)
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def describe_action(action: str | None, delta_seconds: float | None) -> str:
    """Human-readable description of an action+delta pair."""
    if action is None or delta_seconds is None:
        return "--"
    if action == "reference":
        return "(reference)"
    if action == "aligned":
        return "aligned"
    if action == "offset":
        return f"offset by {format_delta(delta_seconds)}"
    # trim
    return f"trim head by {format_delta(abs(delta_seconds))}"
