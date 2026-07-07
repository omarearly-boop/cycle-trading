#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ct_watch_claude.py -- Watch checker for Claude scheduled tasks.
Outputs GREEN signals as JSON to stdout.
Updates last_alerted in watch_alerts.json.

Usage: python ct_watch_claude.py
"""
import sys
import os
import json
import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# Load .env
env_file = BASE_DIR / '.env'
if env_file.exists():
    for line in env_file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())

WATCH_FILE  = BASE_DIR / 'watch_alerts.json'
_INTL_SUFFIXES = ('.L', '.DE', '.PA', '.T', '.AX', '.TO', '.SW', '.NS', '.HK', '.F')

def load():
    if WATCH_FILE.exists():
        try:
            return json.loads(WATCH_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'email': '', 'tickers': []}

def save(data):
    WATCH_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def get_tl_color(setup):
    prob      = setup.get('Prob', 0)
    earn_warn = setup.get('Earn') == 'SOON!'
    direction = 'LONG' if 'LONG' in setup.get('Dir', '') else 'SHORT'
    monthly   = setup.get('MonthlyTrend')
    if earn_warn:                              return 'RED'
    if monthly == 'SHORT' and direction == 'LONG':  return 'RED'
    if monthly == 'LONG'  and direction == 'SHORT': return 'RED'
    if prob >= 65: return 'GREEN'
    if prob >= 50: return 'YELLOW'
    return 'RED'

def analyze(entry):
    ticker    = entry['ticker']
    direction = entry.get('direction', 'LONG')
    is_crypto    = ticker.endswith('-USD')
    is_israel    = ticker.endswith('.TA')
    is_intl      = any(ticker.endswith(s) for s in _INTL_SUFFIXES)
    is_commodity = ticker.endswith('=F')
    asset_type   = (
        'CRYPTO'    if is_crypto    else
        'ISRAEL'    if is_israel    else
        'INTL'      if is_intl      else
        'COMMODITY' if is_commodity else
        'STOCK'
    )
    try:
        from ct_analysis import _fetch_market_data, _detect_setup
        from ct_config   import MAX_DIST_STOCK
        market = _fetch_market_data(
            ticker,
            is_crypto=is_crypto, is_commodity=is_commodity,
            is_israel=is_israel, is_intl=is_intl,
        )
        if not market:
            return None
        setup = _detect_setup(
            ticker, 100000, market, is_crypto, asset_type,
            MAX_DIST_STOCK, direction,
            is_commodity=is_commodity, is_israel=is_israel, is_intl=is_intl,
        )
        return setup
    except Exception as e:
        print(f"ERROR {ticker}: {e}", file=sys.stderr)
        return None

def main():
    data    = load()
    tickers = data.get('tickers', [])
    today   = datetime.date.today().isoformat()
    green   = []
    updated = False

    for entry in tickers:
        if entry.get('last_alerted') == today:
            continue
        setup = analyze(entry)
        if not setup:
            continue
        tl = get_tl_color(setup)
        if tl != 'GREEN':
            continue

        factors_lines = []
        for label, delta, explain in setup.get('_pfacts', []):
            sign = '+' if delta >= 0 else ''
            factors_lines.append(f"{label}: {sign}{delta} | {explain}")

        green.append({
            'ticker'   : entry['ticker'],
            'direction': entry.get('direction', 'LONG'),
            'note'     : entry.get('note', ''),
            'added'    : entry.get('added', ''),
            'prob'     : setup.get('Prob', 0),
            'price'    : setup.get('Price', 0),
            'entry'    : setup.get('Entry', 0),
            'stop'     : setup.get('Stop', 0),
            'target'   : setup.get('Target', 0),
            'rr'       : setup.get('R:R', 0),
            'horizon'  : setup.get('HorizonLabel', '-'),
            'monthly'  : setup.get('MonthlyTrend', '-'),
            'earn'     : setup.get('Earn', '-'),
            'factors'  : '\n'.join(factors_lines),
        })
        entry['last_alerted'] = today
        entry['alert_count']  = entry.get('alert_count', 0) + 1
        updated = True

    if updated:
        save(data)

    print(json.dumps(green, ensure_ascii=False))

if __name__ == '__main__':
    main()
