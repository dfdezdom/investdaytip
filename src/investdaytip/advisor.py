"""Market analysis and portfolio advisor.

Fetches VIX/VXN via yfinance to determine market regime, bubble risk,
and produces buy/hold/sell signals. Integrates with InvestDayTip's
scoring engine for portfolio review and buy recommendations.
"""

from __future__ import annotations

import argparse
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from investdaytip.data_source import _suppress_stderr
from investdaytip.dataroma import fetch_superinvestor_universe, get_superinvestor_data
from investdaytip.html_export import export_recommendations_html
from investdaytip.main import _load_tickers_from_file, _parse_min_market_cap, _render
from investdaytip.recommender import recommend
from investdaytip.sentiment import fear_greed_index

logger = logging.getLogger(__name__)

# VIX thresholds
VIX_BULLISH = 15
VIX_NEUTRAL = 25
VIX_BEARISH = 35


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

def _fetch_index(ticker: str) -> Optional[float]:
    """Fetch latest close of any index via yfinance."""
    with _suppress_stderr():
        t = yf.Ticker(ticker)
        hist = t.history(period="5d", interval="1d")
    if hist is not None and not hist.empty and "Close" in hist:
        val = float(hist["Close"].iloc[-1])
        if math.isfinite(val):
            return val
    return None


# ---------------------------------------------------------------------------
# Market regime
# ---------------------------------------------------------------------------

def market_regime() -> dict:
    """Determine market regime from VIX (+ VXN).

    Returns:
        dict with keys: vix, vxn, regime, label, description, action
    """
    vix = _fetch_index("^VIX")
    vxn = _fetch_index("^VXN")

    if vix is None:
        return {
            "vix": None, "vxn": vxn,
            "regime": "unknown",
            "label": "No data",
            "description": "Could not fetch VIX.",
            "action": "hold",
        }

    if vix > VIX_BEARISH:
        regime, label, action = "crash", "🔴 Crash / Panic", "sell"
    elif vix > VIX_NEUTRAL:
        regime, label, action = "bearish", "🟠 Elevated fear", "hold"
    elif vix > VIX_BULLISH:
        regime, label, action = "neutral", "🟡 Normal market", "buy"
    else:
        regime, label, action = "bullish", "🟢 Calm / Good entry", "buy"

    descriptions = {
        "crash": "VIX very high. Probable correction underway. Prioritize defense.",
        "bearish": "Uncertainty. Reduce risk, increase defensives.",
        "neutral": "Normal conditions. Selective value picking.",
        "bullish": "Low VIX. Calm market, good time to buy.",
    }

    return {
        "vix": vix,
        "vxn": vxn,
        "regime": regime,
        "label": label,
        "description": descriptions[regime],
        "action": action,
    }


# ---------------------------------------------------------------------------
# Bubble risk
# ---------------------------------------------------------------------------

def bubble_risk() -> dict:
    """Assess bubble risk via VIX historical percentile.

    VIX persistently below its 2-year median suggests complacency.
    """
    with _suppress_stderr():
        t = yf.Ticker("^VIX")
        hist = t.history(period="2y", interval="1d")
    if hist is None or hist.empty or "Close" not in hist:
        return {"level": "unknown", "pct_rank": None, "note": "No historical VIX data."}

    closes = hist["Close"].dropna().values
    if len(closes) < 20:
        return {"level": "unknown", "pct_rank": None, "note": "Insufficient data."}

    current = closes[-1]
    rank = (closes < current).sum() / len(closes) * 100  # percentile

    if rank > 90:
        level = "high"
        note = "VIX in high percentile — extreme panic, possible bottom"
    elif rank < 15:
        level = "high"
        note = "VIX in very low percentile — complacency, bubble risk"
    elif rank < 30:
        level = "medium"
        note = "Low VIX — possible overconfidence"
    else:
        level = "low"
        note = "VIX in normal range, no bubble signals"

    return {"level": level, "pct_rank": round(rank, 1), "note": note}


# ---------------------------------------------------------------------------
# Macro indicators
# ---------------------------------------------------------------------------

def _fetch_yield_curve() -> dict[str, Optional[float]]:
    """Fetch 10Y and 2Y Treasury yields and compute spread."""
    y10 = _fetch_index("^TNX")
    y2 = _fetch_index("2YY=F")
    if y10 is not None and y2 is not None:
        return {"y10": y10, "y2": y2, "spread": y10 - y2}
    return {}


def _fetch_bond_volatility() -> Optional[float]:
    """Fetch MOVE index (bond volatility)."""
    return _fetch_index("^MOVE")


def _fetch_dxy() -> Optional[float]:
    """Fetch US Dollar Index."""
    return _fetch_index("DX-Y.NYB")


def macro_regime() -> dict:
    """Composite macro regime combining VIX, yield curve, bond vol and DXY.

    Returns a 0-100 macro health score and a regime label.
    """
    vix_data = market_regime()
    yield_data = _fetch_yield_curve()
    move = _fetch_bond_volatility()
    dxy = _fetch_dxy()

    score = 50  # neutral

    # VIX impact
    if vix_data["regime"] == "crash":
        score -= 20
    elif vix_data["regime"] == "bearish":
        score -= 10
    elif vix_data["regime"] == "bullish":
        score += 10

    # Yield curve impact
    spread = yield_data.get("spread")
    if spread is not None:
        if spread < 0:
            score -= 20
        elif spread < 0.5:
            score -= 10
        elif spread > 1.0:
            score += 5

    # MOVE impact
    if move is not None:
        if move > 120:
            score -= 15
        elif move > 100:
            score -= 10
        elif move < 60:
            score += 5

    # DXY impact
    if dxy is not None:
        if dxy > 105:
            score -= 10
        elif dxy > 100:
            score -= 5
        elif dxy < 95:
            score += 5

    # Fear & Greed impact
    fg = fear_greed_index()
    fg_score = fg["score"] if fg and fg.get("score") is not None else None
    if fg_score is not None:
        if fg_score < 25:
            score += 10  # extreme fear — contrarian buy
        elif fg_score < 45:
            score += 5   # fear — mildly oversold
        elif fg_score > 75:
            score -= 10  # extreme greed — complacency risk
        elif fg_score > 55:
            score -= 5   # greed — mildly overbought

    score = max(0, min(100, score))

    if score >= 70:
        regime, action = "healthy", "buy"
        label = "🟢 Macro healthy"
        desc = "Favorable macro backdrop. Good for long-term equity exposure."
    elif score >= 45:
        regime, action = "neutral", "hold"
        label = "🟡 Mixed signals"
        desc = "Some macro headwinds but no major risks. Selective buying."
    elif score >= 25:
        regime, action = "warning", "hold"
        label = "🟠 Macro warning"
        desc = "Multiple macro stress signals. Reduce risk, favor defensives."
    else:
        regime, action = "danger", "sell"
        label = "🔴 Macro danger"
        desc = "Severe macro stress. Consider raising cash or hedging."

    return {
        "score": score,
        "regime": regime,
        "label": label,
        "description": desc,
        "action": action,
        "vix": vix_data,
        "yield": yield_data,
        "move": move,
        "dxy": dxy,
        "fear_greed": fg,
    }


# ---------------------------------------------------------------------------
# Portfolio review
# ---------------------------------------------------------------------------

def portfolio_review(cartera_path: str, min_market_cap: float = 2_000_000_000) -> dict:
    """Score existing portfolio, flag weak positions."""
    if not Path(cartera_path).exists():
        return {"error": f"File not found: {cartera_path}"}

    tickers = _load_tickers_from_file(cartera_path)
    if not tickers:
        return {"error": "No tickers found in file."}

    results = recommend(tickers=tickers, top_n=len(tickers), min_market_cap=min_market_cap)

    weak = [s for s in results if s.total < 40]
    moderate = [s for s in results if 40 <= s.total < 60]
    strong = [s for s in results if s.total >= 60]

    sectors = set()
    for s in results:
        sec = (
            getattr(s.data, "sector", None)
            or getattr(s.data, "category", None)
            or "Unknown"
        )
        sectors.add(sec)

    return {
        "results": results,
        "weak_positions": weak,
        "moderate_positions": moderate,
        "strong_positions": strong,
        "sectors": sorted(sectors),
        "count": len(results),
        "tickers": tickers,
    }


# ---------------------------------------------------------------------------
# Comprehensive analysis (multiple regions × asset classes)
# ---------------------------------------------------------------------------

_CURRENCY_DEFAULTS: dict[str, str] = {
    "us": "USD",
    "eu": "EUR",
    "asia": "all",
    "superinvestor": "USD",
    "all": "all",
}


def run_comprehensive(
    risk: str = "moderate",
    portfolio_path: str = "portfolios/portfolio.txt",
    regions: Iterable[str] = ("us",),
    asset_classes: Iterable[str] = ("stocks",),
    top_n: int = 10,
    currencies: dict[str, str] | None = None,
    min_market_cap: float = 2_000_000_000,
) -> dict:
    """Run analysis across multiple region×asset_class combinations.

    This is the **programmatic API** for the opencode advisor agent.
    It performs market analysis once, portfolio review once, then
    iterates over every requested combination of region and asset class
    to produce buy recommendations.

    All calls to yfinance are wrapped with ``_suppress_stderr()`` inside
    ``recommend()`` — no stderr noise.

    Args:
        risk: Risk profile label (for display only).
        portfolio_path: Path to portfolio ticker file.
        regions: Iterable of region codes (``"us"``, ``"eu"``, ``"asia"``).
        asset_classes: Iterable of asset classes (``"stocks"``, ``"etfs"``).
        top_n: How many recommendations per combination.
        currencies: Optional override dict mapping region → currency code.
            Falls back to ``_CURRENCY_DEFAULTS``.
        min_market_cap: Minimum market cap / AUM in native currency.
            Tickers below this threshold skip expensive history fetches.
            Pass ``0`` to disable. Supports human-readable values via CLI.

    Returns:
        dict with keys:
            - ``macro``: macro_regime() output
            - ``bubble``: bubble_risk() output
            - ``portfolio``: portfolio_review() output
            - ``recommendations``: dict ``"{region}:{asset_class}"`` → list of ScoredAsset
            - ``errors``: list of error strings
            - ``html_reports``: list of generated HTML file paths
    """
    result: dict = {
        "macro": macro_regime(),
        "bubble": bubble_risk(),
        "portfolio": portfolio_review(portfolio_path, min_market_cap),
        "recommendations": {},
        "errors": [],
        "html_reports": [],
    }

    # Portfolio errors should not prevent generating recommendations
    if "error" in result["portfolio"]:
        result["errors"].append(f"Portfolio review failed: {result['portfolio']['error']}")

    currency_map = (
        dict(currencies)
        if currencies is not None
        else _CURRENCY_DEFAULTS.copy()
    )

    portfolio_path_obj = Path(portfolio_path)
    portfolio_tickers: set[str] = set()
    if portfolio_path_obj.exists():
        portfolio_tickers = {
            t.upper() for t in _load_tickers_from_file(str(portfolio_path_obj))
        }

    for region in regions:
        ccy = currency_map.get(region, "all")
        for ac in asset_classes:
            key = f"{region}:{ac}"
            try:
                recs = recommend(
                    asset_class=ac,
                    region=region,
                    top_n=top_n,
                    currency=ccy,
                    min_market_cap=min_market_cap,
                )
                filtered = [
                    r for r in recs
                    if r.data.ticker.upper() not in portfolio_tickers
                ]
                result["recommendations"][key] = filtered

                # export HTML for this combination
                Path("advisor_recommendations").mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d-%H%M")
                dest = (
                    f"advisor_recommendations/advisor_{region}_{ac}_{timestamp}.html"
                )
                out = export_recommendations_html(
                    filtered,
                    dest,
                    top_n=top_n,
                    asset_class=ac,
                    region=region,
                    currency=ccy,
                    tickers=None,
                )
                result["html_reports"].append(out)
            except YFRateLimitError:
                result["errors"].append(
                    f"Rate limit reached for {region}/{ac}. Skipping."
                )
            except Exception as exc:
                result["errors"].append(
                    f"Error for {region}/{ac}: {exc}"
                )

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _fmt_risk_profile(profile: str) -> str:
    icons = {"conservative": "🛡️", "moderate": "⚖️", "aggressive": "🚀"}
    return f"{icons.get(profile, '')} {profile.capitalize()}"


def advisor_main(argv: list[str] | None = None) -> int:
    """CLI entry for ``investdaytip advisor``."""
    parser = argparse.ArgumentParser(prog="investdaytip advisor")
    parser.add_argument(
        "--risk",
        choices=["conservative", "moderate", "aggressive"],
        help="Risk profile (interactive if omitted)",
    )
    parser.add_argument(
        "--portfolio",
        default="portfolios/portfolio.txt",
        help="Portfolio ticker file (default: portfolios/portfolio.txt)",
    )
    parser.add_argument("-a", "--asset-class", metavar="TYPE",
                        choices=["all", "stocks", "etfs"], default=None,
                        help="Asset class: all, stocks, etfs (interactive if omitted).")
    parser.add_argument("-r", "--region", metavar="REG", nargs="+",
                        choices=["all", "us", "eu", "asia", "superinvestor"], default=None,
                        help="Region(s): all, us, eu, asia, superinvestor (interactive if omitted).")
    parser.add_argument(
        "-c", "--currency", metavar="CUR", nargs="+",
        choices=["all", "USD", "EUR", "GBP", "CHF", "JPY", "HKD", "INR",
                 "KRW", "TWD", "SGD", "AUD", "DKK", "SEK", "NOK", "GBp"],
        default=None,
        help="Currency: all, USD, EUR, GBP, CHF, JPY, HKD, INR, KRW, TWD, SGD, AUD, DKK, SEK, NOK, GBp (interactive if omitted).",
    )
    parser.add_argument("--min-market-cap", metavar="CAP", type=_parse_min_market_cap, default=2_000_000_000,
                        help="Minimum market cap (default: 2B).")
    parser.add_argument("-s", "--sector", type=str, default=None,
                        help="Filter by sector (case-insensitive).")
    parser.add_argument("--superinvestor", action="store_true",
                        help="Include superinvestor ownership data.")
    parser.add_argument("--no-cache", action="store_true",
                        help="Bypass SQLite cache.")
    parser.add_argument("--cache-clear", action="store_true",
                        help="Clear all cached data.")
    args = parser.parse_args(argv)

    from investdaytip.cache import clear_cache
    from investdaytip.cache import set_enabled as cache_set_enabled
    if args.cache_clear:
        clear_cache()
    if args.no_cache:
        cache_set_enabled(False)

    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
    from rich.prompt import Prompt
    from rich.table import Table

    console = Console()

    # ── Warm-up superinvestor cache ────────────────────────
    # Superinvestor data is only relevant for stocks, not ETFs
    if args.superinvestor and not args.no_cache and args.asset_class != "etfs":
        if not get_superinvestor_data():
            with Progress(
                SpinnerColumn(),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as si_progress:
                si_task = si_progress.add_task("Fetching superinvestor data", total=None)
                def _si_cb(done: int, total: int, name: str) -> None:
                    si_progress.update(si_task, total=total, completed=done, description=f"Fetching {name}")
                try:
                    fetch_superinvestor_universe(progress_cb=_si_cb)
                except Exception:
                    logger.warning("Could not fetch superinvestor data, continuing without it.")
                    console.print("[yellow]⚠️  Could not fetch superinvestor data, continuing without it.[/yellow]")

    # ── Risk profile ───────────────────────────────────────
    risk = args.risk
    if not risk:
        console.print(Panel("[bold cyan]📊 InvestDayTip Advisor[/bold cyan]\n"))
        risk = Prompt.ask(
            "What is your risk profile?",
            choices=["conservative", "moderate", "aggressive"],
            default="moderate",
        )

    console.print(f"\nRisk profile: [bold]{_fmt_risk_profile(risk)}[/bold]\n")

    # ── Market analysis ────────────────────────────────────
    with console.status("[bold green]Analyzing market..."):
        macro = macro_regime()
        bubble = bubble_risk()

    vix = macro["vix"]
    vix_str = f"{vix['vix']:.2f}" if vix["vix"] is not None else "N/A"
    vxn_str = f"{vix['vxn']:.2f}" if vix["vxn"] is not None else "N/A"

    yd = macro["yield"]
    if yd.get("y10") is not None and yd.get("y2") is not None:
        yield_str = f"{yd['spread']:.2f}% ({yd['y10']:.2f}% − {yd['y2']:.2f}%)"
        yield_note = "inverted" if yd["spread"] < 0 else "normal"
    else:
        yield_str = "N/A"
        yield_note = ""

    move_str = f"{macro['move']:.2f}" if macro["move"] is not None else "N/A"
    move_note = ""
    if macro["move"] is not None:
        if macro["move"] > 120:
            move_note = " (bond panic)"
        elif macro["move"] > 100:
            move_note = " (elevated)"
        elif macro["move"] < 60:
            move_note = " (calm)"

    dxy_str = f"{macro['dxy']:.2f}" if macro["dxy"] is not None else "N/A"
    dxy_note = ""
    if macro["dxy"] is not None:
        if macro["dxy"] > 105:
            dxy_note = " (strong)"
        elif macro["dxy"] > 100:
            dxy_note = " (neutral)"
        elif macro["dxy"] < 95:
            dxy_note = " (weak)"

    market_table = Table(title="📈 Market Analysis", show_lines=True)
    market_table.add_column("Indicator", style="bold cyan")
    market_table.add_column("Value")
    market_table.add_row("VIX (S&P 500)", vix_str)
    market_table.add_row("VXN (Nasdaq 100)", vxn_str)
    market_table.add_row("10Y−2Y Spread", f"{yield_str} {yield_note}".strip())
    market_table.add_row("MOVE Index", f"{move_str}{move_note}")
    market_table.add_row("DXY", f"{dxy_str}{dxy_note}")
    fg = macro.get("fear_greed")
    if fg and fg.get("score") is not None:
        fg_score = fg["score"]
        fg_rating = fg.get("rating", "").capitalize()
        icon = "🟢" if fg_score < 25 else "🟡" if fg_score < 45 else "⚪" if fg_score < 56 else "🟠" if fg_score < 76 else "🔴"
        fg_str = f"{icon} {fg_score:.1f} — {fg_rating}"
    else:
        fg_str = "N/A"
    market_table.add_row("Fear & Greed", fg_str)
    market_table.add_row("Macro Regime", macro["label"])
    market_table.add_row("Score", f"{macro['score']}/100")
    market_table.add_row("Signal", f"[bold]{macro['action'].upper()}[/bold]")
    market_table.add_row(
        "Bubble risk",
        f"{bubble['level'].upper()} — {bubble['note']}",
    )
    console.print(market_table)

    # ── Portfolio review ───────────────────────────────────
    portfolio_path = Path(args.portfolio)
    missing: set[str] = set()
    if not portfolio_path.exists():
        logger.warning("Portfolio file not found: %s", args.portfolio)
        console.print(f"\n[yellow]Portfolio file not found: {args.portfolio}[/yellow]")
        console.print(
            "Add a ticker file (one per line, # for comments) to the "
            "portfolios/ folder, or use --portfolio to point to an "
            "existing portfolio file."
        )
    else:
        with console.status("[bold green]Analyzing portfolio..."):
            try:
                review = portfolio_review(str(portfolio_path), args.min_market_cap)
            except YFRateLimitError:
                logger.warning("Rate limit reached while analyzing portfolio.")
                console.print(
                    "\n[yellow]⏳ Rate limit reached while analyzing portfolio. "
                    "Wait 1-2 minutes and try again.[/yellow]"
                )
                return 1

        if "error" in review:
            logger.error("Portfolio review error: %s", review['error'])
            console.print(f"\n[red]{review['error']}[/red]")
        else:
            port_table = Table(title="📋 Current Portfolio", show_lines=True)
            port_table.add_column("#", style="bold")
            port_table.add_column("Ticker")
            port_table.add_column("Score", justify="right")
            port_table.add_column("Sector")
            port_table.add_column("Signal")

            for i, s in enumerate(review["results"], start=1):
                sector = (
                    getattr(s.data, "sector", None)
                    or getattr(s.data, "category", None)
                    or "-"
                )
                if s.total < 40:
                    signal = "[red]🔴 SELL[/red]"
                elif s.total < 60:
                    signal = "[yellow]🟡 HOLD[/yellow]"
                else:
                    signal = "[green]🟢 OK[/green]"
                port_table.add_row(str(i), s.data.ticker, f"{s.total:.1f}", sector, signal)

            console.print(port_table)

            # Sell recommendations
            if review["weak_positions"]:
                console.print("\n[bold red]⚠️  Weak positions (consider selling):[/bold red]")
                for s in review["weak_positions"]:
                    console.print(f"  • [red]{s.data.ticker}[/red] — Score {s.total:.1f}"
                                  f" — {'; '.join(s.rationale[:2])}")

            # Sector gaps
            _all_sectors = {"Technology", "Financials", "Healthcare",
                            "Energy", "Consumer", "Utilities", "Real Estate"}
            missing = _all_sectors - set(review["sectors"])
            if missing:
                console.print("\n[bold yellow]📌 Missing sectors:[/bold yellow]")
                for sec in sorted(missing):
                    console.print(f"  • [yellow]{sec}[/yellow]")

    # ── Buy recommendations (always interactive) ──────────
    macro_action = macro["action"]
    if macro_action == "buy":
        console.print(f"\n[bold green]✅ Macro signal: {macro_action.upper()}[/bold green]")
    else:
        console.print(f"\n[bold yellow]📊 Macro signal: {macro_action.upper()} — {macro['description']}[/bold yellow]")

    # Resolve asset class, region, currency (CLI flags or interactive)
    _risk_defaults = {
        "conservative": ("etfs", "eu"),
        "moderate": ("all", "all"),
        "aggressive": ("stocks", "all"),
    }
    try:
        if args.asset_class:
            ac = args.asset_class
        else:
            d_ac, _ = _risk_defaults.get(risk, ("all", "all"))
            ac = Prompt.ask(
                "What asset types do you want to analyze?",
                choices=["all", "stocks", "etfs"],
                default=d_ac,
            )

        if args.region:
            reg = args.region
        else:
            _, d_reg = _risk_defaults.get(risk, ("all", "all"))
            reg = [
                Prompt.ask(
                    "Which regions?",
                    choices=["all", "us", "eu", "asia", "superinvestor"],
                    default=d_reg,
                )
            ]

        if args.currency:
            ccy = args.currency
        else:
            _reg_for_ccy = reg[0] if isinstance(reg, list) else reg
            _currency_choices = {
                "us": ["USD", "all"],
                "eu": ["EUR", "USD", "GBP", "all"],
                "asia": ["USD", "JPY", "HKD", "all"],
                "superinvestor": ["USD", "all"],
                "all": ["all", "USD", "EUR", "GBP", "JPY", "HKD"],
            }
            _currency_defaults = {"us": "USD", "eu": "EUR", "asia": "all", "superinvestor": "USD", "all": "all"}
            ccy = [
                Prompt.ask(
                    "Currency filter?",
                    choices=_currency_choices.get(_reg_for_ccy, ["all"]),
                    default=_currency_defaults.get(_reg_for_ccy, "all"),
                )
            ]

        # Ask about missing sector focus (only in interactive mode)
        target_sector: str | None = None
        if missing and not args.asset_class:
            sector_choices = sorted(missing) + ["All", "No"]
            target_sector = Prompt.ask(
                "Focus on a specific missing sector?",
                choices=sector_choices,
                default="No",
            )
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. Exiting.[/yellow]")
        return 0

    with console.status("[bold green]Generating buy recommendations..."):

        portfolio_tickers: set[str] = set()
        if portfolio_path.exists():
            portfolio_tickers = {
                t.upper() for t in _load_tickers_from_file(str(portfolio_path))
            }

        try:
            results = recommend(asset_class=ac, region=reg, top_n=10, currency=ccy, min_market_cap=args.min_market_cap, sector=args.sector)
        except YFRateLimitError:
            logger.warning("yfinance rate limit reached.")
            console.print(
                "\n[yellow]⏳ yfinance rate limit reached. "
                "Wait 1-2 minutes and run:[/yellow]"
            )
            _reg_str = " ".join(reg) if isinstance(reg, list) else str(reg)
            _ccy_str = " ".join(ccy) if isinstance(ccy, list) else str(ccy)
            console.print(
                f"  [bold]investdaytip -a {ac} -r {_reg_str} -c {_ccy_str} "
                "--top 10 --export-html[/bold]"
            )
            return 1

        new_results = [
            r for r in results
            if r.data.ticker.upper() not in portfolio_tickers
        ]

        # Filter by target sector if requested
        if target_sector and target_sector != "No":
            def _sector_of(r) -> Optional[str]:
                return (
                    getattr(r.data, "sector", None)
                    or getattr(r.data, "category", None)
                )

            if target_sector == "All":
                sector_results = [
                    r for r in new_results
                    if _sector_of(r) in missing
                ]
            else:
                sector_results = [
                    r for r in new_results
                    if _sector_of(r) == target_sector
                ]
            if sector_results:
                new_results = sector_results
            else:
                label = "missing sectors" if target_sector == "All" else target_sector
                logger.info("No %s picks found — showing all.", label)
                console.print(f"[yellow]No {label} picks found — showing all.[/yellow]")

    if new_results:
        _render(new_results, console, include_superinvestor=args.superinvestor)
        Path("advisor_recommendations").mkdir(parents=True, exist_ok=True)
        dest = f"advisor_recommendations/recommendations_advisor_{datetime.now():%Y%m%d-%H%M}.html"
        try:
            out = export_recommendations_html(
                new_results, dest, top_n=10,
                asset_class=ac, region=reg,
                currency=ccy, tickers=None,
                include_superinvestor=args.superinvestor,
            )
            logger.info("Advisor HTML report exported: %s", out)
            console.print(f"\n[green]📄 HTML report:[/green] {out}")
        except Exception as exc:
            logger.error("Error exporting advisor HTML: %s", exc)
            console.print(f"\n[red]Error exporting HTML:[/red] {exc}")
    else:
        logger.info("No new recommendations found outside current portfolio.")
        console.print("[yellow]No new recommendations found outside current portfolio.[/yellow]")

    if macro_action != "buy":
        console.print(f"\n[yellow]💡 {macro['description']}[/yellow]")

    console.print(
        "\n[dim italic]Disclaimer: This is not financial advice. "
        "Do your own research.[/dim italic]"
    )

    return 0
