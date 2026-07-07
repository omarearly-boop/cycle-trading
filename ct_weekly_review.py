#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ct_weekly_review.py — Weekly winner miss-rate analyzer.

For every stock in our universe:
  1. Calculates last week's % change via yfinance
  2. Flags top gainers (default: >5%)
  3. Cross-references with our most recent scan CSV
  4. Splits into: CAUGHT (in scan) vs MISSED (not in scan)
  5. For MISSED winners, explains why (no S/R level, too far, RSI, etc.)
  6. Saves HTML report to REPORTS/

Usage:
  python ct_weekly_review.py            # analyze last week
  python ct_weekly_review.py --min 3   # lower threshold to 3%
"""

import sys, os, csv, glob, json, datetime, argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR    = Path(__file__).parent
REPORTS_DIR = BASE_DIR / 'REPORTS'
REPORTS_DIR.mkdir(exist_ok=True)

# ─── CLI args ────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument('--min', type=float, default=5.0,
                    help='Minimum weekly gain %% to count as a winner (default: 5.0)')
parser.add_argument('--top', type=int, default=30,
                    help='Max winners to show in report (default: 30)')
args = parser.parse_args()
MIN_GAIN = args.min
TOP_N    = args.top

# ─── Date range: last full trading week (Mon–Fri) ────────────────────────────
today = datetime.date.today()
last_fri = today - datetime.timedelta(days=(today.weekday() - 4) % 7 or 7)
last_mon = last_fri - datetime.timedelta(days=4)
period_label = f"{last_mon.strftime('%b %d')} – {last_fri.strftime('%b %d, %Y')}"
print(f"\n  Week: {period_label}")

# ─── Load universe ────────────────────────────────────────────────────────────
cache_file = BASE_DIR / '.universe_cache.json'
if cache_file.exists():
    with open(cache_file, encoding='utf-8') as f:
        us_tickers = json.load(f)['data']['us']
else:
    # fallback: read from latest scan CSV
    csvs = sorted(glob.glob(str(REPORTS_DIR / 'cycles_scan_*.csv')))
    us_tickers = []
    if csvs:
        with open(csvs[-1], newline='', encoding='utf-8') as f:
            us_tickers = [r['Ticker'] for r in csv.DictReader(f)]
print(f"  Universe: {len(us_tickers)} tickers")

# ─── Load latest scan CSV (our setups) ───────────────────────────────────────
csvs = sorted(glob.glob(str(REPORTS_DIR / 'cycles_scan_*.csv')))
scan_setups = {}   # ticker -> row dict
if csvs:
    latest_csv = csvs[-1]
    scan_date  = Path(latest_csv).stem.replace('cycles_scan_', '')
    with open(latest_csv, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            t = row.get('Ticker', '').strip()
            if t:
                scan_setups[t] = row
    print(f"  Scan file: {Path(latest_csv).name}  ({len(scan_setups)} setups)")

# ─── Fetch weekly performance ─────────────────────────────────────────────────
import warnings
warnings.filterwarnings('ignore')

try:
    import yfinance as yf
except ImportError:
    print("  Installing yfinance...")
    os.system(f'{sys.executable} -m pip install yfinance --break-system-packages -q')
    import yfinance as yf

print(f"\n  Fetching weekly data for {len(us_tickers)} tickers (this takes ~1-2 min)...")

start_str = (last_mon - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
end_str   = (last_fri + datetime.timedelta(days=1)).strftime('%Y-%m-%d')

# Batch download — much faster than individual calls
BATCH = 100
all_weekly = {}   # ticker -> {'pct': float, 'close': float, 'open': float}

for i in range(0, len(us_tickers), BATCH):
    batch = us_tickers[i:i+BATCH]
    try:
        df = yf.download(
            batch, start=start_str, end=end_str,
            auto_adjust=True, progress=False, threads=True
        )
        closes = df.get('Close')
        if closes is None:
            continue
        for t in batch:
            try:
                series = closes[t].dropna()
                if len(series) < 2:
                    continue
                open_px  = series.iloc[0]
                close_px = series.iloc[-1]
                pct      = (close_px / open_px - 1) * 100
                all_weekly[t] = {
                    'pct':   round(float(pct), 2),
                    'open':  round(float(open_px), 2),
                    'close': round(float(close_px), 2),
                }
            except Exception:
                pass
    except Exception as e:
        print(f"    batch {i//BATCH+1} error: {e}")
    pct_done = min(i + BATCH, len(us_tickers))
    print(f"    {pct_done}/{len(us_tickers)} done", end='\r')

print(f"\n  Got data for {len(all_weekly)} tickers")

# ─── Find winners ─────────────────────────────────────────────────────────────
winners = sorted(
    [(t, d) for t, d in all_weekly.items() if d['pct'] >= MIN_GAIN],
    key=lambda x: x[1]['pct'],
    reverse=True
)[:TOP_N]

print(f"\n  Top gainers (>={MIN_GAIN}%): {len(winners)}")

# ─── Cross-reference with scan ────────────────────────────────────────────────
caught  = []   # scanner had a setup for these
missed  = []   # scanner did NOT have a setup

for ticker, perf in winners:
    if ticker in scan_setups:
        row = scan_setups[ticker]
        caught.append({
            'ticker':    ticker,
            'pct':       perf['pct'],
            'close':     perf['close'],
            'prob':      row.get('Prob', '-'),
            'direction': row.get('Dir', row.get('Direction', '-')),
            'tl':        row.get('TrafficLight', '-'),
            'entry':     row.get('Entry', '-'),
        })
    else:
        missed.append({
            'ticker': ticker,
            'pct':    perf['pct'],
            'close':  perf['close'],
            'reason': 'Not in scan — price not near any S/R level',
        })

print(f"  CAUGHT: {len(caught)}  |  MISSED: {len(missed)}")

# ─── For MISSED tickers: quick diagnosis via yfinance RSI/distance ────────────
def diagnose_missed(ticker, close_px):
    """Return a short reason why the scanner would have skipped this ticker."""
    try:
        df = yf.download(ticker, period='3mo', interval='1d',
                         auto_adjust=True, progress=False)
        if df is None or len(df) < 20:
            return 'Not enough history'
        closes = df['Close'].squeeze()
        # RSI
        delta = closes.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss
        rsi   = float((100 - 100 / (1 + rs)).iloc[-1])
        # 52-week low/high proximity
        hi52 = float(closes.rolling(252).max().iloc[-1])
        lo52 = float(closes.rolling(252).min().iloc[-1])
        dist_hi = (hi52 - close_px) / hi52 * 100
        dist_lo = (close_px - lo52) / lo52 * 100
        reasons = []
        if rsi > 65:
            reasons.append(f'RSI {rsi:.0f} — overbought at scan time')
        if dist_hi < 5:
            reasons.append(f'Near 52w high ({dist_hi:.1f}% below) — no resistance level')
        if not reasons:
            reasons.append(f'No S/R cluster found within entry range (RSI {rsi:.0f})')
        return '; '.join(reasons)
    except Exception:
        return 'Diagnosis unavailable'

print("\n  Diagnosing missed winners...")
for i, m in enumerate(missed):
    m['reason'] = diagnose_missed(m['ticker'], m['close'])
    print(f"    [{i+1}/{len(missed)}] {m['ticker']}: {m['reason']}", end='\r')
print()

# ─── HTML Report ──────────────────────────────────────────────────────────────
def _row_caught(r):
    tl_color = {'GREEN': '#22c55e', 'YELLOW': '#f59e0b', 'RED': '#ef4444'}.get(r['tl'], '#888')
    return (
        f"<tr>"
        f"<td class='tk'>{r['ticker']}</td>"
        f"<td class='pct'>+{r['pct']:.1f}%</td>"
        f"<td>${r['close']:.2f}</td>"
        f"<td style='color:{tl_color};font-weight:700'>{r['tl']}</td>"
        f"<td>{r['direction']}</td>"
        f"<td>{r['prob']}%</td>"
        f"<td>${r['entry']}</td>"
        f"</tr>"
    )

def _row_missed(r):
    return (
        f"<tr>"
        f"<td class='tk'>{r['ticker']}</td>"
        f"<td class='pct'>+{r['pct']:.1f}%</td>"
        f"<td>${r['close']:.2f}</td>"
        f"<td colspan='4' style='color:#888;font-size:12px'>{r['reason']}</td>"
        f"</tr>"
    )

caught_rows = ''.join(_row_caught(r) for r in caught)
missed_rows = ''.join(_row_missed(r) for r in missed)

catch_rate = len(caught) / len(winners) * 100 if winners else 0

ts       = datetime.datetime.now().strftime('%Y%m%d_%H%M')
out_path = REPORTS_DIR / f'weekly_review_{ts}.html'

html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Weekly Review {period_label}</title>
<style>
  body   {{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:24px}}
  h1     {{color:#58a6ff;margin:0 0 6px}}
  h2     {{color:#8b949e;font-size:14px;margin:0 0 20px;font-weight:normal}}
  .pills {{display:flex;gap:12px;margin-bottom:28px;flex-wrap:wrap}}
  .pill  {{background:#1e293b;border-radius:20px;padding:8px 20px;font-size:15px;font-weight:700}}
  .pill.green {{border:2px solid #22c55e;color:#22c55e}}
  .pill.red   {{border:2px solid #ef4444;color:#ef4444}}
  .pill.blue  {{border:2px solid #58a6ff;color:#58a6ff}}
  .pill.gold  {{border:2px solid #f59e0b;color:#f59e0b}}
  section {{margin-bottom:32px}}
  h3     {{color:#58a6ff;margin:0 0 12px}}
  table  {{width:100%;border-collapse:collapse;background:#1e293b;border-radius:10px;overflow:hidden}}
  thead tr {{background:#0f172a}}
  th     {{padding:10px 14px;text-align:left;color:#6e7681;font-size:12px;text-transform:uppercase}}
  td     {{padding:10px 14px;font-size:13px;border-bottom:1px solid #21262d}}
  .tk    {{font-weight:700;font-size:15px;color:#e2e8f0}}
  .pct   {{font-weight:700;color:#22c55e}}
  .lesson{{background:#1a2030;border:1px solid #3b4561;border-radius:8px;
           padding:16px 20px;margin-top:20px;font-size:13px;color:#94a3b8;line-height:1.7}}
  .lesson b {{color:#e2e8f0}}
</style>
</head>
<body>
<h1>📊 Weekly Winner Review — {period_label}</h1>
<h2>Universe: {len(us_tickers)} tickers &nbsp;|&nbsp; Min gain: +{MIN_GAIN}% &nbsp;|&nbsp; Scan: {scan_date if csvs else 'N/A'}</h2>

<div class="pills">
  <div class="pill blue">🏆 Winners found: {len(winners)}</div>
  <div class="pill green">✅ CAUGHT: {len(caught)} ({catch_rate:.0f}%)</div>
  <div class="pill red">❌ MISSED: {len(missed)}</div>
</div>

<section>
  <h3>✅ Caught by scanner ({len(caught)} tickers)</h3>
  <table>
    <thead><tr>
      <th>Ticker</th><th>Week Gain</th><th>Close</th>
      <th>Traffic Light</th><th>Direction</th><th>Prob</th><th>Entry</th>
    </tr></thead>
    <tbody>{caught_rows if caught_rows else "<tr><td colspan='7' style='color:#555;text-align:center'>None caught this week</td></tr>"}</tbody>
  </table>
</section>

<section>
  <h3>❌ Missed by scanner ({len(missed)} tickers)</h3>
  <table>
    <thead><tr>
      <th>Ticker</th><th>Week Gain</th><th>Close</th>
      <th colspan="4">Why scanner missed it</th>
    </tr></thead>
    <tbody>{missed_rows if missed_rows else "<tr><td colspan='7' style='color:#555;text-align:center'>No misses — scanner caught everything!</td></tr>"}</tbody>
  </table>

  <div class="lesson">
    <b>What "missed" usually means:</b><br>
    • <b>No S/R level</b> — stock was in the middle of a range with no clear support/resistance cluster<br>
    • <b>Near 52w high</b> — breakout mode, our method focuses on retests of S/R levels, not breakouts<br>
    • <b>RSI overbought at scan time</b> — scanner filtered it out as extended<br>
    • <b>Not in universe</b> — ticker wasn't in S&P500/NASDAQ100/S&P400<br>
    <br>
    <b>Lesson:</b> if many missed stocks share the same reason, that's a signal to review that filter.
    Add frequent miss reasons to <code>learnings.json</code> for future improvement.
  </div>
</section>

<p style='color:#334155;font-size:11px;text-align:center;margin-top:32px'>
  Cycles Trading Weekly Review &mdash; Generated {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}
</p>
</body>
</html>"""

out_path.write_text(html, encoding='utf-8')
print(f"\n  Report saved: {out_path}")

import webbrowser
try:
    webbrowser.open('file:///' + str(out_path).replace('\\', '/'))
except Exception:
    pass

print(f"\n  === SUMMARY ===")
print(f"  Week:        {period_label}")
print(f"  Winners:     {len(winners)} stocks gained >={MIN_GAIN}%")
print(f"  CAUGHT:      {len(caught)} ({catch_rate:.0f}%)")
print(f"  MISSED:      {len(missed)}")
if missed:
    from collections import Counter
    reasons = Counter()
    for m in missed:
        r = m['reason']
        if 'RSI' in r and 'overbought' in r:   reasons['RSI overbought'] += 1
        elif '52w high' in r:                   reasons['Near 52w high (breakout)'] += 1
        elif 'No S/R' in r or 'S/R cluster' in r: reasons['No S/R level in range'] += 1
        else:                                   reasons['Other'] += 1
    print(f"\n  Miss reasons:")
    for reason, count in reasons.most_common():
        print(f"    {count}x  {reason}")
