#!/usr/bin/env python3
"""Fase 0/F1 — probe de un motor de asignación inspirado en systematic global macro.

EXPERIMENTO, no producto. No entra en el paquete ni se expone en la CLI hasta
que pase el gate de la fase 3 (ver informe): batir al 60/40 en Sharpe y drawdown
FUERA de muestra.

Qué copia del enfoque Quantedge y qué no:

  copiado     señal de tendencia time-series + sizing inverse-vol + vol target de
              cartera con reescalado, y caja cuando no hay señal. Es el esqueleto
              de un CTA multi-activo.
  NO copiado  apalancamiento (aquí gross <= 1), >250 mercados, futuros con roll,
              carry/ILS, optimización intradía. Sin apalancamiento ni coste de
              financiación, el retorno esperado es necesariamente menor.

Sesgos declarados (no se corrigen aquí):
  - survivorship: el universo es el de ETFs que existen HOY aplicado hacia atrás.
  - precios adjusted de yfinance para ETFs (distribuciones reinvertidas).
  - caja remunerada al 0%: conservador y deliberado.
  - sin coste de financiación ni slippage implícito de mercado.

Uso:
    .venv/bin/python scripts/alloc_probe.py
    .venv/bin/python scripts/alloc_probe.py --out alloc-probe.json
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

# Sleeves: se usan para el informe, no para el sizing (v1 no impone cap por sleeve).
UNIVERSE: Dict[str, str] = {
    "SPY": "equity_us",
    "QQQ": "equity_us",
    "IWM": "equity_us",
    "EFA": "equity_intl",
    "EEM": "equity_intl",
    "EWG": "equity_intl",
    "EWJ": "equity_intl",
    "TLT": "bonds",
    "IEF": "bonds",
    "SHY": "bonds",
    "LQD": "bonds",
    "HYG": "bonds",
    "TIP": "bonds",
    "DBC": "commodities",
    "GSG": "commodities",
    "GLD": "commodities",
    "SLV": "commodities",
    "VNQ": "real_estate",
    "IYR": "real_estate",
    "UUP": "fx",
    "FXE": "fx",
    "FXY": "fx",
}

START = "2004-01-01"
COST_BPS = 10.0          # por unidad de turnover (spread + comisión de un ETF líquido)
ASSET_CAP = 0.25         # cap por activo
VOL_WINDOW = 63          # días para vol/cov realized
TRADING_DAYS = 252


@dataclass
class Params:
    lookback_m: int = 12
    skip_m: int = 1
    vol_target: float = 0.10
    rebalance: str = "M"   # mensual
    use_sma_filter: bool = False
    cash_allowed: bool = True  # False = siempre invertido entre elegibles (sin caja ociosa)
    weighting: str = "inverse_vol"  # "inverse_vol" | "sleeve_risk" (F2)
    meta: dict = field(default_factory=dict)


def download(tickers: List[str], start: str = START) -> pd.DataFrame:
    data = yf.download(tickers, start=start, progress=False, auto_adjust=True)
    close = data["Close"] if isinstance(data.columns, pd.MultiIndex) else data[["Close"]]
    return close.dropna(how="all")


def load_prices(path: str, refresh: bool, tickers: Optional[List[str]] = None,
                start: str = START) -> pd.DataFrame:
    """Precios fijos en disco: yfinance reajusta la serie entre llamadas y el
    backtest debe ser reproducible (misma leccion que --no-cache en backtest.py)."""
    if path and not refresh and Path(path).exists():
        px = pd.read_csv(path, index_col=0, parse_dates=True)
        print(f"precios desde cache local {path}")
        return px
    px = download(tickers or list(UNIVERSE), start)
    if path:
        px.to_csv(path)
        print(f"precios guardados en {path}")
    return px


def monthly_rebalance_mask(index: pd.DatetimeIndex) -> np.ndarray:
    """True en el último día de negociación de cada mes."""
    s = pd.Series(index=index, data=index)
    return (s.dt.to_period("M") != s.dt.to_period("M").shift(-1)).to_numpy()


def target_weights(
    px: pd.DataFrame,
    rets: pd.DataFrame,
    date: pd.Timestamp,
    p: Params,
) -> pd.Series:
    """Pesos objetivo en `date` con la información disponible hasta `date`."""
    cols = px.columns
    hist = px.loc[:date]
    lb, skip = p.lookback_m * 21, p.skip_m * 21
    if len(hist) < lb + skip + 2:
        return pd.Series(0.0, index=cols)

    past = hist.iloc[-1 - skip]
    old = hist.iloc[-1 - skip - lb]
    trend = (past / old) - 1.0

    eligible = trend > 0
    if p.use_sma_filter:
        sma = hist.iloc[:, :].rolling(200).mean().iloc[-1]
        eligible &= hist.iloc[-1] > sma

    vol = rets.loc[:date].tail(VOL_WINDOW).std() * math.sqrt(TRADING_DAYS)
    vol = vol.where(eligible & (vol > 0)).dropna()
    if vol.empty:
        return pd.Series(0.0, index=cols)

    cov = rets.loc[:date].tail(VOL_WINDOW)[vol.index].cov() * TRADING_DAYS

    if p.weighting == "sleeve_risk":
        # F2: mismo riesgo por sleeve, inverse-vol dentro de cada sleeve.
        sleeves = pd.Series({t: UNIVERSE.get(t, "other") for t in vol.index})
        within = 1.0 / vol
        within = within / within.groupby(sleeves).transform("sum")
        sleeve_vol = {}
        for name in sleeves.unique():
            members = list(sleeves[sleeves == name].index)
            ws = within[members].to_numpy()
            sleeve_vol[name] = float(
                np.sqrt(ws @ cov.loc[members, members].to_numpy() @ ws)
            )
        sv = pd.Series(sleeve_vol)
        raw = (1.0 / sv)
        raw = raw / raw.sum()
        w = pd.Series(0.0, index=vol.index)
        for name in sleeves.unique():
            members = list(sleeves[sleeves == name].index)
            w[members] = raw[name] * within[members]
    else:
        w = (1.0 / vol)
        w = w / w.sum()
    ex_ante = float(np.sqrt(w.to_numpy() @ cov.to_numpy() @ w.to_numpy()))
    if not math.isfinite(ex_ante) or ex_ante <= 0:
        return pd.Series(0.0, index=cols)

    scale = min(p.vol_target / ex_ante, 1.0)  # sin apalancamiento
    if not p.cash_allowed:
        scale = 1.0  # variante estructural: todo el capital entre los elegibles
    w = w * scale

    # cap por activo con renormalización (solo si todavía queda presupuesto <= 1)
    for _ in range(5):
        over = w > ASSET_CAP
        if not over.any():
            break
        excess = float((w[over] - ASSET_CAP).sum())
        w[over] = ASSET_CAP
        room = (w < ASSET_CAP) & (w > 0)
        if room.any() and excess > 0:
            w[room] += excess * (w[room] / w[room].sum())
    if w.sum() > 1.0:
        w = w / w.sum()

    out = pd.Series(0.0, index=cols)
    out.loc[w.index] = w
    return out


def run(px: pd.DataFrame, p: Params) -> pd.Series:
    """Curva de equity neta de costes (base 1.0)."""
    rets = px.pct_change().fillna(0.0)
    index = px.index
    reb = monthly_rebalance_mask(index)
    cols = px.columns

    w = pd.Series(0.0, index=cols)
    equity = [1.0]
    turnovers = []
    exposures = []
    for i in range(1, len(index)):
        r = rets.iloc[i]
        # la caja vale 1 y no devenga interes: el valor del portfolio crece solo
        # con los activos de riesgo, luego se renormaliza.
        port_ret = float((w * r).sum())
        denom = 1.0 + port_ret
        if denom > 0:
            w = w * (1.0 + r) / denom
        if reb[i - 1]:
            tw = target_weights(px, rets, index[i - 1], p)
            turn = float((tw - w).abs().sum())
            port_ret -= turn * COST_BPS / 10_000.0
            turnovers.append(turn)
            w = tw
        exposures.append(float(w.sum()))
        equity.append(equity[-1] * (1.0 + port_ret))
    out = pd.Series(equity, index=index, name="strategy")
    out.attrs["avg_turnover"] = float(np.mean(turnovers)) if turnovers else 0.0
    out.attrs["rebalances"] = len(turnovers)
    out.attrs["avg_exposure"] = float(np.mean(exposures)) if exposures else 0.0
    return out


def metrics(eq: pd.Series, name: str = "x") -> dict:
    eq = eq.dropna()
    r = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 else float("nan")
    vol = float(r.std() * math.sqrt(TRADING_DAYS))
    sharpe = float(r.mean() / r.std() * math.sqrt(TRADING_DAYS)) if r.std() > 0 else float("nan")
    dd = (eq / eq.cummax() - 1.0).min()
    return {
        "name": name,
        "start": str(eq.index[0].date()),
        "end": str(eq.index[-1].date()),
        "cagr": round(cagr, 4),
        "vol": round(vol, 4),
        "sharpe": round(sharpe, 3),
        "max_dd": round(float(dd), 4),
        "calmar": round(float(cagr / abs(dd)) if dd < 0 else float("nan"), 2),
        "total": round(float(eq.iloc[-1] / eq.iloc[0] - 1), 4),
    }


def lever(eq: pd.Series, k: float) -> pd.Series:
    """Diagnostico: misma estrategia con apalancamiento k (diario). No ejecutable retail."""
    r = eq.pct_change().fillna(0.0) * k
    return (1.0 + r).cumprod()


def run_overlay(px: pd.DataFrame, rotate_to_bonds: bool = True,
                filter_bond: bool = False,
                eq: str = "SPY", bd: str = "IEF") -> pd.Series:
    """F2: 60/40 puro con overlay de tendencia (12-1) sobre la pata de renta variable.

    Si SPY pierde tendencia el 60% pasa a IEF (rotacion) o a caja (sin rotacion).
    """
    r = px[[eq, bd]].pct_change().fillna(0.0)
    idx = px.index
    reb = monthly_rebalance_mask(idx)
    w = pd.Series({eq: 0.6, bd: 0.4})
    equity = [1.0]
    toggles = 0
    months_off = 0
    for i in range(1, len(idx)):
        rr = r.iloc[i]
        port = float((w * rr).sum())
        den = 1.0 + port
        if den > 0:
            w = w * (1.0 + rr) / den
        if reb[i - 1]:
            tw = {eq: 0.6, bd: 0.4}
            for leg, weight in ((eq, 0.6), (bd, 0.4)):
                if leg == bd and not filter_bond:
                    continue
                hist = px[leg].loc[:idx[i - 1]]
                if len(hist) <= 274:
                    continue
                mom = hist.iloc[-22] / hist.iloc[-274] - 1.0
                if mom > 0:
                    continue
                tw[leg] = 0.0
                months_off += 1
                if leg == eq and rotate_to_bonds and not filter_bond:
                    tw[bd] = 0.4 + weight
            tw = pd.Series(tw)
            port -= float((tw - w).abs().sum()) * COST_BPS / 10_000.0
            toggles += 1 if (tw[eq] == 0) != (w[eq] == 0) else 0
            w = tw
        equity.append(equity[-1] * (1.0 + port))
    out = pd.Series(equity, index=idx)
    out.attrs["toggles"] = toggles
    out.attrs["months_off"] = months_off
    return out


def benchmark_60_40(px: pd.DataFrame, eq: str = "SPY", bd: str = "IEF") -> pd.Series:
    r = px[[eq, bd]].pct_change().fillna(0.0)
    port = 0.6 * r[eq] + 0.4 * r[bd]
    return (1 + port).cumprod()


def long_validation(refresh: bool = False) -> dict:
    """Tramo 1993-2026 con proxies (VFINX/VFITX) para no validar solo en 2004+."""
    px = load_prices("alloc-probe-long-prices.csv", refresh,
                     tickers=["VFINX", "VFITX"], start="1993-01-01")
    eq, bd = "VFINX", "VFITX"
    base = benchmark_60_40(px, eq=eq, bd=bd)
    ov_eq = run_overlay(px, rotate_to_bonds=False, eq=eq, bd=bd)
    ov_both = run_overlay(px, rotate_to_bonds=False, filter_bond=True, eq=eq, bd=bd)

    print("\n== validacion larga 1993-2026 (VFINX/VFITX) ==")
    print(f"{'cartera':40} {'CAGR':>7} {'vol':>7} {'Sharpe':>7} {'maxDD':>8} {'Calmar':>7}")
    for name, ser in (("60/40", base), ("+ overlay RV", ov_eq), ("+ overlay ambas patas", ov_both)):
        m = metrics(ser, name)
        print(f"{name:40} {m['cagr']:7.2%} {m['vol']:7.2%} {m['sharpe']:7.2f} "
              f"{m['max_dd']:8.2%} {m['calmar']:7.2f}")

    out = {}
    splits = [("1993-2003", None, "2004-01-01"),
              ("2004-2015", "2004-01-01", "2016-01-01"),
              ("2016-2026", "2016-01-01", None)]
    for label, a, b in splits:
        seg = {}
        for name, ser in (("60/40", base), ("overlay RV", ov_eq), ("overlay 2 patas", ov_both)):
            x = ser.loc[a:b] if a or b else ser
            seg[name] = metrics(x, name)
        out[label] = seg
        print(f"  {label}: " + " | ".join(
            f"{k} Sharpe {v['sharpe']:.2f} DD {v['max_dd']:.2%}" for k, v in seg.items()))
    return {"full": {n: metrics(s, n) for n, s in
                     (("60/40", base), ("overlay RV", ov_eq), ("overlay 2 patas", ov_both))},
            "by_period": out,
            "toggles": ov_both.attrs["toggles"],
            "months_off": ov_both.attrs["months_off"]}


def benchmark_equal_weight(px: pd.DataFrame) -> pd.Series:
    r = px.pct_change().fillna(0.0)
    return (1 + r.mean(axis=1)).cumprod()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="alloc-probe.json")
    ap.add_argument("--prices", default="alloc-probe-prices.csv")
    ap.add_argument("--refresh", action="store_true", help="re-descarga precios")
    ap.add_argument("--long", action="store_true", help="validacion 1993-2026 con proxies")
    args = ap.parse_args()

    px = load_prices(args.prices, args.refresh)
    px = px.loc[:, [t for t in px.columns if px[t].notna().sum() > 500]]
    print(f"datos: {px.shape[0]} sesiones, {px.shape[1]} ETFs, "
          f"{px.index[0].date()} -> {px.index[-1].date()}")

    p = Params()
    strat = run(px, p)
    strat_full = run(px, Params(cash_allowed=False))
    strat_sleeve = run(px, Params(weighting="sleeve_risk"))
    ov_bonds = run_overlay(px, rotate_to_bonds=True)
    ov_cash = run_overlay(px, rotate_to_bonds=False)
    b60 = benchmark_60_40(px)
    bew = benchmark_equal_weight(px)

    k = p.vol_target / metrics(strat, "x")["vol"] if metrics(strat, "x")["vol"] > 0 else 1.0
    rows = [metrics(strat, f"trend+voltarget ({p.lookback_m}m, vol {p.vol_target:.0%})"),
            metrics(strat_full, "trend full-invested (sin caja)"),
            metrics(lever(strat, k), f"  ^ mismo, apalancado x{k:.2f} a vol {p.vol_target:.0%}"),
            metrics(strat_sleeve, "trend + risk budget por sleeve"),
            metrics(ov_bonds, "60/40 + overlay trend (rota a bonos)"),
            metrics(ov_cash, "60/40 + overlay trend (rota a caja)"),
            metrics(b60, "60/40 (SPY/IEF)"),
            metrics(bew, "equal-weight B&H")]

    # estabilidad de parámetros: mismo motor, distintos lookback / vol target
    grid = []
    for lb in (6, 9, 12):
        for vt in (0.08, 0.10, 0.12):
            eq = run(px, Params(lookback_m=lb, vol_target=vt))
            m = metrics(eq, f"lb{lb} vol{vt:.0%}")
            m["lookback_m"] = lb
            m["vol_target"] = vt
            m["avg_turnover"] = round(eq.attrs["avg_turnover"], 3)
            grid.append(m)

    # fuera de muestra: diseño 2004-2015 / test 2016-2026
    split = pd.Timestamp("2016-01-01")
    oos = {
        "design_2004_2015": [
            metrics(strat.loc[:split], "trend+vt"),
            metrics(strat_sleeve.loc[:split], "sleeve"),
            metrics(b60.loc[:split], "60/40"),
            metrics(ov_bonds.loc[:split], "60/40+overlay"),
        ],
        "test_2016_2026": [
            metrics(strat.loc[split:], "trend+vt"),
            metrics(strat_sleeve.loc[split:], "sleeve"),
            metrics(b60.loc[split:], "60/40"),
            metrics(ov_bonds.loc[split:], "60/40+overlay"),
        ],
    }
    print(f"\noverlay: {ov_bonds.attrs['toggles']} cambios de estado, "
          f"{ov_bonds.attrs['months_off']} meses fuera de renta variable "
          f"de {len(px.index) // 21} meses")

    print(f"exposicion media: voltarget {strat.attrs['avg_exposure']:.1%} "
          f"| full-invested {strat_full.attrs['avg_exposure']:.1%}")
    turn = strat.attrs["avg_turnover"]
    print(f"\nturnover medio por rebalanceo mensual: {turn:.1%} "
          f"({strat.attrs['rebalances']} rebalanceos)")
    print(f"coste anual estimado: {turn * 12 * COST_BPS / 100:.2f}%")
    print("exposicion media a renta variable/activos: ver pesos (sin apalancamiento)")

    print("\n== metricas ==")
    hdr = f"{'cartera':42} {'CAGR':>7} {'vol':>7} {'Sharpe':>7} {'maxDD':>7} {'Calmar':>7}"
    print(hdr)
    for m in rows:
        print(f"{m['name']:42} {m['cagr']:7.2%} {m['vol']:7.2%} {m['sharpe']:7.2f} "
              f"{m['max_dd']:7.2%} {m['calmar']:7.2f}")

    print("\n== estabilidad de parametros ==")
    for m in grid:
        print(f"lookback {m['lookback_m']:2d}m  vol {m['vol_target']:.0%}   "
              f"CAGR {m['cagr']:6.2%}  Sharpe {m['sharpe']:5.2f}  maxDD {m['max_dd']:7.2%}  "
              f"turn {m['avg_turnover']:.2f}")

    print("\n== fuera de muestra ==")
    for k, v in oos.items():
        print(f"{k}: " + " | ".join(
            f"{m['name']} CAGR {m['cagr']:.2%} Sharpe {m['sharpe']:.2f} maxDD {m['max_dd']:.2%}"
            for m in v))

    long_res = long_validation(args.refresh) if args.long else None

    payload = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "params": p.__dict__,
        "cost_bps": COST_BPS,
        "asset_cap": ASSET_CAP,
        "universe": UNIVERSE,
        "results": rows,
        "avg_exposure": {"voltarget": strat.attrs["avg_exposure"],
                         "full_invested": strat_full.attrs["avg_exposure"],
                         "sleeve_risk": strat_sleeve.attrs["avg_exposure"]},
        "long_validation": long_res,
        "overlay": {"toggles": ov_bonds.attrs["toggles"],
                    "months_off": ov_bonds.attrs["months_off"]},
        "grid": grid,
        "oos": oos,
        "bias_notes": [
            "survivorship: universo actual aplicado hacia atras",
            "cash al 0%",
            "sin financiacion ni slippage adicional",
        ],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"\nescrito {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
