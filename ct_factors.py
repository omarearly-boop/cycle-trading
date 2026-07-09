#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ct_factors.py — 21-factor registry + calc_probability()."""
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
    RSI_LONG_MAX, RSI_SHORT_MIN, MIN_RR, EARNINGS_WARN_DAYS,
    MAX_DIST_STOCK, FUNDAMENTAL_TIMEOUT,
)
# check_fibonacci_zone is defined later in this file (see below) — it used to
# be imported from ct_indicators, but it never actually lived there, which
# broke every module that imports it transitively (ct_analysis.py,
# cycles_trading_scanner.py) as soon as they tried to import this module.

# ══════════════════════════════════════════════════════════════

# ── Factor decorator — auto-registers functions into FACTORS on definition ──
FACTORS: list = []   # populated by @factor as module loads

def factor(fn):
    """
    Decorator that registers a factor function in FACTORS.
    To add a new factor: write the function with @factor — one step.
    To disable a factor: comment out @factor — one step.
    """
    FACTORS.append(fn)
    return fn


#  FACTOR REGISTRY — each factor is a pure function (r) → (delta, label, explanation)
#  To add Factor 17: write a _factor_xxx function, append to FACTORS list below.
#  To disable a factor: remove it from FACTORS (no other change needed).
# ══════════════════════════════════════════════════════════════

@factor
def _factor_rsi(r):
    is_long = 'LONG' in r['Dir']
    v = r['RSI']
    if is_long:
        if 30 <= v <= 50:   return +16, "RSI", f"RSI {v} — ideal pullback zone (30–50)"
        elif 50 < v <= 58:  return +8,  "RSI", f"RSI {v} — acceptable, not overbought"
        elif v < 30:        return +5,  "RSI", f"RSI {v} — oversold bounce potential"
        else:               return -8,  "RSI", f"RSI {v} — elevated, less room to run"
    else:
        if 55 <= v <= 72:   return +16, "RSI", f"RSI {v} — ideal bounce zone (55–72)"
        elif 50 <= v < 55:  return +8,  "RSI", f"RSI {v} — acceptable, not oversold"
        elif v > 72:        return +5,  "RSI", f"RSI {v} — overbought, reversal likely"
        else:               return -8,  "RSI", f"RSI {v} — low, bearish case weaker"

@factor
def _factor_rr(r):
    v = r['R:R']
    if v >= 4.0:   return +14, "R:R Ratio", f"R:R 1:{v} — excellent room to target"
    elif v >= 3.0: return +10, "R:R Ratio", f"R:R 1:{v} — strong setup"
    elif v >= 2.5: return +6,  "R:R Ratio", f"R:R 1:{v} — solid"
    else:          return +2,  "R:R Ratio", f"R:R 1:{v} — minimum threshold"

@factor
def _factor_volume(r):
    """
    Factor 3 — Volume on Retest (quantitative).

    vol_ratio = recent 3-bar avg / 20-bar avg of volume.
    Low volume on a pullback = smart money NOT distributing = bullish.
    High volume on a pullback = distribution / selling pressure = bearish.

    Cycles Trading principle: "מחזורי מסחר נמוכים בריטסט = אין מכירה של כסף חכם"
    """
    vol_ratio = r.get('_vol_ratio', 1.0)
    vol_ok    = r.get('Vol') == 'OK'

    if vol_ok and vol_ratio <= 0.55:
        return (+16, "Volume", f"Vol {vol_ratio:.2f}× baseline — very light (strong no-distribution signal)")
    elif vol_ok and vol_ratio <= 0.75:
        return (+10, "Volume", f"Vol {vol_ratio:.2f}× baseline — declining (accumulation zone)")
    elif vol_ok:
        return (+5,  "Volume", f"Vol {vol_ratio:.2f}× baseline — below avg (mild retest confirmation)")
    elif vol_ratio > 1.6:
        return (-14, "Volume", f"Vol {vol_ratio:.2f}× baseline — heavy selling (distribution on retest)")
    elif vol_ratio > 1.2:
        return (-6,  "Volume", f"Vol {vol_ratio:.2f}× baseline — above avg (caution, possible distribution)")
    else:
        return (-3,  "Volume", f"Vol {vol_ratio:.2f}× baseline — not clearly declining (weak confirmation)")

@factor
def _factor_entry_distance(r):
    is_long = 'LONG' in r['Dir']
    key_level = r['Support'] if is_long else r['Resist']
    dist_pct  = abs(r['Price'] - key_level) / r['Price'] * 100
    if dist_pct <= 2:   return +14, "Entry Distance", f"Only {dist_pct:.1f}% from key level — near-perfect entry"
    elif dist_pct <= 5: return +9,  "Entry Distance", f"{dist_pct:.1f}% from key level — good entry"
    elif dist_pct <= 8: return +4,  "Entry Distance", f"{dist_pct:.1f}% from key level — acceptable"
    elif dist_pct <= 12:return  0,  "Entry Distance", f"{dist_pct:.1f}% from key level — stretched"
    else:               return -8,  "Entry Distance", f"{dist_pct:.1f}% from key level — too far"

@factor
def _factor_earnings(r):
    earn = r['Earn']
    if earn == 'SOON!':        return -14, "Earnings Risk", "Earnings report soon — high volatility risk"
    elif earn and earn != '-': return +3,  "Earnings Risk", f"Next earnings: {earn} — safe window"
    else:                      return +5,  "Earnings Risk", "No earnings concern"

@factor
def _factor_setup_quality(r):
    v = r.get('_score', 2.0)
    if v >= 6.0:   return +8,  "Setup Quality", f"Setup score {v:.1f} — high-quality signal"
    elif v >= 4.0: return +4,  "Setup Quality", f"Setup score {v:.1f} — good signal"
    elif v >= 2.5: return +1,  "Setup Quality", f"Setup score {v:.1f} — average"
    else:          return -3,  "Setup Quality", f"Setup score {v:.1f} — weak signal"

@factor
def _factor_stop_distance(r):
    stop_pct = abs(r['Entry'] - r['Stop']) / r['Entry'] * 100
    if stop_pct <= 4:   return +6, "Stop Distance", f"Stop {stop_pct:.1f}% away — tight, controlled risk"
    elif stop_pct <= 8: return +3, "Stop Distance", f"Stop {stop_pct:.1f}% away — normal"
    elif stop_pct <= 12:return  0, "Stop Distance", f"Stop {stop_pct:.1f}% away — wide"
    else:               return -5, "Stop Distance", f"Stop {stop_pct:.1f}% away — very wide stop"

@factor
def _factor_monthly_trend(r):
    is_long  = 'LONG' in r['Dir']
    m_trend  = r.get('MonthlyTrend')
    m_candle = r.get('MonthlyCandle')
    if m_trend is None:
        return None  # factor not applicable → skip
    if is_long:
        if m_trend == 'LONG' and m_candle in ('BULL', 'STRONG_BULL', 'NEUTRAL'):
            return +18, "Monthly Trend", f"Monthly trend LONG, last candle {m_candle} — full alignment"
        elif m_trend == 'LONG':
            return +8,  "Monthly Trend", f"Monthly trend LONG despite bearish candle ({m_candle})"
        elif m_trend == 'SHORT' and m_candle in ('NEUTRAL', 'BULL'):
            return -18, "Monthly Trend", f"Monthly trend SHORT — weekly LONG is counter-trend"
        else:
            return -30, "Monthly Trend", f"Monthly SHORT + {m_candle} — strong warning"
    else:
        if m_trend == 'SHORT' and m_candle in ('BEAR', 'STRONG_BEAR', 'NEUTRAL'):
            return +18, "Monthly Trend", f"Monthly trend SHORT, last candle {m_candle} — full alignment"
        elif m_trend == 'SHORT':
            return +8,  "Monthly Trend", f"Monthly trend SHORT, candle mixed ({m_candle})"
        elif m_trend == 'LONG':
            return -25, "Monthly Trend", f"Monthly trend LONG — SHORT is counter-trend"
        else:
            return 0,   "Monthly Trend", "Monthly neutral — no directional confirmation"

@factor
def _factor_sector_rs(r):
    is_long   = 'LONG' in r['Dir']
    rs_label  = r.get('SectorRS')
    sec_trend = r.get('SectorTrend')
    if not rs_label:
        return None  # not applicable (crypto/commodity/intl)
    if is_long:
        if   rs_label == 'STRONG+': d = +14; ex = f"Outperforming sector by {r.get('RS_pct',0)}% — strong RS"
        elif rs_label == 'ABOVE':   d = +7;  ex = "Stock above sector — positive RS"
        elif rs_label == 'NEUTRAL': d = +2;  ex = "Stock in line with sector"
        elif rs_label == 'BELOW':   d = -10; ex = "Stock underperforming sector — weak RS"
        else:                       d = -18; ex = "Stock significantly weaker — avoid"
        if sec_trend == 'DOWN': d -= 8; ex += " | sector in downtrend"
    else:
        if   rs_label == 'WEAK-':   d = +14; ex = "Stock weaker than sector — SHORT aligned"
        elif rs_label == 'BELOW':   d = +7;  ex = "Stock underperforming — SHORT confirmed"
        elif rs_label == 'NEUTRAL': d = +2;  ex = "Sector neutral"
        elif rs_label == 'ABOVE':   d = -10; ex = "Stock outperforming — SHORT risky"
        else:                       d = -18; ex = "Stock leading sector — SHORT very risky"
        if sec_trend == 'UP': d -= 8; ex += " | sector in uptrend"
    return d, "Sector RS", ex

@factor
def _factor_support_quality(r):
    sup_q = r.get('SupportQ')
    if not sup_q:
        return None
    touches = r.get('SupportTouches', 1)
    if   sup_q == 'STRONG': return +10, "Support Quality", f"Support tested {touches}x — proven level"
    elif sup_q == 'MEDIUM': return +4,  "Support Quality", f"Support tested {touches}x — reasonable level"
    else:                   return -8,  "Support Quality", "Support tested once — unproven"

@factor
def _factor_atr_volatility(r):
    v = r.get('ATR_pct', 0)
    if v <= 0: return None
    if v > 12:   return -18, "Volatility (ATR)", f"ATR {v:.1f}% — extreme volatility"
    elif v > 8:  return -10, "Volatility (ATR)", f"ATR {v:.1f}% — high volatility, smaller position"
    elif v > 5:  return  0,  "Volatility (ATR)", f"ATR {v:.1f}% — normal volatility"
    else:        return +4,  "Volatility (ATR)", f"ATR {v:.1f}% — low volatility, easy stop"

@factor
def _factor_earnings_zone(r):
    if r.get('Earn') == 'APPROACHING' and r.get('EarnDays'):
        return -8, "Earnings Zone", f"Earnings in {r['EarnDays']} days — event risk (15–30d zone)"
    return None

@factor
def _factor_late_entry(r):
    if 'LONG' not in r['Dir']: return None
    v = r.get('LateEntry', 0)
    if v > 8:   return -15, "Late Entry", f"Price {v:.1f}% above support — likely missed retest"
    elif v > 5: return -8,  "Late Entry", f"Price {v:.1f}% above support — entry less optimal"
    return None

@factor
def _factor_fundamentals(r):
    fund = r.get('_fundamental')
    if not fund: return None
    sig = fund.get('signal', 'HOLD')
    cons = fund.get('consensus', '—')
    tgt  = fund.get('target', '?')
    if sig == 'BUY':
        return +15, "Fundamentals", f"Analyst BUY (conf {fund.get('conf')}%) — {cons}, target ${tgt}"
    elif sig == 'SELL':
        return -15, "Fundamentals", f"Analyst SELL (conf {fund.get('conf')}%) — {cons}"
    else:
        return 0,   "Fundamentals", f"Analyst HOLD — {cons}"

@factor
def _factor_macd(r):
    macd = r.get('_macd')
    if not macd: return None
    is_long = 'LONG' in r['Dir']
    cross = macd.get('cross')
    trend = macd.get('trend')
    div   = macd.get('divergence')
    if   cross == 'GOLDEN' and is_long:     d = +12; ex = 'MACD Golden Cross — bullish momentum confirmed'
    elif cross == 'DEATH'  and not is_long: d = +12; ex = 'MACD Death Cross — bearish momentum confirmed'
    elif cross == 'GOLDEN' and not is_long: d = -12; ex = 'MACD Golden Cross — conflicts with SHORT'
    elif cross == 'DEATH'  and is_long:     d = -12; ex = 'MACD Death Cross — conflicts with LONG'
    elif trend == 'BULL'   and is_long:     d = +6;  ex = 'MACD above signal line — bullish trend'
    elif trend == 'BEAR'   and not is_long: d = +6;  ex = 'MACD below signal line — bearish trend'
    elif trend == 'BEAR'   and is_long:     d = -6;  ex = 'MACD below signal line — weak LONG momentum'
    else:                                   d =  0;  ex = 'MACD neutral'
    if div == 'BULL_DIV' and is_long:
        d += 8; ex += ' + Bullish divergence'
    return d, "MACD", ex

@factor
def _factor_bollinger(r):
    boll = r.get('_boll')
    if not boll: return None
    is_long = 'LONG' in r['Dir']
    pos     = boll.get('position')
    pct_b   = boll.get('pct_b', 0.5)
    squeeze = boll.get('squeeze', False)
    if   pos == 'NEAR_LOWER' and is_long:     return +10, "Bollinger Bands", f'Near lower band (%B={pct_b:.2f}) — oversold, good LONG'
    elif pos == 'NEAR_UPPER' and not is_long: return +10, "Bollinger Bands", f'Near upper band (%B={pct_b:.2f}) — overbought, good SHORT'
    elif pos == 'NEAR_UPPER' and is_long:     return -8,  "Bollinger Bands", f'Near upper band (%B={pct_b:.2f}) — overbought, risky LONG'
    elif pos == 'NEAR_LOWER' and not is_long: return -8,  "Bollinger Bands", f'Near lower band (%B={pct_b:.2f}) — oversold, risky SHORT'
    elif squeeze:                             return +5,  "Bollinger Bands", 'Bollinger Squeeze — breakout imminent'
    else:                                     return  0,  "Bollinger Bands", f'Mid-band (%B={pct_b:.2f}) — neutral'


@factor
def _factor_level_reliability(r):
    """
    Factor 17 — Level Reliability + False Breakout (N.M.S.).

    Two sub-checks:
      A) Was the key level broken in BOTH directions historically?
         If yes → UNRELIABLE → heavy penalty (-18).
      B) Is the current move a false breakout (wick through level, no weekly close)?
         If yes → N.M.S. not satisfied → penalty (-12).

    A level that scores CLEAN and has a VALID or NO breakout gets a bonus.
    """
    rel = r.get('_level_rel', 'UNKNOWN')
    fb  = r.get('_false_breakout', False)
    fb_label = r.get('_fb_label', '')

    # Sub-check A — level broken both ways
    if rel == 'UNRELIABLE':
        return (-18, 'Level Reliability',
                'Level broken both directions — market never respected it')

    # Sub-check B — false breakout (N.M.S. not met)
    if fb and fb_label == 'FALSE_BREAKOUT':
        return (-12, 'Level Reliability',
                'False breakout — wick through level but no weekly close (N.M.S. criteria)')

    # Positive: clean level, valid or no breakout yet
    if rel == 'CLEAN':
        return (+10, 'Level Reliability',
                'Level never broken to the other side — strong, reliable barrier')
    if rel == 'TESTED':
        return (+4, 'Level Reliability',
                'Level tested once then held — moderate confidence')

    return None   # UNKNOWN — no opinion


@factor
def _factor_level_ambiguity(r):
    """
    Factor 18 — Level Ambiguity (the ALB lesson).

    When multiple competing support/resistance levels cluster near the entry,
    the trader cannot point to ONE clear level → lower conviction setup.

    Expert rule: "אין רמה אחת מובהקת שבה ניתן לפעול → לחפש הזדמנות אחרת"

      CLEAR     → +8   (single unambiguous level — high conviction)
      CROWDED   → -6   (two levels — moderate ambiguity)
      AMBIGUOUS → -16  (three+ levels — look elsewhere)
    """
    amb   = r.get('_level_amb', 'CLEAR')
    n     = r.get('_level_amb_n', 0)
    if   amb == 'CLEAR':
        return (+8,  'Level Clarity',
                f'Single clear entry level — no competing levels nearby ({n} other)')
    elif amb == 'CROWDED':
        return (-6,  'Level Clarity',
                f'2 competing levels in zone — some ambiguity about where to act')
    elif amb == 'AMBIGUOUS':
        return (-16, 'Level Clarity',
                f'{n} competing levels nearby — unclear entry point (seek cleaner setup)')
    return None


def check_fibonacci_zone(df, direction: str, price: float):
    """
    Compute the Fibonacci retracement zone for the current setup.
    Uses the last 52-bar window. Anchors Fibonacci to the LAST significant
    swing low/high before the opposing extreme -- not the absolute min/max.
    This matches the methodology: draw from the last meaningful turning point,
    filtering insignificant daily wiggles (data is weekly so small daily
    moves are naturally absent).

    Returns (zone, ret_pct, swing_low, swing_high, fib_levels_dict)
    zone: 'GOLDEN_ZONE' | 'SHALLOW' | 'DEEP' | 'TOO_DEEP' | 'NO_RETRACEMENT' | 'UNKNOWN'
    """
    def _last_swing_low(df_slice, window=2):
        """Most recent local minimum in df_slice (fallback: global min)."""
        lows = df_slice['Low'].values
        n = len(lows)
        for i in range(n - 1 - window, window, -1):
            if (all(lows[i] <= lows[i - j] for j in range(1, window + 1)) and
                    all(lows[i] <= lows[i + j] for j in range(1, window + 1))):
                return float(lows[i])
        return float(df_slice['Low'].min())  # fallback

    def _last_swing_high(df_slice, window=2):
        """Most recent local maximum in df_slice (fallback: global max)."""
        highs = df_slice['High'].values
        n = len(highs)
        for i in range(n - 1 - window, window, -1):
            if (all(highs[i] >= highs[i - j] for j in range(1, window + 1)) and
                    all(highs[i] >= highs[i + j] for j in range(1, window + 1))):
                return float(highs[i])
        return float(df_slice['High'].max())  # fallback

    try:
        look = df.tail(min(52, len(df)))
        if direction == 'LONG':
            swing_high = float(look['High'].max())
            hi_idx     = look['High'].idxmax()
            before_hi  = look.loc[:hi_idx]
            if len(before_hi) < 5:
                return 'UNKNOWN', 0, 0, 0, {}
            swing_low  = _last_swing_low(before_hi)   # FIXED: last significant low
            move       = swing_high - swing_low
            if move <= 0:
                return 'UNKNOWN', 0, 0, 0, {}
            retracement = (swing_high - price) / move
        else:  # SHORT
            swing_low  = float(look['Low'].min())
            lo_idx     = look['Low'].idxmin()
            before_lo  = look.loc[:lo_idx]
            if len(before_lo) < 5:
                return 'UNKNOWN', 0, 0, 0, {}
            swing_high = _last_swing_high(before_lo)  # FIXED: last significant high
            move       = swing_high - swing_low
            if move <= 0:
                return 'UNKNOWN', 0, 0, 0, {}
            retracement = (price - swing_low) / move

        ret_pct = retracement * 100
        span    = swing_high - swing_low
        # Fib levels (from swing_low)
        fib_levels = {
            '23.6': round(swing_low + span * 0.764, 2) if direction == 'LONG' else round(swing_high - span * 0.764, 2),
            '38.2': round(swing_low + span * 0.618, 2) if direction == 'LONG' else round(swing_high - span * 0.618, 2),
            '50.0': round((swing_high + swing_low) / 2, 2),
            '61.8': round(swing_low + span * 0.382, 2) if direction == 'LONG' else round(swing_high - span * 0.382, 2),
            '78.6': round(swing_low + span * 0.214, 2) if direction == 'LONG' else round(swing_high - span * 0.214, 2),
        }

        if 36 <= ret_pct <= 63:
            zone = 'GOLDEN_ZONE'
        elif 20 <= ret_pct < 36:
            zone = 'SHALLOW'
        elif 63 < ret_pct <= 80:
            zone = 'DEEP'
        elif ret_pct > 80:
            zone = 'TOO_DEEP'
        else:
            zone = 'NO_RETRACEMENT'

        return zone, round(ret_pct, 1), swing_low, swing_high, fib_levels

    except Exception:
        return 'UNKNOWN', 0, 0, 0, {}


@factor
def _factor_fibonacci(r):
    """
    Factor 20 — Fibonacci Retracement Zone.

    Cycles Trading insight (from Discord Q&A, May–Jun 2026):
    Students repeatedly asked about Fibonacci. Expert consensus:
      - Golden Zone 38.2%–61.8%: ideal retracement entry area       → +8
      - Shallow (<38.2%): price hasn't pulled back enough yet        →  0
      - Deep (61.8%–78.6%): valid but weakening setup               → -5
      - Too deep (>78.6%): likely trend change, not retracement     → -12
      - No retracement (<23.6%): entering too early, before pullback → -3

    Rule: "האם אפשר להסתמך רק על פיבו ללא תמיכה? רק אם יש חפיפה בין
           רמת תמיכה לאזור פיבו — אז זה מחזק."
    """
    zone    = r.get('_fib_zone', 'UNKNOWN')
    ret_pct = r.get('_fib_ret_pct', 0)

    if zone == 'UNKNOWN':
        return None
    if zone == 'GOLDEN_ZONE':
        return (+8,  f'Fib {ret_pct:.0f}%: Golden Zone',
                f'Price in golden Fibonacci zone ({ret_pct:.0f}% retracement) — ideal entry area 38.2%–61.8%')
    if zone == 'SHALLOW':
        return ( 0,  f'Fib {ret_pct:.0f}%: Shallow',
                f'Shallow retracement ({ret_pct:.0f}%) — price hasn\'t pulled back to fib levels yet; consider waiting')
    if zone == 'DEEP':
        return (-5,  f'Fib {ret_pct:.0f}%: Deep',
                f'Deep retracement ({ret_pct:.0f}%) — near 78.6% level; still valid but signal is weakening')
    if zone == 'TOO_DEEP':
        return (-12, f'Fib {ret_pct:.0f}%: Too Deep',
                f'Beyond 78.6% ({ret_pct:.0f}%) — retracement suggests possible trend reversal, not correction')
    if zone == 'NO_RETRACEMENT':
        return (-3,  f'Fib {ret_pct:.0f}%: No Retrace',
                f'Minimal retracement ({ret_pct:.0f}%) — entering too early before a proper Fibonacci pullback')
    return None


@factor
def _factor_market_regime(r):
    """Factor 21 - Market Regime (SPY + QQQ weekly vs 20-week MA).

    BULL  (both above 20W MA): LONG +12, SHORT -15
    BEAR  (both below 20W MA): SHORT +12, LONG -15
    NEUTRAL (mixed): +3 both directions

    Principle: trade with the macro regime, not against it.
    """
    from ct_market_data import get_market_regime
    try:
        regime_data = get_market_regime()
        regime      = regime_data.get('regime', 'NEUTRAL')
    except Exception:
        return None

    is_long = 'LONG' in r['Dir']

    if regime == 'BULL':
        if is_long:
            return (+12, 'Market Regime',
                    'BULL regime: SPY+QQQ above 20W MA -- LONG aligned with macro trend')
        else:
            return (-15, 'Market Regime',
                    'BULL regime: SPY+QQQ above 20W MA -- SHORT is counter-trend, higher risk')
    elif regime == 'BEAR':
        if not is_long:
            return (+12, 'Market Regime',
                    'BEAR regime: SPY+QQQ below 20W MA -- SHORT aligned with macro trend')
        else:
            return (-15, 'Market Regime',
                    'BEAR regime: SPY+QQQ below 20W MA -- LONG is counter-trend, higher risk')
    else:  # NEUTRAL
        return (+3, 'Market Regime',
                'NEUTRAL regime: mixed SPY/QQQ signals -- range market, both directions possible')


@factor
def _factor_directional_volume(r):
    """
    Factor 22 -- Directional Volume.
    Compares avg volume on down bars vs up bars over last 10 weekly bars.
    High volume on down bars = institutional selling pressure (expert: ASTS lesson).
    Expert principle: high down-bar volume = sellers still in control.
    """
    ratio = r.get('_dir_vol_ratio', 1.0)
    # Guard against zero — happens when every bar in the window is an up-bar
    inv = (1 / ratio) if ratio > 0 else 99.0

    if ratio >= 2.0:
        return (-14, 'Dir.Volume', f'Down-bar vol {ratio:.1f}x up-bar vol -- heavy institutional selling. '
                                   f'Support may NOT hold -- wait for volume to dry up before entering')
    elif ratio >= 1.4:
        return (-8,  'Dir.Volume', f'Down-bar vol {ratio:.1f}x up-bar vol -- sellers dominate. '
                                   f'Support at risk -- look for entry at next lower level')
    elif ratio >= 1.1:
        return (-3,  'Dir.Volume', f'Down-bar vol {ratio:.1f}x up-bar vol -- mild selling bias, watch closely')
    elif ratio <= 0.5:
        return (+12, 'Dir.Volume', f'Up-bar vol {inv:.1f}x down-bar vol -- strong accumulation')
    elif ratio <= 0.7:
        return (+7,  'Dir.Volume', f'Up-bar vol {inv:.1f}x down-bar vol -- buyers dominate')
    elif ratio <= 0.9:
        return (+3,  'Dir.Volume', f'Up-bar vol {inv:.1f}x down-bar vol -- mild buying bias')
    else:
        return (0,   'Dir.Volume', f'Dir.vol ratio {ratio:.1f} -- balanced pressure')



@factor
def _factor_fundamental_quality(r):
    """
    Factor 23 -- Fundamental Quality.
    Scores valuation, growth, profitability and ownership aligned with the trade direction.
    LONG: rewards cheap+growing, penalises expensive+declining.
    SHORT: rewards expensive+declining fundamentals (validates short thesis).
    Data sourced from get_fundamental_analysis() → setup['_fundamental']['scores'].
    """
    fund = r.get('_fundamental')
    if not fund:
        return None  # ETF/crypto/commodity -- no fundamental data

    scores   = fund.get('scores', {})
    is_long   = 'LONG' in r['Dir']

    pe          = scores.get('pe')
    fwd_pe      = scores.get('fwdPE')
    peg         = scores.get('peg')
    rev_growth  = scores.get('revenueGrowth')  # decimal, e.g. 0.15 = +15%
    eps_growth  = scores.get('epsGrowth')
    net_margin  = scores.get('netMargin')
    roe         = scores.get('roe')
    inst_own    = scores.get('instOwn')
    debt_eq     = scores.get('debtToEquity')
    current_r   = scores.get('currentRatio')

    pts   = 0
    notes = []

    # --- Valuation ---
    val_pe = fwd_pe if fwd_pe else pe
    if val_pe:
        if is_long:
            if val_pe < 15:
                pts += 6; notes.append(f'Low P/E {val_pe:.0f}x (cheap)')
            elif val_pe < 25:
                pts += 2; notes.append(f'Fair P/E {val_pe:.0f}x')
            elif val_pe > 50:
                pts -= 8; notes.append(f'Expensive: P/E {val_pe:.0f}x')
            elif val_pe > 35:
                pts -= 4; notes.append(f'Rich P/E {val_pe:.0f}x')
        else:  # SHORT
            if val_pe > 50:
                pts += 8; notes.append(f'Expensive: P/E {val_pe:.0f}x validates short')
            elif val_pe > 35:
                pts += 4; notes.append(f'Rich P/E {val_pe:.0f}x supports short')
            elif val_pe < 15:
                pts -= 6; notes.append(f'Cheap P/E {val_pe:.0f}x (risky short)')

    if peg and peg > 0:
        if is_long:
            if peg < 1.0:
                pts += 5; notes.append(f'PEG {peg:.1f} (growth at discount)')
            elif peg > 3.0:
                pts -= 5; notes.append(f'PEG {peg:.1f} (overvalued vs growth)')
        else:
            if peg > 3.0:
                pts += 5; notes.append(f'PEG {peg:.1f} validates short')

    # --- Growth ---
    if rev_growth is not None:
        if is_long:
            if rev_growth >= 0.20:
                pts += 7; notes.append(f'Revenue +{rev_growth*100:.0f}% (strong growth)')
            elif rev_growth >= 0.10:
                pts += 4; notes.append(f'Revenue +{rev_growth*100:.0f}% (solid growth)')
            elif rev_growth < 0:
                pts -= 6; notes.append(f'Revenue {rev_growth*100:.0f}% (declining)')
        else:
            if rev_growth < -0.05:
                pts += 6; notes.append(f'Revenue {rev_growth*100:.0f}% supports short')
            elif rev_growth >= 0.15:
                pts -= 5; notes.append(f'Revenue +{rev_growth*100:.0f}% (risky short)')

    if eps_growth is not None:
        if is_long:
            if eps_growth >= 0.25:
                pts += 6; notes.append(f'EPS +{eps_growth*100:.0f}% (accelerating)')
            elif eps_growth >= 0.10:
                pts += 3
            elif eps_growth < -0.10:
                pts -= 5; notes.append(f'EPS {eps_growth*100:.0f}% (decelerating)')
        else:
            if eps_growth < -0.10:
                pts += 5; notes.append(f'EPS {eps_growth*100:.0f}% supports short')
            elif eps_growth >= 0.25:
                pts -= 4; notes.append(f'EPS +{eps_growth*100:.0f}% (risky short)')

    # --- Profitability ---
    if net_margin is not None:
        if is_long:
            if net_margin >= 0.20:
                pts += 5; notes.append(f'Net margin {net_margin*100:.0f}% (excellent)')
            elif net_margin < 0:
                pts -= 6; notes.append(f'Net margin {net_margin*100:.1f}% (unprofitable)')
        else:
            if net_margin < 0:
                pts += 5; notes.append(f'Unprofitable: net margin {net_margin*100:.1f}%')
            elif net_margin >= 0.20:
                pts -= 4; notes.append(f'High margin {net_margin*100:.0f}% (risky short)')

    if roe is not None and is_long:
        if roe >= 0.20:
            pts += 4; notes.append(f'ROE {roe*100:.0f}% (strong returns)')
        elif roe < 0:
            pts -= 3

    # --- Balance sheet / risk ---
    if debt_eq is not None:
        if is_long and debt_eq > 3.0:
            pts -= 4; notes.append(f'High leverage D/E {debt_eq:.1f}')
        elif not is_long and debt_eq > 3.0:
            pts += 3; notes.append(f'Leveraged: D/E {debt_eq:.1f}')

    if current_r is not None and current_r < 1.0:
        if is_long:
            pts -= 3; notes.append(f'Liquidity risk (current ratio {current_r:.1f})')
        else:
            pts += 3; notes.append(f'Liquidity risk validates short')

    # --- Institutional ownership ---
    if inst_own is not None:
        if is_long and inst_own >= 0.70:
            pts += 3; notes.append(f'High institutional ownership {inst_own*100:.0f}%')
        elif not is_long and inst_own < 0.20:
            pts += 2; notes.append(f'Low institutional support {inst_own*100:.0f}%')

    # Clamp between -20 and +20
    pts = max(-20, min(20, pts))

    if not notes:
        notes = ['Mixed fundamentals — neutral weight']

    direction_word = 'LONG' if is_long else 'SHORT'
    summary = '; '.join(notes[:3])
    label = f'Fundamental ({direction_word})'

    if pts >= 8:
        return (pts, label, f'Strong fundamentals support {direction_word}: {summary}')
    elif pts >= 3:
        return (pts, label, f'Solid fundamentals: {summary}')
    elif pts >= -2:
        return (pts, label, f'Neutral fundamentals: {summary}')
    elif pts >= -8:
        return (pts, label, f'Weak fundamentals (caution): {summary}')
    else:
        return (pts, label, f'Poor fundamentals work against {direction_word}: {summary}')


@factor
def _factor_trend_confirmation(r):
    """Factor 19 - Trend Confirmation (the MELI lesson).

    The last confirmed weekly swing low (SHORT) or swing high (LONG) must
    have been CLOSED through, not merely wicked. If the swing level holds,
    the move is a correction inside the prior trend, not a confirmed new trend.

    Expert rule: wait for the weekly close before entering.

      CONFIRMED   -> +10  (swing level closed through -- real trend)
      UNCONFIRMED -> -18  (swing level holds -- likely correction, wait)
    """
    confirmed  = r.get('_trend_confirmed', True)
    label      = r.get('_trend_conf_label', 'CONFIRMED')
    is_long    = 'LONG' in r['Dir']
    swing_word = 'high' if is_long else 'low'

    if confirmed:
        return (+10, f'TrendConf: {label}',
                f'Trend structure confirmed -- last swing {swing_word} closed through')
    else:
        return (-18, f'TrendConf: {label}',
                f'Last swing {swing_word} not closed through -- may be a correction; '
                f'wait for weekly close confirmation before entering')



@factor
def _factor_monthly_sr_confluence(r):
    """
    Factor 24 -- Monthly S/R Confluence (top-down confirmation).

    Cycles Trading insight: the strongest setups appear where a weekly S/R
    level aligns with a MONTHLY S/R level.  Monthly levels carry the weight
    of institutional memory -- they represent major accumulation / distribution
    zones that held for months.

    Also factors in the monthly Fibonacci golden zone (50-61.8%) when it
    overlaps the monthly S/R level, as seen in CAMT July 2026.

    Scoring:
      within 3% of monthly S/R level        -> +18  (strong confluence)
      within 3% + monthly Fib golden zone   -> +25  (double confluence)
      within 5% of monthly S/R level        -> +10
      within 8% of monthly S/R level        -> +5
      no monthly level nearby               ->  0
      monthly trend opposes trade direction -> -10  (e.g. LONG but monthly BEAR)
    """
    msr = r.get('monthly_sr') or {}
    if not msr:
        return None

    dist_pct      = msr.get('dist_pct', 999.0)
    nearest_label = msr.get('nearest_label', 'NONE')
    monthly_trend = msr.get('monthly_trend', 'NEUTRAL')
    fib_zone_mo   = msr.get('fib_zone_monthly', 'UNKNOWN')
    nearest_level = msr.get('nearest_level')
    is_long       = 'LONG' in r['Dir']
    direction     = 'LONG' if is_long else 'SHORT'

    if nearest_label == 'NONE' or nearest_level is None:
        return (0, 'MonthlyS/R: none', 'No monthly S/R level found near current price')

    level_str = f'${nearest_level:,.2f} ({nearest_label.lower()})'

    # Monthly trend penalty
    trend_penalty = 0
    trend_note    = ''
    if direction == 'LONG'  and monthly_trend == 'BEAR':
        trend_penalty = -10
        trend_note    = ' | Monthly trend BEAR -- fighting the monthly trend'
    elif direction == 'SHORT' and monthly_trend == 'BULL':
        trend_penalty = -10
        trend_note    = ' | Monthly trend BULL -- fighting the monthly trend'

    # Golden zone bonus
    fib_bonus = 0
    fib_note  = ''
    if fib_zone_mo == 'GOLDEN_ZONE':
        fib_bonus = 7
        fib_note  = ' + Monthly Fib golden zone'

    if dist_pct <= 3.0:
        base_score = 18 + fib_bonus
        label = f'MonthlyS/R: {dist_pct:.1f}% from {level_str}{fib_note}'
        detail = (f'Price within {dist_pct:.1f}% of monthly {nearest_label.lower()} '
                  f'{level_str} -- strong institutional level{fib_note}{trend_note}')
    elif dist_pct <= 5.0:
        base_score = 10 + fib_bonus
        label = f'MonthlyS/R: {dist_pct:.1f}% from {level_str}'
        detail = (f'Price within {dist_pct:.1f}% of monthly {nearest_label.lower()} '
                  f'{level_str} -- moderate confluence{fib_note}{trend_note}')
    elif dist_pct <= 8.0:
        base_score = 5 + fib_bonus
        label = f'MonthlyS/R: {dist_pct:.1f}% from {level_str}'
        detail = (f'Price approaching monthly {nearest_label.lower()} '
                  f'{level_str} ({dist_pct:.1f}% away){fib_note}{trend_note}')
    else:
        base_score = 0
        label = f'MonthlyS/R: {dist_pct:.1f}% from {level_str}'
        detail = (f'No nearby monthly level (closest: {level_str} at {dist_pct:.1f}%)'
                  f'{trend_note}')

    total = base_score + trend_penalty
    return (total, label, detail)

@factor
def _factor_candle_compression(r):
    """
    Factor 25 - Pullback Candle Compression.

    As price approaches support (LONG) or resistance (SHORT), candle BODIES
    should SHRINK each bar smaller than the last means sellers/buyers are
    losing momentum, and smart money is quietly absorbing the move.

    An AGGRESSIVE large final bar just before the level is the opposite signal:
    price is falling hard into support, not drifting, support is at real risk.

    Master principle: candles getting smaller (nrot sheholchim veketanim begodlam).
    Opposite warning: aggressive drop into support = skip.

    Body sizes are ATR-normalised so a 1.2x reading = bar was 1.2x the ATR.

    Scoring (last 4 weekly candles, b0=oldest b3=newest):
      b3 < b2 < b1 (3 shrinking)  -> COMPRESSING    +10
      b3 < b2 only (2 shrinking)  -> MILD COMPRESS   +5
      b3 > 1.5x avg(b0,b1,b2)    -> AGGRESSIVE     -12
      otherwise                   -> NEUTRAL           0
    """
    bodies = r.get('_candle_bodies', [])
    if len(bodies) < 3:
        return None   # not enough weekly data

    b       = bodies[-4:] if len(bodies) >= 4 else bodies
    last    = b[-1]
    prev    = b[-2]
    prev2   = b[-3] if len(b) >= 3 else b[-2]
    older   = b[:-1]
    avg_old = sum(older) / len(older) if older else last

    # Aggressive drop: last bar significantly bigger than prior approach bars
    if avg_old > 0 and last > avg_old * 1.5:
        ratio = round(last / avg_old, 1)
        return (-12, 'Candle Compression',
                f'Last bar {ratio}x avg of prior bars '
                f'-- aggressive drop into level. Support may not hold -- skip or wait.')

    two_shrinking   = last < prev
    three_shrinking = two_shrinking and (prev < prev2)

    if three_shrinking:
        return (+10, 'Candle Compression',
                '3 consecutive shrinking bars -- textbook compression. '
                'Smart money absorbing the pullback.')
    elif two_shrinking:
        return (+5, 'Candle Compression',
                '2 shrinking bars into level -- partial compression. '
                'Healthy but watch for 3rd bar confirmation.')
    else:
        return (0, 'Candle Compression',
                'No clear compression -- bar sizes mixed on approach. '
                'Not a disqualifier but setup is less textbook.')


@factor
def _factor_breakout_quality(r):
    """
    Factor 26 - Breakout Quality.

    A retest setup only has conviction if the original breakout above/below
    the key level was made with a STRONG candle (large body) AND HIGH volume.

    Weak breakout = price drifted across the level, not a true break.
    Retest of a drifted level has low conviction -- the market may not
    respect it on the retest either.

    Master: lo hayta kan be-emet pritza ichutit shel harama
    (There was no quality breakout of the level)

    Scoring:
      body >= 0.7x ATR AND vol >= 1.3x avg  -> QUALITY BREAKOUT   +10
      one condition met                      -> PARTIAL              +5
      body < 0.3x ATR  OR  vol < 0.8x avg   -> WEAK DRIFT           -8
      otherwise                              -> AVERAGE               0
      no breakout found in 12-bar window     -> skip (None)
    """
    is_long   = 'LONG' in r['Dir']
    direction = 'LONG' if is_long else 'SHORT'
    bq        = r.get('_breakout_quality', {})

    if not bq or direction not in bq or bq[direction] is None:
        return None   # no breakout detected in recent window

    data       = bq[direction]
    body_ratio = data.get('body_ratio', 0.0)
    vol_ratio  = data.get('vol_ratio',  1.0)

    strong_body = body_ratio >= 0.7
    good_vol    = vol_ratio  >= 1.3
    weak_body   = body_ratio <  0.3
    low_vol     = vol_ratio  <  0.8

    if strong_body and good_vol:
        return (+10, 'Breakout Quality',
                f'Breakout bar: body {body_ratio}x ATR, vol {vol_ratio}x avg '
                f'-- quality break. Retest has conviction.')
    elif strong_body or good_vol:
        return (+5, 'Breakout Quality',
                f'Breakout bar: body {body_ratio}x ATR, vol {vol_ratio}x avg '
                f'-- partial quality (one condition met). Acceptable.')
    elif weak_body or low_vol:
        return (-8, 'Breakout Quality',
                f'Breakout bar: body {body_ratio}x ATR, vol {vol_ratio}x avg '
                f'-- weak drift across level, not a true break. '
                f'Retest setup has low conviction.')
    else:
        return (0, 'Breakout Quality',
                f'Breakout bar: body {body_ratio}x ATR, vol {vol_ratio}x avg '
                f'-- average breakout quality.')


@factor
def _factor_adx_structure(r):
    """
    Factor 27 - Long-term Structure (ADX).

    ADX measures trend strength on the weekly timeframe.
    A low ADX = stock is range-bound / chopping -- setup has lower conviction.
    A high ADX = clear directional trend -- setup has higher conviction.

    Scoring:
      ADX > 30 AND 52-week range > 30%  -> STRONG TREND      +8
      ADX > 25                           -> TRENDING           +4
      ADX 20-25                          -> MIXED / NEUTRAL     0
      ADX < 20                                       -> RANGING            -6
      ADX < 20 AND range < 20%          -> TIGHT CHOP        -12  (e.g. 7-year range stock)
      + chronic: >70% of last 26 weeks ADX < 20 -> additional  -5
    """
    adx_data = r.get('_adx_weekly', {})
    if not adx_data:
        return None

    adx       = adx_data.get('adx', 0)
    range_pct = adx_data.get('range_pct_52', 0)
    low_bars  = adx_data.get('low_adx_bars', 0)

    if adx <= 0:
        return None

    chronic = low_bars >= 18  # >70% of last 26 weekly bars had ADX < 20

    if adx > 30 and range_pct > 30:
        delta = +8
        expl  = (f'ADX {adx} - strong trend, 52-week range {range_pct}%. '
                 f'Clear directional structure supports setup conviction.')
    elif adx > 25:
        delta = +4
        expl  = (f'ADX {adx} - trending. Good directional conviction for the setup.')
    elif adx >= 20:
        delta = 0
        expl  = (f'ADX {adx} - mixed/developing trend. Neutral structure.')
    elif adx < 20 and range_pct < 20:
        delta = -12
        expl  = (f'ADX {adx} - range-bound, 52-week range only {range_pct}%. '
                 f'Stock in tight multi-year chop - low setup conviction.')
    else:
        delta = -6
        expl  = (f'ADX {adx} - weak trend, stock ranging. '
                 f'52-week range {range_pct}%.')

    if chronic:
        delta -= 5
        expl  += f' Chronic chop: {low_bars}/26 recent weeks ADX<20.'

    return (delta, 'Long-term Structure', expl)


@factor
def _factor_spy_rs(r):
    """Factor 28 - Relative Strength vs SPY (13-week / 1 quarter).

    Compares the stock's 13-week return against SPY's 13-week return.
    A stock that outperforms the market shows institutional accumulation
    -- exactly what we want entering a setup.

    Scoring:
      RS vs SPY >= +15%  -> LEADER   +10  (crushing the market)
      RS vs SPY >= +5%   -> STRONG    +6  (clearly outperforming)
      RS vs SPY >=  0%   -> INLINE     0  (matching market)
      RS vs SPY >= -5%   -> LAGGING   -4  (slightly underperforming)
      RS vs SPY <  -5%   -> WEAK      -8  (consistently losing vs market)
    """
    spy_rs = r.get('_spy_rs', {})
    if not spy_rs:
        return None

    rs      = spy_rs.get('rs_vs_spy', 0)
    stk_ret = spy_rs.get('stock_ret', 0)
    spy_ret = spy_rs.get('spy_ret', 0)

    if rs >= 15:
        delta = +10
        expl  = (f'RS vs SPY: +{rs}% over 13 weeks (stock +{stk_ret}% vs SPY +{spy_ret}%). '
                 f'Market LEADER -- strong institutional accumulation signal.')
    elif rs >= 5:
        delta = +6
        expl  = (f'RS vs SPY: +{rs}% over 13 weeks (stock +{stk_ret}% vs SPY +{spy_ret}%). '
                 f'Outperforming market -- good momentum entering setup.')
    elif rs >= 0:
        delta = 0
        expl  = (f'RS vs SPY: +{rs}% over 13 weeks. Matching market -- neutral.')
    elif rs >= -5:
        delta = -4
        expl  = (f'RS vs SPY: {rs}% over 13 weeks (stock {stk_ret}% vs SPY {spy_ret}%). '
                 f'Slightly lagging market -- reduced conviction.')
    else:
        delta = -8
        expl  = (f'RS vs SPY: {rs}% over 13 weeks (stock {stk_ret}% vs SPY {spy_ret}%). '
                 f'Consistently underperforming market -- weak setup conviction.')

    return (delta, 'SPY Relative Strength', expl)



@factor
def _factor_volume_surge(r):
    """Factor 29 - Volume Surge on Breakout.

    Looks at the highest-volume bar in the last 8 weekly bars.
    A strong directional bar (up or down) with vol > 1.5x avg = institutional
    participation = setup confirmation.
    A high-vol bar in the WRONG direction = distribution / warning.

    Scoring:
      vol >= 2.0x AND direction aligned    -> +10
      vol >= 1.5x AND direction aligned    ->  +6
      vol >= 1.2x AND direction aligned    ->  +3
      vol >= 1.5x AND direction OPPOSITE   ->  -8  (distribution)
      vol >= 1.2x AND direction OPPOSITE   ->  -4
    """
    surge  = r.get("_surge_vol", {})
    if not surge:
        return None

    ratio  = surge.get("ratio", 0)
    s_dir  = surge.get("direction", "FLAT")
    is_long = "LONG" in r["Dir"]

    aligned = (is_long and s_dir == "UP") or (not is_long and s_dir == "DOWN")
    opposite = (is_long and s_dir == "DOWN") or (not is_long and s_dir == "UP")

    if ratio >= 2.0 and aligned:
        return (+10, "Volume Surge", f"Vol surge {ratio}x avg ({s_dir}) -- strong institutional participation on breakout.")
    elif ratio >= 1.5 and aligned:
        return (+6,  "Volume Surge", f"Vol surge {ratio}x avg ({s_dir}) -- good volume confirmation.")
    elif ratio >= 1.2 and aligned:
        return (+3,  "Volume Surge", f"Vol {ratio}x avg ({s_dir}) -- mild vol confirmation.")
    elif ratio >= 1.5 and opposite:
        return (-8,  "Volume Surge", f"Vol surge {ratio}x avg but direction {s_dir} -- possible distribution. Warning.")
    elif ratio >= 1.2 and opposite:
        return (-4,  "Volume Surge", f"Vol {ratio}x avg ({s_dir}) -- slightly against setup direction.")
    else:
        return (0,   "Volume Surge", f"Vol {ratio}x avg -- no strong surge signal. Neutral.")


@factor
def _factor_candle_quality(r):
    """Factor 30 - Price Action Candle Quality (last weekly bar).

    Reads the pattern of the most recent weekly close candle:
      HAMMER        -- long lower wick, small body = bullish rejection from low
      SHOOTING_STAR -- long upper wick, small body = bearish rejection from high
      BULL_ENGULF   -- current up bar engulfs previous down bar = bullish reversal
      BEAR_ENGULF   -- current down bar engulfs previous up bar = bearish reversal
      NEUTRAL       -- no clear signal

    Aligned patterns boost score. Opposite patterns warn.

    Scoring:
      aligned reversal candle  -> +8
      neutral / flat candle    ->  0
      opposite reversal candle -> -6
    """
    cp     = r.get("_candle_pattern", {})
    if not cp:
        return None

    ctype   = cp.get("type", "NEUTRAL")
    is_long = "LONG" in r["Dir"]

    bullish_candles = {"HAMMER", "BULL_ENGULF"}
    bearish_candles = {"SHOOTING_STAR", "BEAR_ENGULF"}

    if is_long and ctype in bullish_candles:
        return (+8, "Candle Pattern", f"{ctype} on weekly -- bullish rejection / reversal candle aligned with LONG setup.")
    elif not is_long and ctype in bearish_candles:
        return (+8, "Candle Pattern", f"{ctype} on weekly -- bearish rejection / reversal candle aligned with SHORT setup.")
    elif is_long and ctype in bearish_candles:
        return (-6, "Candle Pattern", f"{ctype} on weekly -- bearish candle against LONG setup. Caution.")
    elif not is_long and ctype in bullish_candles:
        return (-6, "Candle Pattern", f"{ctype} on weekly -- bullish candle against SHORT setup. Caution.")
    else:
        return (0,  "Candle Pattern", f"Candle: {ctype} -- no strong reversal signal. Neutral.")


@factor
def _factor_short_squeeze(r):
    """Factor 31 - Short Squeeze Potential.

    High short interest + LONG setup = potential for a fast short squeeze move.
    When shorts are forced to cover, price can rally 2-3x faster than usual.

    For SHORT setups: very high short interest is a WARNING -- too many shorts
    means consensus trade = crowded = vulnerable to squeeze.

    Scoring (LONG):
      Short float > 25%  -> +10  (extreme squeeze fuel)
      Short float > 15%  -> +6   (high squeeze potential)
      Short float > 8%   -> +2   (moderate)
      Short float < 3%   ->  0   (no fuel)

    Scoring (SHORT):
      Short float > 25%  -> -8   (crowded -- dangerous short)
      Short float > 15%  -> -4   (crowded short -- caution)
    """
    short_int = r.get("ShortInt", 0) or 0   # already as %, e.g. 18.5
    is_long   = "LONG" in r["Dir"]

    if short_int <= 0:
        return None

    if is_long:
        if short_int > 25:
            return (+10, "Short Squeeze", f"Short float {short_int:.1f}% -- extreme squeeze fuel. Fast move potential on LONG.")
        elif short_int > 15:
            return (+6,  "Short Squeeze", f"Short float {short_int:.1f}% -- high squeeze potential on LONG.")
        elif short_int > 8:
            return (+2,  "Short Squeeze", f"Short float {short_int:.1f}% -- moderate short interest.")
        else:
            return (0,   "Short Squeeze", f"Short float {short_int:.1f}% -- low, no squeeze dynamic.")
    else:  # SHORT
        if short_int > 25:
            return (-8,  "Short Squeeze", f"Short float {short_int:.1f}% -- very crowded short. High squeeze risk.")
        elif short_int > 15:
            return (-4,  "Short Squeeze", f"Short float {short_int:.1f}% -- crowded short. Caution.")
        elif short_int > 8:
            return (0,   "Short Squeeze", f"Short float {short_int:.1f}% -- moderate competition on short side.")
        else:
            return (+2,  "Short Squeeze", f"Short float {short_int:.1f}% -- low short interest supports short thesis.")


@factor
def _factor_cci(r):
    """
    Factor 32 -- CCI Oscillator (Commodity Channel Index, period=20).
    Course (lessons 27, 30): CCI +-200 is the key threshold.
    Below -200 = deeply oversold -> bullish for LONG entries.
    Above +200 = deeply overbought -> bullish for SHORT entries.
    -100 to +100 = neutral zone.
    """
    cci_val = r.get('_cci_val', 0.0)
    is_long = 'LONG' in r['Dir']

    if is_long:
        if cci_val <= -200:   return (+14, 'CCI', f'CCI {cci_val:.0f} -- deeply oversold (<-200), strong LONG signal')
        elif cci_val <= -100: return (+8,  'CCI', f'CCI {cci_val:.0f} -- oversold (<-100), LONG-favourable')
        elif cci_val <= 0:    return (+3,  'CCI', f'CCI {cci_val:.0f} -- mild pullback, neutral to positive')
        elif cci_val <= 100:  return (-2,  'CCI', f'CCI {cci_val:.0f} -- neutral zone, caution on LONG')
        elif cci_val <= 200:  return (-6,  'CCI', f'CCI {cci_val:.0f} -- elevated CCI, overbought caution')
        else:                 return (-10, 'CCI', f'CCI {cci_val:.0f} -- extreme overbought (>+200), avoid LONG')
    else:
        if cci_val >= 200:    return (+14, 'CCI', f'CCI {cci_val:.0f} -- deeply overbought (>+200), strong SHORT signal')
        elif cci_val >= 100:  return (+8,  'CCI', f'CCI {cci_val:.0f} -- overbought (>+100), SHORT-favourable')
        elif cci_val >= 0:    return (+3,  'CCI', f'CCI {cci_val:.0f} -- mild bounce, neutral for SHORT')
        elif cci_val >= -100: return (-2,  'CCI', f'CCI {cci_val:.0f} -- neutral zone, caution on SHORT')
        elif cci_val >= -200: return (-6,  'CCI', f'CCI {cci_val:.0f} -- dropping CCI, overbought caution')
        else:                 return (-10, 'CCI', f'CCI {cci_val:.0f} -- extreme oversold (<-200), avoid SHORT')


@factor
def _factor_rsi_divergence(r):
    """
    Factor 33 -- RSI Divergence (lesson 26).
    Course: price/RSI divergence is the strongest reversal signal.
    BULLISH divergence (LONG): price lower low, RSI higher low.
    BEARISH divergence (SHORT): price higher high, RSI lower high.
    """
    div     = r.get('_rsi_divergence', 'NONE')
    is_long = 'LONG' in r['Dir']

    if div == 'NONE':
        return None   # no divergence detected -- skip factor (neutral)

    if is_long:
        if div == 'BULLISH':
            return (+12, 'RSI Divergence', 'Bullish RSI divergence -- price lower low, RSI higher low')
        else:   # BEARISH -- bad for LONG
            return (-8,  'RSI Divergence', 'Bearish RSI divergence -- price higher high, RSI lower high (distribution)')
    else:
        if div == 'BEARISH':
            return (+12, 'RSI Divergence', 'Bearish RSI divergence -- price higher high, RSI lower high')
        else:   # BULLISH -- bad for SHORT
            return (-8,  'RSI Divergence', 'Bullish RSI divergence -- price lower low, RSI higher low (accumulation)')


@factor
def _factor_retest_window(r):
    """
    Factor 34 -- 5-Candle Retest Window (lesson 14).
    Course: wait at least 5 weekly bars after breakout before entering retest.
    Entering too early = chasing, high failure rate.
    """
    bars = r.get('_bars_since_breakout', 99)

    if bars >= 99:
        return None   # no breakout in window -- skip
    if bars < 3:
        return (-16, 'Retest Window', f'Only {bars} bar(s) since breakout -- too early, high failure risk')
    elif bars < 5:
        return (-8,  'Retest Window', f'{bars} bars since breakout -- approaching window (need >=5)')
    elif bars <= 12:
        return (+8,  'Retest Window', f'{bars} bars since breakout -- valid retest window (5-12 bars)')
    else:
        return (+3,  'Retest Window', f'{bars} bars since breakout -- late retest, still valid')



@factor
def _factor_secondary_trend(r):
    """
    Factor 35 -- Secondary Trend Validation (lessons 10, 16).
    Course: a valid setup requires:
      1. Primary uptrend (confirmed by get_trend -- already gated upstream)
      2. Intermediate correction of 8-15 bars before retest
    Too short a pullback (<5 bars) = not a true correction, probably noise.
    Too long (>20 bars) = trend may be broken, not just correcting.
    """
    bars = r.get('_bars_since_breakout', 99)  # reuse -- bars since price crossed key level
    # When bars_since_breakout = 99 it means no breakout in 12-bar window.
    # In that case look at LateEntry distance as proxy for pullback depth.
    if bars < 99:
        if bars < 5:
            return (-10, 'Secondary Trend', f'Correction only {bars} bars -- too brief, likely noise not real pullback')
        elif bars <= 12:
            return (+10, 'Secondary Trend', f'{bars}-bar correction -- healthy intermediate pullback (ideal 8-15 bars)')
        elif bars <= 20:
            return (+4,  'Secondary Trend', f'{bars}-bar correction -- extended but trend intact')
        else:
            return (-6,  'Secondary Trend', f'{bars}+ bars since breakout -- prolonged; check if trend still valid')
    # Fallback: use LateEntry (distance from key level) as depth proxy
    late_pct = r.get('LateEntry', 0) or 0
    if late_pct >= 5:
        return (+6, 'Secondary Trend', f'Price {late_pct:.1f}% from level -- meaningful pullback depth')
    return None


@factor
def _factor_chart_pattern(r):
    """
    Factor 36 -- Chart Pattern Detection (lessons 19-22).
    Detects: Flag/Pennant (tightest, most common), Cup-like base.
    Course: these patterns have the highest statistical completion rate.

    Flag: after strong rally (>10% in 5 bars), consolidation <5% range for 3-8 bars.
    Cup base: price forms a U-shape over 10-30 bars, returns near prior high.
    """
    bq = r.get('_breakout_quality', {})
    is_long = 'LONG' in r['Dir']
    direction = 'LONG' if is_long else 'SHORT'

    # Use breakout quality data as proxy for prior impulse strength
    bk = bq.get(direction)
    if not bk:
        return None

    body_ratio = bk.get('body_ratio', 0)   # breakout candle body / ATR
    vol_ratio  = bk.get('vol_ratio', 1.0)  # breakout vol / 20-bar avg

    # Strong prior move: large body (>1.5x ATR) on high volume (>1.5x avg)
    # + current price pulling back calmly (low vol on retest already scored in Factor 3)
    # = Flag/Pennant pattern
    is_flag = body_ratio >= 1.5 and vol_ratio >= 1.5

    # Moderate move with decent volume -- possible cup/base
    is_cup  = 0.8 <= body_ratio < 1.5 and vol_ratio >= 1.0

    bars = r.get('_bars_since_breakout', 99)
    fib_zone = r.get('_fib_zone', 'UNKNOWN')

    if is_flag and 3 <= bars <= 10:
        return (+12, 'Chart Pattern',
                f'Flag/Pennant: strong breakout (body {body_ratio:.1f}x ATR, vol {vol_ratio:.1f}x avg) '
                f'+ {bars}-bar calm consolidation -- high-probability pattern (lessons 19-22)')
    elif is_flag:
        return (+6, 'Chart Pattern',
                f'Strong prior breakout (flag-like) but {bars} bars since -- check if still valid')
    elif is_cup and fib_zone == 'GOLDEN_ZONE' and bars >= 10:
        return (+8, 'Chart Pattern',
                f'Cup base: moderate breakout + {bars}-bar handle + Fib golden zone -- solid base pattern')
    elif is_cup:
        return (+3, 'Chart Pattern',
                f'Possible base formation: breakout {body_ratio:.1f}x ATR, {bars} bars since')
    elif body_ratio < 0.5:
        return (-6, 'Chart Pattern',
                f'Weak prior breakout (body {body_ratio:.1f}x ATR) -- no clear pattern structure')
    return None


def calc_probability(r):
    """
    Iterate FACTORS registry. Each factor returns (delta, label, explanation) or None to skip.
    Base 50, capped [15, 92]. To add a factor: write @factor def _factor_xxx(r) -> tuple.
    """
    score   = 50.0
    factors = []
    for fn in FACTORS:
        result = fn(r)
        if result is None:
            continue
        delta, label, expl = result
        score  += delta
        factors.append((label, delta, expl))
    probability = max(15, min(92, round(score)))
    return probability, factors
