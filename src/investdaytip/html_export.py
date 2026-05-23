"""Self-contained HTML export for recommendation results."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote, quote_plus
from typing import Any

from investdaytip.scoring import ScoredAsset


def infer_region_from_ticker(ticker: str) -> str:
    """Infer region from Yahoo ticker suffix.

    The mapping follows the project's curated universes and README conventions.
    """
    upper = ticker.upper()
    suffix = upper.rsplit(".", 1)[-1] if "." in upper else ""
    eu_suffixes = {"DE", "PA", "AS", "L", "MC", "MI", "SW", "ST", "CO", "HE", "OL"}
    asia_suffixes = {"T", "HK", "SI", "NS", "KS", "TW", "AX"}
    if suffix in eu_suffixes:
        return "eu"
    if suffix in asia_suffixes:
        return "asia"
    return "us"


def _fmt_price(price: Any, currency: Any) -> str:
    if price is None:
        return "-"
    symbol = {
        "USD": "$",
        "EUR": "EUR ",
        "GBP": "GBP ",
        "GBp": "p",
        "CHF": "CHF ",
        "DKK": "DKK ",
        "SEK": "SEK ",
        "NOK": "NOK ",
        "JPY": "JPY ",
    }.get(str(currency or ""), "")
    if symbol:
        return f"{symbol}{float(price):,.2f}"
    if currency:
        return f"{float(price):,.2f} {currency}"
    return f"{float(price):,.2f}"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:.2f}%"


def _google_finance_url(ticker: str) -> str:
  query = quote_plus(ticker)
  return f"https://www.google.com/finance?hl=en&q={query}"


def _split_ticker(ticker: str) -> tuple[str, str]:
  t = ticker.upper()
  if "." in t:
    base, suffix = t.rsplit(".", 1)
    return base, suffix
  return t, ""


def _exchange_mapping(ticker: str) -> tuple[str | None, str | None]:
  _, suffix = _split_ticker(ticker)
  # (Google Finance exchange, TradingView exchange)
  mapping = {
    "": ("NASDAQ", "NASDAQ"),
    "DE": ("ETR", "XETR"),
    "PA": ("EPA", "EURONEXT"),
    "AS": ("AMS", "EURONEXT"),
    "L": ("LON", "LSE"),
    "MC": ("BME", "BME"),
    "MI": ("BIT", "MIL"),
    "SW": ("SWX", "SIX"),
    "T": ("TYO", "TSE"),
    "HK": ("HKG", "HKEX"),
    "SI": ("SGX", "SGX"),
    "NS": ("NSE", "NSE"),
    "KS": ("KRX", "KRX"),
    "TW": ("TPE", "TWSE"),
    "AX": ("ASX", "ASX"),
  }
  return mapping.get(suffix, (None, None))


def _tradingview_url(ticker: str) -> str:
  base, _ = _split_ticker(ticker)
  _, tv_exchange = _exchange_mapping(ticker)
  if not tv_exchange:
    return f"https://www.tradingview.com/symbols/{quote(ticker, safe='')}"
  tv_symbol = f"{tv_exchange}:{base}"
  return f"https://www.tradingview.com/symbols/{quote(tv_symbol, safe=':')}"


def _yahoo_finance_url(ticker: str) -> str:
  return f"https://finance.yahoo.com/quote/{quote_plus(ticker)}"


def _build_links(ticker: str) -> dict[str, str]:
  return {
    "google": _google_finance_url(ticker),
    "tradingview": _tradingview_url(ticker),
    "yahoo": _yahoo_finance_url(ticker),
  }


def _as_row(index: int, s: ScoredAsset) -> dict[str, Any]:
    d = s.data
    return {
        "rank": index,
        "asset_type": s.asset_type.lower(),
        "ticker": d.ticker,
        "ticker_url": _google_finance_url(d.ticker),
        "links": _build_links(d.ticker),
        "name": d.name or "-",
        "sector": d.sector or "-",
        "region": infer_region_from_ticker(d.ticker),
        "price": d.current_price,
        "price_text": _fmt_price(d.current_price, getattr(d, "currency", None)),
        "return_1m": d.return_1m,
        "return_1m_text": _fmt_pct(d.return_1m),
        "return_12m": d.return_12m,
        "return_12m_text": _fmt_pct(d.return_12m),
        "score": round(s.total, 2),
        "breakdown": " / ".join(f"{int(round(v))}" for v in s.breakdown.values()),
        "why": "; ".join(s.rationale[:3]) if s.rationale else "-",
    }


def _render_initial_rows(rows: list[dict[str, Any]]) -> str:
  if not rows:
    return '<tr><td colspan="14" class="muted">No recommendations were generated for this run.</td></tr>'

  out: list[str] = []
  for r in rows:
    one_m_class = "pos" if (r["return_1m"] is not None and r["return_1m"] >= 0) else "neg"
    one_y_class = "pos" if (r["return_12m"] is not None and r["return_12m"] >= 0) else "neg"
    score = r.get("score")
    score_txt = f"{float(score):.1f}" if isinstance(score, (int, float)) else "-"
    out.append(
      "<tr>"
      f"<td>{int(r['rank'])}</td>"
      f"<td class=\"type-col\">{escape(str(r['asset_type']).upper())}</td>"
      f"<td><strong><a href=\"{escape(str(r['ticker_url']))}\" target=\"_blank\" rel=\"noopener noreferrer\">{escape(str(r['ticker']))}</a></strong></td>"
      f"<td class=\"link-col\"><a href=\"{escape(str(r['links']['tradingview']))}\" target=\"_blank\" rel=\"noopener noreferrer\" title=\"TradingView\"><span class=\"link-icon icon-tv\">TV</span></a></td>"
      f"<td class=\"link-col\"><a href=\"{escape(str(r['links']['yahoo']))}\" target=\"_blank\" rel=\"noopener noreferrer\" title=\"Yahoo Finance\"><span class=\"link-icon icon-yahoo\">Y</span></a></td>"
      f"<td>{escape(str(r['name']))}</td>"
      f"<td class=\"desktop-only region-col\">{escape(str(r['region']).upper())}</td>"
      f"<td class=\"desktop-only\">{escape(str(r['sector']))}</td>"
      f"<td class=\"num\">{escape(str(r['price_text']))}</td>"
      f"<td class=\"num {one_m_class}\">{escape(str(r['return_1m_text']))}</td>"
      f"<td class=\"num {one_y_class}\">{escape(str(r['return_12m_text']))}</td>"
      f"<td class=\"num\"><strong>{escape(score_txt)}</strong></td>"
      f"<td class=\"desktop-only breakdown-col\">{escape(str(r['breakdown']))}</td>"
      f"<td class=\"desktop-only why-col\">{escape(str(r['why']))}</td>"
      "</tr>"
    )
  return "".join(out)


def export_recommendations_html(
    results: list[ScoredAsset],
    destination: str,
    *,
    top_n: int,
    asset_class: str,
    region: str,
    tickers: list[str] | None,
) -> str:
    """Write a self-contained, filterable HTML report to ``destination``."""
    rows = [_as_row(i, s) for i, s in enumerate(results, start=1)]
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "top_n": top_n,
        "asset_class": asset_class,
        "region": region,
        "tickers": tickers or [],
        "row_count": len(rows),
    }

    rows_json = json.dumps(rows, ensure_ascii=True)
    metadata_json = json.dumps(metadata, ensure_ascii=True)
    initial_rows_html = _render_initial_rows(rows)

    html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>InvestDayTip Report</title>
  <style>
    :root {{
      --bg: #f3f5ef;
      --panel: #ffffff;
      --line: #dde5cf;
      --text: #1f2a1f;
      --muted: #58664d;
      --accent: #2f7d4a;
      --accent-soft: #d8f0df;
      --neg: #a84035;
      --pos: #1e7a35;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: radial-gradient(circle at top right, #e9f4dc 0, #f3f5ef 40%), var(--bg);
    }}
    .wrap {{ width: 100%; max-width: none; margin: 0; padding: 20px 20px 36px; }}
    h1 {{ margin: 0 0 8px; font-size: 1.6rem; }}
    .meta {{ color: var(--muted); font-size: 0.92rem; margin-bottom: 16px; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px; }}
    .chip {{
      background: var(--accent-soft);
      border: 1px solid #b7dcc4;
      color: #16482a;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 0.84rem;
    }}
    .filters {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      margin-bottom: 16px;
      box-shadow: 0 8px 20px rgba(35, 45, 22, 0.08);
    }}
    label {{ display: grid; gap: 6px; font-size: 0.86rem; color: var(--muted); }}
    input, select {{
      width: 100%;
      padding: 9px 10px;
      border: 1px solid #c9d4b5;
      border-radius: 10px;
      background: #fdfefb;
      color: var(--text);
      font-size: 0.95rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
      box-shadow: 0 8px 20px rgba(35, 45, 22, 0.08);
    }}
    thead th {{
      text-align: left;
      font-size: 0.82rem;
      letter-spacing: 0.02em;
      color: #2a402d;
      background: #e9f2df;
      border-bottom: 1px solid var(--line);
      padding: 10px;
      position: sticky;
      top: 0;
      z-index: 1;
    }}
    th.sortable {{ cursor: pointer; user-select: none; }}
    th.sortable.active {{ color: #1f6037; }}
    .sort-indicator {{ margin-left: 4px; color: #6d7f68; }}
    tbody td {{ border-top: 1px solid #edf2e6; padding: 10px; font-size: 0.92rem; vertical-align: top; }}
    .num {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
    .type-col {{ width: 64px; min-width: 58px; white-space: nowrap; }}
    .region-col {{ width: 62px; min-width: 56px; white-space: nowrap; }}
    .why-col {{ width: 36%; min-width: 420px; }}
    .breakdown-col {{ white-space: nowrap; min-width: 92px; }}
    td.why-col {{ white-space: normal; line-height: 1.35; word-break: break-word; }}
    .link-col {{ text-align: center; width: 38px; min-width: 34px; padding-left: 4px; padding-right: 4px; }}
    .link-col a {{ text-decoration: none; }}
    .link-icon {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      border-radius: 999px;
      font-size: 0.56rem;
      font-weight: 700;
      color: #fff;
      line-height: 1;
    }}
    .icon-tv {{ background: #1b1f2a; }}
    .icon-yahoo {{ background: #5f01d1; }}
    .muted {{ color: var(--muted); }}
    .pos {{ color: var(--pos); font-weight: 600; }}
    .neg {{ color: var(--neg); font-weight: 600; }}
    .count {{ margin: 8px 0 12px; color: var(--muted); font-size: 0.9rem; }}
    @media (max-width: 900px) {{
      .desktop-only {{ display: none; }}
      body {{ font-size: 14px; }}
      .wrap {{ padding: 14px 12px 24px; }}
      tbody td, thead th {{ padding: 8px; }}
    }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <h1>InvestDayTip Report</h1>
    <div class=\"meta\" id=\"generatedAt\"></div>
    <div class=\"chips\" id=\"runParams\"></div>

    <section class=\"filters\" aria-label=\"Filters\">
      <label>Search ticker / name
        <input id=\"q\" type=\"text\" placeholder=\"AAPL, Vanguard, Energy...\" />
      </label>
      <label>Asset class
        <select id=\"assetClass\">
          <option value=\"all\">All</option>
          <option value=\"stock\">Stocks</option>
          <option value=\"etf\">ETFs</option>
        </select>
      </label>
      <label>Region
        <select id=\"region\">
          <option value=\"all\">All</option>
          <option value=\"us\">US</option>
          <option value=\"eu\">EU</option>
          <option value=\"asia\">Asia</option>
        </select>
      </label>
      <label>Min score
        <input id=\"minScore\" type=\"number\" min=\"0\" max=\"100\" step=\"0.1\" placeholder=\"0\" />
      </label>
      <label>Min 1M return (%)
        <input id=\"min1m\" type=\"number\" step=\"0.1\" placeholder=\"e.g. 0\" />
      </label>
      <label>Min 1Y return (%)
        <input id=\"min1y\" type=\"number\" step=\"0.1\" placeholder=\"e.g. 0\" />
      </label>
    </section>

    <div class=\"count\" id=\"count\"></div>

    <table aria-label=\"Recommendations\">
      <thead>
        <tr>
          <th class="sortable num active" data-sort-key="rank" data-sort-type="number" tabindex="0" aria-sort="ascending">#<span class="sort-indicator">↑</span></th>
          <th class="sortable type-col" data-sort-key="asset_type" data-sort-type="text" tabindex="0" aria-sort="none">Type<span class="sort-indicator">↕</span></th>
          <th class="sortable" data-sort-key="ticker" data-sort-type="text" tabindex="0" aria-sort="none">Ticker<span class="sort-indicator">↕</span></th>
          <th class="link-col">T</th>
          <th class="link-col">Y</th>
          <th class="sortable" data-sort-key="name" data-sort-type="text" tabindex="0" aria-sort="none">Name<span class="sort-indicator">↕</span></th>
          <th class="desktop-only sortable region-col" data-sort-key="region" data-sort-type="text" tabindex="0" aria-sort="none">Region<span class="sort-indicator">↕</span></th>
          <th class="desktop-only sortable" data-sort-key="sector" data-sort-type="text" tabindex="0" aria-sort="none">Sector/Category<span class="sort-indicator">↕</span></th>
          <th class="num sortable" data-sort-key="price" data-sort-type="number" tabindex="0" aria-sort="none">Price<span class="sort-indicator">↕</span></th>
          <th class="num sortable" data-sort-key="return_1m" data-sort-type="number" tabindex="0" aria-sort="none">1M<span class="sort-indicator">↕</span></th>
          <th class="num sortable" data-sort-key="return_12m" data-sort-type="number" tabindex="0" aria-sort="none">1Y<span class="sort-indicator">↕</span></th>
          <th class="num sortable" data-sort-key="score" data-sort-type="number" tabindex="0" aria-sort="none">Score<span class="sort-indicator">↕</span></th>
          <th class="desktop-only sortable breakdown-col" data-sort-key="breakdown" data-sort-type="text" tabindex="0" aria-sort="none">Breakdown<span class="sort-indicator">↕</span></th>
          <th class="desktop-only sortable why-col" data-sort-key="why" data-sort-type="text" tabindex="0" aria-sort="none">Why<span class="sort-indicator">↕</span></th>
        </tr>
      </thead>
      <tbody id=\"tbody\">{initial_rows_html}</tbody>
    </table>
  </div>

  <script>
    const rows = {rows_json};
    const metadata = {metadata_json};

    const $ = (id) => document.getElementById(id);
    const controlIds = ["q", "assetClass", "region", "minScore", "min1m", "min1y"];
    const controls = controlIds.map($).filter(Boolean);
    const tableHeaders = Array.from(document.querySelectorAll("th.sortable"));
    const sortState = {{ key: "rank", dir: "asc", type: "number" }};

    function pctClass(v) {{
      if (v == null || Number.isNaN(v)) return "muted";
      return v >= 0 ? "pos" : "neg";
    }}

    function renderParams() {{
      const chips = [
        `top=${{metadata.top_n}}`,
        `asset_class=${{metadata.asset_class}}`,
        `region=${{metadata.region}}`,
        metadata.tickers.length ? `tickers=${{metadata.tickers.join(",")}}` : "tickers=curated",
      ];
      $("runParams").innerHTML = chips.map(c => `<span class=\"chip\">${{c}}</span>`).join("");
      const when = new Date(metadata.generated_at).toLocaleString();
      $("generatedAt").textContent = `Generated: ${{when}} · Rows: ${{metadata.row_count}}`;
    }}

    function asNum(value) {{
      if (value === "" || value == null) return null;
      const n = Number(value);
      return Number.isFinite(n) ? n : null;
    }}

    function applyFilters() {{
      const q = $("q").value.trim().toLowerCase();
      const asset = $("assetClass").value;
      const region = $("region").value;
      const minScore = asNum($("minScore").value);
      const min1m = asNum($("min1m").value);
      const min1y = asNum($("min1y").value);

      return rows.filter(r => {{
        if (q && !(r.ticker.toLowerCase().includes(q) || r.name.toLowerCase().includes(q) || r.sector.toLowerCase().includes(q))) return false;
        if (asset !== "all" && r.asset_type !== asset) return false;
        if (region !== "all" && r.region !== region) return false;
        if (minScore != null && (r.score == null || r.score < minScore)) return false;
        if (min1m != null && (r.return_1m == null || (r.return_1m * 100) < min1m)) return false;
        if (min1y != null && (r.return_12m == null || (r.return_12m * 100) < min1y)) return false;
        return true;
      }});
    }}

    function _cmpNullable(a, b, type) {{
      const aNull = a == null;
      const bNull = b == null;
      if (aNull && bNull) return 0;
      if (aNull) return 1;
      if (bNull) return -1;
      if (type === "number") return a - b;
      return String(a).localeCompare(String(b));
    }}

    function sortRows(items) {{
      const direction = sortState.dir === "asc" ? 1 : -1;
      return [...items].sort((a, b) => {{
        const cmp = _cmpNullable(a[sortState.key], b[sortState.key], sortState.type);
        return cmp * direction;
      }});
    }}

    function renderSortIndicators() {{
      tableHeaders.forEach(h => {{
        const key = h.dataset.sortKey;
        const indicator = h.querySelector(".sort-indicator");
        const active = key === sortState.key;
        h.classList.toggle("active", active);
        h.setAttribute("aria-sort", active ? (sortState.dir === "asc" ? "ascending" : "descending") : "none");
        if (indicator) indicator.textContent = active ? (sortState.dir === "asc" ? "↑" : "↓") : "↕";
      }});
    }}

    function renderTable(filtered) {{
      $("count").textContent = `Showing ${{filtered.length}} of ${{rows.length}} rows`;
      const body = filtered.map(r => {{
        const oneMClass = pctClass(r.return_1m);
        const oneYClass = pctClass(r.return_12m);
        const scoreText = Number.isFinite(r.score) ? r.score.toFixed(1) : "-";
        const googleUrl = (r.links && r.links.google) || r.ticker_url;
        const tvUrl = (r.links && r.links.tradingview) || r.ticker_url;
        const yahooUrl = (r.links && r.links.yahoo) || r.ticker_url;
        return `
          <tr>
            <td>${{r.rank}}</td>
            <td class="type-col">${{r.asset_type.toUpperCase()}}</td>
            <td><strong><a href="${{googleUrl}}" target="_blank" rel="noopener noreferrer">${{r.ticker}}</a></strong></td>
            <td class="link-col"><a href="${{tvUrl}}" target="_blank" rel="noopener noreferrer" title="TradingView"><span class="link-icon icon-tv">TV</span></a></td>
            <td class="link-col"><a href="${{yahooUrl}}" target="_blank" rel="noopener noreferrer" title="Yahoo Finance"><span class="link-icon icon-yahoo">Y</span></a></td>
            <td>${{r.name}}</td>
            <td class=\"desktop-only region-col\">${{r.region.toUpperCase()}}</td>
            <td class=\"desktop-only\">${{r.sector}}</td>
            <td class=\"num\">${{r.price_text}}</td>
            <td class=\"num ${{oneMClass}}\">${{r.return_1m_text}}</td>
            <td class=\"num ${{oneYClass}}\">${{r.return_12m_text}}</td>
            <td class=\"num\"><strong>${{scoreText}}</strong></td>
            <td class=\"desktop-only breakdown-col\">${{r.breakdown}}</td>
            <td class=\"desktop-only why-col\">${{r.why}}</td>
          </tr>
        `;
      }}).join("");
      $("tbody").innerHTML = body || '<tr><td colspan="14" class="muted">No rows match the selected filters.</td></tr>';
    }}

    function rerender() {{
      renderTable(sortRows(applyFilters()));
      renderSortIndicators();
    }}

    function setSortFromHeader(header) {{
      const key = header.dataset.sortKey;
      const type = header.dataset.sortType || "text";
      if (!key) return;
      if (sortState.key === key) {{
        sortState.dir = sortState.dir === "asc" ? "desc" : "asc";
      }} else {{
        sortState.key = key;
        sortState.type = type;
        sortState.dir = "asc";
      }}
      rerender();
    }}

    renderParams();
    controls.forEach(c => {{
      c.addEventListener("input", rerender);
      c.addEventListener("change", rerender);
    }});
    tableHeaders.forEach(h => {{
      h.addEventListener("click", () => setSortFromHeader(h));
      h.addEventListener("keydown", (ev) => {{
        if (ev.key === "Enter" || ev.key === " ") {{
          ev.preventDefault();
          setSortFromHeader(h);
        }}
      }});
    }});
    rerender();
  </script>
</body>
</html>
"""

    with open(destination, "w", encoding="utf-8") as f:
        f.write(html)

    return destination
