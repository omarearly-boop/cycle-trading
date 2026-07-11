#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ct_taxonomy.py — single source of truth for the Discord-learning domain
vocabulary: trading keywords, mentor identification, and the
concept → Factor mapping used to classify student/mentor exchanges.

Why this file exists
---------------------
Before this module, discord_monitor.py and generate_discord_report.py each
hand-maintained their own copy of KEYWORDS_HE/EN, MENTOR_NAMES, and a
concept-detection regex table. They had already drifted: report.py's table
recognized 'risk_reward' and 'stop_candle' that monitor.py's didn't, and
monitor.py's MENTOR_NAMES included Hebrew mentor names that report.py's
didn't. The same Discord message could get a different concept, confidence,
or STUDENT/MENTOR role depending on which script processed it.

Both scripts now import from here instead of keeping their own table.
"""
import re

# ══════════════════════════════════════════════════════════════
#  TRADING KEYWORDS — union of both scripts' former lists
# ══════════════════════════════════════════════════════════════
KEYWORDS_HE = [
    'מגמה', 'תמיכה', 'התנגדות', 'סטופ', 'כניסה', 'שבירה', 'סגירה',
    'אמין', 'תקף', 'רמה', 'שפל', 'שיא', 'ריטסט', 'פיבונאצ\'י', 'פיבונאצ', 'פיבו',
    'שבועי', 'חודשי', 'יומי', 'ויק', 'פריצה', 'מומנטום', 'אישור',
    'לחכות', 'עסקה', 'לונג', 'שורט', 'ATR', 'RSI', 'MACD', 'N.M.S',
    'נמס', 'סיכון', 'מגמת עלייה', 'מגמת ירידה', 'ממוצע נע', 'הארכה', 'תיקון',
    '38.2', '61.8', '0.786',
]
KEYWORDS_EN = [
    'support', 'resistance', 'stop', 'entry', 'breakout', 'retest',
    'weekly', 'monthly', 'trend', 'swing', 'pivot', 'close', 'wick',
    'momentum', 'confirmation', 'level', 'target', 'setup', 'fibonacci',
    'extension', 'retracement', 'correction', 'atr',
]
ALL_KEYWORDS = KEYWORDS_HE + KEYWORDS_EN


def keyword_hits(text: str) -> int:
    """Count how many trading keywords appear in text (case-insensitive)."""
    t = (text or '').lower()
    return sum(1 for kw in ALL_KEYWORDS if kw.lower() in t)


# ══════════════════════════════════════════════════════════════
#  MENTOR / EXPERT IDENTIFICATION — union of both scripts' sets
# ══════════════════════════════════════════════════════════════
MENTOR_NAMES = {
    # מנהלים
    'cyclestrading', 'royben10', 'זיו', 'רועי',
    # מנטורים — Discord usernames
    'eliravid.', 'razshlomian', 'itamarku', 'ymbp13',
    'shalevb.g_34506', 'meni6282', 'yairmish', '_shayh',
    'sagioscar', 'avigailalmog',
    # מנטורים — שמות עבריים
    'ישראל מאיר', 'רז שלומיאן', 'שליו בן גיגי', 'אלי רביד',
    'גולן', 'שגיא', 'שלמה', 'יוסף', 'מני', 'רז',
}


def detect_role(author: str) -> str:
    """'MENTOR' if author matches MENTOR_NAMES, 'STUDENT' otherwise."""
    a = (author or '').strip()
    for name in MENTOR_NAMES:
        if name in a or a in name:
            return 'MENTOR'
    return 'STUDENT'


# ══════════════════════════════════════════════════════════════
#  FACTOR_PATTERNS — the ONE table both concept-classification and
#  gap-detection are derived from (previously two separate,
#  independently-drifting tables: CONCEPT_MAP and EXPERT_TO_FACTOR).
#
#  Ordered — first match wins for single-concept classification
#  (detect_concept). All matches are considered for gap detection
#  (detect_gaps), since one answer can touch several Factors.
#
#  `covered=True` means cycles_trading_scanner.py already implements
#  this concept as a Factor (see SCANNER_STATUS below for the
#  human-readable status used in HTML reports).
# ══════════════════════════════════════════════════════════════
FACTOR_PATTERNS = [
    (r'פיבו|פיבונאצ|fibonacci|fib|הרחבה|retracement|38\.2|61\.8|0\.786',
     'fibonacci', 'Factor 20 — Fibonacci', True),
    (r'N\.M\.S|נמ"ס|נמס|סגירה מעל|סגירה מתחת|weekly close',
     'nms_breakout', 'Factor 17 — NMS', True),
    (r'RSI|רסי',
     'rsi', 'Factor 1 — RSI', True),
    (r'MACD|מקד',
     'macd', 'Factor 15 — MACD', True),
    (r'ATR|תנודתיות|volatility',
     'volatility', 'Factor 11 — ATR', True),
    (r'ריטסט|retest',
     'retest', 'Factor 4 — Entry Distance', True),
    (r'מגמה.*חודש|monthly trend',
     'monthly_trend', 'Factor 8 — Monthly Trend', True),
    (r'רמות מתחרות|שתי רמות|ambiguous|crowded|אין רמה מובהקת',
     'level_ambiguity', 'Factor 18 — Level Ambiguity', True),
    (r'רמה לא אמינה|unreliable|נשבר.*שני כיוון',
     'level_reliability', 'Factor 17 — Level Reliability', True),
    (r'late entry|כניסה מאוחרת',
     'late_entry', 'Factor 9 — Late Entry', True),
    (r'שפל.*מחזיק|לא נשבר|swing low',
     'trend_confirmation', 'Factor 19 — Trend Confirmation', True),
    (r'דוחות|earnings|רווחים|דיבידנד',
     'earnings', 'Factor 5 — Earnings', True),
    (r'נזילות|liquidity|10 מיליון|10m|מחזור נמוך',
     'liquidity', 'Factor 3 — Liquidity', True),
    (r'ממוצע נע|moving average',
     'moving_avg', 'Factor 8 — Monthly Trend (partial)', True),
    # ── Concepts added Jul 2026 (Factors 32-43 + trade management) ──
    (r'sweep|סוויפ|הנזל|ינזיל|לצוד סטופים|stop.?run|פריצת שווא כלפי מטה',
     'liquidity_sweep', 'Factor 43 — Sweep Risk', True),
    (r'\bCCI\b',
     'cci', 'Factor 32 — CCI', True),
    (r'דיברגנצ|divergence',
     'divergence', 'Factor 33 — RSI Divergence', True),
    (r'גאפ|פער מחיר|\bgap\b',
     'price_gap', 'Factor 37 — Price Gaps', True),
    (r'ספל|ידית|cup|handle|ראש וכתפיים|head.{0,5}shoulder|דגל|תבנית',
     'chart_pattern', 'Factor 36 — Chart Patterns', True),
    (r'גאן|gann',
     'gann', 'Factor 39 — Gann Levels', True),
    (r'vwap',
     'vwap', 'Factor 41 — VWAP', True),
    (r'כניסה אגרסיבית|שיטות כניסה|שלוש.{0,10}כניס|aggressive entry',
     'entry_method', 'Factor 42 — Entry Method', True),
    (r'מימוש חלקי|יציאה חלקית|partial|כמות.{0,15}סטופ|לעדכן.{0,15}סטופ',
     'partial_exit', 'pm_partial + Rule 3 — Partial Exits', True),
    (r'עקרון המומנטום|שיעור 33|momentum principle',
     'momentum_trail', 'Rule 2 — Momentum Trailing', True),
    (r'יחס|ratio|1:2|1:3|TP|target',
     'risk_reward', 'Factor 2 — Risk:Reward + T1/T2/T3', True),
    (r'סטופ.*נמוך|סטופ.*נר|stop.*candle',
     'stop_candle', 'Factor 6 — Stop Logic', False),
    (r'מגמה|trend',
     'trend', 'Factor 8 — Trend Analysis', True),
    (r'תמיכה|support',
     'support_stop', 'Factor 6 — Support/Stop', True),
]


def detect_concept(text: str) -> tuple:
    """First matching Factor pattern → (concept, factor_label)."""
    for pattern, concept, factor, _covered in FACTOR_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return concept, factor
    return 'general', 'New Factor'


def detect_gaps(expert_answer: str, chart_analysis: dict = None) -> tuple:
    """
    Find every Factor pattern the expert's answer touches, split into
    `covered` (scanner already implements it) and `gaps` (it doesn't).
    Returns (gaps, covered) — both lists of
    {'concept','factor','expert_ref','covered'} dicts.
    """
    gaps, covered = [], []
    text = expert_answer or ''
    for pattern, concept, factor, is_covered in FACTOR_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        entry = {'concept': concept, 'factor': factor,
                  'expert_ref': m.group(0), 'covered': is_covered}
        (covered if is_covered else gaps).append(entry)
    return gaps, covered


def alignment_label(gaps: list, covered: list) -> str:
    """ALIGNED / PARTIAL / MISALIGNED based on how many gaps were found."""
    if not gaps:
        return 'ALIGNED'
    if len(gaps) <= 1:
        return 'PARTIAL'
    return 'MISALIGNED'


# ══════════════════════════════════════════════════════════════
#  SCANNER_STATUS — human-readable status/description per concept,
#  used by generate_discord_report.py's HTML cards. Derived from the
#  same FACTOR_PATTERNS table so "covered" can't disagree between the
#  two scripts again.
# ══════════════════════════════════════════════════════════════
SCANNER_STATUS = {
    'fibonacci':          ('✅ תוקן',  'OK',   '#22c55e', 'Factor 20 — Fibonacci נוסף עכשיו'),
    'nms_breakout':       ('✅ קיים',  'OK',   '#22c55e', 'Factor 17 — check_false_breakout()'),
    'rsi':                ('✅ קיים',  'OK',   '#22c55e', 'Factor 1 — _factor_rsi()'),
    'macd':               ('✅ קיים',  'OK',   '#22c55e', 'Factor 15 — calc_macd()'),
    'volatility':         ('✅ קיים',  'OK',   '#22c55e', 'Factor 11 — ATR filter'),
    'retest':             ('✅ קיים',  'OK',   '#22c55e', 'Factor 4 — entry distance check'),
    'monthly_trend':      ('✅ קיים',  'OK',   '#22c55e', 'Factor 8 — get_monthly_analysis()'),
    'level_ambiguity':    ('✅ קיים',  'OK',   '#22c55e', 'Factor 18 — check_level_ambiguity()'),
    'level_reliability':  ('✅ קיים',  'OK',   '#22c55e', 'Factor 17 — check_level_reliability()'),
    'late_entry':         ('✅ קיים',  'OK',   '#22c55e', 'Factor 9 — LateEntry calc'),
    'trend_confirmation': ('✅ קיים',  'OK',   '#22c55e', 'Factor 19 — check_swing_broken()'),
    'earnings':           ('✅ קיים',  'OK',   '#22c55e', 'Factor 5 — get_earnings()'),
    'liquidity':          ('✅ קיים',  'OK',   '#22c55e', 'Factor 3 — Volume > 10M'),
    'moving_avg':         ('✅ קיים',  'OK',   '#22c55e', 'Factor 8 — monthly SMA'),
    'risk_reward':        ('✅ קיים',  'OK',   '#22c55e', 'T1/T2/T3 + Rule 3 TP-trail + pm_partial (יולי 2026)'),
    'stop_candle':        ('🔧 חסר',  'GAP',  '#f59e0b', 'סטופ = min(תמיכה, נמוך נר) — לא מיושם'),
    'liquidity_sweep':    ('✅ קיים',  'OK',   '#22c55e', 'Factor 43 — sweep risk + reclaim bonus'),
    'cci':                ('✅ קיים',  'OK',   '#22c55e', 'Factor 32 — CCI ±200'),
    'divergence':         ('✅ קיים',  'OK',   '#22c55e', 'Factor 33 — swing-based RSI divergence'),
    'price_gap':          ('✅ קיים',  'OK',   '#22c55e', 'Factor 37 — detect_price_gaps()'),
    'chart_pattern':      ('✅ קיים',  'OK',   '#22c55e', 'Factor 36 — Cup&Handle / H&S geometric'),
    'gann':               ('✅ קיים',  'OK',   '#22c55e', 'Factor 39 — gann_100/gann_50 confluence'),
    'vwap':               ('✅ קיים',  'OK',   '#22c55e', 'Factor 41 — rolling 20W VWAP'),
    'entry_method':       ('✅ קיים',  'OK',   '#22c55e', 'Factor 42 — AGGRESSIVE/SOLID/MORE_SOLID'),
    'partial_exit':       ('✅ קיים',  'OK',   '#22c55e', 'pm_partial + stop-quantity warnings'),
    'momentum_trail':     ('✅ קיים',  'OK',   '#22c55e', 'Rule 2 — pm_rule2_momentum()'),
    'trend':              ('✅ קיים',  'OK',   '#22c55e', 'get_trend() + monthly trend'),
    'support_stop':       ('✅ קיים',  'OK',   '#22c55e', 'Factor 6 — stop distance check'),
    'general':            ('ℹ️ כללי', 'INFO', '#64748b', 'שיעור כללי'),
}

# Kept in sync with SCANNER_STATUS's 'OK' entries — provided for callers
# (like discord_monitor.py's older detect_gaps signature) that just need
# the set of covered concept names.
SCANNER_FACTORS_COVERED = {c for c, (_, kind, _, _) in SCANNER_STATUS.items() if kind == 'OK'}

FIX_CODE = {
    'risk_reward': '''\
# ✅ כבר מיושם (יולי 2026) — T1/T2/T3 בדוח, Rule 3 TP-trail בניהול פוזיציה,
# ו-pm_partial לרישום מימוש חלקי:
#   python cycles_trading_scanner.py partial <position_id> <units_sold> [price]''',

    'stop_candle': '''\
# 🔧 תצוגה מקדימה — שינוי זה לא מתבצע אוטומטית. לעדכון הסורק:
# ערוך את ct_analysis.py → קטע LONG ב-_detect_setup()

# לפני:  stop = round(support * 0.97, 4)
# אחרי:
entry_candle_low = float(df["Low"].iloc[-1])
stop = round(min(support * 0.97,
                 entry_candle_low * 0.99), 4)
# → סטופ = min(מתחת לתמיכה, מתחת לנמוך הנר)''',

    'fibonacci': '''\
# ✅ כבר נוסף — check_fibonacci_zone() + Factor 20 ב-ct_analysis.py
# GOLDEN_ZONE (38-61%) → +8 | SHALLOW → 0 | DEEP → -5 | TOO_DEEP → -12''',
}
