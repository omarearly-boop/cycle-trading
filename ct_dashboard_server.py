#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ct_dashboard_server.py — Cycles Trading Pipeline Dashboard

Local web server. Open http://localhost:5050 in your browser.

Usage:
  python ct_dashboard_server.py
  python ct_dashboard_server.py --port 8080
"""

import os, sys, json, time, uuid, threading, subprocess, datetime
from pathlib import Path

try:
    from flask import Flask, jsonify, request, Response
except ImportError:
    print('Installing Flask...')
    subprocess.run([sys.executable, '-m', 'pip', 'install', 'flask',
                    '--break-system-packages', '-q'], check=False)
    from flask import Flask, jsonify, request, Response

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Manual .env fallback (python-dotenv often not installed)
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
BASE_DIR    = Path(__file__).parent
REPORTS_DIR  = BASE_DIR / 'REPORTS'
HISTORY_FILE = BASE_DIR / 'run_history.json'
REPORTS_DIR.mkdir(exist_ok=True)

HIST_LOCK = threading.Lock()

# Live price check cache (background thread, refreshed every 60 s)
LIVECHECK_CACHE = {'ts': 0.0, 'data': [], 'running': False}
LIVE_LOCK       = threading.Lock()


_STATUS_ZONE = {
    'GREEN':       'IN_ZONE',
    'YELLOW':      'WAIT',
    'RED':         'NOT_YET',
    'NO_LEVEL':    'NOT_YET',
    'FETCH_ERROR': 'UNKNOWN',
}

def _zone_status(entry: dict, price: float) -> str:
    """Map watch-checker status to display zone.
    Uses the stored status field (already computed by ct_watch_checker.py)
    rather than re-deriving from live price, so the dashboard stays in sync
    with the checker's logic.
    Falls back to price-vs-entry heuristic if status is missing.
    """
    stored = entry.get('status', '')
    if stored in _STATUS_ZONE:
        return _STATUS_ZONE[stored]

    # Fallback: price-vs-entry heuristic
    if not price or not entry.get('entry_price'):
        return 'UNKNOWN'
    ep  = float(entry['entry_price'])
    dir = entry.get('direction', 'LONG')
    pct = (price - ep) / ep * 100

    if dir == 'LONG':
        if 0.0 <= pct <= 1.0:  return 'IN_ZONE'   # at support
        if 1.0 < pct <= 7.0:   return 'WAIT'       # approaching support
        if pct > 7.0:          return 'NOT_YET'    # too far above
        return 'NOT_YET'                            # below support = broken
    else:
        if -1.0 <= pct <= 0.0: return 'IN_ZONE'
        if -7.0 <= pct < -1.0: return 'WAIT'
        return 'NOT_YET'


def _refresh_livecheck() -> None:
    """Fetch live prices for all watchlist tickers and update cache."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    wf = BASE_DIR / 'watch_alerts.json'
    if not wf.exists():
        return
    try:
        raw  = wf.read_bytes()
        text = raw.decode('utf-8', errors='replace')
        try:
            wdata = json.loads(text)
        except json.JSONDecodeError:
            for sep in ('\r\n    {', '\n    {'):
                idx = text.rfind('},' + sep[:-1])
                if idx >= 0:
                    text = text[:idx + 1] + '\n  ]\n}'
                    break
            wdata = json.loads(text)
        entries = wdata.get('tickers', [])
    except Exception:
        return

    if not entries:
        return

    try:
        import yfinance as yf
    except ImportError:
        return

    # Respect the global Yahoo throttle — this loop previously fired 20
    # parallel unthrottled requests for the whole watchlist every 60s,
    # a major contributor to YFRateLimitError during scans.
    try:
        from ct_market_data import _yf_throttle
    except Exception:
        _yf_throttle = lambda: None

    def _fetch(entry):
        try:
            _yf_throttle()
            fi = yf.Ticker(entry['ticker']).fast_info
            p  = (fi.get('last_price') or fi.get('lastPrice')
                  or fi.get('regularMarketPrice'))
            return entry['ticker'], float(p) if p else None
        except Exception:
            return entry['ticker'], None

    prices = {}
    with ThreadPoolExecutor(max_workers=min(4, len(entries))) as pool:
        futs = {pool.submit(_fetch, e): e for e in entries}
        for fut in as_completed(futs):
            tk, px = fut.result()
            prices[tk] = px

    def _cur_symbol(tk: str) -> str:
        for suf, sym in (('.TA', 'ILA '), ('.L', 'GBX '), ('.DE', '€'), ('.PA', '€'),
                         ('.T', '¥'), ('.HK', 'HK$'), ('.TO', 'C$'), ('.AX', 'A$'),
                         ('.SW', 'CHF '), ('.NS', '₹')):
            if tk.endswith(suf):
                return sym
        return '$'

    results = []
    for entry in entries:
        price    = prices.get(entry['ticker'])
        ep_raw   = entry.get('entry_price')
        zone     = _zone_status(entry, price)   # reads stored status first; price used as fallback only
        pct_away = None
        if price and ep_raw:
            pct_away = round((price - float(ep_raw)) / float(ep_raw) * 100, 1)
        results.append({
            'ticker':       entry['ticker'],
            'direction':    entry.get('direction', ''),
            'entry_price':  ep_raw,
            'stop_price':   entry.get('stop_price'),
            'target_price': entry.get('target_price'),
            'rr':           entry.get('rr'),
            'prob':         entry.get('prob'),
            'cur_price':    round(price, 2) if price else None,
            'pct_away':     pct_away,
            'zone':         zone,
            'timeframe':    entry.get('timeframe', 'WEEKLY'),
            'cur':          _cur_symbol(entry['ticker']),
        })

    with LIVE_LOCK:
        LIVECHECK_CACHE['ts']      = time.time()
        LIVECHECK_CACHE['data']    = results
        LIVECHECK_CACHE['running'] = False


def _livecheck_loop() -> None:
    """Background thread: refresh live prices every 5 minutes.
    (Was 60s — with a 135-ticker watchlist that meant ~2 Yahoo requests/sec
    around the clock, starving the scanner of rate-limit headroom.)"""
    time.sleep(6)          # small startup delay
    while True:
        try:
            with LIVE_LOCK:
                LIVECHECK_CACHE['running'] = True
            _refresh_livecheck()
        except Exception:
            with LIVE_LOCK:
                LIVECHECK_CACHE['running'] = False
        time.sleep(300)


threading.Thread(target=_livecheck_loop, daemon=True).start()


def _append_history(entry: dict) -> None:
    """Append one run record to run_history.json (keep last 100)."""
    with HIST_LOCK:
        try:
            hist = json.loads(HISTORY_FILE.read_text(encoding='utf-8')) if HISTORY_FILE.exists() else []
        except Exception:
            hist = []
        hist.append(entry)
        HISTORY_FILE.write_text(json.dumps(hist[-100:], indent=2), encoding='utf-8')


app      = Flask(__name__)
JOBS     = {}
JOB_LOCK = threading.Lock()

TASKS = {
    'weekly': {
        'label':    'Cycles Report',
        'icon':     '📊',
        'desc':     'RSI 30-67 · Near S/R · 42 Factors',
        'schedule': 'Mon-Fri 23:15 + Sunday 08:00',
        'color':    '#3fb950',
        'cmd':      [sys.executable, 'cycles_trading_scanner.py'],
        'report':   'cycles_report_*.html',
    },
    'momentum': {
        'label':    'Momentum',
        'icon':     '🚀',
        'desc':     'RSI 55-78 · Above MA20/50 · SPY >2%',
        'schedule': 'Mon-Fri 23:15 + Sunday 08:00',
        'color':    '#d29922',
        'cmd':      [sys.executable, 'cycles_trading_scanner.py', 'momentum'],
        'report':   'momentum_report_*.html',
    },
    'review': {
        'label':    'Weekly Review',
        'icon':     '📋',
        'desc':     'What moved · Improve logic',
        'schedule': 'Sunday 08:00',
        'color':    '#a371f7',
        'cmd':      [sys.executable, 'ct_weekly_review.py'],
        'report':   'weekly_review_*.html',
    },
    'monthly': {
        'label':    'Monthly S/R',
        'icon':     '🗓️',
        'desc':     'Monthly levels · Fibonacci golden zone',
        'schedule': '1st Sunday / month',
        'color':    '#79c0ff',
        'cmd':      [sys.executable, 'cycles_trading_scanner.py', 'monthly'],
        'report':   'monthly_scan_*.html',
    },
    'daily': {
        'label':    'Watch Checker',
        'icon':     '🔔',
        'desc':     'Checks watchlist · Email on GREEN hit',
        'schedule': 'Hourly Mon-Fri 16:30-23:30 (US session)',
        'color':    '#f85149',
        'cmd':      [sys.executable, 'ct_watch_checker.py'],
        'report':   'watch_report_*.html',
    },
    'pipeline': {
        'label':    'Full Pipeline',
        'icon':     '⚡',
        'desc':     "Runs today's scheduled tasks",
        'schedule': 'Hourly Mon-Fri (auto)',
        'color':    '#58a6ff',
        'cmd':      [sys.executable, 'ct_pipeline.py'],
        'report':   None,
    },
    'all': {
        'label':    'Full Workflow',
        'icon':     '🔥',
        'desc':     'Runs ALL scans regardless of schedule',
        'schedule': 'Manual only',
        'color':    '#f85149',
        'cmd':      [sys.executable, 'ct_pipeline.py', '--force', 'all'],
        'report':   None,
    },
}


# ---------------------------------------------------------------------------
# Job runner (background thread)
# ---------------------------------------------------------------------------

def _run_job(job_id: str, task: str):
    info    = TASKS[task]
    t_start = JOBS[job_id].get('start', time.time())
    env     = os.environ.copy()
    env.setdefault('CT_PORTFOLIO_SIZE', '25000')
    env['PYTHONUNBUFFERED']  = '1'
    env['PYTHONUTF8']        = '1'
    env['PYTHONIOENCODING']  = 'utf-8'

    with JOB_LOCK:
        JOBS[job_id]['status'] = 'running'

    lines = [f'▶  Starting: {info["label"]}', '']
    try:
        proc = subprocess.Popen(
            info['cmd'], cwd=str(BASE_DIR), env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace'
        )
        with JOB_LOCK:
            JOBS[job_id]['proc'] = proc
        for raw in proc.stdout:
            lines.append(raw.rstrip())
            with JOB_LOCK:
                JOBS[job_id]['log'] = lines[:]
        proc.wait()
        status = 'done' if proc.returncode == 0 else 'error'
        lines += ['', f'■  Exit code: {proc.returncode}']
    except Exception as e:
        status = 'error'
        lines += ['', f'✗  Exception: {e}']

    t_end = time.time()
    with JOB_LOCK:
        JOBS[job_id].update({'status': status, 'log': lines, 'end': t_end})
        # Keep only last 50 completed jobs to prevent unbounded memory growth
        if len(JOBS) > 50:
            # Remove oldest completed/error jobs first
            done_ids = [jid for jid, j in JOBS.items()
                        if j.get('status') in ('done', 'error', 'stopped') and jid != job_id]
            for jid in list(done_ids)[:-49]:
                JOBS.pop(jid, None)

    _append_history({
        'id':           job_id,
        'task':         task,
        'label':        info['label'],
        'icon':         info.get('icon', '⚙'),
        'triggered_by': os.environ.get('CT_TRIGGERED_BY', 'manual'),
        'started':      datetime.datetime.fromtimestamp(t_start).strftime('%Y-%m-%d %H:%M'),
        'ended':        datetime.datetime.fromtimestamp(t_end).strftime('%H:%M'),
        'status':       status,
        'duration_sec': max(0, int(t_end - t_start)),
    })


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route('/api/today')
def api_today():
    now     = datetime.datetime.now()
    weekday = now.weekday()
    planned = []
    if weekday == 6:
        planned += ['weekly', 'momentum', 'review']
        if now.day <= 7:
            planned.append('monthly')
    planned.append('daily')
    return jsonify({
        'day':     ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][weekday],
        'date':    now.strftime('%d/%m/%Y'),
        'time':    now.strftime('%H:%M'),
        'planned': planned,
    })


@app.route('/api/schedule')
def api_schedule():
    """Next scheduled event, mirroring the Task Scheduler setup:
      - Watch Checker : hourly at :30, Mon-Fri 16:30-23:30 (US session)
      - Evening Scan  : Mon-Fri 23:15 (Cycles + Momentum + Checker)
      - Sunday 08:00  : full pipeline (review, monthly on 1st Sunday)
    """
    now    = datetime.datetime.now()
    events = []   # (datetime, [tasks])
    for d in range(0, 8):
        day = now.date() + datetime.timedelta(days=d)
        wd  = day.weekday()
        if wd < 5:   # Mon-Fri
            for h in range(16, 24):
                t = datetime.datetime.combine(day, datetime.time(h, 30))
                if t > now:
                    events.append((t, ['daily']))
                    break   # only the next hourly slot matters
            t = datetime.datetime.combine(day, datetime.time(23, 15))
            if t > now:
                events.append((t, ['weekly', 'momentum', 'daily']))
        elif wd == 6:   # Sunday
            t = datetime.datetime.combine(day, datetime.time(8, 0))
            if t > now:
                tasks = ['weekly', 'momentum', 'review', 'daily']
                if day.day <= 7:
                    tasks.insert(3, 'monthly')
                events.append((t, tasks))
    events.sort(key=lambda x: x[0])
    nxt, tasks = events[0]
    if   nxt.date() == now.date():                                day_lbl = 'Today'
    elif nxt.date() == now.date() + datetime.timedelta(days=1):   day_lbl = 'Tomorrow'
    else: day_lbl = nxt.strftime('%a')
    mins_away = max(0, int((nxt - now).total_seconds() / 60))
    remaining = [t.strftime('%H:%M') for t, _ in events if t.date() == now.date()][:6]
    LABELS = {'weekly': 'Cycles Report', 'momentum': 'Momentum',
              'review': 'Weekly Review',  'monthly':  'Monthly S/R',
              'daily':  'Watch Checker'}
    return jsonify({
        'next_label':       f"{day_lbl} {nxt.strftime('%H:%M')}",
        'mins_until':       mins_away,
        'next_tasks':       tasks,
        'next_task_labels': [LABELS[t] for t in tasks],
        'remaining_today':  remaining,
    })


@app.route('/api/run/<task>', methods=['POST'])
def api_run(task):
    if task not in TASKS:
        return jsonify({'error': f'Unknown task: {task}'}), 400
    job_id = str(uuid.uuid4())[:8]
    with JOB_LOCK:
        JOBS[job_id] = {'task': task, 'status': 'starting',
                        'log': [], 'start': time.time(), 'end': None}
    threading.Thread(target=_run_job, args=(job_id, task), daemon=True).start()
    return jsonify({'job_id': job_id})


@app.route('/api/job/<job_id>')
def api_job(job_id):
    with JOB_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'not found'}), 404
    elapsed = int(time.time() - job['start'])
    return jsonify({'task': job['task'], 'status': job['status'],
                    'log': job['log'], 'elapsed': elapsed})


@app.route('/api/stop/<job_id>', methods=['POST'])
def api_stop(job_id):
    with JOB_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'not found'}), 404
    proc = job.get('proc')
    if proc and proc.poll() is None:
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                               capture_output=True)
            else:
                import signal
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try: proc.kill()
            except Exception: pass
        with JOB_LOCK:
            JOBS[job_id]['status'] = 'stopped'
            JOBS[job_id].setdefault('log', []).extend(['', '■  Stopped by user.'])
    return jsonify({'ok': True})


@app.route('/api/stop/all', methods=['POST'])
def api_stop_all():
    with JOB_LOCK:
        running = [(jid, job) for jid, job in JOBS.items()
                   if job.get('status') in ('running', 'starting')]
    count = 0
    for jid, job in running:
        proc = job.get('proc')
        if proc and proc.poll() is None:
            try:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                                   capture_output=True)
                else:
                    import signal
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                try: proc.kill()
                except Exception: pass
            with JOB_LOCK:
                JOBS[jid]['status'] = 'stopped'
                JOBS[jid].setdefault('log', []).extend(['', '■  Stopped by user.'])
            count += 1
    return jsonify({'stopped': count})


@app.route('/api/reports')
def api_reports():
    PREFIXES = ['cycles_report','momentum_report','weekly_review','monthly_scan','watch_report','calibration_report','winner_autopsy']
    all_files = sorted(REPORTS_DIR.glob('*.html'), key=lambda p: p.stat().st_mtime, reverse=True)
    seen, out = set(), []
    for f in all_files:
        prefix = next((p for p in PREFIXES if f.name.startswith(p)), f.name)
        if prefix not in seen:
            seen.add(prefix)
            out.append({'name': f.name, 'size': f.stat().st_size, 'mtime': int(f.stat().st_mtime)})
        if len(out) >= 10:
            break
    return jsonify(out)


@app.route('/api/watchlist')
def api_watchlist():
    wf = BASE_DIR / 'watch_alerts.json'
    try:
        return jsonify(json.loads(wf.read_text(encoding='utf-8')))
    except Exception:
        return jsonify({'tickers': []})


@app.route('/api/history')
def api_history():
    try:
        hist = json.loads(HISTORY_FILE.read_text(encoding='utf-8')) if HISTORY_FILE.exists() else []
    except Exception:
        hist = []
    return jsonify(list(reversed(hist[-25:])))


@app.route('/api/hotlist')
def api_hotlist():
    wf = BASE_DIR / 'watch_alerts.json'
    if not wf.exists():
        return jsonify({'tickers': [], 'updated': None, 'total': 0})
    try:
        raw  = wf.read_bytes()
        text = raw.decode('utf-8', errors='replace')
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Recover from file truncated mid-write
            for sep in ('\r\n    {', '\n    {'):
                idx = text.rfind('},' + sep[:-1])
                if idx >= 0:
                    text = text[:idx + 1] + '\n  ]\n}'
                    break
            data = json.loads(text)
        tickers = data.get('tickers', [])
        updated = max((t.get('last_checked', '') for t in tickers), default=None)
        return jsonify({'tickers': tickers, 'updated': updated, 'total': len(tickers)})
    except Exception as e:
        return jsonify({'tickers': [], 'updated': None, 'total': 0, 'error': str(e)})


@app.route('/api/livecheck')
def api_livecheck():
    with LIVE_LOCK:
        ts      = LIVECHECK_CACHE['ts']
        data    = LIVECHECK_CACHE['data'][:]
        running = LIVECHECK_CACHE['running']
    updated = datetime.datetime.fromtimestamp(ts).strftime('%H:%M:%S') if ts else None
    age_sec = int(time.time() - ts) if ts else None
    return jsonify({'data': data, 'updated': updated,
                    'age_sec': age_sec, 'running': running, 'total': len(data)})


@app.route('/api/cycles-status')
def api_cycles_status():
    import re as _re
    rpts = sorted(REPORTS_DIR.glob('cycles_report*.html'),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not rpts:
        return jsonify({'cards': [], 'updated': None, 'total': 0})
    try:
        html = rpts[0].read_text(encoding='utf-8', errors='replace')
        cards, pat = [], _re.compile(
            r'<div class="card (long|short)-card"[^>]*'
            r'data-horizon="([^"]+)"[^>]*data-tl="([^"]+)"'
        )
        for m in pat.finditer(html):
            direction = 'LONG' if m.group(1) == 'long' else 'SHORT'
            horizon, tl = m.group(2), m.group(3)
            chunk  = html[m.end():m.end() + 600]
            tk_m   = _re.search(r'<span class="ticker">([A-Z0-9.]+)</span>', chunk)
            pb_m   = _re.search(r'(\d{2,3})%', chunk)
            if tk_m:
                cards.append({'ticker': tk_m.group(1), 'direction': direction,
                              'tl': tl, 'horizon': horizon,
                              'prob': int(pb_m.group(1)) if pb_m else None})
        updated  = datetime.datetime.fromtimestamp(
            rpts[0].stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        filtered = [c for c in cards if c['tl'] in ('GREEN', 'YELLOW')]
        return jsonify({'cards': filtered, 'updated': updated,
                        'report': rpts[0].name, 'total': len(cards)})
    except Exception as e:
        return jsonify({'cards': [], 'updated': None, 'total': 0, 'error': str(e)})


@app.route('/report/<path:filename>')
def serve_report(filename):
    p = (REPORTS_DIR / filename).resolve()
    if p.parent == REPORTS_DIR.resolve() and p.suffix == '.html' and p.exists():
        return p.read_text(encoding='utf-8')
    return 'Not found', 404


@app.route('/')
def index():
    return DASHBOARD_HTML


# ---------------------------------------------------------------------------
# Jenkins-style Pipeline Dashboard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Jenkins-style Pipeline Dashboard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Jenkins-style Pipeline Dashboard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Jenkins-style Pipeline Dashboard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Jenkins-style Pipeline Dashboard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Jenkins-style Pipeline Dashboard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Jenkins-style Pipeline Dashboard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Jenkins-style Pipeline Dashboard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Jenkins-style Pipeline Dashboard
# ---------------------------------------------------------------------------

DASHBOARD_HTML = '\\\n<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>Cycles Trading Pipeline</title>\n<style>\n:root{--bg:#0d1117;--sf:#161b22;--br:#30363d;--tx:#e6edf3;--mu:#adbac7;--dm:#768390;\n      --bl:#58a6ff;--gn:#3fb950;--am:#d29922;--rd:#f85149;--vl:#a371f7;--sk:#79c0ff}\n*{box-sizing:border-box;margin:0;padding:0}\nbody{font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;background:var(--bg);color:var(--tx);min-height:100vh}\n/* Header */\n.hdr{background:var(--bg);border-bottom:1px solid var(--br);padding:10px 20px;\n     display:flex;align-items:center;gap:10px;flex-wrap:wrap}\n.logo{color:var(--bl);font-size:15px;font-weight:700}\n.hdr-t{color:var(--mu);font-size:12px;flex:1}\n.pill{background:var(--sf);border:1px solid var(--br);border-radius:20px;\n      padding:3px 12px;font-size:11px;color:var(--mu)}\n.btn{border:none;border-radius:6px;padding:6px 14px;font-size:12px;font-weight:700;\n     cursor:pointer;color:#fff;transition:opacity .15s;white-space:nowrap}\n.btn:hover{opacity:.82}.btn:disabled{opacity:.3;cursor:not-allowed}\n/* Today bar */\n.tbar{background:var(--sf);border-bottom:1px solid var(--br);padding:5px 20px;\n      display:flex;align-items:center;gap:8px;font-size:11px;color:var(--mu);flex-wrap:wrap}\n.tpill{border-radius:20px;padding:2px 10px;font-size:10px;font-weight:700;border:1px solid;background:transparent}\n/* World clocks (analog) */\n.wc-clk{display:flex;flex-direction:column;align-items:center;line-height:1.15;min-width:50px}\n.wc-clk svg{display:block}\n.wc-face{fill:var(--sf);stroke-width:2.5}\n.wc-hand{stroke:var(--tx);stroke-linecap:round}\n.wc-lbl{font-size:9px;font-weight:700;color:var(--tx);margin-top:1px}\n.wc-dig{font-size:9px;color:var(--mu);font-family:\'JetBrains Mono\',\'Courier New\',monospace}\n.wc-st{font-size:8px;font-weight:700}\n.wc-open{color:#3fb950}.wc-closed{color:#768390}.wc-mid{color:#d29922}\n/* Pipeline */\n.pw{padding:24px 20px 12px;overflow-x:auto}\n.pipe{display:flex;align-items:flex-start;min-width:1100px;position:relative}\n/* Stage */\n.st{display:flex;flex-direction:column;align-items:center;flex-shrink:0;min-width:90px}\n/* Line connector */\n.ln{height:3px;background:var(--br);flex:1;min-width:18px;margin-top:26px;transition:background .4s}\n.ln.lit{background:var(--gn)}\n/* Circle */\n.ci{width:52px;height:52px;border-radius:50%;border:3px solid var(--dm);background:var(--sf);\n    display:flex;align-items:center;justify-content:center;font-size:20px;\n    cursor:pointer;transition:border-color .3s,box-shadow .3s,background .3s;\n    position:relative;z-index:2;user-select:none;flex-shrink:0}\n.ci:hover{border-color:var(--bl);box-shadow:0 0 10px #58a6ff44}\n.ci.nc{cursor:default}.ci.nc:hover{border-color:var(--dm);box-shadow:none}\n/* Dot */\n.dot{position:absolute;bottom:-3px;right:-3px;width:17px;height:17px;border-radius:50%;\n     font-size:10px;display:flex;align-items:center;justify-content:center;\n     background:var(--dm);border:2px solid var(--bg);color:#fff;pointer-events:none;font-weight:700}\n/* Stage label / sub */\n.sl{font-size:11px;font-weight:700;color:var(--tx);margin-top:8px;text-align:center;max-width:88px;line-height:1.3}\n.ss{font-size:9px;color:var(--mu);text-align:center;margin-top:3px;max-width:90px;line-height:1.4}\n.sch-lbl{font-size:8px;color:var(--am);text-align:center;margin-top:2px;max-width:90px;font-weight:700}\n/* Run btn */\n.rb{margin-top:6px;font-size:9px;padding:2px 10px;border-radius:10px;background:transparent;\n    border:1px solid var(--br);color:var(--mu);cursor:pointer;transition:all .15s;white-space:nowrap}\n.rb:hover{border-color:var(--bl);color:var(--bl)}\n.rb:disabled{opacity:.3;cursor:not-allowed;pointer-events:none}\n/* Report cards */\n.rc{background:var(--sf);border:1px solid var(--br);border-radius:8px;padding:12px 16px;\n    display:flex;flex-direction:column;gap:8px;min-width:180px;flex:1;max-width:220px}\n.rc-hd{display:flex;align-items:center;gap:8px}\n.rc-ico{font-size:18px}\n.rc-nm{font-size:12px;font-weight:700;color:var(--tx)}\n.rc-dt{font-size:10px;color:var(--mu);margin-top:-4px}\n.rc-open{display:block;text-align:center;margin-top:4px;padding:5px 0;border-radius:6px;\n         border:1px solid var(--bl);color:var(--bl);background:transparent;\n         text-decoration:none;font-size:11px;font-weight:700;cursor:pointer}\n.rc-open:hover{background:var(--bl);color:#000}\n/* States */\n.s-idle .ci{border-color:var(--dm)}\n.s-queued .ci{border-color:var(--am);opacity:.7}\n.s-running .ci{border-color:var(--bl)!important;animation:glow 1.1s ease-in-out infinite}\n.s-done .ci{border-color:var(--gn)!important;background:#0d2318}\n.s-error .ci{border-color:var(--rd)!important;background:#2a0d0d}\n.s-running .sl{color:var(--bl)}.s-done .sl{color:var(--gn)}\n.s-error .sl{color:var(--rd)}.s-queued .sl{color:var(--am)}\n.s-running .dot{background:var(--bl)}.s-done .dot{background:var(--gn)}\n.s-error .dot{background:var(--rd)}.s-queued .dot{background:var(--am)}\n.today .ci{box-shadow:0 0 0 3px #58a6ff22}\n.opt .ci{opacity:.5}.opt .sl{opacity:.5}.opt .ss{opacity:.5}.opt .sch-lbl{opacity:.5}\n@keyframes glow{0%,100%{box-shadow:0 0 4px 1px #58a6ff33}50%{box-shadow:0 0 14px 4px #58a6ff66}}\n@keyframes spin{0%{transform:rotate(0)}100%{transform:rotate(360deg)}}\n.spin{display:inline-block;animation:spin .7s linear infinite}\n/* Schedule bar */\n.sbar{padding:7px 20px;background:var(--sf);border-bottom:1px solid var(--br);\n      display:flex;gap:0;flex-wrap:wrap;align-items:center;font-size:10px}\n.sbar-title{color:var(--tx);font-weight:700;margin-right:16px;white-space:nowrap}\n.sbar-item{display:flex;align-items:center;gap:6px;padding:2px 16px;border-left:1px solid var(--br)}\n.sbar-time{color:var(--am);font-weight:700;white-space:nowrap}\n.sbar-tasks{color:var(--mu)}\n/* Hot stocks */\n.hot-sec{padding:12px 20px 4px}\n.hot-hdr{display:flex;align-items:center;gap:10px;margin-bottom:10px}\n.hot-title{font-size:13px;font-weight:700;color:var(--tx)}\n.hot-upd{font-size:10px;color:var(--dm)}\n.hot-cnt{font-size:10px;color:var(--dm);margin-left:auto}\n.hot-grid{display:flex;flex-wrap:wrap;gap:8px;min-height:44px}\n.hcard{border-radius:8px;padding:9px 13px;min-width:120px;border:1px solid;\n       transition:transform .15s,box-shadow .15s}\n.hcard:hover{transform:translateY(-2px);box-shadow:0 4px 12px #0004}\n.hcard.green{background:#071d0f;border-color:#2ea043}\n.hcard.yellow{background:#1a1600;border-color:#9e6a03}\n.hcard.dim{background:var(--sf);border-color:var(--br);opacity:.7}\n.hcard-top{display:flex;align-items:baseline;gap:5px}\n.hcard-tk{font-size:14px;font-weight:800;color:var(--tx)}\n.hcard-dir{font-size:9px;font-weight:700}\n.long{color:#3fb950}.short{color:#f85149}\n.hcard-st{font-size:9px;font-weight:700;margin-top:3px}\n.hcard-st.green{color:#3fb950}.hcard-st.yellow{color:#d29922}.hcard-st.dim{color:var(--dm)}\n.hcard-meta{font-size:9px;color:var(--dm);margin-top:2px;line-height:1.4}\n/* Live Watch / Cycles Status panels */\n.lw-sec{padding:8px 20px 4px;display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}\n.lw-panel{flex:1;min-width:280px;max-width:560px}\n.lw-phdr{display:flex;align-items:center;gap:8px;margin-bottom:8px}\n.lw-ptitle{font-size:12px;font-weight:700;color:var(--tx)}\n.lw-pupd{font-size:10px;color:var(--dm)}\n.lw-pcnt{font-size:10px;color:var(--dm);margin-left:auto}\n.lwf{display:flex;gap:4px;margin:0 0 6px}\n.lwf-btn{border:1px solid var(--br);background:transparent;color:var(--mu);border-radius:12px;padding:2px 10px;font-size:9px;font-weight:700;cursor:pointer;transition:all .12s}\n.lwf-btn:hover{border-color:var(--bl);color:var(--bl)}\n.lwf-btn.on{background:rgba(255,255,255,.14);border-color:var(--tx);color:var(--tx)}\n.lw-grid{display:flex;flex-wrap:wrap;gap:6px;min-height:36px}\n.lwc{border-radius:7px;padding:7px 11px;min-width:110px;border:1px solid;transition:transform .15s}\n.lwc:hover{transform:translateY(-2px)}\n.lwc.wait{background:#1a1600;border-color:#9e6a03}\n.lwc.inzone{background:#071d0f;border-color:#2ea043}\n.lwc.notyet{background:#1a0d0d;border-color:#6e2020}\n.lwc-top{display:flex;align-items:center;gap:4px;flex-wrap:wrap}\n.lwc-tk{font-size:13px;font-weight:800;color:var(--tx)}\n.lwc-tf{font-size:8px;font-weight:700;padding:1px 5px;border-radius:4px;background:#ffffff1a;color:var(--mu)}\n.lwc-st{font-size:9px;font-weight:700;margin-top:2px}\n.lwc-st.inzone{color:#3fb950}.lwc-st.wait{color:#d29922}.lwc-st.notyet{color:#f85149}\n.lwc-price{font-size:10px;color:var(--tx);margin-top:3px;font-weight:600}\n.lwc-dist{font-size:9px;color:var(--dm);margin-top:1px}\n.cyc{border-radius:7px;padding:7px 11px;min-width:110px;border:1px solid;transition:transform .15s}\n.cyc:hover{transform:translateY(-2px)}\n.cyc.go{background:#071d0f;border-color:#2ea043}\n.cyc.wait{background:#1a1600;border-color:#9e6a03}\n.cyc-top{display:flex;align-items:center;gap:4px;flex-wrap:wrap}\n.cyc-tk{font-size:13px;font-weight:800;color:var(--tx)}\n.cyc-tf{font-size:8px;font-weight:700;padding:1px 5px;border-radius:4px;background:#ffffff1a;color:var(--mu)}\n.cyc-st{font-size:9px;font-weight:700;margin-top:2px}\n.cyc-st.go{color:#3fb950}.cyc-st.wait{color:#d29922}\n/* Terminal */\n.term{margin:0 20px 20px;background:#0d1117;border:1px solid var(--br);border-radius:8px;overflow:hidden}\n.tbar2{background:var(--sf);border-bottom:1px solid var(--br);padding:7px 14px;\n       display:flex;align-items:center;gap:8px}\n.d{width:10px;height:10px;border-radius:50%}\n.dr{background:#f85149}.dy{background:#d29922}.dg{background:#3fb950}\n.ttl{font-size:12px;color:var(--mu);flex:1}\n.jtag{font-size:10px;padding:2px 10px;border-radius:10px;background:var(--sf);color:var(--mu)}\n.jtag.running{color:var(--bl);animation:blink .9s infinite}\n.jtag.done{color:var(--gn)}.jtag.error{color:var(--rd)}\n@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}\n.clr{background:transparent;border:1px solid var(--br);color:var(--mu);border-radius:5px;padding:3px 10px;font-size:11px;cursor:pointer}\n.clr:hover{border-color:var(--mu);color:var(--tx)}\npre#log{font-family:\'JetBrains Mono\',\'Courier New\',monospace;font-size:11px;line-height:1.6;\n        color:#c9d1d9;padding:14px 16px;min-height:120px;max-height:260px;overflow-y:auto;\n        white-space:pre-wrap;word-break:break-all}\n/* Run History */\n.hist-sec{margin:0 20px 16px;border:1px solid var(--br);border-radius:8px;overflow:hidden}\n.hist-hdr2{background:var(--sf);border-bottom:1px solid var(--br);padding:7px 14px;\n           display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none}\n.hist-hdr2:hover{background:#1c2128}\n.hist-ttl{font-size:12px;color:var(--mu);flex:1}\n.hist-chev{font-size:10px;color:var(--dm);transition:transform .2s}\n.hist-chev.open{transform:rotate(90deg)}\n.hist-body2{overflow:hidden}\n.hist-col-hdr{display:grid;grid-template-columns:130px 1fr 90px 75px 65px;\n              padding:5px 14px;font-size:9px;color:var(--dm);border-bottom:1px solid #21262d;gap:8px}\n.hist-row2{display:grid;grid-template-columns:130px 1fr 90px 75px 65px;\n           padding:6px 14px;font-size:10px;border-bottom:1px solid #21262d;gap:8px;align-items:center}\n.hist-row2:last-child{border-bottom:none}\n.hist-row2:hover{background:#21262d}\n.hbadge{border-radius:10px;padding:1px 8px;font-size:9px;font-weight:700}\n.hbadge.manual{background:#1b3557;color:#58a6ff}\n.hbadge.scheduler{background:#0f2d1f;color:#3fb950}\n.hok{color:#3fb950}.herr{color:#f85149}.hstop{color:#d29922}\n</style>\n</head>\n<body>\n\n<div class="hdr">\n  <div class="logo">&#9889; Cycles Trading Pipeline</div>\n  <div class="hdr-t" id="hdr-t">Loading...</div>\n  <span id="wclk" style="display:flex;gap:12px;align-items:center"></span>\n  <span class="pill" id="hdr-day">&#x2014;</span>\n  <button class="btn" id="btn-today" onclick="runTask(\'pipeline\')" style="background:#1f6feb">&#9654; Run Today</button>\n  <button class="btn" id="btn-all"   onclick="runTask(\'all\')"      style="background:#b91c1c">&#128293; Run All</button>\n  <button class="btn" id="btn-stop"  onclick="stopAll()"            style="background:#161b22;border:1px solid #6e0f0f;color:#f85149">&#9632; Stop</button>\n</div>\n\n<div class="tbar">\n  <b>Today&#8217;s plan:</b><span id="plan-pills">Loading...</span>\n</div>\n\n<div class="pw">\n<div class="pipe">\n\n  <!-- 1. Scheduler -->\n  <div class="st s-done" id="node-scheduler">\n    <div class="ci nc">&#9889;<div class="dot">&#10003;</div></div>\n    <div class="sl">Scheduler</div>\n    <div class="ss" id="sch-next">&#8212;</div>\n    <div class="ss" id="sch-tasks" style="color:var(--mu);line-height:1.6"></div>\n  </div>\n  <div class="ln lit" id="ln-1"></div>\n\n  <!-- 2. Cycles Report -->\n  <div class="st s-idle" id="node-weekly">\n    <div class="ci" onclick="runTask(\'weekly\')">&#128202;<div class="dot">&#8212;</div></div>\n    <div class="sl">Cycles Report</div>\n    <div class="ss">cycles_trading_scanner.py<br>&#8594; cycles_report.html</div>\n    <div class="sch-lbl">&#9201; Mon-Fri 23:15 &#183; Sun 08:00</div>\n    <button class="rb" id="btn-weekly" onclick="runTask(\'weekly\')">&#9654; Run</button>\n  </div>\n  <div class="ln" id="ln-2"></div>\n\n  <!-- 3. Momentum Scan -->\n  <div class="st s-idle" id="node-momentum">\n    <div class="ci" onclick="runTask(\'momentum\')">&#128640;<div class="dot">&#8212;</div></div>\n    <div class="sl">Momentum Scan</div>\n    <div class="ss">cycles_trading_scanner.py momentum<br>&#8594; momentum_report.html</div>\n    <div class="sch-lbl">&#9201; Mon-Fri 23:15 &#183; Sun 08:00</div>\n    <button class="rb" id="btn-momentum" onclick="runTask(\'momentum\')">&#9654; Run</button>\n  </div>\n  <div class="ln" id="ln-3"></div>\n\n  <!-- 4. Weekly Review -->\n  <div class="st s-idle" id="node-review">\n    <div class="ci" onclick="runTask(\'review\')">&#128203;<div class="dot">&#8212;</div></div>\n    <div class="sl">Weekly Review</div>\n    <div class="ss">ct_weekly_review.py<br>&#8594; weekly_review.html</div>\n    <div class="sch-lbl">&#128197; Every Sunday 08:00</div>\n    <button class="rb" id="btn-review" onclick="runTask(\'review\')">&#9654; Run</button>\n  </div>\n  <div class="ln" id="ln-4"></div>\n\n  <!-- 5. Monthly S/R (optional) -->\n  <div class="st s-idle opt" id="node-monthly">\n    <div class="ci" onclick="runTask(\'monthly\')">&#128197;<div class="dot">&#8212;</div></div>\n    <div class="sl">Monthly S/R</div>\n    <div class="ss" id="monthly-ss">cycles_trading_scanner.py monthly</div>\n    <div class="sch-lbl">&#128197; 1st Sunday 08:00</div>\n    <button class="rb" id="btn-monthly" onclick="runTask(\'monthly\')">&#9654; Run</button>\n  </div>\n  <div class="ln" id="ln-5"></div>\n\n  <!-- 6. Watch Store (read-only) -->\n  <div class="st s-done" id="node-store">\n    <div class="ci nc">&#128190;<div class="dot">&#10003;</div></div>\n    <div class="sl">Watch Store</div>\n    <div class="ss">watch_alerts.json<br>active watchlist</div>\n    <div class="ss" id="store-cnt" style="margin-top:6px;font-size:11px;color:var(--tx);font-weight:700">&#8212; tickers</div>\n  </div>\n  <div class="ln lit" id="ln-6"></div>\n\n  <!-- 7. Watch Checker -->\n  <div class="st s-idle" id="node-daily">\n    <div class="ci" onclick="runTask(\'daily\')">&#128276;<div class="dot">&#8212;</div></div>\n    <div class="sl">Watch Checker</div>\n    <div class="ss">ct_watch_checker.py</div>\n    <div class="sch-lbl">&#9201; Hourly 16:30-23:30 (US session)</div>\n    <button class="rb" id="btn-daily" onclick="runTask(\'daily\')">&#9654; Run</button>\n  </div>\n  <div class="ln" id="ln-7"></div>\n\n  <!-- 8. Email Output -->\n  <div class="st" id="node-output">\n    <div class="ci nc">&#128231;<div class="dot" style="background:var(--dm)">&#8212;</div></div>\n    <div class="sl">Email Output</div>\n    <div class="ss">Alert on GREEN hit<br>&#8594; omarearly@gmail.com</div>\n  </div>\n\n</div>\n</div>\n\n<!-- Schedule overview bar -->\n<div class="sbar">\n  <span class="sbar-title">&#128197; SCHEDULE</span>\n  <span class="sbar-item">\n    <span class="sbar-time">Hourly 16:30-23:30</span>\n    <span class="sbar-tasks">Mon-Fri (US session) &#8594; &#128276; Watch Checker</span>\n  </span>\n  <span class="sbar-item">\n    <span class="sbar-time">Mon-Fri 23:15</span>\n    <span class="sbar-tasks">after US close &#8594; &#128202; Cycles &#183; &#128640; Momentum &#183; &#128276; Checker</span>\n  </span>\n  <span class="sbar-item">\n    <span class="sbar-time">Every Sunday</span>\n    <span class="sbar-tasks">08:00 &#8594; &#128203; Weekly Review</span>\n  </span>\n  <span class="sbar-item">\n    <span class="sbar-time">1st Sunday</span>\n    <span class="sbar-tasks">08:00 &#8594; + &#128197; Monthly S/R</span>\n  </span>\n</div>\n\n<!-- Live Watch + Cycles Status -->\n<div class="lw-sec">\n  <div class="lw-panel">\n    <div class="lw-phdr">\n      <span class="lw-ptitle">&#128308; Live Watch</span>\n      <span class="lw-pupd" id="lw-upd"></span>\n      <span class="lw-pcnt" id="lw-cnt"></span>\n    </div>\n    <div class="lwf" id="lw-filt">\n      <button class="lwf-btn on" onclick="setLwFilt(this,\'ALL\')">All</button>\n      <button class="lwf-btn" onclick="setLwFilt(this,\'IN_ZONE\')">&#128994; At Entry</button>\n      <button class="lwf-btn" onclick="setLwFilt(this,\'WAIT\')">&#9889; Near</button>\n    </div>\n    <div class="lw-grid" id="lw-grid">\n      <div style="color:var(--dm);font-size:10px;padding:4px">Loading...</div>\n    </div>\n  </div>\n  <div class="lw-panel">\n    <div class="lw-phdr">\n      <span class="lw-ptitle">&#128202; Cycles Status</span>\n      <span class="lw-pupd" id="cy-upd"></span>\n      <span class="lw-pcnt" id="cy-cnt"></span>\n    </div>\n    <div class="lwf" id="cy-filt">\n      <button class="lwf-btn on" onclick="setCyFilt(this,\'ALL\')">All</button>\n      <button class="lwf-btn" onclick="setCyFilt(this,\'GREEN\')">&#128994; GO</button>\n      <button class="lwf-btn" onclick="setCyFilt(this,\'YELLOW\')">&#9889; Wait</button>\n    </div>\n    <div class="lw-grid" id="cy-grid">\n      <div style="color:var(--dm);font-size:10px;padding:4px">Loading...</div>\n    </div>\n  </div>\n</div>\n\n<!-- Reports row -->\n<div id="rep-sec" style="padding:4px 20px 16px;display:flex;gap:12px;flex-wrap:wrap"></div>\n\n<!-- Run History -->\n<div class="hist-sec">\n  <div class="hist-hdr2" onclick="toggleHist()">\n    <span style="font-size:13px">&#128196;</span>\n    <span class="hist-ttl">Run History</span>\n    <span class="hist-chev open" id="hist-chev">&#9655;</span>\n  </div>\n  <div class="hist-body2" id="hist-body2">\n    <div class="hist-col-hdr">\n      <span>Started</span><span>Task</span><span>Triggered by</span><span>Status</span><span>Duration</span>\n    </div>\n    <div id="hist-rows">\n      <div style="color:var(--dm);font-size:10px;padding:10px 14px">No runs recorded yet. Run any task to see history here.</div>\n    </div>\n  </div>\n</div>\n\n<div class="term">\n  <div class="tbar2">\n    <div class="d dr"></div><div class="d dy"></div><div class="d dg"></div>\n    <span class="ttl">Log Output</span>\n    <span class="jtag" id="jtag">IDLE</span>\n    <button class="clr" onclick="clearLog()">Clear</button>\n  </div>\n  <pre id="log">$ Cycles Trading Pipeline Dashboard ready.\n$ Click any stage to run it, or use Run Today / Run All.</pre>\n</div>\n\n<script>\nconst SCAN_KEYS = [\'weekly\',\'momentum\',\'review\',\'monthly\'];\nconst ALL_KEYS  = [\'weekly\',\'momentum\',\'review\',\'monthly\',\'daily\'];\nconst LINE_AFTER = {weekly:\'ln-2\',momentum:\'ln-3\',review:\'ln-4\',monthly:\'ln-5\',daily:\'ln-7\'};\nconst DOT_ICONS  = {running:\'<span class="spin">&#8635;</span>\',done:\'&#10003;\',error:\'&#10007;\',queued:\'&#8230;\',idle:\'&#8212;\'};\nconst TASK_ICONS = {weekly:\'&#128202;\',momentum:\'&#128640;\',review:\'&#128203;\',monthly:\'&#128197;\',daily:\'&#128276;\',pipeline:\'&#9889;\',all:\'&#128293;\'};\n\nlet currentJob=null, activeTask=null, pollTimer=null, todayTasks=[], nodeStates={};\nlet histOpen=true;\nALL_KEYS.forEach(k=>nodeStates[k]=\'idle\');\n\nfunction setNodeState(key,state,badge){\n  const node=document.getElementById(\'node-\'+key);\n  if(!node)return;\n  node.className=node.className.replace(/\\bs-\\w+/g,\'\').replace(/\\btoday\\b/g,\'\').trim()\n    +\' s-\'+state+(todayTasks.includes(key)?\' today\':\'\');\n  const dot=node.querySelector(\'.dot\');\n  if(dot)dot.innerHTML=DOT_ICONS[state]||\'&#8212;\';\n  const btn=document.getElementById(\'btn-\'+key);\n  if(btn)btn.disabled=(state===\'running\');\n  if(state===\'done\'){\n    const lnId=LINE_AFTER[key];\n    if(lnId){const l=document.getElementById(lnId);if(l)l.classList.add(\'lit\');}\n  }\n  nodeStates[key]=state;\n}\n\nfunction resetAll(){\n  ALL_KEYS.forEach(k=>setNodeState(k,\'idle\'));\n  document.querySelectorAll(\'.rb\').forEach(b=>b.disabled=false);\n  [\'btn-today\',\'btn-all\'].forEach(id=>{const b=document.getElementById(id);if(b)b.disabled=false;});\n  [\'ln-2\',\'ln-3\',\'ln-4\',\'ln-5\',\'ln-7\'].forEach(id=>{const l=document.getElementById(id);if(l)l.classList.remove(\'lit\');});\n}\n\nfunction runTask(task){\n  if(currentJob&&!confirm(\'A task is running. Start anyway?\'))return;\n  if(currentJob)stopPoll();\n  activeTask=task;\n  lockBtns();\n  if(ALL_KEYS.includes(task))setNodeState(task,\'running\');\n  else if(task===\'all\'||task===\'pipeline\')ALL_KEYS.forEach(k=>setNodeState(k,\'queued\'));\n  setJTag(\'Starting…\',\'running\');\n  setLog([\'&#9654; Starting: \'+task]);\n  fetch(\'/api/run/\'+task,{method:\'POST\'}).then(r=>r.json()).then(d=>{\n    if(d.error){setLog([\'Error: \'+d.error]);setJTag(\'ERROR\',\'error\');resetAll();return;}\n    currentJob=d.job_id;startPoll();\n  }).catch(e=>{setLog([\'Error: \'+e]);setJTag(\'ERROR\',\'error\');resetAll();});\n}\n\nfunction parsePipelineLog(lines){\n  const MAP={\'Weekly Retest Scan\':\'weekly\',\'Momentum Scan\':\'momentum\',\n             \'Weekly Review\':\'review\',\'Monthly S/R Scan\':\'monthly\',\'Daily Watch Checker\':\'daily\'};\n  let cur=null;\n  lines.forEach(l=>{\n    const m=l.match(/>>>\\s+(.+)/);\n    if(m){const k=MAP[m[1].trim()];if(k){if(cur&&cur!==k)setNodeState(cur,\'done\');cur=k;setNodeState(k,\'running\');}}\n    if((l.includes(\'OK in\')||l.includes(\'Exit code: 0\'))&&cur){setNodeState(cur,\'done\');cur=null;}\n    if((l.includes(\'FAILED\')||l.includes(\'TIMEOUT\')||l.includes(\'EXCEPTION\'))&&cur){setNodeState(cur,\'error\');cur=null;}\n  });\n}\n\nfunction startPoll(){\n  stopPoll();\n  pollTimer=setInterval(()=>{\n    fetch(\'/api/job/\'+currentJob).then(r=>r.json()).then(d=>{\n      setLog(d.log);\n      const e=d.elapsed<60?d.elapsed+\'s\':Math.floor(d.elapsed/60)+\'m\'+(d.elapsed%60)+\'s\';\n      if(activeTask===\'pipeline\'||activeTask===\'all\')parsePipelineLog(d.log);\n      if(d.status===\'running\'||d.status===\'starting\'){\n        setJTag(\'Running \'+e,\'running\');\n        if(activeTask!==\'pipeline\'&&activeTask!==\'all\'&&ALL_KEYS.includes(activeTask))\n          setNodeState(activeTask,\'running\');\n      }else{\n        stopPoll();currentJob=null;\n        const ok=d.status===\'done\';\n        if(activeTask!==\'pipeline\'&&activeTask!==\'all\'){setNodeState(activeTask,ok?\'done\':\'error\');}\n        else{ALL_KEYS.forEach(k=>{if(nodeStates[k]!==\'done\')setNodeState(k,ok?\'done\':\'error\');});}\n        setJTag(ok?\'Done \'+e:\'Error\',ok?\'done\':\'error\');\n        unlockBtns();loadReports();loadWatchlist();loadHistory();loadLiveCheck();loadCyclesStatus();activeTask=null;\n      }\n    }).catch(()=>{});\n  },600);\n}\n\nfunction stopPoll(){if(pollTimer){clearInterval(pollTimer);pollTimer=null;}}\n\nfunction stopAll(){\n  fetch(\'/api/stop/all\',{method:\'POST\'}).then(r=>r.json()).then(()=>{\n    stopPoll();setJTag(\'Stopped\',\'error\');resetAll();currentJob=null;activeTask=null;\n  }).catch(()=>{});\n}\n\nfunction lockBtns(){\n  [\'btn-today\',\'btn-all\'].forEach(id=>{const b=document.getElementById(id);if(b)b.disabled=true;});\n  document.querySelectorAll(\'.rb\').forEach(b=>b.disabled=true);\n}\nfunction unlockBtns(){\n  [\'btn-today\',\'btn-all\'].forEach(id=>{const b=document.getElementById(id);if(b)b.disabled=false;});\n  document.querySelectorAll(\'.rb\').forEach(b=>b.disabled=false);\n}\nfunction setLog(lines){const el=document.getElementById(\'log\');\n  el.innerHTML=(Array.isArray(lines)?lines:[\'\'+lines]).join(\'\\n\');el.scrollTop=el.scrollHeight;}\nfunction clearLog(){document.getElementById(\'log\').textContent=\'$ Log cleared.\';setJTag(\'IDLE\',\'\');resetAll();}\nfunction setJTag(msg,cls){const el=document.getElementById(\'jtag\');el.textContent=msg;el.className=\'jtag \'+(cls||\'\');}\n\n/* ── Today / clock ─────────────────────────────────────────────────────── */\nconst WCLK=[{nm:\'New York\',tz:\'America/New_York\',type:\'ny\'},\n            {nm:\'London\',tz:\'Europe/London\',type:\'ldn\'},\n            {nm:\'Tokyo\',tz:\'Asia/Tokyo\',type:\'tyo\'}];\nfunction _mktState(type,wd,mins){\n  if(wd===\'Sat\'||wd===\'Sun\')return[\'CLOSED\',\'wc-closed\'];\n  if(type===\'ny\'){\n    if(mins>=570&&mins<960)return[\'OPEN\',\'wc-open\'];\n    if(mins>=240&&mins<570)return[\'PRE\',\'wc-mid\'];\n    if(mins>=960&&mins<1200)return[\'AFTER\',\'wc-mid\'];\n  }else if(type===\'ldn\'){\n    if(mins>=480&&mins<990)return[\'OPEN\',\'wc-open\'];\n  }else{\n    if((mins>=540&&mins<690)||(mins>=750&&mins<900))return[\'OPEN\',\'wc-open\'];\n    if(mins>=690&&mins<750)return[\'LUNCH\',\'wc-mid\'];\n  }\n  return[\'CLOSED\',\'wc-closed\'];\n}\nfunction updateWorldClocks(){\n  const el=document.getElementById(\'wclk\');\n  if(!el)return;\n  el.innerHTML=WCLK.map(m=>{\n    const parts=new Intl.DateTimeFormat(\'en-GB\',{timeZone:m.tz,weekday:\'short\',hour:\'2-digit\',minute:\'2-digit\',hour12:false}).formatToParts(new Date());\n    const o={};parts.forEach(x=>{o[x.type]=x.value;});\n    const h=parseInt(o.hour,10),mi=parseInt(o.minute,10);\n    const st=_mktState(m.type,o.weekday,h*60+mi);\n    const ha=(h%12)*30+mi*0.5, ma=mi*6;\n    return \'<span class="wc-clk \'+st[1]+\'" title="\'+m.nm+\' \\u2014 \'+st[0]+\'">\'\n      +\'<svg viewBox="0 0 40 40" width="36" height="36">\'\n      +\'<circle class="wc-face" cx="20" cy="20" r="17.5" stroke="currentColor"/>\'\n      +\'<line x1="20" y1="5.5" x2="20" y2="9" stroke="#768390" stroke-width="1.5"/>\'\n      +\'<line x1="20" y1="31" x2="20" y2="34.5" stroke="#768390" stroke-width="1.5"/>\'\n      +\'<line x1="5.5" y1="20" x2="9" y2="20" stroke="#768390" stroke-width="1.5"/>\'\n      +\'<line x1="31" y1="20" x2="34.5" y2="20" stroke="#768390" stroke-width="1.5"/>\'\n      +\'<line class="wc-hand" x1="20" y1="20" x2="20" y2="12.5" stroke-width="2.4" transform="rotate(\'+ha+\' 20 20)"/>\'\n      +\'<line class="wc-hand" x1="20" y1="20" x2="20" y2="8.5" stroke-width="1.5" transform="rotate(\'+ma+\' 20 20)"/>\'\n      +\'<circle cx="20" cy="20" r="1.6" fill="#e6edf3"/>\'\n      +\'</svg>\'\n      +\'<span class="wc-lbl">\'+m.nm+\'</span>\'\n      +\'<span class="wc-dig">\'+o.hour+\':\'+o.minute+\'</span>\'\n      +\'<span class="wc-st">\'+st[0]+\'</span>\'\n      +\'</span>\';\n  }).join(\'\');\n}\nfunction loadToday(){\n  fetch(\'/api/today\').then(r=>r.json()).then(d=>{\n    document.getElementById(\'hdr-t\').textContent=d.date+\'  \'+d.time;\n    document.getElementById(\'hdr-day\').textContent=d.day;\n    todayTasks=d.planned;\n    const C={weekly:\'#3fb950\',momentum:\'#d29922\',review:\'#a371f7\',monthly:\'#79c0ff\',daily:\'#f85149\'};\n    const L={weekly:\'Cycles\',momentum:\'Momentum\',review:\'Review\',monthly:\'Monthly\',daily:\'Checker\'};\n    document.getElementById(\'plan-pills\').innerHTML=\' \'+d.planned.filter(k=>k!==\'pipeline\')\n      .map(k=>\'<span class="tpill" style="color:\'+C[k]+\';border-color:\'+C[k]+\'55">\'+L[k]+\'</span>\').join(\' \');\n    d.planned.forEach(k=>{\n      const n=document.getElementById(\'node-\'+k);\n      if(n&&!n.className.includes(\'today\'))n.className+=\' today\';\n    });\n    const isFirstSun=(d.day===\'Sun\'&&parseInt(d.date)<=7);\n    const mn=document.getElementById(\'node-monthly\');\n    if(mn){if(isFirstSun)mn.classList.remove(\'opt\');else mn.classList.add(\'opt\');}\n  }).catch(()=>{});\n}\n\n/* ── Scheduler node ────────────────────────────────────────────────────── */\nfunction loadSchedule(){\n  fetch(\'/api/schedule\').then(r=>r.json()).then(d=>{\n    const nx=document.getElementById(\'sch-next\');\n    if(nx)nx.textContent=\'Next: \'+d.next_label+\' (in \'+d.mins_until+\'m)\';\n    const tk=document.getElementById(\'sch-tasks\');\n    if(tk)tk.textContent=d.next_task_labels.join(\' · \');\n  }).catch(()=>{});\n}\n\n/* ── Reports ───────────────────────────────────────────────────────────── */\nfunction loadReports(){\n  fetch(\'/api/reports\').then(r=>r.json()).then(list=>{\n    const sec=document.getElementById(\'rep-sec\');\n    if(!list.length){sec.innerHTML=\'<div style="color:var(--dm);font-size:11px;padding:8px">No reports yet.</div>\';return;}\n    const CFG={\n      cycles_report:   {ico:\'&#128202;\',nm:\'Cycles Report\',  c:\'#3fb950\'},\n      momentum_report: {ico:\'&#128640;\',nm:\'Momentum Scan\',  c:\'#d29922\'},\n      weekly_review:   {ico:\'&#128203;\',nm:\'Weekly Review\',  c:\'#a371f7\'},\n      monthly_scan:    {ico:\'&#128197;\',nm:\'Monthly S/R\',    c:\'#79c0ff\'},\n      watch_report:    {ico:\'&#128276;\',nm:\'Watch Checker\',  c:\'#f85149\'},\n      calibration_report:{ico:\'&#128200;\',nm:\'Calibration\',   c:\'#79c0ff\'},\n      winner_autopsy:  {ico:\'&#128202;\',nm:\'Winner Autopsy\', c:\'#a371f7\'}\n    };\n    sec.innerHTML=list.slice(0,6).map(r=>{\n      const key=Object.keys(CFG).find(k=>r.name.startsWith(k))||\'\';\n      const cfg=CFG[key]||{ico:\'&#128196;\',nm:r.name,c:\'#8b949e\'};\n      const d=new Date(r.mtime*1000);\n      const dt=d.toLocaleDateString()+\' \'+d.toLocaleTimeString([],{hour:\'2-digit\',minute:\'2-digit\'});\n      return \'<div class="rc"><div class="rc-hd"><span class="rc-ico">\'+cfg.ico+\'</span>\'\n            +\'<div><div class="rc-nm" style="color:\'+cfg.c+\'">\'+cfg.nm+\'</div>\'\n            +\'<div class="rc-dt">\'+dt+\'</div></div></div>\'\n            +\'<a class="rc-open" href="/report/\'+r.name+\'" target="_blank">Open Report</a></div>\';\n    }).join(\'\');\n  }).catch(()=>{});\n}\n\n/* ── Watchlist count ───────────────────────────────────────────────────── */\nfunction loadWatchlist(){\n  fetch(\'/api/watchlist\').then(r=>r.json()).then(data=>{\n    const list=data.tickers||[];\n    const el=document.getElementById(\'store-cnt\');\n    if(el)el.textContent=(list.length||\'0\')+\' tickers\';\n  }).catch(()=>{});\n}\n\n/* ── Live Watch (IN_ZONE green + WAIT yellow + live price) ─────────────── */\nfunction loadLiveCheck(){\n  fetch(\'/api/livecheck\').then(r=>r.json()).then(data=>{\n    const upd=document.getElementById(\'lw-upd\');\n    const cnt=document.getElementById(\'lw-cnt\');\n    const spin=data.running?\'<span class="spin">&#8635;</span> \':\'\';\n    if(upd)upd.innerHTML=spin+(data.updated?\'updated: \'+data.updated:\'waiting...\');\n    const items=data.data||[];\n    LW_ITEMS=items.filter(t=>t.zone===\'IN_ZONE\'||t.zone===\'WAIT\')\n      .sort((a,b)=>(a.zone===\'IN_ZONE\'?0:1)-(b.zone===\'IN_ZONE\'?0:1));\n    const inZ=items.filter(t=>t.zone===\'IN_ZONE\').length;\n    const wZ=items.filter(t=>t.zone===\'WAIT\').length;\n    if(cnt)cnt.textContent=(inZ?inZ+\' at entry\':\'\')+(inZ&&wZ?\' \\u00b7 \':\'\')+(wZ?wZ+\' near\':\'\')+\' / \'+(data.total||0)+\' watching\';\n    LW_TOTAL=data.total||0;\n    paintLive();\n  }).catch(()=>{});\n}\nlet LW_ITEMS=[], LW_FILT=\'ALL\', LW_TOTAL=0;\nfunction setLwFilt(btn,f){\n  LW_FILT=f;\n  document.querySelectorAll(\'#lw-filt .lwf-btn\').forEach(b=>b.classList.remove(\'on\'));\n  if(btn)btn.classList.add(\'on\');\n  paintLive();\n}\nfunction paintLive(){\n  const grid=document.getElementById(\'lw-grid\');\n  if(!grid)return;\n  let items=LW_ITEMS;\n  if(LW_FILT!==\'ALL\')items=items.filter(t=>t.zone===LW_FILT);\n  if(!items.length){\n    grid.innerHTML=\'<div style="color:var(--dm);font-size:10px;padding:4px">\'\n      +(LW_ITEMS.length?\'No tickers match this filter.\':(LW_TOTAL?\'No tickers near entry zone.\':\'No watchlist data. Run Watch Checker first.\'))\n      +\'</div>\';\n    return;\n  }\n  grid.innerHTML=items.map(t=>{\n    const isInZone=t.zone===\'IN_ZONE\';\n    const cls=isInZone?\'inzone\':\'wait\';\n    const stLabel=isInZone?\'&#128994; AT ENTRY\':\'&#9889; NEAR ZONE\';\n    const isLong=(t.direction||\'LONG\')===\'LONG\';\n    const dirCls=isLong?\'long\':\'short\';\n    const dirLbl=isLong?\'&#9650; L\':\'&#9660; S\';\n    const tf=((t.timeframe||\'WEEKLY\').charAt(0));\n    const cu=t.cur||\'$\';const price=t.cur_price?(+t.cur_price>=100?cu+(+t.cur_price).toFixed(0):cu+(+t.cur_price).toFixed(2)):\'--\';\n    const pctNum=t.pct_away!=null?(+t.pct_away):null;\n    const pct=pctNum!=null?((pctNum>=0?\'+\':\'\')+pctNum.toFixed(1)+\'%\'):\'\';\n    const entry=t.entry_price?(\'entry \'+cu+(+t.entry_price>=100?(+t.entry_price).toFixed(0):(+t.entry_price).toFixed(2))):\'\';\n    const stop=t.stop_price?(\'stop \'+cu+(+t.stop_price>=100?(+t.stop_price).toFixed(0):(+t.stop_price).toFixed(2))):\'\';\n    const rr=t.rr?(\'R:R \'+t.rr):\'\';\n    const prob=t.prob?(t.prob+\'%\'):\'\';\n    return \'<div class="lwc \'+cls+\'">\'\n      +\'<div class="lwc-top"><span class="lwc-tk">\'+t.ticker+\'</span>\'\n      +\'<span class="lwc-tf">\'+tf+\'</span>\'\n      +\'<span class="lwc-dir \'+dirCls+\'">\'+dirLbl+\'</span>\'\n      +(prob?\'<span style="font-size:9px;color:var(--dm);margin-left:3px">\'+prob+\'</span>\':\'\')\n      +\'</div>\'\n      +\'<div class="lwc-st \'+cls+\'">\'+stLabel+\'</div>\'\n      +(price!==\'--\'?\'<div class="lwc-price">\'+price+(pct?\' <span style="color:var(--dm);font-size:9px">\'+pct+\'</span>\':\'\')+\'</div>\':\'\')\n      +((entry||stop||rr)?\'<div class="lwc-dist">\'\n        +(entry?entry:\'\')\n        +(stop&&entry?\' \\u00b7 \'+stop:stop?stop:\'\')\n        +(rr?(entry||stop?\' \\u00b7 \':\'\')+rr:\'\')\n        +\'</div>\':\'\')\n      +\'</div>\';\n  }).join(\'\');\n}\n\n/* ── Cycles Status (GO / WAIT for approval) ────────────────────────────── */\nfunction loadCyclesStatus(){\n  fetch(\'/api/cycles-status\').then(r=>r.json()).then(data=>{\n    const upd=document.getElementById(\'cy-upd\');\n    const cnt=document.getElementById(\'cy-cnt\');\n    if(upd)upd.textContent=data.updated?\'as of \'+data.updated:\'\';\n    CY_ITEMS=data.cards||[];\n    CY_TOTAL=data.total||0;\n    if(cnt)cnt.textContent=CY_ITEMS.length+\' signals / \'+CY_TOTAL+\' total\';\n    paintCycles();\n  }).catch(()=>{});\n}\nlet CY_ITEMS=[], CY_FILT=\'ALL\', CY_TOTAL=0;\nfunction setCyFilt(btn,f){\n  CY_FILT=f;\n  document.querySelectorAll(\'#cy-filt .lwf-btn\').forEach(b=>b.classList.remove(\'on\'));\n  if(btn)btn.classList.add(\'on\');\n  paintCycles();\n}\nfunction paintCycles(){\n  const grid=document.getElementById(\'cy-grid\');\n  if(!grid)return;\n  let cards=CY_ITEMS;\n  if(CY_FILT!==\'ALL\')cards=cards.filter(c=>c.tl===CY_FILT);\n  if(!cards.length){\n    grid.innerHTML=\'<div style="color:var(--dm);font-size:10px;padding:4px">\'\n      +(CY_ITEMS.length?\'No signals match this filter.\':\'No GO/WAIT signals in latest cycles report.\')\n      +\'</div>\';\n    return;\n  }\n  grid.innerHTML=cards.map(c=>{\n    const isGo=c.tl===\'GREEN\';\n    const cls=isGo?\'go\':\'wait\';\n    const stLabel=isGo?\'&#128994; GO\':\'&#9889; WAIT\';\n    const tf=c.horizon===\'MONTHLY\'?\'M\':\'W\';\n    const isLong=(c.direction||\'\').toUpperCase()===\'LONG\';\n    const dirStr=c.direction?(isLong?\'&#9650; LONG\':\'&#9660; SHORT\'):\'\';\n    return \'<div class="cyc \'+cls+\'">\'\n      +\'<div class="cyc-top"><span class="cyc-tk">\'+c.ticker+\'</span>\'\n      +\'<span class="cyc-tf">\'+tf+\'</span></div>\'\n      +\'<div class="cyc-st \'+cls+\'">\'+stLabel+\'</div>\'\n      +(dirStr?\'<div style="font-size:9px;color:var(--dm);margin-top:2px">\'+dirStr+\'</div>\':\'\')\n      +\'</div>\';\n  }).join(\'\');\n}\n\n/* ── Run History table ─────────────────────────────────────────────────── */\nfunction toggleHist(){\n  histOpen=!histOpen;\n  const body=document.getElementById(\'hist-body2\');\n  const chev=document.getElementById(\'hist-chev\');\n  body.style.display=histOpen?\'block\':\'none\';\n  if(chev){chev.classList.toggle(\'open\',histOpen);}\n}\n\nfunction loadHistory(){\n  fetch(\'/api/history\').then(r=>r.json()).then(rows=>{\n    const el=document.getElementById(\'hist-rows\');\n    if(!rows.length){\n      el.innerHTML=\'<div style="color:var(--dm);font-size:10px;padding:10px 14px">No runs recorded yet.</div>\';\n      return;\n    }\n    el.innerHTML=rows.map(r=>{\n      const ok=r.status===\'done\',err=r.status===\'error\';\n      const dur=r.duration_sec<60?r.duration_sec+\'s\':Math.floor(r.duration_sec/60)+\'m \'+(r.duration_sec%60)+\'s\';\n      const by=r.triggered_by===\'scheduler\'\n        ?\'<span class="hbadge scheduler">&#9201; scheduler</span>\'\n        :\'<span class="hbadge manual">&#128100; manual</span>\';\n      const st=ok?\'<span class="hok">&#10003; done</span>\'\n               :err?\'<span class="herr">&#10007; error</span>\'\n               :\'<span class="hstop">&#9632; stopped</span>\';\n      const ico=(TASK_ICONS[r.task]||\'&#9881;\');\n      return \'<div class="hist-row2">\'\n        +\'<span style="color:var(--dm)">\'+r.started+\'</span>\'\n        +\'<span>\'+ico+\' \'+r.label+\'</span>\'\n        +\'<span>\'+by+\'</span>\'\n        +\'<span>\'+st+\'</span>\'\n        +\'<span style="color:var(--dm)">\'+dur+\'</span>\'\n        +\'</div>\';\n    }).join(\'\');\n  }).catch(()=>{});\n}\n\n/* ── Init ──────────────────────────────────────────────────────────────── */\nloadToday();loadReports();loadWatchlist();loadSchedule();loadHistory();loadLiveCheck();loadCyclesStatus();updateWorldClocks();\nsetInterval(()=>fetch(\'/api/today\').then(r=>r.json()).then(d=>{\n  document.getElementById(\'hdr-t\').textContent=d.date+\'  \'+d.time;\n}).catch(()=>{}),60000);\nsetInterval(loadReports,      20000);\nsetInterval(loadWatchlist,    20000);\nsetInterval(loadSchedule,     60000);\nsetInterval(loadHistory,      30000);\nsetInterval(loadLiveCheck,    60000);\nsetInterval(loadCyclesStatus, 120000);\nsetInterval(updateWorldClocks, 15000);\n</script>\n</body>\n</html>'


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    port = 5050
    if '--port' in sys.argv:
        idx  = sys.argv.index('--port')
        port = int(sys.argv[idx + 1])

    print(f'  Cycles Trading Dashboard')
    print(f'  Open: http://localhost:{port}')
    print(f'  Press Ctrl+C to stop.')
    print()
    app.run(host='127.0.0.1', port=port, debug=False, threaded=True)
