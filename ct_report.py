#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ct_report.py — Profit potential breakdown."""
import os
from ct_config import RISK_PCT

# ══════════════════════════════════════════════════════════════
#  PROFIT POTENTIAL BREAKDOWN
# ══════════════════════════════════════════════════════════════

def profit_breakdown(results, portfolio, risk_trade):
    """
    For every setup found, print a full profit/loss analysis:
    - Exact $ profit if target hit (T1, T2, T3)
    - Exact $ loss if stop hit
    - % gain on portfolio
    - Scenarios: Win / Lose / Partial exit
    - Weekly compounding projection (4 weeks)
    """
    print()
    print("=" * 70)
    print("  PROFIT POTENTIAL ANALYSIS — per setup")
    print("=" * 70)

    for i, r in enumerate(results, 1):
        ticker  = r['Ticker']
        direc   = 'LONG' if 'LONG' in r['Dir'] else 'SHORT'
        entry   = r['Entry']
        stop    = r['Stop']
        target  = r['Target']   # T1
        units   = r['Units']
        pos_val = r['Pos$']
        rr      = r['R:R']
        rsi_v   = r['RSI']
        typ     = r['Type']

        # Risk and reward per unit — Fibonacci extensions (Cycles Trading)
        if direc == 'LONG':
            risk_per_unit = entry - stop
            rew_t1        = target - entry
            t2            = round(entry + rew_t1 * 1.618, 2)  # Fib 161.8%
            t3            = round(entry + rew_t1 * 2.618, 2)  # Fib 261.8%
            rew_t2        = t2 - entry
            rew_t3        = t3 - entry
        else:
            risk_per_unit = stop - entry
            rew_t1        = entry - target
            t2            = round(entry - rew_t1 * 1.618, 2)  # Fib 161.8%
            t3            = round(entry - rew_t1 * 2.618, 2)  # Fib 261.8%
            rew_t2        = entry - t2
            rew_t3        = entry - t3

        # Dollar amounts
        loss_total    = round(risk_per_unit * units, 2)
        profit_t1     = round(rew_t1 * units, 2)
        profit_t2     = round(rew_t2 * units, 2)
        profit_t3     = round(rew_t3 * units, 2)
        profit_half   = round(profit_t1 * 0.5, 2)   # partial exit at T1

        # % of portfolio
        pct_loss      = round(loss_total / portfolio * 100, 2)
        pct_t1        = round(profit_t1  / portfolio * 100, 2)
        pct_t2        = round(profit_t2  / portfolio * 100, 2)
        pct_t3        = round(profit_t3  / portfolio * 100, 2)

        # Compounding: what happens if same R:R repeated over 4 weeks
        port_after = portfolio
        weekly_wins = []
        for w in range(1, 5):
            gain = port_after * RISK_PCT * rr
            port_after = round(port_after + gain, 2)
            weekly_wins.append(port_after)

        prob      = r.get('Prob', '?')
        prob_bar  = '#' * int(prob / 5) + '-' * (20 - int(prob / 5)) if isinstance(prob, int) else '?'
        sq_lvl    = r.get('SqueezeRisk', 'NONE')
        short_int = r.get('ShortInt', 0)
        inst_own  = r.get('InstOwn', 0)

        print()
        print(f"  #{i}  {ticker}  [{direc}]  {typ}  |  Entry ${entry}  |  RSI {rsi_v}")
        print(f"  Success probability: {prob}%  [{prob_bar}]")
        if direc == 'SHORT' and sq_lvl != 'NONE':
            warn = '🚨 HIGH RISK — Consider skipping!' if sq_lvl == 'HIGH' else '⚠  Manage size carefully'
            print(f"  *** SQUEEZE RISK {sq_lvl}: Short Interest {short_int}%  |  Institutional {inst_own}%  — {warn}")
        elif direc == 'LONG' and short_int >= 15:
            print(f"  >>> Squeeze Potential: Short Interest {short_int}% — forced covering could boost move")
        print(f"  " + "-" * 66)

        # Scenario table
        print(f"  {'SCENARIO':<28} {'PRICE':>10} {'PROFIT/LOSS':>12} {'% of Portfolio':>16}")
        print(f"  {'-'*28} {'-'*10} {'-'*12} {'-'*16}")
        print(f"  {'Stop hit (full loss)':<28} {'$'+str(stop):>10} {'-$'+str(abs(loss_total)):>12} {'-'+str(pct_loss)+'%':>16}")
        print(f"  {'Target T1 (full exit)':<28} {'$'+str(target):>10} {'+$'+str(profit_t1):>12} {'+'+str(pct_t1)+'%':>16}")
        print(f"  {'Target T1 (half exit)':<28} {'$'+str(target):>10} {'+$'+str(profit_half):>12} {'+'+str(round(pct_t1/2,2))+'%':>16}")
        print(f"  {'Target T2 (Fib 161.8%)':<28} {'$'+str(t2):>10} {'+$'+str(profit_t2):>12} {'+'+str(pct_t2)+'%':>16}")
        print(f"  {'Target T3 (Fib 261.8%)':<28} {'$'+str(t3):>10} {'+$'+str(profit_t3):>12} {'+'+str(pct_t3)+'%':>16}")

        # Weekly compounding projection
        print()
        print(f"  COMPOUNDING PROJECTION (if same setup repeats each week):")
        print(f"  {'Week':<8} {'Portfolio After Win':>20} {'Total Gain':>14}")
        print(f"  {'-'*8} {'-'*20} {'-'*14}")
        for w, val in enumerate(weekly_wins, 1):
            gain_total = round(val - portfolio, 2)
            print(f"  {'Week '+str(w):<8} {'$'+f'{val:,.2f}':>20} {'+$'+f'{gain_total:,.2f}':>14}")

        print()
        print(f"  Position size: ${pos_val:,}   |   Units: {units}   |   R:R: 1:{rr}")
        print(f"  Risk amount  : ${abs(loss_total):,}  ({pct_loss}% of portfolio)")
        print(f"  " + "=" * 66)


