#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ct_learnings.py — Case study learnings system."""
import json, os
from datetime import datetime

# ══════════════════════════════════════════════════════════════
#  LEARNINGS SYSTEM — loads case studies from cycles_learnings.json
# ══════════════════════════════════════════════════════════════

def load_learnings():
    """Load case studies from cycles_learnings.json (same folder as script)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cycles_learnings.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cases = data.get('case_studies', [])
        lessons = data.get('global_lessons', [])
        print(f"\n  📚 Learnings loaded: {len(cases)} case studies | {len(lessons)} global rules")
        return data
    except Exception as e:
        print(f"  ⚠ Could not load learnings: {e}")
        return None

def save_case_study(ticker, direction, setup_dict, note=''):
    """Append a new case study entry to cycles_learnings.json."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cycles_learnings.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        existing_ids = [c.get('id','CS-000') for c in data.get('case_studies',[])]
        max_n = max((int(i.split('-')[1]) for i in existing_ids if '-' in i), default=0)
        new_id = f"CS-{max_n+1:03d}"
        entry = {
            'id':        new_id,
            'date':      datetime.now().strftime('%Y-%m-%d'),
            'ticker':    ticker,
            'direction': direction,
            'entry':     setup_dict.get('Entry'),
            'stop':      setup_dict.get('Stop'),
            'target':    setup_dict.get('Target'),
            'rr':        setup_dict.get('R:R'),
            'prob':      setup_dict.get('Prob'),
            'atr_pct':   setup_dict.get('ATR_pct'),
            'earn_days': setup_dict.get('EarnDays'),
            'monthly':   setup_dict.get('MonthlyTrend'),
            'candle':    setup_dict.get('MonthlyCandle'),
            'sector_rs': setup_dict.get('SectorRS'),
            'support_q': setup_dict.get('SupportQ'),
            'outcome':   'PENDING',
            'note':      note,
            'lessons':   []
        }
        data.setdefault('case_studies', []).append(entry)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return new_id
    except Exception as e:
        return None

LEARNINGS = load_learnings()   # loaded once at startup


