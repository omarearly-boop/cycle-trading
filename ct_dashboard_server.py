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

# ---------------------------------------------------------------------------
BASE_DIR    = Path(__file__).parent
REPORTS_DIR = BASE_DIR / 'REPORTS'
REPORTS_DIR.mkdir(exist_ok=True)

app      = Flask(__name__)
JOBS     = {}
JOB_LOCK = threading.Lock()

TASKS = {
    'weekly': {
        'label':    'Weekly Retest',
        'icon':     '📊',
        'desc':     'RSI 30-67 · Near S/R · 24 Factors',
        'schedule': 'Sunday 08:00',
        'color':    '#22c55e',
        'cmd':      [sys.executable, 'cycles_trading_scanner.py'],
        'report':   'cycles_report_*.html',
    },
    'momentum': {
        'label':    'Momentum',
        'icon':     '🚀',
        'desc':     'RSI 55-78 · Above MA20/50 · SPY >2%',
        'schedule': 'Sunday 08:00',
        'color':    '#f59e0b',
        'cmd':      [sys.executable, 'cycles_trading_scanner.py', 'momentum'],
        'report':   'momentum_report_*.html',
    },
    'review': {
        'label':    'Weekly Review',
        'icon':     '📋',
        'desc':     'מה זז השבוע · שיפור לוגיקה',
        'schedule': 'Sunday 08:00',
        'color':    '#a78bfa',
        'cmd':      [sys.executable, 'ct_weekly_review.py'],
        'report':   'weekly_review_*.html',
    },
    'monthly': {
        'label':    'Monthly S/R',
        'icon':     '🗓️',
        'desc':     'Monthly levels · Fibonacci golden zone',
        'schedule': '1st Sunday / month',
        'color':    '#38bdf8',
        'cmd':      [sys.executable, 'cycles_trading_scanner.py', 'monthly'],
        'report':   'monthly_scan_*.html',
    },
    'daily': {
        'label':    'Watch Checker',
        'icon':     '🔔',
        'desc':     'Checks watchlist · Email on GREEN hit',
        'schedule': 'Daily 08:00 · 09:45 · 16:45',
        'color':    '#ef4444',
        'cmd':      [sys.executable, 'ct_watch_checker.py'],
        'report':   'watch_report_*.html',
    },
    'pipeline': {
        'label':    'Full Pipeline',
        'icon':     '⚡',
        'desc':     'Runs all tasks per today\'s schedule',
        'schedule': 'Daily 08:00 (auto-decide)',
        'color':    '#58a6ff',
        'cmd':      [sys.executable, 'ct_pipeline.py'],
        'report':   None,
    },
    'all': {
        'label':    'Full Workflow',
        'icon':     '🔥',
        'desc':     'Runs ALL scans regardless of schedule',
        'schedule': 'Manual only',
        'color':    '#ff6b6b',
        'cmd':      [sys.executable, 'ct_pipeline.py', '--force', 'all'],
        'report':   None,
    },
}


# ---------------------------------------------------------------------------
# Job runner (background thread)
# ---------------------------------------------------------------------------

def _run_job(job_id: str, task: str):
    info = TASKS[task]
    env  = os.environ.copy()
    env.setdefault('CT_PORTFOLIO_SIZE', '25000')
    # Force unbuffered + UTF-8 so live output flows through the pipe immediately
    env['PYTHONUNBUFFERED']  = '1'
    env['PYTHONUTF8']        = '1'
    env['PYTHONIOENCODING']  = 'utf-8'

    with JOB_LOCK:
        JOBS[job_id]['status'] = 'running'

    lines = [f'▶  Starting: {info["label"]}', '']
    try:
        proc = subprocess.Popen(
            info['cmd'], cwd=str(BASE_DIR), env=env,
            stdin=subprocess.DEVNULL,           # no interactive prompts
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

    with JOB_LOCK:
        JOBS[job_id].update({'status': status, 'log': lines,
                             'end': time.time()})


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


@app.route('/api/run/<task>', methods=['POST'])
def api_run(task):
    if task not in TASKS:
        return jsonify({'error': f'Unknown task: {task}'}), 400
    job_id = str(uuid.uuid4())[:8]
    with JOB_LOCK:
        JOBS[job_id] = {
            'task': task, 'status': 'starting',
            'log': [], 'start': time.time(), 'end': None
        }
    threading.Thread(target=_run_job, args=(job_id, task), daemon=True).start()
    return jsonify({'job_id': job_id})


@app.route('/api/job/<job_id>')
def api_job(job_id):
    with JOB_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return jsonify({'error': 'not found'}), 404
    elapsed = int(time.time() - job['start'])
    return jsonify({
        'task':    job['task'],
        'status':  job['status'],
        'log':     job['log'],
        'elapsed': elapsed,
    })




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
            try:
                proc.kill()
            except Exception:
                pass
        with JOB_LOCK:
            JOBS[job_id]['status'] = 'stopped'
            JOBS[job_id].setdefault('log', []).extend(['', '\u25a0  Stopped by user.'])
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
                try:
                    proc.kill()
                except Exception:
                    pass
            with JOB_LOCK:
                JOBS[jid]['status'] = 'stopped'
                JOBS[jid].setdefault('log', []).extend(['', '\u25a0  Stopped by user.'])
            count += 1
    return jsonify({'stopped': count})

@app.route('/api/reports')
def api_reports():
    PREFIXES = ['cycles_report','momentum_report','weekly_review','monthly_scan','watch_report']
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
# Embedded workflow dashboard HTML
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="he">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cycles Trading Pipeline</title>
<style>
:root{
  --bg:#0c1220;--card:#1a2236;--card2:#131d30;
  --border:#2a3a52;--text:#e2e8f0;--muted:#8899aa;--dim:#4a5568;
  --blue:#4f9eff;--green:#22c55e;--amber:#f59e0b;--red:#ef4444;
  --violet:#a78bfa;--sky:#38bdf8;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;overflow-x:hidden}
.hdr{background:#080e1a;border-bottom:1px solid var(--border);padding:14px 24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.hdr-logo{color:var(--blue);font-size:18px;font-weight:700;white-space:nowrap}
.hdr-time{color:var(--muted);font-size:13px;flex:1;min-width:120px}
.hdr-day{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:4px 14px;font-size:12px;color:var(--muted)}
.btn-run-all{background:linear-gradient(135deg,#1d4ed8,#7c3aed);color:#fff;border:none;border-radius:8px;padding:8px 20px;font-size:13px;font-weight:700;cursor:pointer;transition:opacity .15s;white-space:nowrap}
.btn-run-all:hover{opacity:.85}
.btn-run-all:disabled{opacity:.4;cursor:not-allowed}
.today-bar{background:#111827;border-bottom:1px solid var(--border);padding:8px 24px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:12px;color:var(--muted)}
.today-bar b{color:var(--text)}
.plan-pill{border-radius:20px;padding:2px 10px;font-size:11px;font-weight:700;border:1px solid;background:transparent}
/* WORKFLOW */
.wf-outer{padding:24px;overflow-x:auto}
.wf{display:flex;align-items:flex-start;gap:0;min-width:920px}
.stage{display:flex;flex-direction:column;gap:10px;flex:1;min-width:160px}
.stage-hdr{display:flex;align-items:center;gap:8px;padding:0 2px 10px;border-bottom:1px solid var(--border);margin-bottom:4px}
.stage-num{width:22px;height:22px;border-radius:50%;background:var(--card);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:var(--blue);flex-shrink:0}
.stage-name{font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:var(--dim);font-weight:700}
.stage-tag{margin-left:auto;font-size:9px;padding:1px 7px;border-radius:10px;border:1px solid var(--border);color:var(--dim)}
.arrow-col{display:flex;align-items:center;justify-content:center;padding:0 6px;padding-top:14px;flex-shrink:0;min-width:28px;opacity:.5}
/* NODES */
.node{background:var(--card);border:1.5px solid var(--border);border-radius:10px;padding:12px;position:relative;overflow:hidden;transition:border-color .25s,box-shadow .25s}
.node .top-bar{position:absolute;top:0;left:0;right:0;height:3px;border-radius:10px 10px 0 0}
.node-head{display:flex;align-items:center;gap:7px;margin-bottom:6px}
.node-icon{font-size:16px}
.node-label{font-size:13px;font-weight:700}
.node-desc{font-size:11px;color:var(--muted);line-height:1.5;margin-bottom:8px}
.node-sched{font-size:10px;color:var(--dim);margin-bottom:10px}
.node-footer{display:flex;align-items:center;justify-content:space-between;gap:6px}
.node-badge{font-size:10px;padding:2px 8px;border-radius:12px;background:var(--bg);color:var(--dim);font-weight:600}
.btn-run{border:none;border-radius:6px;padding:5px 13px;font-size:11px;font-weight:700;cursor:pointer;color:#000;transition:opacity .15s,transform .1s;flex-shrink:0}
.btn-run:hover{opacity:.85}
.btn-run:active{transform:scale(.96)}
.btn-run:disabled{opacity:.35;cursor:not-allowed}
/* States */
.node.state-running{border-color:var(--blue);animation:glow 1.4s ease-in-out infinite}
.node.state-running .node-badge{color:var(--blue)}
.node.state-done{border-color:#166534}
.node.state-done .node-badge{color:var(--green)}
.node.state-error{border-color:#991b1b}
.node.state-error .node-badge{color:var(--red)}
.node.state-queued{opacity:.65}
.node.state-queued .node-badge{color:var(--amber)}
.node.is-today{box-shadow:0 0 0 1px #1e3a5f inset}
@keyframes glow{0%,100%{box-shadow:0 0 0 2px #4f9eff22}50%{box-shadow:0 0 12px 3px #4f9eff44}}
/* STORE */
.store-node{background:var(--card);border:1.5px solid var(--border);border-radius:10px;padding:14px;text-align:center}
.store-icon{font-size:28px;margin-bottom:6px}
.store-name{font-size:12px;font-weight:700;color:var(--sky);margin-bottom:4px}
.store-count{font-size:26px;font-weight:700;color:var(--text);margin-bottom:2px}
.store-sub{font-size:10px;color:var(--dim)}
.store-tickers{margin-top:8px;display:flex;flex-direction:column;gap:3px;max-height:130px;overflow-y:auto}
.store-tick{display:flex;align-items:center;gap:6px;padding:3px 6px;background:var(--bg);border-radius:5px;font-size:10px}
.store-tick .sym{font-weight:700;min-width:44px}
.store-tick .dir{font-weight:700;font-size:9px}
.store-tick .stat{color:var(--dim);font-size:9px;margin-left:auto}
/* OUTPUT */
.out-node{background:var(--card);border:1.5px solid var(--border);border-radius:10px;padding:12px}
.out-title{font-size:11px;font-weight:700;color:var(--muted);margin-bottom:8px}
.out-report{display:flex;align-items:center;justify-content:space-between;padding:4px 6px;background:var(--bg);border-radius:5px;margin-bottom:4px;border:1px solid var(--border);gap:6px}
.out-report .rn{font-size:10px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.btn-open{font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid var(--blue);color:var(--blue);background:transparent;cursor:pointer;text-decoration:none;white-space:nowrap;flex-shrink:0}
.btn-open:hover{background:var(--blue);color:#000}
.email-node{background:linear-gradient(135deg,#1a2236,#1a1f30);border:1.5px solid #1e3a5f;border-radius:10px;padding:12px;margin-top:10px}
.en-title{font-size:11px;font-weight:700;color:var(--amber);margin-bottom:4px}
.en-cond{font-size:10px;color:var(--green);margin-bottom:2px}
.en-addr{font-size:10px;color:var(--dim)}
/* TERMINAL */
.term-section{padding:0 24px 24px}
.term-wrap{background:#080e1a;border:1px solid var(--border);border-radius:10px;overflow:hidden}
.term-bar{background:#0e1726;border-bottom:1px solid var(--border);padding:8px 14px;display:flex;align-items:center;gap:8px}
.dot{width:10px;height:10px;border-radius:50%}
.dot-r{background:#ef4444}.dot-y{background:#f59e0b}.dot-g{background:#22c55e}
.term-title{font-size:12px;color:var(--muted);flex:1}
.job-tag{font-size:11px;padding:2px 10px;border-radius:10px;background:var(--card);color:var(--dim)}
.job-tag.running{color:var(--blue);animation:pulse .9s infinite}
.job-tag.done{color:var(--green)}.job-tag.error{color:var(--red)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.btn-clear{background:transparent;border:1px solid var(--border);color:var(--dim);border-radius:5px;padding:3px 10px;font-size:11px;cursor:pointer}
.btn-clear:hover{border-color:var(--muted);color:var(--text)}
.btn-stop-node{border:none;border-radius:6px;padding:5px 9px;font-size:13px;font-weight:700;cursor:pointer;transition:opacity .15s;flex-shrink:0}
.btn-stop-node:hover{opacity:.75}
pre#terminal{font-family:'Courier New',monospace;font-size:11.5px;line-height:1.55;color:#b0c4d8;padding:14px 16px;min-height:160px;max-height:320px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;background:transparent}
</style>
</head>
<body>

<div class="hdr">
  <div class="hdr-logo">&#9889; Cycles Trading Pipeline</div>
  <div class="hdr-time" id="hdr-time">Loading...</div>
  <span class="hdr-day" id="hdr-day">&#x2014;</span>
  <button class="btn-run-all" id="btn-all" onclick="runTask('pipeline')">&#9654; Run Today</button>
  <button class="btn-run-all" id="btn-run-all-wf" onclick="startFullWorkflow()"
    style="background:linear-gradient(135deg,#7f1d1d,#dc2626)">&#128293; Run All</button>
  <button class="btn-run-all" id="btn-stop-all" onclick="stopAll()" style="background:linear-gradient(135deg,#1a1a2e,#4a0000);border:1px solid #7f1d1d">&#9632; Stop All</button>
</div>

<div class="today-bar">
  <b>Auto-plan today:</b>
  <span id="plan-pills">Loading...</span>
</div>

<div class="wf-outer"><div class="wf">

  <!-- Stage 1: Scheduler -->
  <div class="stage" style="max-width:148px">
    <div class="stage-hdr">
      <div class="stage-num">1</div>
      <div class="stage-name">Scheduler</div>
    </div>
    <div class="node">
      <div class="top-bar" style="background:#58a6ff"></div>
      <div class="node-head"><span class="node-icon">&#128336;</span><span class="node-label" style="font-size:12px">Automatic</span></div>
      <div class="node-desc">
        <b style="color:#4f9eff">Daily</b><br>08:00 &middot; 09:45 &middot; 16:45<br><br>
        <b style="color:#22c55e">Sunday</b><br>Full scan 08:00<br><br>
        <b style="color:#38bdf8">1st Sunday</b><br>+ Monthly S/R
      </div>
      <div style="margin-top:8px">
        <span class="plan-pill" id="sch-day" style="color:#58a6ff;border-color:#1e3a5f">&#x2014;</span>
      </div>
    </div>
  </div>

  <div class="arrow-col"><svg width="24" height="24" viewBox="0 0 24 24" fill="none"><path d="M5 12h14M13 6l6 6-6 6" stroke="#4f9eff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></div>

  <!-- Stage 2: Scanners -->
  <div class="stage">
    <div class="stage-hdr">
      <div class="stage-num">2</div>
      <div class="stage-name">Scanners</div>
      <span class="stage-tag">parallel</span>
    </div>
    <div id="scanner-nodes"></div>
  </div>

  <div class="arrow-col"><svg width="24" height="24" viewBox="0 0 24 24" fill="none"><path d="M5 12h14M13 6l6 6-6 6" stroke="#4f9eff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></div>

  <!-- Stage 3: Data Store -->
  <div class="stage" style="max-width:162px">
    <div class="stage-hdr">
      <div class="stage-num">3</div>
      <div class="stage-name">Data Store</div>
    </div>
    <div class="store-node">
      <div class="store-icon">&#128190;</div>
      <div class="store-name">watch_alerts.json</div>
      <div class="store-count" id="store-count">&#x2014;</div>
      <div class="store-sub">stocks tracked</div>
      <div class="store-tickers" id="store-tickers"></div>
    </div>
  </div>

  <div class="arrow-col"><svg width="24" height="24" viewBox="0 0 24 24" fill="none"><path d="M5 12h14M13 6l6 6-6 6" stroke="#4f9eff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></div>

  <!-- Stage 4: Checker -->
  <div class="stage" style="max-width:185px">
    <div class="stage-hdr">
      <div class="stage-num">4</div>
      <div class="stage-name">Checker</div>
    </div>
    <div id="checker-node"></div>
  </div>

  <div class="arrow-col"><svg width="24" height="24" viewBox="0 0 24 24" fill="none"><path d="M5 12h14M13 6l6 6-6 6" stroke="#4f9eff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></div>

  <!-- Stage 5: Output -->
  <div class="stage">
    <div class="stage-hdr">
      <div class="stage-num">5</div>
      <div class="stage-name">Output</div>
    </div>
    <div class="out-node">
      <div class="out-title">&#128196; Reports</div>
      <div id="output-reports"><div style="color:var(--dim);font-size:11px">Loading...</div></div>
    </div>
    <div class="email-node">
      <div class="en-title">&#128231; Email Alerts</div>
      <div class="en-cond">&#9679; GREEN watchlist hit</div>
      <div class="en-cond" style="color:var(--amber)">&#9889; Momentum GO (SPY &gt;2%)</div>
      <div class="en-cond" style="color:var(--violet)">&#128196; Sunday scan reports</div>
      <div class="en-addr">omarearly@gmail.com</div>
    </div>
  </div>

</div></div>

<div class="term-section">
  <div class="term-wrap">
    <div class="term-bar">
      <div class="dot dot-r"></div><div class="dot dot-y"></div><div class="dot dot-g"></div>
      <span class="term-title">Log Output</span>
      <span class="job-tag" id="job-tag">IDLE</span>
      <button class="btn-clear" onclick="clearLog()">Clear</button>
    </div>
    <pre id="terminal">$ Cycles Trading Dashboard ready.
$ Click Run on any scanner node to start.</pre>
  </div>
</div>

<script>
const SCANNERS = {
  weekly:   {label:'Weekly Retest',icon:'&#128202;',desc:'RSI 30-67 &middot; Near S/R &middot; 24 Factors',schedule:'Sunday 08:00',color:'#22c55e'},
  momentum: {label:'Momentum',icon:'&#128640;',desc:'RSI 55-78 &middot; Above MA20/50 &middot; SPY &gt;2%',schedule:'Sunday 08:00',color:'#f59e0b'},
  review:   {label:'Weekly Review',icon:'&#128203;',desc:'What moved &middot; Improve logic',schedule:'Sunday 08:00',color:'#a78bfa'},
  monthly:  {label:'Monthly S/R',icon:'&#128197;',desc:'Monthly levels &middot; Fibonacci golden zone',schedule:'1st Sunday/month',color:'#38bdf8'},
};
const CHECKER = {
  daily: {label:'Watch Checker',icon:'&#128276;',desc:'Checks watchlist &middot; Email on GREEN',schedule:'Daily 08:00 &middot; 09:45 &middot; 16:45',color:'#ef4444'},
};

let currentJob=null, activeTask=null, pollTimer=null, nodeStates={}, todayTasks=[];

function buildNode(key,info,container){
  const d=document.createElement('div');
  d.className='node state-idle';d.id='node-'+key;
  d.innerHTML=
    '<div class="top-bar" style="background:'+info.color+'"></div>'+
    '<div class="node-head"><span class="node-icon">'+info.icon+'</span>'+
    '<span class="node-label">'+info.label+'</span></div>'+
    '<div class="node-desc">'+info.desc+'</div>'+
    '<div class="node-sched">&#128336; '+info.schedule+'</div>'+
    '<div class="node-footer">'+
      '<span class="node-badge" id="badge-'+key+'">idle</span>'+
      '<button class="btn-run" id="btn-'+key+'" style="background:'+info.color+'" onclick="runTask(\''+key+'\')">&#9654; Run</button>'+
    '<button class="btn-stop-node" id="btn-stop-'+key+'" style="display:none;background:#7f1d1d;color:#fca5a5" onclick="stopCurrentJob()">&#9632;</button>'+
    '</div>';
  container.appendChild(d);
  nodeStates[key]='idle';
}

function buildNodes(){
  const sc=document.getElementById('scanner-nodes');
  Object.entries(SCANNERS).forEach(([k,v])=>buildNode(k,v,sc));
  const cc=document.getElementById('checker-node');
  Object.entries(CHECKER).forEach(([k,v])=>buildNode(k,v,cc));
}

function setNodeState(key,state,badge){
  const node=document.getElementById('node-'+key);
  const bdg=document.getElementById('badge-'+key);
  const btn=document.getElementById('btn-'+key);
  const sbtn=document.getElementById('btn-stop-'+key);
  if(!node)return;
  node.className='node state-'+state+(todayTasks.includes(key)?' is-today':'');
  if(bdg)bdg.textContent=badge||state;
  if(btn)btn.disabled=(state==='running');
  if(sbtn)sbtn.style.display=(state==='running')?'inline-block':'none';
  nodeStates[key]=state;
}

function resetAll(){
  [...Object.keys(SCANNERS),...Object.keys(CHECKER)].forEach(k=>setNodeState(k,'idle','idle'));
  document.querySelectorAll('.btn-run').forEach(b=>b.disabled=false);
  document.getElementById('btn-all').disabled=false;
  document.getElementById('btn-run-all-wf').disabled=false;
}

function startFullWorkflow(){
  if(currentJob&&!confirm('A task is already running. Start anyway?'))return;
  if(currentJob)stopPoll();
  // Mark all nodes as queued first (visual)
  [...Object.keys(SCANNERS),...Object.keys(CHECKER)].forEach(k=>setNodeState(k,'queued','waiting...'));
  activeTask='all';
  document.querySelectorAll('.btn-run').forEach(b=>b.disabled=true);
  document.getElementById('btn-all').disabled=true;
  document.getElementById('btn-run-all-wf').disabled=true;
  setJobTag('Running Full Workflow...','running');
  setLog(['Starting Full Workflow — all 5 scans in sequence...']);
  fetch('/api/run/all',{method:'POST'})
    .then(r=>r.json())
    .then(d=>{
      if(d.error){setLog(['Error: '+d.error]);setJobTag('ERROR','error');resetAll();return;}
      currentJob=d.job_id;startPoll();
    })
    .catch(e=>{setLog(['Error: '+e]);setJobTag('ERROR','error');resetAll();});
}

function runTask(task){
  if(currentJob&&!confirm('A task is already running. Start anyway?'))return;
  if(currentJob)stopPoll();
  activeTask=task;
  document.querySelectorAll('.btn-run').forEach(b=>b.disabled=true);
  document.getElementById('btn-all').disabled=true;
  if(SCANNERS[task]||CHECKER[task])setNodeState(task,'running','starting...');
  setJobTag('Starting...','running');
  setLog(['Starting: '+(SCANNERS[task]||CHECKER[task]||{label:task}).label]);
  fetch('/api/run/'+task,{method:'POST'})
    .then(r=>r.json())
    .then(d=>{
      if(d.error){setLog(['Error: '+d.error]);setJobTag('ERROR','error');resetAll();return;}
      currentJob=d.job_id;startPoll();
    })
    .catch(e=>{setLog(['Network error: '+e]);setJobTag('ERROR','error');resetAll();});
}

function parsePipelineLog(lines){
  const MAP={'Weekly Retest Scan':'weekly','Momentum Scan':'momentum',
             'Weekly Review':'review','Monthly S/R Scan':'monthly','Daily Watch Checker':'daily'};
  let cur=null;
  lines.forEach(l=>{
    const m=l.match(/>>>\s+(.+)/);
    if(m){
      const k=MAP[m[1].trim()];
      if(k){if(cur&&cur!==k)setNodeState(cur,'done','done');cur=k;setNodeState(k,'running','running...');}
    }
    if(l.includes('OK in')&&cur){setNodeState(cur,'done','done');cur=null;}
    if(l.includes('FAILED')&&cur){setNodeState(cur,'error','error');cur=null;}
  });
}

function stopCurrentJob(){
  if(!currentJob)return;
  fetch('/api/stop/'+currentJob,{method:'POST'}).then(r=>r.json()).then(()=>{
    stopPoll();
    setJobTag('Stopped','error');
    setLog((document.getElementById('terminal').textContent+'\n\u25a0  Stopped by user.').split('\n'));
    resetAll();
    currentJob=null;activeTask=null;
  }).catch(()=>{});
}

function stopAll(){
  fetch('/api/stop/all',{method:'POST'}).then(r=>r.json()).then(d=>{
    stopPoll();
    setJobTag('Stopped','error');
    setLog((document.getElementById('terminal').textContent+'\n\u25a0  All stopped.').split('\n'));
    resetAll();
    currentJob=null;activeTask=null;
  }).catch(()=>{});
}

function startPoll(){
  stopPoll();
  pollTimer=setInterval(()=>{
    fetch('/api/job/'+currentJob).then(r=>r.json()).then(d=>{
      setLog(d.log);
      const el=d.elapsed<60?d.elapsed+'s':Math.floor(d.elapsed/60)+'m'+(d.elapsed%60)+'s';
      if(activeTask==='pipeline'||activeTask==='all')parsePipelineLog(d.log);
      if(d.status==='running'||d.status==='starting'){
        setJobTag('Running '+el,'running');
        if(activeTask!=='pipeline'&&(SCANNERS[activeTask]||CHECKER[activeTask]))
          setNodeState(activeTask,'running',el);
      }else{
        stopPoll();currentJob=null;
        const ok=d.status==='done';
        if(activeTask!=='pipeline')setNodeState(activeTask,ok?'done':'error',ok?'done':'error');
        else if(ok||activeTask==='all'){[...Object.keys(SCANNERS),...Object.keys(CHECKER)].forEach(k=>{if(nodeStates[k]!=='done')setNodeState(k,'done','done');});}
        setJobTag(ok?'Done '+el:'Error',ok?'done':'error');
        document.querySelectorAll('.btn-run').forEach(b=>b.disabled=false);
        document.getElementById('btn-all').disabled=false;
        loadReports();loadWatchlist();activeTask=null;
      }
    }).catch(()=>{});
  },600);
}

function stopPoll(){if(pollTimer){clearInterval(pollTimer);pollTimer=null;}}
function setLog(lines){const el=document.getElementById('terminal');el.textContent=lines.join('\n');el.scrollTop=el.scrollHeight;}
function clearLog(){document.getElementById('terminal').textContent='$ Log cleared.';setJobTag('IDLE','');resetAll();}
function setJobTag(msg,cls){const el=document.getElementById('job-tag');el.textContent=msg;el.className='job-tag '+(cls||'');}

function loadToday(){
  fetch('/api/today').then(r=>r.json()).then(d=>{
    document.getElementById('hdr-time').textContent=d.date+'  '+d.time;
    document.getElementById('hdr-day').textContent=d.day;
    document.getElementById('sch-day').textContent=d.day;
    todayTasks=d.planned;
    const C={weekly:'#22c55e',momentum:'#f59e0b',review:'#a78bfa',monthly:'#38bdf8',daily:'#ef4444',pipeline:'#58a6ff'};
    const I={weekly:'&#128202;',momentum:'&#128640;',review:'&#128203;',monthly:'&#128197;',daily:'&#128276;',pipeline:'&#9889;'};
    const L={weekly:'Weekly',momentum:'Momentum',review:'Review',monthly:'Monthly S/R',daily:'Checker',pipeline:'Pipeline'};
    document.getElementById('plan-pills').innerHTML=d.planned.map(k=>
      '<span class="plan-pill" style="color:'+C[k]+';border-color:'+C[k]+'44">'+I[k]+' '+L[k]+'</span>'
    ).join(' ');
    d.planned.forEach(k=>{const n=document.getElementById('node-'+k);if(n)n.classList.add('is-today');});
  }).catch(()=>{});
}

function loadReports(){
  fetch('/api/reports').then(r=>r.json()).then(list=>{
    const el=document.getElementById('output-reports');
    if(!list.length){el.innerHTML='<div style="color:var(--dim);font-size:11px">No reports yet.</div>';return;}
    const C={cycles:'#22c55e',momentum:'#f59e0b',weekly_review:'#a78bfa',monthly:'#38bdf8',watch:'#ef4444'};
    el.innerHTML=list.slice(0,6).map(r=>{
      const key=Object.keys(C).find(k=>r.name.startsWith(k))||'';
      const c=C[key]||'#8899aa';
      const label=r.name.replace(/_\d{8}_\d{4}\.html$/,'').replace(/_/g,' ');
      const ts=(r.name.match(/(\d{8}_\d{4})/)||[])[1]||'';
      const tm=ts?ts.slice(6,8)+'/'+ts.slice(4,6)+' '+ts.slice(9,11)+':'+ts.slice(11):'';
      return '<div class="out-report"><div class="rn"><b style="color:'+c+'">'+label+'</b> '+
             '<span style="color:var(--dim)">'+tm+'</span></div>'+
             '<a class="btn-open" href="/report/'+r.name+'" target="_blank">Open</a></div>';
    }).join('');
  }).catch(()=>{});
}

function loadWatchlist(){
  fetch('/api/watchlist').then(r=>r.json()).then(data=>{
    const list=data.tickers||[];
    document.getElementById('store-count').textContent=list.length;
    const el=document.getElementById('store-tickers');
    if(!list.length){el.innerHTML='<div style="color:var(--dim);font-size:10px;margin-top:4px">Empty</div>';return;}
    el.innerHTML=list.map(t=>{
      const dc=t.direction==='LONG'?'#22c55e':'#ef4444';
      const sc=t.status==='GREEN'?'#22c55e':'#4a5568';
      const tf=t.timeframe==='MONTHLY'?' <span style="color:#38bdf8;font-size:8px">MO</span>':'';
      return '<div class="store-tick"><span class="sym">'+t.ticker+tf+'</span>'+
             '<span class="dir" style="color:'+dc+'">'+t.direction+'</span>'+
             '<span class="stat" style="color:'+sc+'">'+( t.status||'?')+'</span></div>';
    }).join('');
  }).catch(()=>{});
}

function refreshTime(){
  fetch('/api/today').then(r=>r.json()).then(d=>{
    document.getElementById('hdr-time').textContent=d.date+'  '+d.time;
  }).catch(()=>{});
}

buildNodes();
loadToday();
loadReports();
loadWatchlist();
setInterval(refreshTime,60000);
setInterval(loadReports,20000);
setInterval(loadWatchlist,20000);
</script>
</body>
</html>"""

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
