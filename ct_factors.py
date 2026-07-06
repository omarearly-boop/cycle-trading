#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ct_factors.py — 20-factor registry + calc_probability()."""
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
from ct_indicators import check_fibonacci_zone

# ══════════════════════════════════════════════════════════════
#  FACTOR REGISTRY — each factor is a pure function (r) → (delta, label, explanation)
#  To add Factor 17: write a _factor_xxx function, append to FACTORS list below.
#  To disable a factor: remove it from FACTORS (no other change needed).
# ══════════════════════════════════════════════════════════════

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

def _factor_rr(r):
    v = r['R:R']
    if v >= 4.0:   return +14, "R:R Ratio", f"R:R 1:{v} — excellent room to target"
    elif v >= 3.0: return +10, "R:R Ratio", f"R:R 1:{v} — strong setup"
    elif v >= 2.5: return +6,  "R:R Ratio", f"R:R 1:{v} — solid"
    else:          return +2,  "R:R Ratio", f"R:R 1:{v} — minimum threshold"

def _factor_volume(r):
    if r['Vol'] == 'OK': return +10, "Volume", "Volume declining near level — accumulation signal"
    else:                return -6,  "Volume", "Volume not declining — less conviction"

def _factor_entry_distance(r):
    is_long = 'LONG' in r['Dir']
    key_level = r['Support'] if is_long else r['Resist']
    dist_pct  = abs(r['Price'] - key_level) / r['Price'] * 100
    if dist_pct <= 2:   return +14, "Entry Distance", f"Only {dist_pct:.1f}% from key level — near-perfect entry"
    elif dist_pct <= 5: return +9,  "Entry Distance", f"{dist_pct:.1f}% from key level — good entry"
    elif dist_pct <= 8: return +4,  "Entry Distance", f"{dist_pct:.1f}% from key level — acceptable"
    elif dist_pct <= 12:return  0,  "Entry Distance", f"{dist_pct:.1f}% from key level — stretched"
    else:               return -8,  "Entry Distance", f"{dist_pct:.1f}% from key level — too far"

def _factor_earnings(r):
    earn = r['Earn']
    if earn == 'SOON!':        return -14, "Earnings Risk", "Earnings report soon — high volatility risk"
    elif earn and earn != '-': return +3,  "Earnings Risk", f"Next earnings: {earn} — safe window"
    else:                      return +5,  "Earnings Risk", "No earnings concern"

def _factor_setup_quality(r):
    v = r.get('_score', 2.0)
    if v >= 6.0:   return +8,  "Setup Quality", f"Setup score {v:.1f} — high-quality signal"
    elif v >= 4.0: return +4,  "Setup Quality", f"Setup score {v:.1f} — good signal"
    elif v >= 2.5: return +1,  "Setup Quality", f"Setup score {v:.1f} — average"
    else:          return -3,  "Setup Quality", f"Setup score {v:.1f} — weak signal"

def _factor_stop_distance(r):
    stop_pct = abs(r['Entry'] - r['Stop']) / r['Entry'] * 100
    if stop_pct <= 4:   return +6, "Stop Distance", f"Stop {stop_pct:.1f}% away — tight, controlled risk"
    elif stop_pct <= 8: return +3, "Stop Distance", f"Stop {stop_pct:.1f}% away — normal"
    elif stop_pct <= 12:return  0, "Stop Distance", f"Stop {stop_pct:.1f}% away — wide"
    else:               return -5, "Stop Distance", f"Stop {stop_pct:.1f}% away — very wide stop"

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

def _factor_support_quality(r):
    sup_q = r.get('SupportQ')
    if not sup_q:
        return None
    touches = r.get('SupportTouches', 1)
    if   sup_q == 'STRONG': return +10, "Support Quality", f"Support tested {touches}x — proven level"
    elif sup_q == 'MEDIUM': return +4,  "Support Quality", f"Support tested {touches}x — reasonable level"
    else:                   return -8,  "Support Quality", "Support tested once — unproven"

def _factor_atr_volatility(r):
    v = r.get('ATR_pct', 0)
    if v <= 0: return None
    if v > 12:   return -18, "Volatility (ATR)", f"ATR {v:.1f}% — extreme volatility"
    elif v > 8:  return -10, "Volatility (ATR)", f"ATR {v:.1f}% — high volatility, smaller position"
    elif v > 5:  return  0,  "Volatility (ATR)", f"ATR {v:.1f}% — normal volatility"
    else:        return +4,  "Volatility (ATR)", f"ATR {v:.1f}% — low volatility, easy stop"

def _factor_earnings_zone(r):
    if r.get('Earn') == 'APPROACHING' and r.get('EarnDays'):
        return -8, "Earnings Zone", f"Earnings in {r['EarnDays']} days — event risk (15–30d zone)"
    return None

def _factor_late_entry(r):
    if 'LONG' not in r['Dir']: return None
    v = r.get('LateEntry', 0)
    if v > 8:   return -15, "Late Entry", f"Price {v:.1f}% above support — likely missed retest"
    elif v > 5: return -8,  "Late Entry", f"Price {v:.1f}% above support — entry less optimal"
    return None

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
    Uses the last 52-bar swing high/low to measure the prior move.

    Returns (zone, ret_pct, swing_low, swing_high, fib_levels_dict)
    zone: 'GOLDEN_ZONE' | 'SHALLOW' | 'DEEP' | 'TOO_DEEP' | 'NO_RETRACEMENT' | 'UNKNOWN'
    """
    try:
        look = df.tail(min(52, len(df)))
        if direction == 'LONG':
            swing_high = float(look['High'].max())
            hi_idx     = look['High'].idxmax()
            before_hi  = look.loc[:hi_idx]
            if len(before_hi) < 5:
                return 'UNKNOWN', 0, 0, 0, {}
            swing_low  = float(before_hi['Low'].min())
            move       = swing_high - swing_low
            if move <= 0:
                return 'UNKNOWN', 0, 0, 0, {}
            retracement = (swing_high - price) / move
        else:  # SHORT
            swing_low = float(look['Low'].min())
            lo_idx    = look['Low'].idxmin()
            before_lo = look.loc[:lo_idx]
            if len(before_lo) < 5:
                return 'UNKNOWN', 0, 0, 0, {}
            swing_high = float(before_lo['High'].max())
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


def _factor_trend_confirmation(r):
    """
    Factor 19 — Trend Confirmation (the MELI lesson).

    Cycles Trading principle: the last confirmed weekly swing low (SHORT) or
    swing high (LONG) must have been CLOSED through — not merely wicked.
    If the swing level still holds, the move is a CORRECTION inside the prior
    trend, not a new confirmed trend. Wait for the close; don't anticipate.

    Expert rule: "כל עוד השפל האחרון מחזיק, אין אינדקציה ראשונית לשינוי מגמה"

      CONFIRMED   → +10  (swing level was closed through — real trend)
      UNCONFIRMED → -18  (swing level holds — likely correction, wait)
    """
    confirmed = r.get('_trend_confirmed', True)
    label     = r.get('_trend_conf_label', 'CONFIRMED')
    direction = r.get('Direction', 'LONG')
    swing_word = 'low' if direction == 'SHORT' else 'high'

    if confirmed:
        return (+10, f'TrendConf: {label}',
                f'Trend structure confirmed — last swing {swing_word} closed through')
    else:
        return (-18, f'TrendConf: {label}',
                f'Last swing {swing_word} not closed through — may be a correction; '
                f'wait for weekly close confirmation before entering')


# ── Registry: add / remove / reorder factors here ────────────
FACTORS = [
    _factor_rsi,
    _factor_rr,
    _factor_volume,
    _factor_entry_distance,
    _factor_earnings,
    _factor_setup_quality,
    _factor_stop_distance,
    _factor_monthly_trend,
    _factor_sector_rs,
    _factor_support_quality,
    _factor_atr_volatility,
    _factor_earnings_zone,
    _factor_late_entry,
    _factor_fundamentals,
    _factor_macd,
    _factor_bollinger,
    _factor_level_reliability,   # Factor 17 — Level Reliability + N.M.S.
    _factor_level_ambiguity,     # Factor 18 — Level Ambiguity (crowded zone)
    _factor_trend_confirmation,  # Factor 19 — Trend Confirmation (swing broken by weekly close)
    _factor_fibonacci,           # Factor 20 — Fibonacci Retracement Zone (Discord lessons May–Jun 2026)
]

def calc_probability(r):
    """
    Iterate FACTORS registry. Each factor returns (delta, label, explanation) or None to skip.
    Base 50, capped [15, 92]. To add Factor 17: write _factor_xxx, append to FACTORS.
    """
    score   = 50.0
    factors = []
    for fn in FACTORS:
        result = fn(r)
        if result is None:
            continue
        d, label, explain = result
        score += d
        factors.append((label, d, explain))
    probability = max(15, min(92, round(score)))
    return probability, factors


