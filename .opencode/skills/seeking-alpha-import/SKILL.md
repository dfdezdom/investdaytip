---
name: seeking-alpha-import
description: Extract tickers from Seeking Alpha XLSX files using raw XML parsing and run InvestDayTip analysis
license: MIT
---

# Seeking Alpha XLSX Import

## Workflow

1. Extract tickers using raw XML method (openpyxl chokes on conditional formatting)
2. Save CSV to `seeking_alpha_data/<name>.csv`
3. Show ticker count and preview
4. Ask user if they want to run `investdaytip` with those tickers
5. Build command and confirm before executing

## Extraction code

```python
import zipfile, xml.etree.ElementTree as ET, csv
from pathlib import Path

src = Path("path/to/file.xlsx")
dst = Path("seeking_alpha_data") / src.with_suffix(".csv").name
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

with zipfile.ZipFile(src) as z:
    ss = [
        si.find(f"{NS}r").find(f"{NS}t").text or ""
        if si.find(f"{NS}r") is not None
        else (si.find(f"{NS}t").text or "")
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{NS}si")
    ]
    sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))

rows = []
for row in sheet.find(f"{NS}sheetData").findall(f"{NS}row"):
    cells = [v.find(f"{NS}v").text or "" if (v := c.find(f"{NS}v")) is not None else "" for c in row]
    if cells:
        rows.append(cells)

with open(dst, "w", newline="") as f:
    csv.writer(f).writerows(rows)
print(f"{len(rows)} rows -> {dst}")
tickers = [r[1] for r in rows[1:] if len(r) > 1 and r[1].strip()]
print(f"Tickers ({len(tickers)}): {' '.join(tickers)}")
```

## After extraction

Ask user for: region(s), asset class, flags (`--scoring-model`, `--data-source`), number of picks.
