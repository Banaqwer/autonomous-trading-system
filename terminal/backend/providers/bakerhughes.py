"""Baker Hughes North America rig count (energy supply fundamental).
No simple JSON API; the weekly data is published as an Excel workbook. This adapter
parses that workbook when `openpyxl` is installed and the URL is reachable, else it
returns the source so the UI can link out. Set BH_RIGCOUNT_XLSX to override the URL.
"""
from __future__ import annotations
import os, io, urllib.request

DEFAULT_URL = os.environ.get("BH_RIGCOUNT_XLSX",
    "https://rigcount.bakerhughes.com/static-files/")  # index; point to the weekly .xlsx

def rig_count(url=None):
    url = url or DEFAULT_URL
    try:
        import openpyxl
    except ImportError:
        return {"ok": False, "note": "pip install openpyxl to parse the workbook", "source": url}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"openterm/0.1"}), timeout=30) as r:
            wb = openpyxl.load_workbook(io.BytesIO(r.read()), read_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = [[c.value for c in row] for row in ws.iter_rows(max_rows=8)]
        return {"ok": True, "source": url, "preview": rows}
    except Exception as e:
        return {"ok": False, "source": url,
                "note": "set BH_RIGCOUNT_XLSX to the weekly workbook URL", "err": str(e)[:140]}
