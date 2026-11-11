"""UN Comtrade — bilateral trade flows (drives the SPLC supply-chain graph).
Public preview endpoint (no key, limited rows). Full access: free key at comtradeplus.un.org.
"""
from __future__ import annotations
import os, json, urllib.request

def flows(reporter="842", partner="all", commodity="2709", year="2023", flow="M"):
    # 842=USA, 2709=crude petroleum; flow M=imports X=exports
    key = os.environ.get("COMTRADE_API_KEY", "")
    base = "https://comtradeapi.un.org"
    path = (f"/data/v1/get/C/A/HS?reporterCode={reporter}&partnerCode={partner}"
            f"&cmdCode={commodity}&flowCode={flow}&period={year}")
    url = base + path + (f"&subscription-key={key}" if key else "")
    if not key:  # public preview path
        url = base + "/public/v1/preview/C/A/HS?" + url.split("?",1)[1]
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"openterm/0.1"}), timeout=30) as r:
        d = json.loads(r.read())
    data = d.get("data", d if isinstance(d, list) else [])
    return [{"partner": x.get("partnerDesc"), "value_usd": x.get("primaryValue"),
             "qty": x.get("qty"), "flow": x.get("flowDesc")} for x in data][:200]
