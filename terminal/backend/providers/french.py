"""Kenneth French Data Library — Fama-French factor returns (academic, free).
Zipped CSVs from the Dartmouth/Tuck site; parsed into (date, factor) rows.
"""
from __future__ import annotations
import io, zipfile, urllib.request, csv

BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
FILES = {
    "factors_monthly": "F-F_Research_Data_Factors_CSV.zip",
    "factors_daily": "F-F_Research_Data_Factors_daily_CSV.zip",
    "factors5_monthly": "F-F_Research_Data_5_Factors_2x3_CSV.zip",
    "momentum_monthly": "F-F_Momentum_Factor_CSV.zip",
}

def factors(dataset="factors_monthly", limit=360):
    fn = FILES[dataset]
    req = urllib.request.Request(BASE + fn, headers={"User-Agent": "openterm/0.1 (research)"})
    with urllib.request.urlopen(req, timeout=40) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    name = z.namelist()[0]
    text = z.read(name).decode("latin-1")
    rows, header = [], None
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if cells[0][:6].isdigit() and len(cells) > 1:          # data row (YYYYMM / YYYYMMDD)
            try:
                rows.append({"date": cells[0], **{header[i]: float(cells[i + 1])
                             for i in range(len(header)) if cells[i + 1] not in ("", "-99.99")}})
            except (ValueError, IndexError):
                continue
        elif any(h in line for h in ("Mkt-RF", "Mom")) and "," in line:
            header = [c.strip() for c in line.split(",")[1:] if c.strip()]
    return rows[-limit:]
