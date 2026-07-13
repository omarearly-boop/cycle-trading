#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ct_market_data.py -- yfinance I/O adapter

All functions in this module perform network calls (yfinance).
Nothing here is pure -- each function fetches live market data.

Seam: callers that need live data import from here.
      callers that need pure math import from ct_indicators.
      Mocking this module in tests gives a network-free test suite.
"""
import sys, os, warnings, logging
import urllib.request, urllib.error
from datetime import datetime, timedelta
from pathlib import Path
import json

warnings.filterwarnings('ignore')
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

# ---------------------------------------------------------------------------
#  Load .env (so FINNHUB_API_KEY is available without dotenv dependency)
# ---------------------------------------------------------------------------
def _load_env_vars():
    env_file = Path(__file__).parent / '.env'
    if env_file.exists():
        for line in env_file.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

_load_env_vars()


def _install(pkg):
    import subprocess
    print(f"  Installing {pkg}...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])


try:    import yfinance as yf
except: _install("yfinance"); import yfinance as yf

try:    import pandas as pd
except: _install("pandas"); import pandas as pd

from ct_config import SECTOR_ETF, YF_THROTTLE_SEC, YF_MAX_RETRIES

# -----------------------------------------------------------------------------
#  Yahoo rate-limit protection: global request spacing + backoff retry.
#  ALL yfinance network calls in this codebase should go through yf_history /
#  yf_info rather than asset.history() / asset.info directly.
# -----------------------------------------------------------------------------
import threading as _threading
import time as _time
import random as _random

_YF_GATE = _threading.Lock()
_YF_LAST = [0.0]

def _yf_throttle():
    """Enforce a minimum global spacing between Yahoo requests (thread-safe)."""
    with _YF_GATE:
        wait = _YF_LAST[0] + YF_THROTTLE_SEC - _time.time()
        if wait > 0:
            _time.sleep(wait)
        _YF_LAST[0] = _time.time()

def _is_rate_limit(e) -> bool:
    name = type(e).__name__
    return 'RateLimit' in name or 'Too Many Requests' in str(e) or '429' in str(e)

def yf_history(asset, **kwargs):
    """
    asset.history() with global throttling and exponential-backoff retry
    on Yahoo rate limits. Returns the DataFrame or None after retries.
    """
    for attempt in range(YF_MAX_RETRIES):
        _yf_throttle()
        try:
            return asset.history(**kwargs)
        except Exception as e:
            if _is_rate_limit(e) and attempt < YF_MAX_RETRIES - 1:
                pause = (2 ** attempt) * 10 + _random.uniform(0, 3)
                print(f'  ⏳ Yahoo rate limit — backing off {pause:.0f}s...')
                _time.sleep(pause)
                continue
            if _is_rate_limit(e):
                return None
            raise

def yf_info(asset) -> dict:
    """asset.info with throttling + rate-limit retry. Returns {} on failure."""
    for attempt in range(YF_MAX_RETRIES):
        _yf_throttle()
        try:
            return asset.info or {}
        except Exception as e:
            if _is_rate_limit(e) and attempt < YF_MAX_RETRIES - 1:
                pause = (2 ** attempt) * 10 + _random.uniform(0, 3)
                _time.sleep(pause)
                continue
            return {}
    return {}

# -- Sector ETF cache (persists for the lifetime of a scan run) ---------------
_SECTOR_CACHE: dict = {}        # sector_etf -> sec_df

# -- Market Regime cache (persists for the lifetime of a scan run) ------------
_MARKET_REGIME_CACHE: dict = {} # populated once per process by get_market_regime()


# -----------------------------------------------------------------------------
#  Market Regime -- SPY + QQQ weekly trend vs 20-week MA
# -----------------------------------------------------------------------------
def get_market_regime() -> dict:
    """
    Determine macro market regime based on SPY and QQQ vs their 20-week MA.

    BULL    -- both SPY and QQQ close above their 20-week MA
    BEAR    -- both below their 20-week MA
    NEUTRAL -- mixed (one above, one below)

    Cached for the duration of the process (one fetch per scan run).
    Returns dict with keys: regime, spy_price, spy_ma20, spy_above_ma,
                                       qqq_price, qqq_ma20, qqq_above_ma
    On any failure returns {'regime': 'NEUTRAL'} and logs a warning.
    """
    global _MARKET_REGIME_CACHE
    if _MARKET_REGIME_CACHE:
        return _MARKET_REGIME_CACHE

    result: dict = {}
    try:
        for tkr in ('SPY', 'QQQ'):
            asset = yf.Ticker(tkr)
            df    = yf_history(asset, period='2y', interval='1wk',
                               auto_adjust=True, raise_errors=False)
            if df is None or len(df) < 20:
                print(f"  WARNING Market regime: insufficient data for {tkr}")
                _MARKET_REGIME_CACHE = {'regime': 'NEUTRAL'}
                return _MARKET_REGIME_CACHE
            df.columns = [c.capitalize() for c in df.columns]
            price = float(df['Close'].iloc[-1])
            ma20  = float(df['Close'].rolling(20).mean().iloc[-1])
            key   = tkr.lower()
            result[f'{key}_price']    = round(price, 2)
            result[f'{key}_ma20']     = round(ma20,  2)
            result[f'{key}_above_ma'] = price > ma20

        spy_up = result.get('spy_above_ma', False)
        qqq_up = result.get('qqq_above_ma', False)
        if spy_up and qqq_up:
            regime = 'BULL'
        elif not spy_up and not qqq_up:
            regime = 'BEAR'
        else:
            regime = 'NEUTRAL'

        result['regime'] = regime
        _MARKET_REGIME_CACHE.update(result)
        spy_arrow = 'up' if spy_up else 'dn'
        qqq_arrow = 'up' if qqq_up else 'dn'
        print(f"  OK Market Regime: {regime} "
              f"(SPY {spy_arrow} ${result['spy_price']} / MA20 ${result['spy_ma20']}, "
              f"QQQ {qqq_arrow} ${result['qqq_price']} / MA20 ${result['qqq_ma20']})")
        return _MARKET_REGIME_CACHE

    except Exception as e:
        print(f"  WARNING Market regime check failed ({e}) -- defaulting NEUTRAL")
        _MARKET_REGIME_CACHE = {'regime': 'NEUTRAL'}
        return _MARKET_REGIME_CACHE


# -----------------------------------------------------------------------------
#  Earnings date fetch  (Finnhub primary, yfinance fallback)
# -----------------------------------------------------------------------------
def _finnhub_earnings(symbol: str) -> tuple:
    """
    Fetch next earnings date from Finnhub calendar API.
    Returns (date_str, days_until) or (None, None) on any failure.
    Requires FINNHUB_API_KEY in environment / .env
    """
    key = os.environ.get('FINNHUB_API_KEY', '').strip()
    if not key:
        return None, None
    today = datetime.now().date()
    end   = today + timedelta(days=90)   # look 90 days ahead
    url   = (
        f"https://finnhub.io/api/v1/calendar/earnings"
        f"?from={today}&to={end}&symbol={symbol}&token={key}"
    )
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'CyclesTrading/1.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        events = data.get('earningsCalendar', [])
        if not events:
            return None, None
        # Sort by date, pick the earliest future one
        events.sort(key=lambda e: e.get('date', ''))
        for ev in events:
            raw = ev.get('date', '')
            if not raw:
                continue
            ed   = datetime.strptime(raw, '%Y-%m-%d').date()
            days = (ed - today).days
            if days >= 0:
                return str(ed), days
        return None, None
    except Exception:
        return None, None


def _yfinance_earnings(tkr) -> tuple:
    """
    yfinance fallback for earnings date.
    tkr is a yf.Ticker asset object.
    Returns (date_str, days_until) or (None, None).
    """
    try:
        cal = tkr.calendar
        if cal is None:
            return None, None
        dates = cal.get('Earnings Date', []) if isinstance(cal, dict) else (
            cal.loc['Earnings Date'] if hasattr(cal, 'loc') and 'Earnings Date' in cal.index else []
        )
        if hasattr(dates, '__iter__') and not isinstance(dates, str):
            dates = list(dates)
            date  = dates[0] if dates else None
        else:
            date = dates
        if date is None:
            return None, None
        ed   = pd.to_datetime(date).date()
        days = (ed - datetime.now().date()).days
        if days < 0:
            return None, None
        return str(ed), days
    except Exception:
        return None, None


def get_earnings(tkr):
    """
    Return (date_str, days_until) for the next earnings event.
    tkr is a yf.Ticker asset object (already created by the caller).

    Strategy:
      1. Try Finnhub (reliable, official API) using the ticker symbol
      2. Fall back to yfinance .calendar if Finnhub has no data or no key

    Returns (None, None) on any failure.
    """
    symbol = getattr(tkr, 'ticker', None) or getattr(tkr, '_ticker', None)
    if symbol:
        date_str, days = _finnhub_earnings(symbol)
        if date_str is not None:
            return date_str, days
    # Fallback to yfinance
    return _yfinance_earnings(tkr)


# -----------------------------------------------------------------------------
#  Monthly trend analysis
# -----------------------------------------------------------------------------
def get_monthly_analysis(ticker, asset=None):
    """
    Monthly chart -- top-down trend confirmation.
    Returns dict: trend, candle_pct, candle_q  (or None on failure).

    Pass asset if you already have a yf.Ticker(ticker) to avoid a duplicate
    HTTP object -- this is the Perf-2 optimization from the refactor history.
    """
    try:
        if asset is None:
            asset = yf.Ticker(ticker)
        mdf = yf_history(asset, period='4y', interval='1mo', auto_adjust=True, raise_errors=False)
        if mdf is None or len(mdf) < 8:
            return None
        mdf.columns = [c.capitalize() for c in mdf.columns]
        close = mdf['Close']
        opens = mdf['Open']

        sma6  = float(close.rolling(6).mean().iloc[-1])
        sma12 = float(close.rolling(12).mean().iloc[-1])
        price = float(close.iloc[-1])

        if   sma6 > sma12 and price > sma12 * 0.97:  m_trend = 'LONG'
        elif sma6 < sma12 and price < sma12 * 1.03:  m_trend = 'SHORT'
        else:                                          m_trend = None

        # Last completed monthly candle (index -2 = last closed month)
        last_open  = float(opens.iloc[-2])
        last_close = float(close.iloc[-2])
        last_pct   = round((last_close - last_open) / last_open * 100, 1) if last_open else 0

        if   last_pct <= -8: candle_q = 'STRONG_BEAR'
        elif last_pct <= -3: candle_q = 'BEAR'
        elif last_pct >=  8: candle_q = 'STRONG_BULL'
        elif last_pct >=  3: candle_q = 'BULL'
        else:                candle_q = 'NEUTRAL'

        # Monthly momentum health (Sagi, ABBV lesson Jun 2026): RSI
        # divergence + volume character on the MONTHLY chart — 'volume
        # weakens on rallies and strengthens on declines' = buyers fading.
        m_rsi_div = 'NONE'
        m_dirvol  = 1.0
        try:
            from ct_indicators import rsi as _rsi_fn
            _r = _rsi_fn(close).values
            _p = close.values
            _n = len(_p)
            _sl = [i for i in range(3, _n - 1) if _p[i] == min(_p[i-3:i+2])][-3:]
            _sh = [i for i in range(3, _n - 1) if _p[i] == max(_p[i-3:i+2])][-3:]
            if (len(_sl) >= 2 and _p[_sl[-1]] < _p[_sl[-2]]
                    and _r[_sl[-1]] > _r[_sl[-2]]):
                m_rsi_div = 'BULLISH'
            elif (len(_sh) >= 2 and _p[_sh[-1]] > _p[_sh[-2]]
                    and _r[_sh[-1]] < _r[_sh[-2]]):
                m_rsi_div = 'BEARISH'
            _vol = mdf['Volume']
            _chg = close.diff()
            _dn = float(_vol[_chg < 0].tail(6).mean() or 0)
            _up = float(_vol[_chg > 0].tail(6).mean() or 0)
            if _up > 0 and _dn > 0:
                m_dirvol = round(_dn / _up, 2)
        except Exception:
            pass
        return {'trend': m_trend, 'candle_pct': last_pct, 'candle_q': candle_q,
                'rsi_div': m_rsi_div, 'dir_vol_ratio': m_dirvol}
    except Exception:
        return None


# -----------------------------------------------------------------------------
#  Sector relative strength
# -----------------------------------------------------------------------------
def get_sector_rs(ticker, df_weekly):
    """
    Relative Strength of stock vs its sector ETF (4-week return).
    Returns dict: etf, stock_ret, sector_ret, rs, rs_label, sector_trend
    (or None if ticker not in SECTOR_ETF or insufficient data).

    Uses _SECTOR_CACHE to avoid redundant yfinance calls within a scan run.
    """
    sector_etf = SECTOR_ETF.get(ticker.upper())
    if not sector_etf or df_weekly is None or len(df_weekly) < 5:
        return None
    try:
        stock_ret = float(
            (df_weekly['Close'].iloc[-1] / df_weekly['Close'].iloc[-5] - 1) * 100
        )
        sec_df = _SECTOR_CACHE.get(sector_etf)
        if sec_df is None:
            sec_asset = yf.Ticker(sector_etf)
            sec_df = yf_history(sec_asset, period='3mo', interval='1wk',
                                auto_adjust=True, raise_errors=False)
            if sec_df is None or len(sec_df) < 5:
                return None
            sec_df.columns = [c.capitalize() for c in sec_df.columns]
            _SECTOR_CACHE[sector_etf] = sec_df
        sector_ret = float(
            (sec_df['Close'].iloc[-1] / sec_df['Close'].iloc[-5] - 1) * 100
        )
        rs = round(stock_ret - sector_ret, 1)

        if   rs >=  5: rs_label = 'STRONG+'
        elif rs >=  2: rs_label = 'ABOVE'
        elif rs >= -2: rs_label = 'NEUTRAL'
        elif rs >= -5: rs_label = 'BELOW'
        else:          rs_label = 'WEAK-'

        return {
            'etf':          sector_etf,
            'stock_ret':    round(stock_ret, 1),
            'sector_ret':   round(sector_ret, 1),
            'rs':           rs,
            'rs_label':     rs_label,
            'sector_trend': 'UP' if sector_ret > 0 else 'DOWN',
        }
    except Exception:
        return None


# -----------------------------------------------------------------------------
#  Relative Strength vs SPY  (13-week / 1-quarter)
# -----------------------------------------------------------------------------
_SPY_RS_CACHE: dict = {}   # 'spy_df' -> DataFrame, cached per process

def get_spy_rs(df_weekly) -> dict:
    """
    Compare the stock's 13-week return against SPY's 13-week return.
    Returns dict: stock_ret, spy_ret, rs_vs_spy, rs_label
    or {} on any failure.

    SPY data is cached for the scan run (one fetch per process).
    Uses 13 weeks (one quarter) — longer window than sector RS (4 weeks)
    to identify structural leaders vs short-term noise.
    """
    global _SPY_RS_CACHE
    if df_weekly is None or len(df_weekly) < 14:
        return {}
    try:
        # Stock 13-week return
        stock_ret = float(
            (df_weekly['Close'].iloc[-1] / df_weekly['Close'].iloc[-14] - 1) * 100
        )

        # SPY weekly data (cached)
        spy_df = _SPY_RS_CACHE.get('spy_df')
        if spy_df is None:
            spy_asset = yf.Ticker('SPY')
            spy_df = yf_history(spy_asset, period='1y', interval='1wk',
                                auto_adjust=True, raise_errors=False)
            if spy_df is None or len(spy_df) < 14:
                return {}
            spy_df.columns = [c.capitalize() for c in spy_df.columns]
            _SPY_RS_CACHE['spy_df'] = spy_df

        spy_ret = float(
            (spy_df['Close'].iloc[-1] / spy_df['Close'].iloc[-14] - 1) * 100
        )

        rs = round(stock_ret - spy_ret, 1)

        if   rs >= 15: rs_label = 'LEADER'      # crushing the market
        elif rs >=  5: rs_label = 'STRONG'       # clearly outperforming
        elif rs >=  0: rs_label = 'INLINE'       # matching market
        elif rs >= -5: rs_label = 'LAGGING'      # slightly underperforming
        else:          rs_label = 'WEAK'         # consistently losing vs market

        return {
            'stock_ret': round(stock_ret, 1),
            'spy_ret':   round(spy_ret, 1),
            'rs_vs_spy': rs,
            'rs_label':  rs_label,
        }
    except Exception:
        return {}


# ─── Factor 40: Daily entry timing (lessons 26–27) ───────────────────────────

_DAILY_TIMING_CACHE: dict = {}   # ticker -> {'rsi': x, 'cci': y}  (per-run cache)

def get_daily_timing(ticker: str) -> dict:
    """
    Multi-timeframe entry timing (lessons 26–27): weekly chart gives the
    trend, DAILY RSI + CCI give the precise entry timing.
    Ideal LONG: daily RSI < 30 AND daily CCI < -200.

    Fetched only for tickers that already produced a setup (cheap: a few
    dozen calls per scan, not one per scanned ticker).
    Returns {'rsi': float, 'cci': float} or {} on failure.
    """
    if ticker in _DAILY_TIMING_CACHE:
        return _DAILY_TIMING_CACHE[ticker]
    result: dict = {}
    try:
        from ct_indicators import rsi, cci   # lazy import (avoids circular import)
        asset = yf.Ticker(ticker)
        ddf = yf_history(asset, period='6mo', interval='1d',
                         auto_adjust=True, raise_errors=False)
        if ddf is not None and len(ddf) >= 30:
            ddf.columns = [c.capitalize() for c in ddf.columns]
            d_rsi = rsi(ddf['Close'])
            d_cci = cci(ddf['High'], ddf['Low'], ddf['Close'])
            r_v = float(d_rsi.iloc[-1])
            c_v = float(d_cci.iloc[-1])
            if not pd.isna(r_v):
                result = {'rsi': round(r_v, 1),
                          'cci': round(c_v, 1) if not pd.isna(c_v) else 0.0}
                # SSR check (SEC Rule 201): a >=10% intraday drop vs the prior
                # close triggers the short-sale restriction for the rest of
                # that day AND the next day — shorts only on upticks, and
                # sell-stop orders may be rejected (Discord lesson, Jul 2026).
                try:
                    _cl, _lo = ddf['Close'], ddf['Low']
                    ssr = False
                    for k in (-1, -2):          # today and yesterday
                        if len(_cl) >= abs(k) + 1:
                            prev = float(_cl.iloc[k - 1])
                            if prev > 0 and (float(_lo.iloc[k]) - prev) / prev <= -0.10:
                                ssr = True
                    result['ssr'] = ssr
                except Exception:
                    result['ssr'] = False
                # Golan lesson (T/AT&T thread, Jun 2026): stocks that move
                # >10% in a single day are usually <65% institutional and
                # NOT 'technical' — do not enter. Expose the max abs daily
                # change over the fetched ~6 months for the traffic light.
                try:
                    _chg = ddf['Close'].pct_change().abs()
                    result['max_daily_move'] = round(float(_chg.max()) * 100, 1)
                except Exception:
                    pass
                # Dual-listing arbitrage profile (David, SBSW lesson,
                # Nov 2025): ADRs trading on two exchanges gap at the open
                # almost daily (the other session moved) — daily candles
                # 'don't look good' mechanically and daily-timeframe signals
                # mislead; read the weekly. Detect: open gaps >1% on >=25%
                # of the last ~120 sessions. When detected, the >10%/day
                # rule switches to INTRADAY range — a mechanical gap is not
                # 'wild mover' behaviour (Golan's rule targets the latter).
                try:
                    _op0 = ddf['Open']
                    _pc0 = ddf['Close'].shift(1)
                    _gfrac = float((((_op0 - _pc0).abs() / _pc0) > 0.01)
                                   .tail(120).mean())
                    result['arb_gaps'] = bool(_gfrac >= 0.25)
                    if result['arb_gaps']:
                        _ir = ((ddf['High'] - ddf['Low']) / _pc0).abs()
                        result['max_daily_move'] = round(float(_ir.max()) * 100, 1)
                except Exception:
                    pass
                # 'Horseshoe' turn (Golan, PNR case study, Jun 2026): the
                # last ~10 daily closes carve a U at the level — extreme
                # 2-7 sessions ago, >=1% fall into it and >=1% rise out of
                # it = buyers visibly stepping in (mirrored N for shorts).
                try:
                    _c10 = ddf['Close'].tail(10)
                    if len(_c10) == 10:
                        _lo_i = int(_c10.values.argmin())
                        _hi_i = int(_c10.values.argmax())
                        _lo_v = float(_c10.iloc[_lo_i])
                        _hi_v = float(_c10.iloc[_hi_i])
                        result['u_turn'] = bool(
                            2 <= _lo_i <= 7 and _lo_v > 0
                            and float(_c10.iloc[0])  >= _lo_v * 1.01
                            and float(_c10.iloc[-1]) >= _lo_v * 1.01)
                        result['n_turn'] = bool(
                            2 <= _hi_i <= 7 and _hi_v > 0
                            and float(_c10.iloc[0])  <= _hi_v * 0.99
                            and float(_c10.iloc[-1]) <= _hi_v * 0.99)
                except Exception:
                    pass
    except Exception:
        result = {}
    _DAILY_TIMING_CACHE[ticker] = result
    return result


# ─── Factor 24: Monthly S/R Confluence ───────────────────────────────────────

_MONTHLY_SR_CACHE: dict = {}   # ticker -> result  (cache per run)

def get_monthly_sr(ticker: str, asset, current_price: float) -> dict:
    """
    Fetch monthly OHLC and find S/R levels using the same swing-pivot logic
    as the weekly scanner.  Returns a dict with nearest monthly level and
    distance % so Factor 24 can score the confluence.

    Keys returned:
        monthly_support    float | None  -- nearest monthly support below price
        monthly_resist     float | None  -- nearest monthly resistance above price
        nearest_level      float | None  -- whichever is closer to current price
        nearest_label      str           -- 'SUPPORT' | 'RESISTANCE' | 'NONE'
        dist_pct           float         -- % distance current_price -> nearest_level
        monthly_trend      str           -- 'BULL' | 'BEAR' | 'NEUTRAL'
        fib_zone_monthly   str           -- golden zone label on monthly chart
        fib_ret_pct        float
    """
    if ticker in _MONTHLY_SR_CACHE:
        return _MONTHLY_SR_CACHE[ticker]

    result = {
        'monthly_support':  None,
        'monthly_resist':   None,
        'nearest_level':    None,
        'nearest_label':    'NONE',
        'dist_pct':         999.0,
        'monthly_trend':    'NEUTRAL',
        'fib_zone_monthly': 'UNKNOWN',
        'fib_ret_pct':      0.0,
    }

    try:
        import warnings
        import numpy as np
        warnings.filterwarnings('ignore')

        df = yf_history(asset, period='5y', interval='1mo',
                        auto_adjust=True, raise_errors=False)
        if df is None or len(df) < 18:
            _MONTHLY_SR_CACHE[ticker] = result
            return result

        df.columns = [c.capitalize() for c in df.columns]
        df = df.dropna(subset=['Close'])

        # ── Swing pivots (order=2 = needs 2 bars each side confirmed) ────────
        from ct_indicators import swing_lows, swing_highs

        lows  = swing_lows(df['Low'],  order=2)
        highs = swing_highs(df['High'], order=2)

        supports    = sorted([v for v in lows  if v < current_price * 0.985], reverse=True)
        resistances = sorted([v for v in highs if v > current_price * 1.015])

        monthly_support = supports[0]    if supports    else None
        monthly_resist  = resistances[0] if resistances else None

        # -- Which is closer? --
        d_sup = abs(current_price - monthly_support) / current_price * 100 if monthly_support else 999
        d_res = abs(current_price - monthly_resist)  / current_price * 100 if monthly_resist  else 999

        if d_sup <= d_res and monthly_support:
            nearest_level = monthly_support
            nearest_label = 'SUPPORT'
            dist_pct      = d_sup
        elif monthly_resist:
            nearest_level = monthly_resist
            nearest_label = 'RESISTANCE'
            dist_pct      = d_res
        else:
            nearest_level = None
            nearest_label = 'NONE'
            dist_pct      = 999.0

        # -- Monthly trend: last close vs 6-month MA --
        ma6 = float(df['Close'].rolling(6).mean().iloc[-1])
        last_close = float(df['Close'].iloc[-1])
        if last_close > ma6 * 1.02:
            monthly_trend = 'BULL'
        elif last_close < ma6 * 0.98:
            monthly_trend = 'BEAR'
        else:
            monthly_trend = 'NEUTRAL'

        # -- Monthly Fibonacci zone (both directions; Factor 24 picks by trade dir) --
        try:
            from ct_factors import check_fibonacci_zone
            fib_zone_mo, fib_ret_pct, _, _, _ = check_fibonacci_zone(df, 'LONG', current_price)
        except Exception:
            fib_zone_mo  = 'UNKNOWN'
            fib_ret_pct  = 0.0
        try:
            from ct_factors import check_fibonacci_zone
            fib_zone_mo_s, fib_ret_pct_s, _, _, _ = check_fibonacci_zone(df, 'SHORT', current_price)
        except Exception:
            fib_zone_mo_s = 'UNKNOWN'
            fib_ret_pct_s = 0.0

        result = {
            'monthly_support':  round(monthly_support, 2) if monthly_support else None,
            'monthly_resist':   round(monthly_resist,  2) if monthly_resist  else None,
            'nearest_level':    round(nearest_level,   2) if nearest_level   else None,
            'nearest_label':    nearest_label,
            'dist_pct':         round(dist_pct, 1),
            'monthly_trend':    monthly_trend,
            'fib_zone_monthly': fib_zone_mo,
            'fib_ret_pct':      round(fib_ret_pct, 1),
            'fib_zone_monthly_short': fib_zone_mo_s,
            'fib_ret_pct_short':      round(fib_ret_pct_s, 1),
        }

    except Exception:
        pass

    _MONTHLY_SR_CACHE[ticker] = result
    return result
