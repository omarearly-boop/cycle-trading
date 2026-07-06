#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ct_watch_manager.py -- Watchlist manager for Cycles Trading alerts.

Usage:
  python ct_watch_manager.py add  TICKER LONG|SHORT "optional note"
  python ct_watch_manager.py remove TICKER
  python ct_watch_manager.py list
  python ct_watch_manager.py clear

The watchlist is stored in watch_alerts.json.
Entries are NEVER deleted automatically -- only by 'remove' command.
"""

import sys
import json
import datetime
from pathlib import Path

BASE_DIR   = Path(__file__).parent
WATCH_FILE = BASE_DIR / 'watch_alerts.json'


def load():
    if WATCH_FILE.exists():
        try:
            return json.loads(WATCH_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'email': 'omarearly@gmail.com', 'tickers': []}


def save(data):
    data['last_updated'] = datetime.datetime.now().isoformat()
    WATCH_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


def add_ticker(ticker: str, direction: str, note: str = ''):
    ticker    = ticker.upper().strip()
    direction = direction.upper().strip()
    if direction not in ('LONG', 'SHORT'):
        print(f"  ERROR: direction must be LONG or SHORT, got '{direction}'")
        return

    data    = load()
    tickers = data.get('tickers', [])

    # Check if already in list
    for entry in tickers:
        if entry['ticker'] == ticker:
            print(f"  {ticker} already in watchlist (added {entry['added']})")
            return

    entry = {
        'ticker':       ticker,
        'direction':    direction,
        'note':         note,
        'added':        datetime.date.today().isoformat(),
        'last_alerted': None,
        'alert_count':  0,
    }
    tickers.append(entry)
    data['tickers'] = tickers
    save(data)
    print(f"  Added {ticker} ({direction}) to watchlist")
    if note:
        print(f"  Note: {note}")


def remove_ticker(ticker: str):
    ticker  = ticker.upper().strip()
    data    = load()
    before  = len(data.get('tickers', []))
    data['tickers'] = [e for e in data.get('tickers', []) if e['ticker'] != ticker]
    after   = len(data['tickers'])
    if before == after:
        print(f"  {ticker} not found in watchlist")
    else:
        save(data)
        print(f"  Removed {ticker} from watchlist")


def list_tickers():
    data    = load()
    tickers = data.get('tickers', [])
    if not tickers:
        print("  Watchlist is empty.")
        return
    print(f"\n  Watchlist ({len(tickers)} tickers)  -- alerts to: {data.get('email','?')}")
    print("  " + "-" * 60)
    for e in tickers:
        alerted = e.get('last_alerted') or 'never'
        count   = e.get('alert_count', 0)
        note    = f"  | {e['note']}" if e.get('note') else ''
        print(f"  {e['ticker']:10s}  {e['direction']:5s}  added:{e['added']}  alerted:{alerted} ({count}x){note}")
    print()


def clear_all():
    data = load()
    n    = len(data.get('tickers', []))
    data['tickers'] = []
    save(data)
    print(f"  Cleared {n} tickers from watchlist")


if __name__ == '__main__':
    args = sys.argv[1:]

    if not args or args[0] == 'list':
        list_tickers()

    elif args[0] == 'add' and len(args) >= 3:
        note = ' '.join(args[3:]) if len(args) > 3 else ''
        add_ticker(args[1], args[2], note)

    elif args[0] == 'remove' and len(args) >= 2:
        remove_ticker(args[1])

    elif args[0] == 'clear':
        clear_all()

    else:
        print(__doc__)


# ---------------------------------------------------------------------------
#  Auto-add from scanner (called after each setup is detected)
# ---------------------------------------------------------------------------
def _tl_color(setup: dict) -> str:
    """Mirror of ct_watch_checker.get_tl_color() -- kept here to avoid circular import."""
    prob      = setup.get('Prob', 0)
    earn_warn = setup.get('Earn') == 'SOON!'
    direction = 'LONG' if 'LONG' in setup.get('Dir', '') else 'SHORT'
    monthly   = setup.get('MonthlyTrend')

    if earn_warn:
        return 'RED'
    if monthly == 'SHORT' and direction == 'LONG':
        return 'RED'
    if monthly == 'LONG' and direction == 'SHORT':
        return 'RED'
    if prob >= 65:
        return 'GREEN'
    if prob >= 50:
        return 'YELLOW'
    return 'RED'


def auto_add_to_watchlist(setup: dict) -> bool:
    """
    Called by the scanner after each setup is detected.
    Adds the ticker to watch_alerts.json when TL is YELLOW or GREEN.

    Rules:
      - YELLOW (Prob 50-64): add -- will get email alert when it turns GREEN
      - GREEN  (Prob 65+):   add -- for daily monitoring after the weekly scan
      - RED:                 skip
      - Already in list:     skip (never overwrite user's note or added date)

    Returns True if a new entry was added.
    """
    tl = _tl_color(setup)
    if tl == 'RED':
        return False

    ticker    = setup.get('Ticker', '').upper().strip()
    direction = 'LONG' if 'LONG' in setup.get('Dir', '') else 'SHORT'
    prob      = setup.get('Prob', 0)
    if not ticker:
        return False

    data    = load()
    tickers = data.get('tickers', [])

    # Already tracked -- don't touch it
    for entry in tickers:
        if entry['ticker'] == ticker and entry.get('direction') == direction:
            return False

    note = f"auto: {tl} Prob {prob}% (scanner {datetime.date.today().isoformat()})"
    entry = {
        'ticker':       ticker,
        'direction':    direction,
        'note':         note,
        'added':        datetime.date.today().isoformat(),
        'last_alerted': None,
        'alert_count':  0,
        'auto':         True,
    }
    tickers.append(entry)
    data['tickers'] = tickers
    save(data)
    print(f"  [watch] Added {ticker} ({direction}) TL={tl} Prob={prob}%")
    return True
