#!/usr/bin/env python3
"""
discord_monitor.py — Cycles Trading Learning Monitor (Browser Edition)
=======================================================================
עובד דרך הדפדפן — לא צריך Bot Token.
המשימה המתוזמנת פותחת Discord בכרום, שולפת הודעות ומעבירה לסקריפט זה.

Usage:
  python discord_monitor.py process <json_file>  # עיבוד הודעות שנשלפו מכרום
  python discord_monitor.py review               # הצגת שיעורים ממתינים
  python discord_monitor.py report               # פתיחת דוח HTML
  python discord_monitor.py approve <id>         # אישור שיעור
  python discord_monitor.py reject  <id> [note]  # דחיית שיעור
  python discord_monitor.py stats                # סטטיסטיקות

Note on shared modules
-----------------------
Trading keywords, mentor identification, and the concept→Factor mapping
live in ct_taxonomy.py. Q&A pairing (matching a mentor's answer to a
student's question) lives in ct_qa_pairing.py. Both are shared with
generate_discord_report.py so the two scripts can no longer classify the
same message differently — see the architecture review from 2026-07-06
for why (they had drifted, and a duplicated time-proximity pairing
heuristic here had already mispaired messages in production).
"""

import os
import json
import sys
import re
import datetime
from pathlib import Path

from ct_taxonomy import (
    detect_role, detect_concept, detect_gaps, alignment_label, keyword_hits,
    ALL_KEYWORDS,
)
from ct_qa_pairing import pair_messages, message_hash

# ══════════════════════════════════════════════════════════════
#  PATHS
# ══════════════════════════════════════════════════════════════
BASE_DIR       = Path(__file__).parent
LESSONS_FILE   = BASE_DIR / 'pending_lessons.json'
REPORT_FILE    = BASE_DIR / 'discord_lessons_report.html'
LAST_SEEN_FILE = BASE_DIR / '.discord_last_seen.json'

DISCORD_URL = 'https://discord.com/channels/1069278010835992678/1077319951179841667'


# ══════════════════════════════════════════════════════════════
#  SCANNER INTEGRATION
# ══════════════════════════════════════════════════════════════
def run_scanner(ticker: str) -> dict:
    """
    קורא ל-get_scanner_snapshot() המוגדר ב-cycles_trading_scanner.py —
    ממשק יציב ומתועד להשוואה מול הסורק, במקום לגעת ישירות במפתחות
    הפנימיים של analyze(). (המפתחות הישנים שנקראו כאן —
    'Direction'/'Probability'/'_factor_breakdown' — לא היו קיימים בפועל
    אחרי פיצול ct_analysis.py; המפתחות האמיתיים הם 'Dir'/'Prob'/'_pfacts',
    וה-except הרחב הסתיר את זה בשקט.)
    """
    if not ticker:
        return {'error': 'no ticker', 'ticker': ''}
    try:
        import importlib.util
        scanner_path = BASE_DIR / 'cycles_trading_scanner.py'
        if not scanner_path.exists():
            return {'error': 'scanner not found', 'ticker': ticker}

        spec = importlib.util.spec_from_file_location('scanner', scanner_path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        return mod.get_scanner_snapshot(ticker)
    except Exception as e:
        return {'error': str(e), 'ticker': ticker}


# ══════════════════════════════════════════════════════════════
#  MESSAGE PROCESSING
# ══════════════════════════════════════════════════════════════
def _extract_rule_sentence(answer_text: str) -> str:
    """חלץ את המשפט הכי רלוונטי מהתשובה."""
    sentences = re.split(r'[.!?\n]', answer_text)
    for s in sorted(sentences, key=len, reverse=True):
        if any(kw.lower() in s.lower() for kw in ALL_KEYWORDS) and len(s) > 20:
            return s.strip()
    return ''


def _make_pair(question: dict, answers: list) -> dict:
    """
    בונה pair בפורמט שהשכבות הבאות (extract_lesson/add_lessons) מצפות לו,
    מתוך התוצאה של ct_qa_pairing.pair_messages() — יכולה לכלול כמה
    תשובות לאותה שאלה (thread עם כמה מנטורים עונים).
    """
    best   = max(answers, key=lambda a: keyword_hits(a.get('content', '')))
    merged = ' '.join(a.get('content', '') for a in answers)
    return {
        'q_hash':             message_hash(question),
        'a_hash':              message_hash(best),
        'question_author':     question.get('author', '?'),
        'answer_author':       best.get('author', '?'),
        'question_role':       detect_role(question.get('author', '')),
        'answer_role':         detect_role(best.get('author', '')),
        'question_text':       question.get('content', ''),
        'answer_text':         best.get('content', ''),
        'merged_answer_text':  merged,
        'answer_count':        len(answers),
        'timestamp':           question.get('timestamp', ''),
        'has_image':           question.get('has_image', False) or
                                 any(a.get('has_image', False) for a in answers),
        'chart_analysis':      question.get('chart_analysis') or best.get('chart_analysis') or {},
    }


def extract_lesson(pair: dict) -> dict:
    combined   = pair['question_text'] + ' ' + pair['answer_text']
    concept, factor = detect_concept(combined)
    kw_hits    = keyword_hits(combined)
    confidence = 'HIGH' if kw_hits >= 5 else ('MEDIUM' if kw_hits >= 3 else 'LOW')
    rule_sent  = _extract_rule_sentence(pair['answer_text'])

    # ── Gap detection ─────────────────────────────────────────
    chart_analysis = pair.get('chart_analysis') or {}
    ticker         = chart_analysis.get('ticker', '')
    scanner_result = run_scanner(ticker) if ticker else {}
    gaps, covered  = detect_gaps(pair['answer_text'], chart_analysis)
    alignment      = alignment_label(gaps, covered)

    return {
        'concept':          concept,
        'suggested_factor': factor,
        'rule_sentence':    rule_sent,
        'keyword_hits':     kw_hits,
        'confidence':       confidence,
        'has_image':        pair['has_image'],
        'impact':           'HIGH' if confidence == 'HIGH' and pair['has_image'] else 'MEDIUM',
        # ── Comparison ──────────────────────────────────────
        'chart_analysis':   chart_analysis,
        'scanner_result':   scanner_result,
        'gaps':             gaps,
        'covered':          covered,
        'alignment':        alignment,
        'question_role':    pair.get('question_role', 'STUDENT'),
        'answer_role':      pair.get('answer_role', 'UNKNOWN'),
    }


# ══════════════════════════════════════════════════════════════
#  STORAGE
# ══════════════════════════════════════════════════════════════
def load_lessons() -> dict:
    if LESSONS_FILE.exists():
        try:
            return json.loads(LESSONS_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'pending': [], 'approved': [], 'rejected': [], 'implemented': []}


def save_lessons(data: dict):
    data['last_updated'] = datetime.datetime.now().isoformat()
    LESSONS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _lesson_id() -> str:
    import random
    return f"lesson_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(100,999)}"


def load_seen_hashes() -> set:
    if LAST_SEEN_FILE.exists():
        try:
            return set(json.loads(LAST_SEEN_FILE.read_text(encoding='utf-8')).get('hashes', []))
        except Exception:
            pass
    return set()


def save_seen_hashes(hashes: set):
    LAST_SEEN_FILE.write_text(
        json.dumps({'hashes': list(hashes),
                    'updated': datetime.datetime.now().isoformat()},
                   ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


def add_lessons(pairs_with_extraction: list) -> int:
    data   = load_lessons()
    seen   = load_seen_hashes()
    added  = 0
    new_hashes = set()

    for pair, ext in pairs_with_extraction:
        pair_key = pair['q_hash'] + pair['a_hash']
        if pair_key in seen:
            continue
        lesson = {
            'id':              _lesson_id(),
            'status':          'pending_review',
            'source':          'discord_browser',
            'timestamp':       pair['timestamp'],
            'question_author': pair['question_author'],
            'answer_author':   pair['answer_author'],
            'question_text':   pair['question_text'],
            'answer_text':     pair['answer_text'],
            'has_image':       pair['has_image'],
            'extraction':      ext,
            'notes':           '',
        }
        data['pending'].append(lesson)
        new_hashes.add(pair_key)
        added += 1

    if added:
        save_lessons(data)
        save_seen_hashes(seen | new_hashes)
    return added


# ══════════════════════════════════════════════════════════════
#  PROCESS COMMAND — קבלת הודעות מקובץ JSON
# ══════════════════════════════════════════════════════════════
def process_messages_file(json_path: str) -> dict:
    """
    קבל קובץ JSON עם רשימת הודעות שנשלפו מכרום ועבד אותן.

    פורמט הקובץ הצפוי:
    [
      {
        "author": "username",
        "content": "text...",
        "timestamp": "July 5, 2026 at 9:20 PM",
        "is_reply_to": "OtherUser" | null,
        "has_image": true | false
      },
      ...
    ]
    """
    path = Path(json_path)
    if not path.exists():
        print(f'❌ קובץ לא נמצא: {json_path}')
        return {}

    messages = json.loads(path.read_text(encoding='utf-8'))
    print(f'\n{"═"*55}')
    print('  🎓 Cycles Discord Learning Monitor')
    print(f'{"═"*55}')
    print(f'  📨 {len(messages)} הודעות התקבלו')

    raw_pairs = pair_messages(messages)
    print(f'  🔗 {len(raw_pairs)} זוגות שאלה-תשובה זוהו')

    pairs_with_ext = []
    for rp in raw_pairs:
        pair = _make_pair(rp['question'], rp['answers'])
        ext  = extract_lesson(pair)
        if ext['keyword_hits'] >= 2:
            pairs_with_ext.append((pair, ext))

    print(f'  💡 {len(pairs_with_ext)} שיעורים חולצו')

    added = add_lessons(pairs_with_ext)
    print(f'  💾 {added} שיעורים חדשים נשמרו')

    data        = load_lessons()
    report_path = generate_report(data)
    print(f'  📊 דוח: {report_path}')
    print(f'{"═"*55}\n')

    # מחק קובץ זמני
    try:
        path.unlink()
    except Exception:
        pass

    return {'messages': len(messages), 'pairs': len(raw_pairs), 'added': added}


# ══════════════════════════════════════════════════════════════
#  APPROVE / REJECT
# ══════════════════════════════════════════════════════════════
def approve_lesson(lesson_id: str) -> bool:
    data = load_lessons()
    for i, l in enumerate(data['pending']):
        if l['id'] == lesson_id:
            l['status'] = 'approved'
            l['approved_at'] = datetime.datetime.now().isoformat()
            data['approved'].append(l)
            data['pending'].pop(i)
            save_lessons(data)
            print(f'✅ אושר: {lesson_id}')
            return True
    print(f'❌ לא נמצא: {lesson_id}')
    return False


def reject_lesson(lesson_id: str, reason: str = '') -> bool:
    data = load_lessons()
    for i, l in enumerate(data['pending']):
        if l['id'] == lesson_id:
            l['status']      = 'rejected'
            l['rejected_at'] = datetime.datetime.now().isoformat()
            l['notes']       = reason
            data['rejected'].append(l)
            data['pending'].pop(i)
            save_lessons(data)
            print(f'🚫 נדחה: {lesson_id}')
            return True
    print(f'❌ לא נמצא: {lesson_id}')
    return False


# ══════════════════════════════════════════════════════════════
#  HTML REPORT
# ══════════════════════════════════════════════════════════════
def generate_report(data: dict,
                    date_from: 'datetime.date | None' = None,
                    date_to:   'datetime.date | None' = None) -> Path:
    data     = filter_by_date(data, date_from, date_to)
    pending  = data.get('pending',  [])
    approved = data.get('approved', [])
    rejected = data.get('rejected', [])

    def badge(conf):
        colors = {'HIGH': '#22c55e', 'MEDIUM': '#f59e0b', 'LOW': '#94a3b8'}
        return f'<span style="background:{colors.get(conf,"#94a3b8")};color:#000;padding:2px 10px;border-radius:99px;font-size:11px;font-weight:700">{conf}</span>'

    def card(lesson, section):
        ext   = lesson.get('extraction', {})
        conf  = ext.get('confidence', '?')
        bg    = {'pending_review': '#1e293b', 'approved': '#14532d', 'rejected': '#450a0a'}.get(section, '#1e293b')
        chart = '📊 ' if lesson.get('has_image') else ''
        cmds  = (
            f'<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">'
            f'<code style="background:#0f172a;padding:4px 10px;border-radius:6px;color:#4ade80;font-size:11px">'
            f'python discord_monitor.py approve {lesson["id"]}</code>'
            f'<code style="background:#0f172a;padding:4px 10px;border-radius:6px;color:#f87171;font-size:11px">'
            f'python discord_monitor.py reject  {lesson["id"]}</code></div>'
        ) if section == 'pending_review' else ''
        rule_html = (
            f'<div style="background:#0f172a;border-radius:8px;padding:10px;margin-top:10px">'
            f'<span style="color:#64748b;font-size:11px">📌 כלל מחולץ:</span>'
            f'<p style="color:#38bdf8;margin:4px 0 0 0;font-size:13px">{ext["rule_sentence"]}</p></div>'
        ) if ext.get('rule_sentence') else ''

        # ── Comparison block ──────────────────────────────────
        alignment   = ext.get('alignment', '')
        gaps        = ext.get('gaps', [])
        covered     = ext.get('covered', [])
        scanner_r   = ext.get('scanner_result', {})
        chart_a     = ext.get('chart_analysis', {})
        align_color = {'ALIGNED': '#22c55e', 'PARTIAL': '#f59e0b', 'MISALIGNED': '#ef4444'}.get(alignment, '#64748b')
        ticker_str  = chart_a.get('ticker', '')
        scanner_str = scanner_r.get('summary', '') if scanner_r else ''
        q_role      = ext.get('question_role', '')
        a_role      = ext.get('answer_role', '')
        role_icon_q = '🎓' if q_role == 'STUDENT' else '👨‍🏫'
        role_icon_a = '👨‍🏫' if a_role == 'MENTOR' else '🎓'

        gaps_html = ''
        if gaps or covered:
            rows = ''
            for g in covered:
                rows += f'<tr><td style="color:#22c55e">✅ {g["factor"]}</td><td style="color:#94a3b8">{g["expert_ref"]}</td><td style="color:#22c55e">מכוסה</td></tr>'
            for g in gaps:
                rows += f'<tr><td style="color:#f87171">❌ {g["factor"]}</td><td style="color:#94a3b8">{g["expert_ref"]}</td><td style="color:#f87171">פער — חסר</td></tr>'
            gaps_html = (
                f'<div style="background:#0f172a;border-radius:8px;padding:12px;margin-top:12px">'
                f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">'
                f'<span style="color:#64748b;font-size:11px">🔬 השוואה לסורק</span>'
                f'{"<span style=background:" + align_color + ";color:#000;padding:1px 8px;border-radius:99px;font-size:10px;font-weight:700>" + alignment + "</span>" if alignment else ""}'
                f'{"<span style=color:#94a3b8;font-size:11px> &nbsp;| " + ticker_str + " → " + scanner_str + "</span>" if ticker_str else ""}'
                f'</div>'
                f'<table style="width:100%;border-collapse:collapse;font-size:11px">'
                f'<tr style="color:#475569"><th style="text-align:right;padding:2px 0">Factor</th>'
                f'<th style="text-align:right;padding:2px 8px">הזכרת המומחה</th>'
                f'<th style="text-align:right">סטטוס</th></tr>'
                f'{rows}</table></div>'
            )

        return f'''
        <div style="background:{bg};border-radius:12px;padding:20px;margin-bottom:20px;border:1px solid #334155">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
            <span style="font-size:10px;color:#475569;font-family:monospace">{lesson["id"]}</span>
            <div>{chart}{badge(conf)}&nbsp;
              <span style="background:#334155;color:#94a3b8;padding:2px 10px;border-radius:99px;font-size:11px">
                {ext.get("suggested_factor","?")}
              </span>
            </div>
          </div>
          <div style="margin-bottom:10px">
            <span style="color:#64748b;font-size:11px">{role_icon_q} {lesson.get("question_author","?")} (תלמיד)
              &nbsp;·&nbsp; {lesson.get("timestamp","")[:20]}</span>
            <p style="color:#e2e8f0;margin:4px 0 0 0;font-size:14px;line-height:1.5">
              {lesson.get("question_text","")[:400]}</p>
          </div>
          <div style="border-top:1px solid #334155;padding-top:10px">
            <span style="color:#64748b;font-size:11px">{role_icon_a} {lesson.get("answer_author","?")} (מנטור)</span>
            <p style="color:#cbd5e1;margin:4px 0 0 0;font-size:13px;line-height:1.6">
              {lesson.get("answer_text","")[:600]}</p>
          </div>
          {rule_html}
          {gaps_html}
          <div style="margin-top:12px;font-size:11px;color:#475569">
            <b>מושג:</b> {ext.get("concept","?")} &nbsp;|&nbsp;
            <b>השפעה:</b> {ext.get("impact","?")} &nbsp;|&nbsp;
            <b>מילות מפתח:</b> {ext.get("keyword_hits",0)}
          </div>
          {cmds}
        </div>'''

    def section_html(title, items, key):
        content = ''.join(card(l, key) for l in items) if items else \
                  '<p style="color:#475569;font-size:13px">אין פריטים</p>'
        return f'<h2 style="color:#94a3b8;font-size:14px;border-bottom:1px solid #334155;padding-bottom:8px;margin-top:32px">{title} ({len(items)})</h2>{content}'

    stat_items = [
        (len(pending),                   'ממתינים'),
        (len(approved),                  'מאושרים'),
        (len(rejected),                  'נדחו'),
        (len(data.get('implemented',[])), 'יושמו'),
    ]
    stat_div = '<div style="background:#1e293b;border-radius:10px;padding:12px 20px;text-align:center">' \
               '<div style="font-size:28px;font-weight:700;color:#38bdf8">{n}</div>' \
               '<div style="font-size:11px;color:#64748b">{lbl}</div></div>'
    stats_html = ''.join(
        stat_div.format(n=n, lbl=lbl) for n, lbl in stat_items
    )

    date_range_label = ''
    if date_from or date_to:
        f_str = date_from.strftime('%d/%m/%Y') if date_from else '—'
        t_str = date_to.strftime('%d/%m/%Y')   if date_to   else '—'
        date_range_label = (
            f' &nbsp;<span style="background:#1e40af;color:#bfdbfe;padding:2px 10px;'
            f'border-radius:99px;font-size:12px">📅 {f_str} – {t_str}</span>'
        )

    html = f'''<!DOCTYPE html>
<html dir="rtl" lang="he">
<head><meta charset="UTF-8"><title>Cycles Discord Lessons</title></head>
<body style="background:#0f172a;color:#e2e8f0;margin:0;padding:24px;font-family:sans-serif">
  <h1 style="color:#38bdf8;font-size:22px;margin-bottom:4px">🎓 Cycles Trading — Discord Lessons{date_range_label}</h1>
  <p style="color:#64748b;font-size:12px">עודכן: {datetime.datetime.now().strftime("%d/%m/%Y %H:%M")} &nbsp;|&nbsp;
     <a href="{DISCORD_URL}" style="color:#38bdf8">פתח ערוץ Discord</a></p>
  <div style="display:flex;gap:16px;margin:16px 0 28px">
    {stats_html}
  </div>
  {section_html("⏳ ממתינים לבדיקה", pending,  "pending_review")}
  {section_html("✅ מאושרים לשילוב", approved, "approved")}
  {section_html("🚫 נדחו",           rejected, "rejected")}
</body></html>'''

    REPORT_FILE.write_text(html, encoding='utf-8')
    return REPORT_FILE


# ══════════════════════════════════════════════════════════════
#  DATE FILTERING
# ══════════════════════════════════════════════════════════════
def _parse_date_arg(s: str) -> datetime.date:
    """
    Accept: DD-MM-YY, DD-MM-YYYY, YYYY-MM-DD
    Examples: '1-5-26', '01-05-2026', '2026-05-01'
    """
    for fmt in ('%d-%m-%y', '%d-%m-%Y', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f'תאריך לא תקין: {s!r}  — השתמש בפורמט DD-MM-YY')


def filter_by_date(data: dict,
                   date_from: datetime.date | None,
                   date_to:   datetime.date | None) -> dict:
    """Return a copy of data with each section filtered to the given range."""
    if date_from is None and date_to is None:
        return data

    def _in_range(lesson: dict) -> bool:
        ts = lesson.get('timestamp', '')
        if not ts:
            return True
        try:
            # ISO format: 2026-05-15T14:32:00 or 2026-05-15 14:32:00
            d = datetime.datetime.fromisoformat(ts[:19]).date()
        except (ValueError, TypeError):
            return True
        if date_from and d < date_from:
            return False
        if date_to   and d > date_to:
            return False
        return True

    return {
        k: [l for l in v if _in_range(l)] if isinstance(v, list) else v
        for k, v in data.items()
    }


# ══════════════════════════════════════════════════════════════
#  REVIEW (terminal)
# ══════════════════════════════════════════════════════════════
def show_review():
    data    = load_lessons()
    pending = data.get('pending', [])
    if not pending:
        print('✅ אין שיעורים ממתינים')
        return
    print(f'\n{"═"*55}\n  ⏳ {len(pending)} שיעורים ממתינים\n{"═"*55}')
    for l in pending:
        ext = l.get('extraction', {})
        print(f'\n  {l["id"]}')
        print(f'  {l.get("timestamp","")[:20]}  ❓{l["question_author"]} → 💡{l["answer_author"]}')
        print(f'  Factor: {ext.get("suggested_factor","?")}  Conf: {ext.get("confidence","?")}  Image: {"✅" if l.get("has_image") else "❌"}')
        q = l["question_text"]
        print(f'  שאלה: {q[:110]}{"..." if len(q)>110 else ""}')
        if ext.get('rule_sentence'):
            print(f'  📌 {ext["rule_sentence"][:110]}')
        print(f'  ✅ approve: python discord_monitor.py approve {l["id"]}')
        print(f'  🚫 reject:  python discord_monitor.py reject  {l["id"]}')


def show_stats():
    data = load_lessons()
    print(f'\n  📊 Cycles Discord Lessons')
    print(f'  ממתינים:  {len(data.get("pending",[]))}')
    print(f'  מאושרים: {len(data.get("approved",[]))}')
    print(f'  נדחו:     {len(data.get("rejected",[]))}')
    print(f'  יושמו:   {len(data.get("implemented",[]))}')


# ══════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    args = sys.argv[1:]

    if not args or args[0] == 'review':
        show_review()

    elif args[0] == 'process' and len(args) >= 2:
        process_messages_file(args[1])

    elif args[0] == 'report':
        # Optional flags: --from DD-MM-YY  --to DD-MM-YY
        date_from = date_to = None
        rest = args[1:]
        i = 0
        while i < len(rest):
            if rest[i] in ('--from', '-f') and i + 1 < len(rest):
                date_from = _parse_date_arg(rest[i + 1]); i += 2
            elif rest[i] in ('--to', '-t') and i + 1 < len(rest):
                date_to = _parse_date_arg(rest[i + 1]); i += 2
            else:
                i += 1
        data = load_lessons()
        p    = generate_report(data, date_from=date_from, date_to=date_to)
        if date_from or date_to:
            f_s = date_from.strftime('%d/%m/%Y') if date_from else '—'
            t_s = date_to.strftime('%d/%m/%Y')   if date_to   else '—'
            print(f'📅 סינון: {f_s} → {t_s}')
        print(f'📊 {p}')
        import webbrowser; webbrowser.open(str(p))

    elif args[0] == 'approve' and len(args) >= 2:
        approve_lesson(args[1])

    elif args[0] == 'reject' and len(args) >= 2:
        reason = ' '.join(args[2:]) if len(args) > 2 else ''
        reject_lesson(args[1], reason)

    elif args[0] == 'stats':
        show_stats()

    else:
        print(__doc__)
