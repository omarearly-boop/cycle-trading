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
import sys, warnings, logging
from datetime import datetime
import json

warnings.filterwarnings('ignore')
logging.getLogger('yfinance').setLevel(logging.CRITICAL)


def _install(pkg):
    import subprocess
    print(f"  Installing {pkg}...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])


try:    import yfinance as yf
except: _install("yfinance"); import yfinance as yf

try:    import pandas as pd
except: _install("pandas"); import pandas as pd

from ct_config import SECTOR_ETF

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
            df    = asset.history(period='2y', interval='1wk',
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
#  Earnings date fetch
# -----------------------------------------------------------------------------
def get_earnings(tkr):
    """
    Return (date_str, days_until) for the next earnings event.
    tkr is a yf.Ticker asset object (already created by the caller).
    Returns (None, None) on any failure.
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
        return str(ed), days
    except Exception:
        return None, None


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
        mdf = asset.history(period='4y', interval='1mo', auto_adjust=True, raise_errors=False)
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

        return {'trend': m_trend, 'candle_pct': last_pct, 'candle_q': candle_q}
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
            sec_df = sec_asset.history(period='3mo', interval='1wk',
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
