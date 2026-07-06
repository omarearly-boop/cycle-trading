"""
position_manager.py — Cycles Trading Position Manager
======================================================
Manages open trades after entry.  Two stop advancement rules only:
  Rule 1 — New weekly swing low (LONG) / swing high (SHORT)
  Rule 2 — Momentum principle: N consecutive up/down weeks

Usage:
  python position_manager.py                     # check all positions
  python position_manager.py add                 # interactive add
  python position_manager.py list                # show open positions
  python position_manager.py close <id>          # close a position

positions.json lives next to this file.
"""

import json
import os
import sys
import yfinance as yf
import pandas as pd
from datetime import datetime, date

# ══════════════════════════════════════════════════════════════
#  Config
# ══════════════════════════════════════════════════════════════
POSITIONS_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'positions.json')
SWING_LOOKBACK   = 2    # bars each side to confirm a swing point
MOMENTUM_WEEKS   = 3    # consecutive weeks for momentum rule
STOP_BUFFER_PCT  = 0.01 # place stop 1% below the swing low / above swing high


# ══════════════════════════════════════════════════════════════
#  Persistence
# ══════════════════════════════════════════════════════════════

def load_positions() -> list:
    if not os.path.exists(POSITIONS_FILE):
        return []
    with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f).get('positions', [])

def save_positions(positions: list):
    with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(
            {'positions': positions, '_updated': str(date.today())},
            f, indent=2, ensure_ascii=False
        )

def add_position(ticker: str, direction: str, entry: float,
                 stop: float, tp1: float, tp2: float, tp3: float,
                 units: int, notes: str = '') -> dict:
    """Add a new open position to positions.json."""
    direction = direction.upper()
    assert direction in ('LONG', 'SHORT'), "direction must be LONG or SHORT"
    positions = load_positions()
    pos = {
        'id':         f"{ticker.upper()}_{datetime.now().strftime('%Y%m%d%H%M')}",
        'ticker':     ticker.upper(),
        'direction':  direction,
        'entry':      round(float(entry), 4),
        'stop':       round(float(stop),  4),
        'tp1':        round(float(tp1),   4),
        'tp2':        round(float(tp2),   4),
        'tp3':        round(float(tp3),   4),
        'units':      int(units),
        'entry_date': str(date.today()),
        'tp1_hit':    False,
        'tp2_hit':    False,
        'tp3_hit':    False,
        'closed':     False,
        'stop_history': [],   # log of every stop advancement
        'notes':      notes,
    }
    positions.append(pos)
    save_positions(positions)
    print(f'  ✅ Added: {pos["id"]}  {direction} {ticker.upper()} @ {entry}')
    return pos

def close_position(pos_id: str, exit_price: float = None):
    positions = load_positions()
    for p in positions:
        if p['id'] == pos_id:
            p['closed']     = True
            p['close_date'] = str(date.today())
            if exit_price:
                p['exit_price'] = round(float(exit_price), 4)
            print(f'  🔒 Closed: {pos_id}')
            break
    else:
        print(f'  ⚠ Position not found: {pos_id}')
    save_positions(positions)


# ══════════════════════════════════════════════════════════════
#  Market Data
# ══════════════════════════════════════════════════════════════

def _fetch_weekly(ticker: str) -> pd.DataFrame | None:
    try:
        asset = yf.Ticker(ticker)
        df = asset.history(period='6mo', interval='1wk',
                           auto_adjust=True, raise_errors=False)
        if df is None or len(df) < 8:
            return None
        df.columns = [c.capitalize() for c in df.columns]
        return df
    except Exception as e:
        print(f'    ⚠ Data error {ticker}: {e}')
        return None


# ══════════════════════════════════════════════════════════════
#  Swing Point Detection
# ══════════════════════════════════════════════════════════════

def _swing_lows(df: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> list[tuple[int, float]]:
    """
    Return list of (bar_index, price) for weekly swing lows.
    Swing low at i: Low[i] is lower than every bar in [i-lookback .. i-1]
                    AND lower than every bar in [i+1 .. i+lookback].
    Note: skips last bar (incomplete current week).
    """
    lows   = df['Low'].values
    result = []
    for i in range(lookback, len(lows) - lookback - 1):
        if (all(lows[i] <= lows[i-j] for j in range(1, lookback+1)) and
                all(lows[i] <= lows[i+j] for j in range(1, lookback+1))):
            result.append((i, float(lows[i])))
    return result

def _swing_highs(df: pd.DataFrame, lookback: int = SWING_LOOKBACK) -> list[tuple[int, float]]:
    """Return list of (bar_index, price) for weekly swing highs."""
    highs  = df['High'].values
    result = []
    for i in range(lookback, len(highs) - lookback - 1):
        if (all(highs[i] >= highs[i-j] for j in range(1, lookback+1)) and
                all(highs[i] >= highs[i+j] for j in range(1, lookback+1))):
            result.append((i, float(highs[i])))
    return result


# ══════════════════════════════════════════════════════════════
#  Rule 1 — New Swing Low / Swing High
# ══════════════════════════════════════════════════════════════

def check_rule1(pos: dict, df: pd.DataFrame) -> dict:
    """
    LONG: look for the most recent confirmed swing low that is ABOVE
          the current stop → new stop = swing_low * (1 - STOP_BUFFER_PCT).

    SHORT: look for the most recent confirmed swing high that is BELOW
           the current stop → new stop = swing_high * (1 + STOP_BUFFER_PCT).

    Returns: {'advance': bool, 'new_stop': float|None, 'reason': str}
    """
    current_stop = pos['stop']
    direction    = pos['direction']

    if direction == 'LONG':
        pivots = _swing_lows(df)
        # Only pivots that are ABOVE current stop (i.e., they improve it)
        valid  = [(i, p) for (i, p) in pivots if p > current_stop]
        if not valid:
            return {'advance': False, 'new_stop': None,
                    'reason': 'Rule 1: no confirmed swing low above current stop'}
        _, pivot_price = valid[-1]   # most recent
        new_stop = round(pivot_price * (1 - STOP_BUFFER_PCT), 2)
        if new_stop <= current_stop:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 1: swing low {pivot_price:.2f} — stop unchanged (buffer too small)'}
        return {'advance': True, 'new_stop': new_stop,
                'reason': f'Rule 1 (swing low {pivot_price:.2f}) → new stop {new_stop:.2f}',
                'rule': 'SWING_LOW'}

    else:  # SHORT
        pivots = _swing_highs(df)
        valid  = [(i, p) for (i, p) in pivots if p < current_stop]
        if not valid:
            return {'advance': False, 'new_stop': None,
                    'reason': 'Rule 1: no confirmed swing high below current stop'}
        _, pivot_price = valid[-1]
        new_stop = round(pivot_price * (1 + STOP_BUFFER_PCT), 2)
        if new_stop >= current_stop:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 1: swing high {pivot_price:.2f} — stop unchanged'}
        return {'advance': True, 'new_stop': new_stop,
                'reason': f'Rule 1 (swing high {pivot_price:.2f}) → new stop {new_stop:.2f}',
                'rule': 'SWING_HIGH'}


# ══════════════════════════════════════════════════════════════
#  Rule 2 — Momentum Principle
# ══════════════════════════════════════════════════════════════

def check_rule2(pos: dict, df: pd.DataFrame, n_weeks: int = MOMENTUM_WEEKS) -> dict:
    """
    LONG:  N consecutive weekly closes higher than the previous week.
           Stop → below the lowest low of those N weeks.

    SHORT: N consecutive weekly closes lower than the previous week.
           Stop → above the highest high of those N weeks.

    Skips the current (incomplete) week — uses confirmed closed candles.
    """
    current_stop = pos['stop']
    direction    = pos['direction']
    closes = df['Close'].values[:-1]   # exclude current open week
    lows   = df['Low'].values[:-1]
    highs  = df['High'].values[:-1]

    if len(closes) < n_weeks + 1:
        return {'advance': False, 'new_stop': None,
                'reason': f'Rule 2: need {n_weeks+1} closed weeks, have {len(closes)}'}

    recent = closes[-(n_weeks+1):]   # n_weeks comparisons need n_weeks+1 values

    if direction == 'LONG':
        all_higher = all(recent[i] > recent[i-1] for i in range(1, len(recent)))
        if not all_higher:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 2: not {n_weeks} consecutive higher closes'}
        momentum_lows = lows[-n_weeks:]
        new_stop = round(float(min(momentum_lows)) * (1 - STOP_BUFFER_PCT), 2)
        if new_stop <= current_stop:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 2: momentum low {min(momentum_lows):.2f} — stop not improved'}
        return {'advance': True, 'new_stop': new_stop,
                'reason': f'Rule 2 ({n_weeks}-week momentum) → new stop {new_stop:.2f}',
                'rule': 'MOMENTUM'}

    else:  # SHORT
        all_lower = all(recent[i] < recent[i-1] for i in range(1, len(recent)))
        if not all_lower:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 2: not {n_weeks} consecutive lower closes'}
        momentum_highs = highs[-n_weeks:]
        new_stop = round(float(max(momentum_highs)) * (1 + STOP_BUFFER_PCT), 2)
        if new_stop >= current_stop:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 2: momentum high {max(momentum_highs):.2f} — stop not improved'}
        return {'advance': True, 'new_stop': new_stop,
                'reason': f'Rule 2 ({n_weeks}-week momentum) → new stop {new_stop:.2f}',
                'rule': 'MOMENTUM'}


# ══════════════════════════════════════════════════════════════
#  TP / Stop Hit Detection
# ══════════════════════════════════════════════════════════════

def check_hits(pos: dict, df: pd.DataFrame) -> list[str]:
    """
    Check if last week's candle touched any TP or the stop.
    Mutates pos (tp1_hit, tp2_hit, tp3_hit, closed).
    Returns list of alert strings.
    """
    alerts = []
    last   = df.iloc[-1]
    high   = float(last['High'])
    low    = float(last['Low'])
    d      = pos['direction']
    t      = pos['ticker']

    if d == 'LONG':
        if not pos.get('tp1_hit') and high >= pos['tp1']:
            pos['tp1_hit'] = True
            alerts.append(f'🎯 TP1 HIT  {t} reached {pos["tp1"]:.2f}')
        if pos.get('tp1_hit') and not pos.get('tp2_hit') and high >= pos['tp2']:
            pos['tp2_hit'] = True
            alerts.append(f'🎯🎯 TP2 HIT  {t} reached {pos["tp2"]:.2f}')
        if pos.get('tp2_hit') and not pos.get('tp3_hit') and high >= pos['tp3']:
            pos['tp3_hit'] = True
            alerts.append(f'🎯🎯🎯 TP3 HIT  {t} reached {pos["tp3"]:.2f}')
        if low <= pos['stop']:
            pos['closed'] = True
            pos['close_date'] = str(date.today())
            alerts.append(f'🛑 STOP HIT  {t} low {low:.2f} ≤ stop {pos["stop"]:.2f}')

    else:  # SHORT
        if not pos.get('tp1_hit') and low <= pos['tp1']:
            pos['tp1_hit'] = True
            alerts.append(f'🎯 TP1 HIT  {t} reached {pos["tp1"]:.2f}')
        if pos.get('tp1_hit') and not pos.get('tp2_hit') and low <= pos['tp2']:
            pos['tp2_hit'] = True
            alerts.append(f'🎯🎯 TP2 HIT  {t} reached {pos["tp2"]:.2f}')
        if pos.get('tp2_hit') and not pos.get('tp3_hit') and low <= pos['tp3']:
            pos['tp3_hit'] = True
            alerts.append(f'🎯🎯🎯 TP3 HIT  {t} reached {pos["tp3"]:.2f}')
        if high >= pos['stop']:
            pos['closed'] = True
            pos['close_date'] = str(date.today())
            alerts.append(f'🛑 STOP HIT  {t} high {high:.2f} ≥ stop {pos["stop"]:.2f}')

    return alerts


# ══════════════════════════════════════════════════════════════
#  Main: Manage All Positions
# ══════════════════════════════════════════════════════════════

def manage_positions(email_alerts: bool = False) -> list[str]:
    """
    Check every open position.  Apply Rule 1 and Rule 2.  Log results.
    Returns list of actionable alert strings.
    """
    positions = load_positions()
    open_pos  = [p for p in positions if not p.get('closed')]

    print(f'\n{"═"*62}')
    print(f'  CYCLES POSITION MANAGER  —  {date.today()}')
    print(f'  {len(open_pos)} open position(s)')
    print(f'{"═"*62}')

    if not open_pos:
        print('  No open positions.\n')
        return []

    all_alerts   = []
    any_change   = False

    for pos in open_pos:
        t = pos['ticker']
        d = pos['direction']
        print(f'\n  ┌─ {d} {t}  (entered {pos["entry"]:.2f} on {pos["entry_date"]})')

        df = _fetch_weekly(t)
        if df is None:
            print(f'  │  ⚠ Could not fetch weekly data — skipped')
            print(f'  └{"─"*58}')
            continue

        current = float(df['Close'].iloc[-1])
        pnl_pct = round((current - pos['entry']) / pos['entry'] * 100, 1)
        sign    = '+' if pnl_pct >= 0 else ''
        tps     = (f'TP1{"✓" if pos.get("tp1_hit") else "○"} '
                   f'TP2{"✓" if pos.get("tp2_hit") else "○"} '
                   f'TP3{"✓" if pos.get("tp3_hit") else "○"}')
        print(f'  │  Price: {current:.2f}  PnL: {sign}{pnl_pct}%  Stop: {pos["stop"]:.2f}  {tps}')

        # ── TP / Stop hits ───────────────────────────────────
        hit_alerts = check_hits(pos, df)
        for a in hit_alerts:
            print(f'  │  {a}')
            all_alerts.append(a)
        if hit_alerts:
            any_change = True

        if pos.get('closed'):
            print(f'  └  🔴 CLOSED (stop hit)')
            continue

        # ── Rule 1 ───────────────────────────────────────────
        r1 = check_rule1(pos, df)
        if r1['advance']:
            old_stop = pos['stop']
            pos['stop'] = r1['new_stop']
            pos.setdefault('stop_history', []).append({
                'date': str(date.today()), 'from': old_stop,
                'to': r1['new_stop'], 'rule': r1.get('rule', 'SWING')
            })
            print(f'  │  ✅ {r1["reason"]}')
            all_alerts.append(f'📈 ADVANCE STOP  {t}: {r1["reason"]}')
            any_change = True
        else:
            print(f'  │  ⏸ {r1["reason"]}')

        # ── Rule 2 ───────────────────────────────────────────
        r2 = check_rule2(pos, df)
        if r2['advance'] and r2['new_stop'] != pos['stop']:
            # Only apply if better than Rule 1 result
            if (pos['direction'] == 'LONG'  and r2['new_stop'] > pos['stop']) or \
               (pos['direction'] == 'SHORT' and r2['new_stop'] < pos['stop']):
                old_stop = pos['stop']
                pos['stop'] = r2['new_stop']
                pos.setdefault('stop_history', []).append({
                    'date': str(date.today()), 'from': old_stop,
                    'to': r2['new_stop'], 'rule': r2.get('rule', 'MOMENTUM')
                })
                print(f'  │  ✅ {r2["reason"]}')
                all_alerts.append(f'📈 ADVANCE STOP  {t}: {r2["reason"]}')
                any_change = True
            else:
                print(f'  │  ⏸ Rule 2: already covered by Rule 1')
        else:
            if not r2['advance']:
                print(f'  │  ⏸ {r2["reason"]}')

        print(f'  └  Current stop: {pos["stop"]:.2f}')

    # ── Save if anything changed ─────────────────────────────
    if any_change:
        save_positions(positions)
        print(f'\n  💾 positions.json updated.')

    # ── Summary ──────────────────────────────────────────────
    if all_alerts:
        print(f'\n  ╔{"═"*58}╗')
        print(f'  ║  ACTION REQUIRED ({len(all_alerts)} alert(s)):')
        for a in all_alerts:
            print(f'  ║   {a}')
        print(f'  ╚{"═"*58}╝')

        if email_alerts:
            _send_email_alerts(all_alerts)

    print()
    return all_alerts


# ══════════════════════════════════════════════════════════════
#  Display
# ══════════════════════════════════════════════════════════════

def list_positions(show_closed: bool = False):
    positions = load_positions()
    rows = [p for p in positions if show_closed or not p.get('closed')]
    if not rows:
        print('  No positions.')
        return
    print(f'\n  {"ID":<28} {"Dir":<6} {"Entry":>7} {"Stop":>7} {"TP1":>7} {"PnL%":>6}  TPs  Notes')
    print(f'  {"─"*28} {"─"*6} {"─"*7} {"─"*7} {"─"*7} {"─"*6}  {"─"*5}')
    for p in rows:
        try:
            df = _fetch_weekly(p['ticker'])
            current = float(df['Close'].iloc[-1]) if df is not None else p['entry']
        except Exception:
            current = p['entry']
        pnl = round((current - p['entry']) / p['entry'] * 100, 1)
        sign = '+' if pnl >= 0 else ''
        tps  = ('T1' if p.get('tp1_hit') else '  ') + ('T2' if p.get('tp2_hit') else '  ') + ('T3' if p.get('tp3_hit') else '  ')
        closed_mark = ' [CLOSED]' if p.get('closed') else ''
        print(f'  {p["id"]:<28} {p["direction"]:<6} {p["entry"]:>7.2f} {p["stop"]:>7.2f} '
              f'{p["tp1"]:>7.2f} {sign}{pnl:>5.1f}%  {tps}  {p.get("notes","")}{closed_mark}')
    print()


# ══════════════════════════════════════════════════════════════
#  Email (reuses scanner's send_email_summary if available)
# ══════════════════════════════════════════════════════════════

def _send_email_alerts(alerts: list[str]):
    try:
        import smtplib, os as _os
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        SENDER   = 'omarearly@gmail.com'
        APP_PWD  = _os.environ.get('GMAIL_APP_PASSWORD', '')
        if not APP_PWD:
            print('  ⚠ GMAIL_APP_PASSWORD not set — email skipped.')
            return
        body = '\n'.join(alerts)
        msg  = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = f'🔔 Cycles Position Alert — {date.today()}'
        msg['From']    = SENDER
        msg['To']      = SENDER
        with smtplib.SMTP('smtp.gmail.com', 587) as s:
            s.ehlo(); s.starttls()
            s.login(SENDER, APP_PWD)
            s.sendmail(SENDER, SENDER, msg.as_string())
        print('  ✅ Alert email sent.')
    except Exception as e:
        print(f'  ⚠ Email failed: {e}')


# ══════════════════════════════════════════════════════════════
#  Interactive Add
# ══════════════════════════════════════════════════════════════

def _interactive_add():
    print('\n  ── Add Position ──────────────────────────────────────')
    ticker    = input('  Ticker      : ').strip().upper()
    direction = input('  Direction   (LONG/SHORT): ').strip().upper()
    entry     = float(input('  Entry price : '))
    stop      = float(input('  Stop price  : '))
    tp1       = float(input('  TP1         : '))
    tp2       = float(input('  TP2         : '))
    tp3       = float(input('  TP3         : '))
    units     = int(input('  Units/shares: '))
    notes     = input('  Notes       : ').strip()
    add_position(ticker, direction, entry, stop, tp1, tp2, tp3, units, notes)


# ══════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    args = sys.argv[1:]

    if not args or args[0] == 'check':
        manage_positions(email_alerts='--email' in args)

    elif args[0] == 'list':
        list_positions(show_closed='--all' in args)

    elif args[0] == 'add':
        if len(args) >= 9:
            # python position_manager.py add LMND LONG 54.13 45.07 71.38 87.04 100.15 11
            _, _, ticker, direction, entry, stop, tp1, tp2, tp3, units, *rest = sys.argv
            notes = ' '.join(rest)
            add_position(ticker, direction, float(entry), float(stop),
                         float(tp1), float(tp2), float(tp3), int(units), notes)
        else:
            _interactive_add()

    elif args[0] == 'close' and len(args) >= 2:
        pos_id     = args[1]
        exit_price = float(args[2]) if len(args) >= 3 else None
        close_position(pos_id, exit_price)

    else:
        print(__doc__)
