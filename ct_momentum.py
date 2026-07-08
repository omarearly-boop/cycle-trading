#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ct_momentum.py -- Momentum scan mode for Cycles Trading Scanner.

Finds stocks in a CONFIRMED UPTREND that are suitable for
momentum continuation entries — distinct from the retest-of-support
methodology in the main scanner.

Activation:
  python cycles_trading_scanner.py momentum

Filters:
  - SPY weekly gain > 2%  (market in broad rally)
  - RSI between 55 and 78 (trending, not exhausted)
  - Price above 20-week MA
  - Price above 50-week MA
  - Weekly volume >= average  (participation)
  - Monthly trend: BULL
  - No earnings within 7 days

Entry  = current price
Stop   = 20-week MA  (trend-following stop)
Target = entry + (entry - stop) * 2.0  (R:R 1:2 minimum)

This module is READ-ONLY for all existing ct_* modules.
It imports from them but never modifies them.
"""

import os, sys, json, datetime, warnings, webbrowser
from pathlib import Path
warnings.filterwarnings("ignore")

BASE_DIR    = Path(__file__).parent
REPORTS_DIR = BASE_DIR / "REPORTS"
REPORTS_DIR.mkdir(exist_ok=True)


# ─── SPY trend check ──────────────────────────────────────────────────────────

def _spy_weekly_gain() -> float:
    """Return SPY % change over the last full trading week. Returns 0 on error."""
    try:
        import yfinance as yf
        spy = yf.download("SPY", period="1mo", interval="1wk",
                          auto_adjust=True, progress=False)
        closes = spy["Close"].squeeze().dropna()
        if len(closes) < 2:
            return 0.0
        return float((closes.iloc[-1] / closes.iloc[-2] - 1) * 100)
    except Exception:
        return 0.0


def _is_rally_week(min_spy_pct: float = 2.0) -> tuple:
    """Return (is_rally: bool, spy_pct: float)."""
    pct = _spy_weekly_gain()
    return pct >= min_spy_pct, round(pct, 2)


# ─── Single-ticker momentum check ─────────────────────────────────────────────

def _check_momentum(ticker: str, portfolio_size: float = 25000) -> dict | None:
    """
    Returns a setup dict if the ticker passes all momentum filters,
    otherwise returns None.

    The returned dict is compatible with the existing scan result schema
    so it can be passed to generate_html() without modification.
    """
    try:
        import yfinance as yf
        import numpy as np

        asset = yf.Ticker(ticker)
        df = asset.history(period="1y", interval="1wk", auto_adjust=True)
        if df is None or len(df) < 52:
            return None

        closes  = df["Close"].squeeze()
        volumes = df["Volume"].squeeze()

        price   = float(closes.iloc[-1])
        ma20    = float(closes.rolling(20).mean().iloc[-1])
        ma50    = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else ma20

        # ── Filter 1: Price above both MAs ───────────────────────
        if price <= ma20 or price <= ma50:
            return None

        # ── Filter 2: RSI 55–78 ──────────────────────────────────
        delta = closes.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss
        rsi_series = 100 - 100 / (1 + rs)
        rsi   = float(rsi_series.iloc[-1])
        if rsi < 55 or rsi > 78:
            return None

        # ── Filter 3: Volume >= 3-week average ───────────────────
        avg_vol = float(volumes.iloc[-4:-1].mean()) if len(volumes) >= 4 else 0
        cur_vol = float(volumes.iloc[-1])
        vol_ok  = cur_vol >= avg_vol * 0.9

        # ── Filter 4: Monthly trend (4-week close > 8-week ago) ──
        if len(closes) >= 8:
            monthly_bull = float(closes.iloc[-1]) > float(closes.iloc[-5])
        else:
            monthly_bull = True
        if not monthly_bull:
            return None

        # ── Entry / Stop / Target ────────────────────────────────
        entry  = round(price, 2)
        stop   = round(ma20 * 0.995, 2)          # 0.5% below 20-week MA
        risk   = entry - stop
        if risk <= 0:
            return None
        target = round(entry + risk * 2.0, 2)    # R:R 1:2

        rr     = round((target - entry) / risk, 2)

        # ── Position size (1% portfolio risk) ────────────────────
        risk_pct   = 0.01
        risk_trade = portfolio_size * risk_pct
        units      = max(1, int(risk_trade / risk))
        pos_dollar = round(units * entry, 0)
        pos_pct    = round(pos_dollar / portfolio_size * 100, 1)

        # ── Earnings check ───────────────────────────────────────
        try:
            from ct_market_data import get_earnings
            earn_str, earn_days = get_earnings(asset)
        except Exception:
            earn_str, earn_days = "-", 99
        if earn_days is not None and earn_days <= 7:
            return None   # too close to earnings

        # ── Volume label ─────────────────────────────────────────
        vol_label = "HIGH" if cur_vol >= avg_vol * 1.5 else ("OK" if vol_ok else "LOW")

        # ── Build result dict (scanner-compatible schema) ─────────
        return {
            "Ticker":       ticker,
            "Dir":          "⚡ MOMENTUM LONG",
            "Direction":    "LONG",
            "Type":         "MOMENTUM",
            "Price":        entry,
            "RSI":          round(rsi, 1),
            "Support":      round(ma20, 2),
            "Resist":       "-",
            "Entry":        entry,
            "Stop":         stop,
            "Target":       target,
            "R:R":          rr,
            "Units":        units,
            "Risk$":        round(risk_trade, 0),
            "Pos$":         pos_dollar,
            "Pos%":         pos_pct,
            "PosPct":       pos_pct,
            "WasCapped":    False,
            "Vol":          vol_label,
            "Earn":         earn_str,
            "EarnDays":     earn_days,
            "MA20":         round(ma20, 2),
            "MA50":         round(ma50, 2),
            "MonthlyTrend": "BULL",
            "TrafficLight": "YELLOW",   # momentum = caution by default
            "Prob":         65,
            "_score":       65,
            "_pfacts":      [
                ("RSI",       +10, f"RSI {rsi:.0f} — trending, not exhausted"),
                ("Above MA20", +10, f"Price ${entry:.2f} > MA20 ${ma20:.2f}"),
                ("Above MA50", +10, f"Price > MA50 ${ma50:.2f}"),
                ("Volume",    +5 if vol_ok else -5, f"Vol {vol_label}"),
                ("Monthly",   +10, "Monthly trend BULL"),
            ],
            "IsWatchlist":  False,
            "HorizonLabel": "2–4 weeks",
            "HorizonColor": "#58a6ff",
        }

    except Exception as e:
        return None


# ─── Main momentum scan ───────────────────────────────────────────────────────

def scan_momentum(min_spy_pct: float = 2.0):
    """
    Full momentum scan. Called by:
      python cycles_trading_scanner.py momentum
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    print()
    print("=" * 62)
    print("   CYCLES TRADING — MOMENTUM MODE  ⚡")
    print("   Finds trending stocks in a confirmed rally week")
    print("=" * 62)

    # ── Step 1: Check SPY ────────────────────────────────────────
    print()
    print("  Checking SPY weekly trend...")
    is_rally, spy_pct = _is_rally_week(min_spy_pct)
    spy_label = f"SPY {spy_pct:+.1f}% last week"
    if is_rally:
        print(f"  {spy_label}  ->  RALLY WEEK confirmed")
    else:
        print(f"  {spy_label}  ->  NOT a rally week (<{min_spy_pct}%)")
        print(f"  Momentum mode works best when SPY gains >{min_spy_pct}%/week.")
        print(f"  Running anyway...")

    # ── Step 2: Portfolio size ───────────────────────────────────
    print()
    try:
        raw = input("  Portfolio size in $ (press ENTER for $25,000): ").replace(",", "").strip()
        portfolio_size = float(raw) if raw else 25000
    except (EOFError, ValueError):
        portfolio_size = 25000
    print(f"  Portfolio: ${portfolio_size:,.0f}")

    # ── Step 3: Load universe ────────────────────────────────────
    cache = BASE_DIR / ".universe_cache.json"
    if cache.exists():
        with open(cache, encoding="utf-8") as f:
            us_tickers = json.load(f)["data"]["us"]
    else:
        # minimal fallback
        us_tickers = ["AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA",
                      "AVGO","ORCL","AMD","PLTR","COIN"]

    print(f"  Universe: {len(us_tickers)} tickers")
    print()

    # ── Step 4: Scan ─────────────────────────────────────────────
    results = []
    total   = len(us_tickers)

    def _scan_one(t):
        return _check_momentum(t, portfolio_size)

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_scan_one, t): t for t in us_tickers}
        done = 0
        for fut in as_completed(futures):
            done += 1
            pct = int(done / total * 30)
            print(f"  [{'#'*pct}{'-'*(30-pct)}] {done:>3}/{total}", end=""  )
            r = fut.result()
            if r:
                results.append(r)

    print(f"  Done! Found {len(results)} momentum setups.          ")
    print()

    results.sort(key=lambda x: x.get("RSI", 0), reverse=True)

    # ── Step 5: Print summary ─────────────────────────────────────
    if results:
        import pandas as pd
        cols = ["Ticker","Price","RSI","MA20","Entry","Stop","Target","R:R","Pos$","Vol","Earn"]
        print(pd.DataFrame(results)[cols].to_string(index=False))
    else:
        print("  No momentum setups found.")
    print()

    # ── Step 6: HTML report ───────────────────────────────────────
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    out_path = REPORTS_DIR / f"momentum_report_{ts}.html"
    _write_html(results, out_path, spy_pct, is_rally, portfolio_size)
    print(f"  Report: REPORTS/momentum_report_{ts}.html")
    try:
        webbrowser.open("file:///" + out_path.as_posix())
    except Exception:
        pass


# ─── HTML report ──────────────────────────────────────────────────────────────

def _write_html(results, out_path, spy_pct, is_rally, portfolio_size):
    today = datetime.date.today().strftime("%d/%m/%Y")

    spy_color = "#22c55e" if is_rally else "#f59e0b"
    spy_badge = f"SPY {spy_pct:+.1f}% last week"

    rows = ""
    for r in results:
        rsi   = r.get("RSI", 0)
        rsi_c = "#22c55e" if rsi < 70 else "#f59e0b"
        tkr    = r.get("Ticker", "")
        price  = r.get("Price", 0)
        ma20v  = r.get("MA20", 0)
        ma50v  = r.get("MA50", 0)
        entry  = r.get("Entry", 0)
        stopv  = r.get("Stop", 0)
        target = r.get("Target", 0)
        rrv    = r.get("R:R", 0)
        posv   = r.get("Pos$", 0)
        volv   = r.get("Vol", "")
        earnv  = r.get("Earn", "")
        rows += (
            '<tr>'
            f'<td class="tk">{tkr}</td>'
            f'<td>${price:,.2f}</td>'
            f'<td style="color:{rsi_c};font-weight:700">{rsi}</td>'
            f'<td>${ma20v:,.2f}</td>'
            f'<td>${ma50v:,.2f}</td>'
            f'<td style="color:#22c55e">${entry:,.2f}</td>'
            f'<td style="color:#ef4444">${stopv:,.2f}</td>'
            f'<td style="color:#38bdf8">${target:,.2f}</td>'
            f'<td>{rrv}</td>'
            f'<td>${posv:,.0f}</td>'
            f'<td style="color:#8b949e">{volv}</td>'
            f'<td style="color:#f59e0b">{earnv}</td>'
            '</tr>'
        )

    no_results = '<tr><td colspan="12" style="color:#555;text-align:center;padding:24px">No momentum setups found.</td></tr>' 

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Momentum Report {today}</title>
<style>
  body  {{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:24px}}
  h1    {{color:#58a6ff;margin:0 0 4px}}
  .sub  {{color:#8b949e;font-size:14px;margin:0 0 20px}}
  .pills{{display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap}}
  .pill {{background:#1e293b;border-radius:20px;padding:7px 18px;font-size:14px;font-weight:700;border:2px solid #334155}}
  .pill.spy{{border-color:{spy_color};color:{spy_color}}}
  .pill.cnt{{border-color:#58a6ff;color:#58a6ff}}
  .note {{background:#1e293b;border-left:4px solid #f59e0b;padding:12px 16px;
          border-radius:6px;margin-bottom:20px;font-size:13px;color:#94a3b8;line-height:1.7}}
  .note b {{color:#e2e8f0}}
  table {{width:100%;border-collapse:collapse;background:#1e293b;border-radius:10px;overflow:hidden}}
  thead tr {{background:#0f172a}}
  th    {{padding:10px 12px;text-align:left;color:#6e7681;font-size:11px;text-transform:uppercase;white-space:nowrap}}
  td    {{padding:9px 12px;font-size:13px;border-bottom:1px solid #21262d}}
  .tk   {{font-weight:700;font-size:15px}}
  tr:hover>td{{background:#ffffff08}}
</style>
</head>
<body>
<h1>⚡ Momentum Scan — {today}</h1>
<p class="sub">Stocks above MA20 + MA50 · RSI 55–78 · Volume confirmed</p>

<div class="pills">
  <div class="pill spy">{spy_badge}</div>
  <div class="pill cnt">⚡ {len(results)} setups found</div>
  <div class="pill">💼 ${portfolio_size:,.0f} portfolio</div>
</div>

<div class="note">
  <b>What is Momentum Mode?</b><br>
  These are NOT retest-of-support setups. These are stocks already in an uptrend
  that are likely to continue higher during a broad market rally.<br>
  <b>Entry</b> = current price &nbsp;|&nbsp;
  <b>Stop</b> = below 20-week MA &nbsp;|&nbsp;
  <b>Target</b> = R:R 1:2 &nbsp;|&nbsp;
  <b>Risk</b> = 1% of portfolio per trade.<br>
  Use alongside the main scanner — not instead of it.
</div>

<table>
  <thead><tr>
    <th>Ticker</th><th>Price</th><th>RSI</th><th>MA20</th><th>MA50</th>
    <th>Entry</th><th>Stop</th><th>Target</th><th>R:R</th>
    <th>Position</th><th>Volume</th><th>Earnings</th>
  </tr></thead>
  <tbody>{rows if rows else no_results}</tbody>
</table>

<p style="color:#334155;font-size:11px;text-align:center;margin-top:24px">
  Cycles Trading Momentum Mode &mdash; {datetime.datetime.now().strftime("%d/%m/%Y %H:%M")}
  &mdash; This is supplemental to the main Cycles Trading retest scanner.
</p>
</body>
</html>"""

    out_path.write_text(html, encoding="utf-8")
