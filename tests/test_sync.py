"""Tests for the TCX/CSV readers and the sync orchestrator."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from gmpf_sync import rc_csv, tcx
from gmpf_sync.sync import (
    FileTimestamp,
    build_sync_report,
    format_delta,
    read_first_timestamp,
)

UTC = _dt.timezone.utc


# ---- TCX ------------------------------------------------------------------

TCX_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase>
  <Activities>
    <Activity Sport="Biking">
      <Id>2024-08-12T14:23:51.500Z</Id>
      <Lap StartTime="2024-08-12T14:23:51.500Z">
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>
"""


def test_tcx_first_timestamp(tmp_path: Path):
    f = tmp_path / "ride.tcx"
    f.write_text(TCX_BODY, encoding="utf-8")
    epoch = tcx.first_timestamp(f)
    assert epoch is not None
    expected = _dt.datetime(2024, 8, 12, 14, 23, 51, 500_000, tzinfo=UTC).timestamp()
    assert epoch == pytest.approx(expected, abs=1e-3)


def test_tcx_no_id_returns_none(tmp_path: Path):
    f = tmp_path / "empty.tcx"
    f.write_text("<TrainingCenterDatabase></TrainingCenterDatabase>", encoding="utf-8")
    assert tcx.first_timestamp(f) is None


def test_tcx_naive_iso_treated_as_utc(tmp_path: Path):
    f = tmp_path / "naive.tcx"
    f.write_text("<x><Id>2024-01-01T00:00:00</Id></x>", encoding="utf-8")
    expected = _dt.datetime(2024, 1, 1, tzinfo=UTC).timestamp()
    assert tcx.first_timestamp(f) == pytest.approx(expected, abs=1e-3)


# ---- RaceChrono v3 CSV ----------------------------------------------------

CSV_BODY = """\
Session "1"
Date,2024-08-12
"Time","Lat","Lon"
"s","deg","deg"
1723472631.500,40.0,-3.0
1723472631.600,40.0,-3.0
"""


def test_rc_csv_first_timestamp(tmp_path: Path):
    f = tmp_path / "log.csv"
    f.write_text(CSV_BODY, encoding="utf-8")
    epoch = rc_csv.first_timestamp(f)
    assert epoch == pytest.approx(1723472631.500, abs=1e-3)


def test_rc_csv_no_data_returns_none(tmp_path: Path):
    f = tmp_path / "empty.csv"
    f.write_text("Header,A,B\nfoo,bar,baz\n", encoding="utf-8")
    assert rc_csv.first_timestamp(f) is None


# ---- format_delta ---------------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (0.0, "00:00:00.000"),
    (1.5, "00:00:01.500"),
    (-1.5, "-00:00:01.500"),
    (3661.250, "01:01:01.250"),
    (-3661.250, "-01:01:01.250"),
])
def test_format_delta(seconds, expected):
    assert format_delta(seconds) == expected


# ---- sync orchestrator ----------------------------------------------------

def test_read_first_timestamp_tcx(tmp_path: Path):
    f = tmp_path / "ride.tcx"
    f.write_text(TCX_BODY, encoding="utf-8")
    s = read_first_timestamp(f)
    assert s.kind == "tcx"
    assert s.is_present()


def test_read_first_timestamp_csv(tmp_path: Path):
    f = tmp_path / "log.csv"
    f.write_text(CSV_BODY, encoding="utf-8")
    s = read_first_timestamp(f)
    assert s.kind == "csv"
    assert s.is_present()


def test_read_first_timestamp_unknown(tmp_path: Path):
    f = tmp_path / "x.bin"
    f.write_text("nope", encoding="utf-8")
    s = read_first_timestamp(f)
    assert s.kind == "unknown"
    assert not s.is_present()


def test_sync_report_uses_mp4_as_reference_when_present(tmp_path, monkeypatch):
    tcx_file = tmp_path / "ride.tcx"
    tcx_file.write_text(TCX_BODY, encoding="utf-8")
    csv_file = tmp_path / "log.csv"
    csv_file.write_text(CSV_BODY, encoding="utf-8")
    fake_mp4 = tmp_path / "GH010001.MP4"
    fake_mp4.write_bytes(b"\x00" * 16)  # never actually parsed; we monkeypatch

    # Pretend the GoPro file's GPS time is one minute later than the TCX/CSV.
    mp4_epoch = 1723472631.500 + 60.0

    def _fake_extract(path, source="auto"):
        from gmpf_sync.timestamps import TimestampReport, StampSource
        s = StampSource(name="gps", epoch=mp4_epoch, iso="(fake-gps)")
        return TimestampReport(
            file=str(path), file_size=0, sources={"gps": s},
            selected_source="gps", selected_epoch=mp4_epoch, selected_iso="(fake-gps)",
        )

    monkeypatch.setattr("gmpf_sync.sync.extract_timestamps", _fake_extract)

    report = build_sync_report([tcx_file, fake_mp4, csv_file])
    assert report.reference_file == str(fake_mp4)
    assert report.reference_primary_source == "gps"
    assert report.reference_alternatives == []

    by_kind = {e.kind: e for e in report.entries}
    assert by_kind["mp4"].action == "reference"
    # TCX/CSV started 60s BEFORE the GoPro → trim head
    assert by_kind["tcx"].action == "trim"
    assert by_kind["tcx"].delta_seconds == pytest.approx(-60.0, abs=1e-3)
    assert by_kind["csv"].action == "trim"
    assert by_kind["csv"].delta_seconds == pytest.approx(-60.0, abs=1e-3)
    # No alternatives because the fake MP4 only exposes one source.
    assert by_kind["tcx"].alternatives == []
    assert by_kind["csv"].alternatives == []


def test_sync_report_surfaces_mp4_alternatives_when_sources_disagree(tmp_path, monkeypatch):
    """Mimics the GoPro local-time-vs-UTC quirk: mvhd at 14:48:53Z, cdat at 11:48:53Z."""
    csv_file = tmp_path / "log.csv"
    csv_file.write_text(CSV_BODY, encoding="utf-8")
    fake_mp4 = tmp_path / "GX010076.MP4"
    fake_mp4.write_bytes(b"\x00" * 16)

    mvhd_epoch = 1774104533.0          # 2026-03-21T14:48:53Z
    mdhd_epoch = 1774104533.0          # same as mvhd → should be deduped
    cdat_epoch = mvhd_epoch - 3 * 3600  # 3 hours earlier
    csv_epoch = rc_csv.first_timestamp(csv_file)
    assert csv_epoch is not None

    def _fake_extract(path, source="all"):
        from gmpf_sync.timestamps import TimestampReport, StampSource
        sources = {
            "gps":  StampSource.missing("gps", "no fix"),
            "mvhd": StampSource(name="mvhd", epoch=mvhd_epoch, iso="2026-03-21T14:48:53Z"),
            "mdhd": StampSource(name="mdhd", epoch=mdhd_epoch, iso="2026-03-21T14:48:53Z"),
            "cdat": StampSource(name="cdat", epoch=cdat_epoch, iso="2026-03-21T11:48:53Z"),
        }
        return TimestampReport(
            file=str(path), file_size=0, sources=sources,
            selected_source="mvhd", selected_epoch=mvhd_epoch, selected_iso="2026-03-21T14:48:53Z",
        )

    monkeypatch.setattr("gmpf_sync.sync.extract_timestamps", _fake_extract)

    report = build_sync_report([fake_mp4, csv_file])
    # Primary is mvhd; cdat is the only surviving alternative (mdhd deduped).
    assert report.reference_primary_source == "mvhd"
    assert [c.source for c in report.reference_alternatives] == ["cdat"]

    csv_entry = next(e for e in report.entries if e.kind == "csv")
    primary_delta = csv_epoch - mvhd_epoch
    assert csv_entry.delta_seconds == pytest.approx(primary_delta, abs=1e-3)
    assert len(csv_entry.alternatives) == 1
    alt = csv_entry.alternatives[0]
    assert alt.reference_source == "cdat"
    assert alt.delta_seconds == pytest.approx(csv_epoch - cdat_epoch, abs=1e-3)
    # Whatever the absolute direction, the alternative must differ from the
    # primary by exactly the 3-hour TZ gap.
    assert abs(alt.delta_seconds - primary_delta) == pytest.approx(3 * 3600, abs=1e-3)


def test_sync_report_no_mp4_falls_back_to_first_present(tmp_path: Path):
    tcx_file = tmp_path / "ride.tcx"
    tcx_file.write_text(TCX_BODY, encoding="utf-8")
    csv_file = tmp_path / "log.csv"
    csv_file.write_text(CSV_BODY, encoding="utf-8")

    report = build_sync_report([tcx_file, csv_file])
    assert report.reference_file == str(tcx_file)
    by_file = {e.file: e for e in report.entries}
    assert by_file[str(tcx_file)].action == "reference"
    # Same instant ⇒ aligned.
    assert by_file[str(csv_file)].action == "aligned"


def test_sync_report_handles_missing_input(tmp_path: Path):
    csv_file = tmp_path / "log.csv"
    csv_file.write_text("not,a,number\n", encoding="utf-8")
    report = build_sync_report([csv_file])
    assert report.reference_file is None
    assert report.entries[0].epoch is None
    assert "missing" in report.entries[0].detail
