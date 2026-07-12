#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ct_order_sheet.py — Colmex-ready order sheet from the latest scan.

Runs at the end of every evening scan / Sunday pipeline. Takes the latest
REPORTS/cycles_scan_*.csv, filters to setups actually placeable in the
Colmex demo account, ranks them, respects the open-position cap, and
writes REPORTS/order_sheet_*.csv + .html (also printed to stdout/log).

Selection rules (course-consistent, strict GREEN-only per Omar's choice):
  - TrafficLight GREEN only (zero red flags). YELLOWs are counted but not
    listed — check the main cycles report for the discretionary tier.
  - US-listed with a real position size (Units >= 1; non-USD rows size 0)
  - R:R >= 2 (scanner already enforces; double-checked)
  - no 'Earnings imminent (<14d)' red flag (course: avoid)
  - not already an open position in positions.json
  - slots = MAX_OPEN_POSITIONS - open positions; extras listed as ALTERNATE

NOTE: this produces a sheet for the HUMAN to place in the Colmex DEMO
account. Nothing here places orders.
"""

import ast, csv, glob, json, os, sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))

try:
    from ct_config import MAX_OPEN_POSITIONS
except Exception:
    MAX_OPEN_POSITIONS = 6


def _latest_scan_csv():
    files = sorted(glob.glob(os.path.join(_HERE, 'REPORTS', 'cycles_scan_*.csv')))
    return files[-1] if files else None


def _open_tickers():
    try:
        with open(os.path.join(_HERE, 'positions.json'), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return [p['ticker'] for p in data.get('positions', []) if not p.get('closed')]
    except Exception:
        return []


def _flags(row):
    try:
        v = ast.literal_eval(row.get('_red_flags') or '[]')
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _f(row, key, default=0.0):
    try:
        return float(row.get(key) or default)
    except (ValueError, TypeError):
        return default


def build_sheet():
    scan = _latest_scan_csv()
    if not scan:
        print('No cycles_scan CSV found in REPORTS/ — run a scan first.')
        return None

    with open(scan, encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))

    open_pos = _open_tickers()
    slots = max(0, MAX_OPEN_POSITIONS - len(open_pos))
    n_yellow = sum(1 for r in rows
                   if r.get('TrafficLight') == 'YELLOW'
                   and (r.get('Type') or 'STOCK').upper() == 'STOCK')

    candidates = []
    for r in rows:
        # Colmex demo trades US-listed stocks/ETFs only — the scan's Type
        # column marks those 'STOCK' (crypto/commodity/intl are excluded;
        # crypto is USD-sized so a Units check alone would let it through).
        if (r.get('Type') or 'STOCK').upper() != 'STOCK':
            continue
        tl = r.get('TrafficLight', '')
        if tl != 'GREEN':
            continue
        qty = int(_f(r, 'Units'))
        if qty < 1:
            continue                      # non-USD listing or too expensive
        if _f(r, 'R:R') < 2.0:
            continue
        if r['Ticker'] in open_pos:
            continue
        flags = _flags(r)
        if any('Earnings imminent' in fl for fl in flags):
            continue                      # course: no entry <14d to earnings
        side = 'SHORT' if 'SHORT' in r.get('Dir', '') else 'LONG'
        entry, stop = _f(r, 'Entry'), _f(r, 'Stop')
        tp1 = _f(r, 'Target')
        gann = _f(r, 'GannTarget')
        beyond = (gann > tp1) if side == 'LONG' else (0 < gann < tp1)
        tp2 = gann if beyond else tp1
        note_bits = [fl.split(' (')[0] for fl in flags]
        if side == 'SHORT' and (r.get('SSR_Risk') or '').lower() in ('true', '1', 'yes'):
            note_bits.append('SSR risk')
        if side == 'SHORT' and (r.get('SqueezeRisk') or '').strip() not in ('', 'None', 'False'):
            note_bits.append('squeeze risk')
        candidates.append({
            'Ticker': r['Ticker'], 'Side': side, 'TL': tl,
            'Prob': int(_f(r, 'Prob')), 'RR': _f(r, 'R:R'),
            'Qty': qty, 'Entry': entry, 'Stop': stop,
            'TP1': tp1, 'TP2': tp2,
            'PosPct': r.get('PosPct', ''), 'Horizon': r.get('HorizonRange', ''),
            'Note': '; '.join(note_bits) or '-',
        })

    candidates.sort(key=lambda c: (c['TL'] != 'GREEN', -c['Prob'], -c['RR']))
    for i, c in enumerate(candidates):
        c['Rank'] = 'PRIMARY' if i < slots else 'ALTERNATE'
    sheet = candidates[:slots + 3]

    ts = datetime.now().strftime('%Y%m%d_%H%M')
    base = os.path.join(_HERE, 'REPORTS', f'order_sheet_{ts}')

    # ── CSV ──
    cols = ['Rank', 'Ticker', 'Side', 'TL', 'Prob', 'RR', 'Qty',
            'Entry', 'Stop', 'TP1', 'TP2', 'PosPct', 'Horizon', 'Note', 'RegisterCmd']
    for c in sheet:
        c['RegisterCmd'] = (
            f"python cycles_trading_scanner.py add {c['Ticker']} {c['Side']} "
            f"{c['Entry']} {c['Stop']} {c['TP1']} {c['TP2']} {c['TP2']} "
            f"{c['Qty']} \"demo - order sheet {ts}\"")
    with open(base + '.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(sheet)

    # ── HTML ──
    trs = ''
    for c in sheet:
        col = '#1a7f37' if c['TL'] == 'GREEN' else '#b58900'
        dim = ' opacity:0.65;' if c['Rank'] == 'ALTERNATE' else ''
        trs += (f"<tr style='{dim}'><td>{c['Rank']}</td>"
                f"<td><b>{c['Ticker']}</b></td><td>{c['Side']}</td>"
                f"<td style='color:{col};font-weight:bold'>{c['TL']}</td>"
                f"<td>{c['Prob']}</td><td>{c['RR']:.2f}</td><td>{c['Qty']}</td>"
                f"<td>${c['Entry']:,.2f}</td><td>${c['Stop']:,.2f}</td>"
                f"<td>${c['TP1']:,.2f}</td><td>${c['TP2']:,.2f}</td>"
                f"<td>{c['Horizon']}</td><td>{c['Note']}</td></tr>")
    html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Colmex Order Sheet {ts}</title></head>
<body style='font-family:Segoe UI,Arial,sans-serif;margin:24px'>
<h2>Colmex Demo — Order Sheet <small>({ts})</small></h2>
<p>Open positions: <b>{len(open_pos)}</b> ({', '.join(open_pos) or 'none'})
 &nbsp;|&nbsp; Free slots: <b>{slots}</b> of {MAX_OPEN_POSITIONS}
 &nbsp;|&nbsp; GREEN-only policy — {n_yellow} YELLOW setup(s) excluded
 (see the cycles report if you want the discretionary tier)</p>
<p><b>How to place (per row):</b> LONG &rarr; Buy Stop-Limit at Entry with
attached S/L (Stop) and T/P (TP1). SHORT &rarr; Sell Stop-Limit at Entry,
same bracket. After the order FILLS, register it with the RegisterCmd from
the CSV so the watch checker manages it.</p>
<table border='1' cellspacing='0' cellpadding='6' style='border-collapse:collapse'>
<tr style='background:#f0f0f0'><th>Rank</th><th>Ticker</th><th>Side</th>
<th>TL</th><th>Prob</th><th>R:R</th><th>Qty</th><th>Entry</th><th>Stop</th>
<th>TP1</th><th>TP2</th><th>Horizon</th><th>Notes / flags</th></tr>
{trs}</table>
<p style='color:#888;margin-top:16px'>Generated from {os.path.basename(scan)}.
Demo account only — not financial advice.</p></body></html>"""
    with open(base + '.html', 'w', encoding='utf-8') as f:
        f.write(html)

    # ── stdout / log ──
    print(f'\n===== COLMEX ORDER SHEET ({ts}) =====')
    print(f'Open: {len(open_pos)} ({", ".join(open_pos) or "none"}) | free slots: {slots}')
    for c in sheet:
        print(f"  [{c['Rank']:9}] {c['Ticker']:6} {c['Side']:5} {c['TL']:6} "
              f"prob {c['Prob']:>3}  R:R {c['RR']:>5.2f}  qty {c['Qty']:>4}  "
              f"entry {c['Entry']:>9.2f}  stop {c['Stop']:>9.2f}  tp {c['TP1']:>9.2f}"
              f"  | {c['Note']}")
    if not sheet:
        print(f'  (no GREEN setups today — {n_yellow} YELLOW excluded by policy)')
    print(f'Sheet: {base}.html')
    return base + '.html'


if __name__ == '__main__':
    build_sheet()
