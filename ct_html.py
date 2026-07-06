#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ct_html.py — HTML report generator + Pine Script exporter."""
import sys, time, warnings, os, logging
from datetime import datetime
import json
warnings.filterwarnings('ignore')
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.getLogger('peewee').setLevel(logging.CRITICAL)

def _install(pkg):
    import subprocess
    print(f"  Installing {pkg}...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])

try:    import yfinance as yf
except: _install("yfinance"); import yfinance as yf

try:    import pandas as pd
except: _install("pandas"); import pandas as pd
import webbrowser
from ct_config import PORTFOLIO_SIZE, RISK_PCT, MIN_PROBABILITY, MAX_OPEN_POSITIONS
from ct_analysis import get_traffic_light

# ══════════════════════════════════════════════════════════════
#  CARD RENDERERS — module-level so they're independently testable
#  CardRenderer: _render_tv_url, _render_fund_box, _render_setup_cards
#  Coordinator:  generate_html() calls these — no rendering logic there
# ══════════════════════════════════════════════════════════════

# TradingView symbol map for commodities (used by _render_tv_url)
COMMODITY_TV = {
    'GC':'COMEX:GC1!','SI':'COMEX:SI1!','PL':'COMEX:PL1!','PA':'COMEX:PA1!',
    'HG':'COMEX:HG1!','CL':'NYMEX:CL1!','BZ':'NYMEX:BB1!','NG':'NYMEX:NG1!',
    'RB':'NYMEX:RB1!','HO':'NYMEX:HO1!','ZW':'CBOT:ZW1!','ZC':'CBOT:ZC1!',
    'ZS':'CBOT:ZS1!','KC':'ICEUS:KC1!','CC':'ICEUS:CC1!','SB':'ICEUS:SB1!',
    'CT':'ICEUS:CT1!','OJ':'ICEUS:OJ1!','LE':'CME:LE1!','GF':'CME:GF1!',
    'ALI':'LME:AL1!',
}

def _render_tv_url(r):
    """Pure function — result dict → TradingView weekly chart URL."""
    t   = r['Ticker']
    raw = r.get('_raw', t)
    typ = r['Type']
    if typ == 'TASE':
        return f"https://www.tradingview.com/chart/?symbol=TASE:{t}&interval=W"
    if typ == 'CRYPTO':
        return f"https://www.tradingview.com/chart/?symbol=BINANCE:{t}USDT&interval=W"
    if typ == 'COMMODITY':
        sym = COMMODITY_TV.get(t, f'TVC:{t}')
        return f"https://www.tradingview.com/chart/?symbol={sym}&interval=W"
    if typ == 'INTL':
        if   raw.endswith('.L'):  return f"https://www.tradingview.com/chart/?symbol=LSE:{t}&interval=W"
        elif raw.endswith('.DE'): return f"https://www.tradingview.com/chart/?symbol=XETR:{t}&interval=W"
        elif raw.endswith('.PA'): return f"https://www.tradingview.com/chart/?symbol=EURONEXT:{t}&interval=W"
        elif raw.endswith('.T'):  return f"https://www.tradingview.com/chart/?symbol=TSE:{t}&interval=W"
        elif raw.endswith('.HK'): return f"https://www.tradingview.com/chart/?symbol=HKEX:{raw[:-3]}&interval=W"
        elif raw.endswith('.TO'): return f"https://www.tradingview.com/chart/?symbol=TSX:{t}&interval=W"
        elif raw.endswith('.AX'): return f"https://www.tradingview.com/chart/?symbol=ASX:{t}&interval=W"
        elif raw.endswith('.SW'): return f"https://www.tradingview.com/chart/?symbol=SWX:{t}&interval=W"
        elif raw.endswith('.NS'): return f"https://www.tradingview.com/chart/?symbol=NSE:{t}&interval=W"
        return f"https://www.tradingview.com/chart/?symbol={t}&interval=W"
    return f"https://www.tradingview.com/chart/?symbol=NASDAQ:{t}&interval=W"

def _render_fund_box(r):
    """Pure function — result dict → fundamental analysis HTML block."""
    fund = r.get('_fundamental')
    if not fund:
        return ''
    sig      = fund.get('signal', 'HOLD')
    conf_v   = fund.get('conf', 0)
    cons_v   = fund.get('consensus', '—')
    tgt_v    = fund.get('target')
    upside_v = fund.get('upside')
    caveats  = fund.get('caveats', [])
    bullets  = fund.get('bullets', [])
    sig_color = {'BUY': '#3fb950', 'SELL': '#f85149', 'HOLD': '#d29922'}.get(sig, '#8b949e')
    sig_bg    = {'BUY': '#0d2b0d', 'SELL': '#2b0d0d', 'HOLD': '#2b1f0d'}.get(sig, '#161b22')
    sig_emoji = {'BUY': '📈', 'SELL': '📉', 'HOLD': '⏸'}.get(sig, '—')
    target_str  = f'${tgt_v:,.2f}' if tgt_v else '—'
    upside_str  = (f' ({upside_v:+.1f}%)' if upside_v is not None else '')
    bullets_html = ''.join(f'<li>• {b}</li>' for b in bullets)  if bullets  else ''
    caveats_html = ''.join(f'<li>⚠ {c}</li>' for c in caveats) if caveats  else ''
    return f'''
  <div class="fund-box" style="background:{sig_bg};border:1px solid {sig_color}44;">
    <div class="fund-header">
      <span class="fund-title">🔬 Fundamental Analysis</span>
      <span class="fund-signal" style="color:{sig_color};border:1px solid {sig_color}66;">{sig_emoji} {sig} &nbsp;·&nbsp; {conf_v}% confidence</span>
    </div>
    <div class="fund-grid">
      <div class="fund-cell"><div class="fund-label">אנליסטים</div><div class="fund-val" style="color:{sig_color}">{cons_v}</div></div>
      <div class="fund-cell"><div class="fund-label">יעד מחיר</div><div class="fund-val">{target_str}<span style="color:#3fb950;font-size:11px">{upside_str}</span></div></div>
    </div>
    {f'<ul class="fund-bullets">{bullets_html}</ul>'  if bullets_html  else ''}
    {f'<ul class="fund-caveats">{caveats_html}</ul>' if caveats_html else ''}
  </div>'''


def _render_setup_cards(rows, direction, portfolio):
    """Render setup cards for a list of results. Module-level — independently testable."""
    if not rows:
        return f'<p class="none">No {direction} setups found today.</p>'
    html = ''
    card_idx = 0
    for r in rows:
        ticker  = r['Ticker']
        entry   = r['Entry']
        stop    = r['Stop']
        target  = r['Target']
        rr      = r['R:R']
        price   = r['Price']
        rsi_v   = r['RSI']
        pos     = r['Pos$']
        units   = r['Units']
        vol     = r['Vol']
        earn    = r['Earn']
        typ     = r['Type']

        is_long = direction == 'LONG'
        risk_u  = abs(entry - stop)
        rew_t1  = abs(target - entry)
        t2 = round(entry + rew_t1 * 1.618, 2) if is_long else round(entry - rew_t1 * 1.618, 2)
        t3 = round(entry + rew_t1 * 2.618, 2) if is_long else round(entry - rew_t1 * 2.618, 2)

        loss_d   = round(risk_u * units, 2)
        prof_t1  = round(rew_t1 * units, 2)
        prof_t2  = round(abs(t2 - entry) * units, 2)
        prof_t3  = round(abs(t3 - entry) * units, 2)
        pct_loss = round(loss_d / portfolio * 100, 2)
        pct_t1   = round(prof_t1 / portfolio * 100, 2)
        pct_t2   = round(prof_t2 / portfolio * 100, 2)
        pct_t3   = round(prof_t3 / portfolio * 100, 2)

        # 4-week compounding
        port_w = portfolio
        comp_rows = ''
        for w in range(1, 5):
            gain = port_w * 0.10 * rr
            port_w = round(port_w + gain, 2)
            total_gain = round(port_w - portfolio, 2)
            comp_rows += f'''
            <tr>
              <td>Week {w}</td>
              <td>${port_w:,.2f}</td>
              <td class="green">+${total_gain:,.2f}</td>
            </tr>'''

        earn_badge = (
            f'<span class="badge warn">⚠ SOON! Earnings</span>'  if earn == 'SOON!'        else
            f'<span class="badge earn-approaching">📅 {earn}</span>' if earn == 'APPROACHING' else
            f'<span class="badge ok">{earn}</span>'
        )
        # ATR / High Volatility badge
        atr_pct_v = r.get('ATR_pct', 0)
        high_vol  = r.get('HighVol', False)
        atr_badge = (f'<span class="badge atr-high">🔥 ATR {atr_pct_v}%</span>' if high_vol
                     else f'<span class="badge ok">ATR {atr_pct_v}%</span>')
        # Late Entry badge
        late_pct_v = r.get('LateEntry', 0)
        late_badge = (f'<span class="badge late-entry">⚡ LATE +{late_pct_v}% from level</span>' if late_pct_v > 5 else '')

        # MACD badge
        macd_d = r.get('_macd') or {}
        _cross = macd_d.get('cross'); _mtrend = macd_d.get('trend'); _mdiv = macd_d.get('divergence')
        if _cross == 'GOLDEN':
            macd_badge = '<span class="badge macd-bull">⚡ MACD Golden Cross</span>'
        elif _cross == 'DEATH':
            macd_badge = '<span class="badge macd-bear">💀 MACD Death Cross</span>'
        elif _mdiv == 'BULL_DIV':
            macd_badge = '<span class="badge macd-bull">📈 MACD Bull Divergence</span>'
        elif _mtrend == 'BULL':
            macd_badge = '<span class="badge ok">MACD ▲</span>'
        elif _mtrend == 'BEAR':
            macd_badge = '<span class="badge warn">MACD ▼</span>'
        else:
            macd_badge = ''

        # Bollinger badge
        boll_d = r.get('_boll') or {}
        _bpos = boll_d.get('position'); _bpct = boll_d.get('pct_b', 0.5)
        if _bpos == 'NEAR_LOWER':
            boll_badge = f'<span class="badge boll-low">🎯 Lower Band ({_bpct:.2f})</span>'
        elif _bpos == 'NEAR_UPPER':
            boll_badge = f'<span class="badge boll-high">⚠ Upper Band ({_bpct:.2f})</span>'
        elif _bpos == 'SQUEEZE':
            boll_badge = '<span class="badge boll-squeeze">🔥 BB Squeeze</span>'
        else:
            boll_badge = ''

        # ── Position Sizing ──────────────────────────────────
        pos_pct_v    = r.get('PosPct', 0)
        was_capped_v = r.get('WasCapped', False)
        cap_reason_v = r.get('CapReason', '')
        high_vol_v   = r.get('HighVol', False)
        # Position size color: green < 10%, yellow 10-15%, red > 15%
        if pos_pct_v <= 10:
            pos_pct_color = '#3fb950'
            pos_pct_label = 'SAFE'
        elif pos_pct_v <= 15:
            pos_pct_color = '#d29922'
            pos_pct_label = 'OK'
        else:
            pos_pct_color = '#f85149'
            pos_pct_label = 'OVERSIZE'

        pos_size_badge = (
            f'<span class="badge pos-capped">✂ {cap_reason_v}</span>'
            if was_capped_v else ''
        )
        vol_halved_note = ' (×0.5 — High Vol)' if high_vol_v else ''

        # ── Time Horizon badge ───────────────────────────────
        horizon_v    = r.get('TimeHorizon', 'MEDIUM')
        h_label_v    = r.get('HorizonLabel', '📈 Medium')
        h_color_v    = r.get('HorizonColor', '#d29922')
        h_range_v    = r.get('HorizonRange', '')
        est_weeks_v  = r.get('EstWeeks', '?')

        # Squeeze risk badge (SHORT only)
        sq_lvl   = r.get('SqueezeRisk', 'NONE')
        short_int = r.get('ShortInt', 0)
        inst_own  = r.get('InstOwn', 0)
        if not is_long and sq_lvl == 'HIGH':
            squeeze_badge = (
                f'<span class="badge squeeze-high">'
                f'🚨 SQUEEZE RISK HIGH — Short {short_int}% / Inst {inst_own}%</span>'
            )
            squeeze_box = f'''
  <div class="squeeze-box high">
<strong>🚨 SHORT SQUEEZE WARNING — HIGH RISK</strong><br>
Short Interest: <b>{short_int}%</b> &nbsp;|&nbsp; Institutional Ownership: <b>{inst_own}%</b><br>
<span style="color:#f0f6fc">
  {short_int}% of the float is already shorted. Institutional ownership at {inst_own}% means
  very few shares are available. Any positive news can trigger a violent squeeze upward.
  <b>Consider skipping this SHORT.</b>
</span>
  </div>'''
        elif not is_long and sq_lvl == 'MEDIUM':
            squeeze_badge = (
                f'<span class="badge squeeze-med">'
                f'⚠ Squeeze Risk — Short {short_int}% / Inst {inst_own}%</span>'
            )
            squeeze_box = f'''
  <div class="squeeze-box medium">
<strong>⚠ Squeeze Risk — MEDIUM</strong><br>
Short Interest: <b>{short_int}%</b> &nbsp;|&nbsp; Institutional Ownership: <b>{inst_own}%</b><br>
<span style="color:#e6edf3">Elevated short interest. Manage position size carefully.</span>
  </div>'''
        elif is_long and short_int >= 15:
            squeeze_badge = (
                f'<span class="badge squeeze-long">'
                f'🚀 Squeeze Potential — Short {short_int}%</span>'
            )
            squeeze_box = f'''
  <div class="squeeze-box long-squeeze">
<strong>🚀 Short Squeeze Potential (LONG advantage)</strong><br>
Short Interest: <b>{short_int}%</b> — If price moves up, forced short covering will amplify the move.
  </div>'''
        else:
            squeeze_badge = ''
            squeeze_box   = ''
        vol_badge  = (f'<span class="badge ok">✓ Vol OK</span>'
                      if vol == 'OK' else
                      f'<span class="badge warn">⚠ Vol</span>')

        # Probability + Traffic Light
        prob       = r.get('Prob', 50)
        pfacts     = r.get('_pfacts', [])
        if prob >= 70:
            prob_color = '#3fb950'   # green
            prob_label = 'HIGH'
        elif prob >= 55:
            prob_color = '#d29922'   # yellow
            prob_label = 'MEDIUM'
        else:
            prob_color = '#f85149'   # red
            prob_label = 'LOW'

        tl_color, tl_label, tl_green, tl_red = get_traffic_light(prob, r)
        tl_bg = {'GREEN': '#0d2b0d', 'YELLOW': '#2b1f0d', 'RED': '#2b0d0d'}[tl_color]
        tl_border = {'GREEN': '#3fb950', 'YELLOW': '#d29922', 'RED': '#f85149'}[tl_color]
        tl_green_html = ''.join(f'<li>✅ {g}</li>' for g in tl_green) if tl_green else ''
        tl_red_html   = ''.join(f'<li>⚠ {g}</li>' for g in tl_red)   if tl_red   else ''

        factor_html = ''
        for fname, fdelta, fexplain in pfacts:
            sign = '+' if fdelta >= 0 else ''
            clr  = '#3fb950' if fdelta > 0 else ('#f85149' if fdelta < 0 else '#8b949e')
            factor_html += (
                f'<div class="prob-factor" title="{fexplain}">'
                f'<span class="fname">{fname}</span>'
                f'<span class="fdelta" style="color:{clr}">{sign}{fdelta}%</span>'
                f'</div>'
            )

        # ── Top-Down Filter badges ──────────────────────────
        m_trend_v   = r.get('MonthlyTrend')
        m_candle_v  = r.get('MonthlyCandle')
        m_pct_v     = r.get('MonthlyPct')
        sec_rs_v    = r.get('SectorRS')
        sec_etf_v   = r.get('SectorETF')
        sec_trend_v = r.get('SectorTrend')
        rs_pct_v    = r.get('RS_pct')
        sup_q_v     = r.get('SupportQ', 'WEAK')
        sup_tch_v   = r.get('SupportTouches', 0)

        # Monthly trend badge
        if m_trend_v == 'LONG':
            mt_cls = 'bull'; mt_txt = '▲ LONG'
        elif m_trend_v == 'SHORT':
            mt_cls = 'bear'; mt_txt = '▼ SHORT'
        else:
            mt_cls = 'neut'; mt_txt = 'N/A'
        if m_candle_v and m_pct_v is not None:
            mc_sign = '+' if m_pct_v >= 0 else ''
            mc_sub = f'{m_candle_v.replace("_"," ")} ({mc_sign}{m_pct_v}%)'
        elif m_candle_v:
            mc_sub = m_candle_v.replace('_', ' ')
        else:
            mc_sub = '—'

        # Sector RS badge
        _rs_map = {
            'STRONG+': ('bull','STRONG+'), 'ABOVE': ('ok','ABOVE'),
            'NEUTRAL': ('neut','NEUTRAL'), 'BELOW': ('warn','BELOW'),
            'WEAK-':   ('bear','WEAK−'),
        }
        if sec_rs_v:
            rs_cls, rs_disp = _rs_map.get(sec_rs_v, ('neut', sec_rs_v))
            rs_txt = f'{rs_disp} ({rs_pct_v:+.1f}%)' if rs_pct_v is not None else rs_disp
            sec_sub = f'vs {sec_etf_v} · {sec_trend_v}' if sec_etf_v else '—'
        else:
            rs_cls = 'neut'; rs_txt = '—'; sec_sub = 'N/A'

        # Support quality badge
        _sq_map = {
            'STRONG': ('bull','STRONG'), 'MEDIUM': ('ok','MEDIUM'), 'WEAK': ('warn','WEAK'),
        }
        sq_cls, sq_disp = _sq_map.get(sup_q_v, ('neut', str(sup_q_v or '—')))
        sq_sub = f'{sup_tch_v} touch{"es" if sup_tch_v != 1 else ""} at level'

        # ── ColmexPro symbol mapping ──────────────────────
        COLMEX_MAP = {
            # Precious Metals
            'GC':'XAUUSD','SI':'XAGUSD','PL':'XPTUSD','PA':'XPDUSD',
            # Energy
            'CL':'USOIL','BZ':'BRENT','NG':'NATGAS','RB':'GASOLINE',
            'HO':'HEATINGOIL',
            # Industrial Metals
            'HG':'COPPER','ALI':'ALUMINUM',
            # Agriculture
            'ZW':'WHEAT','ZC':'CORN','ZS':'SOYBEANS',
            'KC':'COFFEE','CC':'COCOA','SB':'SUGAR','CT':'COTTON',
            # Livestock
            'LE':'LIVECATTLE','GF':'FEEDERCATTLE',
        }
        if typ == 'CRYPTO':
            cmx_sym = ticker + 'USD'          # BTC → BTCUSD
        elif typ == 'COMMODITY':
            cmx_sym = COLMEX_MAP.get(ticker, ticker)
        elif typ == 'TASE':
            cmx_sym = ticker + '.TA'
        elif typ == 'INTL':
            raw_tick = r.get('_raw', ticker)
            cmx_sym  = raw_tick                # e.g. SAP.DE as-is
        else:
            cmx_sym = ticker                   # US stock: AAPL as-is

        cmx_side   = 'BUY'  if is_long else 'SELL'
        cmx_action = 'Long' if is_long else 'Short'
        cmx_note   = (
            'Note: International stocks traded as CFDs. '
            'Verify symbol name in Colmex search.' if typ == 'INTL'
            else 'Note: Crypto traded as CFD (no actual coin ownership).' if typ == 'CRYPTO'
            else ''
        )

        # Pine Script for this ticker (escape for JS string)
        pine_code = make_pine_for_ticker(r)
        pine_js   = pine_code.replace('\\', '\\\\').replace('`', '\\`').replace('${', '\\${')
        modal_id  = f"pine_{direction}_{card_idx}"
        card_idx += 1

        html += f'''
<div class="card {'long-card' if is_long else 'short-card'}" data-horizon="{horizon_v}">
  <div class="card-header">
<div class="card-title">
  <span class="ticker">{ticker}</span>
  <span class="dir-badge {'long-badge' if is_long else 'short-badge'}">
    {'▲ LONG' if is_long else '▼ SHORT'}
  </span>
  <span class="horizon-badge" style="background:{h_color_v}22;color:{h_color_v};border:1px solid {h_color_v}66;"
        title="~{est_weeks_v} weeks to T1 · {h_range_v}">
    {h_label_v}
  </span>
  <span class="type-badge">{typ}</span>
</div>
<div class="card-meta">
  {vol_badge} {earn_badge} {atr_badge} {late_badge} {macd_badge} {boll_badge} {squeeze_badge}
  <a href="{_render_tv_url(r)}" target="_blank" class="tv-link">📊 TradingView</a>
  <button class="pine-btn" onclick="showPine('{modal_id}')">📋 Pine Script</button>
  <button class="colmex-btn" onclick="showColmex('{modal_id}_cmx')">💹 Trade on Colmex</button>
</div>
  </div>
  {squeeze_box}
  <!-- Position Sizing Box -->
  <div class="pos-size-box">
<div class="pos-size-title">💼 Position Sizing</div>
<div class="pos-size-grid">
  <div class="ps-cell">
    <div class="ps-label">סיכון לעסקה</div>
    <div class="ps-val">${r.get("Risk$",0):,}</div>
    <div class="ps-sub">{int(RISK_PCT*100)}% של התיק{vol_halved_note}</div>
  </div>
  <div class="ps-cell">
    <div class="ps-label">גודל פוזיציה</div>
    <div class="ps-val" style="color:{pos_pct_color}">${r.get("Pos$",0):,}</div>
    <div class="ps-sub" style="color:{pos_pct_color}">{pos_pct_v}% מהתיק — {pos_pct_label} {pos_size_badge}</div>
  </div>
  <div class="ps-cell">
    <div class="ps-label">כמות מניות</div>
    <div class="ps-val">{units}</div>
    <div class="ps-sub">@ ${entry}</div>
  </div>
  <div class="ps-cell">
    <div class="ps-label">מקסימום פוזיציות</div>
    <div class="ps-val">{MAX_OPEN_POSITIONS}</div>
    <div class="ps-sub">סה"כ בו זמנית</div>
  </div>
</div>
<div class="ps-rule">📏 כלל: 1% סיכון × {MAX_OPEN_POSITIONS} פוזיציות = {int(RISK_PCT*MAX_OPEN_POSITIONS*100)}% חשיפה מקסימלית</div>
  </div>
  <!-- Fundamental Analysis Box (stock-analysis skill) -->
  {_render_fund_box(r)}
  <!-- Traffic Light Decision Box -->
  <div class="tl-box" style="background:{tl_bg};border:1px solid {tl_border};">
<div class="tl-signal">{tl_label}</div>
<div class="tl-lists">
  {'<ul class="tl-green">' + tl_green_html + '</ul>' if tl_green_html else ''}
  {'<ul class="tl-red">'   + tl_red_html   + '</ul>' if tl_red_html   else ''}
</div>
  </div>
  <!-- Colmex Trade Modal for {ticker} -->
  <div id="{modal_id}_cmx" class="pine-modal" onclick="if(event.target===this)this.style.display='none'">
<div class="pine-box cmx-box">
  <div class="pine-header cmx-header">
    <span>💹 ColmexPro Order — {ticker} ({cmx_action})</span>
    <button class="pine-close" onclick="document.getElementById('{modal_id}_cmx').style.display='none'">✕</button>
  </div>
  <div class="cmx-instructions">
    <div class="cmx-tip">💡 פתח ColmexPro בטאב נפרד והתחבר → חפש <b>{cmx_sym}</b> → מלא לפי הטופס למטה</div>
    {f'<div class="cmx-note">⚠ {cmx_note}</div>' if cmx_note else ''}
  </div>

  <!-- ColmexPro-style order form -->
  <div class="cmx-form">
    <div class="cmx-form-title">Order entry {cmx_sym}</div>

    <div class="cmx-field-row">
      <span class="cmx-field-label">Symbol</span>
      <div class="cmx-field-val">
        <span class="cmx-eq-badge">EQ</span>
        <b>{cmx_sym}</b>
      </div>
      <button class="cmx-copy" onclick="copyText('{cmx_sym}',this)">Copy</button>
    </div>

    <div class="cmx-field-row">
      <span class="cmx-field-label">Side</span>
      <div class="cmx-side-btns">
        <span class="cmx-side-active {'cmx-side-buy' if is_long else 'cmx-side-sell'}">{cmx_side}</span>
        <span class="cmx-side-inactive">{'Sell' if is_long else 'Buy'}</span>
      </div>
      <button class="cmx-copy" onclick="copyText('{cmx_side}',this)">Copy</button>
    </div>

    <div class="cmx-field-row">
      <span class="cmx-field-label">Quantity</span>
      <div class="cmx-field-input">{units}</div>
      <button class="cmx-copy" onclick="copyText('{units}',this)">Copy</button>
    </div>

    <div class="cmx-field-row">
      <span class="cmx-field-label">Order type</span>
      <div class="cmx-field-val">Limit</div>
    </div>

    <div class="cmx-field-row">
      <span class="cmx-field-label">Validity</span>
      <div class="cmx-field-val">GTC</div>
    </div>

    <div class="cmx-field-row">
      <span class="cmx-field-label">Limit price</span>
      <div class="cmx-field-input cmx-price-blue">{entry}</div>
      <button class="cmx-copy" onclick="copyText('{entry}',this)">Copy</button>
    </div>

    <div class="cmx-field-row">
      <span class="cmx-field-label">SL price</span>
      <div class="cmx-field-input cmx-price-red">{stop}</div>
      <button class="cmx-copy" onclick="copyText('{stop}',this)">Copy</button>
    </div>

    <div class="cmx-field-row">
      <span class="cmx-field-label">TP price <small>(T1)</small></span>
      <div class="cmx-field-input cmx-price-green">{target}</div>
      <button class="cmx-copy" onclick="copyText('{target}',this)">Copy</button>
    </div>

    <div class="cmx-field-row cmx-fib-row">
      <span class="cmx-field-label">TP <small>(T2 Fib)</small></span>
      <div class="cmx-field-input cmx-price-green">{t2}</div>
      <button class="cmx-copy" onclick="copyText('{t2}',this)">Copy</button>
    </div>

    <div class="cmx-field-row cmx-fib-row">
      <span class="cmx-field-label">TP <small>(T3 Fib)</small></span>
      <div class="cmx-field-input cmx-price-green">{t3}</div>
      <button class="cmx-copy" onclick="copyText('{t3}',this)">Copy</button>
    </div>

    <div class="cmx-price-bar">
      <span class="cmx-price-blue">{entry}</span>
      <span class="cmx-bar-mid">{units} units · ${pos:,} · R:R 1:{rr}</span>
      <span class="cmx-price-{'green' if is_long else 'red'}">{target}</span>
    </div>

    <a href="https://webplatform.colmex.com/" target="_blank" class="cmx-place-btn">
      {'🟢 Place BUY Order' if is_long else '🔴 Place SELL Order'}
    </a>
  </div>
</div>
  </div>

  <!-- Pine Script modal for {ticker} -->
  <div id="{modal_id}" class="pine-modal" onclick="if(event.target===this)this.style.display='none'">
<div class="pine-box">
  <div class="pine-header">
    <span>📋 Pine Script — {ticker} {'LONG ▲' if is_long else 'SHORT ▼'}</span>
    <button class="pine-close" onclick="document.getElementById('{modal_id}').style.display='none'">✕</button>
  </div>
  <div class="pine-instructions">
    <b>How to use:</b>&nbsp; 1. Open TradingView &nbsp;→&nbsp;
    2. Open <b>{ticker}</b> chart, Weekly (1W) &nbsp;→&nbsp;
    3. Click <b>Pine Editor</b> at the bottom &nbsp;→&nbsp;
    4. Paste the code below &nbsp;→&nbsp; Click <b>"Add to chart"</b><br>
    → Entry (blue), Stop (red), T1/T2/T3 (green) lines appear automatically.
  </div>
  <textarea id="{modal_id}_code" class="pine-code" readonly>{pine_code}</textarea>
  <button class="pine-copy-btn" onclick="copyPine('{modal_id}')">📋 Copy to Clipboard</button>
  <span id="{modal_id}_copied" class="copied-msg" style="display:none">✓ Copied!</span>
</div>
  </div>

  <div class="levels-grid">
<div class="level-box">
  <div class="level-label">Current Price</div>
  <div class="level-value neutral">${price}</div>
</div>
<div class="level-box">
  <div class="level-label">Entry</div>
  <div class="level-value blue">${entry}</div>
</div>
<div class="level-box">
  <div class="level-label">Stop Loss</div>
  <div class="level-value red">${stop}</div>
</div>
<div class="level-box">
  <div class="level-label">Target T1</div>
  <div class="level-value green">${target}</div>
</div>
<div class="level-box">
  <div class="level-label">T2 (Fib 161.8%)</div>
  <div class="level-value green">${t2}</div>
</div>
<div class="level-box">
  <div class="level-label">T3 (Fib 261.8%)</div>
  <div class="level-value green">${t3}</div>
</div>
<div class="level-box">
  <div class="level-label">R:R Ratio</div>
  <div class="level-value {'green' if rr >= 2.5 else 'neutral'}">1:{rr}</div>
</div>
<div class="level-box">
  <div class="level-label">RSI</div>
  <div class="level-value {'red' if rsi_v > 65 else 'green' if rsi_v < 40 else 'neutral'}">{rsi_v}</div>
</div>
<div class="level-box">
  <div class="level-label">Position $</div>
  <div class="level-value neutral">${pos:,}</div>
</div>
  </div>

  <div class="two-col">
<div>
  <h4>Profit / Loss Scenarios</h4>
  <table class="scenario-table">
    <tr><th>Scenario</th><th>Price</th><th>P&L</th><th>% Portfolio</th></tr>
    <tr class="loss-row">
      <td>Stop hit</td><td>${stop}</td>
      <td>-${loss_d}</td><td class="red">-{pct_loss}%</td>
    </tr>
    <tr>
      <td>Target T1</td><td>${target}</td>
      <td>+${prof_t1}</td><td class="green">+{pct_t1}%</td>
    </tr>
    <tr>
      <td>Target T2</td><td>${t2}</td>
      <td>+${prof_t2}</td><td class="green">+{pct_t2}%</td>
    </tr>
    <tr>
      <td>Target T3</td><td>${t3}</td>
      <td>+${prof_t3}</td><td class="green">+{pct_t3}%</td>
    </tr>
  </table>
</div>
<div>
  <h4>4-Week Compounding</h4>
  <table class="scenario-table">
    <tr><th>Week</th><th>Portfolio</th><th>Total Gain</th></tr>
    {comp_rows}
  </table>
</div>
  </div>

  <!-- Top-Down Filter Analysis -->
  <div class="topdown-section">
<div class="topdown-title">📊 Top-Down Filter Analysis (Cycles Trading)</div>
<div class="topdown-grid">
  <div class="td-box">
    <div class="td-label">Monthly Trend</div>
    <div class="td-badge td-{mt_cls}">{mt_txt}</div>
    <div class="td-sub">{mc_sub}</div>
  </div>
  <div class="td-box">
    <div class="td-label">Sector Relative Strength</div>
    <div class="td-badge td-{rs_cls}">{rs_txt}</div>
    <div class="td-sub">{sec_sub}</div>
  </div>
  <div class="td-box">
    <div class="td-label">Support Quality</div>
    <div class="td-badge td-{sq_cls}">{sq_disp}</div>
    <div class="td-sub">{sq_sub}</div>
  </div>
</div>
  </div>

  <!-- Probability of Success -->
  <div class="prob-section">
<div class="prob-header">
  <span class="prob-title">Estimated Success Probability (reach T1)</span>
  <span class="prob-value" style="color:{prob_color}">{prob}% &nbsp;<small style="font-size:13px;font-weight:500">{prob_label}</small></span>
</div>
<div class="prob-bar-bg">
  <div class="prob-bar-fill" style="width:{prob}%;background:{prob_color}"></div>
</div>
<div class="prob-factors">
  {factor_html}
</div>
<div style="font-size:10px;color:#484f58;margin-top:8px">
  * Algorithmic estimate based on 10 technical factors (incl. Monthly Trend, Sector RS, Support Quality). Not a guarantee. Always manage your risk.
</div>
  </div>

</div>'''
    return html


def generate_html(results, script_d, ts, portfolio, risk_trade, iv_label,
                  n_stocks=0, n_israel=0, n_intl=0, n_crypto=0, n_commodity=0):
    """Thin coordinator — sorts results, assembles page, writes file.
    Card-level HTML lives in _render_setup_cards(), _render_tv_url(), _render_fund_box().
    """
    if not results:
        return None

    longs      = [r for r in results if 'LONG'  in r['Dir']]
    shorts     = [r for r in results if 'SHORT' in r['Dir']]
    try:
        from zoneinfo import ZoneInfo
        _tz = ZoneInfo('Asia/Jerusalem')
    except Exception:
        from datetime import timezone, timedelta
        _tz = timezone(timedelta(hours=3))
    scan_date  = datetime.now(_tz).strftime('%B %d, %Y  %H:%M') + ' (IL)'


    # Card renderer is now module-level: see _render_setup_cards() above
    # (lifted from generate_html for independent testability)

    # Sort by probability descending (best setups first)
    longs.sort(key=lambda x: x.get('Prob', 0),  reverse=True)
    shorts.sort(key=lambda x: x.get('Prob', 0), reverse=True)

    # Split into main (≥ MIN_PROBABILITY) and watchlist (< MIN_PROBABILITY)
    longs_main  = [r for r in longs  if not r.get('IsWatchlist')]
    longs_watch = [r for r in longs  if r.get('IsWatchlist')]
    shorts_main = [r for r in shorts if not r.get('IsWatchlist')]
    shorts_watch= [r for r in shorts if r.get('IsWatchlist')]

    long_cards        = _render_setup_cards(longs_main,  'LONG',  portfolio)
    long_watch_cards  = _render_setup_cards(longs_watch, 'LONG',  portfolio)
    short_cards       = _render_setup_cards(shorts_main, 'SHORT', portfolio)
    short_watch_cards = _render_setup_cards(shorts_watch,'SHORT', portfolio)

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cycles Trading Scanner — {scan_date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0d1117; color: #e6edf3; font-family: 'Segoe UI', Arial, sans-serif; font-size: 14px; }}
  a {{ color: #58a6ff; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}

  .header {{
    background: linear-gradient(135deg, #161b22 0%, #1c2128 100%);
    border-bottom: 1px solid #30363d;
    padding: 24px 32px;
  }}
  .header h1 {{ font-size: 22px; color: #f0f6fc; margin-bottom: 6px; }}
  .header .subtitle {{ color: #8b949e; font-size: 13px; }}

  .stats-bar {{
    display: flex; gap: 16px; flex-wrap: wrap;
    padding: 16px 32px;
    background: #161b22;
    border-bottom: 1px solid #30363d;
  }}
  .stat-box {{
    background: #21262d;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 12px 18px;
    min-width: 150px;
  }}
  .stat-label {{ color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .stat-value {{ color: #f0f6fc; font-size: 18px; font-weight: 600; margin-top: 2px; }}

  .section {{ padding: 24px 32px; }}
  .section-title {{
    font-size: 16px; font-weight: 700;
    margin-bottom: 16px; padding-bottom: 8px;
    border-bottom: 2px solid;
    display: flex; align-items: center; gap: 10px;
  }}
  .section-title.long  {{ color: #3fb950; border-color: #3fb950; }}
  .section-title.short {{ color: #f85149; border-color: #f85149; }}

  .card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 20px;
    margin-bottom: 20px;
    transition: border-color .2s;
  }}
  .long-card  {{ border-left: 4px solid #3fb950; }}
  .short-card {{ border-left: 4px solid #f85149; }}
  .card:hover {{ border-color: #58a6ff; }}

  .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; flex-wrap: wrap; gap: 10px; }}
  .card-title  {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}

  .ticker {{ font-size: 22px; font-weight: 800; color: #f0f6fc; }}
  .dir-badge {{
    font-size: 12px; font-weight: 700; padding: 4px 12px;
    border-radius: 20px; letter-spacing: 1px;
  }}
  .long-badge  {{ background: #1a3a1f; color: #3fb950; border: 1px solid #3fb950; }}
  .short-badge {{ background: #3a1a1a; color: #f85149; border: 1px solid #f85149; }}
  .type-badge  {{ background: #21262d; color: #8b949e; font-size: 11px; padding: 3px 8px; border-radius: 10px; }}

  .card-meta {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
  .badge {{ font-size: 11px; padding: 3px 8px; border-radius: 10px; }}
  .badge.ok               {{ background: #1a3a1f; color: #3fb950; }}
  .badge.warn             {{ background: #3a2a0a; color: #d29922; }}
  .badge.earn-approaching {{ background: #2d2007; color: #e3a800; border: 1px solid #a07800; font-weight:700; }}
  .badge.atr-high         {{ background: #3a0a0a; color: #ff8080; border: 1px solid #cc3333; font-weight:700; }}
  .badge.late-entry       {{ background: #1a1a3a; color: #9999ff; border: 1px solid #5555cc; font-weight:700; }}

  /* ── Horizon badge on each card ── */
  .horizon-badge {{
    font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 12px;
    letter-spacing: 0.3px; margin-left: 4px;
  }}

  /* ── Position Sizing box ── */
  .pos-size-box {{
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    margin: 0 0 12px 0; padding: 14px 16px;
  }}
  .pos-size-title {{
    font-size: 12px; font-weight: 700; color: #8b949e;
    text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px;
  }}
  .pos-size-grid {{
    display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; margin-bottom: 10px;
  }}
  .ps-cell {{ text-align: center; }}
  .ps-label {{ font-size: 10px; color: #6e7681; text-transform: uppercase; margin-bottom: 4px; }}
  .ps-val {{ font-size: 18px; font-weight: 700; color: #e6edf3; }}
  .ps-sub {{ font-size: 11px; color: #8b949e; margin-top: 2px; }}
  .ps-rule {{
    font-size: 11px; color: #58a6ff; border-top: 1px solid #21262d;
    padding-top: 8px; text-align: center;
  }}
  .badge.pos-capped  {{ background: #1a2a3a; color: #58a6ff; border: 1px solid #1f6feb; font-size: 10px; }}
  .badge.macd-bull   {{ background: #0d2b0d; color: #3fb950; border: 1px solid #3fb95066; font-weight:700; }}
  .badge.macd-bear   {{ background: #2b0d0d; color: #f85149; border: 1px solid #f8514966; font-weight:700; }}
  .badge.boll-low    {{ background: #0d1f2b; color: #58a6ff; border: 1px solid #58a6ff66; font-weight:700; }}
  .badge.boll-high   {{ background: #2b1a0d; color: #d29922; border: 1px solid #d2992266; font-weight:700; }}
  .badge.boll-squeeze{{ background: #1f0d2b; color: #bc8cff; border: 1px solid #bc8cff66; font-weight:700; }}

  /* ── Fundamental Analysis box ── */
  .fund-box {{
    border-radius: 8px; padding: 12px 16px; margin: 0 0 12px 0;
  }}
  .fund-header {{
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 10px;
  }}
  .fund-title  {{ font-size: 12px; font-weight: 700; color: #8b949e; text-transform: uppercase; }}
  .fund-signal {{
    font-size: 12px; font-weight: 800; padding: 3px 12px;
    border-radius: 12px; background: transparent;
  }}
  .fund-grid   {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 8px; }}
  .fund-cell   {{ text-align: center; }}
  .fund-label  {{ font-size: 10px; color: #6e7681; text-transform: uppercase; margin-bottom: 3px; }}
  .fund-val    {{ font-size: 16px; font-weight: 700; color: #e6edf3; }}
  .fund-bullets, .fund-caveats {{
    list-style: none; padding: 0; margin: 4px 0 0 0;
    font-size: 12px; line-height: 1.8; color: #8b949e;
  }}
  .fund-caveats li {{ color: #d29922; }}

  /* ── Traffic Light ── */
  .tl-box {{
    border-radius: 8px; padding: 12px 16px; margin: 0 0 12px 0;
    display: flex; align-items: flex-start; gap: 14px;
  }}
  .tl-signal {{ font-size: 18px; font-weight: 800; white-space: nowrap; min-width: 140px; }}
  .tl-lists {{ flex: 1; }}
  .tl-green, .tl-red {{ list-style: none; padding: 0; margin: 0; font-size: 12px; line-height: 1.7; }}
  .tl-green li {{ color: #3fb950; }}
  .tl-red   li {{ color: #f85149; }}

  /* ── Watchlist section ── */
  .watchlist-title {{
    font-size: 14px; font-weight: 700; color: #8b949e;
    padding: 16px 32px 8px; border-top: 1px dashed #30363d; margin-top: 8px;
  }}

  /* ── Horizon filter tabs ── */
  .horizon-tabs {{
    display: flex; gap: 8px; flex-wrap: wrap;
    padding: 16px 32px 0 32px;
    background: #0d1117;
  }}
  .htab {{
    padding: 6px 18px; border-radius: 20px; font-size: 13px; font-weight: 600;
    cursor: pointer; border: 1px solid #30363d;
    background: #161b22; color: #8b949e;
    transition: all 0.15s;
  }}
  .htab:hover {{ border-color: #58a6ff; color: #e6edf3; }}
  .htab.active {{ background: #1f6feb; border-color: #1f6feb; color: #fff; }}
  .htab.tab-weekly  {{ }}
  .htab.tab-monthly {{ }}
  .htab.tab-medium  {{ }}
  .htab.tab-long    {{ }}

  .tv-link {{ background: #1c2d4a; color: #58a6ff; padding: 4px 12px; border-radius: 6px; font-size: 12px; border: 1px solid #1f6feb; }}
  .pine-btn {{ background: #2d2a1f; color: #d29922; padding: 4px 12px; border-radius: 6px; font-size: 12px; border: 1px solid #6e511e; cursor: pointer; }}
  .pine-btn:hover {{ background: #3a3010; }}
  .colmex-btn {{ background: #0d2137; color: #1ea7e1; padding: 4px 12px; border-radius: 6px; font-size: 12px; border: 1px solid #1ea7e1; cursor: pointer; font-weight: 600; }}
  .colmex-btn:hover {{ background: #163450; }}

  /* Pine Script modal */
  .pine-modal {{
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.75); z-index: 9999;
    justify-content: center; align-items: center;
  }}
  .pine-modal[style*="block"] {{ display: flex !important; }}
  .pine-box {{
    background: #161b22; border: 1px solid #30363d;
    border-radius: 12px; padding: 0; width: 90%; max-width: 680px;
    max-height: 90vh; display: flex; flex-direction: column;
    box-shadow: 0 16px 48px rgba(0,0,0,0.6);
  }}
  .pine-header {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 14px 20px; border-bottom: 1px solid #30363d;
    font-weight: 700; color: #d29922; font-size: 14px;
  }}
  .pine-close {{
    background: none; border: none; color: #8b949e;
    font-size: 18px; cursor: pointer; padding: 0 4px;
  }}
  .pine-close:hover {{ color: #f0f6fc; }}
  .pine-instructions {{
    padding: 12px 20px; font-size: 12px; color: #8b949e;
    background: #0d1117; border-bottom: 1px solid #21262d; line-height: 1.6;
  }}
  .pine-code {{
    flex: 1; margin: 0; padding: 14px 20px;
    background: #0d1117; color: #3fb950;
    font-family: 'Consolas', 'Courier New', monospace; font-size: 12px;
    border: none; resize: none; outline: none;
    min-height: 260px; overflow-y: auto;
  }}
  .pine-copy-btn {{
    margin: 12px 20px; padding: 8px 18px;
    background: #1a3a1f; color: #3fb950;
    border: 1px solid #2ea043; border-radius: 6px;
    cursor: pointer; font-size: 13px; font-weight: 600;
    align-self: flex-start;
  }}
  .pine-copy-btn:hover {{ background: #2ea043; color: #fff; }}
  .copied-msg {{ color: #3fb950; font-size: 13px; margin: 12px 0; font-weight: 600; }}

  .levels-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
    gap: 10px;
    margin-bottom: 20px;
  }}
  .level-box {{
    background: #21262d;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 10px 12px;
    text-align: center;
  }}
  .level-label {{ font-size: 10px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 4px; }}
  .level-value {{ font-size: 15px; font-weight: 700; }}
  .level-value.green   {{ color: #3fb950; }}
  .level-value.red     {{ color: #f85149; }}
  .level-value.blue    {{ color: #58a6ff; }}
  .level-value.neutral {{ color: #f0f6fc; }}

  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 16px; }}
  @media (max-width: 700px) {{ .two-col {{ grid-template-columns: 1fr; }} }}

  h4 {{ font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}

  .scenario-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .scenario-table th {{
    background: #21262d; color: #8b949e;
    font-size: 11px; font-weight: 600;
    padding: 6px 10px; text-align: left;
    border-bottom: 1px solid #30363d;
  }}
  .scenario-table td {{ padding: 6px 10px; border-bottom: 1px solid #21262d; }}
  .scenario-table .loss-row td {{ color: #f85149; }}
  .green {{ color: #3fb950; }}
  .red   {{ color: #f85149; }}

  /* ColmexPro Modal — styled like the real platform */
  .cmx-box {{ max-width: 420px; background: #141c26 !important; }}
  .cmx-header {{ background: #141c26 !important; color: #e0e6f0 !important; border-bottom: 1px solid #253347 !important; }}
  .cmx-instructions {{ padding: 10px 14px; font-size: 12px; color: #8b9ab0; border-bottom: 1px solid #253347; }}
  .cmx-tip {{ background: #0d2137; border-left: 3px solid #1ea7e1; padding: 7px 10px; font-size: 12px; color: #8bb8d4; border-radius: 0 4px 4px 0; }}
  .cmx-note {{ color: #d29922; font-size: 11px; background: #2a2310; border-radius: 4px; padding: 5px 8px; margin-top: 6px; }}
  .cmx-form {{ padding: 0 14px 14px; display: flex; flex-direction: column; gap: 0; overflow-y: auto; max-height: 70vh; }}
  .cmx-form-title {{ font-size: 14px; font-weight: 600; color: #c8d8ec; padding: 12px 0 10px; border-bottom: 1px solid #253347; margin-bottom: 8px; }}
  .cmx-field-row {{ display: grid; grid-template-columns: 110px 1fr auto; align-items: center; gap: 8px; padding: 7px 10px; border-radius: 6px; margin-bottom: 4px; background: #1a2535; }}
  .cmx-fib-row {{ background: #151e2c; opacity: 0.85; }}
  .cmx-field-label {{ font-size: 12px; color: #7a8fa8; }}
  .cmx-field-label small {{ font-size: 10px; color: #556070; }}
  .cmx-field-val {{ font-size: 13px; color: #c8d8ec; display: flex; align-items: center; gap: 6px; }}
  .cmx-field-input {{ font-size: 14px; font-weight: 700; color: #c8d8ec; background: #0f1923; border: 1px solid #2d4060; border-radius: 5px; padding: 4px 10px; font-family: monospace; }}
  .cmx-price-blue {{ color: #4a9fd4 !important; border-color: #1e4060 !important; }}
  .cmx-price-red  {{ color: #e05555 !important; border-color: #501e1e !important; }}
  .cmx-price-green{{ color: #3fb950 !important; border-color: #1e4030 !important; }}
  .cmx-eq-badge {{ background: #c94f2a; color: #fff; font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 3px; margin-right: 4px; }}
  .cmx-side-btns {{ display: flex; gap: 4px; }}
  .cmx-side-active {{ padding: 4px 16px; border-radius: 5px; font-size: 13px; font-weight: 700; }}
  .cmx-side-buy  {{ background: #1a5c35; color: #3fb950; border: 1px solid #238636; }}
  .cmx-side-sell {{ background: #5c1a1a; color: #f85149; border: 1px solid #7d1b1b; }}
  .cmx-side-inactive {{ padding: 4px 16px; border-radius: 5px; font-size: 13px; background: #1a2535; color: #556070; border: 1px solid #253347; }}
  .cmx-price-bar {{ display: flex; justify-content: space-between; align-items: center; background: #0f1923; border-radius: 6px; padding: 8px 12px; margin-top: 8px; font-size: 13px; font-weight: 700; font-family: monospace; }}
  .cmx-bar-mid {{ font-size: 11px; color: #556070; font-family: sans-serif; font-weight: 400; text-align: center; }}
  .cmx-copy {{ background: #1a2535; color: #7a8fa8; border: 1px solid #2d4060; border-radius: 4px; font-size: 10px; padding: 3px 8px; cursor: pointer; white-space: nowrap; }}
  .cmx-copy:hover {{ background: #1ea7e1; color: #fff; border-color: #1ea7e1; }}
  .cmx-copy.copied {{ background: #238636; color: #fff; border-color: #238636; }}
  .cmx-place-btn {{ display: block; margin-top: 12px; background: #1b6bbf; color: #fff; text-align: center; padding: 11px; border-radius: 7px; font-weight: 700; text-decoration: none; font-size: 14px; }}
  .cmx-place-btn:hover {{ background: #1558a0; }}

  /* Probability section */
  .prob-section {{
    background: #21262d;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 16px;
  }}
  .prob-header {{
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 10px;
  }}
  .prob-title {{ font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }}
  .prob-value {{
    font-size: 22px; font-weight: 800;
  }}
  .prob-bar-bg {{
    height: 8px; background: #30363d; border-radius: 4px; overflow: hidden; margin-bottom: 12px;
  }}
  .prob-bar-fill {{ height: 100%; border-radius: 4px; transition: width .4s; }}
  .prob-factors {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4px 16px; }}
  .prob-factor {{ font-size: 11px; color: #8b949e; display: flex; justify-content: space-between; }}
  .prob-factor .fname {{ color: #c9d1d9; }}
  .prob-factor .fdelta {{ font-weight: 700; }}

  /* Top-Down Filters */
  .topdown-section {{
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 16px;
  }}
  .topdown-title {{
    font-size: 11px; color: #8b949e; text-transform: uppercase;
    letter-spacing: 0.5px; margin-bottom: 10px; font-weight: 600;
  }}
  .topdown-grid {{
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px;
  }}
  .td-box {{
    background: #161b22; border: 1px solid #30363d;
    border-radius: 6px; padding: 10px 12px;
  }}
  .td-label {{
    font-size: 10px; color: #8b949e; text-transform: uppercase;
    letter-spacing: 0.3px; margin-bottom: 5px;
  }}
  .td-badge {{
    font-size: 12px; font-weight: 700; padding: 3px 8px;
    border-radius: 4px; display: inline-block; margin-bottom: 4px;
  }}
  .td-sub {{ font-size: 10px; color: #8b949e; }}
  .td-bull {{ background: #0d2a1a; color: #3fb950; border: 1px solid #238636; }}
  .td-ok   {{ background: #1a2f0d; color: #8dbb3a; border: 1px solid #5a7a20; }}
  .td-neut {{ background: #21262d; color: #8b949e; border: 1px solid #484f58; }}
  .td-warn {{ background: #2d1f0a; color: #d29922; border: 1px solid #6e511e; }}
  .td-bear {{ background: #2d0f0f; color: #f85149; border: 1px solid #6e1b18; }}

  .none {{ color: #8b949e; font-style: italic; padding: 20px 0; }}

  /* Squeeze risk */
  .badge.squeeze-high {{ background: #4a0a0a; color: #ff6b6b; border: 1px solid #f85149; font-weight:700; }}
  .badge.squeeze-med  {{ background: #3a2a0a; color: #d29922; border: 1px solid #6e511e; }}
  .badge.squeeze-long {{ background: #0d2a1a; color: #56d364; border: 1px solid #2ea043; }}

  .squeeze-box {{
    border-radius: 8px; padding: 14px 16px;
    margin-bottom: 16px; font-size: 13px; line-height: 1.6;
  }}
  .squeeze-box.high {{
    background: #2d0f0f; border: 2px solid #f85149; color: #ffa0a0;
  }}
  .squeeze-box.medium {{
    background: #2d1f0a; border: 1px solid #d29922; color: #f0c060;
  }}
  .squeeze-box.long-squeeze {{
    background: #0d2a1a; border: 1px solid #2ea043; color: #56d364;
  }}
  .squeeze-box strong {{ color: inherit; font-size: 14px; }}

  .footer {{
    text-align: center;
    padding: 20px;
    color: #8b949e;
    font-size: 12px;
    border-top: 1px solid #30363d;
  }}
</style>
</head>
<body>

<div class="header">
  <h1>📈 Cycles Trading Scanner</h1>
  <div class="subtitle">
    {scan_date} &nbsp;|&nbsp; Interval: {iv_label} &nbsp;|&nbsp;
    Based on Focus Trader / Cycles Trading methodology
  </div>
</div>

<div class="stats-bar">
  <div class="stat-box">
    <div class="stat-label">Portfolio</div>
    <div class="stat-value">${portfolio:,.0f}</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">Risk / Trade</div>
    <div class="stat-value">${risk_trade:,.0f}</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">Interval</div>
    <div class="stat-value" style="font-size:14px">{iv_label.strip()}</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">US Stocks</div>
    <div class="stat-value">{n_stocks}</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">Israeli (TASE)</div>
    <div class="stat-value">{n_israel}</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">International</div>
    <div class="stat-value">{n_intl}</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">Crypto</div>
    <div class="stat-value">{n_crypto}</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">Commodities</div>
    <div class="stat-value">{n_commodity}</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">Long Setups</div>
    <div class="stat-value" style="color:#3fb950">{len(longs)}</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">Short Setups</div>
    <div class="stat-value" style="color:#f85149">{len(shorts)}</div>
  </div>
  <div class="stat-box">
    <div class="stat-label">Total Found</div>
    <div class="stat-value">{len(results)}</div>
  </div>
</div>

<!-- ── Time Horizon Filter Tabs ── -->
<div class="horizon-tabs">
  <span class="htab active" onclick="filterHorizon('ALL', this)">🔭 ALL</span>
  <span class="htab tab-weekly"  onclick="filterHorizon('WEEKLY',  this)"
        style="color:#3fb950;border-color:#3fb95055;">⚡ שבועי (1–2w)</span>
  <span class="htab tab-monthly" onclick="filterHorizon('MONTHLY', this)"
        style="color:#58a6ff;border-color:#58a6ff55;">📅 חודשי (3–6w)</span>
  <span class="htab tab-medium"  onclick="filterHorizon('MEDIUM',  this)"
        style="color:#d29922;border-color:#d2992255;">📈 בינוני (2–3m)</span>
  <span class="htab tab-long"    onclick="filterHorizon('LONG',    this)"
        style="color:#8b949e;border-color:#8b949e55;">🎯 ארוך (3m+)</span>
</div>

<div class="section">
  <div class="section-title long">▲ LONG SETUPS &nbsp;
    <span style="font-size:13px;font-weight:400;color:#8b949e">{len(longs_main)} קח / {len(longs_watch)} Watchlist</span>
  </div>
  {long_cards}
  {f'<div class="watchlist-title">👁 Watchlist — LONG (Prob &lt; {MIN_PROBABILITY}% — נצפה בלבד, אל תיכנס)</div>' if longs_watch else ''}
  {long_watch_cards if longs_watch else ''}
</div>

<div class="section">
  <div class="section-title short">▼ SHORT SETUPS &nbsp;
    <span style="font-size:13px;font-weight:400;color:#8b949e">{len(shorts_main)} קח / {len(shorts_watch)} Watchlist</span>
  </div>
  {short_cards}
  {f'<div class="watchlist-title">👁 Watchlist — SHORT (Prob &lt; {MIN_PROBABILITY}% — נצפה בלבד, אל תיכנס)</div>' if shorts_watch else ''}
  {short_watch_cards if shorts_watch else ''}
</div>

<div class="footer">
  Cycles Trading Scanner &nbsp;|&nbsp; For educational purposes only &nbsp;|&nbsp;
  Not financial advice &nbsp;|&nbsp; Always manage your risk
</div>

<script>
/* ── Time Horizon filter ── */
function filterHorizon(horizon, tabEl) {{
  // Update active tab
  document.querySelectorAll('.htab').forEach(function(t) {{ t.classList.remove('active'); }});
  tabEl.classList.add('active');
  // Show/hide cards
  document.querySelectorAll('.card').forEach(function(card) {{
    if (horizon === 'ALL') {{
      card.style.display = '';
    }} else {{
      card.style.display = (card.dataset.horizon === horizon) ? '' : 'none';
    }}
  }});
}}

function showPine(id) {{
  document.getElementById(id).style.display = 'flex';
}}
function showColmex(id) {{
  document.getElementById(id).style.display = 'flex';
}}
function copyPine(id) {{
  var ta = document.getElementById(id + '_code');
  ta.select();
  ta.setSelectionRange(0, 99999);
  try {{ navigator.clipboard.writeText(ta.value); }} catch(e) {{ document.execCommand('copy'); }}
  var msg = document.getElementById(id + '_copied');
  msg.style.display = 'inline';
  setTimeout(function(){{ msg.style.display = 'none'; }}, 2500);
}}
function copyText(val, btn) {{
  try {{ navigator.clipboard.writeText(String(val)); }} catch(e) {{
    var ta = document.createElement('textarea');
    ta.value = String(val); document.body.appendChild(ta);
    ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
  }}
  var orig = btn.textContent;
  btn.textContent = '✓';
  btn.classList.add('copied');
  setTimeout(function(){{ btn.textContent = orig; btn.classList.remove('copied'); }}, 1500);
}}
document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') {{
    document.querySelectorAll('.pine-modal').forEach(function(m){{
      m.style.display = 'none';
    }});
  }}
}});
</script>

</body>
</html>'''

    fpath = os.path.join(script_d, f"cycles_report_{ts}.html")
    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(html)
    return fpath


def make_pine_for_ticker(r):
    """Return a Pine Script string for a single ticker result."""
    ticker  = r['Ticker']
    is_long = 'LONG' in r['Dir']
    entry   = r['Entry']
    stop    = r['Stop']
    target  = r['Target']
    risk_u  = abs(entry - stop)
    rew_t1  = abs(target - entry)
    t2 = round(entry + rew_t1 * 1.618, 4) if is_long else round(entry - rew_t1 * 1.618, 4)
    t3 = round(entry + rew_t1 * 2.618, 4) if is_long else round(entry - rew_t1 * 2.618, 4)

    lines = [
        '//@version=5',
        f'indicator("Cycles: {ticker}", overlay=true, max_lines_count=100)',
        '// ─── HOW TO USE ───────────────────────────────────────────',
        '// 1. Open this stock/coin chart on TradingView (Weekly 1W)',
        '// 2. Click Pine Editor at the bottom',
        '// 3. Paste this code and click "Add to chart"',
        '// 4. Entry / Stop / Target lines appear automatically',
        '',
        f'// {"LONG  ▲" if is_long else "SHORT ▼"}  {ticker}',
        f'// Entry : {entry}  |  Stop: {stop}  |  R:R: {r["R:R"]}',
        '',
        f'entry_price  = {entry}',
        f'stop_price   = {stop}',
        f'target1      = {target}',
        f'target2      = {t2}',
        f'target3      = {t3}',
        '',
        '// Draw horizontal lines',
        'line.new(bar_index - 50, entry_price, bar_index + 20, entry_price,',
        '         color=color.new(color.blue,  0), width=2, style=line.style_solid)',
        'line.new(bar_index - 50, stop_price,  bar_index + 20, stop_price,',
        '         color=color.new(color.red,   0), width=2, style=line.style_dashed)',
        'line.new(bar_index - 50, target1,     bar_index + 20, target1,',
        '         color=color.new(color.green, 0), width=2, style=line.style_dashed)',
        'line.new(bar_index - 50, target2,     bar_index + 20, target2,',
        '         color=color.new(color.green,30), width=1, style=line.style_dashed)',
        'line.new(bar_index - 50, target3,     bar_index + 20, target3,',
        '         color=color.new(color.green,60), width=1, style=line.style_dotted)',
        '',
        '// Labels',
        f'label.new(bar_index+1, entry_price, "Entry  ${entry}",',
        '          color=color.blue,  textcolor=color.white, style=label.style_label_left, size=size.small)',
        f'label.new(bar_index+1, stop_price,  "Stop   ${stop}",',
        '          color=color.red,   textcolor=color.white, style=label.style_label_left, size=size.small)',
        f'label.new(bar_index+1, target1,     "T1     ${target}",',
        '          color=color.green, textcolor=color.white, style=label.style_label_left, size=size.small)',
        f'label.new(bar_index+1, target2,     "T2     ${t2}",',
        '          color=color.green, textcolor=color.white, style=label.style_label_left, size=size.small)',
        f'label.new(bar_index+1, target3,     "T3     ${t3}",',
        '          color=color.green, textcolor=color.white, style=label.style_label_left, size=size.small)',
    ]
    return '\n'.join(lines)


def save_pine_script(results, script_d, ts):
    """Save a combined Pine Script file with all setups (one block per ticker)."""
    if not results:
        return None

    pine_lines = [
        '//@version=5',
        'indicator("Cycles Trading Levels — All Setups", overlay=true, max_lines_count=500)',
        '// ─── HOW TO USE ───────────────────────────────────────────',
        '// 1. Open the chart for any stock/coin below (Weekly 1W)',
        '// 2. Click Pine Editor at the bottom',
        '// 3. Paste this entire script -> click "Add to chart"',
        '// 4. Lines for that ticker appear automatically',
        '',
        'ticker = syminfo.ticker',
        '',
    ]

    for r in results:
        t       = r['Ticker']
        is_long = 'LONG' in r['Dir']
        entry   = r['Entry']
        stop    = r['Stop']
        target  = r['Target']
        rew_t1  = abs(target - entry)
        t2 = round(entry + rew_t1 * 1.618, 4) if is_long else round(entry - rew_t1 * 1.618, 4)
        t3 = round(entry + rew_t1 * 2.618, 4) if is_long else round(entry - rew_t1 * 2.618, 4)

        pine_lines += [
            f'// {"▲" if is_long else "▼"} {t}',
            f'if str.contains(syminfo.ticker, "{t}")',
            f'    line.new(bar_index-50, {entry}, bar_index+20, {entry}, color=color.blue,  width=2)',
            f'    line.new(bar_index-50, {stop},  bar_index+20, {stop},  color=color.red,   width=2, style=line.style_dashed)',
            f'    line.new(bar_index-50, {target},bar_index+20, {target},color=color.green, width=2, style=line.style_dashed)',
            f'    line.new(bar_index-50, {t2},    bar_index+20, {t2},    color=color.green, width=1, style=line.style_dotted)',
            f'    line.new(bar_index-50, {t3},    bar_index+20, {t3},    color=color.green, width=1, style=line.style_dotted)',
            f'    label.new(bar_index+1, {entry},  "Entry ${entry}",  color=color.blue,  textcolor=color.white, style=label.style_label_left)',
            f'    label.new(bar_index+1, {stop},   "Stop  ${stop}",   color=color.red,   textcolor=color.white, style=label.style_label_left)',
            f'    label.new(bar_index+1, {target}, "T1    ${target}", color=color.green, textcolor=color.white, style=label.style_label_left)',
            f'    label.new(bar_index+1, {t2},     "T2    ${t2}",     color=color.green, textcolor=color.white, style=label.style_label_left)',
            f'    label.new(bar_index+1, {t3},     "T3    ${t3}",     color=color.green, textcolor=color.white, style=label.style_label_left)',
            '',
        ]

    pine_path = os.path.join(script_d, f"cycles_levels_{ts}.pine")
    with open(pine_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(pine_lines))

    print(f"  Pine saved  : REPORTS\\cycles_levels_{ts}.pine")
    print()
    return pine_path


