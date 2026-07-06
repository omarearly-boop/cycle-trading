#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ct_indicators.py — Pure technical indicators and level-analysis helpers.

All functions here are pure: they operate on DataFrames / scalars already
in memory and make NO network calls.  Network I/O (yfinance fetches) lives
in ct_market_data.py.  The three names below are re-exported for backward
compatibility so existing callers don't need to change their imports.
"""
import sys, warnings, logging
from datetime import datetime

warnings.filterwarnings('ignore')
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.getLogger('peewee').setLevel(logging.CRITICAL)

try:    import pandas as pd
except:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pandas", "--quiet"])
    import pandas as pd

import numpy as np

from ct_config import (
    EARNINGS_WARN_DAYS, PM_SWING_LOOKBACK,
    MAX_DIST_STOCK, MAX_DIST_CRYPTO, MAX_DIST_COMMODITY, MAX_DIST_INTL,
)

# ── I/O adapter re-exports (backward compat) ────────────────────────────────
# Network calls live in ct_market_data; importing them here keeps all
# existing `from ct_indicators import get_earnings, ...` lines working.
from ct_market_data import get_earnings, get_monthly_analysis, get_sector_rs

# ══════════════════════════════════════════════════════════════
#  INDICATOR FUNCTIONS
# ══════════════════════════════════════════════════════════════

def rsi(series, n=14):
    d  = series.diff()
    g  = d.where(d > 0, 0.0).rolling(n).mean()
    l  = (-d.where(d < 0, 0.0)).rolling(n).mean()
    rs = g / l.replace(0, float('nan'))
    return 100 - (100 / (1 + rs))

def atr(high, low, close, n=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def get_trend(df):
    """
    Determine weekly trend using SMA crossover + price slope.
    Returns: 'LONG', 'SHORT', or None (no clear trend).
    """
    if len(df) < 55:
        return None

    sma20  = float(df['Close'].rolling(20).mean().iloc[-1])
    sma50  = float(df['Close'].rolling(50).mean().iloc[-1])
    price  = float(df['Close'].iloc[-1])
    p8ago  = float(df['Close'].iloc[-9]) if len(df) >= 9 else price

    # Uptrend: SMA20 > SMA50 and price above SMA50
    if sma20 > sma50 and price > sma50 * 0.97:
        return 'LONG'

    # Downtrend: SMA20 < SMA50 and price below SMA50
    if sma20 < sma50 and price < sma50 * 1.03:
        return 'SHORT'

    return None

def swing_lows(series, order=3):
    pts = []
    for i in range(order, len(series) - order):
        win = [series.iloc[i+j] for j in range(-order, order+1) if j != 0]
        if all(series.iloc[i] <= w for w in win):
            pts.append(float(series.iloc[i]))
    return pts

def swing_highs(series, order=3):
    pts = []
    for i in range(order, len(series) - order):
        win = [series.iloc[i+j] for j in range(-order, order+1) if j != 0]
        if all(series.iloc[i] >= w for w in win):
            pts.append(float(series.iloc[i]))
    return pts

def _pm_pivot_lows(df: pd.DataFrame, lookback: int = PM_SWING_LOOKBACK) -> list:
    """
    Confirmed weekly swing lows for the position manager.
    Uses Low column. Excludes the last (current/open) bar.
    Returns list of (bar_index, price).
    """
    lows = df['Low'].values
    result = []
    for i in range(lookback, len(lows) - lookback - 1):
        if (all(lows[i] <= lows[i-j] for j in range(1, lookback+1)) and
                all(lows[i] <= lows[i+j] for j in range(1, lookback+1))):
            result.append((i, float(lows[i])))
    return result

def _pm_pivot_highs(df: pd.DataFrame, lookback: int = PM_SWING_LOOKBACK) -> list:
    """
    Confirmed weekly swing highs for the position manager.
    Uses High column. Excludes the last (current/open) bar.
    Returns list of (bar_index, price).
    """
    highs = df['High'].values
    result = []
    for i in range(lookback, len(highs) - lookback - 1):
        if (all(highs[i] >= highs[i-j] for j in range(1, lookback+1)) and
                all(highs[i] >= highs[i+j] for j in range(1, lookback+1))):
            result.append((i, float(highs[i])))
    return result

def get_levels(df, price, atr_val):
    """
    Find nearest support (below price) and resistance (above price).
    Falls back to SMA20 for support and ATR-based target for resistance.
    """
    sma20 = float(df['Close'].rolling(20).mean().iloc[-1])

    lows  = swing_lows(df['Low'],  order=3)
    highs = swing_highs(df['High'], order=3)

    supports    = [v for v in lows  if v < price * 0.98]
    resistances = [v for v in highs if v > price * 1.02]

    # Support: nearest swing low, or SMA20, or 8% below price
    if supports:
        support = max(supports)
    elif sma20 < price * 0.98:
        support = sma20
    else:
        support = price * 0.92

    # Resistance: nearest swing high, or ATR projection (for stocks at ATH)
    if resistances:
        resistance = min(resistances)
    else:
        resistance = price + atr_val * 3.5   # realistic next resistance

    return round(support, 4), round(resistance, 4)

def vol_declining(df, n=3):
    avg    = float(df['Volume'].rolling(20).mean().iloc[-1])
    recent = float(df['Volume'].iloc[-n:].mean())
    if avg == 0:
        return False
    return recent < avg * 0.85

def get_support_quality(df, support_level, tolerance=0.03):
    """
    Count weekly closes / lows within tolerance of support.
    Returns: (touches, quality)  quality = STRONG / MEDIUM / WEAK
    """
    try:
        lower = support_level * (1 - tolerance)
        upper = support_level * (1 + tolerance)
        lows  = df['Low'].values
        touches = int(sum(1 for l in lows if lower <= l <= upper))
        if   touches >= 3: quality = 'STRONG'
        elif touches >= 2: quality = 'MEDIUM'
        else:              quality = 'WEAK'
        return touches, quality
    except Exception:
        return 0, 'WEAK'


def check_level_reliability(df, level, lookback: int = 52, tolerance: float = 0.02):
    """
    Assess whether a support/resistance level is reliable.

    A level is UNRELIABLE if price crossed it in BOTH directions historically —
    the market demonstrated it does not respect this barrier.

    State-machine: tracks ABOVE → BELOW → ABOVE transitions (for support).
    One such full cycle = the level was broken both ways = UNRELIABLE.

    Returns: (label, reason)
      'CLEAN'      — price only ever approached from one side
      'TESTED'     — single violation then recovered (moderate confidence)
      'UNRELIABLE' — broken both ways; avoid basing entries here
    """
    try:
        closes = df['Close'].values[-lookback:]
        above_thresh = level * (1 + tolerance)
        below_thresh = level * (1 - tolerance)

        # State machine — track direction changes around the level
        saw_above              = False
        saw_below_after_above  = False
        saw_above_after_below  = False

        for c in closes:
            if c > above_thresh:
                if saw_below_after_above:
                    saw_above_after_below = True   # full cycle: ABOVE→BELOW→ABOVE
                if not saw_below_after_above:
                    saw_above = True
            elif c < below_thresh:
                if saw_above:
                    saw_below_after_above = True   # price dipped below after being above

        if saw_above_after_below:
            return ('UNRELIABLE',
                    f'Level {level:.2f} broken both directions — '
                    f'market did not respect it as a barrier (N.M.S.)')

        below_count = int(sum(1 for c in closes if c < below_thresh))
        above_count = int(sum(1 for c in closes if c > above_thresh))

        if saw_below_after_above and above_count > 0:
            return ('TESTED',
                    f'Level {level:.2f} violated once then recovered — '
                    f'moderate confidence ({above_count} bars above, {below_count} below)')

        return ('CLEAN',
                f'Level {level:.2f} never crossed to the other side — '
                f'strong support/resistance barrier')

    except Exception:
        return 'UNKNOWN', 'Level reliability check failed'


def check_false_breakout(df, level, direction: str = 'up',
                         n_recent: int = 4, tolerance: float = 0.005):
    """
    Detect whether recent price action constitutes a FALSE breakout (פריצת שווא).

    N.M.S. criteria (נסגר מעל/מתחת לסטנדרד):
      A valid breakout requires a weekly CLOSE above/below the level,
      not just a wick through it.

    direction='up'  → checking if price falsely broke above a resistance
    direction='down'→ checking if price falsely broke below a support

    Returns: (is_false: bool, label: str, reason: str)
      label: 'FALSE_BREAKOUT' / 'VALID_BREAKOUT' / 'NO_BREAKOUT'
    """
    try:
        recent = df.tail(n_recent)
        above_thresh = level * (1 + tolerance)
        below_thresh = level * (1 - tolerance)

        if direction == 'up':
            # Did any recent bar wick or close above level?
            any_high_above  = any(float(row['High'])  > above_thresh for _, row in recent.iterrows())
            any_close_above = any(float(row['Close']) > above_thresh for _, row in recent.iterrows())
            current_close   = float(df['Close'].iloc[-1])
            currently_above = current_close > above_thresh

            if any_high_above and not any_close_above:
                return (True, 'FALSE_BREAKOUT',
                        f'Wick above {level:.2f} but no weekly close above — N.M.S. not satisfied')
            if any_close_above and not currently_above:
                return (True, 'FALSE_BREAKOUT',
                        f'Previously closed above {level:.2f} but now back below — breakout failed')
            if any_close_above and currently_above:
                return (False, 'VALID_BREAKOUT',
                        f'Weekly close above {level:.2f} confirmed — valid breakout')
            return (False, 'NO_BREAKOUT',
                    f'Price has not yet reached {level:.2f}')

        else:  # direction == 'down'
            any_low_below   = any(float(row['Low'])   < below_thresh for _, row in recent.iterrows())
            any_close_below = any(float(row['Close']) < below_thresh for _, row in recent.iterrows())
            current_close   = float(df['Close'].iloc[-1])
            currently_below = current_close < below_thresh

            if any_low_below and not any_close_below:
                return (True, 'FALSE_BREAKOUT',
                        f'Wick below {level:.2f} but no weekly close below — N.M.S. not satisfied')
            if any_close_below and not currently_below:
                return (True, 'FALSE_BREAKOUT',
                        f'Previously closed below {level:.2f} but now back above — breakdown failed')
            if any_close_below and currently_below:
                return (False, 'VALID_BREAKOUT',
                        f'Weekly close below {level:.2f} confirmed')
            return (False, 'NO_BREAKOUT',
                    f'Price has not yet broken below {level:.2f}')

    except Exception:
        return False, 'UNKNOWN', 'False breakout check failed'


def check_level_ambiguity(df, key_level: float, atr_val: float,
                          window_factor: float = 1.5, min_sep: float = 0.015):
    """
    Detect "crowded zone" ambiguity — the ALB problem.

    When a trader debates "50% or 61.8%?" both levels are plausible →
    no single clear actionable level exists → lower-quality setup.

    Algorithm:
      1. Collect all confirmed weekly swing lows + highs from the last year.
      2. Find those within window_factor × ATR of key_level.
      3. Deduplicate: levels within min_sep (1.5%) of each other = same level.
      4. Count distinct competing levels.

    Returns: (label, n_competing, reason)
      'CLEAR'     — 0-1 other level nearby  → unambiguous entry
      'CROWDED'   — 2 levels nearby          → some ambiguity
      'AMBIGUOUS' — 3+ levels nearby         → unclear where to act
    """
    try:
        window = atr_val * window_factor
        lo     = key_level - window
        hi     = key_level + window

        # All confirmed weekly pivot lows and highs
        all_pivots = (
            [p for (_, p) in _pm_pivot_lows(df,  lookback=2)] +
            [p for (_, p) in _pm_pivot_highs(df, lookback=2)]
        )

        # Keep only those in the window but NOT the key level itself
        nearby = sorted(
            p for p in all_pivots
            if lo <= p <= hi and abs(p - key_level) / key_level > 0.005
        )

        # Deduplicate: merge pivots within min_sep of each other
        deduped = []
        for p in nearby:
            if not deduped or (p - deduped[-1]) / deduped[-1] > min_sep:
                deduped.append(p)

        n = len(deduped)
        nearby_str = ', '.join(f'{p:.2f}' for p in deduped[:4])

        if n <= 1:
            return ('CLEAR', n,
                    f'Single clear level {key_level:.2f} — no ambiguity '
                    f'({n} other level nearby)' if n else
                    f'Single clear level {key_level:.2f} — isolated entry point')
        elif n == 2:
            return ('CROWDED', n,
                    f'2 competing levels near {key_level:.2f} ({nearby_str}) — moderate ambiguity')
        else:
            return ('AMBIGUOUS', n,
                    f'{n} competing levels near {key_level:.2f} ({nearby_str}) — '
                    f'unclear where to act; look for a cleaner setup')

    except Exception:
        return 'CLEAR', 0, 'Level ambiguity check unavailable'


def check_swing_broken(df: pd.DataFrame, direction: str = 'down') -> tuple:
    """
    Trend confirmation — the MELI lesson.

    Cycles Trading principle:
      DOWNTREND (SHORT): the last confirmed weekly swing low must have been
        CLOSED through (weekly close below it, not just a wick).
      UPTREND (LONG): the last confirmed weekly swing high must have been
        CLOSED through.

    If not broken → it is a CORRECTION inside the prior trend, NOT a new
    confirmed trend. Don't trade it as a new trend; wait for confirmation.

    Returns: (confirmed: bool, label: str, reason: str)
      confirmed=True  → 'CONFIRMED'   — swing level was closed through
      confirmed=False → 'UNCONFIRMED' — swing level still holds, may be correction
    """
    try:
        closes = df['Close'].values

        if direction == 'down':
            pivots = _pm_pivot_lows(df, lookback=2)
            if not pivots:
                # No swing lows found — default to confirmed so we don't block
                return (True, 'CONFIRMED', 'No swing lows found — treating as confirmed')

            last_idx, last_low = pivots[-1]

            # Any weekly close AFTER the swing low bar that is BELOW the swing low?
            closes_after = closes[last_idx + 1: -1]   # exclude last open bar
            broken = any(c < last_low for c in closes_after)

            if broken:
                return (True, 'CONFIRMED',
                        f'Downtrend confirmed — weekly close below swing low {last_low:.2f}')
            else:
                return (False, 'UNCONFIRMED',
                        f'Swing low {last_low:.2f} intact — no weekly close below it; '
                        f'may be a correction, wait for confirmation')

        else:  # direction == 'up'
            pivots = _pm_pivot_highs(df, lookback=2)
            if not pivots:
                return (True, 'CONFIRMED', 'No swing highs found — treating as confirmed')

            last_idx, last_high = pivots[-1]
            closes_after = closes[last_idx + 1: -1]
            broken = any(c > last_high for c in closes_after)

            if broken:
                return (True, 'CONFIRMED',
                        f'Uptrend confirmed — weekly close above swing high {last_high:.2f}')
            else:
                return (False, 'UNCONFIRMED',
                        f'Swing high {last_high:.2f} intact — no weekly close above it; '
                        f'may be a correction, wait for confirmation')

    except Exception:
        return (True, 'CONFIRMED', 'Trend confirmation check unavailable')



# ══════════════════════════════════════════════════════════════
#  TIME HORIZON ESTIMATOR
# ══════════════════════════════════════════════════════════════

def calc_macd(df):
    """
    Calculate MACD (12/26/9) on weekly closes.
    Returns dict: macd_val, signal_val, histogram, trend, cross, divergence
    """
    try:
        closes = df['Close'].dropna()
        if len(closes) < 30:
            return None
        ema12 = closes.ewm(span=12, adjust=False).mean()
        ema26 = closes.ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        sig   = macd.ewm(span=9, adjust=False).mean()
        hist  = macd - sig

        macd_now  = round(float(macd.iloc[-1]), 4)
        sig_now   = round(float(sig.iloc[-1]),  4)
        hist_now  = round(float(hist.iloc[-1]), 4)
        hist_prev = round(float(hist.iloc[-2]), 4)

        # Trend: MACD above or below signal line
        trend = 'BULL' if macd_now > sig_now else 'BEAR'

        # Cross detection (last 2 bars)
        cross = None
        if hist_prev < 0 and hist_now > 0:
            cross = 'GOLDEN'   # bullish crossover
        elif hist_prev > 0 and hist_now < 0:
            cross = 'DEATH'    # bearish crossover

        # Bullish divergence: price making lower low but MACD making higher low
        price_ll = closes.iloc[-1] < closes.iloc[-5]
        macd_hl  = float(macd.iloc[-1]) > float(macd.iloc[-5])
        divergence = 'BULL_DIV' if (price_ll and macd_hl) else None

        return {
            'macd':       macd_now,
            'signal':     sig_now,
            'histogram':  hist_now,
            'trend':      trend,
            'cross':      cross,
            'divergence': divergence,
        }
    except Exception:
        return None


def calc_bollinger(df, period=20):
    """
    Calculate Bollinger Bands (20, 2σ) on weekly closes.
    Returns dict: upper, middle, lower, pct_b, squeeze, position
    """
    try:
        closes = df['Close'].dropna()
        if len(closes) < period:
            return None
        ma    = closes.rolling(period).mean()
        std   = closes.rolling(period).std()
        upper = ma + 2 * std
        lower = ma - 2 * std

        price    = float(closes.iloc[-1])
        upper_v  = round(float(upper.iloc[-1]), 4)
        lower_v  = round(float(lower.iloc[-1]), 4)
        mid_v    = round(float(ma.iloc[-1]),    4)
        band_w   = upper_v - lower_v

        # %B: where price sits in the band (0=lower, 1=upper, <0 or >1 = outside)
        pct_b = round((price - lower_v) / band_w, 3) if band_w > 0 else 0.5

        # Squeeze: band width < 5% of price = low volatility, breakout imminent
        squeeze = band_w / price < 0.05 if price > 0 else False

        # Position label
        if pct_b <= 0.1:
            position = 'NEAR_LOWER'    # oversold territory → potential reversal
        elif pct_b >= 0.9:
            position = 'NEAR_UPPER'    # overbought territory
        elif squeeze:
            position = 'SQUEEZE'       # tight bands → breakout coming
        else:
            position = 'MID'

        return {
            'upper':    upper_v,
            'middle':   mid_v,
            'lower':    lower_v,
            'pct_b':    pct_b,
            'squeeze':  squeeze,
            'position': position,
        }
    except Exception:
        return None


def estimate_time_horizon(entry, target, atr_val):
    """
    Estimate weeks to reach T1 based on weekly ATR.
    Assumes stock covers ~60% of its weekly ATR per week on average.
    Returns (est_weeks, horizon_code, display_label, color)
    """
    dist = abs(target - entry)
    weekly_progress = max(atr_val * 0.60, 0.001)
    weeks = dist / weekly_progress

    if   weeks <= 2.5:
        return round(weeks, 1), 'WEEKLY',  '⚡ שבועי',    '#3fb950', '1–2 שבועות'
    elif weeks <= 6:
        return round(weeks, 1), 'MONTHLY', '📅 חודשי',    '#58a6ff', '3–6 שבועות'
    elif weeks <= 12:
        return round(weeks, 1), 'MEDIUM',  '📈 בינוני',   '#d29922', '2–3 חודשים'
    else:
        return round(weeks, 1), 'LONG',    '🎯 ארוך טווח','#8b949e', '3+ חודשים'


