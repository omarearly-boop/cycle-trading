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
    """
    GREEN  -- Prob >= 65, no earnings warning, monthly trend not against direction
    YELLOW -- Prob >= 50 or some moderate warning
    RED    -- Prob < 50 or earnings warning or hard counter-trend
    """
    prob      = setup.get('Prob', 0)
    earn_warn = setup.get('Earn') == 'SOON!'
    direction = 'LONG' if 'LONG' in setup.get('Dir', '') else 'SHORT'
    monthly   = setup.get('MonthlyTrend')   # 'LONG' / 'SHORT' / None

    if earn_warn:
        return 'RED'

    # Monthly trend hard against direction
    if monthly == 'SHORT' and direction == 'LONG':
        return 'RED'
    if monthly == 'LONG' and direction == 'SHORT':
        return 'RED'

    if prob >= 65:
        return 'GREEN'
    if prob >= 50:
        return 'YELLOW'
    return 'RED'


# ---------------------------------------------------------------------------
#  Analyze one ticker (calls the existing scanner modules)
# ---------------------------------------------------------------------------
_INTL_SUFFIXES = ('.L', '.DE', '.PA', '.T', '.AX', '.TO', '.SW', '.NS', '.HK', '.F')

def analyze_ticker(entry: dict):
    """
    Run ct_analysis._detect_setup() on a single watchlist entry.
    Returns (setup_dict, tl_color) or (None, None) if no setup found.
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
            return None, None

        setup = _detect_setup(
            ticker, 100000, market, is_crypto, asset_type,
            MAX_DIST_STOCK, direction,
            is_commodity=is_commodity,
            is_israel=is_israel,
            is_intl=is_intl,
        )
        if setup is None:
            return None, None

        tl = get_tl_color(setup)
        return setup, tl

    except Exception as e:
        print(f"    ERROR analyzing {ticker}: {e}")
        return None, None


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
    for entry in tickers:
        ticker     = entry['ticker']
        direction  = entry.get('direction', 'LONG')
        last_alert = entry.get('last_alerted') or ''

        print(f"  {ticker} ({direction})", end='  ')

        # Already alerted today -- skip
        if last_alert == today:
            print("-- already alerted today, skipping")
            continue

        setup, tl = analyze_ticker(entry)

        if setup is None:
            print("-- no setup found")
            continue

        prob = setup.get('Prob', 0)
        print(f"Prob={prob}%  TL={tl}")

        if tl != 'GREEN':
            continue

        # GREEN -- send alert
        print(f"    Sending GREEN LIGHT alert to {email}...")
        sent = send_green_alert(email, entry, setup)
        if sent:
            entry['last_alerted'] = today
            entry['alert_count']  = entry.get('alert_count', 0) + 1
            alerts_sent += 1
            print(f"    Email sent OK")

    # Save updated last_alerted values
    save_watchlist(data)
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
