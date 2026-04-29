"""TCX (Training Center XML) first-timestamp reader.

TCX is an XML format for fitness/sports activities. The very first absolute
timestamp typically appears as `<Id>YYYY-MM-DDTHH:MM:SS[.fff]Z</Id>` inside
the first `<Activity>` block, where it doubles as the activity's start time.

We don't load the whole document into memory — TCX files can be sizeable for
long activities. Instead we stream the file line-by-line and return as soon
as the first `<Id>` element is found.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

UTC = _dt.timezone.utc

_ID_RE = re.compile(r"<Id>\s*([^<]+?)\s*</Id>", re.IGNORECASE)


def _parse_iso8601(s: str) -> float | None:
    """Parse an ISO-8601 datetime as used in TCX. Returns Unix epoch seconds."""
    s = s.strip()
    if not s:
        return None
    # datetime.fromisoformat in 3.10 doesn't accept the trailing 'Z'. Normalise it.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # TCX timestamps are spec'd as UTC. Treat naive ones as UTC too.
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def first_timestamp(path: str | Path) -> float | None:
    """Return Unix epoch (seconds, float) of the first `<Id>` element, or None."""
    p = Path(path)
    with p.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = _ID_RE.search(line)
            if m:
                return _parse_iso8601(m.group(1))
    return None
