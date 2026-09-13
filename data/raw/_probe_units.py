"""确认三类品的属性与单位，决定建模目标。"""

from __future__ import annotations

import csv
import io
import sys
import urllib.request
import zipfile

sys.stdout.reconfigure(encoding="utf-8")
UA = "brain-agri-graph/0.1 (research)"

TARGETS = {
    "oilseeds": ("https://apps.fas.usda.gov/psdonline/downloads/psd_oilseeds_csv.zip", "Oilseed, Soybean"),
    "cotton": ("https://apps.fas.usda.gov/psdonline/downloads/psd_cotton_csv.zip", "Cotton"),
    "coffee": ("https://apps.fas.usda.gov/psdonline/downloads/psd_coffee_csv.zip", "Coffee, Green"),
}
COUNTRIES = {"China", "United States", "Brazil"}

for name, (url, commodity) in TARGETS.items():
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        member = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        text = zf.read(member).decode("utf-8", errors="replace")
    print(f"=== {name} / {commodity} ===")
    seen: dict[tuple[str, str], str] = {}
    sample: dict[tuple[str, str, int], float] = {}
    for rec in csv.DictReader(io.StringIO(text)):
        if (rec.get("Commodity_Description") or "").strip() != commodity:
            continue
        country = (rec.get("Country_Name") or "").strip()
        if country not in COUNTRIES:
            continue
        attr = (rec.get("Attribute_Description") or "").strip()
        unit = (rec.get("Unit_Description") or "").strip()
        seen[(attr, unit)] = country
        try:
            year = int(float(rec.get("Market_Year") or 0))
            val = float(rec.get("Value") or 0)
        except ValueError:
            continue
        if year == 2023 and attr in {"Yield", "Production", "Area Harvested"}:
            sample[(country, attr, year)] = val
    for (attr, unit), _c in sorted(seen.items()):
        print(f"  {attr:22s} | {unit}")
    print("  2023 样值:")
    for key in sorted(sample):
        print(f"    {key[0]:15s} {key[1]:16s} {sample[key]}")
