#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ct_pipeline.py — Unified Cycles Trading Pipeline

Decision engine: determines which scans to run based on today's date,
runs them in order, and emails the HTML reports.

Decision table:
  Sunday           → weekly + momentum + review + daily
  1st Sunday/month → + monthly scan
  Mon-Sat          → daily only

Usage:
  python ct_pipeline.py                      # auto-decide by date
  python ct_pipeline.py --force all          # force all scans
  python ct_pipeline.py --force weekly       # only weekly retest
  python ct_pipeline.py --force monthly      # only monthly scan
  python ct_pipeline.py --force daily        # only watch checker
  python ct_pipeline.py --force weekly,monthly,daily
  python ct_pipeline.py --dry-run            # show what would run
  python ct_pipeline.py --email              # force email even on weekday
"""

import os, sys, subprocess, datetime, time, smtplib
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from email.mime.base      import MIMEBase
from email                import encoders

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass

# Manual .env fallback — python-dotenv is often not installed, which silently
# killed the pipeline summary email ("Email skipped — no credentials in .env")
def _load_env_fallback():
    env_file = Path(__file__).parent / '.env'
    if env_file.exists():
        for line in env_file.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())
_load_env_fallback()

# ---------------------------------------------------------------------------
#  Paths & constants
# ---------------------------------------------------------------------------

BASE_DIR    = Path(__file__).parent
REPORTS_DIR = BASE_DIR / 'REPORTS'
LOG_DIR     = BASE_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

TODAY   = datetime.date.today()
NOW     = datetime.datetime.now()
WEEKDAY = TODAY.weekday()   # 0=Mon … 6=Sun
DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

LOG_FILE = LOG_DIR / f'pipeline_{TODAY.isoformat()}.log'

# Email credentials are read inside send_email() so they pick up any late .env load

TASK_META = {
    'weekly':   ('Weekly Retest Scan',  'cycles_report_*.html'),
    'momentum': ('Momentum Scan',       'momentum_report_*.html'),
    'review':   ('Weekly Review',       'weekly_review_*.html'),
    'monthly':  ('Monthly S/R Scan',    'monthly_scan_*.html'),
    'daily':    ('Daily Watch Checker', 'watch_report_*.html'),
}


# ---------------------------------------------------------------------------
#  Logging
# ---------------------------------------------------------------------------

def _log(msg: str):
    ts   = datetime.datetime.now().strftime('%H:%M:%S')
    line = f'[{ts}] {msg}'
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode('ascii', errors='replace').decode())
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


# ---------------------------------------------------------------------------
#  Decision engine
# ---------------------------------------------------------------------------

def decide_tasks(force: str = '') -> list:
    """Return ordered list of task names to run."""
    if force == 'all':
        return ['weekly', 'momentum', 'review', 'monthly', 'daily']
    if force:
        return [t.strip() for t in force.split(',') if t.strip() in TASK_META]

    tasks = []

    # Sunday → full weekly cycle
    if WEEKDAY == 6:
        tasks += ['weekly', 'momentum', 'review']

    # First Sunday of month → also monthly scan (runs BEFORE daily)
    if WEEKDAY == 6 and TODAY.day <= 7:
        tasks.append('monthly')

    # Every day → watch checker
    tasks.append('daily')

    return tasks


# ---------------------------------------------------------------------------
#  Task runners
# ---------------------------------------------------------------------------

def _latest_report(pattern: str) -> 'Path | None':
    files = sorted(REPORTS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _run(label: str, cmd: list) -> bool:
    _log(f'>>> {label}')
    t0  = time.time()
    env = os.environ.copy()
    env.setdefault('CT_PORTFOLIO_SIZE', '25000')
    # Force unbuffered + UTF-8 so output streams in real-time, even inside a pipe
    env['PYTHONUNBUFFERED']  = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8']       = '1'
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(BASE_DIR), env=env,
            stdin=subprocess.DEVNULL,          # no interactive prompts
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace'
        )
        # Watchdog: the old TimeoutExpired handler was dead code (Popen has no
        # timeout) — a hung scan blocked the pipeline forever. Kill after 45min.
        import threading as _th
        _killed = {'flag': False}
        def _kill():
            _killed['flag'] = True
            try:
                proc.kill()
            except Exception:
                pass
        _watchdog = _th.Timer(2700, _kill)
        _watchdog.daemon = True
        _watchdog.start()
        last_lines = []
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            last_lines.append(line)
            if len(last_lines) > 8:
                last_lines.pop(0)
            # Stream to our own stdout (captured by dashboard)
            try:
                sys.stdout.write(line + '\n')
                sys.stdout.flush()
            except UnicodeEncodeError:
                pass
        stderr = proc.stderr.read()
        proc.wait()
        _watchdog.cancel()
        elapsed = time.time() - t0
        if _killed['flag']:
            _log(f'    TIMEOUT — killed after 45min (scan hung)')
            return False
        ok = proc.returncode == 0
        _log(f'    {"OK" if ok else f"FAILED (rc={proc.returncode})"} in {elapsed:.0f}s')
        if not ok and stderr:
            _log(f'    STDERR: {stderr[-300:]}')
        return ok
    except Exception as e:
        _log(f'    EXCEPTION: {e}')
        return False


def _run_with_new_report(label: str, cmd: list, pattern: str) -> 'Path | None':
    """Run a scanner command and return its report only if a NEW file was created.
    If the scan succeeded but produced no new HTML (0 setups), log that clearly
    instead of silently returning yesterday's report.
    """
    before = _latest_report(pattern)
    before_mtime = before.stat().st_mtime if before else 0.0

    ok = _run(label, cmd)
    if not ok:
        return None

    after = _latest_report(pattern)
    if after and after.stat().st_mtime > before_mtime + 1:
        # A genuinely new report was written
        return after

    # Scanner ran OK but no new report — 0 setups found
    _log(f'    0 setups found — no new report generated')
    return None


def run_weekly() -> 'Path | None':
    return _run_with_new_report(
        'Weekly Retest Scan',
        [sys.executable, 'cycles_trading_scanner.py'],
        'cycles_report_*.html',
    )


def run_momentum() -> 'Path | None':
    return _run_with_new_report(
        'Momentum Scan',
        [sys.executable, 'cycles_trading_scanner.py', 'momentum'],
        'momentum_report_*.html',
    )


def run_review() -> 'Path | None':
    return _run_with_new_report(
        'Weekly Review',
        [sys.executable, 'ct_weekly_review.py'],
        'weekly_review_*.html',
    )


def run_monthly() -> 'Path | None':
    ok = _run('Monthly S/R Scan',
              [sys.executable, 'cycles_trading_scanner.py', 'monthly'])
    return _latest_report('monthly_scan_*.html') if ok else None


def run_daily() -> 'Path | None':
    ok = _run('Daily Watch Checker', [sys.executable, 'ct_watch_checker.py'])
    return _latest_report('watch_report_*.html') if ok else None


RUNNERS = {
    'weekly':   run_weekly,
    'momentum': run_momentum,
    'review':   run_review,
    'monthly':  run_monthly,
    'daily':    run_daily,
}


# ---------------------------------------------------------------------------
#  Email summary + attachments
# ---------------------------------------------------------------------------

def send_email(tasks_done: list, reports: dict, dry_run: bool = False):
    EMAIL_FROM = os.environ.get('ALERT_EMAIL_FROM', '')
    EMAIL_TO   = os.environ.get('ALERT_EMAIL_TO', '')
    EMAIL_PWD  = os.environ.get('ALERT_EMAIL_PASSWORD', '')
    if not EMAIL_FROM or not EMAIL_PWD:
        _log('Email skipped — no credentials in .env')
        return

    today_str = TODAY.strftime('%d/%m/%Y')
    subject   = f'Cycles Trading Pipeline — {today_str} ({DAY_NAMES[WEEKDAY]})'

    # Build summary rows
    rows = ''
    for task in tasks_done:
        label, _ = TASK_META.get(task, (task, ''))
        rep      = reports.get(task)
        if rep:
            cell = f'<span style="color:#22c55e">&#10003; {rep.name}</span>'
        else:
            cell = '<span style="color:#ef4444">&#10007; no report</span>'
        rows += f'<tr><td style="padding:8px 12px">{label}</td>' \
                f'<td style="padding:8px 12px">{cell}</td></tr>'

    body = f'''
<html>
<body style="font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;
             padding:24px;margin:0">
  <h2 style="color:#58a6ff;margin:0 0 6px">
    Cycles Trading Pipeline</h2>
  <p style="color:#94a3b8;margin:0 0 20px;font-size:13px">
    {today_str} &mdash; {NOW.strftime("%H:%M")}
    {'&nbsp;&nbsp;<span style="color:#f59e0b">[DRY RUN]</span>' if dry_run else ''}
  </p>
  <table style="border-collapse:collapse;background:#1e293b;
                border-radius:8px;overflow:hidden;min-width:420px">
    <thead>
      <tr style="background:#0f172a">
        <th style="padding:10px 12px;text-align:left;color:#6e7681;
                   font-size:11px;text-transform:uppercase">Scan</th>
        <th style="padding:10px 12px;text-align:left;color:#6e7681;
                   font-size:11px;text-transform:uppercase">Result</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <p style="color:#334155;font-size:11px;margin-top:20px">
    HTML reports attached &mdash; open in browser for full view.<br>
    GREEN alerts are sent separately by the watch checker.
  </p>
</body>
</html>'''

    msg = MIMEMultipart()
    msg['From']    = EMAIL_FROM
    msg['To']      = EMAIL_TO
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html', 'utf-8'))

    # Attach HTML reports (skip watch_report — watch checker handles that)
    for task, rep in reports.items():
        if task == 'daily':
            continue   # watch checker already emails GREEN hits
        if rep and rep.exists():
            try:
                with open(rep, 'rb') as f:
                    part = MIMEBase('text', 'html')
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header(
                    'Content-Disposition',
                    f'attachment; filename="{rep.name}"')
                msg.attach(part)
            except Exception as e:
                _log(f'    Attach failed for {rep.name}: {e}')

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30) as s:
            s.login(EMAIL_FROM, EMAIL_PWD)
            s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        _log(f'Email sent → {EMAIL_TO}')
    except Exception as e:
        _log(f'Email FAILED: {e}')


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    args          = sys.argv[1:]
    dry_run       = '--dry-run' in args
    force_email   = '--email'   in args

    force = ''
    if '--force' in args:
        idx   = args.index('--force')
        force = args[idx + 1] if idx + 1 < len(args) else ''

    tasks = decide_tasks(force)

    _log('=' * 60)
    _log(f'Cycles Trading Pipeline — {TODAY} ({DAY_NAMES[WEEKDAY]})')
    _log(f'Tasks planned: {", ".join(tasks)}')
    if dry_run:
        _log('[DRY RUN] — no scans will run')
    _log('=' * 60)

    if dry_run:
        for task in tasks:
            label, _ = TASK_META.get(task, (task, ''))
            _log(f'  WOULD RUN: {label}')
        _log('Done (dry run).')
        return

    reports = {}
    for task in tasks:
        fn = RUNNERS.get(task)
        if fn:
            reports[task] = fn()
        else:
            _log(f'Unknown task: {task}')
        _log('')

    # Summary
    _log('-' * 60)
    _log('Pipeline summary:')
    any_ok = False
    for task in tasks:
        label, _ = TASK_META.get(task, (task, ''))
        rep     