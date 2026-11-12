"""Kenneth French Data Library — Fama-French factor returns (owner: S6). Keyless.

Zipped CSVs from the Dartmouth/Tuck site. A monthly file holds the monthly table first
and then, after a line "Annual Factors: January-December", an ANNUAL table: the old
parser appended those annual rows to the monthly series. `parse` reads the first table
only and returns ISO dates. The source lags about six weeks.
"""
from __future__ import annotations

import io
import zipfile

from core import http
from core.errors import BadRequest, UpstreamError
from core.util import num

BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
SOURCE = "french"
FILES = {
    "factors_monthly": "F-F_Research_Data_Factors_CSV.zip",
    "factors_daily": "F-F_Research_Data_Factors_daily_CSV.zip",
    "factors5_monthly": "F-F_Research_Data_5_Factors_2x3_CSV.zip",
    "momentum_monthly": "F-F_Momentum_Factor_CSV.zip",
}


def fetch(dataset: str = "factors_monthly", timeout: float = 40) -> str:
    """The CSV text inside the zip."""
    if dataset not in FILES:
        raise BadRequest("dataset must be one of " + ", ".join(FILES))
    blob = http.get_bytes(BASE + FILES[dataset], timeout=timeout, source=SOURCE)
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        return z.read(z.namelist()[0]).decode("latin-1")
    except (zipfile.BadZipFile, IndexError, KeyError) as e:
        raise UpstreamError(SOURCE, None, f"not a readable zip ({type(e).__name__})") from None


def parse(text: str, dataset: str = "factors_monthly") -> list:
    """[{date: 'YYYY-MM' | 'YYYY-MM-DD', <factor>: percent, …}] ascending — the FIRST table only."""
    width = 8 if dataset.endswith("_daily") else 6
    rows, header = [], None
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if header is None:
            if len(cells) > 1 and not cells[0] and any(c in ("Mkt-RF", "Mom", "SMB") for c in cells[1:]):
                header = [c for c in cells[1:] if c]
            continue
        key = cells[0]
        if not (key.isdigit() and len(key) == width):
            if rows:
                break                      # blank line / "Annual Factors…" ends the first table
            continue
        row = {"date": f"{key[:4]}-{key[4:6]}" + (f"-{key[6:8]}" if width == 8 else "")}
        for i, name in enumerate(header):
            v = num(cells[i + 1]) if i + 1 < len(cells) else None
            if v is not None and v > -99:   # -99.99 / -999 mark missing values
                row[name] = v
        rows.append(row)
    if header is None or not rows:
        raise UpstreamError(SOURCE, None, "no factor table found in the file")
    return rows


def factors(dataset="factors_monthly", limit=360):
    return parse(fetch(dataset), dataset)[-max(1, int(limit)):]
