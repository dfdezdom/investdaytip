"""Self-contained HTML export for recommendation results."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from html import escape
from typing import Any, TypeGuard
from urllib.parse import quote, quote_plus

from investdaytip.backtest import BacktestResult, _interpret_backtest
from investdaytip.scoring import ScoredAsset

# Base number of <th> columns excluding the optional Superinvestors column.
# Used for the empty-state row colspan server- and client-side.
_TABLE_BASE_COLUMN_COUNT = 16


def _is_finite_number(v: Any) -> TypeGuard[float]:
    """True only for real, finite numeric values (excludes None/NaN/inf/bool)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pct_class(v: Any) -> str:
    """Mirror the client-side ``pctClass``: muted for missing/NaN, else pos/neg."""
    if not _is_finite_number(v):
        return "muted"
    return "pos" if v >= 0 else "neg"


def infer_region_from_ticker(ticker: str) -> str:
    """Infer region from Yahoo ticker suffix.

    The mapping follows the project's curated universes and README conventions.
    """
    upper = ticker.upper()
    suffix = upper.rsplit(".", 1)[-1] if "." in upper else ""
    eu_suffixes = {"DE", "F", "PA", "AS", "L", "MC", "MI", "SW", "ST", "CO", "HE", "OL"}
    asia_suffixes = {"T", "HK", "SI", "NS", "KS", "TW", "AX", "SS", "SZ"}
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
    if not _is_finite_number(value):
        return "-"
    return f"{float(value) * 100:.2f}%"


def _google_finance_url(ticker: str, exchange_hint: str | None = None) -> str:
  base, _ = _split_ticker(ticker)
  google_exchange, _ = _exchange_mapping(ticker, exchange_hint=exchange_hint)
  if not google_exchange:
    query = quote_plus(ticker)
    return f"https://www.google.com/finance?hl=en&q={query}"
  symbol = quote_plus(base)
  exch = quote_plus(google_exchange)
  return f"https://www.google.com/finance/quote/{symbol}:{exch}?hl=en"


def _split_ticker(ticker: str) -> tuple[str, str]:
  t = ticker.upper()
  if "." in t:
    base, suffix = t.rsplit(".", 1)
    return base, suffix
  return t, ""


def _normalize_exchange_hint(exchange_hint: str | None) -> tuple[str | None, str | None]:
  if not exchange_hint:
    return None, None
  hint = exchange_hint.strip().upper()
  # (Google Finance exchange, TradingView exchange)
  mapping = {
    "NMS": ("NASDAQ", "NASDAQ"),
    "NGM": ("NASDAQ", "NASDAQ"),
    "NCM": ("NASDAQ", "NASDAQ"),
    "NYQ": ("NYSE", "NYSE"),
    "ASE": ("NYSEARCA", "AMEX"),
    "PCX": ("NYSEARCA", "AMEX"),
    "BTS": ("NYSE", "NYSE"),
    "TOR": ("TSE", "TSX"),
    "TSX": ("TSE", "TSX"),
  }
  return mapping.get(hint, (None, None))


def _exchange_mapping(ticker: str, exchange_hint: str | None = None) -> tuple[str | None, str | None]:
  base, suffix = _split_ticker(ticker)

  google_from_hint, tv_from_hint = _normalize_exchange_hint(exchange_hint)
  if google_from_hint and tv_from_hint:
    return google_from_hint, tv_from_hint

  # (Google Finance exchange, TradingView exchange)
  us_overrides = {
    # Unsuffixed US tickers are ambiguous; these overrides fix known NYSE symbols.
    "HWM": ("NYSE", "NYSE"),
  }
  if suffix == "" and base in us_overrides:
    return us_overrides[base]

  mapping = {
    "": ("NASDAQ", "NASDAQ"),
    "DE": ("ETR", "XETR"),
    "F": ("FRA", "FWB"),
    "PA": ("EPA", "EURONEXT"),
    "AS": ("AMS", "EURONEXT"),
    "L": ("LON", "LSE"),
    "MC": ("BME", "BME"),
    "MI": ("BIT", "MIL"),
    "SW": ("SWX", "SIX"),
    "ST": ("STO", "OMXSTO"),
    "CO": ("CPH", "OMXCOP"),
    "HE": ("HEL", "OMXHEX"),
    "OL": ("OSL", "OSL"),
    "T": ("TYO", "TSE"),
    "HK": ("HKG", "HKEX"),
    "SI": ("SGX", "SGX"),
    "NS": ("NSE", "NSE"),
    "KS": ("KRX", "KRX"),
    "TW": ("TPE", "TWSE"),
    "AX": ("ASX", "ASX"),
    "TO": ("TSE", "TSX"),
    "SS": ("SHA", "SSE"),
    "SZ": ("SHE", "SZSE"),
  }
  return mapping.get(suffix, (None, None))


def _tradingview_url(ticker: str, exchange_hint: str | None = None) -> str:
  base, _ = _split_ticker(ticker)
  _, tv_exchange = _exchange_mapping(ticker, exchange_hint=exchange_hint)
  if not tv_exchange:
    return f"https://www.tradingview.com/symbols/{quote(ticker, safe='')}"
  tv_symbol = f"{tv_exchange}:{base}"
  return f"https://www.tradingview.com/symbols/{quote(tv_symbol, safe=':')}"


def _yahoo_finance_url(ticker: str) -> str:
  return f"https://finance.yahoo.com/quote/{quote_plus(ticker)}"


def _build_links(ticker: str, exchange_hint: str | None = None) -> dict[str, str]:
  return {
    "google": _google_finance_url(ticker, exchange_hint=exchange_hint),
    "tradingview": _tradingview_url(ticker, exchange_hint=exchange_hint),
    "yahoo": _yahoo_finance_url(ticker),
  }


def _as_row(index: int, s: ScoredAsset) -> dict[str, Any]:
  d = s.data
  pe_value = getattr(d, "trailing_pe", None)
  exchange_hint = getattr(d, "exchange", None)
  rsi_value = getattr(d, "rsi_14", None)
  macd_value = getattr(d, "macd_histogram", None)
  return {
    "rank": index,
    "asset_type": s.asset_type.lower(),
    "ticker": d.ticker,
    "ticker_url": _google_finance_url(d.ticker, exchange_hint=exchange_hint),
    "links": _build_links(d.ticker, exchange_hint=exchange_hint),
    "name": d.name or "-",
    "sector": d.sector or "-",
    "region": infer_region_from_ticker(d.ticker),
    "price": d.current_price,
    "price_text": _fmt_price(d.current_price, getattr(d, "currency", None)),
    "pe": pe_value,
    "pe_text": f"{float(pe_value):.2f}" if _is_finite_number(pe_value) else "-",
    "daily_change": d.daily_change,
    "daily_change_text": _fmt_pct(d.daily_change),
    "return_1m": d.return_1m,
    "return_1m_text": _fmt_pct(d.return_1m),
    "return_12m": d.return_12m,
    "return_12m_text": _fmt_pct(d.return_12m),
    "superinvestor_count": s.superinvestor_count,
    "superinvestor_count_text": f"{s.superinvestor_count}" if s.superinvestor_count is not None else "-",
    "rsi": rsi_value,
    "rsi_text": f"{float(rsi_value):.1f}" if _is_finite_number(rsi_value) else "-",
    "macd": macd_value,
    "macd_text": f"{float(macd_value) * 100:.2f}%" if _is_finite_number(macd_value) else "-",
    "score": round(s.total, 2),
    "breakdown": " / ".join(f"{int(round(v))}" for v in s.breakdown.values()),
    "why": "; ".join(s.rationale[:3]) if s.rationale else "-",
  }


def _render_initial_rows(rows: list[dict[str, Any]], include_superinvestor: bool = False, include_technical: bool = False, column_count: int = 16) -> str:
  if not rows:
    return (
      f'<tr><td colspan="{column_count}" class="muted">'
      'No recommendations were generated for this run.</td></tr>'
    )

  out: list[str] = []
  for r in rows:
    one_m_class = _pct_class(r["return_1m"])
    one_y_class = _pct_class(r["return_12m"])
    daily_class = _pct_class(r["daily_change"])
    score = r.get("score")
    score_txt = f"{float(score):.1f}" if _is_finite_number(score) else "-"

    cells = (
      f"<td>{int(r['rank'])}</td>"
      f"<td class=\"type-col\">{escape(str(r['asset_type']).upper())}</td>"
      f"<td><strong><a href=\"{escape(str(r['ticker_url']))}\" target=\"_blank\" rel=\"noopener noreferrer\">{escape(str(r['ticker']))}</a></strong></td>"
      f"<td class=\"link-col\"><a href=\"{escape(str(r['links']['tradingview']))}\" target=\"_blank\" rel=\"noopener noreferrer\" title=\"TradingView\"><span class=\"link-icon icon-tv\">TV</span></a></td>"
      f"<td class=\"link-col\"><a href=\"{escape(str(r['links']['yahoo']))}\" target=\"_blank\" rel=\"noopener noreferrer\" title=\"Yahoo Finance\"><span class=\"link-icon icon-yahoo\">Y</span></a></td>"
      f"<td>{escape(str(r['name']))}</td>"
      f"<td class=\"desktop-only region-col\">{escape(str(r['region']).upper())}</td>"
      f"<td class=\"desktop-only\">{escape(str(r['sector']))}</td>"
      f"<td class=\"num\">{escape(str(r['price_text']))}</td>"
      f"<td class=\"num {daily_class}\">{escape(str(r['daily_change_text']))}</td>"
      f"<td class=\"num\">{escape(str(r['pe_text']))}</td>"
      f"<td class=\"num {one_m_class}\">{escape(str(r['return_1m_text']))}</td>"
      f"<td class=\"num {one_y_class}\">{escape(str(r['return_12m_text']))}</td>"
    )
    if include_superinvestor:
      cells += f"<td class=\"num\">{escape(str(r['superinvestor_count_text']))}</td>"
    if include_technical:
      cells += (
        f"<td class=\"num\">{escape(str(r['rsi_text']))}</td>"
        f"<td class=\"num\">{escape(str(r['macd_text']))}</td>"
      )
    cells += (
      f"<td class=\"num\"><strong>{escape(score_txt)}</strong></td>"
      f"<td class=\"desktop-only breakdown-col\">{escape(str(r['breakdown']))}</td>"
      f"<td class=\"desktop-only why-col\">{escape(str(r['why']))}</td>"
    )
    out.append(f"<tr>{cells}</tr>")
  return "".join(out)


def export_recommendations_html(
    results: list[ScoredAsset],
    destination: str,
    *,
    top_n: int,
    asset_class: str,
    region: str | list[str] = "all",
    currency: str | list[str] = "all",
    tickers: list[str] | None,
    tickers_file: str | None = None,
    include_superinvestor: bool = False,
    include_technical: bool = False,
    sector: str | None = None,
    fear_greed: dict[str, Any] | None = None,
) -> str:
    """Write a self-contained, filterable HTML report to ``destination``.

    ``fear_greed`` should be fetched by the caller and passed in to avoid a
    side-effect network call inside the rendering function.
    """
    col_count = _TABLE_BASE_COLUMN_COUNT + (1 if include_superinvestor else 0) + (2 if include_technical else 0)
    rows = [_as_row(i, s) for i, s in enumerate(results, start=1)]
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "top_n": top_n,
        "asset_class": asset_class,
        "region": region,
        "currency": currency,
        "tickers": tickers or [],
        "tickers_file": tickers_file,
        "row_count": len(rows),
        "sector": sector,
        "fear_greed": fear_greed if fear_greed is not None else {},
    }

    rows_json = json.dumps(rows, ensure_ascii=True)
    metadata_json = json.dumps(metadata, ensure_ascii=True)
    initial_rows_html = _render_initial_rows(rows, include_superinvestor=include_superinvestor, include_technical=include_technical, column_count=col_count)

    # Build optional column sections
    if include_superinvestor:
        superinvestor_th = '<th class="num sortable" data-sort-key="superinvestor_count" data-sort-type="number" tabindex="0" aria-sort="none">Superinvestors<span class="sort-indicator">↕</span></th>\n          '
        superinvestor_td = '<td class="num">${r.superinvestor_count_text}</td>\n            '
    else:
        superinvestor_th = ""
        superinvestor_td = ""
    if include_technical:
        technical_th = '<th class="num sortable" data-sort-key="rsi" data-sort-type="number" tabindex="0" aria-sort="none">RSI<span class="sort-indicator">↕</span></th>\n          <th class="num sortable" data-sort-key="macd" data-sort-type="number" tabindex="0" aria-sort="none">MACD<span class="sort-indicator">↕</span></th>\n          '
        technical_td = '<td class="num">${r.rsi_text}</td>\n            <td class="num">${r.macd_text}</td>\n            '
    else:
        technical_th = ""
        technical_td = ""

    html = f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>InvestDayTip Report</title>
  <style>
    :root {{
      --bg: #f3f5ef;
      --bg-spot: #e9f4dc;
      --panel: #ffffff;
      --line: #dde5cf;
      --text: #1f2a1f;
      --muted: #58664d;
      --accent: #2f7d4a;
      --accent-soft: #d8f0df;
      --chip-line: #b7dcc4;
      --chip-text: #16482a;
      --input-bg: #fdfefb;
      --input-line: #c9d4b5;
      --thead-bg: #e9f2df;
      --thead-text: #2a402d;
      --tbody-line: #edf2e6;
      --shadow: 0 8px 20px rgba(35, 45, 22, 0.08);
      --tv-icon: #1b1f2a;
      --link: #1f6037;
      --link-hover: #2f7d4a;
      --neg: #a84035;
      --pos: #1e7a35;
    }}
    :root[data-theme="dark"] {{
      --bg: #101714;
      --bg-spot: #1a2a22;
      --panel: #17221d;
      --line: #2a3a31;
      --text: #dde7df;
      --muted: #9bb0a3;
      --accent: #77c18f;
      --accent-soft: #24382e;
      --chip-line: #355444;
      --chip-text: #cde3d4;
      --input-bg: #13201a;
      --input-line: #3a5144;
      --thead-bg: #203329;
      --thead-text: #d5e7da;
      --tbody-line: #24352d;
      --shadow: 0 10px 24px rgba(0, 0, 0, 0.35);
      --tv-icon: #2d3f5c;
      --link: #9fe3b1;
      --link-hover: #c9f2d4;
      --neg: #ff9288;
      --pos: #8fe4a3;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: radial-gradient(circle at top right, var(--bg-spot) 0, var(--bg) 40%), var(--bg);
    }}
    .wrap {{ width: 100%; max-width: none; margin: 0; padding: 20px 20px 36px; }}
    .topbar {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }}
    h1 {{ margin: 0 0 8px; font-size: 1.6rem; }}
    .theme-toggle {{
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 999px;
      font-size: 0.84rem;
      padding: 6px 10px;
      cursor: pointer;
      white-space: nowrap;
    }}
    .theme-toggle:hover {{ border-color: var(--accent); }}
    a {{ color: var(--link); }}
    a:hover {{ color: var(--link-hover); }}
    a:focus-visible {{
      outline: 2px solid var(--link-hover);
      outline-offset: 2px;
      border-radius: 2px;
    }}
    .meta {{ color: var(--muted); font-size: 0.92rem; margin-bottom: 16px; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px; }}
    .chip {{
      background: var(--accent-soft);
      border: 1px solid var(--chip-line);
      color: var(--chip-text);
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
      box-shadow: var(--shadow);
    }}
    label {{ display: grid; gap: 6px; font-size: 0.86rem; color: var(--muted); }}
    input, select {{
      width: 100%;
      padding: 9px 10px;
      border: 1px solid var(--input-line);
      border-radius: 10px;
      background: var(--input-bg);
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
      box-shadow: var(--shadow);
    }}
    thead th {{
      text-align: left;
      font-size: 0.82rem;
      letter-spacing: 0.02em;
      color: var(--thead-text);
      background: var(--thead-bg);
      border-bottom: 1px solid var(--line);
      padding: 10px;
      position: sticky;
      top: 0;
      z-index: 1;
    }}
    th.sortable {{ cursor: pointer; user-select: none; }}
    th.sortable.active {{ color: var(--accent); }}
    .sort-indicator {{ margin-left: 4px; color: #6d7f68; }}
    tbody td {{ border-top: 1px solid var(--tbody-line); padding: 10px; font-size: 0.92rem; vertical-align: top; }}
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
    .icon-tv {{ background: var(--tv-icon); }}
    .icon-yahoo {{ background: #5f01d1; }}
    .muted {{ color: var(--muted); }}
    .pos {{ color: var(--pos); font-weight: 600; }}
    .neg {{ color: var(--neg); font-weight: 600; }}
    .count {{ margin: 8px 0 12px; color: var(--muted); font-size: 0.9rem; }}
    @media (max-width: 900px) {{
      .desktop-only {{ display: none; }}
      body {{ font-size: 14px; }}
      .wrap {{ padding: 14px 12px 24px; }}
      .topbar {{ align-items: center; }}
      tbody td, thead th {{ padding: 8px; }}
    }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"topbar\">
      <h1>InvestDayTip Report</h1>
      <button id=\"themeToggle\" class=\"theme-toggle\" type=\"button\" aria-label=\"Toggle dark mode\">Dark mode</button>
    </div>
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
          <option value=\"superinvestor\">Superinvestors</option>
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
          <th class="num sortable" data-sort-key="daily_change" data-sort-type="number" tabindex="0" aria-sort="none">% Today<span class="sort-indicator">↕</span></th>
          <th class="num sortable" data-sort-key="pe" data-sort-type="number" tabindex="0" aria-sort="none">P/E<span class="sort-indicator">↕</span></th>
          <th class="num sortable" data-sort-key="return_1m" data-sort-type="number" tabindex="0" aria-sort="none">1M<span class="sort-indicator">↕</span></th>
          <th class="num sortable" data-sort-key="return_12m" data-sort-type="number" tabindex="0" aria-sort="none">1Y<span class="sort-indicator">↕</span></th>
          {superinvestor_th}{technical_th}<th class="num sortable" data-sort-key="score" data-sort-type="number" tabindex="0" aria-sort="none">Score<span class="sort-indicator">↕</span></th>
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
    const THEME_KEY = "investdaytip-theme";

    function preferredTheme() {{
      const fromStorage = localStorage.getItem(THEME_KEY);
      if (fromStorage === "light" || fromStorage === "dark") return fromStorage;
      if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) return "dark";
      return "light";
    }}

    function applyTheme(theme) {{
      const nextTheme = theme === "dark" ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", nextTheme);
      localStorage.setItem(THEME_KEY, nextTheme);
      const btn = $("themeToggle");
      if (btn) {{
        btn.textContent = nextTheme === "dark" ? "Light mode" : "Dark mode";
        btn.setAttribute("aria-pressed", nextTheme === "dark" ? "true" : "false");
      }}
    }}

    function pctClass(v) {{
      if (v == null || Number.isNaN(v)) return "muted";
      return v >= 0 ? "pos" : "neg";
    }}

    function renderParams() {{
      const chips = [
        `top=${{metadata.top_n}}`,
        `asset_class=${{metadata.asset_class}}`,
        `region=${{metadata.region}}`,
        `currency=${{metadata.currency}}`,
        metadata.tickers.length ? `tickers=${{metadata.tickers.join(",")}}` : "tickers=curated",
      ];
      if (metadata.tickers_file) chips.push(`tickers_file=${{metadata.tickers_file}}`);
      const fg = metadata.fear_greed;
      if (fg && fg.score != null) {{
        const fgIcons = {{ "extreme fear": "🟢", "fear": "🟡", "neutral": "⚪", "greed": "🟠", "extreme greed": "🔴" }};
        const icon = fgIcons[fg.rating] || "⚪";
        chips.push(`Fear & Greed: ${{icon}} ${{fg.score}} (${{fg.rating}})`);
      }}
      $("runParams").innerHTML = chips.map(c => `<span class=\"chip\">${{c}}</span>`).join("");
      const when = new Date(metadata.generated_at).toLocaleString();
      $("generatedAt").textContent = `Generated: ${{when}} · Rows: ${{metadata.row_count}}`;
    }}

    function asNum(value) {{
      if (value === "" || value == null) return null;
      const n = Number(value);
      return Number.isFinite(n) ? n : null;
    }}

    function escapeHtml(s) {{
      if (s == null) return "";
      const d = document.createElement("div");
      d.textContent = String(s);
      return d.innerHTML;
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
        const dailyClass = pctClass(r.daily_change);
        const scoreText = Number.isFinite(r.score) ? r.score.toFixed(1) : "-";
        const googleUrl = (r.links && r.links.google) || r.ticker_url;
        const tvUrl = (r.links && r.links.tradingview) || r.ticker_url;
        const yahooUrl = (r.links && r.links.yahoo) || r.ticker_url;
        return `
          <tr>
            <td>${{r.rank}}</td>
            <td class="type-col">${{escapeHtml(r.asset_type.toUpperCase())}}</td>
            <td><strong><a href="${{googleUrl}}" target="_blank" rel="noopener noreferrer">${{escapeHtml(r.ticker)}}</a></strong></td>
            <td class="link-col"><a href="${{tvUrl}}" target="_blank" rel="noopener noreferrer" title="TradingView"><span class="link-icon icon-tv">TV</span></a></td>
            <td class="link-col"><a href="${{yahooUrl}}" target="_blank" rel="noopener noreferrer" title="Yahoo Finance"><span class="link-icon icon-yahoo">Y</span></a></td>
            <td>${{escapeHtml(r.name)}}</td>
            <td class="desktop-only region-col">${{escapeHtml(r.region.toUpperCase())}}</td>
            <td class="desktop-only">${{escapeHtml(r.sector)}}</td>
            <td class="num">${{r.price_text}}</td>
            <td class="num ${{dailyClass}}">${{r.daily_change_text}}</td>
            <td class="num">${{r.pe_text}}</td>
            <td class="num ${{oneMClass}}">${{r.return_1m_text}}</td>
            <td class="num ${{oneYClass}}">${{r.return_12m_text}}</td>
            {superinvestor_td}{technical_td}<td class="num"><strong>${{scoreText}}</strong></td>
            <td class="desktop-only breakdown-col">${{escapeHtml(r.breakdown)}}</td>
            <td class="desktop-only why-col">${{escapeHtml(r.why)}}</td>
          </tr>
        `;
      }}).join("");
      $("tbody").innerHTML = body || '<tr><td colspan="' + {col_count} + '" class="muted">No rows match the selected filters.</td></tr>';
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
    applyTheme(preferredTheme());
    $("themeToggle")?.addEventListener("click", () => {{
      const current = document.documentElement.getAttribute("data-theme") || "light";
      applyTheme(current === "dark" ? "light" : "dark");
    }});
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


# ── Backtest HTML export ──────────────────────────────────────────────────────



def _fmt_num(value: Any, decimals: int = 2) -> str:
    if not _is_finite_number(value):
        return "-"
    return f"{float(value):,.{decimals}f}"


_SNAPSHOT_COL_COUNT = 6


def _render_backtest_rows(result: BacktestResult) -> str:
    if not result.snapshots:
        return (
            f'<tr><td colspan="{_SNAPSHOT_COL_COUNT}" class="muted">'
            "No snapshots were generated.</td></tr>"
        )
    out: list[str] = []
    for snap in result.snapshots:
        picks_str = ", ".join(
            f"{p.data.ticker} ({p.total:.1f})" for p in snap.picks
        )
        r6 = _fmt_pct(snap.avg_return_6m)
        r12 = _fmt_pct(snap.avg_return_12m)
        b6 = _fmt_pct(snap.benchmark_return_6m)
        b12 = _fmt_pct(snap.benchmark_return_12m)

        r6_cls = _pct_class(snap.avg_return_6m)
        r12_cls = _pct_class(snap.avg_return_12m)
        b6_cls = _pct_class(snap.benchmark_return_6m)
        b12_cls = _pct_class(snap.benchmark_return_12m)

        out.append(
            f"<tr>"
            f"<td>{snap.date.strftime('%Y-%m-%d')}</td>"
            f"<td>{escape(picks_str)}</td>"
            f"<td class=\"num {r6_cls}\">{r6}</td>"
            f"<td class=\"num {r12_cls}\">{r12}</td>"
            f"<td class=\"num {b6_cls}\">{b6}</td>"
            f"<td class=\"num {b12_cls}\">{b12}</td>"
            f"</tr>"
        )
    return "".join(out)


def export_backtest_html(
    result: BacktestResult,
    destination: str,
    *,
    tickers: list[str] | None = None,
    top_n: int = 5,
    region: str = "us",
    interval_months: int = 3,
) -> str:
    """Write a self-contained HTML backtest report."""
    summary_metrics = [
        ("Snapshots", str(result.total_snapshots), ""),
        ("Cumulative Return", _fmt_pct(result.cumulative_return), _pct_class(result.cumulative_return)),
        ("Benchmark Return", _fmt_pct(result.benchmark_cumulative_return), _pct_class(result.benchmark_cumulative_return)),
        ("Alpha", _fmt_pct(result.alpha), _pct_class(result.alpha)),
        ("Sharpe Ratio", _fmt_num(result.sharpe), ""),
        ("Benchmark Sharpe", _fmt_num(result.benchmark_sharpe), ""),
        ("Win Rate 6M", _fmt_pct(result.win_rate_6m), "pos"),
        ("Win Rate 12M", _fmt_pct(result.win_rate_12m), "pos"),
        ("Max Drawdown", _fmt_pct(result.max_drawdown), "neg"),
    ]
    meta_rows = (
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"Region: {region} · Top {top_n} picks per snapshot · "
        f"Interval: {interval_months}mo · "
        f"Snapshots: {result.total_snapshots}"
    )
    if tickers:
        meta_rows += f" · Tickers: {', '.join(tickers)}"

    rows_html = _render_backtest_rows(result)

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>InvestDayTip — Backtest Report</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0; padding: 24px; background: #f3f5ef; color: #1f2a1f;
    }}
    .wrap {{ max-width: 1200px; margin: 0 auto; }}
    h1 {{ margin: 0 0 4px; font-size: 1.5rem; }}
    .meta {{ color: #58664d; font-size: 0.88rem; margin-bottom: 20px; }}
    .summary {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: 10px; background: #fff; border: 1px solid #dde5cf;
      border-radius: 12px; padding: 16px; margin-bottom: 20px;
    }}
    .summary-item {{ text-align: center; }}
    .summary-label {{ font-size: 0.78rem; color: #58664d; text-transform: uppercase; letter-spacing: 0.04em; }}
    .summary-value {{ font-size: 1.2rem; font-weight: 700; margin-top: 4px; }}
    table {{
      width: 100%; border-collapse: collapse; background: #fff;
      border: 1px solid #dde5cf; border-radius: 12px; overflow: hidden;
    }}
    thead th {{
      text-align: left; font-size: 0.82rem; letter-spacing: 0.02em;
      color: #2a402d; background: #e9f2df;
      border-bottom: 1px solid #dde5cf; padding: 10px;
      position: sticky; top: 0; z-index: 1;
    }}
    tbody td {{ border-top: 1px solid #edf2e6; padding: 10px; font-size: 0.92rem; vertical-align: top; }}
    .num {{ text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }}
    .pos {{ color: #1e7a35; font-weight: 600; }}
    .neg {{ color: #a84035; font-weight: 600; }}
    .muted {{ color: #58664d; }}
    .errors {{ background: #fff5f5; border: 1px solid #e8c4c4; border-radius: 10px; padding: 12px; margin-top: 16px; }}
    .errors h3 {{ margin: 0 0 6px; font-size: 0.95rem; color: #a84035; }}
    .errors ul {{ margin: 0; padding-left: 20px; color: #7a3a30; }}
    .interpretation {{ background: #fff; border: 1px solid #dde5cf; border-radius: 12px; padding: 14px 18px; margin-bottom: 20px; line-height: 1.55; color: #1f2a1f; font-size: 0.95rem; }}
    @media (max-width: 700px) {{
      body {{ padding: 14px; }}
      .summary {{ grid-template-columns: repeat(2, 1fr); }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Backtest Report</h1>
    <div class="meta">{meta_rows}</div>

    <div class="summary">
      {"".join(
        f'<div class="summary-item"><div class="summary-label">{label}</div>'
        f'<div class="summary-value {cls}">{val}</div></div>'
        for label, val, cls in summary_metrics
      )}
    </div>

    <div class="interpretation">{escape(_interpret_backtest(result))}</div>

    <table>
      <thead>
        <tr>
          <th>Date</th>
          <th>Picks (score)</th>
          <th class="num">Port. 6M</th>
          <th class="num">Port. 12M</th>
          <th class="num">Bench. 6M</th>
          <th class="num">Bench. 12M</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>

    {"".join(
      f'<div class="errors"><h3>Warnings / Errors</h3><ul>'
      f'{"".join(f"<li>{escape(e)}</li>" for e in result.errors)}</ul></div>'
    ) if result.errors else ""}
  </div>
</body>
</html>"""

    with open(destination, "w", encoding="utf-8") as f:
        f.write(html)
    return destination
