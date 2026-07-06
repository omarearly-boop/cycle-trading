#!/usr/bin/env python3
"""
generate_discord_report.py — Cycles Discord Learning Report v4
"""
import json, re, datetime, webbrowser
from pathlib import Path
from collections import Counter

BASE       = Path(__file__).parent
INPUT      = BASE / 'discord_processed.json'
RAW_INPUT  = BASE / 'discord_may_june_2026.json'   # has real image URLs
LESSONS    = BASE / 'pending_lessons.json'

REPORT_DIR = BASE / 'REPORT'
REPORT_DIR.mkdir(exist_ok=True)

# Filename: discord_lessons_YYYYMMDD-YYYYMMDD_gen_YYYYMMDD.html
# (data range will be filled in after loading messages)
GUILD_ID   = '1069278010835992678'
CHANNEL_ID = '1077319951179841667'
DISCORD_CHANNEL = f'https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}'

DISCORD_EPOCH = 1420070400000  # Jan 1, 2015 UTC in ms

def ts_to_snowflake(iso_str):
    """Convert ISO timestamp to Discord Snowflake (time-based, unique per message)."""
    try:
        dt = datetime.datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        ms = int(dt.timestamp() * 1000)
        return str((ms - DISCORD_EPOCH) << 22)
    except Exception:
        return None

def discord_jump(image_urls, timestamp=None):
    """Return Discord deep-link. Uses timestamp-based Snowflake (unique per message)."""
    if timestamp:
        sf = ts_to_snowflake(timestamp)
        if sf:
            return f'https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}/{sf}'
    return DISCORD_CHANNEL

KW_HE = ['מגמה','תמיכה','התנגדות','סטופ','כניסה','שבירה','סגירה',
          'רמה','שפל','שיא','ריטסט','פיבו','פיבונאצ','שבועי','חודשי',
          'יומי','פריצה','מומנטום','אישור','עסקה','לונג','שורט','תיקון',
          'N.M.S','נמס','סיכון','הארכה','ATR','RSI','MACD','ממוצע נע',
          '38.2','61.8','0.786']
KW_EN = ['support','resistance','stop','entry','breakout','retest',
         'weekly','monthly','trend','swing','pivot','close','wick',
         'momentum','confirmation','level','target','fibonacci','atr']
ALL_KW = KW_HE + KW_EN

CONCEPT_MAP = [
    (r'פיבו|fibonacci|fib|הרחבה|retracement|38\.2|61\.8|0\.786',
     ('fibonacci',         'Factor 20 — Fibonacci')),
    (r'N\.M\.S|נמ"ס|נמס|סגירה מעל|סגירה מתחת|weekly close',
     ('nms_breakout',      'Factor 17 — NMS')),
    (r'RSI|רסי',
     ('rsi',               'Factor 1 — RSI')),
    (r'MACD|מקד',
     ('macd',              'Factor 15 — MACD')),
    (r'ATR|תנודתיות',
     ('volatility',        'Factor 11 — ATR')),
    (r'ריטסט|retest',
     ('retest',            'Factor 4 — Entry Distance')),
    (r'מגמה.*חודש|monthly trend',
     ('monthly_trend',     'Factor 8 — Monthly Trend')),
    (r'רמות מתחרות|שתי רמות',
     ('level_ambiguity',   'Factor 18 — Level Ambiguity')),
    (r'רמה לא אמינה|unreliable',
     ('level_reliability', 'Factor 17 — Level Reliability')),
    (r'late entry|כניסה מאוחרת',
     ('late_entry',        'Factor 9 — Late Entry')),
    (r'שפל.*מחזיק|לא נשבר|swing low',
     ('trend_confirmation','Factor 19 — Trend Confirmation')),
    (r'דוחות|earnings|רווחים|דיבידנד',
     ('earnings',          'Factor 5 — Earnings')),
    (r'נזילות|liquidity|10 מיליון|מחזור נמוך',
     ('liquidity',         'Factor 3 — Liquidity')),
    (r'ממוצע נע|moving average',
     ('moving_avg',        'Factor 8 — Monthly Trend')),
    (r'יחס|ratio|1:2|1:3|TP|target',
     ('risk_reward',       'Factor 2 — Risk:Reward')),
    (r'סטופ.*נמוך|סטופ.*נר|stop.*candle',
     ('stop_candle',       'Factor 6 — Stop Logic')),
    (r'מגמה|trend',
     ('trend',             'Factor 8 — Trend Analysis')),
    (r'תמיכה|support',
     ('support_stop',      'Factor 6 — Support/Stop')),
]

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
    'risk_reward':        ('🔧 חסר',  'GAP',  '#f59e0b', 'אין TP חלקי / ניהול יציאה מדורג'),
    'stop_candle':        ('🔧 חסר',  'GAP',  '#f59e0b', 'סטופ = min(תמיכה, נמוך נר) — לא מיושם'),
    'trend':              ('✅ קיים',  'OK',   '#22c55e', 'get_trend() + monthly trend'),
    'support_stop':       ('✅ קיים',  'OK',   '#22c55e', 'Factor 6 — stop distance check'),
    'general':            ('ℹ️ כללי', 'INFO', '#64748b', 'שיעור כללי'),
}

FIX_CODE = {
    'risk_reward': '''\
# 🔧 תצוגה מקדימה — שינוי זה לא מתבצע אוטומטית. לעדכון הסורק:
# ערוך את cycles_trading_scanner.py → _build_setup_dict()

def get_tp_levels(entry, resistance, atr_val):
    t1 = resistance
    t2 = resistance + atr_val * 1.5     # יעד מורחב
    t3 = entry + (resistance - entry) * 2  # הרחבה 2:1
    return round(t1,4), round(t2,4), round(t3,4)

# ב-analyze(), לפני _build_setup_dict():
# t1, t2, t3 = get_tp_levels(entry, resistance, atr_val)
# הוסף ל-_setup: 'T2': t2, 'T3': t3''',

    'stop_candle': '''\
# 🔧 תצוגה מקדימה — שינוי זה לא מתבצע אוטומטית. לעדכון הסורק:
# ערוך את cycles_trading_scanner.py → קטע LONG ב-analyze()

# לפני:  stop = round(support * 0.97, 4)
# אחרי:
entry_candle_low = float(df["Low"].iloc[-1])
stop = round(min(support * 0.97,
                 entry_candle_low * 0.99), 4)
# → סטופ = min(מתחת לתמיכה, מתחת לנמוך הנר)''',

    'fibonacci': '''\
# ✅ כבר נוסף — check_fibonacci_zone() + _factor_fibonacci()
# cycles_trading_scanner.py → Factor 20
# GOLDEN_ZONE (38-61%) → +8 | SHALLOW → 0 | DEEP → -5 | TOO_DEEP → -12''',
}

MENTOR_NAMES = {
    'eliravid.','razshlomian','itamarku','ymbp13','shalevb.g_34506',
    'meni6282','yairmish','_shayh','sagioscar','avigailalmog',
    'cyclestrading','royben10',
}

def kw_hits(text):
    t = text.lower()
    return sum(1 for kw in ALL_KW if kw.lower() in t)

def detect_concept(text):
    for pat, (c, f) in CONCEPT_MAP:
        if re.search(pat, text, re.IGNORECASE):
            return c, f
    return 'general', 'General'

def extract_insight(q_text):
    sentences = re.split(r'[?\n]', q_text)
    for s in sorted(sentences, key=lambda x: kw_hits(x), reverse=True):
        if kw_hits(s) >= 1 and 10 < len(s.strip()) < 200:
            return s.strip()
    return ''

def format_ts(ts_str):
    """Format ISO timestamp → DD/MM/YY HH:MM"""
    try:
        dt = datetime.datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        return dt.strftime('%d/%m/%y %H:%M')
    except:
        return ts_str[:10]

# ── Build image-URL lookup from raw scraped file ──────────────────
raw_lookup = {}   # key: (author, timestamp[:19]) → {image_urls, has_image}
if RAW_INPUT.exists():
    raw_msgs = json.loads(RAW_INPUT.read_text(encoding='utf-8'))
    for rm in raw_msgs:
        key = (rm.get('author',''), rm.get('timestamp','')[:19])
        raw_lookup[key] = {
            'image_urls': rm.get('image_urls', []),
            'has_image':  rm.get('has_image', False),
        }
    print(f'📂 {len(raw_lookup)} הודעות גולמיות נטענו (עם image URLs)')

# ── Load & pair ───────────────────────────────────────────────────
msgs = json.loads(INPUT.read_text(encoding='utf-8'))
print(f'✅ {len(msgs)} הודעות נטענו')

# Compute data date range for filename
_timestamps = [m.get('timestamp','')[:10] for m in msgs if m.get('timestamp')]
_data_start = min(_timestamps).replace('-','') if _timestamps else 'unknown'
_data_end   = max(_timestamps).replace('-','') if _timestamps else 'unknown'
_today      = datetime.date.today().strftime('%Y%m%d')
REPORT = REPORT_DIR / f'discord_lessons_{_data_start}-{_data_end}_gen_{_today}.html'
print(f'📄 קובץ פלט: {REPORT.name}')

# ── Pair questions with ALL their mentor answers ──────────────────
# For each mentor reply (type-18), find its question; collect all
# replies per question and pick the best (highest keyword score).

# Step 1: map every mentor reply → its question index
reply_to_q = {}   # reply_idx → question_idx
for i, m in enumerate(msgs):
    reply_to = m.get('is_reply_to')
    if not reply_to: continue
    for j in range(i-1, max(-1, i-30), -1):
        if msgs[j].get('author') == reply_to:
            reply_to_q[i] = j
            break

# Step 2: group by question_idx → [reply_idx, ...]
from collections import defaultdict
q_to_replies = defaultdict(list)
for ri, qj in reply_to_q.items():
    q_to_replies[qj].append(ri)

# Step 3: build pairs — merged text for concept detection, best reply for display
pairs = []
for qj, reply_idxs in sorted(q_to_replies.items()):
    q = msgs[qj]
    replies = [msgs[ri] for ri in reply_idxs]
    merged_a = ' '.join(r.get('content','') for r in replies)
    combined = q.get('content','') + ' ' + merged_a
    if kw_hits(combined) < 2:
        continue
    # Pick the reply with the most keyword hits as the "main" answer
    best = max(replies, key=lambda r: kw_hits(r.get('content','')))
    pairs.append({'q': q, 'a': best, 'all_answers': replies, 'merged_a': merged_a})

print(f'🔗 {len(pairs)} זוגות ({sum(len(p["all_answers"]) for p in pairs)} תשובות סה״כ)')

lessons = []
for idx, p in enumerate(pairs):
    q, a        = p['q'], p['a']
    q_text      = q.get('content','')
    a_text      = a.get('content','')
    merged_a    = p.get('merged_a', a_text)   # all mentor answers merged
    answer_count= len(p.get('all_answers', [a]))
    combined    = q_text + ' ' + merged_a     # use ALL answers for concept detection
    hits        = kw_hits(combined)
    concept, factor = detect_concept(combined)
    conf     = 'HIGH' if hits >= 5 else ('MEDIUM' if hits >= 3 else 'LOW')
    insight  = extract_insight(q_text)
    # Enrich image_urls from raw scraped file (processed file has empty arrays)
    q_key = (q.get('author',''), q.get('timestamp','')[:19])
    a_key = (a.get('author',''), a.get('timestamp','')[:19])
    raw_q = raw_lookup.get(q_key, {})
    raw_a = raw_lookup.get(a_key, {})
    img_urls = (raw_q.get('image_urls') or raw_a.get('image_urls') or
                q.get('image_urls') or a.get('image_urls') or [])
    has_img  = bool(img_urls) or raw_q.get('has_image') or raw_a.get('has_image') or \
               q.get('has_image', False) or a.get('has_image', False)

    lessons.append({
        'id':              f'lesson_{idx:03d}',
        'idx':             idx,
        'timestamp':       q.get('timestamp',''),
        'question_author': q.get('author','?'),
        'answer_author':   a.get('author','?'),
        'question_text':   q_text,
        'thread_topic':    a_text.replace('[thread reply]','').strip(),
        'answer_count':    answer_count,
        'has_image':       has_img,
        'image_urls':      img_urls,
        'extraction': {
            'concept':          concept,
            'suggested_factor': factor,
            'insight':          insight,
            'keyword_hits':     hits,
            'confidence':       conf,
        },
    })

print(f'💡 {len(lessons)} שיעורים')

data = {'pending': lessons, 'approved': [], 'rejected': [],
        'last_updated': datetime.datetime.now().isoformat()}
LESSONS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

concept_counter = Counter(l['extraction']['concept'] for l in lessons)
high = [l for l in lessons if l['extraction']['confidence'] == 'HIGH']
med  = [l for l in lessons if l['extraction']['confidence'] == 'MEDIUM']
low_ = [l for l in lessons if l['extraction']['confidence'] == 'LOW']
img_count = sum(1 for l in lessons if l['has_image'])

# ── Code analysis section ─────────────────────────────────────────
def code_analysis_html():
    rows = ''
    seen = set()
    for concept, count in concept_counter.most_common():
        if concept in seen: continue
        seen.add(concept)
        st = SCANNER_STATUS.get(concept, SCANNER_STATUS['general'])
        status_txt, kind, color, desc = st
        kind_bg = {'OK':'#14532d','GAP':'#451a03','INFO':'#1e293b'}.get(kind,'#1e293b')
        fix_code = FIX_CODE.get(concept, '')
        code_btn = ''
        if fix_code:
            cid = f'acode_{concept}'
            code_btn = (
                f' <button type="button" onclick="toggleEl(\'{cid}\')"'
                f' style="background:#1e3a5f;color:#93c5fd;border:none;padding:2px 8px;'
                f'border-radius:4px;font-size:10px;cursor:pointer">קוד</button>'
                f'<div id="{cid}" style="display:none;margin-top:6px">'
                f'<pre style="background:#020617;color:#7dd3fc;padding:10px;border-radius:6px;'
                f'font-size:10px;direction:ltr;text-align:left;margin:0;overflow-x:auto;'
                f'white-space:pre-wrap">{fix_code}</pre></div>'
            )
        rows += f'''
        <tr style="border-bottom:1px solid #1e293b">
          <td style="padding:8px;color:#e2e8f0">{concept}</td>
          <td style="padding:8px;text-align:center">
            <span style="background:#334155;color:#94a3b8;padding:1px 8px;
              border-radius:99px;font-size:10px">{count}x</span></td>
          <td style="padding:8px">
            <span style="background:{kind_bg};color:{color};padding:2px 8px;
              border-radius:4px;font-size:11px;font-weight:600">{status_txt}</span></td>
          <td style="padding:8px;color:#94a3b8;font-size:11px">{desc}{code_btn}</td>
        </tr>'''

    gaps = [(c,d) for c,(s,k,col,d) in SCANNER_STATUS.items()
            if k=='GAP' and concept_counter.get(c,0)>0]
    gap_banner = ''
    if gaps:
        gap_list = ' | '.join(f'<b style="color:#f59e0b">{d}</b>' for _,d in gaps)
        gap_banner = (f'<div style="background:#451a03;border:1px solid #92400e;'
                      f'border-radius:8px;padding:12px 16px;margin-top:16px">'
                      f'<span style="color:#fbbf24;font-weight:700">🔧 פערים בסורק:</span> '
                      f'<span style="color:#fde68a;font-size:12px">{gap_list}</span>'
                      f'<br><span style="color:#78716c;font-size:11px;margin-top:6px;display:block">'
                      f'לתיקון: ערוך cycles_trading_scanner.py לפי הקוד בלחיצת "קוד"</span></div>')

    factor_grid = ''.join(
        f'<div style="color:#64748b;font-size:10px">F{i+1} — {name}</div>'
        for i,name in enumerate([
            'RSI','Risk:Reward','Volume','Entry Distance','Earnings',
            'Setup Quality','Stop Distance','Monthly Trend','Sector RS','Support Quality',
            'ATR Volatility','Earnings Zone','Late Entry','Fundamentals','MACD',
            'Bollinger Bands','Level Reliability','Level Ambiguity','Trend Confirmation',
            'Fibonacci ✨NEW'
        ]))

    return f'''
    <div id="code-analysis" style="background:#1e293b;border-radius:12px;padding:20px;
                margin-top:40px;border:1px solid #334155">
      <h2 style="color:#38bdf8;font-size:16px;margin:0 0 16px 0">
        📊 ניתוח קוד — מושגי Discord מול Factors בסורק</h2>
      <table style="width:100%;border-collapse:collapse;font-size:12px">
        <tr style="background:#0f172a;color:#475569;font-size:11px">
          <th style="padding:8px;text-align:right">מושג</th>
          <th style="padding:8px;text-align:center">תדירות</th>
          <th style="padding:8px;text-align:right">סטטוס</th>
          <th style="padding:8px;text-align:right">פרטים + קוד</th>
        </tr>
        {rows}
      </table>
      {gap_banner}
      <div style="margin-top:20px;background:#0f172a;border-radius:8px;padding:14px">
        <div style="color:#94a3b8;font-size:11px;margin-bottom:8px;font-weight:600">
          20 Factors בסורק:</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:3px">
          {factor_grid}
        </div>
      </div>
    </div>'''


# ── Card ──────────────────────────────────────────────────────────
def card(lesson, card_idx):
    ext      = lesson.get('extraction', {})
    conf     = ext.get('confidence', '?')
    concept  = ext.get('concept', 'general')
    lid      = lesson['id']
    ts       = lesson.get('timestamp', '')
    ts_fmt   = format_ts(ts)
    q_text   = lesson.get('question_text', '')
    topic    = lesson.get('thread_topic', '')
    insight  = ext.get('insight', '')
    has_img      = lesson.get('has_image', False)
    img_urls     = lesson.get('image_urls', [])
    q_auth       = lesson.get('question_author', '?')
    a_auth       = lesson.get('answer_author', '?')
    answer_count = lesson.get('answer_count', 1)

    st = SCANNER_STATUS.get(concept, SCANNER_STATUS['general'])
    _status, _kind, _color, _desc = st

    # Confidence badge color
    conf_colors = {'HIGH':'#22c55e','MEDIUM':'#f59e0b','LOW':'#94a3b8'}
    cc = conf_colors.get(conf, '#94a3b8')
    conf_badge = (f'<span style="background:{cc};color:#000;padding:2px 9px;'
                  f'border-radius:99px;font-size:11px;font-weight:700">{conf}</span>')

    # Border color
    border = '#166534' if conf=='HIGH' else ('#92400e' if conf=='MEDIUM' else '#334155')

    # Build deep-link using timestamp-based Snowflake (unique per message)
    # Uses the question timestamp so each lesson links to a unique position in Discord
    deep_link = discord_jump(img_urls, timestamp=ts)

    # Image section
    img_html = ''
    if img_urls:
        imgs = ''.join(
            f'<a href="{deep_link}" target="_blank">'
            f'<img src="{u}" style="max-width:100%;max-height:260px;border-radius:8px;'
            f'display:block;margin-top:6px;cursor:pointer;border:2px solid transparent;'
            f'transition:border-color .2s" '
            f'onmouseover="this.style.borderColor=\'#6366f1\'" '
            f'onmouseout="this.style.borderColor=\'transparent\'" '
            f'title="לחץ לפתוח את ההודעה ב-Discord" alt="גרף"/></a>'
            for u in img_urls[:3])
        search_str = f'from:{q_auth} {ts[:10]}'
        img_html = (f'<div style="margin-top:6px">{imgs}'
                    f'<div style="display:flex;align-items:center;gap:8px;margin-top:6px;flex-wrap:wrap">'
                    f'<a href="{deep_link}" target="_blank"'
                    f' style="color:#6366f1;font-size:11px;text-decoration:none;'
                    f'background:#1e1b4b;padding:3px 10px;border-radius:5px;border:1px solid #4338ca">'
                    f'🔗 Discord ~{ts_fmt} ←</a>'
                    f'<button type="button" onclick="copySearch(\'{search_str}\')"'
                    f' style="background:#0f172a;color:#94a3b8;border:1px solid #334155;'
                    f'padding:3px 10px;border-radius:5px;font-size:11px;cursor:pointer;'
                    f'font-family:sans-serif">📋 העתק לחיפוש Discord</button>'
                    f'</div></div>')
    elif has_img:
        search_str = f'from:{q_auth} {ts[:10]}'
        img_html = (
            f'<div style="background:#1e1b4b;border:1px dashed #4338ca;border-radius:8px;'
            f'padding:10px 14px;margin-top:8px;display:flex;align-items:center;gap:12px">'
            f'<span style="color:#818cf8;font-size:18px">📸</span>'
            f'<div style="flex:1">'
            f'<div style="color:#818cf8;font-size:12px;font-weight:600">גרף צורף לשאלה</div>'
            f'<div style="display:flex;gap:8px;margin-top:4px;flex-wrap:wrap">'
            f'<a href="{deep_link}" target="_blank"'
            f' style="color:#6366f1;font-size:11px">🔗 Discord ~{ts_fmt} ←</a>'
            f'<button type="button" onclick="copySearch(\'{search_str}\')"'
            f' style="background:none;color:#64748b;border:1px solid #334155;'
            f'padding:2px 8px;border-radius:4px;font-size:10px;cursor:pointer;'
            f'font-family:sans-serif">📋 העתק לחיפוש</button>'
            f'</div></div></div>')

    # Scanner status tag
    scanner_tag = (
        f'<span style="background:{_color}22;color:{_color};border:1px solid {_color}44;'
        f'padding:2px 8px;border-radius:4px;font-size:10px">{_status} — {_desc[:45]}</span>')

    # Fix code toggle (GAP only)
    fix_html = ''
    if _kind == 'GAP' and concept in FIX_CODE:
        cid = f'fix_{card_idx}'
        fix_html = (
            f'<div style="margin-top:8px">'
            f'<button type="button" onclick="toggleEl(\'{cid}\')"'
            f' style="background:#451a03;color:#fbbf24;border:1px solid #92400e;'
            f'padding:4px 12px;border-radius:6px;font-size:12px;cursor:pointer;'
            f'font-family:sans-serif">🔧 הצג קוד מוצע</button>'
            f'<span style="color:#78716c;font-size:10px;margin-right:8px">'
            f'(תצוגה מקדימה — לא מעדכן אוטומטית)</span>'
            f'<div id="{cid}" style="display:none;margin-top:8px">'
            f'<pre style="background:#020617;color:#7dd3fc;padding:14px;border-radius:8px;'
            f'font-size:11px;direction:ltr;text-align:left;margin:0;overflow-x:auto;'
            f'line-height:1.6;white-space:pre-wrap">{FIX_CODE[concept]}</pre></div></div>')

    # Approve/Reject interactive buttons
    aid = f'card_{card_idx}'
    # If scanner already implements this concept → approve is irrelevant, disable it
    already_in_scanner = (_kind == 'OK')
    if already_in_scanner:
        approve_btn = (
            f'<button type="button" id="approve_{card_idx}" disabled'
            f' style="background:#1e293b;color:#334155;border:1px solid #334155;'
            f'padding:4px 14px;border-radius:6px;font-size:12px;cursor:default;'
            f'font-family:sans-serif;opacity:0.45">'
            f'✅ ממומש בסורק</button>')
        review_note = (
            f'<span style="color:#22c55e;font-size:10px">'
            f'הסורק כבר מכסה מושג זה ({_desc[:40]}) — אין צורך לאשר</span>')
    else:
        approve_btn = (
            f'<button type="button" id="approve_{card_idx}"'
            f' onclick="reviewLesson({card_idx},\'approved\')"'
            f' style="background:#14532d;color:#86efac;border:1px solid #22c55e;'
            f'padding:4px 14px;border-radius:6px;font-size:12px;cursor:pointer;'
            f'font-family:sans-serif">✅ אשר שיעור</button>')
        review_note = (
            f'<span style="color:#475569;font-size:10px">'
            f'אישור = שיעור יתווסף ל-learnings.json | דחייה = לא רלוונטי'
            f'</span>')
    reject_btn = (
        f'<button type="button" id="reject_{card_idx}"'
        f' onclick="reviewLesson({card_idx},\'rejected\')"'
        f' style="background:#450a0a;color:#fca5a5;border:1px solid #b91c1c;'
        f'padding:4px 14px;border-radius:6px;font-size:12px;cursor:pointer;'
        f'font-family:sans-serif">❌ דחה</button>')

    return f'''
    <div id="{aid}" data-conf="{conf}" data-lid="{lid}"
         data-concept="{concept}" data-img="{1 if has_img else 0}"
         style="background:#1e293b;border-radius:12px;padding:20px;
                margin-bottom:14px;border:1px solid {border};
                transition:border-color .2s,background .2s">

      <!-- Header row -->
      <div style="display:flex;justify-content:space-between;align-items:flex-start;
                  margin-bottom:12px;gap:8px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span style="background:#334155;color:#64748b;padding:2px 8px;
                border-radius:4px;font-size:10px;font-family:monospace">#{card_idx+1}</span>
          <span style="color:#475569;font-size:11px">📅 {ts_fmt}</span>
          {"<span style='background:#4c1d95;color:#c4b5fd;padding:2px 8px;border-radius:99px;font-size:10px'>📸 גרף</span>" if has_img else ""}
          {f"<span style='background:#1e3a5f;color:#7dd3fc;padding:2px 8px;border-radius:99px;font-size:10px'>💬 {answer_count} תשובות</span>" if answer_count > 1 else ""}
        </div>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          {conf_badge}
          <span style="background:#334155;color:#64748b;padding:2px 8px;
                border-radius:99px;font-size:10px">{ext.get("suggested_factor","")}</span>
        </div>
      </div>

      <!-- Question -->
      <div style="margin-bottom:12px">
        <div style="color:#475569;font-size:11px;margin-bottom:5px">
          🎓 <b style="color:#94a3b8">{q_auth}</b> שאל:
        </div>
        <div style="background:#0f172a;border-radius:8px;padding:12px;
                    border-right:3px solid #38bdf8;max-height:200px;overflow-y:auto">
          <p style="color:#e2e8f0;margin:0;font-size:13px;line-height:1.7;
                    white-space:pre-wrap">{q_text}</p>
        </div>
        {img_html}
      </div>

      <!-- Thread topic (what mentor titled) -->
      <div style="background:#0f172a;border-radius:8px;padding:10px 12px;
                  border-right:3px solid #f59e0b;margin-bottom:10px">
        <div style="color:#475569;font-size:11px;margin-bottom:4px">
          👨‍🏫 <b style="color:#94a3b8">{a_auth}</b> פתח Thread:
        </div>
        <p style="color:#fde68a;margin:0;font-size:13px;font-weight:500;line-height:1.5">
          {topic or "(ראה בDiscord)"}</p>
        <div style="display:flex;gap:8px;align-items:center;margin-top:6px;flex-wrap:wrap">
          <a href="{deep_link}" target="_blank"
             style="color:#6366f1;font-size:11px;text-decoration:none;
                    background:#1e1b4b;padding:3px 10px;border-radius:5px;
                    border:1px solid #4338ca">
            🔗 Discord ~{ts_fmt} ←</a>
          <button type="button" onclick="copySearch('from:{q_auth} {ts[:10]}')"
             style="background:#0f172a;color:#64748b;border:1px solid #334155;
                    padding:3px 10px;border-radius:5px;font-size:11px;cursor:pointer;
                    font-family:sans-serif">📋 העתק לחיפוש</button>
        </div>
      </div>

      <!-- Insight -->
      {"f'<div style=\"background:#0f172a;border-radius:8px;padding:8px 12px;margin-bottom:10px;border-right:3px solid #22c55e\"><span style=\"color:#475569;font-size:10px\">💡 תובנה: </span><span style=\"color:#86efac;font-size:12px\">' + insight + '</span></div>'" if insight else ""}

      <!-- Scanner tag + fix code -->
      <div style="margin-bottom:10px">
        {scanner_tag}
        {fix_html}
      </div>

      <!-- Review buttons -->
      <div id="review_row_{card_idx}"
           style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;
                  padding-top:8px;border-top:1px solid #1e293b">
        {approve_btn}
        {reject_btn}
        {review_note}
      </div>
      <div id="review_status_{card_idx}" style="display:none;margin-top:6px;
           font-size:11px;color:#94a3b8"></div>
    </div>'''


# ── JS ────────────────────────────────────────────────────────────
SCRIPT = """
// Toggle element show/hide
function toggleEl(id) {
  var el = document.getElementById(id);
  if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

// Copy Discord search query to clipboard + show toast
function copySearch(query) {
  navigator.clipboard.writeText(query).then(function() {
    var toast = document.getElementById('copy_toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'copy_toast';
      toast.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);'
        + 'background:#1e293b;color:#e2e8f0;border:1px solid #38bdf8;border-radius:8px;'
        + 'padding:10px 20px;font-size:13px;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,0.4);'
        + 'direction:rtl;font-family:sans-serif;transition:opacity .3s';
      document.body.appendChild(toast);
    }
    toast.innerHTML = '📋 הועתק: <code style="color:#38bdf8">' + query + '</code>'
      + '<br><span style="font-size:11px;color:#64748b">הדבק בחיפוש Discord (סמל 🔍 למעלה)</span>';
    toast.style.opacity = '1';
    clearTimeout(toast._t);
    toast._t = setTimeout(function() { toast.style.opacity = '0'; }, 3500);
  }).catch(function() {
    prompt('העתק ידנית:', query);
  });
}

// Active filter state
var _activeFilter = { type: null, value: null };

function filterCards(conf) {
  _activeFilter = conf ? { type: 'conf', value: conf } : { type: null, value: null };
  _applyFilter();
  document.querySelectorAll('.fbtn').forEach(function(btn) {
    btn.style.opacity = (!conf || btn.dataset.f === conf) ? '1' : '0.45';
    btn.style.fontWeight = (!conf || btn.dataset.f === conf) ? '700' : '400';
  });
  document.querySelectorAll('.spill').forEach(function(btn) {
    btn.style.opacity = '0.55';
    btn.style.fontWeight = '400';
  });
}

function filterByType(type, value) {
  // Toggle off if same filter clicked again
  if (_activeFilter.type === type && _activeFilter.value === value) {
    _activeFilter = { type: null, value: null };
  } else {
    _activeFilter = { type: type, value: value };
  }
  _applyFilter();
  // Reset confidence buttons
  document.querySelectorAll('.fbtn').forEach(function(btn) {
    btn.style.opacity = (_activeFilter.type === 'conf' && btn.dataset.f === _activeFilter.value) ? '1' : (_activeFilter.type === 'conf' ? '0.45' : '1');
    btn.style.fontWeight = '400';
  });
  // Highlight active stat pill
  document.querySelectorAll('.spill').forEach(function(btn) {
    var active = (_activeFilter.type === type && _activeFilter.value === value && _activeFilter.type !== null);
    btn.style.opacity = (btn.dataset.ftype === type && btn.dataset.fval === value && active) ? '1' : (active ? '0.45' : '0.75');
    btn.style.fontWeight = (btn.dataset.ftype === type && btn.dataset.fval === value && active) ? '700' : '400';
  });
}

function _applyFilter() {
  document.querySelectorAll('[data-conf]').forEach(function(el) {
    var show = true;
    if (_activeFilter.type === 'conf') {
      show = el.dataset.conf === _activeFilter.value;
    } else if (_activeFilter.type === 'concept') {
      show = (el.dataset.concept || '').toLowerCase().indexOf(_activeFilter.value.toLowerCase()) !== -1;
    } else if (_activeFilter.type === 'img') {
      show = el.dataset.img === _activeFilter.value;
    }
    el.style.display = show ? 'block' : 'none';
  });
}

// Review lesson (approve/reject) — persisted in localStorage
var STORAGE_KEY = 'discord_reviews';
function getReviews() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); } catch(e) { return {}; }
}
function saveReviews(r) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(r));
}

function reviewLesson(idx, action) {
  var reviews = getReviews();
  var lid = document.querySelector('[data-lid]#card_' + idx) &&
            document.getElementById('card_' + idx).dataset.lid;
  reviews[idx] = { action: action, lid: lid, ts: new Date().toISOString() };
  saveReviews(reviews);
  applyReviewUI(idx, action);
  updateCounters();
}

function applyReviewUI(idx, action) {
  var card = document.getElementById('card_' + idx);
  if (!card) return;
  if (action === 'approved') {
    card.style.borderColor = '#22c55e';
    card.style.background = '#052e16';
    document.getElementById('review_status_' + idx).style.display = 'block';
    document.getElementById('review_status_' + idx).innerHTML =
      '✅ <b style="color:#4ade80">אושר</b> — יתווסף ל-learnings.json בהרצה הבאה';
  } else {
    card.style.borderColor = '#b91c1c';
    card.style.background = '#1a0a0a';
    document.getElementById('review_status_' + idx).style.display = 'block';
    document.getElementById('review_status_' + idx).innerHTML =
      '❌ <b style="color:#f87171">נדחה</b> — מסומן כלא רלוונטי';
  }
  var approve_btn = document.getElementById('approve_' + idx);
  var reject_btn  = document.getElementById('reject_' + idx);
  if (approve_btn) approve_btn.disabled = true;
  if (reject_btn)  reject_btn.disabled  = true;
}

function updateCounters() {
  var reviews = getReviews();
  var approved = Object.values(reviews).filter(function(r){return r.action==='approved';}).length;
  var rejected = Object.values(reviews).filter(function(r){return r.action==='rejected';}).length;
  var el = document.getElementById('review_counter');
  if (el) el.innerHTML = '✅ ' + approved + ' אושרו &nbsp;|&nbsp; ❌ ' + rejected + ' נדחו';
}

function exportReviewed() {
  var reviews = getReviews();
  var approved = Object.entries(reviews)
    .filter(function(kv){return kv[1].action==='approved';})
    .map(function(kv){ return kv[1].lid || 'lesson_'+kv[0]; });
  if (approved.length === 0) {
    alert('לא אושר שום שיעור עדיין');
    return;
  }
  var txt = 'python discord_monitor.py approve ' + approved.join(' ');
  navigator.clipboard.writeText(txt).then(function(){
    alert('הועתק ללוח! הדבק בטרמינל לשמירה ב-learnings.json:\\n\\n' + txt);
  });
}

// Restore state on load
window.addEventListener('DOMContentLoaded', function() {
  var reviews = getReviews();
  Object.entries(reviews).forEach(function(kv) {
    applyReviewUI(parseInt(kv[0]), kv[1].action);
  });
  updateCounters();
});
"""

# ── Single combined toolbar (stats + filter in one row) ──────────
def stat_pill(n, label, color, ftype=None, fval=None):
    """Stat pill — clickable if ftype/fval given, plain span otherwise."""
    if ftype and fval:
        return (
            f'<button type="button" class="spill" data-ftype="{ftype}" data-fval="{fval}"'
            f' onclick="filterByType(\'{ftype}\',\'{fval}\')"'
            f' style="background:#1e293b;border:1px solid #334155;border-radius:8px;'
            f'padding:4px 10px;font-size:11px;color:{color};white-space:nowrap;'
            f'cursor:pointer;font-family:sans-serif;opacity:0.75">'
            f'<b>{n}</b> <span style="color:#475569">{label}</span></button>')
    return (f'<span style="background:#1e293b;border:1px solid #334155;border-radius:8px;'
            f'padding:4px 10px;font-size:11px;color:{color};white-space:nowrap">'
            f'<b>{n}</b> <span style="color:#475569">{label}</span></span>')

toolbar = f'''
<div style="display:flex;gap:6px;align-items:center;margin-bottom:18px;
            flex-wrap:wrap;background:#0f172a;padding:10px 12px;border-radius:10px;
            border:1px solid #1e293b">
  <button type="button" class="fbtn" data-f="" onclick="filterCards('')"
    style="background:#334155;color:#e2e8f0;border:none;padding:4px 14px;
           border-radius:16px;font-size:12px;cursor:pointer;font-family:sans-serif;
           font-weight:700">הכל</button>
  <button type="button" class="fbtn" data-f="HIGH" onclick="filterCards('HIGH')"
    style="background:#14532d;color:#86efac;border:1px solid #22c55e;padding:4px 14px;
           border-radius:16px;font-size:12px;cursor:pointer;font-family:sans-serif">
    🔴 HIGH ({len(high)})</button>
  <button type="button" class="fbtn" data-f="MEDIUM" onclick="filterCards('MEDIUM')"
    style="background:#451a03;color:#fde68a;border:1px solid #f59e0b;padding:4px 14px;
           border-radius:16px;font-size:12px;cursor:pointer;font-family:sans-serif">
    🟡 MEDIUM ({len(med)})</button>
  <button type="button" class="fbtn" data-f="LOW" onclick="filterCards('LOW')"
    style="background:#1e293b;color:#94a3b8;border:1px solid #475569;padding:4px 14px;
           border-radius:16px;font-size:12px;cursor:pointer;font-family:sans-serif">
    ⚪ LOW ({len(low_)})</button>
  <span style="color:#1e293b;margin:0 2px">│</span>
  {stat_pill(len(lessons), 'שיעורים', '#38bdf8')}
  {stat_pill(img_count, '📸 גרף', '#7c3aed', ftype='img', fval='1')}
  {stat_pill(concept_counter.get("fibonacci",0), 'פיבו', '#f59e0b', ftype='concept', fval='fibonacci')}
  <span style="color:#1e293b;margin:0 2px">│</span>
  <span id="review_counter" style="color:#64748b;font-size:11px"></span>
  <button type="button" onclick="exportReviewed()"
    style="background:#1e3a5f;color:#93c5fd;border:1px solid #1d4ed8;padding:3px 10px;
           border-radius:12px;font-size:11px;cursor:pointer;font-family:sans-serif">
    📋 ייצוא</button>
  <a href="#code-analysis"
     style="color:#475569;font-size:11px;margin-right:auto;text-decoration:none">
    ⬇ ניתוח קוד</a>
</div>'''

# ── Build all cards ───────────────────────────────────────────────
all_cards = ''.join(card(l, i) for i, l in enumerate(high + med + low_))

# ── Final HTML ────────────────────────────────────────────────────
html = f'''<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
  <meta charset="UTF-8">
  <title>Cycles Discord Lessons — מאי-יוני 2026</title>
  <style>
    body{{background:#0f172a;color:#e2e8f0;margin:0;padding:20px 24px;
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Tahoma,sans-serif}}
    *{{box-sizing:border-box}}
    pre{{font-family:"Fira Code","Cascadia Code",Consolas,monospace}}
    ::-webkit-scrollbar{{width:4px;height:4px}}
    ::-webkit-scrollbar-track{{background:#0f172a}}
    ::-webkit-scrollbar-thumb{{background:#334155;border-radius:2px}}
    button{{transition:opacity .15s,border-color .15s}}
    button:hover:not(:disabled){{opacity:.8}}
    button:disabled{{opacity:.35;cursor:default}}
    a{{text-decoration:none;color:inherit}}
    a:hover{{text-decoration:underline}}
  </style>
</head>
<body>
  <script>{SCRIPT}</script>

  <!-- Title -->
  <div style="display:flex;align-items:baseline;gap:12px;margin-bottom:4px;flex-wrap:wrap">
    <h1 style="color:#38bdf8;font-size:20px;margin:0">🎓 Cycles Discord Lessons</h1>
    <span style="background:#1e40af;color:#bfdbfe;padding:2px 10px;border-radius:99px;
          font-size:12px">📅 01/05/2026 – 30/06/2026</span>
    <span style="color:#22c55e;font-size:12px">✅ Factor 20 Fibonacci נוסף</span>
    <a href="{DISCORD_CHANNEL}" target="_blank"
       style="color:#38bdf8;font-size:11px">↗ פתח ערוץ Discord</a>
  </div>
  <p style="color:#475569;font-size:11px;margin:0 0 16px">
    Discord threads: כל שאלה נפתחת כ-Thread. הדוח מציג שאלה + נושא ה-Thread + קישור.
    אין message IDs — לחיפוש גרף ספציפי השתמש בתאריך שמוצג בכל כרטיס.
    <b style="color:#f59e0b">אישור/דחייה</b> שומר בדפדפן; לשמירה ב-learnings.json לחץ "ייצוא אושרים".
  </p>

  <!-- Toolbar: filter + stats in one row -->
  {toolbar}

  <!-- Cards -->
  {all_cards}

  <!-- Code analysis -->
  {code_analysis_html()}

  <div style="height:48px"></div>
</body>
</html>'''

REPORT.write_text(html, encoding='utf-8')
print(f'📊 דוח: {REPORT}')
webbrowser.open(str(REPORT))
print('✅ נפתח')
