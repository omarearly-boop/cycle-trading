#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ct_analysis.py — Core analysis engine: DataFetch / SetupDetector / ResultBuilder."""
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
import webbrowser
from ct_config import (
    PORTFOLIO_SIZE, RISK_PCT, MAX_POS_PCT, MAX_OPEN_POSITIONS,
    MIN_RR, EARNINGS_WARN_DAYS, FUNDAMENTAL_TIMEOUT, MIN_PROBABILITY,
    HARD_BLOCKS, RSI_LONG_MAX, RSI_SHORT_MIN, PM_STOP_BUFFER,
    MAX_DIST_STOCK, MAX_DIST_CRYPTO, MAX_DIST_COMMODITY, MAX_DIST_INTL,
)
from ct_indicators import (
    rsi, atr, get_trend, swing_lows, swing_highs, get_levels,
    vol_declining, get_support_quality, check_level_reliability,
    check_false_breakout, check_level_ambiguity, check_swing_broken,
    calc_macd, calc_bollinger, estimate_time_horizon,
)
from ct_market_data import get_earnings, get_monthly_analysis, get_sector_rs, get_monthly_sr, get_spy_rs
from ct_factors import calc_probability, check_fibonacci_zone
from ct_learnings import load_learnings

# ══════════════════════════════════════════════════════════════
#  CORE ANALYSIS  (one function handles stocks + crypto, LONG + SHORT)
# ══════════════════════════════════════════════════════════════

def clean_ticker(ticker):
    """Return display-friendly ticker name."""
    import re
    return re.sub(r'(-USD|=F|\.[A-Z]+)$', '', ticker)

# -- Scan diagnostics (thread-safe counters) ---
import threading as _threading
_DIAG_LOCK = _threading.Lock()
_DIAG = {'no_data':0,'illiquid':0,'no_trend':0,'rsi_gate':0,'dist':0,'rr':0,'passed':0}
def _diag(key):
    with _DIAG_LOCK: _DIAG[key] += 1
def reset_diag():
    with _DIAG_LOCK:
        for k in _DIAG: _DIAG[k] = 0
def print_diag():
    d = _DIAG
    print('\n  -- Scan funnel (per ticker+direction attempt) --')
    print(f'  No data / error   : {d["no_data"]:>4}')
    print(f'  Illiquid (OTC)    : {d["illiquid"]:>4}')
    print(f'  No clear trend    : {d["no_trend"]:>4}')
    print(f'  RSI gate blocked  : {d["rsi_gate"]:>4}')
    print(f'  Too far from lvl  : {d["dist"]:>4}')
    print(f'  R:R < 2.0         : {d["rr"]:>4}')
    print(f'  Passed (setups)   : {d["passed"]:>4}')

def send_email_summary(subject, body_text, body_html=None, attachment_path=None):
    """
    Send scan summary to omarearly@gmail.com via Gmail SMTP (TLS).
    Uses App Password — set EMAIL_APP_PASSWORD in environment or below.
    How to get App Password: Google Account → Security → App Passwords.
    """
    import smtplib, os
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    # Load from .env if not already in environment
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(_env_path):
        with open(_env_path) as _ef:
            for _line in _ef:
                _line = _line.strip()
                if '=' in _line and not _line.startswith('#'):
                    _k, _v = _line.split('=', 1)
                    os.environ.setdefault(_k.strip(), _v.strip())

    SENDER   = os.environ.get('ALERT_EMAIL_FROM', 'omarearly@gmail.com')
    RECEIVER = os.environ.get('ALERT_EMAIL_TO',   'omarearly@gmail.com')
    APP_PWD  = os.environ.get('ALERT_EMAIL_PASSWORD', os.environ.get('GMAIL_APP_PASSWORD', ''))

    if not APP_PWD:
        print('  [!] Email: ALERT_EMAIL_PASSWORD not set in .env -- skipping.')
        return False
    try:
        _mime_type = 'mixed' if attachment_path else 'alternative'
        msg = MIMEMultipart(_mime_type)
        msg['Subject'] = subject
        msg['From']    = SENDER
        msg['To']      = RECEIVER
        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
        if body_html:
            msg.attach(MIMEText(body_html, 'html', 'utf-8'))
        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, 'rb') as _af:
                _part = MIMEBase('application', 'octet-stream')
                _part.set_payload(_af.read())
            encoders.encode_base64(_part)
            _fname = os.path.basename(attachment_path)
            _part.add_header('Content-Disposition', f'attachment; filename="{_fname}"')
            msg.attach(_part)
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.ehlo()
            server.starttls()
            server.login(SENDER, APP_PWD)
            server.sendmail(SENDER, RECEIVER, msg.as_string())
        print('  ✅ Email summary sent to omarearly@gmail.com')
        return True
    except Exception as e:
        print(f'  ⚠ Email send failed: {e}')
        return False


def get_fundamental_analysis(ticker, info=None):
    """
    Fetches fundamental data directly from Yahoo Finance via yfinance.
    Pass `info` (from asset.info already fetched in analyze()) to skip the HTTP call.
    Returns dict with: signal, conf, consensus, target, upside, caveats, bullets, scores.
    Only runs for US stocks (not crypto/commodity/TASE/INTL).
    """
    try:
        if info is None:
            info = yf.Ticker(ticker).info or {}
        info = info or {}

        # ── Analyst consensus → signal ────────────────────────
        rec_key = (info.get('recommendationKey') or '').lower()
        rec_map = {
            'strong_buy': ('BUY',  90),
            'buy':        ('BUY',  75),
            'hold':       ('HOLD', 55),
            'underperform': ('SELL', 40),
            'sell':       ('SELL', 30),
        }
        signal, conf = rec_map.get(rec_key, ('HOLD', 50))

        # ── Price target ──────────────────────────────────────
        a_target = info.get('targetMeanPrice') or info.get('targetMedianPrice')
        a_curr   = info.get('currentPrice') or info.get('regularMarketPrice')
        upside   = None
        if a_target and a_curr and a_curr > 0:
            upside = round((a_target - a_curr) / a_curr * 100, 1)

        # ── Analyst count & consensus label ──────────────────
        n_analysts = info.get('numberOfAnalystOpinions') or 0
        a_cons = f"{rec_key.replace('_',' ').title()} ({n_analysts} analysts)" if rec_key else '—'

        # ── Quick fundamental caveats ─────────────────────────
        caveats = []
        pe = info.get('trailingPE') or info.get('forwardPE')
        if pe and pe > 50:
            caveats.append(f'High P/E: {pe:.0f}x')
        debt_eq = info.get('debtToEquity')
        if debt_eq and debt_eq > 200:
            caveats.append(f'High D/E ratio: {debt_eq:.0f}%')
        short_pct = info.get('shortPercentOfFloat') or 0
        if short_pct > 0.20:
            caveats.append(f'High short interest: {short_pct*100:.0f}%')
        if upside and upside < -5:
            caveats.append(f'Analyst target below price ({upside:+.1f}%)')

        # ── Summary bullets ───────────────────────────────────
        bullets = []
        sector  = info.get('sector', '')
        industry= info.get('industry', '')
        mkt_cap = info.get('marketCap', 0)
        if mkt_cap:
            cap_str = f"${mkt_cap/1e9:.1f}B" if mkt_cap >= 1e9 else f"${mkt_cap/1e6:.0f}M"
            bullets.append(f"{sector} / {industry} — {cap_str} market cap")
        if upside is not None:
            bullets.append(f"Analyst target ${a_target:.2f} → {upside:+.1f}% upside")
        rev_growth = info.get('revenueGrowth')
        if rev_growth is not None:
            bullets.append(f"Revenue growth: {rev_growth*100:.1f}%")

        # ---- Extended fundamental metrics ----------------
        fwd_pe       = info.get('forwardPE')
        peg          = info.get('pegRatio')
        pb           = info.get('priceToBook')
        ps           = info.get('priceToSalesTrailingTwelveMonths')
        gross_margin = info.get('grossMargins')
        net_margin   = info.get('profitMargins')
        roe          = info.get('returnOnEquity')
        roa          = info.get('returnOnAssets')
        eps_growth   = info.get('earningsGrowth') or info.get('earningsQuarterlyGrowth')
        current_ratio= info.get('currentRatio')
        inst_own     = info.get('heldPercentInstitutions')
        insider_own  = info.get('heldPercentInsiders')
        beta         = info.get('beta')
        fcf          = info.get('freeCashflow')

        # Extended caveats
        if fwd_pe and fwd_pe > 40:
            caveats.append(f'High Forward P/E: {fwd_pe:.0f}x')
        if net_margin is not None and net_margin < 0:
            caveats.append(f'Negative net margin: {net_margin*100:.1f}%')
        if current_ratio is not None and current_ratio < 1.0:
            caveats.append(f'Low liquidity (current ratio {current_ratio:.1f})')
        if eps_growth is not None and eps_growth < -0.10:
            caveats.append(f'EPS declining: {eps_growth*100:.1f}%')

        # Extended bullets
        if gross_margin is not None:
            bullets.append(f'Gross margin: {gross_margin*100:.1f}%  |  Net margin: {net_margin*100:.1f}%' if net_margin else f'Gross margin: {gross_margin*100:.1f}%')
        if roe is not None:
            bullets.append(f'ROE: {roe*100:.1f}%' + (f'  |  ROA: {roa*100:.1f}%' if roa else ''))
        if eps_growth is not None:
            bullets.append(f'EPS growth: {eps_growth*100:+.1f}%')
        if inst_own is not None:
            bullets.append(f'Institutional: {inst_own*100:.1f}%' + (f'  |  Insider: {insider_own*100:.1f}%' if insider_own else ''))

        scores = {
            'pe':           pe,
            'fwdPE':        fwd_pe,
            'peg':          peg,
            'pb':           pb,
            'ps':           ps,
            'debtToEquity': debt_eq,
            'revenueGrowth':rev_growth,
            'epsGrowth':    eps_growth,
            'grossMargin':  gross_margin,
            'netMargin':    net_margin,
            'roe':          roe,
            'roa':          roa,
            'currentRatio': current_ratio,
            'instOwn':      inst_own,
            'insiderOwn':   insider_own,
            'beta':         beta,
            'fcf':          fcf,
            'shortPct':     short_pct,
        }

        return {
            'signal':    signal,
            'conf':      int(conf),
            'consensus': a_cons,
            'target':    a_target,
            'upside':    upside,
            'caveats':   caveats[:5],
            'bullets':   bullets[:6],
            'scores':    scores,
        }
    except Exception as e:
        print(f'  ⚠ Fundamental analysis failed for {ticker}: {e}')
        return None


def is_hard_blocked(direction, m_analysis):
    """
    Returns (True, reason) if this setup violates a hard-block rule
    and should be completely excluded — not even shown in Watchlist.
    Learned from: CS-001 BKR, CS-003 CVX, CS-005 Ford F.
    """
    if not m_analysis:
        return False, ''
    candle = m_analysis.get('candle_q', '') or ''
    for blk_dir, blk_candle, blk_reason in HARD_BLOCKS:
        if direction == blk_dir and blk_candle in candle:
            return True, blk_reason
    return False, ''


def get_traffic_light(prob, r):
    """
    Returns (color, label, reasons[]) — a traffic light for each setup.
    GREEN  = take the trade
    YELLOW = consider carefully
    RED    = do not take
    """
    red_flags   = []
    green_flags = []

    if r.get('Earn') == 'SOON!':
        red_flags.append('Earnings imminent (<14d)')
    if r.get('Earn') == 'APPROACHING':
        red_flags.append('Earnings approaching (15-30d)')
    if r.get('HighVol'):
        red_flags.append(f'High Volatility ATR {r.get("ATR_pct",0)}%')
    if r.get('LateEntry', 0) > 8:
        red_flags.append(f'Late Entry +{r.get("LateEntry",0)}% from level')
    if r.get('SectorRS') in ('WEAK-', 'BELOW'):
        red_flags.append(f'Sector RS: {r.get("SectorRS")}')
    m_trend = r.get('MonthlyTrend')
    direction = 'LONG' if '▲' in r.get('Dir','') else 'SHORT'
    if m_trend == 'SHORT' and direction == 'LONG':
        red_flags.append('Monthly trend vs direction')
    if m_trend == 'LONG' and direction == 'SHORT':
        red_flags.append('Monthly trend vs direction')

    if r.get('SupportQ') == 'STRONG':
        green_flags.append('Strong support level')
    if r.get('SectorRS') in ('STRONG+', 'ABOVE'):
        green_flags.append(f'Sector RS: {r.get("SectorRS")}')
    if r.get('MonthlyTrend') == direction[:4]:
        green_flags.append('Monthly aligned')

    n_red = len(red_flags)
    if prob >= 70 and n_red == 0:
        return 'GREEN',  '🟢 קח את העסקה',    green_flags, red_flags
    elif prob >= 65 and n_red <= 1:
        return 'YELLOW', '🟡 שקול בזהירות',   green_flags, red_flags
    else:
        return 'RED',    '🔴 אל תיכנס',       green_flags, red_flags


def calc_position_size(portfolio_size, entry, stop, atr_pct=0, high_vol=False):
    """
    Calculate proper position size with three safeguards:
    1. Risk-based sizing   — never lose more than RISK_PCT of portfolio on one trade
    2. Volatility scaling  — halve position size when ATR > 8% (HIGH_VOLATILITY)
    3. Max position cap    — never put more than MAX_POS_PCT of portfolio in one position

    Returns: units, pos_val, risk_amt, pos_pct, was_capped, cap_reason
    """
    risk_u = abs(entry - stop)
    if risk_u <= 0 or entry <= 0:
        return 0, 0, 0, 0, False, ''

    # Step 1 — base risk amount (1% of portfolio)
    base_risk = portfolio_size * RISK_PCT

    # Step 2 — halve for high-volatility stocks (ATR > 8%)
    if high_vol:
        base_risk *= 0.50

    risk_amt = base_risk
    units    = risk_amt / risk_u
    pos_val  = units * entry

    # Step 3 — cap at MAX_POS_PCT of portfolio
    max_pos_val = portfolio_size * MAX_POS_PCT
    was_capped  = False
    cap_reason  = ''
    if pos_val > max_pos_val:
        was_capped = True
        cap_reason = f'Capped at {int(MAX_POS_PCT*100)}% of portfolio'
        pos_val  = max_pos_val
        units    = pos_val / entry
        risk_amt = units * risk_u   # actual risk after cap

    pos_pct = round(pos_val / portfolio_size * 100, 1)
    return round(units, 4), round(pos_val, 2), round(risk_amt, 2), pos_pct, was_capped, cap_reason


# ── ResultBuilder helpers — called by analyze() for each setup ───────────────

def _build_setup_dict(direction, ticker, price, rsi_val, support, resistance,
                      entry, stop, target, rratio, units, pos_val, risk_amt,
                      pos_pct, was_capped, cap_reason, vol_ok, earn_warn,
                      earn_approaching, earn_date, earn_days, asset_type, score,
                      squeeze_risk, short_pct, inst_pct, atr_pct, high_volatility,
                      m_analysis, rs_info, sup_touches, sup_q, macd_data, boll_data,
                      level_rel='UNKNOWN', false_breakout=False, fb_label='',
                      level_amb='CLEAR', level_amb_n=0,
                      trend_confirmed=True, trend_conf_label='CONFIRMED',
                      fib_zone='UNKNOWN', fib_ret_pct=0,
                      fib_swing_low=0, fib_swing_high=0, fib_levels=None,
                      monthly_sr=None):
    """
    Assemble the raw setup dict from computed values.
    Pure function — no yfinance calls, no side effects.
    Separated from analyze() so the structure has locality:
    every field lives here, not scattered across 70 lines twice.
    """
    earn_str = 'SOON!' if earn_warn else ('APPROACHING' if earn_approaching else (earn_date or '-'))
    dir_label = '🟢 LONG' if direction == 'LONG' else '🔴 SHORT'
    late_ref  = support if direction == 'LONG' else resistance
    late_pct  = round(abs(price - late_ref) / late_ref * 100, 1) if late_ref else 0
    return {
        'Ticker':         clean_ticker(ticker),
        '_raw':           ticker,
        'Dir':            dir_label,
        'Price':          round(price, 2),
        'RSI':            round(rsi_val, 1),
        'Support':        round(support, 2),
        'Resist':         round(resistance, 2),
        'Entry':          round(entry, 2),
        'Stop':           round(stop, 2),
        'Target':         round(target, 2),
        'R:R':            rratio,
        'Units':          round(units, 1),
        'Risk$':          int(risk_amt),
        'Pos$':           int(pos_val),
        'PosPct':         pos_pct,
        'WasCapped':      was_capped,
        'CapReason':      cap_reason,
        'Vol':            'OK' if vol_ok else 'WARN',
        'Earn':           earn_str,
        'EarnDays':       earn_days,
        'Type':           asset_type,
        '_score':         round(score, 2),
        'SqueezeRisk':    squeeze_risk,
        'ShortInt':       round(short_pct * 100, 1),
        'InstOwn':        round(inst_pct  * 100, 1),
        'ATR_pct':        atr_pct,
        'HighVol':        high_volatility,
        'LateEntry':      late_pct,
        'MonthlyTrend':   m_analysis['trend']      if m_analysis else None,
        'MonthlyCandle':  m_analysis['candle_q']   if m_analysis else None,
        'MonthlyPct':     m_analysis['candle_pct'] if m_analysis else None,
        'SectorETF':      rs_info['etf']            if rs_info else None,
        'SectorRS':       rs_info['rs_label']       if rs_info else None,
        'RS_pct':         rs_info['rs']             if rs_info else None,
        'SectorTrend':    rs_info['sector_trend']   if rs_info else None,
        'SupportQ':       sup_q,
        'SupportTouches': sup_touches,
        'LevelRel':       level_rel,       # CLEAN / TESTED / UNRELIABLE
        '_level_rel':     level_rel,       # read by _factor_level_reliability
        '_false_breakout': false_breakout, # True = N.M.S. not satisfied
        '_fb_label':      fb_label,        # 'FALSE_BREAKOUT' / 'VALID_BREAKOUT' / 'NO_BREAKOUT'
        '_level_amb':     level_amb,       # CLEAR / CROWDED / AMBIGUOUS
        '_level_amb_n':   level_amb_n,     # number of competing levels
        '_trend_confirmed':   trend_confirmed,    # True = swing level closed through
        '_trend_conf_label':  trend_conf_label,   # 'CONFIRMED' / 'UNCONFIRMED'
        '_fib_zone':          fib_zone,           # Factor 20 — Fibonacci zone label
        '_fib_ret_pct':       fib_ret_pct,        # retracement % (0-100)
        '_fib_swing_low':     fib_swing_low,      # swing low used for fib calc
        '_fib_swing_high':    fib_swing_high,     # swing high used for fib calc
        '_fib_levels':        fib_levels or {},   # {'38.2': price, '61.8': price, ...}
        '_macd':          macd_data,
        '_boll':          boll_data,
        '_fundamental':   None,   # filled in by _finalize_setup
        'monthly_sr':     monthly_sr or {},  # Factor 24 -- Monthly S/R Confluence
    }


def _finalize_setup(setup, direction, ticker, atr_val, m_analysis,
                    is_crypto, is_commodity, is_israel, is_intl, cached_info=None):
    """
    Add probability, time horizon, fundamentals, hard-block check.
    Pass cached_info (asset.info dict) to skip the duplicate HTTP call.
    Returns the setup dict (mutated in place) or None if hard-blocked.
    """
    _is_us = not (is_crypto or is_commodity or is_israel or is_intl)
    if _is_us:
        setup['_fundamental'] = get_fundamental_analysis(clean_ticker(ticker), info=cached_info)

    prob, pfacts = calc_probability(setup)
    setup['Prob']    = prob
    setup['_pfacts'] = pfacts

    est_weeks, horizon, h_label, h_color, h_range = estimate_time_horizon(
        setup['Entry'], setup['Target'], atr_val)
    setup['EstWeeks']     = est_weeks
    setup['TimeHorizon']  = horizon
    setup['HorizonLabel'] = h_label
    setup['HorizonColor'] = h_color
    setup['HorizonRange'] = h_range

    blocked, block_reason = is_hard_blocked(direction, m_analysis)
    if blocked:
        print(f'  🚫 HARD BLOCK {clean_ticker(ticker)} {direction}: {block_reason}')
        return None

    setup['IsWatchlist'] = prob < MIN_PROBABILITY
    tl_color, tl_label, _, _ = get_traffic_light(prob, setup)
    setup['TrafficLight'] = tl_color  # 'GREEN', 'YELLOW', or 'RED'
    return setup


# ── SetupDetector — pure LONG/SHORT detection logic ──────────────────────────

def _squeeze_level(sp, ip):
    """Return squeeze risk level for a SHORT setup. Pure — no I/O."""
    if sp >= 0.15 or ip >= 1.0:
        return 'HIGH'
    if sp >= 0.10 or ip >= 0.80:
        return 'MEDIUM'
    return 'NONE'


def _calc_adx(df, period=14):
    """Compute ADX, +DI, -DI from a weekly OHLC DataFrame.

    Returns dict with: adx, plus_di, minus_di, range_pct_52, low_adx_bars.
    Returns {} if not enough data.
    """
    import numpy as np
    n = len(df)
    if n < period * 2 + 2:
        return {}

    high  = df['High'].values.astype(float)
    low   = df['Low'].values.astype(float)
    close = df['Close'].values.astype(float)

    # True Range
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i],
                    abs(high[i] - close[i - 1]),
                    abs(low[i]  - close[i - 1]))

    # Directional Movement
    plus_dm  = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        up   = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i]  = up   if (up > down   and up   > 0) else 0.0
        minus_dm[i] = down if (down > up   and down > 0) else 0.0

    # Wilder smoothing
    def wilder(arr, p):
        out = np.zeros(n)
        out[p] = arr[1:p + 1].sum()
        for i in range(p + 1, n):
            out[i] = out[i - 1] - out[i - 1] / p + arr[i]
        return out

    atr_s   = wilder(tr,       period)
    plus_s  = wilder(plus_dm,  period)
    minus_s = wilder(minus_dm, period)

    plus_di  = np.where(atr_s > 0, 100.0 * plus_s  / atr_s, 0.0)
    minus_di = np.where(atr_s > 0, 100.0 * minus_s / atr_s, 0.0)

    dx = np.where((plus_di + minus_di) > 0,
                  100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di),
                  0.0)

    adx = np.zeros(n)
    start = period * 2
    if start < n:
        adx[start] = dx[period:start].mean()
        for i in range(start + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    # 52-week high/low range as % (last 52 weekly bars ≈ 1 year)
    last52    = close[-52:] if n >= 52 else close
    hi52, lo52 = last52.max(), last52.min()
    range_pct  = round((hi52 - lo52) / lo52 * 100, 1) if lo52 > 0 else 0.0

    # How many of the last 26 bars (≈6 months) had ADX < 20
    recent_adx   = adx[-26:] if n >= 26 else adx[adx > 0]
    valid        = recent_adx[recent_adx > 0]
    low_adx_bars = int((valid < 20).sum())

    return {
        'adx':          round(float(adx[-1]),      1),
        'plus_di':      round(float(plus_di[-1]),  1),
        'minus_di':     round(float(minus_di[-1]), 1),
        'range_pct_52': range_pct,
        'low_adx_bars': low_adx_bars,
    }


def _fetch_market_data(ticker, is_crypto=False, is_commodity=False,
                       is_israel=False, is_intl=False, interval='1wk', period='2y'):
    """
    Fetcher seam — the ONLY place analyze() touches yfinance / the network.
    Returns a dict with everything the detectors need, or None if there isn't
    enough history yet or no clear trend (mirrors the original early returns).
    interval: '1d' / '1wk' / '1mo'
    period:   '1y' / '2y' / '5y'
    """
    asset = yf.Ticker(ticker)
    df    = asset.history(period=period, interval=interval, auto_adjust=True,
                          raise_errors=False)

    # Need fewer bars for monthly, more for daily
    min_bars = {'1d': 100, '1wk': 55, '1mo': 24}.get(interval, 55)
    if is_crypto: min_bars = max(20, min_bars - 20)
    if len(df) < min_bars:
        return None

    df['RSI'] = rsi(df['Close'])
    df['ATR'] = atr(df['High'], df['Low'], df['Close'])

    price   = float(df['Close'].iloc[-1])
    rsi_val = float(df['RSI'].iloc[-1]) if not pd.isna(df['RSI'].iloc[-1]) else 50.0
    atr_val = float(df['ATR'].iloc[-1]) if not pd.isna(df['ATR'].iloc[-1]) else price * 0.03

    # ── MACD + Bollinger Bands ────────────────────────────
    macd_data = calc_macd(df)
    boll_data = calc_bollinger(df)

    trend = get_trend(df)
    if trend is None:
        _diag('no_trend'); return None

    support, resistance = get_levels(df, price, atr_val)
    vol_ok = vol_declining(df)

    # Liquidity hard filter (skip OTC / penny / illiquid)
    avg_vol_20 = float(df['Volume'].rolling(20).mean().iloc[-1])
    MIN_AVG_VOL = 100_000 if not (is_crypto or is_commodity or is_israel or is_intl) else 10_000
    if avg_vol_20 < MIN_AVG_VOL:
        _diag('illiquid'); return None  # OTC / illiquid -- skip


    # Volume ratio — recent 3-bar avg vs 20-bar avg (quantitative retest signal)
    try:
        _vol_recent   = float(df['Volume'].iloc[-3:].mean())
        _vol_baseline = float(df['Volume'].iloc[-20:].mean())
        _vol_ratio    = round(_vol_recent / _vol_baseline, 2) if _vol_baseline > 0 else 1.0
    except Exception:
        _vol_ratio = 1.0

    # Directional volume: avg vol on down bars vs up bars (last 10 weekly bars)
    try:
        _recent10 = df.tail(10)
        _up_bars  = _recent10[_recent10['Close'] >= _recent10['Open']]
        _dn_bars  = _recent10[_recent10['Close'] <  _recent10['Open']]
        _up_vol   = float(_up_bars['Volume'].mean()) if len(_up_bars) > 0 else 0.0
        _dn_vol   = float(_dn_bars['Volume'].mean()) if len(_dn_bars) > 0 else 0.0
        _dir_vol_ratio = round(_dn_vol / _up_vol, 2) if _up_vol > 0 else 1.0
    except Exception:
        _dir_vol_ratio = 1.0

    # Pullback Candle Compression (Factor 25)
    # Measure the last 4 weekly candle body sizes, normalised by ATR so the
    # values are price-independent.  Body = |close - open|.
    # Shrinking bodies on approach = healthy compression (smart money absorbing).
    # Large final candle = aggressive drop = danger signal.
    try:
        _last4   = df.tail(4)
        _bodies  = [abs(float(row['Close']) - float(row['Open']))
                    for _, row in _last4.iterrows()]
        # Normalise by ATR so a 0.3 candle on a $10 stock ≠ a 0.3 candle on $500
        _norm_cb = [round(b / atr_val, 3) if atr_val > 0 else b for b in _bodies]
    except Exception:
        _norm_cb = []

    # Factor 29 — Volume Surge: biggest vol bar in last 8 weeks vs 20-bar avg
    try:
        _avg_vol20 = float(df['Volume'].tail(20).mean())
        _best_ratio = 0.0
        _best_dir   = 'FLAT'
        for _, row in df.tail(8).iterrows():
            _ratio = float(row['Volume']) / _avg_vol20 if _avg_vol20 > 0 else 0
            if _ratio > _best_ratio:
                _best_ratio = round(_ratio, 2)
                _chg = (float(row['Close']) - float(row['Open'])) / float(row['Open']) * 100
                _best_dir = 'UP' if _chg > 1.0 else ('DOWN' if _chg < -1.0 else 'FLAT')
        _surge_vol = {'ratio': _best_ratio, 'direction': _best_dir}
    except Exception:
        _surge_vol = {}

    # Factor 30 — Price Action Candle Quality (last weekly bar)
    try:
        _lb  = df.iloc[-1]
        _pb  = df.iloc[-2] if len(df) >= 2 else None
        _o, _h, _l, _c = float(_lb['Open']), float(_lb['High']), float(_lb['Low']), float(_lb['Close'])
        _body   = abs(_c - _o)
        _u_wick = _h - max(_o, _c)
        _l_wick = min(_o, _c) - _l
        _rng    = _h - _l
        _ctype  = 'NEUTRAL'
        if _rng > 0:
            _bpct = _body / _rng
            _lpct = _l_wick / _rng
            _upct = _u_wick / _rng
            if _lpct > 0.55 and _bpct < 0.35:
                _ctype = 'HAMMER'           # bullish rejection from low
            elif _upct > 0.55 and _bpct < 0.35:
                _ctype = 'SHOOTING_STAR'    # bearish rejection from high
            elif _pb is not None:
                _po, _pc = float(_pb['Open']), float(_pb['Close'])
                if _c > _o and _po > _pc and _c > _po and _o < _pc:
                    _ctype = 'BULL_ENGULF'  # bullish engulfing
                elif _c < _o and _po < _pc and _c < _po and _o > _pc:
                    _ctype = 'BEAR_ENGULF'  # bearish engulfing
        _candle_pattern = {'type': _ctype, 'body_pct': round(_body / _rng, 2) if _rng > 0 else 0}
    except Exception:
        _candle_pattern = {'type': 'NEUTRAL', 'body_pct': 0}

    # ── Earnings (stocks only) ────────────────────────────
    skip_fundamentals = is_crypto or is_commodity
    earn_date, earn_days = (None, None) if skip_fundamentals else get_earnings(asset)
    earn_warn       = earn_days is not None and 0 < earn_days < EARNINGS_WARN_DAYS
    earn_approaching = earn_days is not None and EARNINGS_WARN_DAYS <= earn_days <= 30
    atr_pct         = round(atr_val / price * 100, 2) if price else 0.0
    high_volatility = atr_pct > 8.0

    # ── NEW FILTERS ───────────────────────────────────────
    # 1. Monthly chart analysis (top-down confirmation)
    m_analysis = get_monthly_analysis(ticker, asset) if not is_crypto else None

    # 2. Relative Strength vs Sector (US stocks only — no suffix)
    _is_us_stock = (not is_crypto and not is_commodity and not is_israel and not is_intl)
    rs_info  = get_sector_rs(clean_ticker(ticker), df) if _is_us_stock else None

    # 2b. Relative Strength vs SPY — 13-week (Factor 28)
    spy_rs   = get_spy_rs(df) if _is_us_stock else {}

    # 3a. Monthly S/R confluence (Factor 24)
    monthly_sr = get_monthly_sr(ticker, asset, price) if not is_crypto else {}

    # 3. Support quality calculated per-setup below (needs direction)

    # ── Short Squeeze + cached .info (reused by get_fundamental_analysis) ──
    short_pct   = 0.0   # short interest as % of float
    inst_pct    = 0.0   # institutional ownership %
    _cached_info = None  # passed to _finalize_setup → get_fundamental_analysis
    if not skip_fundamentals:
        try:
            _cached_info = asset.info or {}
            short_pct = float(_cached_info.get('shortPercentOfFloat', 0) or 0)
            inst_pct  = float(_cached_info.get('heldPercentInstitutions', 0) or 0)
        except Exception:
            pass

    # ── Breakout Quality (Factor 26) ─────────────────────────
    # Find the bar that first crossed support (LONG) or resistance (SHORT)
    # in the last 12 bars. Score its body size vs ATR and volume vs 20-bar avg.
    try:
        _avg_vol20 = float(df['Volume'].tail(20).mean()) if len(df) >= 20 else 1.0
        _bk_quality = {}
        _bars12     = df.tail(13)   # need i-1 lookback so fetch 13
        _c  = _bars12['Close'].values.astype(float)
        _o  = _bars12['Open'].values.astype(float)
        _v  = _bars12['Volume'].values.astype(float)
        for _dir, _level in [('LONG', support), ('SHORT', resistance)]:
            _found = None
            for _i in range(1, len(_c)):
                if _dir == 'LONG'  and _c[_i] > _level and _c[_i-1] <= _level:
                    _found = _i; break
                if _dir == 'SHORT' and _c[_i] < _level and _c[_i-1] >= _level:
                    _found = _i; break
            if _found is not None:
                _body = abs(_c[_found] - _o[_found])
                _bk_quality[_dir] = {
                    'body_ratio': round(_body / atr_val, 2) if atr_val > 0 else 0.0,
                    'vol_ratio':  round(_v[_found] / _avg_vol20, 2) if _avg_vol20 > 0 else 1.0,
                }
            else:
                _bk_quality[_dir] = None   # no breakout found in window
    except Exception:
        _bk_quality = {}

    # Factor 27 — ADX Long-term Structure
    try:
        _adx_weekly = _calc_adx(df, period=14)
    except Exception:
        _adx_weekly = {}

    return {
        'df': df, 'price': price, 'rsi_val': rsi_val, 'atr_val': atr_val,
        'macd_data': macd_data, 'boll_data': boll_data, 'trend': trend,
        'support': support, 'resistance': resistance,
        'vol_ok': vol_ok, '_vol_ratio': _vol_ratio, '_dir_vol_ratio': _dir_vol_ratio,
        '_candle_bodies': _norm_cb, '_breakout_quality': _bk_quality,
        '_adx_weekly': _adx_weekly,
        '_surge_vol': _surge_vol, '_candle_pattern': _candle_pattern,
        'earn_date': earn_date, 'earn_days': earn_days, 'earn_warn': earn_warn,
        'earn_approaching': earn_approaching, 'atr_pct': atr_pct,
        'high_volatility': high_volatility, 'm_analysis': m_analysis,
        'rs_info': rs_info, 'spy_rs': spy_rs,
        'short_pct': short_pct, 'inst_pct': inst_pct,
        'cached_info': _cached_info, 'monthly_sr': monthly_sr,
    }


def _detect_setup(ticker, portfolio_size, market, is_crypto, asset_type, max_dist,
                  direction: str,
                  is_commodity=False, is_israel=False, is_intl=False):
    """
    SetupDetector seam — pure given already-fetched market data.
    No yfinance calls here.  direction is 'LONG' or 'SHORT'.
    Returns a finished setup dict, or None.

    Replaces the former _detect_long_setup / _detect_short_setup pair.
    Direction-specific logic is confined to clearly-labelled branches.
    """
    is_long = (direction == 'LONG')

    df, price, rsi_val, atr_val = market['df'], market['price'], market['rsi_val'], market['atr_val']
    support, resistance         = market['support'], market['resistance']
    vol_ok, earn_warn           = market['vol_ok'], market['earn_warn']
    earn_approaching, earn_date = market['earn_approaching'], market['earn_date']
    earn_days, atr_pct          = market['earn_days'], market['atr_pct']
    high_volatility             = market['high_volatility']
    m_analysis, rs_info         = market['m_analysis'], market['rs_info']
    spy_rs                      = market.get('spy_rs', {})
    macd_data, boll_data        = market['macd_data'], market['boll_data']
    short_pct, inst_pct         = market['short_pct'], market['inst_pct']

    # ── Trend + RSI gate ─────────────────────────────────────────
    if is_long:
        if not (market['trend'] == 'LONG' and rsi_val <= RSI_LONG_MAX):
            _diag('rsi_gate'); return None
    else:
        if not (market['trend'] == 'SHORT' and rsi_val >= RSI_SHORT_MIN):
            _diag('rsi_gate'); return None

    # ── Distance to key level ────────────────────────────────────
    key_level = support if is_long else resistance
    dist = (price - key_level) / price if is_long else (key_level - price) / price
    if dist > max_dist:
        _diag('dist'); return None

    # ── Entry / Stop / Target ────────────────────────────────────
    entry  = price
    stop   = round(support * 0.97, 4) if is_long else round(resistance * 1.03, 4)
    target = resistance if is_long else support

    if is_long and target <= entry * 1.02:
        target = round(entry + atr_val * 3, 4)
    elif not is_long and target >= entry * 0.98:
        target = round(entry - atr_val * 3, 4)

    risk_u = (entry - stop)   if is_long else (stop - entry)
    rew_u  = (target - entry) if is_long else (entry - target)
    if not (risk_u > 0 and rew_u > 0):
        return None

    rratio = round(rew_u / risk_u, 2)
    if rratio < MIN_RR:
        _diag('rr'); return None

    # ── Position Sizing (3 safeguards) ──────────────────────────
    units, pos_val, risk_amt, pos_pct, was_capped, cap_reason = \
        calc_position_size(portfolio_size, entry, stop,
                           atr_pct=atr_pct, high_vol=high_volatility)

    # ── Scoring ──────────────────────────────────────────────────
    score = rratio
    if vol_ok:                           score *= 1.25 if is_long else 1.20
    if is_long and rsi_val < 45:         score *= 1.20
    if not is_long and rsi_val > 60:     score *= 1.20
    if dist < 0.05:                      score *= 1.15
    if not earn_warn:                    score *= 1.10

    if is_long:
        # Gann quality check (stocks only, not crypto)
        if not is_crypto:
            high52 = float(df['High'].tail(52).max())
            if price < high52 * 0.50:
                score *= 0.5        # penalise but don't reject
        # High short interest = squeeze potential (bonus for LONG)
        if short_pct >= 0.15:
            score *= 1.15
        sq_lvl = 'NONE'
    else:
        # Squeeze risk penalty (SHORT only)
        sq_lvl = _squeeze_level(short_pct, inst_pct)
        if sq_lvl == 'HIGH':
            score *= 0.30           # heavy penalty — near-disqualify
        elif sq_lvl == 'MEDIUM':
            score *= 0.65

    # ── Level quality checks ─────────────────────────────────────
    # Primary level = support (LONG) / resistance (SHORT)
    # False-breakout reference = resistance (LONG) / support (SHORT)
    lvl_dir = 'up' if is_long else 'down'
    lev_touches, lev_q = get_support_quality(df, key_level)
    level_rel, _       = check_level_reliability(df, key_level)
    fb, fb_label, _    = check_false_breakout(df, resistance if is_long else support,
                                               direction=lvl_dir)
    level_amb, level_amb_n, _ = check_level_ambiguity(df, key_level, atr_val)
    tr_conf, tr_conf_lbl, _   = check_swing_broken(df, direction=lvl_dir)

    # Factor 20 — Fibonacci Retracement Zone
    fib_zone, fib_pct, fib_sl, fib_sh, fib_lvls = \
        check_fibonacci_zone(df, direction, price)

    _setup = _build_setup_dict(
        direction, ticker, price, rsi_val, support, resistance,
        entry, stop, target, rratio, units, pos_val, risk_amt,
        pos_pct, was_capped, cap_reason, vol_ok, earn_warn,
        earn_approaching, earn_date, earn_days, asset_type, score,
        sq_lvl, short_pct, inst_pct, atr_pct, high_volatility,
        m_analysis, rs_info, lev_touches, lev_q, macd_data, boll_data,
        level_rel=level_rel, false_breakout=fb, fb_label=fb_label,
        level_amb=level_amb, level_amb_n=level_amb_n,
        trend_confirmed=tr_conf, trend_conf_label=tr_conf_lbl,
        fib_zone=fib_zone, fib_ret_pct=fib_pct,
        fib_swing_low=fib_sl, fib_swing_high=fib_sh, fib_levels=fib_lvls,
        monthly_sr=market.get('monthly_sr', {}))
    # Pass quantitative volume ratio to factors (Factor 3 enhancement)
    _setup['_vol_ratio']     = market.get('_vol_ratio', 1.0)
    _setup['_dir_vol_ratio'] = market.get('_dir_vol_ratio', 1.0)
    # Factor 25 — Pullback Candle Compression
    _setup['_candle_bodies']      = market.get('_candle_bodies', [])
    # Factor 26 — Breakout Quality
    _setup['_breakout_quality']   = market.get('_breakout_quality', {})
    return _finalize_setup(_setup, direction, ticker, atr_val,
                           m_analysis, is_crypto, is_commodity,
                           is_israel, is_intl, cached_info=market['cached_info'])


# ── Backward-compatible shims ────────────────────────────────────────────────
def _detect_long_setup(ticker, portfolio_size, market, is_crypto, asset_type, max_dist,
                       is_commodity=False, is_israel=False, is_intl=False):
    return _detect_setup(ticker, portfolio_size, market, is_crypto, asset_type, max_dist,
                         'LONG', is_commodity, is_israel, is_intl)


def _detect_short_setup(ticker, portfolio_size, market, is_crypto, asset_type, max_dist,
                        is_commodity=False, is_israel=False, is_intl=False):
    return _detect_setup(ticker, portfolio_size, market, is_crypto, asset_type, max_dist,
                         'SHORT', is_commodity, is_israel, is_intl)


def analyze(ticker, portfolio_size, is_crypto=False, is_israel=False,
            is_commodity=False, is_intl=False, interval='1wk', period='2y'):
    """
    Coordinator — fetch market data once (the only network I/O), then run
    both LONG/SHORT detectors against it. ~20 lines, matches Candidate B
    in architecture-review-cycles-scanner.html.
    Returns a list of valid setups (could be LONG, SHORT, or both).
    """
    setups = []
    try:
        market = _fetch_market_data(ticker, is_crypto=is_crypto, is_commodity=is_commodity,
                                    is_israel=is_israel, is_intl=is_intl,
                                    interval=interval, period=period)
        if market is None:
            return setups
        for direction in ('LONG', 'SHORT'):
            setup = _detect_setup(
                ticker, portfolio_size, market, is_crypto, asset_type,
                MAX_DIST_STOCK, direction,
                is_commodity=is_commodity, is_israel=is_israel, is_intl=is_intl,
            )
            if setup:
                setups.append(setup)
    except Exception as e:
        pass
    return setups


# ── Backward-compatible shims ────────────────────────────────────────────────
def _detect_long_setup(ticker, portfolio_size, market, is_crypto, asset_type, max_dist,
                       is_commodity=False, is_israel=False, is_intl=False):
    return _detect_setup(ticker, portfolio_size, market, is_crypto, asset_type, max_dist,
                         'LONG', is_commodity, is_israel, is_intl)

def _detect_short_setup(ticker, portfolio_size, market, is_crypto, asset_type, max_dist,
                        is_commodity=False, is_israel=False, is_intl=False):
    return _detect_setup(ticker, portfolio_size, market, is_crypto, asset_type, max_dist,
                         'SHORT', is_commodity, is_israel, is_intl)
