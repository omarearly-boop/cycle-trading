#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ct_calibration.py — Signal calibration tracker.

Answers the question that decides real-money sizing: do high-probability
setups actually win more often? Collects every scan signal, tracks whether
price hit Target-1 or the Stop first, and reports win rates bucketed by
probability, traffic light, and entry method.

Data flow:
  ingest    REPORTS/cycles_scan_*.csv  →  signal_history.json
            (run before the 48h report cleanup deletes the CSVs)
  evaluate  fetch daily bars for unresolved signals; resolve WIN (T1 first),
            LOSS (stop first — same-bar ties count as LOSS, conservative),
            or EXPIRED after MAX_WEEKS with the mark-to-market R multiple
  report    REPORTS/calibration_report_<ts>.html + console summary

Usage:
  python ct_calibration.py            # ingest + evaluate + report
  python ct_calibration.py ingest     # ingest only (no network)
  python ct_calibration.py evaluate   # evaluate open signals only
  python ct_calibration.py report     # report only (no network)
"""

import csv, json, sys, datetime, warnings
from pathlib import Path

warnings.filterwarnings('ignore')

BASE_DIR     = Path(__file__).parent
REPORTS_DIR  = BASE_DIR / 'REPORTS'
HISTORY_FILE = BASE_DIR / 'signal_history.json'

MAX_WEEKS   = 12        # expire unresolved signals after 12 weeks
MIN_SAMPLE  = 20        # buckets below this get a low-confidence warning

PROB_BUCKETS = [(0, 64), (65, 69), (70, 74), (75, 79), (80, 100)]


# ---------------------------------------------------------------------------
#  History I/O
# ---------------------------------------------------------------------------

def _load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'signals': {}}


def _save_history(hist: dict) -> None:
    tmp = HISTORY_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(hist, indent=1, ensure_ascii=False), encoding='utf-8')
    import os
    os.replace(tmp, HISTORY_FILE)


# ---------------------------------------------------------------------------
#  Ingest — scan CSVs → history
# ---------------------------------------------------------------------------

def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def ingest() -> int:
    hist    = _load_history()
    signals = hist['signals']
    added   = 0

    for csv_path in sorted(REPORTS_DIR.glob('cycles_scan_*.csv')):
        # filename: cycles_scan_YYYYMMDD_HHMM.csv
        stamp = csv_path.stem.replace('cycles_scan_', '')
        try:
            scan_dt = datetime.datetime.strptime(stamp, '%Y%m%d_%H%M')
        except ValueError:
            continue
        scan_date = scan_dt.date().isoformat()

        try:
            rows = list(csv.DictReader(open(csv_path, encoding='utf-8')))
        except Exception:
            continue

        for r in rows:
            ticker = (r.get('Ticker') or '').strip()
            raw    = (r.get('_raw') or ticker).strip()   # _raw kept in CSV since calibration patch
            dirn   = 'LONG' if 'LONG' in (r.get('Dir') or '') else 'SHORT'
            if not ticker:
                continue
            key = f'{ticker}|{dirn}|{scan_date}'
            prev = signals.get(key)
            # keep the LATEST scan of the day for the same ticker+direction
            if prev and prev.get('scan_ts', '') >= stamp:
                continue
            entry  = _f(r.get('Entry'));  stop = _f(r.get('Stop'))
            target = _f(r.get('Target'))
            if not entry or not stop or not target or entry == stop:
                continue
            sig = {
                'ticker':       ticker,
                'raw':          raw,
                'dir':          dirn,
                'scan_ts':      stamp,
                'scan_date':    scan_date,
                'entry':        entry,
                'stop':         stop,
                'target':       target,
                'rr':           _f(r.get('R:R')),
                'prob':         int(_f(r.get('Prob'), 0) or 0),
                'prob_raw':     int(_f(r.get('ProbRaw'), 0) or 0) or None,
                'tl':           r.get('TrafficLight', ''),
                'entry_method': r.get('EntryMethod', ''),
                'type':         r.get('Type', ''),
                'watchlist':    str(r.get('IsWatchlist', '')).lower() == 'true',
                'status':       (prev or {}).get('status', 'OPEN'),
            }
            if prev:  # carry over any resolution from the replaced record
                for k in ('resolved_date', 'r_multiple', 'weeks_held'):
                    if k in prev:
                        sig[k] = prev[k]
            signals[key] = sig
            if not prev:
                added += 1

    _save_history(hist)
    print(f'  ingest: {added} new signal(s), {len(signals)} total in history')
    return added


# ---------------------------------------------------------------------------
#  Evaluate — resolve open signals against later price action
# ---------------------------------------------------------------------------

def evaluate() -> int:
    hist     = _load_history()
    signals  = hist['signals']
    open_sigs = {k: s for k, s in signals.items() if s.get('status') == 'OPEN'}
    if not open_sigs:
        print('  evaluate: no open signals')
        return 0

    try:
        import yfinance as yf
        from ct_market_data import yf_history
    except Exception as e:
        print(f'  evaluate: yfinance unavailable ({e}) — skipped')
        return 0

    today    = datetime.date.today()
    resolved = 0
    # group by raw symbol so each ticker is fetched once
    by_symbol: dict = {}
    for k, s in open_sigs.items():
        by_symbol.setdefault(s.get('raw') or s['ticker'], []).append((k, s))

    for symbol, items in by_symbol.items():
        earliest = min(s['scan_date'] for _, s in items)
        try:
            df = yf_history(yf.Ticker(symbol), start=earliest, interval='1d',
                            auto_adjust=True, raise_errors=False)
            if df is None or df.empty:
                continue
            df.columns = [c.capitalize() for c in df.columns]
        except Exception:
            continue

        for key, s in items:
            scan_d = datetime.date.fromisoformat(s['scan_date'])
            try:
                bars = df[df.index.date > scan_d]
            except Exception:
                continue
            if bars is None or len(bars) == 0:
                continue
            is_long = s['dir'] == 'LONG'
            entry, stop, target = s['entry'], s['stop'], s['target']
            risk = abs(entry - stop) or 1e-9
            status = 'OPEN'; r_mult = None; res_date = None

            for ts, row in bars.iterrows():
                hi, lo = float(row['High']), float(row['Low'])
                hit_stop   = lo <= stop  if is_long else hi >= stop
                hit_target = hi >= target if is_long else lo <= target
                if hit_stop:                      # ties count as LOSS (conservative)
                    status, r_mult = 'LOSS', -1.0
                    res_date = ts.date().isoformat(); break
                if hit_target:
                    r_mult = (target - entry) / risk if is_long else (entry - target) / risk
                    status = 'WIN'
                    res_date = ts.date().isoformat(); break

            weeks = (today - scan_d).days / 7.0
            if status == 'OPEN' and weeks >= MAX_WEEKS:
                last = float(bars['Close'].iloc[-1])
                r_mult = (last - entry) / risk if is_long else (entry - last) / risk
                status, res_date = 'EXPIRED', today.isoformat()

            if status != 'OPEN':
                s['status']        = status
                s['r_multiple']    = round(r_mult, 2)
                s['resolved_date'] = res_date
                s['weeks_held']    = round(weeks, 1)
                resolved += 1

    _save_history(hist)
    still_open = sum(1 for s in signals.values() if s.get('status') == 'OPEN')
    print(f'  evaluate: resolved {resolved} signal(s); {still_open} still open')
    return resolved


# ---------------------------------------------------------------------------
#  Report
# ---------------------------------------------------------------------------

def _bucket_stats(sigs: list) -> dict:
    wins  = [s for s in sigs if s.get('status') == 'WIN']
    loss  = [s for s in sigs if s.get('status') == 'LOSS']
    expd  = [s for s in sigs if s.get('status') == 'EXPIRED']
    opn   = [s for s in sigs if s.get('status') == 'OPEN']
    decided = len(wins) + len(loss)
    win_rate = round(len(wins) / decided * 100, 1) if decided else None
    rs = [s['r_multiple'] for s in sigs if s.get('r_multiple') is not None]
    avg_r = round(sum(rs) / len(rs), 2) if rs else None
    return {'n': len(sigs), 'wins': len(wins), 'losses': len(loss),
            'expired': len(expd), 'open': len(opn),
            'win_rate': win_rate, 'avg_r': avg_r}


def _row_html(label: str, st: dict, hint: str = '') -> str:
    wr  = f"{st['win_rate']}%" if st['win_rate'] is not None else '—'
    ar  = st['avg_r'] if st['avg_r'] is not None else '—'
    low = ' <span style="color:#d29922;font-size:10px">(low sample)</span>' \
          if 0 < (st['wins'] + st['losses']) < MIN_SAMPLE else ''
    wr_c = '#8b949e'
    if st['win_rate'] is not None:
        wr_c = '#3fb950' if st['win_rate'] >= 55 else ('#d29922' if st['win_rate'] >= 45 else '#f85149')
    return (f'<tr><td>{label}{low}</td><td>{st["n"]}</td>'
            f'<td style="color:#3fb950">{st["wins"]}</td>'
            f'<td style="color:#f85149">{st["losses"]}</td>'
            f'<td>{st["expired"]}</td><td>{st["open"]}</td>'
            f'<td style="color:{wr_c};font-weight:700">{wr}</td>'
            f'<td>{ar}</td>'
            f'<td style="color:#6e7681;font-size:11px">{hint}</td></tr>')


def report() -> str:
    hist = _load_history()
    sigs = list(hist['signals'].values())
    if not sigs:
        print('  report: no signals in history yet — run a scan, then ingest')
        return ''

    sections = []

    # -- probability calibration (the headline table) --
    rows = ''
    for lo, hi in PROB_BUCKETS:
        bucket = [s for s in sigs if lo <= s.get('prob', 0) <= hi]
        if bucket:
            hint = 'watchlist zone' if hi < 65 else ('GREEN-eligible' if lo >= 70 else '')
            rows += _row_html(f'Prob {lo}–{hi}', _bucket_stats(bucket), hint)
    sections.append(('Probability calibration '
                     '(does a higher score actually win more often?)', rows))

    # -- traffic light --
    rows = ''
    for tl in ('GREEN', 'YELLOW', 'RED'):
        bucket = [s for s in sigs if s.get('tl') == tl]
        if bucket:
            rows += _row_html(tl, _bucket_stats(bucket))
    sections.append(('Traffic light', rows))

    # -- entry method (lesson 20) --
    rows = ''
    for m in ('MORE_SOLID', 'SOLID', 'AGGRESSIVE'):
        bucket = [s for s in sigs if s.get('entry_method') == m]
        if bucket:
            rows += _row_html(m, _bucket_stats(bucket))
    sections.append(('Entry method (lesson 20 predicts MORE_SOLID > SOLID > AGGRESSIVE)', rows))

    # -- direction / type --
    rows = ''
    for d in ('LONG', 'SHORT'):
        bucket = [s for s in sigs if s.get('dir') == d]
        if bucket:
            rows += _row_html(d, _bucket_stats(bucket))
    for t in sorted({s.get('type', '') for s in sigs if s.get('type')}):
        bucket = [s for s in sigs if s.get('type') == t]
        rows += _row_html(t, _bucket_stats(bucket))
    sections.append(('Direction & asset type', rows))

    overall = _bucket_stats(sigs)
    decided = overall['wins'] + overall['losses']
    verdict = ('Not enough resolved signals yet — keep collecting. '
               f'Need ~{MIN_SAMPLE}+ decided per bucket before trusting the numbers.'
               if decided < MIN_SAMPLE else
               'Compare the win-rate column against the probability column: '
               'if higher buckets do not win more often, the scoring needs re-weighting '
               'before sizing up real money.')

    thead = ('<tr><th>Bucket</th><th>Signals</th><th>Wins</th><th>Losses</th>'
             '<th>Expired</th><th>Open</th><th>Win rate</th><th>Avg R</th><th></th></tr>')
    body = ''
    for title, rows in sections:
        if rows:
            body += (f'<h3>{title}</h3><table><thead>{thead}</thead>'
                     f'<tbody>{rows}</tbody></table>')

    ts   = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    today = datetime.date.today().strftime('%d/%m/%Y')
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Calibration Report {today}</title>
<style>
 body{{font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:24px}}
 h1{{color:#58a6ff;margin:0 0 4px}} h3{{color:#8b949e;margin:22px 0 8px;font-size:14px}}
 .sub{{color:#8b949e;font-size:13px;margin:0 0 16px}}
 .verdict{{background:#1e293b;border-left:4px solid #58a6ff;padding:12px 16px;
          border-radius:6px;font-size:13px;color:#cbd5e1;line-height:1.7;margin-bottom:8px}}
 table{{width:100%;border-collapse:collapse;background:#1e293b;border-radius:10px;overflow:hidden}}
 thead tr{{background:#0f172a}}
 th{{padding:8px 12px;text-align:left;color:#6e7681;font-size:11px;text-transform:uppercase}}
 td{{padding:8px 12px;font-size:13px;border-bottom:1px solid #21262d}}
 tr:hover>td{{background:#ffffff08}}
</style></head><body>
<h1>&#128200; Signal Calibration — {today}</h1>
<p class="sub">{overall['n']} signals tracked · {overall['wins']}W / {overall['losses']}L
 / {overall['expired']} expired / {overall['open']} open ·
 overall win rate {overall['win_rate'] if overall['win_rate'] is not None else '—'}%
 · avg R {overall['avg_r'] if overall['avg_r'] is not None else '—'}</p>
<div class="verdict"><b>How to read this:</b> {verdict}<br>
WIN = Target-1 hit before stop · LOSS = stop hit first (same-bar ties count as losses)
· EXPIRED = {MAX_WEEKS} weeks passed, scored at mark-to-market R.</div>
{body}
<p style="color:#334155;font-size:11px;text-align:center;margin-top:24px">
 Cycles Trading Calibration &mdash; generated {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}
 &mdash; signal_history.json holds the raw records.</p>
</body></html>"""

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f'calibration_report_{ts}.html'
    out.write_text(html, encoding='utf-8')

    wr = overall['win_rate']
    print(f"  report: {out.name}  ({overall['n']} signals, "
          f"win rate {wr if wr is not None else '—'}%)")
    return str(out)


# ---------------------------------------------------------------------------

if __name__ == '__main__':
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    print('\n  Cycles Trading — Signal Calibration')
    print('  ' + '=' * 45)
    if arg in ('', 'ingest'):
        ingest()
    if arg in ('', 'evaluate'):
        evaluate()
    if arg in ('', 'report'):
        report()
    print()
