"""确认大豆/棉花/咖啡的产量标签源与国别覆盖。"""

from __future__ import annotations

import csv
import io
import sys
import urllib.request
import zipfile

sys.stdout.reconfigure(encoding="utf-8")
UA = "brain-agri-graph/0.1 (research)"

ZIPS = {
    "oilseeds": "https://apps.fas.usda.gov/psdonline/downloads/psd_oilseeds_csv.zip",
    "cotton": "https://apps.fas.usda.gov/psdonline/downloads/psd_cotton_csv.zip",
    "coffee": "https://apps.fas.usda.gov/psdonline/downloads/psd_coffee_csv.zip",
}

WANT_COUNTRIES = {"china", "united states", "brazil", "vietnam", "india"}

for name, url in ZIPS.items():
    print(f"=== {name} ===")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
    except Exception as e:
        print(f"  FAIL {type(e).__name__}: {e}")
        continue
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        member = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        text = zf.read(member).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames
    commodities: dict[str, set[str]] = {}
    attrs: set[str] = set()
    rows = 0
    for rec in reader:
        rows += 1
        country = (rec.get("Country_Name") or "").strip().lower()
        if country not in WANT_COUNTRIES:
            continue
        commodity = (rec.get("Commodity_Description") or "").strip()
        commodities.setdefault(commodity, set()).add(rec.get("Country_Name") or "")
        attrs.add((rec.get("Attribute_Description") or "").strip())
    print(f"  file={member} rows={rows}")
    print(f"  fields={fields}")
    print(f"  attributes={sorted(a for a in attrs if a)[:14]}")
    for commodity, countries in sorted(commodities.items()):
        print(f"  {commodity}: {sorted(countries)}")
