#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ct_monthly_scan.py -- Monthly timeframe setup scanner.

Scans the full universe on MONTHLY data and finds stocks near
monthly S/R levels (within 8%).  Auto-adds candidates to
watch_alerts.json with timeframe="MONTHLY" so ct_watch_checker
tracks them daily until price reaches the level.

Usage:
  python cycles_trading_scanner.py monthly
  python cycles_trading_scanner.py monthly --dist 5
"""

import os, json, datetime, warnings, webbrowser
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
warnings.filterwarnings('ignore')

BASE_DIR    = Path(__file__).parent
REPORTS_DIR = BASE_DIR / 'REPORTS'
REPORTS_DIR.mkdir(exist_ok=True)
WATCH_FILE  = BASE_DIR / 'watch_alerts.json'
TODAY       = datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
#  Watchlist helpers
# ---------------------------------------------------------------------------

def _load_watchlist():
    if WATCH_FILE.exists():
        try:
            return json.loads(WATCH_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'email': '', 'tickers': []}


def _save_watchlist(data):
    WATCH_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


# ---------------------------------------------------------------------------
#  Single-ticker monthly analysis
# ---------------------------------------------------------------------------

def _scan_monthly(ticker: str) -> dict:
    try:
        import yfinance as yf
        from ct_indicators import swing_lows, swing_highs
        from ct_factors import check_fibonacci_zone

        asset = yf.Ticker(ticker)
        df = asset.history(period='5y', interval='1mo',
                           auto_adjust=True, raise_errors=False)
        if df is None or len(df) < 18:
            return None
        df.columns = [c.capitalize() for c in df.columns]
        df = df.dropna(subset=['Close'])

        price = float(df['Close'].iloc[-1])
        if price <= 0:
            return None

        # Monthly RSI
        delta = df['Close'].diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss
        rsi   = float((100 - 100 / (1 + rs)).iloc[-1])

        # Monthly S/R
        lows  = swing_lows(df['Low'],  order=2)
        highs = swing_highs(df['High'], order=2)
        supports    = sorted([v for v in lows  if v < price * 0.985], reverse=True)
        resistances = sorted([v for v in highs if v > price * 1.015])

        m_support = supports[0]    if supports    else None
        m_resist  = resistances[0] if resistances else None

        d_sup = abs(price - m_support) / price * 100 if m_support else 999
        d_res = abs(price - m_resist)  / price * 100 if m_resist  else 999

        if d_sup <= d_res:
            nearest_label = 'SUPPORT'
            dist_pct      = d_sup
            direction     = 'LONG'
        else:
            nearest_label = 'RESISTANCE'
            dist_pct      = d_res
            direction     = 'SHORT'

        if dist_pct > 8.0:
            return None

        # Monthly trend vs 6M MA
        ma6 = float(df['Close'].rolling(6).mean().iloc[-1])
        if price > ma6 * 1.02:
            m_trend = 'BULL'
        elif price < ma6 * 0.98:
            m_trend = 'BEAR'
        else:
            m_trend = 'NEUTRAL'

        if direction == 'LONG'  and m_trend == 'BEAR':
            return None
        if direction == 'SHORT' and m_trend == 'BULL':
            return None

        # Fibonacci zone
        try:
            fib_zone, fib_pct, fib_sl, fib_sh, fib_lvls = \
                check_fibonacci_zone(df, direction, price)
        except Exception:
            fib_zone, fib_pct = 'UNKNOWN', 0.0

        fib_desc = {
            'GOLDEN_ZONE':    'Golden Zone (38.2-61.8%)',
            'SHALLOW':        'Shallow (<38.2%)',
            'DEEP':           'Deep (61.8-78.6%)',
            'TOO_DEEP':       'Too Deep (>78.6%)',
            'NO_RETRACEMENT': 'No Retracement',
            'UNKNOWN':        '-',
        }.get(fib_zone, fib_zone)

        # Entry / Stop / Target
        if direction == 'LONG':
            entry  = round(price, 2)
            stop   = round(m_support * 0.96, 2) if m_support else round(price * 0.92, 2)
            target = round(m_resist,  2)         if m_resist  else round(price * 1.15, 2)
        else:
            entry  = round(price, 2)
            stop   = round(m_resist  * 1.04, 2) if m_resist  else round(price * 1.08, 2)
            target = round(m_support, 2)         if m_support else round(price * 0.85, 2)

        risk   = abs(entry - stop)
        reward = abs(target - entry)
        rr     = round(reward / risk, 2) if risk > 0 else 0

        # Score
        score = 50
        if dist_pct <= 3:   score += 20
        elif dist_pct <= 5: score += 12
        elif dist_pct <= 8: score += 5
        if fib_zone == 'GOLDEN_ZONE': score += 15
        elif fib_zone == 'SHALLOW':   score -= 5
        if m_trend in ('BULL', 'BEAR'): score += 10
        if rr >= 3: score += 8
        elif rr >= 2: score += 4
        score = max(15, min(92, score))

        return {
            'ticker':          ticker,
            'direction':       direction,
            'timeframe':       'MONTHLY',
            'price':           round(price, 2),
            'rsi_monthly':     round(rsi, 1),
            'monthly_support': round(m_support, 2) if m_support else None,
            'monthly_resist':  round(m_resist,  2) if m_resist  else None,
            'nearest_label':   nearest_label,
            'dist_pct':        round(dist_pct, 1),
            'monthly_trend':   m_trend,
            'fib_zone':        fib_zone,
            'fib_desc':        fib_desc,
            'fib_pct':         round(fib_pct, 1),
            'entry':           entry,
            'stop':            stop,
            'target':          target,
            'rr':              rr,
            'score':           score,
        }

    except Exception:
        return None


# ---------------------------------------------------------------------------
#  Auto-add to watchlist
# ---------------------------------------------------------------------------

def _make_watchlist_entry(candidate: dict) -> dict:
    t = candidate['ticker']
    note = (
        f"auto: monthly {candidate['nearest_label'].lower()} retest "
        f"dist {candidate['dist_pct']}% "
        f"| {candidate['fib_desc']} "
        f"| score {candidate['score']}%"
    )
    return {
        'ticker':          t,
        'direction':       candidate['direction'],
        'timeframe':       'MONTHLY',
        'note':            note,
        'added':           TODAY,
        'last_alerted':    None,
        'alert_count':     0,
        'auto':            True,
        'status':          'PENDING',
        'prob':            candidate['score'],
        'entry_price':     candidate['entry'],
        'stop_price':      candidate['stop'],
        'target_price':    candidate['target'],
        'rr':              candidate['rr'],
        'last_checked':    TODAY,
        'monthly_support': candidate['monthly_support'],
        'monthly_resist':  candidate['monthly_resist'],
        'dist_pct':        candidate['dist_pct'],
        'fib_zone':        candidate['fib_zone'],
        'monthly_trend':   candidate['monthly_trend'],
    }


# ---------------------------------------------------------------------------
#  Main scan entry point
# ---------------------------------------------------------------------------

def scan_monthly(max_dist: float = 8.0):
    print()
    print('=' * 62)
    print('   CYCLES TRADING -- MONTHLY SCAN')
    print('   Finds stocks near monthly S/R levels (top-down)')
    print('=' * 62)
    print()

    cache = BASE_DIR / '.universe_cache.json'
    if cache.exists():
        with open(cache, encoding='utf-8') as f:
            us_tickers = json.load(f)['data']['us']
    else:
        us_tickers = ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META',
                      'TSLA', 'AVGO', 'ORCL', 'AMD', 'PLTR', 'CAMT', 'GOOG']

    print(f'  Universe : {len(us_tickers)} tickers')
    print(f'  Filter   : price within {max_dist}% of monthly S/R level')
    print()

    results = []
    total   = len(us_tickers)

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_scan_monthly, t): t for t in us_tickers}
        done = 0
        for fut in as_completed(futures):
            done += 1
            pct = int(done / total * 35)
            print(f'  [{"#" * pct}{"-" * (35-pct)}] {done:>3}/{total}', end='\r')
            r = fut.result()
            if r and r['dist_pct'] <= max_dist:
                results.append(r)

    results.sort(key=lambda x: (x['dist_pct'], -x['score']))
    print(f'  Done!  Found {len(results)} monthly setups.          ')
    print()

    if not results:
        print('  No monthly S/R setups found in current universe.')
        return

    # Print summary table
    print(f"  {'Ticker':<8} {'Dir':<6} {'Price':>8} {'RSI':>5} "
          f"{'Dist%':>6} {'M.Level':>9} {'Fib Zone':<24} {'R:R':>5} {'Score':>6}")
    print('  ' + '-' * 79)
    for r in results:
        lvl = r['monthly_support'] if r['direction'] == 'LONG' else r['monthly_resist']
        print(f"  {r['ticker']:<8} {r['direction']:<6} {r['price']:>8.2f} "
              f"{r['rsi_monthly']:>5.1f} {r['dist_pct']:>5.1f}% "
              f"{(lvl or 0):>9.2f} {r['fib_desc']:<24} "
              f"{r['rr']:>5.2f} {r['score']:>5}%")
    print()

    # Auto-add to watchlist
    wl = _load_watchlist()
    existing = {t['ticker'] for t in wl.get('tickers', [])}
    added = []
    for r in results:
        if r['ticker'] not in existing:
            wl['tickers'].append(_make_watchlist_entry(r))
            existing.add(r['ticker'])
            added.append(r['ticker'])

    if added:
        _save_watchlist(wl)
        print(f"  Added to watchlist: {', '.join(added)}")
    else:
        print('  All monthly candidates already in watchlist.')
    print()

    # HTML report
    ts       = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    out_path = REPORTS_DIR / f'monthly_scan_{ts}.html'
    _write_html(results, out_path, max_dist)
    print(f'  Report: REPORTS/monthly_scan_{ts}.html')
    try:
        webbrowser.open('file:///' + str(out_path).replace(os.sep, '/'))
    except Exception:
        pass


# ---------------------------------------------------------------------------
#  HTML report
# ---------------------------------------------------------------------------

def _write_html(results, out_path, max_dist):
    today = datetime.date.today().strftime('%d/%m/%Y')

    fib_colors = {
        'GOLDEN_ZONE':    '#22c55e',
        'SHALLOW':        '#f59e0b',
        'DEEP':           '#ef4444',
        'TOO_DEEP':       '#7f1d1d',
        'NO_RETRACEMENT': '#6b7280',
        'UNKNOWN':        '#6b7280',
    }

    rows = ''
    for r in results:
        lvl    = r['monthly_support'] if r['direction'] == 'LONG' else r['monthly_resist']
        dir_c  = '#22c55e' if r['direction'] == 'LONG' else '#ef4444'
        fib_c  = fib_colors.get(r['fib_zone'], '#6b7280')
        sc_c   = '#22c55e' if r['score'] >= 70 else ('#f59e0b' if r['score'] >= 55 else '#ef4444')
        dist_c = '#22c55e' if r['dist_pct'] <= 3 else ('#f59e0b' if r['dist_pct'] <= 5 else '#e2e8f0')
        rows += (
            '<tr>'
            + f'<td style="font-weight:700;font-size:15px">{r["ticker"]}</td>'
            + f'<td style="color:{dir_c};font-weight:700">{r["direction"]}</td>'
            + f'<td>${r["price"]:,.2f}</td>'
            + f'<td>{r["rsi_monthly"]}</td>'
            + f'<td style="color:{dist_c};font-weight:700">{r["dist_pct"]}%</td>'
            + f'<td>${(lvl or 0):,.2f}</td>'
            + f'<td style="color:{fib_c}">{r["fib_desc"]}</td>'
            + f'<td style="color:#22c55e">${r["entry"]:,.2f}</td>'
            + f'<td style="color:#ef4444">${r["stop"]:,.2f}</td>'
            + f'<td style="color:#38bdf8">${r["target"]:,.2f}</td>'
            + f'<td>{r["rr"]}</td>'
            + f'<td style="color:{sc_c};font-weight:700">{r["score"]}%</td>'
            + f'<td style="color:#8b949e;font-size:11px">{r["monthly_trend"]}</td>'
            + '</tr>'
        )

    no_row = (
        '<tr><td colspan="13" style="color:#555;text-align:center;padding:24px">'
        f'No monthly setups found within {max_dist}%.</td></tr>'
    )

    css = (
        'body{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:24px}'
        'h1{color:#58a6ff;margin:0 0 4px}'
        '.sub{color:#8b949e;font-size:14px;margin:0 0 20px}'
        '.pills{display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap}'
        '.pill{background:#1e293b;border-radius:20px;padding:7px 18px;'
        'font-size:14px;font-weight:700;border:2px solid #334155}'
        '.note{background:#1e293b;border-left:4px solid #58a6ff;padding:12px 16px;'
        'border-radius:6px;margin-bottom:20px;font-size:13px;color:#94a3b8;line-height:1.7}'
        '.note b{color:#e2e8f0}'
        'table{width:100%;border-collapse:collapse;background:#1e293b;'
        'border-radius:10px;overflow:hidden}'
        'thead tr{background:#0f172a}'
        'th{padding:10px 12px;text-align:left;color:#6e7681;font-size:11px;'
        'text-transform:uppercase;white-space:nowrap}'
        'td{padding:9px 12px;font-size:13px;border-bottom:1px solid #21262d}'
        'tr:hover>td{background:#ffffff08}'
    )

    pills = (
        f'<div class="pill" style="border-color:#58a6ff;color:#58a6ff">'
        f'Monthly: {len(results)} setups</div>'
        f'<div class="pill" style="border-color:#f59e0b;color:#f59e0b">'
        f'Filter: within {max_dist}% of level</div>'
    )

    note_html = (
        '<div class="note">'
        '<b>What is Monthly Scan?</b><br>'
        'These are stocks retesting major monthly S/R levels - the same levels '
        'institutions watch. They are automatically added to your watchlist.<br>'
        'The <b>daily watch checker</b> will alert you when price reaches the exact '
        'entry zone on the weekly chart. <b>Do not enter until weekly confirmation.</b>'
        '</div>'
    )

    thead = (
        '<thead><tr>'
        '<th>Ticker</th><th>Dir</th><th>Price</th><th>RSI</th><th>Dist%</th>'
        '<th>M.Level</th><th>Fib Zone</th><th>Entry</th><th>Stop</th>'
        '<th>Target</th><th>R:R</th><th>Score</th><th>M.Trend</th>'
        '</tr></thead>'
    )

    ts_str = datetime.datetime.now().strftime('%d/%m/%Y %H:%M')
    footer = (
        f'<p style="color:#334155;font-size:11px;text-align:center;margin-top:24px">'
        f'Cycles Trading Monthly Scan &mdash; {ts_str} &mdash; '
        f'Confirm on weekly chart before entering.</p>'
    )

    html = (
        '<!DOCTYPE html><html><head>'
        '<meta charset="UTF-8">'
        f'<title>Monthly Scan {today}</title>'
        f'<style>{css}</style>'
        '</head><body>'
        f'<h1>Monthly Scan &mdash; {today}</h1>'
        f'<p class="sub">Stocks near monthly S/R levels &middot; Top-down confirmation &middot; Auto-added to watchlist</p>'
        f'<div class="pills">{pills}</div>'
        f'{note_html}'
        f'<table>{thead}<tbody>{rows if rows else no_row}</tbody></table>'
        f'{footer}'
        '</body></html>'
    )

    out_path.write_text(html, encoding='utf-8')
