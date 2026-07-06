#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║         CYCLES TRADING SCANNER  v6                          ║
║         סייקלס טריידינג — LONG + SHORT — Stocks + Crypto    ║
║                                                              ║
║   Just double-click run_scanner.bat to run                  ║
╚══════════════════════════════════════════════════════════════╝

WHAT THIS SCRIPT DOES:
  Scans stocks AND crypto on the WEEKLY chart.
  Finds setups for BOTH directions:

  🟢 LONG  — uptrend + pullback to support  + RSI not overbought
  🔴 SHORT — downtrend + bounce to resistance + RSI not oversold

  For every qualifying asset it calculates:
  - Entry, Stop Loss, Target (based on key levels + ATR)
  - Risk:Reward ratio (minimum 1:2)
  - Position size (based on your portfolio and risk %)
  - Score to rank the best setups first
"""

import sys, time, warnings, os, webbrowser, logging
from datetime import datetime
warnings.filterwarnings('ignore')
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.getLogger('peewee').setLevel(logging.CRITICAL)

def _install(pkg):
    import subprocess
    print(f"  Installing {pkg}...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])

try:    import yfinance as yf
except: _install("yfinance"); import yfinance as yf
try:    import pandas as pd
except: _install("pandas"); import pandas as pd
import json

# ── Module imports ──────────────────────────────────────────
from ct_config    import *
from ct_learnings import load_learnings, save_case_study
from ct_indicators import (
    rsi, atr, get_trend, get_levels, get_support_quality,
    check_level_reliability, check_false_breakout, check_level_ambiguity,
    check_swing_broken, calc_macd, calc_bollinger, estimate_time_horizon,
    check_fibonacci_zone, vol_declining, swing_lows, swing_highs,
    _pm_pivot_lows, _pm_pivot_highs,
)
from ct_market_data import get_earnings, get_monthly_analysis, get_sector_rs
from ct_positions import (
    _pm_load, _pm_save, pm_add, pm_close,
    pm_rule1_swing, pm_rule2_momentum, pm_check_hits,
    manage_positions, list_positions,
)
from ct_factors  import calc_probability
from ct_analysis import (
    clean_ticker, send_email_summary, get_fundamental_analysis,
    is_hard_blocked, get_traffic_light, calc_position_size,
    _build_setup_dict, _finalize_setup, _fetch_market_data,
    _detect_setup, _detect_long_setup, _detect_short_setup, analyze,
)
from ct_report   import profit_breakdown
from ct_html     import generate_html, make_pine_for_ticker, save_pine_script

# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print()
    print("=" * 62)
    n_cases = len(LEARNINGS.get('case_studies',[])) if LEARNINGS else 0
    print(f"   CYCLES TRADING SCANNER  v6  |  📚 {n_cases} case studies loaded")
    print("   LONG + SHORT  |  US · INTL · TASE · CRYPTO · COMMODITIES  |  Weekly")
    try:
        from zoneinfo import ZoneInfo as _ZI
        _now = datetime.now(_ZI('Asia/Jerusalem'))
    except Exception:
        from datetime import timezone as _tz, timedelta as _td
        _now = datetime.now(_tz(_td(hours=3)))
    print(f"   {_now.strftime('%Y-%m-%d  %H:%M')} Israel Time")
    print("=" * 62)
    print()

    # ══════════════════════════════════════════════════════════
    #  OPTION 1 — Portfolio size
    # ══════════════════════════════════════════════════════════
    global PORTFOLIO_SIZE
    if PORTFOLIO_SIZE is None:
        try:
            raw = input("  [1] Enter your portfolio size in $  (e.g. 1000): ").replace(',','').strip()
            PORTFOLIO_SIZE = float(raw) if raw else 1000
        except (EOFError, ValueError):
            PORTFOLIO_SIZE = 1000
        print(f"      -> Portfolio set to: ${PORTFOLIO_SIZE:,.0f}")

    # ══════════════════════════════════════════════════════════
    #  OPTION 2 — Specific ticker or scan all
    # ══════════════════════════════════════════════════════════
    print()
    try:
        ticker_input = input(
            "  [2] Specific ticker (press ENTER to scan ALL)\n"
            "      US: AAPL  |  Israeli: LUMI.TA  |  Intl: SAP.DE / 7203.T\n"
            "      Crypto: BTC-USD  |  Commodity: GC=F (Gold) / CL=F (Oil)\n"
            "      > "
        ).strip().upper()
    except (EOFError, KeyboardInterrupt):
        ticker_input = ''

    # Interval is always Weekly — Cycles Trading standard
    INTERVAL, PERIOD, IV_LABEL = '1wk', '2y', 'Weekly (1wk)'
    print()

    # Build the lists to scan based on input
    INTL_SUFFIXES = ('.L','.DE','.PA','.T','.HK','.TO','.AX','.SW','.NS')

    if ticker_input:
        if ticker_input.endswith('-USD') or any(ticker_input == t.replace('-USD','') for t in CRYPTO_WATCHLIST):
            full = ticker_input if ticker_input.endswith('-USD') else ticker_input + '-USD'
            if full not in CRYPTO_WATCHLIST: CRYPTO_WATCHLIST.append(full); print(f"  -> Added '{full}' to crypto list.")
            scan_stocks = []; scan_israel = []; scan_intl = []; scan_crypto = [full]; scan_commodity = []
        elif ticker_input.endswith('=F'):
            if ticker_input not in COMMODITY_WATCHLIST: COMMODITY_WATCHLIST.append(ticker_input); print(f"  -> Added '{ticker_input}' to commodities.")
            scan_stocks = []; scan_israel = []; scan_intl = []; scan_crypto = []; scan_commodity = [ticker_input]
        elif ticker_input.endswith('.TA'):
            if ticker_input not in ISRAEL_WATCHLIST: ISRAEL_WATCHLIST.append(ticker_input); print(f"  -> Added '{ticker_input}' to Israeli list.")
            scan_stocks = []; scan_israel = [ticker_input]; scan_intl = []; scan_crypto = []; scan_commodity = []
        elif any(ticker_input.endswith(s) for s in INTL_SUFFIXES):
            if ticker_input not in INTL_WATCHLIST: INTL_WATCHLIST.append(ticker_input); print(f"  -> Added '{ticker_input}' to international list.")
            scan_stocks = []; scan_israel = []; scan_intl = [ticker_input]; scan_crypto = []; scan_commodity = []
        else:
            if ticker_input not in STOCK_WATCHLIST: STOCK_WATCHLIST.append(ticker_input); print(f"  -> Added '{ticker_input}' to US stocks.")
            scan_stocks = [ticker_input]; scan_israel = []; scan_intl = []; scan_crypto = []; scan_commodity = []
        print(f"  Scanning: {ticker_input}")
    else:
        scan_stocks    = STOCK_WATCHLIST
        scan_israel    = ISRAEL_WATCHLIST
        scan_intl      = INTL_WATCHLIST
        scan_crypto    = CRYPTO_WATCHLIST
        scan_commodity = COMMODITY_WATCHLIST
        total_all = len(scan_stocks)+len(scan_israel)+len(scan_intl)+len(scan_crypto)+len(scan_commodity)
        print(f"  Scanning ALL {total_all} assets:")
        print(f"    US Stocks: {len(scan_stocks)}  |  Israeli: {len(scan_israel)}  |  International: {len(scan_intl)}")
        print(f"    Crypto: {len(scan_crypto)}  |  Commodities: {len(scan_commodity)}")

    print()

    risk_trade   = PORTFOLIO_SIZE * RISK_PCT
    profit_1win  = risk_trade * MIN_RR
    need_3k_week = (3000 / MIN_RR) / RISK_PCT

    print(f"  +--------------------------------------------------+")
    print(f"  |  Portfolio size    : ${PORTFOLIO_SIZE:>10,.0f}                  |")
    print(f"  |  Risk per trade    : ${risk_trade:>10,.0f}  ({RISK_PCT*100:.0f}%)           |")
    print(f"  |  Profit per 1 win  : ${profit_1win:>10,.0f}  (R:R {MIN_RR:.0f}:1)          |")
    print(f"  |  Need for $3k/week : ${need_3k_week:>10,.0f}  portfolio size     |")
    print(f"  |  Chart interval    : {IV_LABEL:<28}  |")
    print(f"  +--------------------------------------------------+")
    print()

    all_results = []
    total = len(scan_stocks)+len(scan_israel)+len(scan_intl)+len(scan_crypto)+len(scan_commodity)
    idx   = 0

    def scan_group(tickers, label, **kwargs):
        """Scan a list of tickers concurrently — network I/O releases the GIL."""
        nonlocal idx
        if not tickers: return
        print(f"\n  ── {label} ({'—'*40})")
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _scan_one(ticker):
            return (ticker, analyze(ticker, PORTFOLIO_SIZE, interval=INTERVAL, period=PERIOD, **kwargs))

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(_scan_one, t): t for t in tickers}
            for future in as_completed(futures):
                idx += 1
                pct = int(idx / max(total, 1) * 30)
                try:
                    ticker, setups = future.result()
                except Exception:
                    ticker = futures[future]
                    setups = []
                print(f"  [{'#'*pct}{'-'*(30-pct)}] {idx:>3}/{total}  {ticker:<14}", end='\r')
                for s in setups:
                    all_results.append(s)

    scan_group(scan_stocks,    'US Stocks',             is_crypto=False, is_israel=False, is_commodity=False, is_intl=False)
    scan_group(scan_israel,    'Israeli Stocks (TASE)',  is_crypto=False, is_israel=True,  is_commodity=False, is_intl=False)
    scan_group(scan_intl,      'International Stocks',   is_crypto=False, is_israel=False, is_commodity=False, is_intl=True)
    scan_group(scan_crypto,    'Crypto',                 is_crypto=True,  is_israel=False, is_commodity=False, is_intl=False)
    scan_group(scan_commodity, 'Commodities',            is_crypto=False, is_israel=False, is_commodity=True,  is_intl=False)

    print(f"\n  Done! Scan complete.                          ")
    print()

    # ── Sort by score ────────────────────────────────────────
    all_results.sort(key=lambda x: x['_score'], reverse=True)

    longs  = [r for r in all_results if 'LONG'  in r['Dir']]
    shorts = [r for r in all_results if 'SHORT' in r['Dir']]

    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 300)

    # ── Print LONG results ───────────────────────────────────
    print("=" * 80)
    print(f"  [LONG]  LONG SETUPS  — {len(longs)} found")
    print("=" * 80)
    if longs:
        cols = ['Ticker','Type','Price','RSI','Support','Entry','Stop','Target','R:R','Pos$','Vol','Earn']
        print(pd.DataFrame(longs)[cols].to_string(index=False))
    else:
        print("  No LONG setups found today.")
    print()

    print("=" * 80)
    print(f"  [SHORT] SHORT SETUPS — {len(shorts)} found")
    print("=" * 80)
    if shorts:
        cols = ['Ticker','Type','Price','RSI','Resist','Entry','Stop','Target','R:R','Pos$','Vol','Earn']
        print(pd.DataFrame(shorts)[cols].to_string(index=False))
    else:
        print("  No SHORT setups found today.")
    print()

    # ── Profit potential breakdown ───────────────────────────
    if all_results:
        profit_breakdown(all_results, PORTFOLIO_SIZE, risk_trade)

    # ── Setup paths ──────────────────────────────────────────
    ts       = datetime.now().strftime('%Y%m%d_%H%M')
    script_d = os.path.dirname(os.path.abspath(__file__))

    # Create REPORTS folder next to the script if it doesn't exist
    reports_d = os.path.join(script_d, 'REPORTS')
    os.makedirs(reports_d, exist_ok=True)

    generated_files = []

    # ── Save CSV ─────────────────────────────────────────────
    if all_results:
        fpath = os.path.join(reports_d, f"cycles_scan_{ts}.csv")
        out   = [{k:v for k,v in r.items() if k not in ('_score','_pfacts','_raw')} for r in all_results]
        pd.DataFrame(out).to_csv(fpath, index=False)
        generated_files.append(fpath)
        print(f"  CSV saved   : REPORTS\\cycles_scan_{ts}.csv")
        print()

    # ── HTML Report ──────────────────────────────────────────
    html_path = generate_html(all_results, reports_d, ts,
                              PORTFOLIO_SIZE, risk_trade, IV_LABEL,
                              len(scan_stocks), len(scan_israel),
                              len(scan_intl), len(scan_crypto), len(scan_commodity))
    if html_path:
        generated_files.append(html_path)
        print(f"  HTML saved  : REPORTS\\cycles_report_{ts}.html")
        webbrowser.open('file:///' + html_path.replace('\\', '/'))
        print()

        # ── Email summary notification ────────────────────────
        longs  = [r for r in all_results if '▲' in r.get('Dir','') or 'LONG' in r.get('Dir','')]
        shorts = [r for r in all_results if '▼' in r.get('Dir','') or 'SHORT' in r.get('Dir','')]
        green_l = [r for r in longs  if not r.get('IsWatchlist') and r.get('Prob',0) >= 70]
        green_s = [r for r in shorts if not r.get('IsWatchlist') and r.get('Prob',0) >= 70]
        yellow  = [r for r in all_results if not r.get('IsWatchlist') and 65 <= r.get('Prob',0) < 70]
        watch   = [r for r in all_results if r.get('IsWatchlist')]

        tickers_green  = ', '.join(r['Ticker'] for r in green_l + green_s) or 'אין'
        tickers_yellow = ', '.join(r['Ticker'] for r in yellow[:8])         or 'אין'
        tickers_watch  = ', '.join(r['Ticker'] for r in watch[:8])          or 'אין'

        subj = f"🤖 Cycles Scanner {ts} — {len(green_l+green_s)} GO setups"

        # Plain-text body
        txt_body = (
            f"Cycles Trading Scanner — {ts}\n"
            f"{'='*42}\n"
            f"🟢 GO  ({len(green_l+green_s)} setups): {tickers_green}\n"
            f"🟡 WAIT ({len(yellow)} setups): {tickers_yellow}\n"
            f"👁  Watchlist ({len(watch)}):  {tickers_watch}\n"
            f"📊 Total scanned: {len(all_results)} setups\n"
            f"💼 Portfolio: ${PORTFOLIO_SIZE:,}  |  Risk/trade: ${int(PORTFOLIO_SIZE*RISK_PCT)}\n"
            f"{'='*42}\n"
            f"Open the HTML report for full details:\n{html_path}\n"
        )

        # HTML body — colour-coded table of green setups
        rows_html = ''
        for r in green_l + green_s:
            tl = r.get('TrafficLight','')
            tl_color = '#27ae60' if tl=='GREEN' else '#f39c12' if tl=='YELLOW' else '#e74c3c'
            rows_html += (
                f"<tr>"
                f"<td style='padding:6px 10px;font-weight:bold'>{r['Ticker']}</td>"
                f"<td style='padding:6px 10px'>{r.get('Dir','')}</td>"
                f"<td style='padding:6px 10px;color:{tl_color};font-weight:bold'>{r.get('Prob',0)}%</td>"
                f"<td style='padding:6px 10px'>{r.get('Entry','')}</td>"
                f"<td style='padding:6px 10px'>{r.get('Stop','')}</td>"
                f"<td style='padding:6px 10px'>{r.get('Target','')}</td>"
                f"<td style='padding:6px 10px'>{r.get('Horizon','')}</td>"
                f"<td style='padding:6px 10px'>${r.get('Pos$',0):,.0f} ({r.get('Pos%',0)}%)</td>"
                f"</tr>"
            )
        html_body = f"""
<html><body style='font-family:Arial,sans-serif;color:#222;max-width:900px'>
<h2 style='color:#1a5276'>🤖 Cycles Trading Scanner — {ts}</h2>
<p style='font-size:15px'>
  🟢 <b>GO:</b> {len(green_l+green_s)} setups &nbsp;|&nbsp;
  🟡 <b>WAIT:</b> {len(yellow)} &nbsp;|&nbsp;
  👁 <b>Watchlist:</b> {len(watch)} &nbsp;|&nbsp;
  📊 Total: {len(all_results)}
</p>
<table border='0' cellspacing='0' style='border-collapse:collapse;width:100%;font-size:13px'>
  <thead>
    <tr style='background:#1a5276;color:#fff'>
      <th style='padding:8px 10px;text-align:left'>Ticker</th>
      <th style='padding:8px 10px;text-align:left'>Dir</th>
      <th style='padding:8px 10px;text-align:left'>Prob</th>
      <th style='padding:8px 10px;text-align:left'>Entry</th>
      <th style='padding:8px 10px;text-align:left'>Stop</th>
      <th style='padding:8px 10px;text-align:left'>Target</th>
      <th style='padding:8px 10px;text-align:left'>Horizon</th>
      <th style='padding:8px 10px;text-align:left'>Position</th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
</table>
<p style='margin-top:18px;color:#555'>
  💼 Portfolio: <b>${PORTFOLIO_SIZE:,}</b> &nbsp;|&nbsp;
  Risk/trade: <b>${int(PORTFOLIO_SIZE*RISK_PCT)}</b> (1%)
</p>
<p><a href='file:///{html_path.replace(chr(92),'/')}'>📂 Open full HTML report</a></p>
</body></html>
"""
        send_email_summary(subj, txt_body, html_body)

    # ── Pine Script ──────────────────────────────────────────
    pine_path = save_pine_script(all_results, reports_d, ts)
    if pine_path:
        generated_files.append(pine_path)

    # ── Summary ──────────────────────────────────────────────
    if generated_files:
        print(f"  All files saved to: {reports_d}")
        print()

    # ── Position Manager — check open trades after every scan ─
    manage_positions(send_email=True)



if __name__ == "__main__":
    import sys as _sys
    _args = _sys.argv[1:]

    if not _args:
        # Normal scan
        main()

    elif _args[0] == 'positions':
        # python cycles_trading_scanner.py positions
        # python cycles_trading_scanner.py positions --all
        list_positions(show_closed='--all' in _args)

    elif _args[0] == 'check':
        # python cycles_trading_scanner.py check
        # python cycles_trading_scanner.py check --email
        manage_positions(send_email='--email' in _args)

    elif _args[0] == 'add':
        # python cycles_trading_scanner.py add LMND LONG 54.13 45.07 71.38 87.04 100.15 11 "discord rec"
        if len(_args) >= 9:
            _, ticker, direction, entry, stop, tp1, tp2, tp3, units, *rest = _args
            notes = ' '.join(rest)
            pm_add(ticker, direction, float(entry), float(stop),
                   float(tp1), float(tp2), float(tp3), int(units), notes)
        else:
            # interactive
            print('Usage: python cycles_trading_scanner.py add TICKER DIR ENTRY STOP TP1 TP2 TP3 UNITS [notes]')
            ticker    = input('Ticker     : ').strip().upper()
            direction = input('Direction  : ').strip().upper()
            entry     = float(input('Entry      : '))
            stop_p    = float(input('Stop       : '))
            tp1       = float(input('TP1        : '))
            tp2       = float(input('TP2        : '))
            tp3       = float(input('TP3        : '))
            units     = int(input('Units      : '))
            notes     = input('Notes      : ').strip()
            pm_add(ticker, direction, entry, stop_p, tp1, tp2, tp3, units, notes)

    elif _args[0] == 'close':
        # python cycles_trading_scanner.py close LMND_202607051437 [exit_price]
        if len(_args) >= 2:
            pos_id     = _args[1]
            exit_price = float(_args[2]) if len(_args) >= 3 else None
            pm_close(pos_id, exit_price)
        else:
            print('Usage: python cycles_trading_scanner.py close <position_id> [exit_price]')

    else:
        print('Commands: (none) → full scan | positions | check [--email] | add | close <id> [price]')
