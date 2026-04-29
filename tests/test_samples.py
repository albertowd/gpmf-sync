"""Sanity tests against GoPro sample MP4s.

These tests need the sample files from the official gpmf-parser repository
(https://github.com/gopro/gpmf-parser/tree/main/samples). Point the
`GMPF_SYNC_SAMPLES_DIR` environment variable at the directory containing
them, or place the directory at `<project>/samples/`. Tests are skipped
when no sample directory is available.
"""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

import pytest

from gmpf_sync.timestamps import extract_timestamps

UTC = _dt.timezone.utc
_PROJECT_ROOT = Path(__file__).parent.parent
SAMPLES_DIR = Path(
    os.environ.get("GMPF_SYNC_SAMPLES_DIR")
    or _PROJECT_ROOT / "samples"
)

# (filename, has_gps_fix)
# has_gps_fix: True = has GPSF>=2; False = GPSU present but fix=0; None = no GPS data.
SAMPLES = [
    ("hero5.mp4", True),
    ("hero6.mp4", True),
    ("hero7.mp4", False),
    ("hero8.mp4", False),
    ("karma.mp4", None),
    ("Fusion.mp4", True),
    ("max-heromode.mp4", True),
    ("max-360mode.mp4", True),
    ("hero6+ble.mp4", False),
]


@pytest.fixture(scope="module")
def samples_available():
    if not SAMPLES_DIR.is_dir():
        pytest.skip(f"sample directory {SAMPLES_DIR} not present")


@pytest.mark.parametrize("name,_has_fix", SAMPLES)
def test_mvhd_extracted(samples_available, name, _has_fix):
    r = extract_timestamps(SAMPLES_DIR / name, source="all")
    mvhd = r.sources["mvhd"]
    assert mvhd.is_present(), f"{name}: mvhd missing"
    dt = _dt.datetime.fromtimestamp(mvhd.epoch, UTC)
    # Sanity: timestamp is plausibly within the GoPro era. Some sample cameras
    # had factory-default clocks, so this is a loose range.
    assert _dt.datetime(2014, 1, 1, tzinfo=UTC) <= dt <= _dt.datetime(2030, 1, 1, tzinfo=UTC), \
        f"{name}: mvhd {dt.isoformat()} outside plausible range"


@pytest.mark.parametrize("name,has_fix", SAMPLES)
def test_gps_when_expected(samples_available, name, has_fix):
    r = extract_timestamps(SAMPLES_DIR / name, source="gps")
    gps = r.sources["gps"]
    if has_fix is None:
        assert not gps.is_present() or gps.detail.get("fix", 0) == 0
        return
    if has_fix:
        assert gps.is_present()
        assert gps.detail.get("fix", 0) >= 2
    else:
        if gps.is_present():
            assert gps.detail.get("fix", 0) == 0


@pytest.mark.parametrize("name,has_fix", SAMPLES)
def test_auto_prefers_gps_when_fixed(samples_available, name, has_fix):
    r = extract_timestamps(SAMPLES_DIR / name, source="auto")
    if has_fix:
        assert r.selected_source == "gps"
    else:
        assert r.selected_source in ("gps", "mvhd", "mdhd", "cdat")
