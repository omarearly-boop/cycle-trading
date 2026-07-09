#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ct_positions.py — Position manager: CRUD + stop-advancement rules."""
import sys, time, warnings, os, logging
from datetime import datetime
import json
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
from ct_config import (
    POSITIONS_FILE, PM_SWING_LOOKBACK, PM_MOMENTUM_WEEKS, PM_STOP_BUFFER,
)
from ct_indicators import rsi, atr, _pm_pivot_lows, _pm_pivot_highs

# ══════════════════════════════════════════════════════════════
#  POSITION MANAGER — persistence
# ══════════════════════════════════════════════════════════════

def _pm_load() -> list:
    """Load open positions from positions.json."""
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f).get('positions', [])
    except Exception:
        return []

def _pm_save(positions: list):
    """Persist positions list to positions.json."""
    with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(
            {'positions': positions, '_updated': datetime.now().strftime('%Y-%m-%d')},
            f, indent=2, ensure_ascii=False
        )

def pm_add(ticker: str, direction: str, entry: float,
           stop: float, tp1: float, tp2: float, tp3: float,
           units: int, notes: str = '') -> dict:
    """
    Add a new open position.
    Called after a scanner entry is confirmed and filled.
    """
    direction = direction.upper()
    positions = _pm_load()
    pos = {
        'id':           f"{ticker.upper()}_{datetime.now().strftime('%Y%m%d%H%M')}",
        'ticker':       ticker.upper(),
        'direction':    direction,
        'entry':        round(float(entry), 4),
        'stop':         round(float(stop),  4),
        'tp1':          round(float(tp1),   4),
        'tp2':          round(float(tp2),   4),
        'tp3':          round(float(tp3),   4),
        'units':        int(units),
        'entry_date':   datetime.now().strftime('%Y-%m-%d'),
        'tp1_hit':      False,
        'tp2_hit':      False,
        'tp3_hit':      False,
        'closed':       False,
        'stop_history': [],
        'notes':        notes,
    }
    positions.append(pos)
    _pm_save(positions)
    print(f'  ✅ Position added: {pos["id"]}  {direction} {ticker.upper()} @ {entry}')
    return pos

def pm_close(pos_id: str, exit_price: float = None):
    """Mark a position as closed (stop hit or manual exit)."""
    positions = _pm_load()
    for p in positions:
        if p['id'] == pos_id:
            p['closed']     = True
            p['close_date'] = datetime.now().strftime('%Y-%m-%d')
            if exit_price is not None:
                p['exit_price'] = round(float(exit_price), 4)
            _pm_save(positions)
            print(f'  🔒 Closed: {pos_id}')
            return
    print(f'  ⚠ Position not found: {pos_id}')



# ══════════════════════════════════════════════════════════════
#  POSITION MANAGER — the two stop advancement rules
# ══════════════════════════════════════════════════════════════

def pm_rule1_swing(pos: dict, df: pd.DataFrame) -> dict:
    """
    Rule 1 — Swing Low / Swing High.
    LONG : advance stop when a new confirmed weekly swing low forms ABOVE the current stop.
           New stop = swing_low * (1 - PM_STOP_BUFFER).
    SHORT: advance stop when a new confirmed weekly swing high forms BELOW the current stop.
           New stop = swing_high * (1 + PM_STOP_BUFFER).
    Returns: {'advance': bool, 'new_stop': float|None, 'reason': str}
    """
    current_stop = pos['stop']
    direction    = pos['direction']

    if direction == 'LONG':
        pivots = _pm_pivot_lows(df)
        valid  = [p for (_, p) in pivots if p > current_stop]
        if not valid:
            return {'advance': False, 'new_stop': None,
                    'reason': 'Rule 1: no confirmed swing low above current stop — wait'}
        swing_price = valid[-1]   # most recent
        new_stop    = round(swing_price * (1 - PM_STOP_BUFFER), 2)
        if new_stop <= current_stop:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 1: swing low {swing_price:.2f} found but buffer too small'}
        return {'advance': True, 'new_stop': new_stop, 'rule': 'SWING_LOW',
                'reason': f'Rule 1 ✅ swing low {swing_price:.2f} → new stop {new_stop:.2f}'}

    else:  # SHORT
        pivots = _pm_pivot_highs(df)
        valid  = [p for (_, p) in pivots if p < current_stop]
        if not valid:
            return {'advance': False, 'new_stop': None,
                    'reason': 'Rule 1: no confirmed swing high below current stop — wait'}
        swing_price = valid[-1]
        new_stop    = round(swing_price * (1 + PM_STOP_BUFFER), 2)
        if new_stop >= current_stop:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 1: swing high {swing_price:.2f} found but buffer too small'}
        return {'advance': True, 'new_stop': new_stop, 'rule': 'SWING_HIGH',
                'reason': f'Rule 1 ✅ swing high {swing_price:.2f} → new stop {new_stop:.2f}'}


def pm_rule2_momentum(pos: dict, df: pd.DataFrame,
                      n_weeks: int = PM_MOMENTUM_WEEKS) -> dict:
    """
    Rule 2 — Momentum Principle.
    LONG : N consecutive weekly closes each higher than the previous.
           New stop = lowest Low of those N weeks * (1 - PM_STOP_BUFFER).
    SHORT: N consecutive weekly closes each lower than the previous.
           New stop = highest High of those N weeks * (1 + PM_STOP_BUFFER).
    Uses only confirmed (closed) candles — excludes current open week.
    Returns: {'advance': bool, 'new_stop': float|None, 'reason': str}
    """
    current_stop = pos['stop']
    direction    = pos['direction']
    closes = df['Close'].values[:-1]   # confirmed closed weeks only
    lows   = df['Low'].values[:-1]
    highs  = df['High'].values[:-1]

    if len(closes) < n_weeks + 1:
        return {'advance': False, 'new_stop': None,
                'reason': f'Rule 2: need {n_weeks+1} closed weeks, have {len(closes)}'}

    recent = closes[-(n_weeks + 1):]   # n_weeks comparisons need n_weeks+1 values

    if direction == 'LONG':
        all_higher = all(recent[i] > recent[i-1] for i in range(1, len(recent)))
        if not all_higher:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 2: no {n_weeks} consecutive higher closes yet'}
        momentum_low = float(min(lows[-n_weeks:]))
        new_stop = round(momentum_low * (1 - PM_STOP_BUFFER), 2)
        if new_stop <= current_stop:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 2: {n_weeks}-week momentum but stop not improved'}
        return {'advance': True, 'new_stop': new_stop, 'rule': 'MOMENTUM',
                'reason': f'Rule 2 ✅ {n_weeks}-week momentum → new stop {new_stop:.2f}'}

    else:  # SHORT
        all_lower = all(recent[i] < recent[i-1] for i in range(1, len(recent)))
        if not all_lower:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 2: no {n_weeks} consecutive lower closes yet'}
        momentum_high = float(max(highs[-n_weeks:]))
        new_stop = round(momentum_high * (1 + PM_STOP_BUFFER), 2)
        if new_stop >= current_stop:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 2: {n_weeks}-week momentum but stop not improved'}
        return {'advance': True, 'new_stop': new_stop, 'rule': 'MOMENTUM',
                'reason': f'Rule 2 ✅ {n_weeks}-week momentum → new stop {new_stop:.2f}'}


def pm_rule3_tp_trail(pos: dict) -> dict:
    """
    Rule 3 -- TP-Based Trailing Stop (course Gap #8).
    After TP1 hit: advance stop to entry (breakeven).
    After TP2 hit: advance stop to TP1 level.
    After TP3 hit: advance stop to TP2 level.
    Only ever advances (never retreats the stop).
    """
    d       = pos['direction']
    stop    = pos['stop']
    entry   = pos['entry']
    tp1     = pos['tp1']
    tp2     = pos['tp2']
    tp1_hit = pos.get('tp1_hit', False)
    tp2_hit = pos.get('tp2_hit', False)
    tp3_hit = pos.get('tp3_hit', False)

    if d == 'LONG':
        if tp3_hit and stop < pos['tp2']:
            return {'advance': True, 'new_stop': pos['tp2'], 'rule': 'TP3_TRAIL',
                    'reason': f'Rule 3 OK TP3 hit -> stop to TP2 {pos["tp2"]:.2f}'}
        if tp2_hit and stop < tp1:
            return {'advance': True, 'new_stop': tp1, 'rule': 'TP2_TRAIL',
                    'reason': f'Rule 3 OK TP2 hit -> stop to TP1 {tp1:.2f}'}
        if tp1_hit and stop < entry:
            return {'advance': True, 'new_stop': entry, 'rule': 'TP1_TRAIL',
                    'reason': f'Rule 3 OK TP1 hit -> stop to breakeven {entry:.2f}'}
    else:  # SHORT
        if tp3_hit and stop > pos['tp2']:
            return {'advance': True, 'new_stop': pos['tp2'], 'rule': 'TP3_TRAIL',
                    'reason': f'Rule 3 OK TP3 hit -> stop to TP2 {pos["tp2"]:.2f}'}
        if tp2_hit and stop > tp1:
            return {'advance': True, 'new_stop': tp1, 'rule': 'TP2_TRAIL',
                    'reason': f'Rule 3 OK TP2 hit -> stop to TP1 {tp1:.2f}'}
        if tp1_hit and stop > entry:
            return {'advance': True, 'new_stop': entry, 'rule': 'TP1_TRAIL',
                    'reason': f'Rule 3 OK TP1 hit -> stop to breakeven {entry:.2f}'}

    return {'advance': False, 'new_stop': None,
            'reason': 'Rule 3: no TP milestone reached yet'}

def pm_check_hits(pos: dict, df: pd.DataFrame) -> list:
    """
    Detect TP hits and stop touches against the last weekly bar.
    Mutates pos flags (tp1_hit, tp2_hit, tp3_hit, closed).
    Returns list of alert strings.
    """
    alerts = []
    last   = df.iloc[-1]
    high   = float(last['High'])
    low    = float(last['Low'])
    t      = pos['ticker']
    d      = pos['direction']

    if d == 'LONG':
        if not pos.get('tp1_hit') and high >= pos['tp1']:
            pos['tp1_hit'] = True
            alerts.append(f'🎯 TP1 HIT — {t} reached {pos["tp1"]:.2f}')
        if pos.get('tp1_hit') and not pos.get('tp2_hit') and high >= pos['tp2']:
            pos['tp2_hit'] = True
            alerts.append(f'🎯🎯 TP2 HIT — {t} reached {pos["tp2"]:.2f}')
        if pos.get('tp2_hit') and not pos.get('tp3_hit') and high >= pos['tp3']:
            pos['tp3_hit'] = True
            alerts.append(f'🎯🎯🎯 TP3 HIT — {t} reached {pos["tp3"]:.2f}')
        if low <= pos['stop']:
            pos['closed']     = True
            pos['close_date'] = datetime.now().strftime('%Y-%m-%d')
            alerts.append(f'🛑 STOP HIT — {t} low {low:.2f} ≤ stop {pos["stop"]:.2f}')
    else:  # SHORT
        if not pos.get('tp1_hit') and low <= pos['tp1']:
            pos['tp1_hit'] = True
            alerts.append(f'🎯 TP1 HIT — {t} reached {pos["tp1"]:.2f}')
        if pos.get('tp1_hit') and not pos.get('tp2_hit') and low <= pos['tp2']:
            pos['tp2_hit'] = True
            alerts.append(f'🎯🎯 TP2 HIT — {t} reached {pos["tp2"]:.2f}')
        if pos.get('tp2_hit') and not pos.get('tp3_hit') and low <= pos['tp3']:
            pos['tp3_hit'] = True
            alerts.append(f'🎯🎯🎯 TP3 HIT — {t} reached {pos["tp3"]:.2f}')
        if high >= pos['stop']:
            pos['closed']     = True
            pos['close_date'] = datetime.now().strftime('%Y-%m-%d')
            alerts.append(f'🛑 STOP HIT — {t} high {high:.2f} ≥ stop {pos["stop"]:.2f}')

    return alerts


def manage_positions(send_email: bool = False) -> list:
    """
    Weekly position check — the two Cycles Trading stop advancement rules.
    Call this at the end of every scan run (or separately).
    Returns list of actionable alert strings.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    positions = _pm_load()
    open_pos  = [p for p in positions if not p.get('closed')]

    print(f'\n{"═"*62}')
    print(f'  📂 POSITION MANAGER  —  {datetime.now().strftime("%Y-%m-%d")}')
    print(f'  {len(open_pos)} open position(s)')
    print(f'{"═"*62}')

    if not open_pos:
        print('  No open positions.\n')
        return []

    all_alerts  = []
    any_change  = False

    def _fetch(ticker):
        try:
            asset = yf.Ticker(ticker)
            df = asset.history(period='6mo', interval='1wk',
                               auto_adjust=True, raise_errors=False)
            if df is None or len(df) < 8:
                return None
            df.columns = [c.capitalize() for c in df.columns]
            return df
        except Exception:
            return None

    # Fetch all tickers in parallel
    dfs = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_fetch, p['ticker']): p['ticker'] for p in open_pos}
        for future in as_completed(futures):
            dfs[futures[future]] = future.result()

    for pos in open_pos:
        t  = pos['ticker']
        d  = pos['direction']
        df = dfs.get(t)

        print(f'\n  ┌─ {d} {t}  entered {pos["entry"]:.2f} on {pos["entry_date"]}')

        if df is None:
            print(f'  │  ⚠ Could not fetch data — skipped')
            print(f'  └{"─"*58}')
            continue

        current = float(df['Close'].iloc[-1])
        raw_pct = (current - pos['entry']) / pos['entry'] * 100
        pnl_pct = round(raw_pct if d == 'LONG' else -raw_pct, 1)  # SHORT profits when price falls
        sign    = '+' if pnl_pct >= 0 else ''
        tps     = (f'TP1{"✓" if pos.get("tp1_hit") else "○"} '
                   f'TP2{"✓" if pos.get("tp2_hit") else "○"} '
                   f'TP3{"✓" if pos.get("tp3_hit") else "○"}')
        print(f'  │  Price: {current:.2f}  PnL: {sign}{pnl_pct}%  Stop: {pos["stop"]:.2f}  {tps}')

        # ── TP / stop hits ───────────────────────────────────
        hit_alerts = pm_check_hits(pos, df)
        for a in hit_alerts:
            print(f'  │  {a}')
            all_alerts.append(a)
        if hit_alerts:
            any_change = True
        if pos.get('closed'):
            print(f'  └  🔴 CLOSED')
            continue

        # ── Rule 1 — swing low / high ────────────────────────
        r1 = pm_rule1_swing(pos, df)
        print(f'  │  {r1["reason"]}')
        if r1['advance']:
            old = pos['stop']
            pos['stop'] = r1['new_stop']
            pos.setdefault('stop_history', []).append({
                'date': datetime.now().strftime('%Y-%m-%d'),
                'from': old, 'to': r1['new_stop'], 'rule': r1['rule']
            })
            all_alerts.append(f'📈 ADVANCE STOP  {t}: {r1["reason"]}')
            any_change = True

        # ── Rule 2 — momentum ────────────────────────────────
        r2 = pm_rule2_momentum(pos, df)
        if r2['advance']:
            long_better  = pos['direction'] == 'LONG'  and r2['new_stop'] > pos['stop']
            short_better = pos['direction'] == 'SHORT' and r2['new_stop'] < pos['stop']
            if long_better or short_better:
                old = pos['stop']
                pos['stop'] = r2['new_stop']
                pos.setdefault('stop_history', []).append({
                    'date': datetime.now().strftime('%Y-%m-%d'),
                    'from': old, 'to': r2['new_stop'], 'rule': r2['rule']
                })
                print(f'  │  {r2["reason"]}')
                all_alerts.append(f'📈 ADVANCE STOP  {t}: {r2["reason"]}')
                any_change = True
            else:
                print(f'  │  Rule 2: {r2["reason"]} (already covered by Rule 1)')
        else:
            print(f'  │  {r2["reason"]}')


        # -- Rule 3 -- TP-based trail stop
        r3 = pm_rule3_tp_trail(pos)
        if r3['advance']:
            is_better = (pos['direction'] == 'LONG' and r3['new_stop'] > pos['stop']) \
                     or (pos['direction'] == 'SHORT' and r3['new_stop'] < pos['stop'])
            if is_better:
                old = pos['stop']
                pos['stop'] = r3['new_stop']
                pos.setdefault('stop_history', []).append({
                    'date': datetime.now().strftime('%Y-%m-%d'),
                    'from': old, 'to': r3['new_stop'], 'rule': r3['rule']
                })
                print(f'  |  {r3["reason"]}')
                all_alerts.append(f'TRAIL STOP  {t}: {r3["reason"]}')
                any_change = True
            else:
                print(f'  |  Rule 3: stop already tighter')
        else:
            print(f'  |  {r3["reason"]}')
        print(f'  └  Active stop: {pos["stop"]:.2f}')

    if any_change:
        _pm_save(positions)
        print(f'\n  💾 positions.json updated.')

    if all_alerts:
        print(f'\n  ╔{"═"*58}╗')
        print(f'  ║  ACTION REQUIRED ({len(all_alerts)} alert(s))')
        for a in all_alerts:
            print(f'  ║   {a}')
        print(f'  ╚{"═"*58}╝')
        if send_email and all_alerts:
            from ct_analysis import send_email_summary   # local import avoids circular import at module load
            body = '\n'.join(all_alerts)
            send_email_summary(
                f'🔔 Cycles Position Alert — {datetime.now().strftime("%Y-%m-%d")}',
                body
            )
    print()
    return all_alerts


def list_positions(show_closed: bool = False):
    """Print a compact table of all (open) positions."""
    positions = _pm_load()
    rows = [p for p in positions if show_closed or not p.get('closed')]
    if not rows:
        print('  No positions found.')
        return
    print(f'\n  {"ID":<28} {"Dir":<6} {"Entry":>7} {"Stop":>7} '
          f'{"TP1":>7} {"TP2":>7} {"TP3":>7}  TPs')
    print(f'  {"─"*28} {"─"*6} {"─"*7} {"─"*7} {"─"*7} {"─"*7} {"─"*7}  {"─"*5}')
    for p in rows:
        tps = ('T1✓' if p.get('tp1_hit') else 'T1○') + \
              ('T2✓' if p.get('tp2_hit') else 'T2○') + \
              ('T3✓' if p.get('tp3_hit') else 'T3○')
        closed_mark = ' [CLOSED]' if p.get('closed') else ''
        print(f'  {p["id"]:<28} {p["direction"]:<6} {p["entry"]:>7.2f} '
              f'{p["stop"]:>7.2f} {p["tp1"]:>7.2f} {p["tp2"]:>7.2f} '
              f'{p["tp3"]:>7.2f}  {tps}{closed_mark}')
    print()
