"""
preprocess_discord.py  —  ממיר Discord fiber JSON לפורמט discord_monitor.py

Type 18 = מנטור פתח thread בתגובה לשאלת תלמיד.
הסקריפט:
  1. מזהה usernames של מנטורים (מי שפותח threads)
  2. מקשר כל type-18 לשאלת התלמיד הקדמת-זמן שתוכנה מתחיל עם תוכן ה-type-18
  3. שומר קובץ מוכן לdiscord_monitor.py process
  4. מדפיס רשימת mentors שנמצאו (להוספה ל-MENTOR_NAMES)
"""
import json, re, sys
from pathlib import Path

INPUT  = Path(r'C:\Users\omare\Downloads\discord_may_june_2026.json')
OUTPUT = Path(r'C:\Users\omare\Downloads\discord_processed.json')

msgs = json.loads(INPUT.read_text(encoding='utf-8'))

# ── 1. זהה מנטורים — אלה שפותחים type-18 (threads)
mentor_usernames = {m['author'] for m in msgs if m.get('type') == 18 and m.get('author')}
print(f'מנטורים שזוהו ({len(mentor_usernames)}): {sorted(mentor_usernames)}')

# ── 2. קשר type-18 לשאלה המתאימה
#    Discord מחזיר ב-type-18 תוכן = תחילת השאלה (truncated)
#    חפש type-0 שתוכנו מתחיל עם תוכן ה-type-18 (או מכיל אותו)

def normalize(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '').strip())[:60]

# בנה lookup: normalized content → message index
content_to_idx: dict[str, int] = {}
for i, m in enumerate(msgs):
    if m.get('type') == 0:
        key = normalize(m.get('content', ''))
        content_to_idx[key] = i

def find_matching_question(thread_content: str, search_from: int) -> int | None:
    """מצא את השאלה (type-0) שה-thread נפתח בתגובה לה."""
    key = normalize(thread_content)
    # חפש אחורה מ-search_from
    for j in range(search_from - 1, max(-1, search_from - 30), -1):
        m = msgs[j]
        if m.get('type') != 0:
            continue
        q_key = normalize(m.get('content', ''))
        if key and (q_key.startswith(key) or key in q_key):
            return j
    # fallback: ה-type-0 הקרוב ביותר לפני ה-type-18
    for j in range(search_from - 1, max(-1, search_from - 5), -1):
        if msgs[j].get('type') == 0:
            return j
    return None

# ── 3. בנה רשימה מוכנה
output_msgs = []
paired_questions: set[int] = set()

for i, m in enumerate(msgs):
    if m.get('type') == 18:
        q_idx = find_matching_question(m.get('content', ''), i)
        if q_idx is not None:
            student_msg = msgs[q_idx]
            # הוסף שאלה עם is_reply_to=None
            if q_idx not in paired_questions:
                output_msgs.append({
                    'author':      student_msg['author'],
                    'content':     student_msg['content'],
                    'timestamp':   student_msg['timestamp'],
                    'is_reply_to': None,
                    'has_image':   student_msg.get('has_image', False),
                    'image_urls':  student_msg.get('image_urls', []),
                    'role':        'STUDENT',
                })
                paired_questions.add(q_idx)
            # הוסף תשובת מנטור עם is_reply_to=שם התלמיד
            output_msgs.append({
                'author':      m['author'],
                'content':     f"[thread reply to: {student_msg['content'][:120]}]",
                'timestamp':   m['timestamp'],
                'is_reply_to': student_msg['author'],
                'has_image':   m.get('has_image', False),
                'image_urls':  m.get('image_urls', []),
                'role':        'MENTOR',
            })
    elif m.get('type') == 0 and i not in paired_questions:
        # הודעת תלמיד ללא תשובה — כלול בכל זאת
        output_msgs.append({
            'author':      m['author'],
            'content':     m['content'],
            'timestamp':   m['timestamp'],
            'is_reply_to': m.get('is_reply_to'),
            'has_image':   m.get('has_image', False),
            'image_urls':  m.get('image_urls', []),
            'role':        'STUDENT',
        })

# ── 4. מיין לפי timestamp
output_msgs.sort(key=lambda x: x['timestamp'])

# ── 5. שמור
OUTPUT.write_text(json.dumps(output_msgs, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'\n✅ שמור {len(output_msgs)} הודעות ל-{OUTPUT}')

# ── 6. זוגות שזוהו
pairs = [(m['is_reply_to'], m['author']) for m in output_msgs if m['is_reply_to']]
print(f'🔗 {len(pairs)} זוגות שאלה-תשובה:')
for student, mentor in pairs[:10]:
    print(f'   {student:25s} → {mentor}')
if len(pairs) > 10:
    print(f'   ... ועוד {len(pairs)-10}')

print(f'\n📋 העתק ל-MENTOR_NAMES ב-discord_monitor.py:')
print('MENTOR_NAMES = {')
for u in sorted(mentor_usernames):
    print(f"    '{u}',")
print('}')
