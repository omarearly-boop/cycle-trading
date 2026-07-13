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

    # -- level rehabilitation (Sagi TDY lesson) --
    def _rel(closes, level=100.0):
        _d = pd.DataFrame({'Close': closes})
        return ind.check_level_reliability(_d, level, lookback=52)[0]
    # broken both ways early, then 20 bars above INCLUDING a held retest -> TESTED
    A['rel_rehab'] = _rel([105]*4 + [95]*3 + [106]*10 + [101, 100.5] + [107]*12)
    # broken both ways, stayed far above but NEVER retested -> still UNRELIABLE
    A['rel_no_touch'] = _rel([105]*4 + [95]*3 + [110]*20)
    # recent re-cross -> UNRELIABLE
    A['rel_recent_cross'] = _rel([105]*10 + [95]*5 + [106]*5 + [94]*3 + [105]*4)
    # never broken -> CLEAN
    A['rel_clean'] = _rel([106 + i*0.1 for i in range(30)])

    # -- confirmed-swing trailing (Eli RKLB lesson) --
    _rklb_pre  = [42, 48, 55, 65, 73, 70, 66, 68, 70]        # low formed, peak 73 NOT broken
    _rklb_post = [42, 48, 55, 65, 73, 70, 66, 68, 70, 76]    # 76 breaks the peak -> confirmed
    A['conf_low_unbroken'] = ind.last_confirmed_swing_low(_rklb_pre)
    A['conf_low_broken']   = ind.last_confirmed_swing_low(_rklb_post)
    A['conf_high_short']   = ind.last_confirmed_swing_high(
        [100, 92, 85, 80, 84, 88, 86, 82, 76])               # bounce high 88, trough 80 broken by 76

    # -- stop placement (stop-candle rule + ATR-aware buffer, BG lesson) --
    A['stop_bg_case']   = ind.calc_stop_long(108.4, 106.0, 8.54)   # ATR buffer dominates
    A['stop_low_vol']   = ind.calc_stop_long(100.0, 99.0, 2.0)     # 3% dominates (old rule)
    A['stop_deep_wick'] = ind.calc_stop_long(100.0, 94.0, 2.0)     # wick deeper than buffer
    A['stop_short_bg']  = ind.calc_stop_short(108.4, 111.0, 8.54)
    A['stop_no_atr']    = ind.calc_stop_long(100.0, 99.0, 0)       # ATR unknown -> 3%

    # -- fib protection in the stop (Yosef SOFI lesson) --
    A['stop_fib_tuck']    = ind.calc_stop_long(100.0, 99.0, 2.0,
                                               fib_levels_below=[96.8])  # cheap tuck
    A['stop_fib_too_far'] = ind.calc_stop_long(100.0, 99.0, 2.0,
                                               fib_levels_below=[94.0])  # would 'double' -> skip
    A['stop_fib_already_covered'] = ind.calc_stop_long(100.0, 99.0, 2.0,
                                                       fib_levels_below=[98.0])  # above stop
    A['stop_fib_short']   = ind.calc_stop_short(100.0, 101.0, 2.0,
                                                fib_levels_above=[103.5])

    # -- fib anchor selection (Shlomo K., MDGL lesson) --
    # MDGL-like: real correction to 12 (blue), run-up with only a SHALLOW
    # dip to 24 (red, <38.2% of the leg), top at 30 -> anchor must step
    # down past the red low to the blue low.
    _mdgl_low  = [14,13,12,12.5,14, 16,18,20,22,25.8, 24,24.5,26,28,29, 29.5]
    _mdgl_high = [16,15,13.5,14,16, 18,20,22,24,26,   25,26,28,30,29.5, 29.8]
    _mdgl = pd.DataFrame({'Low': _mdgl_low, 'High': _mdgl_high,
                          'Close': [(a+b)/2 for a, b in zip(_mdgl_low, _mdgl_high)],
                          'Open':  [(a+b)/2 for a, b in zip(_mdgl_low, _mdgl_high)],
                          'Volume': [1e6]*len(_mdgl_low)},
                         index=pd.date_range('2025-01-03', periods=len(_mdgl_low), freq='W-FRI'))
    _fz = fac.check_fibonacci_zone(_mdgl, 'LONG', 27.0)
    A['fib_anchor_mdgl'] = _r4([_fz[0], _fz[2], _fz[3]])   # zone, swing_low, swing_high
    # control: the dip to 24 made DEEP (down to 18 = >38.2% of the leg) ->
    # recent low becomes a valid anchor
    _deep_low  = [14,13,12,12.5,14, 16,18,20,22,25.8, 18,19,26,28,29, 29.5]
    _deep_high = [16,15,13.5,14,16, 18,20,22,24,26,   20,21,28,30,29.5, 29.8]
    _mdgl2 = pd.DataFrame({'Low': _deep_low, 'High': _deep_high,
                           'Close': [(a+b)/2 for a, b in zip(_deep_low, _deep_high)],
                           'Open':  [(a+b)/2 for a, b in zip(_deep_low, _deep_high)],
                           'Volume': [1e6]*len(_deep_low)},
                          index=pd.date_range('2025-01-03', periods=len(_deep_low), freq='W-FRI'))
    _fz2 = fac.check_fibonacci_zone(_mdgl2, 'LONG', 27.0)
    A['fib_anchor_deep_dip'] = _r4([_fz2[0], _fz2[2], _fz2[3]])

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
    # HCA refinement: broken neckline credit scales with break volume
    A['f36_broken_lowvol']  = f36({'Dir': '🔴 SHORT', 'Price': 85.0,
                                   '_vol_ratio': 0.8, **geo})[0]
    A['f36_broken_highvol'] = f36({'Dir': '🔴 SHORT', 'Price': 85.0,
                                   '_vol_ratio': 1.6, **geo})[0]
    A['f36_long_broken_lowvol'] = f36({'Dir': '🟢 LONG', 'Price': 85.0,
                                       '_vol_ratio': 0.8, **geo})[0]
    A['f36_cup_long'] = f36({'Dir': '🟢 LONG',
                             '_chart_pattern': {'type': 'CUP_HANDLE', 'rim': 100,
                                                'depth_pct': 20}})[0]

    # -- Factor 44: technical stock via institutional ownership (Golan) --
    f44 = fac._factor_technical_stock
    A['f44_high']    = f44({'InstOwn': 90.0})[0]
    A['f44_low']     = f44({'InstOwn': 50.0})[0]
    A['f44_mid']     = f44({'InstOwn': 75.0})
    A['f44_unknown'] = f44({'InstOwn': 0})

    # -- Factor 45: last candle tested the level (Golan AMZN lesson) --
    f45 = fac._factor_level_tested
    _cpb = {'low': 234.5, 'high': 247.0, 'close': 246.0}
    A['f45_tested_hold'] = f45({'Dir': '🟢 LONG', 'Support': 235.0,
                                '_candle_pattern': _cpb})[0]
    A['f45_no_touch']    = f45({'Dir': '🟢 LONG', 'Support': 225.0,
                                '_candle_pattern': _cpb})
    A['f45_closed_below'] = f45({'Dir': '🟢 LONG', 'Support': 250.0,
                                 '_candle_pattern': _cpb})
    A['f45_short_tested'] = f45({'Dir': '🔴 SHORT', 'Resist': 246.5,
                                 '_candle_pattern': _cpb})[0]
    A['f45_missing_ohlc'] = f45({'Dir': '🟢 LONG', 'Support': 235.0,
                                 '_candle_pattern': {'type': 'HAMMER'}})

    # -- Factor 46: daily horseshoe turn (Golan PNR lesson) --
    f46 = fac._factor_daily_uturn
    A['f46_long_u']   = f46({'Dir': '🟢 LONG',  '_daily_timing': {'u_turn': True,  'n_turn': False}})[0]
    A['f46_long_n']   = f46({'Dir': '🟢 LONG',  '_daily_timing': {'u_turn': False, 'n_turn': True}})[0]
    A['f46_short_n']  = f46({'Dir': '🔴 SHORT', '_daily_timing': {'u_turn': False, 'n_turn': True}})[0]
    A['f46_no_turn']  = f46({'Dir': '🟢 LONG',  '_daily_timing': {'u_turn': False, 'n_turn': False}})
    A['f46_no_data']  = f46({'Dir': '🟢 LONG',  '_daily_timing': {}})

    # -- U/N-turn detection math (same logic as get_daily_timing) --
    def _turns(vals):
        c10 = pd.Series(vals, dtype=float)
        lo_i, hi_i = int(c10.values.argmin()), int(c10.values.argmax())
        lo_v, hi_v = float(c10.iloc[lo_i]), float(c10.iloc[hi_i])
        u = bool(2 <= lo_i <= 7 and lo_v > 0 and float(c10.iloc[0]) >= lo_v * 1.01
                 and float(c10.iloc[-1]) >= lo_v * 1.01)
        n = bool(2 <= hi_i <= 7 and hi_v > 0 and float(c10.iloc[0]) <= hi_v * 0.99
                 and float(c10.iloc[-1]) <= hi_v * 0.99)
        return [u, n]
    A['uturn_pnr_like'] = _turns([76, 74.5, 73, 71.5, 70, 70.5, 71.5, 72.5, 74, 75])
    A['uturn_downtrend'] = _turns([80, 79, 78, 77, 76, 75, 74, 73, 72, 71])
    A['uturn_ntop'] = _turns([70, 72, 74, 76, 77, 76.5, 75, 74, 72, 71])

    # -- Factor 47: monthly momentum health (Sagi ABBV lesson) --
    f47 = fac._factor_monthly_momentum_health
    A['f47_abbv_case'] = f47({'Dir': '🟢 LONG', 'MonthlyRSIDiv': 'BEARISH',
                              'MonthlyDirVol': 1.5})[0]      # div + adverse vol
    A['f47_div_only']  = f47({'Dir': '🟢 LONG', 'MonthlyRSIDiv': 'BEARISH',
                              'MonthlyDirVol': 1.0})[0]
    A['f47_healthy']   = f47({'Dir': '🟢 LONG', 'MonthlyRSIDiv': 'NONE',
                              'MonthlyDirVol': 0.6})[0]
    A['f47_neutral']   = f47({'Dir': '🟢 LONG', 'MonthlyRSIDiv': 'NONE',
                              'MonthlyDirVol': 1.0})
    A['f47_short_adverse'] = f47({'Dir': '🔴 SHORT', 'MonthlyRSIDiv': 'BULLISH',
                                  'MonthlyDirVol': 0.6})[0]

    # -- dual-listing arbitrage guard (David SBSW lesson) --
    A['f40_arb_gated'] = fac._factor_daily_timing(
        {'Dir': '🟢 LONG', '_daily_timing': {'arb_gaps': True, 'rsi': 25.0, 'cci': -250}})[0]
    A['f40_normal']    = fac._factor_daily_timing(
        {'Dir': '🟢 LONG', '_daily_timing': {'arb_gaps': False, 'rsi': 25.0, 'cci': -250}})[0]
    A['f46_arb_gated'] = fac._factor_daily_uturn(
        {'Dir': '🟢 LONG', '_daily_timing': {'arb_gaps': True, 'u_turn': True, 'n_turn': False}})

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
