"""Append a scheduled-run record to run_history.json.

Called by the Task Scheduler .bat files so scheduler-triggered runs show up
in the dashboard's Run History next to manual (dashboard-launched) jobs.

Usage: python ct_record_run.py <task> <label> <start_epoch> <exit_code>
"""
import sys, json, time, uuid, os, datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
HF   = BASE / 'run_history.json'


def main():
    task  = sys.argv[1]
    label = sys.argv[2]
    t0    = int(sys.argv[3])
    code  = int(sys.argv[4])
    now   = time.time()
    icons = {'daily': '\U0001F514', 'weekly': '\U0001F4CA', 'momentum': '\U0001F680',
             'review': '\U0001F4CB', 'monthly': '\U0001F4C5', 'pipeline': '⚡',
             'evening': '\U0001F319'}
    entry = {
        'id':           uuid.uuid4().hex[:8],
        'task':         task,
        'label':        label,
        'icon':         icons.get(task, '⚙'),
        'triggered_by': 'scheduler',
        'started':      datetime.datetime.fromtimestamp(t0).strftime('%Y-%m-%d %H:%M'),
        'ended':        datetime.datetime.fromtimestamp(now).strftime('%H:%M'),
        'status':       'done' if code == 0 else 'error',
        'duration_sec': int(now - t0),
    }
    try:
        hist = json.loads(HF.read_text(encoding='utf-8')) if HF.exists() else []
        if not isinstance(hist, list):
            hist = []
    except Exception:
        hist = []
    hist.append(entry)
    tmp = HF.with_suffix('.tmp')
    tmp.write_text(json.dumps(hist[-100:], indent=2), encoding='utf-8')
    os.replace(tmp, HF)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:                      # never break the calling .bat
        print('record_run failed:', e)
