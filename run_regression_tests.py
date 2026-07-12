#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_regression_tests.py — golden-value regression suite for the engine.

Purpose: catch UNINTENDED behavior changes. Run after ANY code change:

    python run_regression_tests.py            # compare vs tests_golden.json
    python run_regression_tests.py --update   # re-baseline after an INTENDED change

Exit code 0 = all match, 1 = regression detected (diff is printed).

Design:
  - 100% offline and deterministic: yfinance/requests are stubbed BEFORE any
    ct_* import; all inputs are seeded synthetic data. Same result on any
    machine, any time of day, market open or closed.
  - Golden-file pattern: the expected values live in tests_golden.json
    (committed to git). If a change ALTERS behavior on purpose, run with
    --update and commit the new golden file together with the change — the
    git diff of tests_golden.json then documents exactly what shifted.

Covers: indicators (RSI/ATR/VWAP/swings/levels/patterns/Gann/gaps/quality/
reliability), factor registry integrity, probability compression math,
Factor 36 pattern branches, traffic-light red-flag rules, universe parsers.
"""

import json, os, sys, types

_HERE = os.path.dirname(os.path.abspath(__file__))
_GOLDEN = os.path.join(_HERE, 'tests_golden.json')
sys.path.insert(0, _HERE)

# ── Stub network modules BEFORE importing any ct_* module ──────────────
def _blocked(*a, **k):
    raise RuntimeError('network blocked in regression tests')

_yf = types.ModuleType('yfinance')
class _NoTicker:
    def __init__(self, *a, **k):
        _blocked()
_yf.Ticker = _NoTicker
_yf.download = _blocked
sys.modules['yfinance'] = _yf

_rq = types.ModuleType('requests')
_rq.get = _blocked
_rq.post = _blocked
sys.modules['requests'] = _rq

import numpy as np
import pandas as pd

import ct_indicators as ind
import ct_factors as fac
import ct_universe as uni
from ct_analysis import get_traffic_light


# ── Deterministic fixtures ──────────────────────────────────────────────
def _r4(x):
    """Round for stable cross-machine floats; handles lists/dicts/tuples."""
    if isinstance(x, (list, tuple)):
        return [_r4(v) for v in x]
    if isinstance(x, dict):
        return {k: _r4(v) for k, v in x.items()}
    if isinstance(x, (np.floating, float)):
        return round(float(x), 4)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def _df_trend(n=80, seed=7):
    """Weekly bars: downtrend, base, uptrend with pullback — generic fixture."""
    rng = np.random.RandomState(seed)
    drift = np.concatenate([
        np.linspace(100, 70, n // 3),
        np.full(n // 4, 70) + rng.randn(n // 4),
        np.linspace(70, 120, n - n // 3 - n // 4),
    ])
    noise = rng.randn(n) * 1.2
    close = drift + noise
    high  = close + np.abs(rng.randn(n)) * 1.5 + 0.5
    low   = close - np.abs(rng.randn(n)) * 1.5 - 0.5
    opn   = close + rng.randn(n) * 0.8
    vol   = (1e6 + rng.rand(n) * 5e5).astype(int)
    idx = pd.date_range('2024-01-05', periods=n, freq='W-FRI')
    return pd.DataFrame({'Open': opn, 'High': high, 'Low': low,
                         'Close': close, 'Volume': vol}, index=idx)


def _df_hs(n=60):
    """Crafted head & shoulders: shoulders ~100, head ~115, neckline ~90."""
    seg = [80, 84, 88, 92, 90,                       # rise
           94, 98, 100, 97, 92,                      # left shoulder -> neckline
           96, 103, 110, 115, 112, 104, 95, 91,      # head -> neckline
           94, 99, 101, 98, 93, 91,                  # right shoulder
           90, 89]
    close = np.array(seg + [88] * (n - len(seg)), dtype=float)[:n]
    high  = close + 1.0
    low   = close - 1.0
    vol   = np.full(n, 1_000_000)
    idx = pd.date_range('2024-06-07', periods=n, freq='W-FRI')
    return pd.DataFrame({'Open': close, 'High': high, 'Low': low,
                         'Close': close, 'Volume': vol}, index=idx)


def _df_gap(n=30):
    """Last bar gaps up 5% over the prior high."""
    close = np.linspace(50, 60, n)
    high  = close + 0.5
    low   = close - 0.5
    close[-1] = high[-2] * 1.05 + 0.5
    low[-1]   = high[-2] * 1.05
    high[-1]  = close[-1] + 0.5
    vol = np.full(n, 2_000_000)
    idx = pd.date_range('2025-01-03', periods=n, freq='W-FRI')
    return pd.DataFrame({'Open': close, 'High': high, 'Low': low,
                         'Close': close, 'Volume': vol}, index=idx)


# ── Build the "actual" results dict ─────────────────────────────────────
def build_actual():
    A = {}
    df = _df_trend()
    close, high, low = df['Close'], df['High'], df['Low']

    # -- indicators --
    A['rsi_last3']  = _r4(list(ind.rsi(close).iloc[-3:]))
    A['atr_last']   = _r4(ind.atr(high, low, close).iloc[-1])
    A['vwap_last']  = _r4(ind.calc_vwap(df))          # returns float or None
    A['cci_last']   = _r4(ind.cci(high, low, close).iloc[-1])
    A['trend']      = ind.get_trend(df)
    A['swing_lows_tail']  = _r4(ind.swing_lows(close, order=3)[-3:])
    A['swing_highs_tail'] = _r4(ind.swing_highs(close, order=3)[-3:])

    _atr = float(ind.atr(high, low, close).iloc[-1])
    _price = float(close.iloc[-1])
    _sup, _res = ind.get_levels(df, _price, _atr)
    A['level_support']    = _r4(_sup)
    A['level_resistance'] = _r4(_res)
    # invariants: support below price (allow at-the-level retest +0.5%),
    # resistance meaningfully above (role-reversal fix 25f591e)
    A['levels_sane'] = bool(_sup <= _price * 1.005 and _res > _price)

    A['support_quality'] = _r4(ind.get_support_quality(df, _sup))
    A['level_reliability'] = _r4(ind.check_level_reliability(df, _sup))
    A['gann'] = _r4(ind.check_gann_levels(df))
    A['gaps'] = _r4(ind.detect_price_gaps(_df_gap()))
    A['pattern_hs']    = _r4(ind.detect_chart_pattern(_df_hs()))
    A['pattern_trend'] = _r4(ind.detect_chart_pattern(df))
    A['macd'] = _r4(ind.calc_macd(df))
    A['bollinger'] = _r4(ind.calc_bollinger(df))
    A['time_horizon'] = _r4(ind.estimate_time_horizon(100, 130, 5.0))

    # -- factor registry integrity --
    A['n_factors'] = len(fac.FACTORS)
    A['factor_names'] = sorted(fn.__name__ for fn in fac.FACTORS)

    # -- probability compression math (exact spec) --
    _orig = fac.FACTORS
    comp = {}
    for raw in (0, 10, 15, 20, 21, 35, 50, 80, -10, -20, -35, -60):
        fac.FACTORS = [lambda r, d=raw: (d, 'stub', 'stub')]
        comp[str(raw)] = fac.calc_probability({})[0]
    fac.FACTORS = _orig
    A['compression'] = comp

    # -- Factor 36: pattern relevant only after neckline break (CMC lesson) --
    f36 = fac._factor_chart_pattern
    geo = {'_chart_pattern': {'type': 'HEAD_SHOULDERS', 'neckline': 90.0}}
    A['f36_long_intact']  = f36({'Dir': '🟢 LONG',  'Price': 95.0, **geo})[0]
    A['f36_long_broken']  = f36({'Dir': '🟢 LONG',  'Price': 85.0, **geo})[0]
    A['f36_short_intact'] = f36({'Dir': '🔴 SHORT', 'Price': 95.0, **geo})[0]
    A['f36_short_broken'] = f36({'Dir': '🔴 SHORT', 'Price': 85.0, **geo})[0]
    A['f36_cup_long'] = f36({'Dir': '🟢 LONG',
                             '_chart_pattern': {'type': 'CUP_HANDLE', 'rim': 100,
                                                'depth_pct': 20}})[0]

    # -- Factor 44: technical stock via institutional ownership (Golan) --
    f44 = fac._factor_technical_stock
    A['f44_high']    = f44({'InstOwn': 90.0})[0]
    A['f44_low']     = f44({'InstOwn': 50.0})[0]
    A['f44_mid']     = f44({'InstOwn': 75.0})
    A['f44_unknown'] = f44({'InstOwn': 0})

    # -- traffic light rules --
    def tl(prob, extra):
        base = {'Dir': '🟢 LONG', 'Earn': '-', 'SupportQ': 'STRONG'}
        base.update(extra)
        out = get_traffic_light(prob, base)
        color = out[0]
        reasons = out[-1] if isinstance(out[-1], list) else []
        return [color, len(reasons)]
    A['tl_clean_green']   = tl(80, {})
    A['tl_low_prob']      = tl(55, {})
    A['tl_earn_soon']     = tl(80, {'Earn': 'SOON!'})
    A['tl_earn_approach'] = tl(80, {'Earn': 'APPROACHING'})
    A['tl_unreliable']    = tl(80, {'_level_rel': 'UNRELIABLE'})
    A['tl_ambiguous']     = tl(80, {'_level_amb': 'AMBIGUOUS'})
    A['tl_fib_deep']      = tl(80, {'_fib_zone': 'TOO_DEEP'})
    A['tl_rsi_div']       = tl(80, {'_rsi_divergence': 'BEARISH'})
    A['tl_monthly_conf']  = tl(80, {'MonthlyTrend': 'SHORT'})
    A['tl_partial_bar']   = tl(80, {'PartialBar': True})
    A['tl_ssr']           = tl(80, {'SSR_Risk': True})
    A['tl_sweep']         = tl(80, {'_sweep': {'LONG': {'impulse': True,
                                                        'first_touch': True,
                                                        'swept_reclaimed': False}},
                                    '_fib_ret_pct': 25})
    A['tl_sweep_reclaimed'] = tl(80, {'_sweep': {'LONG': {'impulse': True,
                                                          'first_touch': True,
                                                          'swept_reclaimed': True}},
                                      '_fib_ret_pct': 25})
    A['tl_two_flags'] = tl(80, {'Earn': 'APPROACHING', '_level_amb': 'AMBIGUOUS'})
    A['tl_daily_mover'] = tl(80, {'_daily_timing': {'max_daily_move': 12.0}})
    A['tl_daily_calm']  = tl(80, {'_daily_timing': {'max_daily_move': 6.5}})

    # -- universe parsers --
    good_csv = ('iShares Fund\nHoldings as of Jul 2026\n\n'
                'Ticker,Name,Sector,Weight (%)\n'
                'AIT,Applied Industrial,Industrials,0.72\n'
                'MOG.A,Moog,Industrials,0.45\n'
                'XTSLA,Cash,Cash,0.10\nUSD,Dollar,Cash,0.05\n')
    A['ishares_csv']  = uni._parse_ishares_csv(good_csv)
    A['ishares_html'] = uni._parse_ishares_csv(
        '<!DOCTYPE html><html><body>fundTicker stuff</body></html>')
    rows = ''.join(
        f'<tr><td><a href="#">{s}</a></td><td>N{i}</td></tr>'
        for i, s in enumerate(['AHR', 'AIT', 'MOG.A'] + [f'T{j}' for j in range(120)]))
    wiki = ('<table class="wikitable"><tr><th>Name</th></tr>'
            '<tr><td>Someone</td></tr></table>'
            f'<table class="wikitable"><tr><th>Symbol</th><th>Security</th></tr>{rows}</table>')
    class _FakeResp:
        text = wiki
        def raise_for_status(self):
            pass
    _saved = uni.requests.get
    uni.requests.get = lambda *a, **k: _FakeResp()
    try:
        wk = uni._fetch_wikipedia_index('Test_page')
        A['wiki_first3'] = wk[:3]
        A['wiki_count']  = len(wk)
    finally:
        uni.requests.get = _saved

    return A


# ── Compare / update ────────────────────────────────────────────────────
def main():
    actual = build_actual()

    if '--update' in sys.argv:
        with open(_GOLDEN, 'w', encoding='utf-8') as f:
            json.dump(actual, f, indent=2, ensure_ascii=False, sort_keys=True)
        print(f'Golden file re-baselined: {len(actual)} checks -> {_GOLDEN}')
        print('Commit tests_golden.json together with the intended change.')
        return 0

    if not os.path.exists(_GOLDEN):
        print('No tests_golden.json found. Create the baseline first:')
        print('  python run_regression_tests.py --update')
        return 1

    with open(_GOLDEN, encoding='utf-8') as f:
        golden = json.load(f)

    # JSON round-trip normalizes tuples->lists etc.
    actual = json.loads(json.dumps(actual, ensure_ascii=False, sort_keys=True))

    failed = []
    for key in sorted(set(golden) | set(actual)):
        g, a = golden.get(key, '<MISSING>'), actual.get(key, '<MISSING>')
        if g != a:
            failed.append(key)
            print(f'REGRESSION  {key}')
            print(f'    golden: {g}')
            print(f'    actual: {a}')

    n = len(golden)
    if failed:
        print(f'\n{len(failed)}/{n} checks FAILED. If the change was intended,')
        print('re-baseline with: python run_regression_tests.py --update')
        return 1
    print(f'All {n} regression checks PASSED.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
