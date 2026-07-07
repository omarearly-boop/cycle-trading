#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ct_watch_checker.py -- Daily green-light checker for Cycles Trading watchlist.

Reads watch_alerts.json, runs analyze() on each ticker.
Sends email ONLY when traffic light is GREEN and not already alerted today.

Usage:
  python ct_watch_checker.py          # run check (called by scheduled task)
  python ct_watch_checker.py test     # test email sending (sends a test mail)

Email credentials must be in .env:
  ALERT_EMAIL_FROM=omarearly@gmail.com
  ALERT_EMAIL_TO=omarearly@gmail.com
  ALERT_EMAIL_PASSWORD=xxxx xxxx xxxx xxxx
"""

import sys
import os
import json
import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

BASE_DIR   = Path(__file__).parent
WATCH_FILE = BASE_DIR / 'watch_alerts.json'

# ---------------------------------------------------------------------------
#  Load .env
# ---------------------------------------------------------------------------
def _load_env():
    env_file = BASE_DIR / '.env'
    if env_file.exists():
        for line in env_file.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

_load_env()


# ---------------------------------------------------------------------------
#  Watchlist I/O
# ---------------------------------------------------------------------------
def load_watchlist():
    if WATCH_FILE.exists():
        try:
            return json.loads(WATCH_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'email': '', 'tickers': []}


def save_watchlist(data):
    WATCH_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


# ---------------------------------------------------------------------------
#  Traffic light -- mirrors the logic in ct_html.py
# ---------------------------------------------------------------------------
def get_tl_color(setup: dict) -> str:
    """Use the TrafficLight already set by _finalize_setup() in ct_analysis.
    GREEN  = prob>=70 AND zero red flags
    YELLOW = prob>=65 AND <=1 red flag
    RED    = everything else
    """
    return setup.get('TrafficLight', 'RED')


# ---------------------------------------------------------------------------
#  Analyze one ticker (calls the existing scanner modules)
# ---------------------------------------------------------------------------
_INTL_SUFFIXES = ('.L', '.DE', '.PA', '.T', '.AX', '.TO', '.SW', '.NS', '.HK', '.F')

def analyze_ticker(entry: dict):
    """
    Run ct_analysis._detect_setup() on a single watchlist entry.
    Returns (setup, tl, current_price, reason) where reason is one of:
      None           -> success
      'FETCH_ERROR'  -> market data unavailable
      'NO_LEVEL'     -> price not near any S/R level
    """
    ticker    = entry['ticker']
    direction = entry.get('direction', 'LONG')

    is_crypto    = ticker.endswith('-USD')
    is_israel    = ticker.endswith('.TA')
    is_intl      = any(ticker.endswith(s) for s in _INTL_SUFFIXES)
    is_commodity = ticker.endswith('=F')

    asset_type = (
        'CRYPTO'    if is_crypto    else
        'ISRAEL'    if is_israel    else
        'INTL'      if is_intl      else
        'COMMODITY' if is_commodity else
        'STOCK'
    )

    try:
        from ct_analysis import _fetch_market_data, _detect_setup
        from ct_config   import MAX_DIST_STOCK

        market = _fetch_market_data(
            ticker,
            is_crypto=is_crypto,
            is_commodity=is_commodity,
            is_israel=is_israel,
            is_intl=is_intl,
        )
        if market is None:
            return None, None, None, 'FETCH_ERROR'

        current_price = market.get('price')

        setup = _detect_setup(
            ticker, 100000, market, is_crypto, asset_type,
            MAX_DIST_STOCK, direction,
            is_commodity=is_commodity,
            is_israel=is_israel,
            is_intl=is_intl,
        )
        if setup is None:
            return None, None, current_price, 'NO_LEVEL'

        tl = get_tl_color(setup)
        return setup, tl, current_price, None

    except Exception as e:
        print(f"    ERROR analyzing {ticker}: {e}")
        return None, None, None, 'FETCH_ERROR' 


# ---------------------------------------------------------------------------
#  Email sender
# ---------------------------------------------------------------------------
def send_green_alert(to_email: str, entry: dict, setup: dict) -> bool:
    from_email = os.environ.get('ALERT_EMAIL_FROM', '')
    password   = os.environ.get('ALERT_EMAIL_PASSWORD', '').replace(' ', '')

    if not from_email or not password:
        print("    ERROR: ALERT_EMAIL_FROM or ALERT_EMAIL_PASSWORD missing in .env")
        return False

    ticker    = entry['ticker']
    direction = entry.get('direction', 'LONG')
    note      = entry.get('note', '')
    added     = entry.get('added', '')
    prob      = setup.get('Prob', 0)
    price     = setup.get('Price', 0)
    entry_p   = setup.get('Entry', 0)
    stop      = setup.get('Stop', 0)
    target    = setup.get('Target', 0)
    rr        = setup.get('R:R', 0)
    earn      = setup.get('Earn', '-')
    horizon   = setup.get('HorizonLabel', '-')
    monthly   = setup.get('MonthlyTrend', '-')
    sector_rs = setup.get('SectorRS', '-')
    vol       = setup.get('Vol', '-')

    dir_color = '#22c55e' if direction == 'LONG' else '#ef4444'
    dir_emoji = 'LONG' if direction == 'LONG' else 'SHORT'

    subject = f"[GREEN LIGHT] {ticker} {dir_emoji} -- Prob {prob}% -- Cycles Alert"

    factors_html = ''
    for label, delta, explain in setup.get('_pfacts', []):
        sign  = '+' if delta >= 0 else ''
        color = '#22c55e' if delta > 0 else ('#ef4444' if delta < 0 else '#94a3b8')
        factors_html += (
            f'<tr>'
            f'<td style="padding:4px 8px;color:#94a3b8;font-size:12px">{label}</td>'
            f'<td style="padding:4px 8px;color:{color};font-size:12px;font-weight:700">{sign}{delta}</td>'
            f'<td style="padding:4px 8px;color:#64748b;font-size:11px">{explain[:70]}</td>'
            f'</tr>'
        )

    note_html = (
        f'<p style="background:#1e293b;border-radius:8px;padding:12px;'
        f'color:#94a3b8;font-size:13px"><b>Note:</b> {note}</p>'
    ) if note else ''

    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="background:#0f172a;color:#e2e8f0;font-family:sans-serif;padding:24px;margin:0">

  <div style="max-width:600px;margin:0 auto">

    <!-- Header -->
    <div style="background:#14532d;border-radius:12px;padding:20px;margin-bottom:20px;
                border:2px solid #22c55e">
      <h2 style="margin:0 0 4px 0;color:#22c55e;font-size:22px">
        GREEN LIGHT -- {ticker}
      </h2>
      <span style="background:{dir_color};color:#fff;padding:3px 12px;border-radius:99px;
                   font-size:13px;font-weight:700">{dir_emoji}</span>
      &nbsp;
      <span style="background:#1e40af;color:#bfdbfe;padding:3px 12px;border-radius:99px;
                   font-size:13px;font-weight:700">Prob {prob}%</span>
    </div>

    <!-- Trade parameters -->
    <table style="width:100%;border-collapse:collapse;background:#1e293b;
                  border-radius:12px;overflow:hidden;margin-bottom:16px">
      <tr>
        <td style="padding:10px 16px;color:#64748b;font-size:13px">Current Price</td>
        <td style="padding:10px 16px;color:#e2e8f0;font-size:15px;font-weight:700">${price}</td>
      </tr>
      <tr style="background:#0f172a">
        <td style="padding:10px 16px;color:#64748b;font-size:13px">Entry</td>
        <td style="padding:10px 16px;color:#22c55e;font-size:15px;font-weight:700">${entry_p}</td>
      </tr>
      <tr>
        <td style="padding:10px 16px;color:#64748b;font-size:13px">Stop Loss</td>
        <td style="padding:10px 16px;color:#ef4444;font-size:15px;font-weight:700">${stop}</td>
      </tr>
      <tr style="background:#0f172a">
        <td style="padding:10px 16px;color:#64748b;font-size:13px">Target</td>
        <td style="padding:10px 16px;color:#38bdf8;font-size:15px;font-weight:700">${target}</td>
      </tr>
      <tr>
        <td style="padding:10px 16px;color:#64748b;font-size:13px">Risk:Reward</td>
        <td style="padding:10px 16px;color:#e2e8f0;font-size:15px;font-weight:700">1:{rr}</td>
      </tr>
      <tr style="background:#0f172a">
        <td style="padding:10px 16px;color:#64748b;font-size:13px">Time Horizon</td>
        <td style="padding:10px 16px;color:#e2e8f0;font-size:13px">{horizon}</td>
      </tr>
      <tr>
        <td style="padding:10px 16px;color:#64748b;font-size:13px">Monthly Trend</td>
        <td style="padding:10px 16px;color:#e2e8f0;font-size:13px">{monthly}</td>
      </tr>
      <tr style="background:#0f172a">
        <td style="padding:10px 16px;color:#64748b;font-size:13px">Sector RS</td>
        <td style="padding:10px 16px;color:#e2e8f0;font-size:13px">{sector_rs}</td>
      </tr>
      <tr>
        <td style="padding:10px 16px;color:#64748b;font-size:13px">Volume</td>
        <td style="padding:10px 16px;color:#e2e8f0;font-size:13px">{vol}</td>
      </tr>
      <tr style="background:#0f172a">
        <td style="padding:10px 16px;color:#64748b;font-size:13px">Earnings</td>
        <td style="padding:10px 16px;color:#f59e0b;font-size:13px">{earn}</td>
      </tr>
    </table>

    {note_html}

    <!-- Factor breakdown -->
    <div style="background:#1e293b;border-radius:12px;padding:16px;margin-bottom:16px">
      <h3 style="color:#64748b;font-size:13px;margin:0 0 12px 0">Factor Breakdown</h3>
      <table style="width:100%;border-collapse:collapse">
        {factors_html}
      </table>
    </div>

    <!-- Footer -->
    <p style="color:#334155;font-size:11px;text-align:center">
      Cycles Trading Watch Alert -- Added to watchlist: {added} --
      {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}
    </p>

  </div>
</body>
</html>
"""

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = from_email
        msg['To']      = to_email
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(from_email, password)
            smtp.sendmail(from_email, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"    ERROR sending email: {e}")
        return False


# ---------------------------------------------------------------------------
#  Test email
# ---------------------------------------------------------------------------
def send_test_email():
    # -- Diagnostics --
    env_file = BASE_DIR / '.env'
    print(f"  .env path : {env_file}")
    print(f"  .env exists: {env_file.exists()}")
    if env_file.exists():
        raw = env_file.read_text(encoding='utf-8')
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith('#'): continue
            k = line.split('=', 1)[0].strip()
            print(f"    {k} = {'(set)' if k in line else '(missing)'}")

    from_email = os.environ.get('ALERT_EMAIL_FROM', '')
    to_email   = os.environ.get('ALERT_EMAIL_TO', from_email)
    raw_pw     = os.environ.get('ALERT_EMAIL_PASSWORD', '')
    password   = raw_pw.replace(' ', '')

    print(f"  ALERT_EMAIL_FROM    : {from_email!r}")
    print(f"  ALERT_EMAIL_TO      : {to_email!r}")
    print(f"  ALERT_EMAIL_PASSWORD: {'(set, len=' + str(len(password)) + ')' if password else '(MISSING)'}")

    if not from_email or not password:
        print("  ERROR: set ALERT_EMAIL_FROM and ALERT_EMAIL_PASSWORD in .env")
        return

    subject = "[TEST] Cycles Trading Watch Alert -- email working"
    body    = "<h2 style=\'color:#22c55e\'>Test OK</h2><p>Email alerts are configured correctly.</p>"

    print("  Connecting to smtp.gmail.com:465 ...")
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = from_email
        msg['To']      = to_email
        msg.attach(MIMEText(body, 'html', 'utf-8'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            print("  Connected. Logging in...")
            smtp.login(from_email, password)
            print("  Login OK. Sending...")
            smtp.sendmail(from_email, to_email, msg.as_string())
        print(f"  Test email sent to {to_email}")
    except smtplib.SMTPAuthenticationError as e:
        print(f"  ERROR: Authentication failed -- wrong App Password? {e}")
    except smtplib.SMTPException as e:
        print(f"  ERROR: SMTP error -- {e}")
    except OSError as e:
        print(f"  ERROR: Network/connection error -- {e}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
#  Main check loop
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
#  HTML report for watch checker
# ---------------------------------------------------------------------------
def generate_watch_html(results: list, today: str) -> str:
    """Generate watch-list HTML report with expandable fundamental analysis."""
    import webbrowser
    try:
        from ct_html import _render_fund_box
        _has_fund = True
    except Exception:
        _has_fund = False

    reports_dir = BASE_DIR / 'REPORTS'
    reports_dir.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    out_path = reports_dir / f'watch_report_{ts}.html'

    tl_bg    = {'GREEN': '#d5f5e3', 'YELLOW': '#fef9e7', 'RED': '#fdecea',
                'NO_LEVEL': '#f8f9fa', 'FETCH_ERROR': '#fff3e0',
                'REMOVED': '#f0e6ff', None: '#f8f9fa'}
    tl_icon  = {'GREEN': '🟢',      'YELLOW': '🟡',      'RED': '🔴',
                'NO_LEVEL': '🔘',    'FETCH_ERROR': '⚠️',
                'REMOVED': '🗑',   None: '⚪'}
    tl_label = {'GREEN': 'GO',      'YELLOW': 'WAIT',    'RED': 'NOT YET',
                'NO_LEVEL': 'Not in zone', 'FETCH_ERROR': 'Fetch error',
                'REMOVED': 'Auto-removed', None: 'No Setup'}

    rows_html = ''
    for idx, r in enumerate(sorted(results, key=lambda x: x.get('prob', 0), reverse=True)):
        tl   = r.get('tl')
        bg   = tl_bg.get(tl, '#f8f9fa')
        icon = tl_icon.get(tl, '⚪')
        lbl  = tl_label.get(tl, 'NO DATA')
        prob = r.get('prob', 0)
        earn = r.get('earn', '-') or '-'
        rid  = f'fund_{idx}'

        fund_html = ''
        if _has_fund and r.get('setup'):
            try:
                fund_html = _render_fund_box(r['setup'])
            except Exception:
                pass

        # Simple button — calls named JS function, no quote escaping
        btn = (f'<button onclick="toggleFund(\'{rid}\')" '
               f'style="font-size:11px;padding:2px 8px;border:1px solid #ccc;'
               f'border-radius:4px;background:#fff;cursor:pointer;margin-left:6px"'
               f' title="Toggle fundamental analysis">📊</button>') if fund_html else ''

        fund_row = (f'<tr id="{rid}" style="display:none">'
                    f'<td colspan="11" style="padding:0 14px 12px 14px;background:#1a1f2e">'
                    f'{fund_html}</td></tr>') if fund_html else ''

        # When no setup: show current price in Entry column
        no_setup = tl in ('NO_LEVEL', 'FETCH_ERROR', None)
        entry_val = (f"<span style='color:#888;font-size:12px'>now {r.get('cur_price','-')}</span>"
                     if no_setup else r.get('entry', '-'))

        earn_color = '#c0392b' if ('SOON' in earn or 'APPROACH' in earn) else '#555'
        rows_html += (
            f"<tr style='background:{bg}'>"
            f"<td style='padding:10px 14px;font-weight:bold;font-size:15px'>{r['ticker']}{btn}</td>"
            f"<td style='padding:10px 14px'>{r.get('direction','')}</td>"
            f"<td style='padding:10px 14px;font-size:18px;text-align:center'>{icon}</td>"
            f"<td style='padding:10px 14px;font-weight:bold'>{lbl}</td>"
            f"<td style='padding:10px 14px;font-weight:bold;color:#1a5276'>{prob if not no_setup else ''}</td>"
            f"<td style='padding:10px 14px'>{entry_val}</td>"
            f"<td style='padding:10px 14px'>{r.get('stop','-') if not no_setup else '-'}</td>"
            f"<td style='padding:10px 14px'>{r.get('target','-') if not no_setup else '-'}</td>"
            f"<td style='padding:10px 14px'>{r.get('rr','-') if not no_setup else '-'}</td>"
            f"<td style='padding:10px 14px;color:{earn_color}'>{earn}</td>"
            f"<td style='padding:10px 14px;color:#777;font-size:12px'>{r.get('notes','')}</td>"
            f"</tr>{fund_row}"
        )

    n_go      = sum(1 for r in results if r.get('tl') == 'GREEN')
    n_wait    = sum(1 for r in results if r.get('tl') == 'YELLOW')
    n_zone    = sum(1 for r in results if r.get('tl') == 'NO_LEVEL')
    n_removed = sum(1 for r in results if r.get('tl') == 'REMOVED')
    n_no      = sum(1 for r in results if r.get('tl') not in
                    ('GREEN', 'YELLOW', 'NO_LEVEL', 'FETCH_ERROR', 'REMOVED'))

    table_html = (
        '<table><thead><tr>'
        '<th>Ticker</th><th>Dir</th><th></th><th>Status</th><th>Prob</th>'
        '<th>Entry</th><th>Stop</th><th>Target</th><th>R:R</th><th>Earnings</th><th>Notes</th>'
        '</tr></thead><tbody>' + rows_html + '</tbody></table>'
    ) if results else '<div class="empty">Watchlist is empty</div>'

    html = f"""<!DOCTYPE html>
<html lang="he">
<head>
<meta charset="UTF-8">
<title>Watch Report {today}</title>
<style>
  body {{font-family:Arial,sans-serif;background:#f0f3f7;margin:0;padding:20px;color:#222}}
  .header {{background:linear-gradient(135deg,#1a5276,#2980b9);color:#fff;
            padding:24px 32px;border-radius:12px;margin-bottom:24px}}
  h1 {{margin:0 0 8px;font-size:24px}}
  .pills {{display:flex;gap:12px;flex-wrap:wrap;margin-top:10px}}
  .pill {{background:rgba(255,255,255,.2);border-radius:20px;padding:5px 16px;
          font-size:14px;font-weight:bold}}
  table {{width:100%;border-collapse:collapse;background:#fff;
          border-radius:10px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)}}
  thead tr {{background:#1a5276;color:#fff}}
  th {{padding:10px 14px;text-align:left;font-size:13px;white-space:nowrap}}
  tr:hover > td {{filter:brightness(.97)}}
  .empty {{text-align:center;padding:40px;color:#999;font-size:16px}}
  /* fund-box classes (dark theme, sits on #1a1f2e background) */
  .fund-box {{border-radius:8px;padding:12px 16px;margin:0}}
  .fund-header {{display:flex;justify-content:space-between;align-items:center;
                 margin-bottom:10px;flex-wrap:wrap;gap:6px}}
  .fund-title  {{font-size:12px;font-weight:700;color:#8b949e;text-transform:uppercase}}
  .fund-signal {{font-size:12px;font-weight:800;padding:3px 12px;
                 border-radius:12px;background:transparent}}
  .fund-bullets,.fund-caveats {{list-style:none;padding:0;margin:4px 0 0 0;
                                 font-size:12px;line-height:1.8;color:#8b949e}}
  .fund-caveats li {{color:#d29922}}
  .fm-sections  {{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0 4px 0}}
  .fm-section   {{flex:1 1 140px;min-width:110px}}
  .fm-section-title {{font-size:10px;font-weight:700;color:#6e7681;
                       text-transform:uppercase;margin-bottom:4px;letter-spacing:.5px}}
  .fm-grid      {{display:flex;flex-direction:column;gap:2px}}
  .fm-cell      {{display:flex;justify-content:space-between;align-items:center;
                   font-size:11px;padding:1px 0;border-bottom:1px solid #ffffff18}}
  .fm-lbl       {{color:#8b949e;white-space:nowrap;margin-right:6px}}
  .fm-val       {{font-weight:700;font-size:11px;text-align:right}}
</style>
<script>
function toggleFund(id) {{
  var el = document.getElementById(id);
  el.style.display = (el.style.display === 'none') ? 'table-row' : 'none';
}}
</script>
</head>
<body>
<div class="header">
  <h1>👁 Cycles Watch Report — {today}</h1>
  <div class="pills">
    <span class="pill">🟢 GO: {n_go}</span>
    <span class="pill">🟡 WAIT: {n_wait}</span>
    <span class="pill">🔴 NOT YET: {n_no}</span>
    <span class="pill">🔘 Not in zone: {n_zone}</span>
    <span class="pill">🗑 Removed: {n_removed}</span>
    <span class="pill">📋 Total: {len(results)}</span>
  </div>
</div>
{table_html}
</body></html>"""

    out_path.write_text(html, encoding='utf-8')
    print(f"  HTML report: {out_path}")
    try:
        webbrowser.open('file:///' + str(out_path).replace('\\', '/'))
    except Exception:
        pass
    return str(out_path)


def run_check():
    data    = load_watchlist()
    tickers = data.get('tickers', [])
    email   = data.get('email', os.environ.get('ALERT_EMAIL_TO', ''))
    today   = datetime.date.today().isoformat()

    print(f"\n  Cycles Watch Checker -- {today}")
    print(f"  {len(tickers)} tickers in watchlist")
    print("  " + "=" * 55)

    if not tickers:
        print("  Watchlist is empty. Add tickers with ct_watch_manager.py")
        return

    if not email:
        print("  ERROR: no email configured in watch_alerts.json or .env")
        return

    alerts_sent = 0
    html_results = []
    for entry in tickers:
        ticker     = entry['ticker']
        direction  = entry.get('direction', 'LONG')
        last_alert = entry.get('last_alerted') or ''

        print(f"  {ticker} ({direction})", end='  ')

        setup, tl, cur_price, reason = analyze_ticker(entry)

        if setup is None:
            reason_label = 'Fetch error' if reason == 'FETCH_ERROR' else 'Not in zone'
            price_str = f'${cur_price:.2f}' if cur_price else '-'
            print(f"-- {reason_label}  (price={price_str})")
            entry['status']       = reason or 'NO_DATA'
            entry['last_checked'] = today
            # Track how long the stock has been out of zone
            if reason == 'NO_LEVEL':
                entry.setdefault('not_in_zone_since', today)
            elif reason != 'FETCH_ERROR':
                entry.pop('not_in_zone_since', None)
            html_results.append({'ticker': ticker, 'direction': direction,
                                  'prob': 0, 'tl': reason, 'cur_price': price_str,
                                  'notes': reason_label,
                                  'not_in_zone_since': entry.get('not_in_zone_since')})
            continue

        prob = setup.get('Prob', 0)
        print(f"Prob={prob}%  TL={tl}")
        # Persist status to watch_alerts.json
        entry.pop('not_in_zone_since', None)   # back in zone — reset counter
        entry['status']       = tl
        entry['prob']         = prob
        entry['entry_price']  = setup.get('Entry')
        entry['stop_price']   = setup.get('Stop')
        entry['target_price'] = setup.get('Target')
        entry['rr']           = setup.get('R:R')
        entry['last_checked'] = today
        html_results.append({
            'ticker':    ticker,
            'direction': direction,
            'prob':      prob,
            'tl':        tl,
            'entry':     setup.get('Entry', '-'),
            'stop':      setup.get('Stop', '-'),
            'target':    setup.get('Target', '-'),
            'rr':        setup.get('R:R', '-'),
            'earn':      setup.get('Earn', '-'),
            'notes':     '',
            'setup':     setup,   # full dict — used by generate_watch_html for _render_fund_box
        })

        # Already alerted today -- skip email only
        if last_alert == today or tl != 'GREEN':
            continue

        # GREEN -- send alert
        print(f"    Sending GREEN LIGHT alert to {email}...")
        sent = send_green_alert(email, entry, setup)
        if sent:
            entry['last_alerted'] = today
            entry['alert_count']  = entry.get('alert_count', 0) + 1
            alerts_sent += 1
            print(f"    Email sent OK")

    # Auto-prune: remove tickers that have been "Not in zone" for 21+ days
    NOT_IN_ZONE_DAYS = 21
    before = len(data['tickers'])
    pruned = []
    kept   = []
    for t in data['tickers']:
        niz = t.get('not_in_zone_since')
        if niz:
            try:
                days_out = (datetime.date.today() - datetime.date.fromisoformat(niz)).days
                if days_out >= NOT_IN_ZONE_DAYS:
                    pruned.append((t['ticker'], days_out))
                    continue
            except Exception:
                pass
        kept.append(t)
    data['tickers'] = kept
    if pruned:
        print(f"\n  Auto-pruned {len(pruned)} stale ticker(s) (>{NOT_IN_ZONE_DAYS}d out of zone):")
        for sym, days in pruned:
            print(f"    REMOVED {sym} — {days} days not in zone")
        # Mark removed in html_results so report shows them as removed
        removed_syms = {s for s, _ in pruned}
        for r in html_results:
            if r['ticker'] in removed_syms:
                r['notes'] = r.get('notes','') + f'  [auto-removed: {NOT_IN_ZONE_DAYS}d out of zone]'
                r['tl'] = 'REMOVED'

    # Save updated last_alerted values
    save_watchlist(data)
    generate_watch_html(html_results, today)
    print(f"\n  Done. {alerts_sent} green light alert(s) sent.")
    print("  " + "=" * 55 + "\n")


# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        send_test_email()
    else:
        run_check()
