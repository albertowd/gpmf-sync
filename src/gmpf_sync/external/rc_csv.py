"""RaceChrono v3 CSV first-timestamp reader.

RaceChrono v3 exports CSV logs whose first column is the Unix epoch in
seconds, with millisecond precision (e.g. ``1723472631.500``). Other rows
in the header are non-numeric (column titles, units), so the first row
where column 0 parses as a positive float is the start of the data.
"""
from __future__ import annotations

import re
from pathlib import Path

# Match a positive number with at least one decimal place. Matches the
# reference Deno implementation, which also requires a fractional part —
# this avoids accidentally consuming integer-only header values like row
# indices.
_EPOCH_RE = re.compile(r"^\d+\.\d+$")


def first_timestamp(path: str | Path) -> float | None:
    """Return Unix epoch (seconds, float) of the first data row, or None."""
    p = Path(path)
    with p.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            head = line.split(",", 1)[0].strip()
            if _EPOCH_RE.match(head):
                try:
                    return float(head)
                except ValueError:
                    continue
    return None
