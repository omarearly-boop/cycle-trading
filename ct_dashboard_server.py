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
        'desc':     'RSI 30-67 · Near S/R · 26 Factors',
        'schedule': 'Sunday 08:00',
        'color':    '#3fb950',
        'cmd':      [sys.executable, 'cycles_trading_scanner.py'],
        'report':   'cycles_report_*.html',
    },
    'momentum': {
        'label':    'Momentum',
        'icon':     '🚀',
        'desc':     'RSI 55-78 · Above MA20/50 · SPY >2%',
        'schedule': 'Sunday 08:00',
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
        'schedule': 'Daily 08:00 · 09:45 · 16:45',
        'color':    '#f85149',
        'cmd':      [sys.executable, 'ct_watch_checker.py'],
        'report':   'watch_report_*.html',
    },
    'pipeline': {
        'label':    'Full Pipeline',
        'icon':     '⚡',
        'desc':     "Runs today's scheduled tasks",
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
        'color':    '#f85149',
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

    with JOB_LOCK:
        JOBS[job_id].update({'status': status, 'log': lines, 'end': time.time()})


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
# Jenkins-style Pipeline Dashboard
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cycles Trading Pipeline</title>
<style>
:root{
  --bg:#0d1117;--surface:#161b22;--border:#30363d;
  --text:#e6edf3;--muted:#8b949e;--dim:#484f58;
  --blue:#58a6ff;--green:#3fb950;--amber:#d29922;
  --red:#f85149;--violet:#a371f7;--sky:#79c0ff;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
     background:var(--bg);color:var(--text);min-height:100vh}

/* ── Header ── */
.hdr{background:#0d1117;border-bottom:1px solid var(--border);
     padding:10px 20px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.hdr-logo{color:var(--blue);font-size:15px;font-weight:700;letter-spacing:-.3px}
.hdr-time{color:var(--muted);font-size:12px;flex:1;min-width:100px}
.hdr-pill{background:var(--surface);border:1px solid var(--border);
          border-radius:20px;padding:3px 12px;font-size:11px;color:var(--muted)}
.btn-h{border:none;border-radius:6px;padding:6px 14px;font-size:12px;font-weight:700;
       cursor:pointer;color:#fff;transition:opacity .15s;white-space:nowrap}
.btn-h:hover{opacity:.82}.btn-h:disabled{opacity:.3;cursor:not-allowed}

/* ── Today bar ── */
.today-bar{background:var(--surface);border-bottom:1px solid var(--border);
           padding:5px 20px;display:flex;align-items:center;gap:8px;
           font-size:11px;color:var(--muted);flex-wrap:wrap}
.plan-pill{border-radius:20px;padding:2px 10px;font-size:10px;
           font-weight:700;border:1px solid;background:transparent}

/* ══════════════════════════════════════════
   JENKINS PIPELINE
══════════════════════════════════════════ */
.jk-wrap{padding:28px 24px 20px;overflow-x:auto}

/* Main horizontal pipe row */
.jk-pipe{display:flex;align-items:center;min-width:900px;gap:0;position:relative}

/* Horizontal connector line */
.jk-line{height:3px;background:var(--border);flex:1;min-width:20px;transition:background .4s}
.jk-line.lit{background:var(--green)}

/* ── Stage wrapper ── */
.jk-stage{display:flex;flex-direction:column;align-items:center;flex-shrink:0;
          position:relative;min-width:72px}

/* ── Circle bubble ── */
.jk-circle{
  width:54px;height:54px;border-radius:50%;
  border:3px solid var(--dim);
  background:var(--surface);
  display:flex;align-items:center;justify-content:center;
  font-size:22px;cursor:pointer;
  transition:border-color .3s,box-shadow .3s,background .3s;
  position:relative;z-index:2;user-select:none;
}
.jk-circle:hover{border-color:var(--blue);box-shadow:0 0 10px #58a6ff44}
.jk-circle.no-click{cursor:default}
.jk-circle.no-click:hover{border-color:var(--dim);box-shadow:none}

/* Stage label */
.jk-label{font-size:11px;font-weight:600;color:var(--muted);
          margin-top:9px;text-align:center;max-width:76px;line-height:1.3}
.jk-sub{font-size:10px;color:var(--dim);text-align:center;
        margin-top:3px;max-width:80px;min-height:14px}

/* Run button */
.jk-btn{margin-top:6px;font-size:9px;padding:2px 10px;border-radius:10px;
        background:transparent;border:1px solid var(--border);color:var(--dim);
        cursor:pointer;transition:all .15s;white-space:nowrap}
.jk-btn:hover{border-color:var(--blue);color:var(--blue)}
.jk-btn:disabled{opacity:.3;cursor:not-allowed;pointer-events:none}

/* ── States ── */
.s-idle   .jk-circle{border-color:var(--dim)}
.s-queued .jk-circle{border-color:var(--amber);opacity:.65}
.s-running .jk-circle{border-color:var(--blue)!important;
            animation:jk-glow 1.1s ease-in-out infinite}
.s-done   .jk-circle{border-color:var(--green)!important;background:#0d2318}
.s-error  .jk-circle{border-color:var(--red)!important;background:#2a0d0d}

.s-running .jk-label{color:var(--blue)}
.s-done    .jk-label{color:var(--green)}
.s-error   .jk-label{color:var(--red)}
.s-queued  .jk-label{color:var(--amber)}

/* Status dot (bottom-right of circle) */
.jk-dot{position:absolute;bottom:-3px;right:-3px;width:18px;height:18px;
        border-radius:50%;font-size:10px;font-weight:700;
        display:flex;align-items:center;justify-content:center;
        background:var(--dim);border:2px solid var(--bg);color:#fff;
        transition:background .3s;pointer-events:none}
.s-running .jk-dot{background:var(--blue)}
.s-done    .jk-dot{background:var(--green)}
.s-error   .jk-dot{background:var(--red)}
.s-queued  .jk-dot{background:var(--amber)}

/* Today highlight ring */
.today-ring .jk-circle{box-shadow:0 0 0 3px #58a6ff22}

@keyframes jk-glow{
  0%,100%{box-shadow:0 0 4px 1px #58a6ff33}
  50%{box-shadow:0 0 14px 4px #58a6ff66}
}
@keyframes spin{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}
.spin{display:inline-block;animation:spin .7s linear infinite}

/* ── Parallel scanners block ── */
.jk-para-wrap{
  display:flex;flex-direction:column;gap:0;
  position:relative;flex-shrink:0;
}

/* Vertical left + right bars connecting the parallel branches */
.jk-para-wrap::before,
.jk-para-wrap::after{
  content:'';position:absolute;
  top:27px;       /* center of top circle */
  bottom:27px;    /* center of bottom circle */
  width:3px;background:var(--border);z-index:1;
  transition:background .4s;
}
.jk-para-wrap::before{left:0}
.jk-para-wrap::after{right:0}
.jk-para-wrap.all-done::before,
.jk-para-wrap.all-done::after{background:var(--green)}

/* Each parallel row */
.jk-para-row{
  display:flex;align-items:center;gap:0;
  padding:6px 0;position:relative;
}

/* Horizontal stubs from vertical bar to circle */
.jk-stub{width:22px;height:3px;background:var(--border);flex-shrink:0;transition:background .4s}
.jk-stub.lit{background:var(--green)}

/* Smaller circle for parallel items */
.jk-circle-sm{
  width:46px;height:46px;border-radius:50%;
  border:3px solid var(--dim);
  background:var(--surface);
  display:flex;align-items:center;justify-content:center;
  font-size:18px;cursor:pointer;
  transition:border-color .3s,box-shadow .3s,background .3s;
  position:relative;z-index:2;user-select:none;flex-shrink:0;
}
.jk-circle-sm:hover{border-color:var(--blue);box-shadow:0 0 8px #58a6ff44}

/* Parallel item label area */
.jk-para-info{margin-left:10px;min-width:100px}
.jk-para-name{font-size:11px;font-weight:600;color:var(--muted);line-height:1.3}
.jk-para-badge{font-size:10px;color:var(--dim);margin-top:2px}
.jk-para-btn{
  display:inline-block;margin-top:4px;font-size:9px;padding:1px 8px;
  border-radius:8px;background:transparent;border:1px solid var(--border);
  color:var(--dim);cursor:pointer;transition:all .15s;
}
.jk-para-btn:hover{border-color:var(--blue);color:var(--blue)}
.jk-para-btn:disabled{opacity:.3;cursor:not-allowed;pointer-events:none}

/* Parallel item states (applied on the .jk-para-row) */
.ps-idle    .jk-circle-sm{border-color:var(--dim)}
.ps-queued  .jk-circle-sm{border-color:var(--amber);opacity:.65}
.ps-running .jk-circle-sm{border-color:var(--blue)!important;animation:jk-glow 1.1s ease-in-out infinite}
.ps-done    .jk-circle-sm{border-color:var(--green)!important;background:#0d2318}
.ps-error   .jk-circle-sm{border-color:var(--red)!important;background:#2a0d0d}
.ps-running .jk-para-name{color:var(--blue)}
.ps-done    .jk-para-name{color:var(--green)}
.ps-error   .jk-para-name{color:var(--red)}
.ps-queued  .jk-para-name{color:var(--amber)}
.ps-running .jk-para-badge{color:var(--blue)}
.ps-done    .jk-para-badge{color:var(--green)}

/* ── Data Store ── */
.jk-store{margin-top:8px;text-align:center}
.jk-count{font-size:20px;font-weight:700;color:var(--text)}
.jk-count-lbl{font-size:9px;color:var(--dim);margin-top:1px}
.jk-tickers{margin-top:6px;display:flex;flex-direction:column;gap:2px;
             max-height:90px;overflow-y:auto;min-width:110px}
.jk-tick{display:flex;align-items:center;gap:5px;padding:2px 5px;
         background:var(--surface);border-radius:3px;font-size:9px}
.jk-tick .sym{font-weight:700;min-width:32px;color:var(--text)}
.jk-tick .sta{margin-left:auto;color:var(--dim)}

/* ── Reports ── */
.jk-reports{margin-top:8px;min-width:148px}
.jk-rep{display:flex;align-items:center;justify-content:space-between;
        gap:5px;padding:3px 6px;background:var(--surface);border-radius:4px;
        margin-bottom:3px}
.jk-rep .rn{font-size:9px;color:var(--muted);overflow:hidden;
             text-overflow:ellipsis;white-space:nowrap;flex:1}
.btn-open{font-size:9px;padding:1px 7px;border-radius:4px;
          border:1px solid var(--blue);color:var(--blue);
          background:transparent;cursor:pointer;text-decoration:none;
          white-space:nowrap;flex-shrink:0}
.btn-open:hover{background:var(--blue);color:#000}

/* ── Terminal ── */
.term-wrap{margin:0 20px 20px;background:#0d1117;
           border:1px solid var(--border);border-radius:8px;overflow:hidden}
.term-bar{background:var(--surface);border-bottom:1px solid var(--border);
          padding:7px 14px;display:flex;align-items:center;gap:8px}
.dot{width:10px;height:10px;border-radius:50%}
.dot-r{background:#f85149}.dot-y{background:#d29922}.dot-g{background:#3fb950}
.term-title{font-size:12px;color:var(--muted);flex:1}
.job-tag{font-size:10px;padding:2px 10px;border-radius:10px;
         background:var(--surface);color:var(--dim)}
.job-tag.running{color:var(--blue);animation:blink .9s infinite}
.job-tag.done{color:var(--green)}.job-tag.error{color:var(--red)}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.btn-clear{background:transparent;border:1px solid var(--border);color:var(--dim);
           border-radius:5px;padding:3px 10px;font-size:11px;cursor:pointer}
.btn-clear:hover{border-color:var(--muted);color:var(--text)}
pre#terminal{font-family:'JetBrains Mono','Courier New',monospace;font-size:11px;
             line-height:1.6;color:#8b949e;padding:14px 16px;
             min-height:150px;max-height:300px;overflow-y:auto;
             white-space:pre-wrap;word-break:break-all;background:transparent}
</style>
</head>
<body>

<!-- ── Header ── -->
<div class="hdr">
  <div class="hdr-logo">&#9889; Cycles Trading Pipeline</div>
  <div class="hdr-time" id="hdr-time">Loading...</div>
  <span class="hdr-pill" id="hdr-day">&#x2014;</span>
  <button class="btn-h" id="btn-today" onclick="runTask('pipeline')"
          style="background:#1f6feb">&#9654; Run Today</button>
  <button class="btn-h" id="btn-all" onclick="startFullWorkflow()"
          style="background:#b91c1c">&#128293; Run All</button>
  <button class="btn-h" id="btn-stop" onclick="stopAll()"
          style="background:#161b22;border:1px solid #6e0f0f;color:#f85149">&#9632; Stop</button>
</div>

<!-- ── Today bar ── -->
<div class="today-bar">
  <b>Today&rsquo;s plan:</b>
  <span id="plan-pills">Loading...</span>
</div>

<!-- ══ JENKINS PIPELINE ══ -->
<div class="jk-wrap">
<div class="jk-pipe">

  <!-- ① Scheduler -->
  <div class="jk-stage s-done" id="node-scheduler">
    <div class="jk-circle no-click">
      &#9889;
      <div class="jk-dot">&#10003;</div>
    </div>
    <div class="jk-label">Scheduler</div>
    <div class="jk-sub" id="sch-day">auto</div>
  </div>

  <div class="jk-line" id="line-to-scan"></div>

  <!-- ② Parallel Scanners -->
  <div class="jk-para-wrap" id="para-wrap">

    <!-- Weekly -->
    <div class="jk-para-row ps-idle" id="node-weekly">
      <div class="jk-stub" id="stub-l-weekly"></div>
      <div class="jk-circle-sm" onclick="runTask('weekly')">&#128202;</div>
      <div class="jk-para-info">
        <div class="jk-para-name">Weekly Retest</div>
        <div class="jk-para-badge" id="badge-weekly">idle &middot; Sunday</div>
        <button class="jk-para-btn" id="btn-weekly" onclick="runTask('weekly')">&#9654; Run</button>
      </div>
      <div class="jk-stub jk-stub-r" id="stub-r-weekly"></div>
    </div>

    <!-- Momentum -->
    <div class="jk-para-row ps-idle" id="node-momentum">
      <div class="jk-stub" id="stub-l-momentum"></div>
      <div class="jk-circle-sm" onclick="runTask('momentum')">&#128640;</div>
      <div class="jk-para-info">
        <div class="jk-para-name">Momentum</div>
        <div class="jk-para-badge" id="badge-momentum">idle &middot; Sunday</div>
        <button class="jk-para-btn" id="btn-momentum" onclick="runTask('momentum')">&#9654; Run</button>
      </div>
      <div class="jk-stub jk-stub-r" id="stub-r-momentum"></div>
    </div>

    <!-- Weekly Review -->
    <div class="jk-para-row ps-idle" id="node-review">
      <div class="jk-stub" id="stub-l-review"></div>
      <div class="jk-circle-sm" onclick="runTask('review')">&#128203;</div>
      <div class="jk-para-info">
        <div class="jk-para-name">Weekly Review</div>
        <div class="jk-para-badge" id="badge-review">idle &middot; Sunday</div>
        <button class="jk-para-btn" id="btn-review" onclick="runTask('review')">&#9654; Run</button>
      </div>
      <div class="jk-stub jk-stub-r" id="stub-r-review"></div>
    </div>

    <!-- Monthly -->
    <div class="jk-para-row ps-idle" id="node-monthly">
      <div class="jk-stub" id="stub-l-monthly"></div>
      <div class="jk-circle-sm" onclick="runTask('monthly')">&#128197;</div>
      <div class="jk-para-info">
        <div class="jk-para-name">Monthly S/R</div>
        <div class="jk-para-badge" id="badge-monthly">idle &middot; 1st Sunday</div>
        <button class="jk-para-btn" id="btn-monthly" onclick="runTask('monthly')">&#9654; Run</button>
      </div>
      <div class="jk-stub jk-stub-r" id="stub-r-monthly"></div>
    </div>

  </div><!-- /para-wrap -->

  <div class="jk-line" id="line-to-store"></div>

  <!-- ③ Data Store -->
  <div class="jk-stage s-done" id="node-store">
    <div class="jk-circle no-click" style="font-size:20px">
      &#128190;
      <div class="jk-dot">&#10003;</div>
    </div>
    <div class="jk-label">Data Store</div>
    <div class="jk-store">
      <div class="jk-count" id="store-count">—</div>
      <div class="jk-count-lbl">watched</div>
      <div class="jk-tickers" id="store-tickers"></div>
    </div>
  </div>

  <div class="jk-line" id="line-to-checker"></div>

  <!-- ④ Watch Checker -->
  <div class="jk-stage s-idle" id="node-daily">
    <div class="jk-circle" onclick="runTask('daily')">
      &#128276;
      <div class="jk-dot">&#8212;</div>
    </div>
    <div class="jk-label">Watch Checker</div>
    <div class="jk-sub" id="badge-daily">Daily</div>
    <button class="jk-btn" id="btn-daily" onclick="runTask('daily')">&#9654; Run</button>
  </div>

  <div class="jk-line" id="line-to-out"></div>

  <!-- ⑤ Output -->
  <div class="jk-stage" id="node-output">
    <div class="jk-circle no-click" style="font-size:20px">
      &#128231;
      <div class="jk-dot" style="background:var(--dim)">&#8212;</div>
    </div>
    <div class="jk-label">Output</div>
    <div class="jk-reports" id="output-reports">
      <div style="color:var(--dim);font-size:10px;margin-top:6px">Loading&hellip;</div>
    </div>
  </div>

</div><!-- /jk-pipe -->
</div><!-- /jk-wrap -->

<!-- ── Terminal ── -->
<div class="term-wrap">
  <div class="term-bar">
    <div class="dot dot-r"></div><div class="dot dot-y"></div><div class="dot dot-g"></div>
    <span class="term-title">Log Output</span>
    <span class="job-tag" id="job-tag">IDLE</span>
    <button class="btn-clear" onclick="clearLog()">Clear</button>
  </div>
  <pre id="terminal">$ Cycles Trading Dashboard ready.
$ Click any stage to run it individually, or use Run All.</pre>
</div>

<script>
// ── State ──────────────────────────────────────────────────────────────────
const SCAN_KEYS = ['weekly','momentum','review','monthly'];
const ALL_KEYS  = ['weekly','momentum','review','monthly','daily'];

let currentJob = null, activeTask = null, pollTimer = null;
let nodeStates = {};
let todayTasks = [];

ALL_KEYS.forEach(k => nodeStates[k] = 'idle');

// ── Node state setters ─────────────────────────────────────────────────────
function setScannerState(key, state, badge) {
  const row   = document.getElementById('node-'+key);
  const bdg   = document.getElementById('badge-'+key);
  const btn   = document.getElementById('btn-'+key);
  const stubL = document.getElementById('stub-l-'+key);
  const stubR = document.getElementById('stub-r-'+key);
  if (!row) return;

  // Remove old ps- class, add new
  row.className = row.className.replace(/\bps-\w+/g,'').trim() + ' ps-'+state;

  if (bdg) {
    const icons = {running:'<span class="spin">&#9696;</span> running&hellip;',
                   done:'&#10003; done', error:'&#10007; error',
                   queued:'&#8230; queued', idle:'idle'};
    bdg.innerHTML = badge ? badge : (icons[state] || state);
  }
  if (btn) btn.disabled = (state === 'running');
  if (stubL && state === 'done') stubL.classList.add('lit');
  else if (stubL) stubL.classList.remove('lit');
  if (stubR && state === 'done') stubR.classList.add('lit');
  else if (stubR) stubR.classList.remove('lit');

  nodeStates[key] = state;

  // Light up vertical bars when all scanners done
  const allDone = SCAN_KEYS.every(k => nodeStates[k] === 'done');
  const pw = document.getElementById('para-wrap');
  if (pw) pw.classList.toggle('all-done', allDone);
}

function setCheckerState(state, badge) {
  const node = document.getElementById('node-daily');
  const bdg  = document.getElementById('badge-daily');
  const btn  = document.getElementById('btn-daily');
  const dot  = node ? node.querySelector('.jk-dot') : null;
  if (!node) return;

  node.className = node.className.replace(/\bs-\w+/g,'').trim() + ' s-'+state
                   + (todayTasks.includes('daily') ? ' today-ring' : '');

  if (dot) {
    const icons = {running:'<span class="spin">&#9696;</span>',done:'&#10003;',
                   error:'&#10007;',queued:'&#8230;',idle:'&#8212;'};
    dot.innerHTML = icons[state] || '&#8212;';
  }
  if (bdg) bdg.textContent = badge || (state==='idle'?'Daily':state);
  if (btn) btn.disabled = (state === 'running');
  nodeStates['daily'] = state;

  // Light up line after checker when done
  const lineOut = document.getElementById('line-to-out');
  if (lineOut) lineOut.classList.toggle('lit', state === 'done');
}

function setNodeState(key, state, badge) {
  if (key === 'daily') { setCheckerState(state, badge); return; }
  if (SCAN_KEYS.includes(key)) { setScannerState(key, state, badge); return; }
}

function resetAll() {
  ALL_KEYS.forEach(k => setNodeState(k, 'idle'));
  document.querySelectorAll('.jk-btn,.jk-para-btn').forEach(b => b.disabled = false);
  ['btn-today','btn-all'].forEach(id => { const b=document.getElementById(id); if(b) b.disabled=false; });
  // Reset lit lines
  ['line-to-store','line-to-checker','line-to-out'].forEach(id => {
    const el = document.getElementById(id); if(el) el.classList.remove('lit');
  });
  document.querySelectorAll('.jk-stub').forEach(s => s.classList.remove('lit'));
  const pw = document.getElementById('para-wrap');
  if (pw) pw.classList.remove('all-done');
}

// ── Task runners ───────────────────────────────────────────────────────────
function startFullWorkflow() {
  if (currentJob && !confirm('A task is running. Start anyway?')) return;
  if (currentJob) stopPoll();
  ALL_KEYS.forEach(k => setNodeState(k, 'queued', 'waiting…'));
  activeTask = 'all';
  lockButtons();
  setJobTag('Running Full Workflow…', 'running');
  setLog(['▶ Starting Full Workflow — all 5 scans in sequence…']);
  fetch('/api/run/all', {method:'POST'})
    .then(r => r.json())
    .then(d => {
      if (d.error) { setLog(['Error: '+d.error]); setJobTag('ERROR','error'); resetAll(); return; }
      currentJob = d.job_id; startPoll();
    })
    .catch(e => { setLog(['Error: '+e]); setJobTag('ERROR','error'); resetAll(); });
}

function runTask(task) {
  if (currentJob && !confirm('A task is running. Start anyway?')) return;
  if (currentJob) stopPoll();
  activeTask = task;
  lockButtons();
  if (ALL_KEYS.includes(task)) setNodeState(task, 'running', 'starting…');
  setJobTag('Starting…', 'running');
  setLog(['▶ Starting: ' + task]);
  fetch('/api/run/'+task, {method:'POST'})
    .then(r => r.json())
    .then(d => {
      if (d.error) { setLog(['Error: '+d.error]); setJobTag('ERROR','error'); resetAll(); return; }
      currentJob = d.job_id; startPoll();
    })
    .catch(e => { setLog(['Error: '+e]); setJobTag('ERROR','error'); resetAll(); });
}

// ── Pipeline log parser ────────────────────────────────────────────────────
function parsePipelineLog(lines) {
  const MAP = {
    'Weekly Retest Scan' : 'weekly',
    'Momentum Scan'      : 'momentum',
    'Weekly Review'      : 'review',
    'Monthly S/R Scan'   : 'monthly',
    'Daily Watch Checker': 'daily',
  };
  let cur = null;
  lines.forEach(l => {
    const m = l.match(/>>>\s+(.+)/);
    if (m) {
      const k = MAP[m[1].trim()];
      if (k) {
        if (cur && cur !== k) setNodeState(cur, 'done');
        cur = k;
        setNodeState(k, 'running');
      }
    }
    if ((l.includes('OK in') || l.includes('Exit code: 0')) && cur) {
      setNodeState(cur, 'done'); cur = null;
    }
    if ((l.includes('FAILED') || l.includes('TIMEOUT') || l.includes('EXCEPTION')) && cur) {
      setNodeState(cur, 'error'); cur = null;
    }
  });
}

// ── Polling ────────────────────────────────────────────────────────────────
function startPoll() {
  stopPoll();
  pollTimer = setInterval(() => {
    fetch('/api/job/'+currentJob)
      .then(r => r.json())
      .then(d => {
        setLog(d.log);
        const el = d.elapsed < 60 ? d.elapsed+'s'
                                  : Math.floor(d.elapsed/60)+'m'+(d.elapsed%60)+'s';

        if (activeTask === 'pipeline' || activeTask === 'all') parsePipelineLog(d.log);

        if (d.status === 'running' || d.status === 'starting') {
          setJobTag('Running ' + el, 'running');
          if (activeTask !== 'pipeline' && activeTask !== 'all' && ALL_KEYS.includes(activeTask))
            setNodeState(activeTask, 'running', el);
        } else {
          stopPoll(); currentJob = null;
          const ok = d.status === 'done';

          if (activeTask !== 'pipeline' && activeTask !== 'all') {
            setNodeState(activeTask, ok ? 'done' : 'error');
          } else {
            // Mark any remaining queued/running nodes
            ALL_KEYS.forEach(k => {
              if (nodeStates[k] !== 'done') setNodeState(k, ok ? 'done' : 'error');
            });
          }

          // Light up lines when everything is done
          if (ok) {
            ['line-to-store','line-to-checker','line-to-out'].forEach(id => {
              const el = document.getElementById(id); if(el) el.classList.add('lit');
            });
          }

          setJobTag(ok ? 'Done ' + el : 'Error', ok ? 'done' : 'error');
          unlockButtons();
          loadReports(); loadWatchlist();
          activeTask = null;
        }
      }).catch(() => {});
  }, 600);
}

function stopPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

function stopAll() {

  fetch('/api/stop/all', {method:'POST'})
    .then(r => r.json())
    .then(() => {
      stopPoll();
      setJobTag('Stopped', 'error');
      resetAll(); currentJob = null; activeTask = null;
    }).catch(() => {});
}

// -- UI helpers
function lockButtons() {
  ['btn-today','btn-all'].forEach(id => { const b=document.getElementById(id); if(b) b.disabled=true; });
  document.querySelectorAll('.jk-btn,.jk-para-btn').forEach(b => b.disabled=true);
}
function unlockButtons() {
  ['btn-today','btn-all'].forEach(id => { const b=document.getElementById(id); if(b) b.disabled=false; });
  document.querySelectorAll('.jk-btn,.jk-para-btn').forEach(b => b.disabled=false);
}

function setLog(lines) {
  const el = document.getElementById('terminal');
  el.textContent = lines.join('\n');
  el.scrollTop = el.scrollHeight;
}
function clearLog() {
  document.getElementById('terminal').textContent = '$ Log cleared.';
  setJobTag('IDLE', '');
  resetAll();
}
function setJobTag(msg, cls) {
  const el = document.getElementById('job-tag');
  el.textContent = msg;
  el.className = 'job-tag ' + (cls || '');
}

// -- Data loaders
function loadToday() {
  fetch('/api/today').then(r => r.json()).then(d => {
    document.getElementById('hdr-time').textContent = d.date + '  ' + d.time;
    document.getElementById('hdr-day').textContent  = d.day;
    document.getElementById('sch-day').textContent  = d.day;
    todayTasks = d.planned;
    const C = {weekly:'#3fb950',momentum:'#d29922',review:'#a371f7',
               monthly:'#79c0ff',daily:'#f85149',pipeline:'#58a6ff'};
    const L = {weekly:'Weekly',momentum:'Momentum',review:'Review',monthly:'Monthly',daily:'Checker'};
    document.getElementById('plan-pills').innerHTML = d.planned
      .filter(k => k !== 'pipeline')
      .map(k => '<span class="plan-pill" style="color:'+C[k]+';border-color:'+C[k]+'55">'
               +(L[k]||k)+'</span>')
      .join(' ');
    d.planned.forEach(k => {
      const n = document.getElementById('node-'+k);
      if (n && !n.className.includes('today-ring')) n.className += ' today-ring';
    });
  }).catch(() => {});
}

function loadReports() {
  fetch('/api/reports').then(r => r.json()).then(list => {
    const el = document.getElementById('output-reports');
    if (!list.length) {
      el.innerHTML='<div style="color:var(--dim);font-size:10px;margin-top:6px">No reports yet.</div>';
      return;
    }
    const C = {cycles:'#3fb950',momentum:'#d29922',weekly_review:'#a371f7',monthly:'#79c0ff',watch:'#f85149'};
    el.innerHTML = list.slice(0,5).map(r => {
      const key = Object.keys(C).find(k => r.name.startsWith(k)) || '';
      const c   = C[key] || '#8b949e';
      const lbl = r.name.replace(/_\d{8}_\d{4}\.html$/, '').replace(/_/g,' ');
      return '<div class="jk-rep"><div class="rn" style="color:'+c+'">'+lbl+'</div>'
           + '<a class="btn-open" href="/report/'+r.name+'" target="_blank">Open</a></div>';
    }).join('');
  }).catch(() => {});
}

function loadWatchlist() {
  fetch('/api/watchlist').then(r => r.json()).then(data => {
    const list = data.tickers || [];
    document.getElementById('store-count').textContent = list.length;
    const el = document.getElementById('store-tickers');
    if (!list.length) {
      el.innerHTML='<div style="color:var(--dim);font-size:9px">Empty</div>';
      return;
    }
    const SC = {GREEN:'#3fb950',YELLOW:'#d29922',RED:'#f85149'};
    el.innerHTML = list.slice(0,8).map(t => {
      const dc = t.direction === 'LONG' ? '#3fb950' : '#f85149';
      const sc = SC[t.status] || '#484f58';
      return '<div class="jk-tick"><span class="sym">'+t.ticker+'</span>'
           + '<span style="color:'+dc+';font-size:9px;font-weight:700">'+t.direction+'</span>'
           + '<span class="sta" style="color:'+sc+'">'+( t.status||'?')+'</span></div>';
    }).join('') + (list.length > 8
      ? '<div style="color:var(--dim);font-size:9px;margin-top:2px">+' + (list.length-8) + ' more</div>'
      : '');
  }).catch(() => {});
}

function refreshTime() {
  fetch('/api/today').then(r => r.json()).then(d => {
    document.getElementById('hdr-time').textContent = d.date + '  ' + d.time;
  }).catch(() => {});
}

// -- Init
loadToday();
loadReports();
loadWatchlist();
setInterval(refreshTime, 60000);
setInterval(loadReports, 20000);
setInterval(loadWatchlist, 20000);
</script>
</body>
</html>
"""



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
