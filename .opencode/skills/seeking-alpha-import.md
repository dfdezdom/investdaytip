---
description: Import Seeking Alpha XLSX files to extract tickers and run InvestDayTip analysis. Use when the user provides a .xlsx path or wants to analyze tickers from a file.
---

# Seeking Alpha XLSX Import

## Workflow

1. **Extract tickers** — use the raw XML method (openpyxl chokes on Seeking Alpha's conditional formatting). Save to `seeking_alpha_data/<name>.csv`.
2. **Show a preview** — print the ticker count and first few rows (Rank, Symbol, Company Name).
3. **Ask the user** if they want to run `investdaytip` with those tickers.
4. **If yes**, build the command and ask for confirmation before executing.

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
    cells = []
    for c in row:
        v = c.find(f"{NS}v")
        val = v.text if v is not None else ""
        if c.get("t") == "s" and val:
            val = ss[int(val)]
        cells.append(val)
    if cells:
        rows.append(cells)

with open(dst, "w", newline="") as f:
    csv.writer(f).writerows(rows)
print(f"{len(rows)} rows -> {dst}")
tickers = [r[1] for r in rows[1:] if len(r) > 1 and r[1].strip()]
print(f"Tickers ({len(tickers)}): {' '.join(tickers)}")
```

## After extraction

Ask the user:
- Which region(s) to analyze
- Asset class (stocks / etfs)
- `investdaytip` flags (`--scoring-model`, `--data-source`, etc.)
- Number of picks (`-n`)
