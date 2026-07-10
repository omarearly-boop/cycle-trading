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
    tmp = WATCH_FILE.with_suffix('.tmp')
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    import os
    os.replace(tmp, WATCH_FILE)


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

# Zone tolerance: how far from entry_price the current price can be
# before we skip the full analysis (saves 5-15s per ticker).
_ZONE_PCT = 0.12   # 12% — if price is more than 12% away from entry, skip

def _quick_price_check(entry: dict):
    """
    Fetch only the current price (fast_info, ~200ms) and compare to the
    stored entry_price / stop_price to decide if a full analysis is worth running.

    Returns (current_price, in_zone):
      in_zone=True  -> price is near the entry zone, run full analysis
      in_zone=False -> price is far from zone, skip heavy fetch
      in_zone=True  -> also returned when entry_price is missing (safe default)
    """
    ticker      = entry['ticker']
    entry_price = entry.get('entry_price')
    stop_price  = entry.get('stop_price')
    direction   = entry.get('direction', 'LONG')

    if not entry_price:
        return None, True   # no stored level → always run full analysis

    try:
        import yfinance as _yf
        from ct_market_data import _yf_throttle
        _yf_throttle()   # respect the global Yahoo request spacing
        fi    = _yf.Ticker(ticker).fast_info
        price = fi.get('lastPrice') or fi.get('regularMarketPrice')
        if not price:
            return None, True   # can't get price → run full to be safe

        price = float(price)

        if direction == 'LONG':
            # In zone: price is between stop and entry+buffer (waiting for pullback or just touched)
            lower = float(stop_price) * 0.90 if stop_price else entry_price * 0.85
            upper = entry_price * (1 + _ZONE_PCT)
        else:   # SHORT
            # In zone: price is between entry-buffer and stop (waiting for rally back to level)
            upper = float(stop_price) * 1.10 if stop_price else entry_price * 1.15
            lower = entry_price * (1 - _ZONE_PCT)

        in_zone = lower <= price <= upper
        return price, in_zone

    except Exception:
        return None, True   # error → run full analysis


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
        from ct_config   import (MAX_DIST_STOCK, MAX_DIST_CRYPTO,
                                 MAX_DIST_COMMODITY, MAX_DIST_INTL)

        # Use the same per-asset distance the scanner uses (was always 12%,
        # which rejected crypto setups the scanner accepted at 20%)
        max_dist = (MAX_DIST_CRYPTO    if is_crypto    else
                    MAX_DIST_COMMODITY if is_commodity else
                    MAX_DIST_INTL      if (is_israel or is_intl) else
                    MAX_DIST_STOCK)

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
            max_dist, direction,
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
    gann_tgt  = setup.get('GannTarget', 0)
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
      <tr style="background:#0f172a">
        <td style="padding:10px 16px;color:#64748b;font-size:13px">Gann 100%</td>
        <td style="padding:10px 16px;color:#a855f7;font-size:13px;font-weight:700">${gann_tgt if gann_tgt else '-'}</td>
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
        tf = r.get('timeframe', 'WEEKLY')
        tf_badge = ('<span style="background:#1e3a5f;color:#38bdf8;font-size:9px;font-weight:700;'
                    'padding:1px 5px;border-radius:4px;margin-left:5px;vertical-align:middle">MO</span>'
                    if tf == 'MONTHLY' else
                    '<span style="background:#1a3320;color:#22c55e;font-size:9px;font-weight:700;'
                    'padding:1px 5px;border-radius:4px;margin-left:5px;vertical-align:middle">W</span>')
        rows_html += (
            f"<tr data-status='{tl}' style='background:{bg}'>"
            f"<td style='padding:10px 14px;font-weight:bold;font-size:15px'>{r['ticker']}{tf_badge}{btn}</td>"
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
  .filter-bar {{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 0}}
  .flt-btn {{border:2px solid transparent;border-radius:20px;padding:5px 16px;font-size:13px;font-weight:700;cursor:pointer;background:rgba(255,255,255,.15);color:#fff;transition:background .15s,border-color .15s}}
  .flt-btn:hover{{background:rgba(255,255,255,.25)}}
  .flt-btn.active{{background:rgba(255,255,255,.35);border-color:#fff}}
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
var _activeFilter = 'ALL';
function filterStatus(status, btn) {{
  _activeFilter = status;
  document.querySelectorAll('.flt-btn').forEach(function(b){{ b.classList.remove('active'); }});
  btn.classList.add('active');
  document.querySelectorAll('tbody tr[data-status]').forEach(function(row){{
    var s = row.getAttribute('data-status');
    var show = (status === 'ALL' || s === status);
    row.style.display = show ? '' : 'none';
    var next = row.nextElementSibling;
    if(next && !next.hasAttribute('data-status')){{
      next.style.display = 'none';
    }}
  }});
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
  <div class="filter-bar">
    <button class="flt-btn active" onclick="filterStatus('ALL',this)">❖ All</button>
    <button class="flt-btn" onclick="filterStatus('GREEN',this)">🟢 GO</button>
    <button class="flt-btn" onclick="filterStatus('YELLOW',this)">🟡 Wait</button>
    <button class="flt-btn" onclick="filterStatus('RED',this)">🔴 Not Yet</button>
    <button class="flt-btn" onclick="filterStatus('NO_LEVEL',this)">⚪ Not in Zone</button>
    <button class="flt-btn" onclick="filterStatus('REMOVED',this)">🗑 Removed</button>
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



# ---------------------------------------------------------------------------
#  Momentum GO signal — fires when SPY crosses +2% weekly gain
# ---------------------------------------------------------------------------

def _spy_weekly_gain_live() -> float:
    """SPY % gain from last Friday close to now."""
    try:
        import yfinance as yf
        from ct_market_data import yf_history
        spy = yf_history(yf.Ticker('SPY'), period='5d', interval='1d',
                         auto_adjust=True, raise_errors=False)
        if spy is None:
            return 0.0
        closes = spy['Close'].squeeze().dropna()
        if len(closes) < 2:
            return 0.0
        # last Friday = first bar if today is Mon–Fri, else second-to-last
        return float((closes.iloc[-1] / closes.iloc[0] - 1) * 100)
    except Exception:
        return 0.0


def _enrich_candidates_with_live_prices(candidates: list) -> list:
    """
    Re-fetch the current market price for each candidate and compare
    to the Entry price saved at scan time (Sunday).

    Adds three fields to each candidate dict:
      current_price  — live price right now
      drift_pct      — (current - scan_entry) / scan_entry * 100
      chase_status   — one of 'VALID', 'CHASING', 'DROPPED'
      status_color   — hex colour for the email badge
    """
    try:
        import yfinance as yf
    except Exception:
        # yfinance unavailable — return candidates untouched
        for c in candidates:
            c.setdefault('current_price', c.get('Entry', 0))
            c.setdefault('drift_pct', 0.0)
            c.setdefault('chase_status', 'UNKNOWN')
            c.setdefault('status_color', '#94a3b8')
        return candidates

    enriched = []
    for c in candidates:
        ticker     = c.get('Ticker', '')
        scan_entry = float(c.get('Entry', 0) or 0)
        current_price = scan_entry   # safe fallback

        try:
            from ct_market_data import yf_history
            asset = yf.Ticker(ticker)
            hist  = yf_history(asset, period='2d', interval='1h', auto_adjust=True)
            if hist is not None and not hist.empty:
                current_price = float(hist['Close'].iloc[-1])
            else:
                fi = getattr(asset, 'fast_info', {})
                lp = getattr(fi, 'last_price', None) or fi.get('lastPrice')
                if lp:
                    current_price = float(lp)
        except Exception:
            pass   # keep fallback

        drift_pct = ((current_price - scan_entry) / scan_entry * 100) if scan_entry else 0.0

        if drift_pct > 5.0:
            chase_status = f'⚠ CHASING (+{drift_pct:.1f}%)'
            status_color = '#f59e0b'   # amber — do not enter
        elif drift_pct < -5.0:
            chase_status = f'⚠ DROPPED ({drift_pct:.1f}%)'
            status_color = '#ef4444'   # red — level may have broken
        else:
            chase_status = f'✓ VALID ({drift_pct:+.1f}%)'
            status_color = '#22c55e'   # green — still a clean entry

        enriched.append({
            **c,
            'current_price': round(current_price, 2),
            'drift_pct':     round(drift_pct, 1),
            'chase_status':  chase_status,
            'status_color':  status_color,
        })

    return enriched


def _count_open_positions() -> int:
    """Return number of currently OPEN positions from positions.json."""
    try:
        import json as _j
        pos_file = BASE_DIR / 'positions.json'
        if not pos_file.exists():
            return 0
        data = _j.loads(pos_file.read_text(encoding='utf-8'))
        # positions.json uses 'closed': bool (see ct_positions.pm_add) — there
        # is no 'status' key; the old check made the banner always read 0 open
        return sum(1 for p in data.get('positions', []) if not p.get('closed'))
    except Exception:
        return 0


def check_momentum_go(email: str):
    """
    Called every time the watch checker runs.
    If SPY weekly gain > 2% AND momentum candidates exist AND
    we haven't already sent a GO alert today → send the email.
    """
    import json as _json, os as _os
    cand_file = BASE_DIR / 'momentum_candidates.json'
    if not cand_file.exists():
        print("  Momentum GO: no candidates file yet (run Sunday scan first)")
        return

    try:
        data = _json.loads(cand_file.read_text(encoding='utf-8'))
    except Exception:
        return

    candidates = data.get('candidates', [])
    if not candidates:
        print("  Momentum GO: no candidates from last Sunday")
        return

    today = datetime.date.today().isoformat()
    if data.get('last_go_alert') == today:
        print("  Momentum GO: already alerted today")
        return

    spy_pct = _spy_weekly_gain_live()
    print(f"  Momentum GO check: SPY weekly gain = {spy_pct:+.1f}%  (need >2%)")

    if spy_pct < 2.0:
        return

    # ── Fix 1: Re-fetch live prices — compare to Sunday scan entry ──────────
    scan_date = data.get('scan_date', 'last Sunday')
    days_since = 0
    try:
        days_since = (datetime.date.today() - datetime.date.fromisoformat(scan_date)).days
    except Exception:
        pass
    print(f"  Re-fetching live prices ({days_since}d since Sunday scan)...")
    candidates = _enrich_candidates_with_live_prices(candidates)

    n_valid   = sum(1 for c in candidates if c.get('drift_pct', 0) <= 5.0)
    n_chasing = sum(1 for c in candidates if c.get('drift_pct', 0) > 5.0)
    print(f"  {n_valid} valid  |  {n_chasing} chasing (>5% above scan price)")

    # ── Fix 3: Check open position count ────────────────────────────────────
    try:
        from ct_config import MAX_OPEN_POSITIONS
        max_pos = MAX_OPEN_POSITIONS
    except Exception:
        max_pos = 6
    open_pos  = _count_open_positions()
    remaining = max_pos - open_pos
    if remaining <= 0:
        pos_banner_color  = '#7f1d1d'
        pos_banner_border = '#ef4444'
        pos_banner_text   = (f'🚫 AT POSITION LIMIT ({open_pos}/{max_pos} open) — '
                             f'close a position before entering new trades.')
    elif remaining == 1:
        pos_banner_color  = '#422006'
        pos_banner_border = '#f59e0b'
        pos_banner_text   = (f'⚠ Almost full ({open_pos}/{max_pos} open) — '
                             f'room for 1 more trade. Be selective.')
    else:
        pos_banner_color  = '#052e16'
        pos_banner_border = '#22c55e'
        pos_banner_text   = (f'✓ {open_pos}/{max_pos} positions open — '
                             f'room for {remaining} more trades.')

    # ── Build email ──────────────────────────────────────────────────────────
    EMAIL_FROM = os.environ.get('ALERT_EMAIL_FROM', '')
    EMAIL_PWD  = os.environ.get('ALERT_EMAIL_PASSWORD', '')
    if not EMAIL_FROM or not EMAIL_PWD:
        print("  Momentum GO: no email credentials")
        return

    rows = ''
    for c in candidates:
        sc = c.get('status_color', '#94a3b8')
        drift_pct   = c.get('drift_pct', 0)
        scan_entry  = c.get('Entry', '-')
        live_price  = c.get('current_price', '-')
        chase_label = c.get('chase_status', '-')
        # Dim chasing rows slightly
        row_opacity = 'opacity:0.55;' if drift_pct > 5.0 else ''
        rows += (
            f"<tr style='{row_opacity}'>"
            f"<td style='padding:8px 12px;font-weight:700;font-size:15px'>{c.get('Ticker','')}</td>"
            f"<td style='padding:8px 12px;color:#94a3b8'>${scan_entry}</td>"
            f"<td style='padding:8px 12px;font-weight:700'>${live_price}</td>"
            f"<td style='padding:8px 12px;font-weight:700;color:{sc}'>{chase_label}</td>"
            f"<td style='padding:8px 12px;color:#f59e0b;font-weight:700'>{c.get('RSI','')}</td>"
            f"<td style='padding:8px 12px;color:#ef4444'>${c.get('Stop','')}</td>"
            f"<td style='padding:8px 12px;color:#38bdf8'>${c.get('Target','')}</td>"
            f"<td style='padding:8px 12px'>{c.get('R:R','')}</td>"
            f"<td style='padding:8px 12px'>{c.get('Pos$','')}</td>"
            f"<td style='padding:8px 12px;color:#6e7681;font-size:12px'>{c.get('Earn','')}</td>"
            f"</tr>"
        )

    chasing_note = (
        f'<p style="background:#422006;border:1px solid #f59e0b;border-radius:6px;'
        f'padding:10px 14px;color:#fde68a;font-size:12px;margin:12px 0">'
        f'⚠ <b>{n_chasing} candidate(s) are marked CHASING</b> — price moved >5% above Sunday entry. '
        f'These are dimmed and should NOT be entered at current prices. '
        f'Wait for a pullback or skip.</p>'
    ) if n_chasing > 0 else ''

    body = f"""
<html>
<body style="font-family:Arial,sans-serif;background:#0f172a;color:#e2e8f0;padding:24px;margin:0">
  <h2 style="color:#f59e0b;margin:0 0 4px">&#9889; Momentum GO Signal</h2>
  <p style="color:#94a3b8;margin:0 0 16px;font-size:13px">
    SPY is up <b style="color:#22c55e">{spy_pct:+.1f}%</b> this week &mdash;
    momentum condition met &mdash; {today}
  </p>

  <!-- Position capacity banner -->
  <div style="background:{pos_banner_color};border:1px solid {pos_banner_border};
              border-radius:8px;padding:10px 16px;margin-bottom:16px;font-size:13px;
              font-weight:700;color:#e2e8f0">
    {pos_banner_text}
  </div>

  {chasing_note}

  <p style="color:#94a3b8;font-size:12px;margin:0 0 12px">
    Candidates from scan on <b>{scan_date}</b>
    ({days_since} day(s) ago — live prices fetched now):
  </p>

  <table style="border-collapse:collapse;background:#1e293b;border-radius:8px;
                overflow:hidden;min-width:680px">
    <thead>
      <tr style="background:#0f172a">
        <th style="padding:8px 12px;text-align:left;color:#6e7681;font-size:11px">Ticker</th>
        <th style="padding:8px 12px;text-align:left;color:#6e7681;font-size:11px">Scan Entry</th>
        <th style="padding:8px 12px;text-align:left;color:#6e7681;font-size:11px">Live Price</th>
        <th style="padding:8px 12px;text-align:left;color:#6e7681;font-size:11px">Status</th>
        <th style="padding:8px 12px;text-align:left;color:#6e7681;font-size:11px">RSI</th>
        <th style="padding:8px 12px;text-align:left;color:#6e7681;font-size:11px">Stop</th>
        <th style="padding:8px 12px;text-align:left;color:#6e7681;font-size:11px">Target</th>
        <th style="padding:8px 12px;text-align:left;color:#6e7681;font-size:11px">R:R</th>
        <th style="padding:8px 12px;text-align:left;color:#6e7681;font-size:11px">Position</th>
        <th style="padding:8px 12px;text-align:left;color:#6e7681;font-size:11px">Earnings</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>

  <p style="color:#475569;font-size:11px;margin-top:16px">
    ✓ VALID = live price within 5% of scan entry — safe to enter.<br>
    ⚠ CHASING = moved >5% since scan — wait for pullback or skip.<br>
    Stop = 20-week MA. Risk = 1% of portfolio per trade.
  </p>
</body>
</html>"""

    n_label = f"{n_valid} valid" + (f" + {n_chasing} chasing" if n_chasing else "")
    subject = f"[MOMENTUM GO] SPY {spy_pct:+.1f}% — {n_label} — {today}"
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    msg = MIMEMultipart()
    msg['From']    = EMAIL_FROM
    msg['To']      = email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html', 'utf-8'))
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30) as s:
            s.login(EMAIL_FROM, EMAIL_PWD)
            s.sendmail(EMAIL_FROM, email, msg.as_string())
        print(f"  Momentum GO email sent → {email}")
        # Mark alerted today (atomic write)
        data['last_go_alert'] = today
        tmp = cand_file.with_suffix('.tmp')
        tmp.write_text(_json.dumps(data, indent=2), encoding='utf-8')
        _os.replace(tmp, cand_file)
    except Exception as e:
        print(f"  Momentum GO email FAILED: {e}")


# ---------------------------------------------------------------------------
#  Trade Management Alerts
# ---------------------------------------------------------------------------
def send_partial_exit_alert(to_email: str, entry: dict, cur_price: float,
                             pct_toward_target: float) -> bool:
    """Send PARTIAL EXIT signal when price reaches 90%+ toward target on low vol."""
    from_email = os.environ.get('ALERT_EMAIL_FROM', '')
    password   = os.environ.get('ALERT_EMAIL_PASSWORD', '').replace(' ', '')
    if not from_email or not password:
        return False

    ticker    = entry['ticker']
    direction = entry.get('direction', 'LONG')
    entry_p   = entry.get('entry_price') or 0
    stop      = entry.get('stop_price')  or 0
    target    = entry.get('target_price') or 0
    rr        = entry.get('rr') or 0

    subject = f"[PARTIAL EXIT] {ticker} {direction} -- {pct_toward_target:.0f}% to target"
    dir_color = '#22c55e' if direction == 'LONG' else '#ef4444'

    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="background:#0f172a;color:#e2e8f0;font-family:sans-serif;padding:24px;margin:0">
<div style="max-width:600px;margin:0 auto">
  <div style="background:#1e293b;border-radius:12px;padding:24px;border-left:4px solid #f59e0b">
    <div style="color:#f59e0b;font-size:13px;font-weight:700;letter-spacing:2px;margin-bottom:8px">
      PARTIAL EXIT SIGNAL
    </div>
    <div style="font-size:28px;font-weight:800;color:#f1f5f9">{ticker}</div>
    <div style="color:{dir_color};font-size:14px;font-weight:700;margin-top:4px">{direction}</div>
  </div>
  <div style="background:#1e293b;border-radius:12px;padding:20px;margin-top:12px">
    <p style="color:#94a3b8;margin:0 0 12px 0;font-size:14px">
      Price has reached <b style="color:#f59e0b">{pct_toward_target:.0f}%</b> of the way to target
      on below-average volume. Consider taking a partial profit (1/3 to 1/2 of position).
    </p>
    <table style="width:100%;border-collapse:collapse">
      <tr>
        <td style="padding:6px 0;color:#64748b;font-size:13px">Entry</td>
        <td style="padding:6px 0;color:#e2e8f0;font-size:13px;text-align:right">${entry_p:.2f}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#64748b;font-size:13px">Current Price</td>
        <td style="padding:6px 0;color:#f59e0b;font-size:15px;font-weight:700;text-align:right">${cur_price:.2f}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#64748b;font-size:13px">Target</td>
        <td style="padding:6px 0;color:#22c55e;font-size:13px;text-align:right">${target:.2f}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#64748b;font-size:13px">Stop</td>
        <td style="padding:6px 0;color:#ef4444;font-size:13px;text-align:right">${stop:.2f}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#64748b;font-size:13px">R:R</td>
        <td style="padding:6px 0;color:#e2e8f0;font-size:13px;text-align:right">{rr}</td>
      </tr>
    </table>
  </div>
  <p style="color:#475569;font-size:11px;text-align:center;margin-top:16px">
    Cycles Trading -- automated signal -- not financial advice
  </p>
</div>
</body>
</html>"""

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = from_email
        msg['To']      = to_email
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as srv:
            srv.login(from_email, password)
            srv.sendmail(from_email, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"    Partial-exit email FAILED: {e}")
        return False


def send_trail_stop_alert(to_email: str, entry: dict, new_stop: float) -> bool:
    """Send TRAIL STOP UPDATE when a new swing low/high allows tightening the stop."""
    from_email = os.environ.get('ALERT_EMAIL_FROM', '')
    password   = os.environ.get('ALERT_EMAIL_PASSWORD', '').replace(' ', '')
    if not from_email or not password:
        return False

    ticker    = entry['ticker']
    direction = entry.get('direction', 'LONG')
    old_stop  = entry.get('stop_price') or entry.get('trail_stop') or 0
    entry_p   = entry.get('entry_price') or 0
    target    = entry.get('target_price') or 0

    subject = f"[TRAIL STOP] {ticker} {direction} -- Move stop to ${new_stop:.2f}"
    dir_color = '#22c55e' if direction == 'LONG' else '#ef4444'
    change    = new_stop - old_stop if direction == 'LONG' else old_stop - new_stop

    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="background:#0f172a;color:#e2e8f0;font-family:sans-serif;padding:24px;margin:0">
<div style="max-width:600px;margin:0 auto">
  <div style="background:#1e293b;border-radius:12px;padding:24px;border-left:4px solid #6366f1">
    <div style="color:#6366f1;font-size:13px;font-weight:700;letter-spacing:2px;margin-bottom:8px">
      TRAILING STOP UPDATE
    </div>
    <div style="font-size:28px;font-weight:800;color:#f1f5f9">{ticker}</div>
    <div style="color:{dir_color};font-size:14px;font-weight:700;margin-top:4px">{direction}</div>
  </div>
  <div style="background:#1e293b;border-radius:12px;padding:20px;margin-top:12px">
    <p style="color:#94a3b8;margin:0 0 12px 0;font-size:14px">
      A new swing {'low' if direction=='LONG' else 'high'} has formed above your current stop.
      Move your stop to lock in more profit (improvement: <b style="color:#6366f1">+${change:.2f}</b>).
    </p>
    <table style="width:100%;border-collapse:collapse">
      <tr>
        <td style="padding:6px 0;color:#64748b;font-size:13px">Entry</td>
        <td style="padding:6px 0;color:#e2e8f0;font-size:13px;text-align:right">${entry_p:.2f}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#64748b;font-size:13px">Old Stop</td>
        <td style="padding:6px 0;color:#ef4444;font-size:13px;text-align:right">${old_stop:.2f}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#6366f1;font-size:15px;font-weight:700">NEW STOP</td>
        <td style="padding:6px 0;color:#6366f1;font-size:15px;font-weight:700;text-align:right">${new_stop:.2f}</td>
      </tr>
      <tr>
        <td style="padding:6px 0;color:#64748b;font-size:13px">Target</td>
        <td style="padding:6px 0;color:#22c55e;font-size:13px;text-align:right">${target:.2f}</td>
      </tr>
    </table>
  </div>
  <p style="color:#475569;font-size:11px;text-align:center;margin-top:16px">
    Cycles Trading -- automated signal -- not financial advice
  </p>
</div>
</body>
</html>"""

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = from_email
        msg['To']      = to_email
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as srv:
            srv.login(from_email, password)
            srv.sendmail(from_email, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"    Trail-stop email FAILED: {e}")
        return False



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

        # ── Quick pre-filter: skip full analysis if price is far from zone ──
        quick_price, in_zone = _quick_price_check(entry)
        if not in_zone:
            price_str = f'${quick_price:.2f}' if quick_price else '-'
            print(f"-- Not in zone (pre-filter)  (price={price_str})")
            entry['status']       = 'NO_LEVEL'
            entry['last_checked'] = today
            entry.setdefault('not_in_zone_since', today)
            html_results.append({'ticker': ticker, 'direction': direction,
                                  'prob': 0, 'tl': 'NO_LEVEL', 'cur_price': price_str,
                                  'notes': 'Not in zone',
                                  'timeframe': entry.get('timeframe', 'WEEKLY'),
                                  'not_in_zone_since': entry.get('not_in_zone_since')})
            continue

        setup, tl, cur_price, reason = analyze_ticker(entry)

        if setup is None:
            reason_label = 'Fetch error' if reason == 'FETCH_ERROR' else 'Not in zone'
            price_str = f'${cur_price:.2f}' if cur_price else '-'
            print(f"-- {reason_label}  (price={price_str})")
            entry['status']       = reason or 'NO_DATA'
            entry['last_checked'] = today
            if reason == 'NO_LEVEL':
                entry.setdefault('not_in_zone_since', today)
            elif reason != 'FETCH_ERROR':
                entry.pop('not_in_zone_since', None)
            html_results.append({'ticker': ticker, 'direction': direction,
                                  'prob': 0, 'tl': reason, 'cur_price': price_str,
                                  'notes': reason_label,
                                  'timeframe': entry.get('timeframe', 'WEEKLY'),
                                  'not_in_zone_since': entry.get('not_in_zone_since')})
            continue

        prob = setup.get('Prob', 0)
        print(f"Prob={prob}%  TL={tl}")
        entry.pop('not_in_zone_since', None)
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
            'timeframe': entry.get('timeframe', 'WEEKLY'),
            'entry':     setup.get('Entry', '-'),
            'stop':      setup.get('Stop', '-'),
            'target':    setup.get('Target', '-'),
            'rr':        setup.get('R:R', '-'),
            'earn':      setup.get('Earn', '-'),
            'notes':     '',
            'setup':     setup,
        })

        if last_alert == today or tl != 'GREEN':
            continue

        print(f"    Sending GREEN LIGHT alert to {email}...")
        sent = send_green_alert(email, entry, setup)
        if sent:
            entry['last_alerted'] = today
            entry['alert_count']  = entry.get('alert_count', 0) + 1
            alerts_sent += 1
            print(f"    Email sent OK")


    # -- Phase 4: Trade Management (Partial Exit + Trail Stop) ----------------
    # Manages ONLY real recorded positions (positions.json, registered with
    # `python cycles_trading_scanner.py add ...`) — NOT watchlist tickers.
    # The old behaviour assumed every GREEN-alerted watchlist ticker was an
    # open trade and even overwrote the watchlist's stored stop (the FICO
    # email incident): alerts fired for trades that were never entered.
    print()
    print("  Phase 4: trade management checks (recorded open positions)...")
    try:
        import yfinance as _yf
        from ct_indicators import swing_lows as _swing_lows, swing_highs as _swing_highs
        from ct_positions import _pm_load as _load_positions, _pm_save as _save_positions
        from ct_config import PM_STOP_BUFFER as _pm_buf
    except Exception as _e:
        print(f"    Skipping Phase 4 (import error: {_e})")
        _yf = None

    if _yf is not None:
        _positions = _load_positions()
        _open_pos  = [p for p in _positions if not p.get('closed')]
        if not _open_pos:
            print("    No open positions recorded — nothing to manage.")
            print("    Register a real trade with:")
            print("    python cycles_trading_scanner.py add TICKER DIR ENTRY STOP TP1 TP2 TP3 UNITS")
        _pos_changed = False
        for _pos in _open_pos:
            _ticker    = _pos['ticker']
            _direction = _pos.get('direction', 'LONG')
            _is_long   = 'LONG' in _direction
            _entry_p   = _pos.get('entry') or 0
            _stop_p    = _pos.get('stop') or 0
            _target_p  = _pos.get('tp1') or 0

            if _entry_p <= 0 or _stop_p <= 0 or _target_p <= 0:
                continue

            try:
                from ct_market_data import yf_history as _yfh
                _hist = _yfh(_yf.Ticker(_ticker), period='6mo', interval='1wk')
                if _hist is None or _hist.empty or len(_hist) < 6:
                    continue
                _close_s = _hist['Close']
                _vol_s   = _hist['Volume']
                _cur_p   = float(_close_s.iloc[-1])
            except Exception:
                continue

            # --- Partial Exit check (course step 8: near T1 on fading volume) ---
            if _is_long:
                _dist_total = _target_p - _entry_p
                _dist_done  = _cur_p - _entry_p
            else:
                _dist_total = _entry_p - _target_p
                _dist_done  = _entry_p - _cur_p

            _pct = (_dist_done / _dist_total) * 100 if _dist_total > 0 else 0
            _avg_vol = float(_vol_s.rolling(10).mean().iloc[-1]) if len(_vol_s) >= 10 else 0
            _vol_low = _avg_vol > 0 and float(_vol_s.iloc[-1]) < _avg_vol

            if _pct >= 90 and _vol_low and _pos.get('partial_exit_alerted') != today:
                _mail_entry = {'ticker': _ticker, 'direction': _direction,
                               'entry_price': _entry_p, 'stop_price': _stop_p,
                               'target_price': _target_p, 'rr': ''}
                print(f"    {_ticker}: {_pct:.0f}% toward TP1, low vol -> PARTIAL EXIT")
                if send_partial_exit_alert(email, _mail_entry, _cur_p, _pct):
                    _pos['partial_exit_alerted'] = today
                    _pos_changed = True
                    alerts_sent += 1

            # --- Trail Stop check (Rule 1: most recent swing + 1% buffer) ---
            _prev_close = _close_s.iloc[:-1]  # confirmed bars only
            if _is_long:
                _swings   = _swing_lows(_prev_close, order=2)
                _new_stop = round(_swings[-1] * (1 - _pm_buf), 2) if _swings else None
                _better   = (_new_stop is not None
                             and _new_stop > _stop_p and _new_stop < _cur_p)
            else:
                _swings   = _swing_highs(_prev_close, order=2)
                _new_stop = round(_swings[-1] * (1 + _pm_buf), 2) if _swings else None
                _better   = (_new_stop is not None
                             and _new_stop < _stop_p and _new_stop > _cur_p)

            if _better:
                _mail_entry = {'ticker': _ticker, 'direction': _direction,
                               'entry_price': _entry_p, 'stop_price': _stop_p,
                               'target_price': _target_p}
                print(f"    {_ticker}: trail stop {_stop_p:.2f} -> {_new_stop:.2f}")
                if send_trail_stop_alert(email, _mail_entry, _new_stop):
                    _pos.setdefault('stop_history', []).append(
                        {'date': today, 'from': _stop_p, 'to': _new_stop,
                         'rule': 'HOURLY_TRAIL'})
                    _pos['stop'] = _new_stop
                    _pos_changed = True
                    alerts_sent += 1

        if _pos_changed:
            _save_positions(_positions)
            print("    positions.json updated (trailed stops recorded)")

    # Auto-prune: remove tickers out of zone for 21+ days
    NOT_IN_ZONE_DAYS = 21
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
            print(f"    REMOVED {sym} \u2014 {days} days not in zone")
        removed_syms = {s for s, _ in pruned}
        for r in html_results:
            if r['ticker'] in removed_syms:
                r['notes'] = r.get('notes','') + f'  [auto-removed: {NOT_IN_ZONE_DAYS}d out of zone]'
                r['tl'] = 'REMOVED'

    save_watchlist(data)
    generate_watch_html(html_results, today)
    print(f"\n  Done. {alerts_sent} green light alert(s) sent.")
    print("  " + "=" * 55)

    print()
    check_momentum_go(email)
    print("  " + "=" * 55 + "\n")


# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        send_test_email()
    else:
        run_check()
