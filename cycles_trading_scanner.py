#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════╗
║         CYCLES TRADING SCANNER  v6                          ║
║         סייקלס טריידינג — LONG + SHORT — Stocks + Crypto    ║
║                                                              ║
║   Just double-click run_scanner.bat to run                  ║
╚══════════════════════════════════════════════════════════════╝

WHAT THIS SCRIPT DOES:
  Scans stocks AND crypto on the WEEKLY chart.
  Finds setups for BOTH directions:

  🟢 LONG  — uptrend + pullback to support  + RSI not overbought
  🔴 SHORT — downtrend + bounce to resistance + RSI not oversold

  For every qualifying asset it calculates:
  - Entry, Stop Loss, Target (based on key levels + ATR)
  - Risk:Reward ratio (minimum 1:2)
  - Position size (based on your portfolio and risk %)
  - Score to rank the best setups first
"""

import sys, time, warnings, os, webbrowser, logging
from datetime import datetime
warnings.filterwarnings('ignore')
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.getLogger('peewee').setLevel(logging.CRITICAL)

# ─── AUTO-INSTALL ──────────────────────────────────────────────
def _install(pkg):
    import subprocess
    print(f"  Installing {pkg}...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])

try:    import yfinance as yf
except: _install("yfinance"); import yfinance as yf

try:    import pandas as pd
except: _install("pandas"); import pandas as pd

import json


# ══════════════════════════════════════════════════════════════
#  LEARNINGS SYSTEM — loads case studies from cycles_learnings.json
# ══════════════════════════════════════════════════════════════

def load_learnings():
    """Load case studies from cycles_learnings.json (same folder as script)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cycles_learnings.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cases = data.get('case_studies', [])
        lessons = data.get('global_lessons', [])
        print(f"\n  📚 Learnings loaded: {len(cases)} case studies | {len(lessons)} global rules")
        return data
    except Exception as e:
        print(f"  ⚠ Could not load learnings: {e}")
        return None

def save_case_study(ticker, direction, setup_dict, note=''):
    """Append a new case study entry to cycles_learnings.json."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cycles_learnings.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        existing_ids = [c.get('id','CS-000') for c in data.get('case_studies',[])]
        max_n = max((int(i.split('-')[1]) for i in existing_ids if '-' in i), default=0)
        new_id = f"CS-{max_n+1:03d}"
        entry = {
            'id':        new_id,
            'date':      datetime.now().strftime('%Y-%m-%d'),
            'ticker':    ticker,
            'direction': direction,
            'entry':     setup_dict.get('Entry'),
            'stop':      setup_dict.get('Stop'),
            'target':    setup_dict.get('Target'),
            'rr':        setup_dict.get('R:R'),
            'prob':      setup_dict.get('Prob'),
            'atr_pct':   setup_dict.get('ATR_pct'),
            'earn_days': setup_dict.get('EarnDays'),
            'monthly':   setup_dict.get('MonthlyTrend'),
            'candle':    setup_dict.get('MonthlyCandle'),
            'sector_rs': setup_dict.get('SectorRS'),
            'support_q': setup_dict.get('SupportQ'),
            'outcome':   'PENDING',
            'note':      note,
            'lessons':   []
        }
        data.setdefault('case_studies', []).append(entry)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return new_id
    except Exception as e:
        return None

LEARNINGS = load_learnings()   # loaded once at startup


# ══════════════════════════════════════════════════════════════
#  POSITION MANAGER — persistence
# ══════════════════════════════════════════════════════════════

def _pm_load() -> list:
    """Load open positions from positions.json."""
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f).get('positions', [])
    except Exception:
        return []

def _pm_save(positions: list):
    """Persist positions list to positions.json."""
    with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(
            {'positions': positions, '_updated': datetime.now().strftime('%Y-%m-%d')},
            f, indent=2, ensure_ascii=False
        )

def pm_add(ticker: str, direction: str, entry: float,
           stop: float, tp1: float, tp2: float, tp3: float,
           units: int, notes: str = '') -> dict:
    """
    Add a new open position.
    Called after a scanner entry is confirmed and filled.
    """
    direction = direction.upper()
    positions = _pm_load()
    pos = {
        'id':           f"{ticker.upper()}_{datetime.now().strftime('%Y%m%d%H%M')}",
        'ticker':       ticker.upper(),
        'direction':    direction,
        'entry':        round(float(entry), 4),
        'stop':         round(float(stop),  4),
        'tp1':          round(float(tp1),   4),
        'tp2':          round(float(tp2),   4),
        'tp3':          round(float(tp3),   4),
        'units':        int(units),
        'entry_date':   datetime.now().strftime('%Y-%m-%d'),
        'tp1_hit':      False,
        'tp2_hit':      False,
        'tp3_hit':      False,
        'closed':       False,
        'stop_history': [],
        'notes':        notes,
    }
    positions.append(pos)
    _pm_save(positions)
    print(f'  ✅ Position added: {pos["id"]}  {direction} {ticker.upper()} @ {entry}')
    return pos

def pm_close(pos_id: str, exit_price: float = None):
    """Mark a position as closed (stop hit or manual exit)."""
    positions = _pm_load()
    for p in positions:
        if p['id'] == pos_id:
            p['closed']     = True
            p['close_date'] = datetime.now().strftime('%Y-%m-%d')
            if exit_price is not None:
                p['exit_price'] = round(float(exit_price), 4)
            _pm_save(positions)
            print(f'  🔒 Closed: {pos_id}')
            return
    print(f'  ⚠ Position not found: {pos_id}')


# ══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════

PORTFOLIO_SIZE     = 25000  # Omar's demo portfolio — $25,000
RISK_PCT           = 0.01   # 1% risk per trade  (was 10% — FIXED)
MAX_POS_PCT        = 0.15   # max 15% of portfolio in one position
MAX_OPEN_POSITIONS = 6      # never hold more than 6 simultaneous trades
MIN_RR             = 2.0    # minimum risk:reward ratio
EARNINGS_WARN_DAYS = 14     # warn if earnings within N days

# ── Fundamental analysis (yfinance, Factor 14) ──────────────
FUNDAMENTAL_TIMEOUT   = 15   # seconds per ticker (yfinance is fast)

# ── Accuracy / Loss-reduction settings ──────────────────────
MIN_PROBABILITY    = 65     # only show setups ≥ 65% — rest → Watchlist
# Hard-block rules: if ALL conditions match → skip setup entirely (not even watchlist)
HARD_BLOCKS = [
    # (direction, monthly_candle_contains, reason)
    ('LONG',  'STRONG_BEAR', 'Monthly STRONG_BEAR + LONG = structural conflict (CS-001 BKR, CS-003 CVX, CS-005 Ford)'),
    ('SHORT', 'STRONG_BULL', 'Monthly STRONG_BULL + SHORT = structural conflict'),
]

# RSI thresholds
RSI_LONG_MAX  = 65   # LONG only if RSI < 65 (not overbought)
RSI_SHORT_MIN = 35   # SHORT only if RSI > 35 (not oversold)

# ── Position Manager ──────────────────────────────────────────
POSITIONS_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'positions.json')
PM_SWING_LOOKBACK = 2     # bars each side to confirm a weekly swing pivot
PM_MOMENTUM_WEEKS = 3     # consecutive up/down weeks to trigger momentum rule
PM_STOP_BUFFER    = 0.01  # 1 % buffer below swing low / above swing high

# Max distance from key level to enter
MAX_DIST_STOCK     = 0.12   # 12% for stocks
MAX_DIST_CRYPTO    = 0.20   # 20% for crypto (more volatile)
MAX_DIST_COMMODITY = 0.15   # 15% for commodities
MAX_DIST_INTL      = 0.12   # 12% for international stocks


# ══════════════════════════════════════════════════════════════
#  WATCHLISTS — loaded from watchlists.json (edit tickers there)
# ══════════════════════════════════════════════════════════════

def _load_watchlists():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'watchlists.json')
    if not os.path.exists(path):
        print('  ⚠ watchlists.json not found — using empty lists')
        return {}, {}, {}, {}, {}, {}
    with open(path, 'r', encoding='utf-8') as f:
        d = json.load(f)
    return (d.get('stocks', []), d.get('israel', []), d.get('intl', []),
            d.get('crypto', []), d.get('commodity', []), d.get('sector_etf', {}))

STOCK_WATCHLIST, ISRAEL_WATCHLIST, INTL_WATCHLIST, CRYPTO_WATCHLIST, COMMODITY_WATCHLIST, SECTOR_ETF = _load_watchlists()

# Perf: sector ETF weekly data is fetched once per scan, not once per stock
_SECTOR_CACHE: dict = {}  # sector_etf → sec_df

# (watchlists loaded from watchlists.json above)
_STOCK_FALLBACK = [
    # ── Mega Cap Tech ──────────────────────────────────────────
    'AAPL','MSFT','NVDA','GOOGL','GOOG','META','AMZN','TSLA','AVGO',
    # ── Software / Cloud ───────────────────────────────────────
    'ORCL','CRM','ADBE','INTU','NOW','SNOW','DDOG','NET','MDB',
    'PLTR','CRWD','PANW','ZS','FTNT','OKTA','HUBS','WDAY','VEEV',
    'TEAM','DOCN','GTLB','PATH','AI','SMAR','ASAN','BOX','DOCU',
    # ── Semiconductors ─────────────────────────────────────────
    'AMD','QCOM','TXN','MU','AMAT','LRCX','KLAC','MRVL','ADI',
    'ARM','INTC','TSM','ASML','SMCI','ON','MPWR','SWKS','QRVO',
    'WOLF','ONTO','COHU','CRUS','DIOD',
    # ── Internet / E-Commerce ──────────────────────────────────
    'EBAY','ETSY','SHOP','MELI','SE','GRAB','BABA','JD','PDD',
    'AMZN','W','CHWY',
    # ── Fintech / Payments ─────────────────────────────────────
    'V','MA','AXP','PYPL','SQ','AFRM','SOFI','LC','UPST','OPFI',
    'COIN','MARA','RIOT','CLSK','HUT','BTBT',
    # ── Big Finance / Banks ────────────────────────────────────
    'JPM','BAC','WFC','GS','MS','C','USB','PNC','TFC','COF',
    'BLK','SPGI','MCO','ICE','CME','CBOE','NDAQ','MKTX','TW','HOOD','IBKR','MRX',
    # ── Insurance ──────────────────────────────────────────────
    'BRK-B','MET','PRU','AFL','ALL','TRV','HIG','CB','AIG','PGR',
    # ── Healthcare / Pharma ────────────────────────────────────
    'UNH','JNJ','LLY','PFE','ABBV','MRK','TMO','ABT','DHR','SYK',
    'BMY','AMGN','GILD','BIIB','REGN','VRTX','MRNA','BNTX','NVAX',
    'CVS','CI','HUM','MOH','CNC','ELV','MCK','CAH','ABC',
    # ── Medical Devices ────────────────────────────────────────
    'ISRG','BSX','EW','MDT','BDX','BAX','ZBH','HOLX','IDXX','ALGN',
    # ── Consumer Staples ───────────────────────────────────────
    'WMT','COST','TGT','HD','LOW','KR','SFM','GO','CASY',
    'PG','KO','PEP','MDLZ','GIS','K','CPB','MKC','SJM',
    'CL','CHD','EL','ULTA','COTY',
    # ── Consumer Discretionary ─────────────────────────────────
    'MCD','SBUX','CMG','DPZ','YUM','QSR','DENN','CAKE','SHAK',
    'NKE','LULU','UAA','PVH','RL','TPR','CPRI','HBI',
    'F','GM','RIVN','LCID','NIO','LI','XPEV','FSR',
    # ── Media / Entertainment ──────────────────────────────────
    'NFLX','DIS','CMCSA','WBD','PARA','FOXA','FOX','AMC','CNK',
    'TTWO','EA','RBLX','U','DKNG','PENN',
    # ── Social / Communication ─────────────────────────────────
    'SNAP','PINS','RDDT','APP','MTCH','BMBL','IAC',
    'T','VZ','TMUS','LUMN','FYBR','ATUS',
    # ── Travel / Hotels / Airlines ─────────────────────────────
    'MAR','HLT','IHG','H','WH','CHH','BKNG','EXPE','ABNB',
    'DAL','UAL','AAL','LUV','JBLU','ALK','SAVE',
    'RCL','CCL','NCLH','LVS','MGM','WYNN','CZR','PENN',
    # ── Energy ─────────────────────────────────────────────────
    'XOM','CVX','COP','SLB','EOG','PXD','DVN','HAL','BKR','MPC',
    'VLO','PSX','HES','APA','OXY','FANG','RRC','AR','EQT',
    # ── Utilities / Clean Energy ───────────────────────────────
    'NEE','DUK','SO','D','AEP','EXC','SRE','XEL','ES','AWK',
    'ENPH','SEDG','RUN','NOVA','ARRY','FSLR','CSIQ','SPWR',
    # ── Real Estate / REITs ────────────────────────────────────
    'AMT','PLD','EQIX','CCI','SPG','O','VICI','WELL','DLR','PSA',
    # ── Industrials ────────────────────────────────────────────
    'CAT','DE','GE','HON','MMM','RTX','LMT','NOC','GD','BA',
    'UPS','FDX','XPO','JBHT','CHRW','EXPD','ZIM','MATX',
    'LIN','APD','SHW','DD','NEM','FCX','CLF','AA','X',
    # ── ETFs (for macro view) ──────────────────────────────────
    'SPY','QQQ','IWM','DIA','XLK','XLF','XLE','XLV','XLP','XLY','ARKK',
]

# ── (INTL_WATCHLIST, CRYPTO_WATCHLIST, COMMODITY_WATCHLIST, SECTOR_ETF loaded from watchlists.json) ──
_INTL_FALLBACK = [
    # ── UK — FTSE 100 (.L) ─────────────────────────────────────
    'AZN.L',    # AstraZeneca
    'SHEL.L',   # Shell
    'BP.L',     # BP
    'HSBA.L',   # HSBC
    'ULVR.L',   # Unilever
    'GSK.L',    # GSK
    'RIO.L',    # Rio Tinto
    'GLEN.L',   # Glencore
    'AAL.L',    # Anglo American
    'LSEG.L',   # London Stock Exchange Group
    'DGE.L',    # Diageo
    'BATS.L',   # British American Tobacco
    'REL.L',    # RELX
    'VOD.L',    # Vodafone
    'LLOY.L',   # Lloyds Banking
    'BARC.L',   # Barclays
    'NWG.L',    # NatWest
    'BT-A.L',   # BT Group
    'PRU.L',    # Prudential
    'BHP.L',    # BHP Group
    # ── Germany — DAX 40 (.DE) ─────────────────────────────────
    'SAP.DE',   # SAP
    'SIE.DE',   # Siemens
    'ALV.DE',   # Allianz
    'BAYN.DE',  # Bayer
    'BMW.DE',   # BMW
    'MBG.DE',   # Mercedes-Benz
    'VOW3.DE',  # Volkswagen
    'DTE.DE',   # Deutsche Telekom
    'DBK.DE',   # Deutsche Bank
    'BAS.DE',   # BASF
    'MUV2.DE',  # Munich Re
    'ADS.DE',   # Adidas
    'EOAN.DE',  # E.ON
    'RWE.DE',   # RWE
    'HEN3.DE',  # Henkel
    # ── France — CAC 40 (.PA) ──────────────────────────────────
    'MC.PA',    # LVMH
    'OR.PA',    # L'Oreal
    'TTE.PA',   # TotalEnergies
    'SAN.PA',   # Sanofi
    'BNP.PA',   # BNP Paribas
    'AIR.PA',   # Airbus
    'AI.PA',    # Air Liquide
    'RI.PA',    # Pernod Ricard
    'KER.PA',   # Kering
    'DG.PA',    # Vinci
    'CAP.PA',   # Capgemini
    'ENGI.PA',  # Engie
    'WLN.PA',   # Worldline
    # ── Japan — Nikkei (.T) ────────────────────────────────────
    '7203.T',   # Toyota
    '6758.T',   # Sony
    '9984.T',   # SoftBank
    '6861.T',   # Keyence
    '8306.T',   # Mitsubishi UFJ
    '9432.T',   # NTT
    '7974.T',   # Nintendo
    '6501.T',   # Hitachi
    '4063.T',   # Shin-Etsu Chemical
    '4502.T',   # Takeda Pharma
    '6367.T',   # Daikin
    '8316.T',   # Sumitomo Mitsui
    # ── Canada — TSX (.TO) ─────────────────────────────────────
    'RY.TO',    # Royal Bank of Canada
    'TD.TO',    # Toronto-Dominion Bank
    'BNS.TO',   # Bank of Nova Scotia
    'BMO.TO',   # Bank of Montreal
    'CM.TO',    # CIBC
    'ENB.TO',   # Enbridge
    'CNR.TO',   # Canadian National Railway
    'CP.TO',    # Canadian Pacific
    'SU.TO',    # Suncor Energy
    'ABX.TO',   # Barrick Gold
    'AEM.TO',   # Agnico Eagle Mines
    'WCN.TO',   # Waste Connections
    # ── Australia — ASX (.AX) ──────────────────────────────────
    'CBA.AX',   # Commonwealth Bank
    'BHP.AX',   # BHP
    'CSL.AX',   # CSL
    'ANZ.AX',   # ANZ Banking
    'NAB.AX',   # National Australia Bank
    'WBC.AX',   # Westpac
    'WES.AX',   # Wesfarmers
    'FMG.AX',   # Fortescue Metals
    'MQG.AX',   # Macquarie Group
    'RIO.AX',   # Rio Tinto
    # ── Switzerland — SMI (.SW) ────────────────────────────────
    'NESN.SW',  # Nestle
    'ROG.SW',   # Roche
    'NOVN.SW',  # Novartis
    'ABB.SW',   # ABB
    'ZURN.SW',  # Zurich Insurance
    'LONN.SW',  # Lonza
    'SREN.SW',  # Swiss Re
    # ── India — NIFTY (.NS) ────────────────────────────────────
    'RELIANCE.NS',    # Reliance Industries
    'TCS.NS',         # Tata Consultancy
    'HDFCBANK.NS',    # HDFC Bank
    'INFY.NS',        # Infosys
    'ICICIBANK.NS',   # ICICI Bank
    'HINDUNILVR.NS',  # Hindustan Unilever
    'KOTAKBANK.NS',   # Kotak Mahindra
    'WIPRO.NS',       # Wipro
    'AXISBANK.NS',    # Axis Bank
    'TATAMOTORS.NS',  # Tata Motors
    # ── Hong Kong / China (.HK) ────────────────────────────────
    '0700.HK',  # Tencent
    '9988.HK',  # Alibaba (HK)
    '3690.HK',  # Meituan
    '0939.HK',  # China Construction Bank
    '1299.HK',  # AIA Group
    '0941.HK',  # China Mobile
    '0388.HK',  # HKEX
    '2318.HK',  # Ping An Insurance
    '1211.HK',  # BYD
    '0005.HK',  # HSBC (HK)
]



# ══════════════════════════════════════════════════════════════
#  INDICATOR FUNCTIONS
# ══════════════════════════════════════════════════════════════

def rsi(series, n=14):
    d  = series.diff()
    g  = d.where(d > 0, 0.0).rolling(n).mean()
    l  = (-d.where(d < 0, 0.0)).rolling(n).mean()
    rs = g / l.replace(0, float('nan'))
    return 100 - (100 / (1 + rs))

def atr(high, low, close, n=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def get_trend(df):
    """
    Determine weekly trend using SMA crossover + price slope.
    Returns: 'LONG', 'SHORT', or None (no clear trend).
    """
    if len(df) < 55:
        return None

    sma20  = float(df['Close'].rolling(20).mean().iloc[-1])
    sma50  = float(df['Close'].rolling(50).mean().iloc[-1])
    price  = float(df['Close'].iloc[-1])
    p8ago  = float(df['Close'].iloc[-9]) if len(df) >= 9 else price

    # Uptrend: SMA20 > SMA50 and price above SMA50
    if sma20 > sma50 and price > sma50 * 0.97:
        return 'LONG'

    # Downtrend: SMA20 < SMA50 and price below SMA50
    if sma20 < sma50 and price < sma50 * 1.03:
        return 'SHORT'

    return None

def swing_lows(series, order=3):
    pts = []
    for i in range(order, len(series) - order):
        win = [series.iloc[i+j] for j in range(-order, order+1) if j != 0]
        if all(series.iloc[i] <= w for w in win):
            pts.append(float(series.iloc[i]))
    return pts

def swing_highs(series, order=3):
    pts = []
    for i in range(order, len(series) - order):
        win = [series.iloc[i+j] for j in range(-order, order+1) if j != 0]
        if all(series.iloc[i] >= w for w in win):
            pts.append(float(series.iloc[i]))
    return pts

def _pm_pivot_lows(df: pd.DataFrame, lookback: int = PM_SWING_LOOKBACK) -> list:
    """
    Confirmed weekly swing lows for the position manager.
    Uses Low column. Excludes the last (current/open) bar.
    Returns list of (bar_index, price).
    """
    lows = df['Low'].values
    result = []
    for i in range(lookback, len(lows) - lookback - 1):
        if (all(lows[i] <= lows[i-j] for j in range(1, lookback+1)) and
                all(lows[i] <= lows[i+j] for j in range(1, lookback+1))):
            result.append((i, float(lows[i])))
    return result

def _pm_pivot_highs(df: pd.DataFrame, lookback: int = PM_SWING_LOOKBACK) -> list:
    """
    Confirmed weekly swing highs for the position manager.
    Uses High column. Excludes the last (current/open) bar.
    Returns list of (bar_index, price).
    """
    highs = df['High'].values
    result = []
    for i in range(lookback, len(highs) - lookback - 1):
        if (all(highs[i] >= highs[i-j] for j in range(1, lookback+1)) and
                all(highs[i] >= highs[i+j] for j in range(1, lookback+1))):
            result.append((i, float(highs[i])))
    return result

def get_levels(df, price, atr_val):
    """
    Find nearest support (below price) and resistance (above price).
    Falls back to SMA20 for support and ATR-based target for resistance.
    """
    sma20 = float(df['Close'].rolling(20).mean().iloc[-1])

    lows  = swing_lows(df['Low'],  order=3)
    highs = swing_highs(df['High'], order=3)

    supports    = [v for v in lows  if v < price * 0.98]
    resistances = [v for v in highs if v > price * 1.02]

    # Support: nearest swing low, or SMA20, or 8% below price
    if supports:
        support = max(supports)
    elif sma20 < price * 0.98:
        support = sma20
    else:
        support = price * 0.92

    # Resistance: nearest swing high, or ATR projection (for stocks at ATH)
    if resistances:
        resistance = min(resistances)
    else:
        resistance = price + atr_val * 3.5   # realistic next resistance

    return round(support, 4), round(resistance, 4)

def vol_declining(df, n=3):
    avg    = float(df['Volume'].rolling(20).mean().iloc[-1])
    recent = float(df['Volume'].iloc[-n:].mean())
    if avg == 0:
        return False
    return recent < avg * 0.85

def get_earnings(tkr):
    try:
        cal = tkr.calendar
        if cal is None: return None, None
        dates = cal.get('Earnings Date', []) if isinstance(cal, dict) else (
            cal.loc['Earnings Date'] if hasattr(cal,'loc') and 'Earnings Date' in cal.index else []
        )
        if hasattr(dates,'__iter__') and not isinstance(dates, str):
            dates = list(dates)
            date  = dates[0] if dates else None
        else:
            date = dates
        if date is None: return None, None
        ed   = pd.to_datetime(date).date()
        days = (ed - datetime.now().date()).days
        return str(ed), days
    except:
        return None, None


# ══════════════════════════════════════════════════════════════
#  NEW FILTER FUNCTIONS
# ══════════════════════════════════════════════════════════════

def get_monthly_analysis(ticker, asset=None):
    """
    Monthly chart — top-down trend confirmation.
    Returns dict: trend, candle_pct, candle_q
    Pass asset if you already have a yf.Ticker(ticker) to avoid a duplicate HTTP object.
    """
    try:
        if asset is None:
            asset = yf.Ticker(ticker)
        mdf = asset.history(period='4y', interval='1mo', auto_adjust=True, raise_errors=False)
        if mdf is None or len(mdf) < 8:
            return None
        mdf.columns = [c.capitalize() for c in mdf.columns]
        close = mdf['Close']
        opens = mdf['Open']

        sma6  = float(close.rolling(6).mean().iloc[-1])
        sma12 = float(close.rolling(12).mean().iloc[-1])
        price = float(close.iloc[-1])

        if sma6 > sma12 and price > sma12 * 0.97:
            m_trend = 'LONG'
        elif sma6 < sma12 and price < sma12 * 1.03:
            m_trend = 'SHORT'
        else:
            m_trend = None

        # Last completed monthly candle (index -2 = last closed month)
        last_open  = float(opens.iloc[-2])
        last_close = float(close.iloc[-2])
        last_pct   = round((last_close - last_open) / last_open * 100, 1) if last_open else 0

        if   last_pct <= -8:  candle_q = 'STRONG_BEAR'
        elif last_pct <= -3:  candle_q = 'BEAR'
        elif last_pct >=  8:  candle_q = 'STRONG_BULL'
        elif last_pct >=  3:  candle_q = 'BULL'
        else:                 candle_q = 'NEUTRAL'

        return {'trend': m_trend, 'candle_pct': last_pct, 'candle_q': candle_q}
    except Exception:
        return None


def get_sector_rs(ticker, df_weekly):
    """
    Relative Strength of stock vs its sector ETF (4-week return).
    Returns dict: etf, stock_ret, sector_ret, rs, rs_label, sector_trend
    """
    sector_etf = SECTOR_ETF.get(ticker.upper())
    if not sector_etf or df_weekly is None or len(df_weekly) < 5:
        return None
    try:
        stock_ret = float(
            (df_weekly['Close'].iloc[-1] / df_weekly['Close'].iloc[-5] - 1) * 100
        )
        sec_df = _SECTOR_CACHE.get(sector_etf)
        if sec_df is None:
            sec_asset = yf.Ticker(sector_etf)
            sec_df = sec_asset.history(period='3mo', interval='1wk',
                                       auto_adjust=True, raise_errors=False)
            if sec_df is None or len(sec_df) < 5:
                return None
            sec_df.columns = [c.capitalize() for c in sec_df.columns]
            _SECTOR_CACHE[sector_etf] = sec_df
        sector_ret = float(
            (sec_df['Close'].iloc[-1] / sec_df['Close'].iloc[-5] - 1) * 100
        )
        rs = round(stock_ret - sector_ret, 1)

        if   rs >=  5:  rs_label = 'STRONG+'
        elif rs >=  2:  rs_label = 'ABOVE'
        elif rs >= -2:  rs_label = 'NEUTRAL'
        elif rs >= -5:  rs_label = 'BELOW'
        else:           rs_label = 'WEAK-'

        return {
            'etf':          sector_etf,
            'stock_ret':    round(stock_ret, 1),
            'sector_ret':   round(sector_ret, 1),
            'rs':           rs,
            'rs_label':     rs_label,
            'sector_trend': 'UP' if sector_ret > 0 else 'DOWN',
        }
    except Exception:
        return None


def get_support_quality(df, support_level, tolerance=0.03):
    """
    Count weekly closes / lows within tolerance of support.
    Returns: (touches, quality)  quality = STRONG / MEDIUM / WEAK
    """
    try:
        lower = support_level * (1 - tolerance)
        upper = support_level * (1 + tolerance)
        lows  = df['Low'].values
        touches = int(sum(1 for l in lows if lower <= l <= upper))
        if   touches >= 3: quality = 'STRONG'
        elif touches >= 2: quality = 'MEDIUM'
        else:              quality = 'WEAK'
        return touches, quality
    except Exception:
        return 0, 'WEAK'


def check_level_reliability(df, level, lookback: int = 52, tolerance: float = 0.02):
    """
    Assess whether a support/resistance level is reliable.

    A level is UNRELIABLE if price crossed it in BOTH directions historically —
    the market demonstrated it does not respect this barrier.

    State-machine: tracks ABOVE → BELOW → ABOVE transitions (for support).
    One such full cycle = the level was broken both ways = UNRELIABLE.

    Returns: (label, reason)
      'CLEAN'      — price only ever approached from one side
      'TESTED'     — single violation then recovered (moderate confidence)
      'UNRELIABLE' — broken both ways; avoid basing entries here
    """
    try:
        closes = df['Close'].values[-lookback:]
        above_thresh = level * (1 + tolerance)
        below_thresh = level * (1 - tolerance)

        # State machine — track direction changes around the level
        saw_above              = False
        saw_below_after_above  = False
        saw_above_after_below  = False

        for c in closes:
            if c > above_thresh:
                if saw_below_after_above:
                    saw_above_after_below = True   # full cycle: ABOVE→BELOW→ABOVE
                if not saw_below_after_above:
                    saw_above = True
            elif c < below_thresh:
                if saw_above:
                    saw_below_after_above = True   # price dipped below after being above

        if saw_above_after_below:
            return ('UNRELIABLE',
                    f'Level {level:.2f} broken both directions — '
                    f'market did not respect it as a barrier (N.M.S.)')

        below_count = int(sum(1 for c in closes if c < below_thresh))
        above_count = int(sum(1 for c in closes if c > above_thresh))

        if saw_below_after_above and above_count > 0:
            return ('TESTED',
                    f'Level {level:.2f} violated once then recovered — '
                    f'moderate confidence ({above_count} bars above, {below_count} below)')

        return ('CLEAN',
                f'Level {level:.2f} never crossed to the other side — '
                f'strong support/resistance barrier')

    except Exception:
        return 'UNKNOWN', 'Level reliability check failed'


def check_false_breakout(df, level, direction: str = 'up',
                         n_recent: int = 4, tolerance: float = 0.005):
    """
    Detect whether recent price action constitutes a FALSE breakout (פריצת שווא).

    N.M.S. criteria (נסגר מעל/מתחת לסטנדרד):
      A valid breakout requires a weekly CLOSE above/below the level,
      not just a wick through it.

    direction='up'  → checking if price falsely broke above a resistance
    direction='down'→ checking if price falsely broke below a support

    Returns: (is_false: bool, label: str, reason: str)
      label: 'FALSE_BREAKOUT' / 'VALID_BREAKOUT' / 'NO_BREAKOUT'
    """
    try:
        recent = df.tail(n_recent)
        above_thresh = level * (1 + tolerance)
        below_thresh = level * (1 - tolerance)

        if direction == 'up':
            # Did any recent bar wick or close above level?
            any_high_above  = any(float(row['High'])  > above_thresh for _, row in recent.iterrows())
            any_close_above = any(float(row['Close']) > above_thresh for _, row in recent.iterrows())
            current_close   = float(df['Close'].iloc[-1])
            currently_above = current_close > above_thresh

            if any_high_above and not any_close_above:
                return (True, 'FALSE_BREAKOUT',
                        f'Wick above {level:.2f} but no weekly close above — N.M.S. not satisfied')
            if any_close_above and not currently_above:
                return (True, 'FALSE_BREAKOUT',
                        f'Previously closed above {level:.2f} but now back below — breakout failed')
            if any_close_above and currently_above:
                return (False, 'VALID_BREAKOUT',
                        f'Weekly close above {level:.2f} confirmed — valid breakout')
            return (False, 'NO_BREAKOUT',
                    f'Price has not yet reached {level:.2f}')

        else:  # direction == 'down'
            any_low_below   = any(float(row['Low'])   < below_thresh for _, row in recent.iterrows())
            any_close_below = any(float(row['Close']) < below_thresh for _, row in recent.iterrows())
            current_close   = float(df['Close'].iloc[-1])
            currently_below = current_close < below_thresh

            if any_low_below and not any_close_below:
                return (True, 'FALSE_BREAKOUT',
                        f'Wick below {level:.2f} but no weekly close below — N.M.S. not satisfied')
            if any_close_below and not currently_below:
                return (True, 'FALSE_BREAKOUT',
                        f'Previously closed below {level:.2f} but now back above — breakdown failed')
            if any_close_below and currently_below:
                return (False, 'VALID_BREAKOUT',
                        f'Weekly close below {level:.2f} confirmed')
            return (False, 'NO_BREAKOUT',
                    f'Price has not yet broken below {level:.2f}')

    except Exception:
        return False, 'UNKNOWN', 'False breakout check failed'


def check_level_ambiguity(df, key_level: float, atr_val: float,
                          window_factor: float = 1.5, min_sep: float = 0.015):
    """
    Detect "crowded zone" ambiguity — the ALB problem.

    When a trader debates "50% or 61.8%?" both levels are plausible →
    no single clear actionable level exists → lower-quality setup.

    Algorithm:
      1. Collect all confirmed weekly swing lows + highs from the last year.
      2. Find those within window_factor × ATR of key_level.
      3. Deduplicate: levels within min_sep (1.5%) of each other = same level.
      4. Count distinct competing levels.

    Returns: (label, n_competing, reason)
      'CLEAR'     — 0-1 other level nearby  → unambiguous entry
      'CROWDED'   — 2 levels nearby          → some ambiguity
      'AMBIGUOUS' — 3+ levels nearby         → unclear where to act
    """
    try:
        window = atr_val * window_factor
        lo     = key_level - window
        hi     = key_level + window

        # All confirmed weekly pivot lows and highs
        all_pivots = (
            [p for (_, p) in _pm_pivot_lows(df,  lookback=2)] +
            [p for (_, p) in _pm_pivot_highs(df, lookback=2)]
        )

        # Keep only those in the window but NOT the key level itself
        nearby = sorted(
            p for p in all_pivots
            if lo <= p <= hi and abs(p - key_level) / key_level > 0.005
        )

        # Deduplicate: merge pivots within min_sep of each other
        deduped = []
        for p in nearby:
            if not deduped or (p - deduped[-1]) / deduped[-1] > min_sep:
                deduped.append(p)

        n = len(deduped)
        nearby_str = ', '.join(f'{p:.2f}' for p in deduped[:4])

        if n <= 1:
            return ('CLEAR', n,
                    f'Single clear level {key_level:.2f} — no ambiguity '
                    f'({n} other level nearby)' if n else
                    f'Single clear level {key_level:.2f} — isolated entry point')
        elif n == 2:
            return ('CROWDED', n,
                    f'2 competing levels near {key_level:.2f} ({nearby_str}) — moderate ambiguity')
        else:
            return ('AMBIGUOUS', n,
                    f'{n} competing levels near {key_level:.2f} ({nearby_str}) — '
                    f'unclear where to act; look for a cleaner setup')

    except Exception:
        return 'CLEAR', 0, 'Level ambiguity check unavailable'


def check_swing_broken(df: pd.DataFrame, direction: str = 'down') -> tuple:
    """
    Trend confirmation — the MELI lesson.

    Cycles Trading principle:
      DOWNTREND (SHORT): the last confirmed weekly swing low must have been
        CLOSED through (weekly close below it, not just a wick).
      UPTREND (LONG): the last confirmed weekly swing high must have been
        CLOSED through.

    If not broken → it is a CORRECTION inside the prior trend, NOT a new
    confirmed trend. Don't trade it as a new trend; wait for confirmation.

    Returns: (confirmed: bool, label: str, reason: str)
      confirmed=True  → 'CONFIRMED'   — swing level was closed through
      confirmed=False → 'UNCONFIRMED' — swing level still holds, may be correction
    """
    try:
        closes = df['Close'].values

        if direction == 'down':
            pivots = _pm_pivot_lows(df, lookback=2)
            if not pivots:
                # No swing lows found — default to confirmed so we don't block
                return (True, 'CONFIRMED', 'No swing lows found — treating as confirmed')

            last_idx, last_low = pivots[-1]

            # Any weekly close AFTER the swing low bar that is BELOW the swing low?
            closes_after = closes[last_idx + 1: -1]   # exclude last open bar
            broken = any(c < last_low for c in closes_after)

            if broken:
                return (True, 'CONFIRMED',
                        f'Downtrend confirmed — weekly close below swing low {last_low:.2f}')
            else:
                return (False, 'UNCONFIRMED',
                        f'Swing low {last_low:.2f} intact — no weekly close below it; '
                        f'may be a correction, wait for confirmation')

        else:  # direction == 'up'
            pivots = _pm_pivot_highs(df, lookback=2)
            if not pivots:
                return (True, 'CONFIRMED', 'No swing highs found — treating as confirmed')

            last_idx, last_high = pivots[-1]
            closes_after = closes[last_idx + 1: -1]
            broken = any(c > last_high for c in closes_after)

            if broken:
                return (True, 'CONFIRMED',
                        f'Uptrend confirmed — weekly close above swing high {last_high:.2f}')
            else:
                return (False, 'UNCONFIRMED',
                        f'Swing high {last_high:.2f} intact — no weekly close above it; '
                        f'may be a correction, wait for confirmation')

    except Exception:
        return (True, 'CONFIRMED', 'Trend confirmation check unavailable')


# ══════════════════════════════════════════════════════════════
#  POSITION MANAGER — the two stop advancement rules
# ══════════════════════════════════════════════════════════════

def pm_rule1_swing(pos: dict, df: pd.DataFrame) -> dict:
    """
    Rule 1 — Swing Low / Swing High.
    LONG : advance stop when a new confirmed weekly swing low forms ABOVE the current stop.
           New stop = swing_low * (1 - PM_STOP_BUFFER).
    SHORT: advance stop when a new confirmed weekly swing high forms BELOW the current stop.
           New stop = swing_high * (1 + PM_STOP_BUFFER).
    Returns: {'advance': bool, 'new_stop': float|None, 'reason': str}
    """
    current_stop = pos['stop']
    direction    = pos['direction']

    if direction == 'LONG':
        pivots = _pm_pivot_lows(df)
        valid  = [p for (_, p) in pivots if p > current_stop]
        if not valid:
            return {'advance': False, 'new_stop': None,
                    'reason': 'Rule 1: no confirmed swing low above current stop — wait'}
        swing_price = valid[-1]   # most recent
        new_stop    = round(swing_price * (1 - PM_STOP_BUFFER), 2)
        if new_stop <= current_stop:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 1: swing low {swing_price:.2f} found but buffer too small'}
        return {'advance': True, 'new_stop': new_stop, 'rule': 'SWING_LOW',
                'reason': f'Rule 1 ✅ swing low {swing_price:.2f} → new stop {new_stop:.2f}'}

    else:  # SHORT
        pivots = _pm_pivot_highs(df)
        valid  = [p for (_, p) in pivots if p < current_stop]
        if not valid:
            return {'advance': False, 'new_stop': None,
                    'reason': 'Rule 1: no confirmed swing high below current stop — wait'}
        swing_price = valid[-1]
        new_stop    = round(swing_price * (1 + PM_STOP_BUFFER), 2)
        if new_stop >= current_stop:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 1: swing high {swing_price:.2f} found but buffer too small'}
        return {'advance': True, 'new_stop': new_stop, 'rule': 'SWING_HIGH',
                'reason': f'Rule 1 ✅ swing high {swing_price:.2f} → new stop {new_stop:.2f}'}


def pm_rule2_momentum(pos: dict, df: pd.DataFrame,
                      n_weeks: int = PM_MOMENTUM_WEEKS) -> dict:
    """
    Rule 2 — Momentum Principle.
    LONG : N consecutive weekly closes each higher than the previous.
           New stop = lowest Low of those N weeks * (1 - PM_STOP_BUFFER).
    SHORT: N consecutive weekly closes each lower than the previous.
           New stop = highest High of those N weeks * (1 + PM_STOP_BUFFER).
    Uses only confirmed (closed) candles — excludes current open week.
    Returns: {'advance': bool, 'new_stop': float|None, 'reason': str}
    """
    current_stop = pos['stop']
    direction    = pos['direction']
    closes = df['Close'].values[:-1]   # confirmed closed weeks only
    lows   = df['Low'].values[:-1]
    highs  = df['High'].values[:-1]

    if len(closes) < n_weeks + 1:
        return {'advance': False, 'new_stop': None,
                'reason': f'Rule 2: need {n_weeks+1} closed weeks, have {len(closes)}'}

    recent = closes[-(n_weeks + 1):]   # n_weeks comparisons need n_weeks+1 values

    if direction == 'LONG':
        all_higher = all(recent[i] > recent[i-1] for i in range(1, len(recent)))
        if not all_higher:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 2: no {n_weeks} consecutive higher closes yet'}
        momentum_low = float(min(lows[-n_weeks:]))
        new_stop = round(momentum_low * (1 - PM_STOP_BUFFER), 2)
        if new_stop <= current_stop:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 2: {n_weeks}-week momentum but stop not improved'}
        return {'advance': True, 'new_stop': new_stop, 'rule': 'MOMENTUM',
                'reason': f'Rule 2 ✅ {n_weeks}-week momentum → new stop {new_stop:.2f}'}

    else:  # SHORT
        all_lower = all(recent[i] < recent[i-1] for i in range(1, len(recent)))
        if not all_lower:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 2: no {n_weeks} consecutive lower closes yet'}
        momentum_high = float(max(highs[-n_weeks:]))
        new_stop = round(momentum_high * (1 + PM_STOP_BUFFER), 2)
        if new_stop >= current_stop:
            return {'advance': False, 'new_stop': None,
                    'reason': f'Rule 2: {n_weeks}-week momentum but stop not improved'}
        return {'advance': True, 'new_stop': new_stop, 'rule': 'MOMENTUM',
                'reason': f'Rule 2 ✅ {n_weeks}-week momentum → new stop {new_stop:.2f}'}


def pm_check_hits(pos: dict, df: pd.DataFrame) -> list:
    """
    Detect TP hits and stop touches against the last weekly bar.
    Mutates pos flags (tp1_hit, tp2_hit, tp3_hit, closed).
    Returns list of alert strings.
    """
    alerts = []
    last   = df.iloc[-1]
    high   = float(last['High'])
    low    = float(last['Low'])
    t      = pos['ticker']
    d      = pos['direction']

    if d == 'LONG':
        if not pos.get('tp1_hit') and high >= pos['tp1']:
            pos['tp1_hit'] = True
            alerts.append(f'🎯 TP1 HIT — {t} reached {pos["tp1"]:.2f}')
        if pos.get('tp1_hit') and not pos.get('tp2_hit') and high >= pos['tp2']:
            pos['tp2_hit'] = True
            alerts.append(f'🎯🎯 TP2 HIT — {t} reached {pos["tp2"]:.2f}')
        if pos.get('tp2_hit') and not pos.get('tp3_hit') and high >= pos['tp3']:
            pos['tp3_hit'] = True
            alerts.append(f'🎯🎯🎯 TP3 HIT — {t} reached {pos["tp3"]:.2f}')
        if low <= pos['stop']:
            pos['closed']     = True
            pos['close_date'] = datetime.now().strftime('%Y-%m-%d')
            alerts.append(f'🛑 STOP HIT — {t} low {low:.2f} ≤ stop {pos["stop"]:.2f}')
    else:  # SHORT
        if not pos.get('tp1_hit') and low <= pos['tp1']:
            pos['tp1_hit'] = True
            alerts.append(f'🎯 TP1 HIT — {t} reached {pos["tp1"]:.2f}')
        if pos.get('tp1_hit') and not pos.get('tp2_hit') and low <= pos['tp2']:
            pos['tp2_hit'] = True
            alerts.append(f'🎯🎯 TP2 HIT — {t} reached {pos["tp2"]:.2f}')
        if pos.get('tp2_hit') and not pos.get('tp3_hit') and low <= pos['tp3']:
            pos['tp3_hit'] = True
            alerts.append(f'🎯🎯🎯 TP3 HIT — {t} reached {pos["tp3"]:.2f}')
        if high >= pos['stop']:
            pos['closed']     = True
            pos['close_date'] = datetime.now().strftime('%Y-%m-%d')
            alerts.append(f'🛑 STOP HIT — {t} high {high:.2f} ≥ stop {pos["stop"]:.2f}')

    return alerts


def manage_positions(send_email: bool = False) -> list:
    """
    Weekly position check — the two Cycles Trading stop advancement rules.
    Call this at the end of every scan run (or separately).
    Returns list of actionable alert strings.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    positions = _pm_load()
    open_pos  = [p for p in positions if not p.get('closed')]

    print(f'\n{"═"*62}')
    print(f'  📂 POSITION MANAGER  —  {datetime.now().strftime("%Y-%m-%d")}')
    print(f'  {len(open_pos)} open position(s)')
    print(f'{"═"*62}')

    if not open_pos:
        print('  No open positions.\n')
        return []

    all_alerts  = []
    any_change  = False

    def _fetch(ticker):
        try:
            asset = yf.Ticker(ticker)
            df = asset.history(period='6mo', interval='1wk',
                               auto_adjust=True, raise_errors=False)
            if df is None or len(df) < 8:
                return None
            df.columns = [c.capitalize() for c in df.columns]
            return df
        except Exception:
            return None

    # Fetch all tickers in parallel
    dfs = {}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_fetch, p['ticker']): p['ticker'] for p in open_pos}
        for future in as_completed(futures):
            dfs[futures[future]] = future.result()

    for pos in open_pos:
        t  = pos['ticker']
        d  = pos['direction']
        df = dfs.get(t)

        print(f'\n  ┌─ {d} {t}  entered {pos["entry"]:.2f} on {pos["entry_date"]}')

        if df is None:
            print(f'  │  ⚠ Could not fetch data — skipped')
            print(f'  └{"─"*58}')
            continue

        current = float(df['Close'].iloc[-1])
        pnl_pct = round((current - pos['entry']) / pos['entry'] * 100, 1)
        sign    = '+' if pnl_pct >= 0 else ''
        tps     = (f'TP1{"✓" if pos.get("tp1_hit") else "○"} '
                   f'TP2{"✓" if pos.get("tp2_hit") else "○"} '
                   f'TP3{"✓" if pos.get("tp3_hit") else "○"}')
        print(f'  │  Price: {current:.2f}  PnL: {sign}{pnl_pct}%  Stop: {pos["stop"]:.2f}  {tps}')

        # ── TP / stop hits ───────────────────────────────────
        hit_alerts = pm_check_hits(pos, df)
        for a in hit_alerts:
            print(f'  │  {a}')
            all_alerts.append(a)
        if hit_alerts:
            any_change = True
        if pos.get('closed'):
            print(f'  └  🔴 CLOSED')
            continue

        # ── Rule 1 — swing low / high ────────────────────────
        r1 = pm_rule1_swing(pos, df)
        print(f'  │  {r1["reason"]}')
        if r1['advance']:
            old = pos['stop']
            pos['stop'] = r1['new_stop']
            pos.setdefault('stop_history', []).append({
                'date': datetime.now().strftime('%Y-%m-%d'),
                'from': old, 'to': r1['new_stop'], 'rule': r1['rule']
            })
            all_alerts.append(f'📈 ADVANCE STOP  {t}: {r1["reason"]}')
            any_change = True

        # ── Rule 2 — momentum ────────────────────────────────
        r2 = pm_rule2_momentum(pos, df)
        if r2['advance']:
            long_better  = pos['direction'] == 'LONG'  and r2['new_stop'] > pos['stop']
            short_better = pos['direction'] == 'SHORT' and r2['new_stop'] < pos['stop']
            if long_better or short_better:
                old = pos['stop']
                pos['stop'] = r2['new_stop']
                pos.setdefault('stop_history', []).append({
                    'date': datetime.now().strftime('%Y-%m-%d'),
                    'from': old, 'to': r2['new_stop'], 'rule': r2['rule']
                })
                print(f'  │  {r2["reason"]}')
                all_alerts.append(f'📈 ADVANCE STOP  {t}: {r2["reason"]}')
                any_change = True
            else:
                print(f'  │  Rule 2: {r2["reason"]} (already covered by Rule 1)')
        else:
            print(f'  │  {r2["reason"]}')

        print(f'  └  Active stop: {pos["stop"]:.2f}')

    if any_change:
        _pm_save(positions)
        print(f'\n  💾 positions.json updated.')

    if all_alerts:
        print(f'\n  ╔{"═"*58}╗')
        print(f'  ║  ACTION REQUIRED ({len(all_alerts)} alert(s))')
        for a in all_alerts:
            print(f'  ║   {a}')
        print(f'  ╚{"═"*58}╝')
        if send_email and all_alerts:
            body = '\n'.join(all_alerts)
            send_email_summary(
                f'🔔 Cycles Position Alert — {datetime.now().strftime("%Y-%m-%d")}',
                body
            )
    print()
    return all_alerts


def list_positions(show_closed: bool = False):
    """Print a compact table of all (open) positions."""
    positions = _pm_load()
    rows = [p for p in positions if show_closed or not p.get('closed')]
    if not rows:
        print('  No positions found.')
        return
    print(f'\n  {"ID":<28} {"Dir":<6} {"Entry":>7} {"Stop":>7} '
          f'{"TP1":>7} {"TP2":>7} {"TP3":>7}  TPs')
    print(f'  {"─"*28} {"─"*6} {"─"*7} {"─"*7} {"─"*7} {"─"*7} {"─"*7}  {"─"*5}')
    for p in rows:
        tps = ('T1✓' if p.get('tp1_hit') else 'T1○') + \
              ('T2✓' if p.get('tp2_hit') else 'T2○') + \
              ('T3✓' if p.get('tp3_hit') else 'T3○')
        closed_mark = ' [CLOSED]' if p.get('closed') else ''
        print(f'  {p["id"]:<28} {p["direction"]:<6} {p["entry"]:>7.2f} '
              f'{p["stop"]:>7.2f} {p["tp1"]:>7.2f} {p["tp2"]:>7.2f} '
              f'{p["tp3"]:>7.2f}  {tps}{closed_mark}')
    print()


# ══════════════════════════════════════════════════════════════
#  TIME HORIZON ESTIMATOR
# ══════════════════════════════════════════════════════════════

def calc_macd(df):
    """
    Calculate MACD (12/26/9) on weekly closes.
    Returns dict: macd_val, signal_val, histogram, trend, cross, divergence
    """
    try:
        closes = df['Close'].dropna()
        if len(closes) < 30:
            return None
        ema12 = closes.ewm(span=12, adjust=False).mean()
        ema26 = closes.ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        sig   = macd.ewm(span=9, adjust=False).mean()
        hist  = macd - sig

        macd_now  = round(float(macd.iloc[-1]), 4)
        sig_now   = round(float(sig.iloc[-1]),  4)
        hist_now  = round(float(hist.iloc[-1]), 4)
        hist_prev = round(float(hist.iloc[-2]), 4)

        # Trend: MACD above or below signal line
        trend = 'BULL' if macd_now > sig_now else 'BEAR'

        # Cross detection (last 2 bars)
        cross = None
        if hist_prev < 0 and hist_now > 0:
            cross = 'GOLDEN'   # bullish crossover
        elif hist_prev > 0 and hist_now < 0:
            cross = 'DEATH'    # bearish crossover

        # Bullish divergence: price making lower low but MACD making higher low
        price_ll = closes.iloc[-1] < closes.iloc[-5]
        macd_hl  = float(macd.iloc[-1]) > float(macd.iloc[-5])
        divergence = 'BULL_DIV' if (price_ll and macd_hl) else None

        return {
            'macd':       macd_now,
            'signal':     sig_now,
            'histogram':  hist_now,
            'trend':      trend,
            'cross':      cross,
            'divergence': divergence,
        }
    except Exception:
        return None


def calc_bollinger(df, period=20):
    """
    Calculate Bollinger Bands (20, 2σ) on weekly closes.
    Returns dict: upper, middle, lower, pct_b, squeeze, position
    """
    try:
        closes = df['Close'].dropna()
        if len(closes) < period:
            return None
        ma    = closes.rolling(period).mean()
        std   = closes.rolling(period).std()
        upper = ma + 2 * std
        lower = ma - 2 * std

        price    = float(closes.iloc[-1])
        upper_v  = round(float(upper.iloc[-1]), 4)
        lower_v  = round(float(lower.iloc[-1]), 4)
        mid_v    = round(float(ma.iloc[-1]),    4)
        band_w   = upper_v - lower_v

        # %B: where price sits in the band (0=lower, 1=upper, <0 or >1 = outside)
        pct_b = round((price - lower_v) / band_w, 3) if band_w > 0 else 0.5

        # Squeeze: band width < 5% of price = low volatility, breakout imminent
        squeeze = band_w / price < 0.05 if price > 0 else False

        # Position label
        if pct_b <= 0.1:
            position = 'NEAR_LOWER'    # oversold territory → potential reversal
        elif pct_b >= 0.9:
            position = 'NEAR_UPPER'    # overbought territory
        elif squeeze:
            position = 'SQUEEZE'       # tight bands → breakout coming
        else:
            position = 'MID'

        return {
            'upper':    upper_v,
            'middle':   mid_v,
            'lower':    lower_v,
            'pct_b':    pct_b,
            'squeeze':  squeeze,
            'position': position,
        }
    except Exception:
        return None


def estimate_time_horizon(entry, target, atr_val):
    """
    Estimate weeks to reach T1 based on weekly ATR.
    Assumes stock covers ~60% of its weekly ATR per week on average.
    Returns (est_weeks, horizon_code, display_label, color)
    """
    dist = abs(target - entry)
    weekly_progress = max(atr_val * 0.60, 0.001)
    weeks = dist / weekly_progress

    if   weeks <= 2.5:
        return round(weeks, 1), 'WEEKLY',  '⚡ שבועי',    '#3fb950', '1–2 שבועות'
    elif weeks <= 6:
        return round(weeks, 1), 'MONTHLY', '📅 חודשי',    '#58a6ff', '3–6 שבועות'
    elif weeks <= 12:
        return round(weeks, 1), 'MEDIUM',  '📈 בינוני',   '#d29922', '2–3 חודשים'
    else:
        return round(weeks, 1), 'LONG',    '🎯 ארוך טווח','#8b949e', '3+ חודשים'


# ══════════════════════════════════════════════════════════════
#  FACTOR REGISTRY — each factor is a pure function (r) → (delta, label, explanation)
#  To add Factor 17: write a _factor_xxx function, append to FACTORS list below.
#  To disable a factor: remove it from FACTORS (no other change needed).
# ══════════════════════════════════════════════════════════════

def _factor_rsi(r):
    is_long = 'LONG' in r['Dir']
    v = r['RSI']
    if is_long:
        if 30 <= v <= 50:   return +16, "RSI", f"RSI {v} — ideal pullback zone (30–50)"
        elif 50 < v <= 58:  return +8,  "RSI", f"RSI {v} — acceptable, not overbought"
        elif v < 30:        return +5,  "RSI", f"RSI {v} — oversold bounce potential"
        else:               return -8,  "RSI", f"RSI {v} — elevated, less room to run"
    else:
        if 55 <= v <= 72:   return +16, "RSI", f"RSI {v} — ideal bounce zone (55–72)"
        elif 50 <= v < 55:  return +8,  "RSI", f"RSI {v} — acceptable, not oversold"
        elif v > 72:        return +5,  "RSI", f"RSI {v} — overbought, reversal likely"
        else:               return -8,  "RSI", f"RSI {v} — low, bearish case weaker"

def _factor_rr(r):
    v = r['R:R']
    if v >= 4.0:   return +14, "R:R Ratio", f"R:R 1:{v} — excellent room to target"
    elif v >= 3.0: return +10, "R:R Ratio", f"R:R 1:{v} — strong setup"
    elif v >= 2.5: return +6,  "R:R Ratio", f"R:R 1:{v} — solid"
    else:          return +2,  "R:R Ratio", f"R:R 1:{v} — minimum threshold"

def _factor_volume(r):
    if r['Vol'] == 'OK': return +10, "Volume", "Volume declining near level — accumulation signal"
    else:                return -6,  "Volume", "Volume not declining — less conviction"

def _factor_entry_distance(r):
    is_long = 'LONG' in r['Dir']
    key_level = r['Support'] if is_long else r['Resist']
    dist_pct  = abs(r['Price'] - key_level) / r['Price'] * 100
    if dist_pct <= 2:   return +14, "Entry Distance", f"Only {dist_pct:.1f}% from key level — near-perfect entry"
    elif dist_pct <= 5: return +9,  "Entry Distance", f"{dist_pct:.1f}% from key level — good entry"
    elif dist_pct <= 8: return +4,  "Entry Distance", f"{dist_pct:.1f}% from key level — acceptable"
    elif dist_pct <= 12:return  0,  "Entry Distance", f"{dist_pct:.1f}% from key level — stretched"
    else:               return -8,  "Entry Distance", f"{dist_pct:.1f}% from key level — too far"

def _factor_earnings(r):
    earn = r['Earn']
    if earn == 'SOON!':        return -14, "Earnings Risk", "Earnings report soon — high volatility risk"
    elif earn and earn != '-': return +3,  "Earnings Risk", f"Next earnings: {earn} — safe window"
    else:                      return +5,  "Earnings Risk", "No earnings concern"

def _factor_setup_quality(r):
    v = r.get('_score', 2.0)
    if v >= 6.0:   return +8,  "Setup Quality", f"Setup score {v:.1f} — high-quality signal"
    elif v >= 4.0: return +4,  "Setup Quality", f"Setup score {v:.1f} — good signal"
    elif v >= 2.5: return +1,  "Setup Quality", f"Setup score {v:.1f} — average"
    else:          return -3,  "Setup Quality", f"Setup score {v:.1f} — weak signal"

def _factor_stop_distance(r):
    stop_pct = abs(r['Entry'] - r['Stop']) / r['Entry'] * 100
    if stop_pct <= 4:   return +6, "Stop Distance", f"Stop {stop_pct:.1f}% away — tight, controlled risk"
    elif stop_pct <= 8: return +3, "Stop Distance", f"Stop {stop_pct:.1f}% away — normal"
    elif stop_pct <= 12:return  0, "Stop Distance", f"Stop {stop_pct:.1f}% away — wide"
    else:               return -5, "Stop Distance", f"Stop {stop_pct:.1f}% away — very wide stop"

def _factor_monthly_trend(r):
    is_long  = 'LONG' in r['Dir']
    m_trend  = r.get('MonthlyTrend')
    m_candle = r.get('MonthlyCandle')
    if m_trend is None:
        return None  # factor not applicable → skip
    if is_long:
        if m_trend == 'LONG' and m_candle in ('BULL', 'STRONG_BULL', 'NEUTRAL'):
            return +18, "Monthly Trend", f"Monthly trend LONG, last candle {m_candle} — full alignment"
        elif m_trend == 'LONG':
            return +8,  "Monthly Trend", f"Monthly trend LONG despite bearish candle ({m_candle})"
        elif m_trend == 'SHORT' and m_candle in ('NEUTRAL', 'BULL'):
            return -18, "Monthly Trend", f"Monthly trend SHORT — weekly LONG is counter-trend"
        else:
            return -30, "Monthly Trend", f"Monthly SHORT + {m_candle} — strong warning"
    else:
        if m_trend == 'SHORT' and m_candle in ('BEAR', 'STRONG_BEAR', 'NEUTRAL'):
            return +18, "Monthly Trend", f"Monthly trend SHORT, last candle {m_candle} — full alignment"
        elif m_trend == 'SHORT':
            return +8,  "Monthly Trend", f"Monthly trend SHORT, candle mixed ({m_candle})"
        elif m_trend == 'LONG':
            return -25, "Monthly Trend", f"Monthly trend LONG — SHORT is counter-trend"
        else:
            return 0,   "Monthly Trend", "Monthly neutral — no directional confirmation"

def _factor_sector_rs(r):
    is_long   = 'LONG' in r['Dir']
    rs_label  = r.get('SectorRS')
    sec_trend = r.get('SectorTrend')
    if not rs_label:
        return None  # not applicable (crypto/commodity/intl)
    if is_long:
        if   rs_label == 'STRONG+': d = +14; ex = f"Outperforming sector by {r.get('RS_pct',0)}% — strong RS"
        elif rs_label == 'ABOVE':   d = +7;  ex = "Stock above sector — positive RS"
        elif rs_label == 'NEUTRAL': d = +2;  ex = "Stock in line with sector"
        elif rs_label == 'BELOW':   d = -10; ex = "Stock underperforming sector — weak RS"
        else:                       d = -18; ex = "Stock significantly weaker — avoid"
        if sec_trend == 'DOWN': d -= 8; ex += " | sector in downtrend"
    else:
        if   rs_label == 'WEAK-':   d = +14; ex = "Stock weaker than sector — SHORT aligned"
        elif rs_label == 'BELOW':   d = +7;  ex = "Stock underperforming — SHORT confirmed"
        elif rs_label == 'NEUTRAL': d = +2;  ex = "Sector neutral"
        elif rs_label == 'ABOVE':   d = -10; ex = "Stock outperforming — SHORT risky"
        else:                       d = -18; ex = "Stock leading sector — SHORT very risky"
        if sec_trend == 'UP': d -= 8; ex += " | sector in uptrend"
    return d, "Sector RS", ex

def _factor_support_quality(r):
    sup_q = r.get('SupportQ')
    if not sup_q:
        return None
    touches = r.get('SupportTouches', 1)
    if   sup_q == 'STRONG': return +10, "Support Quality", f"Support tested {touches}x — proven level"
    elif sup_q == 'MEDIUM': return +4,  "Support Quality", f"Support tested {touches}x — reasonable level"
    else:                   return -8,  "Support Quality", "Support tested once — unproven"

def _factor_atr_volatility(r):
    v = r.get('ATR_pct', 0)
    if v <= 0: return None
    if v > 12:   return -18, "Volatility (ATR)", f"ATR {v:.1f}% — extreme volatility"
    elif v > 8:  return -10, "Volatility (ATR)", f"ATR {v:.1f}% — high volatility, smaller position"
    elif v > 5:  return  0,  "Volatility (ATR)", f"ATR {v:.1f}% — normal volatility"
    else:        return +4,  "Volatility (ATR)", f"ATR {v:.1f}% — low volatility, easy stop"

def _factor_earnings_zone(r):
    if r.get('Earn') == 'APPROACHING' and r.get('EarnDays'):
        return -8, "Earnings Zone", f"Earnings in {r['EarnDays']} days — event risk (15–30d zone)"
    return None

def _factor_late_entry(r):
    if 'LONG' not in r['Dir']: return None
    v = r.get('LateEntry', 0)
    if v > 8:   return -15, "Late Entry", f"Price {v:.1f}% above support — likely missed retest"
    elif v > 5: return -8,  "Late Entry", f"Price {v:.1f}% above support — entry less optimal"
    return None

def _factor_fundamentals(r):
    fund = r.get('_fundamental')
    if not fund: return None
    sig = fund.get('signal', 'HOLD')
    cons = fund.get('consensus', '—')
    tgt  = fund.get('target', '?')
    if sig == 'BUY':
        return +15, "Fundamentals", f"Analyst BUY (conf {fund.get('conf')}%) — {cons}, target ${tgt}"
    elif sig == 'SELL':
        return -15, "Fundamentals", f"Analyst SELL (conf {fund.get('conf')}%) — {cons}"
    else:
        return 0,   "Fundamentals", f"Analyst HOLD — {cons}"

def _factor_macd(r):
    macd = r.get('_macd')
    if not macd: return None
    is_long = 'LONG' in r['Dir']
    cross = macd.get('cross')
    trend = macd.get('trend')
    div   = macd.get('divergence')
    if   cross == 'GOLDEN' and is_long:     d = +12; ex = 'MACD Golden Cross — bullish momentum confirmed'
    elif cross == 'DEATH'  and not is_long: d = +12; ex = 'MACD Death Cross — bearish momentum confirmed'
    elif cross == 'GOLDEN' and not is_long: d = -12; ex = 'MACD Golden Cross — conflicts with SHORT'
    elif cross == 'DEATH'  and is_long:     d = -12; ex = 'MACD Death Cross — conflicts with LONG'
    elif trend == 'BULL'   and is_long:     d = +6;  ex = 'MACD above signal line — bullish trend'
    elif trend == 'BEAR'   and not is_long: d = +6;  ex = 'MACD below signal line — bearish trend'
    elif trend == 'BEAR'   and is_long:     d = -6;  ex = 'MACD below signal line — weak LONG momentum'
    else:                                   d =  0;  ex = 'MACD neutral'
    if div == 'BULL_DIV' and is_long:
        d += 8; ex += ' + Bullish divergence'
    return d, "MACD", ex

def _factor_bollinger(r):
    boll = r.get('_boll')
    if not boll: return None
    is_long = 'LONG' in r['Dir']
    pos     = boll.get('position')
    pct_b   = boll.get('pct_b', 0.5)
    squeeze = boll.get('squeeze', False)
    if   pos == 'NEAR_LOWER' and is_long:     return +10, "Bollinger Bands", f'Near lower band (%B={pct_b:.2f}) — oversold, good LONG'
    elif pos == 'NEAR_UPPER' and not is_long: return +10, "Bollinger Bands", f'Near upper band (%B={pct_b:.2f}) — overbought, good SHORT'
    elif pos == 'NEAR_UPPER' and is_long:     return -8,  "Bollinger Bands", f'Near upper band (%B={pct_b:.2f}) — overbought, risky LONG'
    elif pos == 'NEAR_LOWER' and not is_long: return -8,  "Bollinger Bands", f'Near lower band (%B={pct_b:.2f}) — oversold, risky SHORT'
    elif squeeze:                             return +5,  "Bollinger Bands", 'Bollinger Squeeze — breakout imminent'
    else:                                     return  0,  "Bollinger Bands", f'Mid-band (%B={pct_b:.2f}) — neutral'


def _factor_level_reliability(r):
    """
    Factor 17 — Level Reliability + False Breakout (N.M.S.).

    Two sub-checks:
      A) Was the key level broken in BOTH directions historically?
         If yes → UNRELIABLE → heavy penalty (-18).
      B) Is the current move a false breakout (wick through level, no weekly close)?
         If yes → N.M.S. not satisfied → penalty (-12).

    A level that scores CLEAN and has a VALID or NO breakout gets a bonus.
    """
    rel = r.get('_level_rel', 'UNKNOWN')
    fb  = r.get('_false_breakout', False)
    fb_label = r.get('_fb_label', '')

    # Sub-check A — level broken both ways
    if rel == 'UNRELIABLE':
        return (-18, 'Level Reliability',
                'Level broken both directions — market never respected it')

    # Sub-check B — false breakout (N.M.S. not met)
    if fb and fb_label == 'FALSE_BREAKOUT':
        return (-12, 'Level Reliability',
                'False breakout — wick through level but no weekly close (N.M.S. criteria)')

    # Positive: clean level, valid or no breakout yet
    if rel == 'CLEAN':
        return (+10, 'Level Reliability',
                'Level never broken to the other side — strong, reliable barrier')
    if rel == 'TESTED':
        return (+4, 'Level Reliability',
                'Level tested once then held — moderate confidence')

    return None   # UNKNOWN — no opinion


def _factor_level_ambiguity(r):
    """
    Factor 18 — Level Ambiguity (the ALB lesson).

    When multiple competing support/resistance levels cluster near the entry,
    the trader cannot point to ONE clear level → lower conviction setup.

    Expert rule: "אין רמה אחת מובהקת שבה ניתן לפעול → לחפש הזדמנות אחרת"

      CLEAR     → +8   (single unambiguous level — high conviction)
      CROWDED   → -6   (two levels — moderate ambiguity)
      AMBIGUOUS → -16  (three+ levels — look elsewhere)
    """
    amb   = r.get('_level_amb', 'CLEAR')
    n     = r.get('_level_amb_n', 0)
    if   amb == 'CLEAR':
        return (+8,  'Level Clarity',
                f'Single clear entry level — no competing levels nearby ({n} other)')
    elif amb == 'CROWDED':
        return (-6,  'Level Clarity',
                f'2 competing levels in zone — some ambiguity about where to act')
    elif amb == 'AMBIGUOUS':
        return (-16, 'Level Clarity',
                f'{n} competing levels nearby — unclear entry point (seek cleaner setup)')
    return None


def check_fibonacci_zone(df, direction: str, price: float):
    """
    Compute the Fibonacci retracement zone for the current setup.
    Uses the last 52-bar swing high/low to measure the prior move.

    Returns (zone, ret_pct, swing_low, swing_high, fib_levels_dict)
    zone: 'GOLDEN_ZONE' | 'SHALLOW' | 'DEEP' | 'TOO_DEEP' | 'NO_RETRACEMENT' | 'UNKNOWN'
    """
    try:
        look = df.tail(min(52, len(df)))
        if direction == 'LONG':
            swing_high = float(look['High'].max())
            hi_idx     = look['High'].idxmax()
            before_hi  = look.loc[:hi_idx]
            if len(before_hi) < 5:
                return 'UNKNOWN', 0, 0, 0, {}
            swing_low  = float(before_hi['Low'].min())
            move       = swing_high - swing_low
            if move <= 0:
                return 'UNKNOWN', 0, 0, 0, {}
            retracement = (swing_high - price) / move
        else:  # SHORT
            swing_low = float(look['Low'].min())
            lo_idx    = look['Low'].idxmin()
            before_lo = look.loc[:lo_idx]
            if len(before_lo) < 5:
                return 'UNKNOWN', 0, 0, 0, {}
            swing_high = float(before_lo['High'].max())
            move       = swing_high - swing_low
            if move <= 0:
                return 'UNKNOWN', 0, 0, 0, {}
            retracement = (price - swing_low) / move

        ret_pct = retracement * 100
        span    = swing_high - swing_low
        # Fib levels (from swing_low)
        fib_levels = {
            '23.6': round(swing_low + span * 0.764, 2) if direction == 'LONG' else round(swing_high - span * 0.764, 2),
            '38.2': round(swing_low + span * 0.618, 2) if direction == 'LONG' else round(swing_high - span * 0.618, 2),
            '50.0': round((swing_high + swing_low) / 2, 2),
            '61.8': round(swing_low + span * 0.382, 2) if direction == 'LONG' else round(swing_high - span * 0.382, 2),
            '78.6': round(swing_low + span * 0.214, 2) if direction == 'LONG' else round(swing_high - span * 0.214, 2),
        }

        if 36 <= ret_pct <= 63:
            zone = 'GOLDEN_ZONE'
        elif 20 <= ret_pct < 36:
            zone = 'SHALLOW'
        elif 63 < ret_pct <= 80:
            zone = 'DEEP'
        elif ret_pct > 80:
            zone = 'TOO_DEEP'
        else:
            zone = 'NO_RETRACEMENT'

        return zone, round(ret_pct, 1), swing_low, swing_high, fib_levels

    except Exception:
        return 'UNKNOWN', 0, 0, 0, {}


def _factor_fibonacci(r):
    """
    Factor 20 — Fibonacci Retracement Zone.

    Cycles Trading insight (from Discord Q&A, May–Jun 2026):
    Students repeatedly asked about Fibonacci. Expert consensus:
      - Golden Zone 38.2%–61.8%: ideal retracement entry area       → +8
      - Shallow (<38.2%): price hasn't pulled back enough yet        →  0
      - Deep (61.8%–78.6%): valid but weakening setup               → -5
      - Too deep (>78.6%): likely trend change, not retracement     → -12
      - No retracement (<23.6%): entering too early, before pullback → -3

    Rule: "האם אפשר להסתמך רק על פיבו ללא תמיכה? רק אם יש חפיפה בין
           רמת תמיכה לאזור פיבו — אז זה מחזק."
    """
    zone    = r.get('_fib_zone', 'UNKNOWN')
    ret_pct = r.get('_fib_ret_pct', 0)

    if zone == 'UNKNOWN':
        return None
    if zone == 'GOLDEN_ZONE':
        return (+8,  f'Fib {ret_pct:.0f}%: Golden Zone',
                f'Price in golden Fibonacci zone ({ret_pct:.0f}% retracement) — ideal entry area 38.2%–61.8%')
    if zone == 'SHALLOW':
        return ( 0,  f'Fib {ret_pct:.0f}%: Shallow',
                f'Shallow retracement ({ret_pct:.0f}%) — price hasn\'t pulled back to fib levels yet; consider waiting')
    if zone == 'DEEP':
        return (-5,  f'Fib {ret_pct:.0f}%: Deep',
                f'Deep retracement ({ret_pct:.0f}%) — near 78.6% level; still valid but signal is weakening')
    if zone == 'TOO_DEEP':
        return (-12, f'Fib {ret_pct:.0f}%: Too Deep',
                f'Beyond 78.6% ({ret_pct:.0f}%) — retracement suggests possible trend reversal, not correction')
    if zone == 'NO_RETRACEMENT':
        return (-3,  f'Fib {ret_pct:.0f}%: No Retrace',
                f'Minimal retracement ({ret_pct:.0f}%) — entering too early before a proper Fibonacci pullback')
    return None


def _factor_trend_confirmation(r):
    """
    Factor 19 — Trend Confirmation (the MELI lesson).

    Cycles Trading principle: the last confirmed weekly swing low (SHORT) or
    swing high (LONG) must have been CLOSED through — not merely wicked.
    If the swing level still holds, the move is a CORRECTION inside the prior
    trend, not a new confirmed trend. Wait for the close; don't anticipate.

    Expert rule: "כל עוד השפל האחרון מחזיק, אין אינדקציה ראשונית לשינוי מגמה"

      CONFIRMED   → +10  (swing level was closed through — real trend)
      UNCONFIRMED → -18  (swing level holds — likely correction, wait)
    """
    confirmed = r.get('_trend_confirmed', True)
    label     = r.get('_trend_conf_label', 'CONFIRMED')
    direction = r.get('Direction', 'LONG')
    swing_word = 'low' if direction == 'SHORT' else 'high'

    if confirmed:
        return (+10, f'TrendConf: {label}',
                f'Trend structure confirmed — last swing {swing_word} closed through')
    else:
        return (-18, f'TrendConf: {label}',
                f'Last swing {swing_word} not closed through — may be a correction; '
                f'wait for weekly close confirmation before entering')


# ── Registry: add / remove / reorder factors here ────────────
FACTORS = [
    _factor_rsi,
    _factor_rr,
    _factor_volume,
    _factor_entry_distance,
    _factor_earnings,
    _factor_setup_quality,
    _factor_stop_distance,
    _factor_monthly_trend,
    _factor_sector_rs,
    _factor_support_quality,
    _factor_atr_volatility,
    _factor_earnings_zone,
    _factor_late_entry,
    _factor_fundamentals,
    _factor_macd,
    _factor_bollinger,
    _factor_level_reliability,   # Factor 17 — Level Reliability + N.M.S.
    _factor_level_ambiguity,     # Factor 18 — Level Ambiguity (crowded zone)
    _factor_trend_confirmation,  # Factor 19 — Trend Confirmation (swing broken by weekly close)
    _factor_fibonacci,           # Factor 20 — Fibonacci Retracement Zone (Discord lessons May–Jun 2026)
]

def calc_probability(r):
    """
    Iterate FACTORS registry. Each factor returns (delta, label, explanation) or None to skip.
    Base 50, capped [15, 92]. To add Factor 17: write _factor_xxx, append to FACTORS.
    """
    score   = 50.0
    factors = []
    for fn in FACTORS:
        result = fn(r)
        if result is None:
            continue
        d, label, explain = result
        score += d
        factors.append((label, d, explain))
    probability = max(15, min(92, round(score)))
    return probability, factors


# ══════════════════════════════════════════════════════════════
#  CORE ANALYSIS  (one function handles stocks + crypto, LONG + SHORT)
# ══════════════════════════════════════════════════════════════

def clean_ticker(ticker):
    """Return display-friendly ticker name."""
    import re
    return re.sub(r'(-USD|=F|\.[A-Z]+)$', '', ticker)

def send_email_summary(subject, body_text, body_html=None):
    """
    Send scan summary to omarearly@gmail.com via Gmail SMTP (TLS).
    Uses App Password — set EMAIL_APP_PASSWORD in environment or below.
    How to get App Password: Google Account → Security → App Passwords.
    """
    import smtplib, os
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    SENDER   = 'omarearly@gmail.com'
    RECEIVER = 'omarearly@gmail.com'
    APP_PWD  = os.environ.get('GMAIL_APP_PASSWORD', '')  # set env var or hard-code here

    if not APP_PWD:
        print('  ⚠ Email: GMAIL_APP_PASSWORD not set — skipping email notification.')
        print('    Set it with:  set GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx')
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = SENDER
        msg['To']      = RECEIVER
        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
        if body_html:
            msg.attach(MIMEText(body_html, 'html', 'utf-8'))
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.ehlo()
            server.starttls()
            server.login(SENDER, APP_PWD)
            server.sendmail(SENDER, RECEIVER, msg.as_string())
        print('  ✅ Email summary sent to omarearly@gmail.com')
        return True
    except Exception as e:
        print(f'  ⚠ Email send failed: {e}')
        return False


def get_fundamental_analysis(ticker, info=None):
    """
    Fetches fundamental data directly from Yahoo Finance via yfinance.
    Pass `info` (from asset.info already fetched in analyze()) to skip the HTTP call.
    Returns dict with: signal, conf, consensus, target, upside, caveats, bullets, scores.
    Only runs for US stocks (not crypto/commodity/TASE/INTL).
    """
    try:
        if info is None:
            info = yf.Ticker(ticker).info or {}
        info = info or {}

        # ── Analyst consensus → signal ────────────────────────
        rec_key = (info.get('recommendationKey') or '').lower()
        rec_map = {
            'strong_buy': ('BUY',  90),
            'buy':        ('BUY',  75),
            'hold':       ('HOLD', 55),
            'underperform': ('SELL', 40),
            'sell':       ('SELL', 30),
        }
        signal, conf = rec_map.get(rec_key, ('HOLD', 50))

        # ── Price target ──────────────────────────────────────
        a_target = info.get('targetMeanPrice') or info.get('targetMedianPrice')
        a_curr   = info.get('currentPrice') or info.get('regularMarketPrice')
        upside   = None
        if a_target and a_curr and a_curr > 0:
            upside = round((a_target - a_curr) / a_curr * 100, 1)

        # ── Analyst count & consensus label ──────────────────
        n_analysts = info.get('numberOfAnalystOpinions') or 0
        a_cons = f"{rec_key.replace('_',' ').title()} ({n_analysts} analysts)" if rec_key else '—'

        # ── Quick fundamental caveats ─────────────────────────
        caveats = []
        pe = info.get('trailingPE') or info.get('forwardPE')
        if pe and pe > 50:
            caveats.append(f'High P/E: {pe:.0f}x')
        debt_eq = info.get('debtToEquity')
        if debt_eq and debt_eq > 200:
            caveats.append(f'High D/E ratio: {debt_eq:.0f}%')
        short_pct = info.get('shortPercentOfFloat') or 0
        if short_pct > 0.20:
            caveats.append(f'High short interest: {short_pct*100:.0f}%')
        if upside and upside < -5:
            caveats.append(f'Analyst target below price ({upside:+.1f}%)')

        # ── Summary bullets ───────────────────────────────────
        bullets = []
        sector  = info.get('sector', '')
        industry= info.get('industry', '')
        mkt_cap = info.get('marketCap', 0)
        if mkt_cap:
            cap_str = f"${mkt_cap/1e9:.1f}B" if mkt_cap >= 1e9 else f"${mkt_cap/1e6:.0f}M"
            bullets.append(f"{sector} / {industry} — {cap_str} market cap")
        if upside is not None:
            bullets.append(f"Analyst target ${a_target:.2f} → {upside:+.1f}% upside")
        rev_growth = info.get('revenueGrowth')
        if rev_growth is not None:
            bullets.append(f"Revenue growth: {rev_growth*100:.1f}%")

        scores = {
            'pe':          pe,
            'debtToEquity': debt_eq,
            'revenueGrowth': rev_growth,
            'shortPct':    short_pct,
        }

        return {
            'signal':    signal,
            'conf':      int(conf),
            'consensus': a_cons,
            'target':    a_target,
            'upside':    upside,
            'caveats':   caveats[:3],
            'bullets':   bullets[:3],
            'scores':    scores,
        }
    except Exception as e:
        print(f'  ⚠ Fundamental analysis failed for {ticker}: {e}')
        return None


def is_hard_blocked(direction, m_analysis):
    """
    Returns (True, reason) if this setup violates a hard-block rule
    and should be completely excluded — not even shown in Watchlist.
    Learned from: CS-001 BKR, CS-003 CVX, CS-005 Ford F.
    """
    if not m_analysis:
        return False, ''
    candle = m_analysis.get('candle_q', '') or ''
    for blk_dir, blk_candle, blk_reason in HARD_BLOCKS:
        if direction == blk_dir and blk_candle in candle:
            return True, blk_reason
    return False, ''


def get_traffic_light(prob, r):
    """
    Returns (color, label, reasons[]) — a traffic light for each setup.
    GREEN  = take the trade
    YELLOW = consider carefully
    RED    = do not take
    """
    red_flags   = []
    green_flags = []

    if r.get('Earn') == 'SOON!':
        red_flags.append('Earnings imminent (<14d)')
    if r.get('Earn') == 'APPROACHING':
        red_flags.append('Earnings approaching (15-30d)')
    if r.get('HighVol'):
        red_flags.append(f'High Volatility ATR {r.get("ATR_pct",0)}%')
    if r.get('LateEntry', 0) > 8:
        red_flags.append(f'Late Entry +{r.get("LateEntry",0)}% from level')
    if r.get('SectorRS') in ('WEAK-', 'BELOW'):
        red_flags.append(f'Sector RS: {r.get("SectorRS")}')
    m_trend = r.get('MonthlyTrend')
    direction = 'LONG' if '▲' in r.get('Dir','') else 'SHORT'
    if m_trend == 'SHORT' and direction == 'LONG':
        red_flags.append('Monthly trend vs direction')
    if m_trend == 'LONG' and direction == 'SHORT':
        red_flags.append('Monthly trend vs direction')

    if r.get('SupportQ') == 'STRONG':
        green_flags.append('Strong support level')
    if r.get('SectorRS') in ('STRONG+', 'ABOVE'):
        green_flags.append(f'Sector RS: {r.get("SectorRS")}')
    if r.get('MonthlyTrend') == direction[:4]:
        green_flags.append('Monthly aligned')

    n_red = len(red_flags)
    if prob >= 70 and n_red == 0:
        return 'GREEN',  '🟢 קח את העסקה',    green_flags, red_flags
    elif prob >= 65 and n_red <= 1:
        return 'YELLOW', '🟡 שקול בזהירות',   green_flags, red_flags
    else:
        return 'RED',    '🔴 אל תיכנס',       green_flags, red_flags


def calc_position_size(portfolio_size, entry, stop, atr_pct=0, high_vol=False):
    """
    Calculate proper position size with three safeguards:
    1. Risk-based sizing   — never lose more than RISK_PCT of portfolio on one trade
    2. Volatility scaling  — halve position size when ATR > 8% (HIGH_VOLATILITY)
    3. Max position cap    — never put more than MAX_POS_PCT of portfolio in one position

    Returns: units, pos_val, risk_amt, pos_pct, was_capped, cap_reason
    """
    risk_u = abs(entry - stop)
    if risk_u <= 0 or entry <= 0:
        return 0, 0, 0, 0, False, ''

    # Step 1 — base risk amount (1% of portfolio)
    base_risk = portfolio_size * RISK_PCT

    # Step 2 — halve for high-volatility stocks (ATR > 8%)
    if high_vol:
        base_risk *= 0.50

    risk_amt = base_risk
    units    = risk_amt / risk_u
    pos_val  = units * entry

    # Step 3 — cap at MAX_POS_PCT of portfolio
    max_pos_val = portfolio_size * MAX_POS_PCT
    was_capped  = False
    cap_reason  = ''
    if pos_val > max_pos_val:
        was_capped = True
        cap_reason = f'Capped at {int(MAX_POS_PCT*100)}% of portfolio'
        pos_val  = max_pos_val
        units    = pos_val / entry
        risk_amt = units * risk_u   # actual risk after cap

    pos_pct = round(pos_val / portfolio_size * 100, 1)
    return round(units, 4), round(pos_val, 2), round(risk_amt, 2), pos_pct, was_capped, cap_reason


# ── ResultBuilder helpers — called by analyze() for each setup ───────────────

def _build_setup_dict(direction, ticker, price, rsi_val, support, resistance,
                      entry, stop, target, rratio, units, pos_val, risk_amt,
                      pos_pct, was_capped, cap_reason, vol_ok, earn_warn,
                      earn_approaching, earn_date, earn_days, asset_type, score,
                      squeeze_risk, short_pct, inst_pct, atr_pct, high_volatility,
                      m_analysis, rs_info, sup_touches, sup_q, macd_data, boll_data,
                      level_rel='UNKNOWN', false_breakout=False, fb_label='',
                      level_amb='CLEAR', level_amb_n=0,
                      trend_confirmed=True, trend_conf_label='CONFIRMED',
                      fib_zone='UNKNOWN', fib_ret_pct=0,
                      fib_swing_low=0, fib_swing_high=0, fib_levels=None):
    """
    Assemble the raw setup dict from computed values.
    Pure function — no yfinance calls, no side effects.
    Separated from analyze() so the structure has locality:
    every field lives here, not scattered across 70 lines twice.
    """
    earn_str = 'SOON!' if earn_warn else ('APPROACHING' if earn_approaching else (earn_date or '-'))
    dir_label = '🟢 LONG' if direction == 'LONG' else '🔴 SHORT'
    late_ref  = support if direction == 'LONG' else resistance
    late_pct  = round(abs(price - late_ref) / late_ref * 100, 1) if late_ref else 0
    return {
        'Ticker':         clean_ticker(ticker),
        '_raw':           ticker,
        'Dir':            dir_label,
        'Price':          round(price, 2),
        'RSI':            round(rsi_val, 1),
        'Support':        round(support, 2),
        'Resist':         round(resistance, 2),
        'Entry':          round(entry, 2),
        'Stop':           round(stop, 2),
        'Target':         round(target, 2),
        'R:R':            rratio,
        'Units':          round(units, 1),
        'Risk$':          int(risk_amt),
        'Pos$':           int(pos_val),
        'PosPct':         pos_pct,
        'WasCapped':      was_capped,
        'CapReason':      cap_reason,
        'Vol':            'OK' if vol_ok else 'WARN',
        'Earn':           earn_str,
        'EarnDays':       earn_days,
        'Type':           asset_type,
        '_score':         round(score, 2),
        'SqueezeRisk':    squeeze_risk,
        'ShortInt':       round(short_pct * 100, 1),
        'InstOwn':        round(inst_pct  * 100, 1),
        'ATR_pct':        atr_pct,
        'HighVol':        high_volatility,
        'LateEntry':      late_pct,
        'MonthlyTrend':   m_analysis['trend']      if m_analysis else None,
        'MonthlyCandle':  m_analysis['candle_q']   if m_analysis else None,
        'MonthlyPct':     m_analysis['candle_pct'] if m_analysis else None,
        'SectorETF':      rs_info['etf']            if rs_info else None,
        'SectorRS':       rs_info['rs_label']       if rs_info else None,
        'RS_pct':         rs_info['rs']             if rs_info else None,
        'SectorTrend':    rs_info['sector_trend']   if rs_info else None,
        'SupportQ':       sup_q,
        'SupportTouches': sup_touches,
        'LevelRel':       level_rel,       # CLEAN / TESTED / UNRELIABLE
        '_level_rel':     level_rel,       # read by _factor_level_reliability
        '_false_breakout': false_breakout, # True = N.M.S. not satisfied
        '_fb_label':      fb_label,        # 'FALSE_BREAKOUT' / 'VALID_BREAKOUT' / 'NO_BREAKOUT'
        '_level_amb':     level_amb,       # CLEAR / CROWDED / AMBIGUOUS
        '_level_amb_n':   level_amb_n,     # number of competing levels
        '_trend_confirmed':   trend_confirmed,    # True = swing level closed through
        '_trend_conf_label':  trend_conf_label,   # 'CONFIRMED' / 'UNCONFIRMED'
        '_fib_zone':          fib_zone,           # Factor 20 — Fibonacci zone label
        '_fib_ret_pct':       fib_ret_pct,        # retracement % (0-100)
        '_fib_swing_low':     fib_swing_low,      # swing low used for fib calc
        '_fib_swing_high':    fib_swing_high,     # swing high used for fib calc
        '_fib_levels':        fib_levels or {},   # {'38.2': price, '61.8': price, ...}
        '_macd':          macd_data,
        '_boll':          boll_data,
        '_fundamental':   None,   # filled in by _finalize_setup
    }


def _finalize_setup(setup, direction, ticker, atr_val, m_analysis,
                    is_crypto, is_commodity, is_israel, is_intl, cached_info=None):
    """
    Add probability, time horizon, fundamentals, hard-block check.
    Pass cached_info (asset.info dict) to skip the duplicate HTTP call.
    Returns the setup dict (mutated in place) or None if hard-blocked.
    """
    _is_us = not (is_crypto or is_commodity or is_israel or is_intl)
    if _is_us:
        setup['_fundamental'] = get_fundamental_analysis(clean_ticker(ticker), info=cached_info)

    prob, pfacts = calc_probability(setup)
    setup['Prob']    = prob
    setup['_pfacts'] = pfacts

    est_weeks, horizon, h_label, h_color, h_range = estimate_time_horizon(
        setup['Entry'], setup['Target'], atr_val)
    setup['EstWeeks']     = est_weeks
    setup['TimeHorizon']  = horizon
    setup['HorizonLabel'] = h_label
    setup['HorizonColor'] = h_color
    setup['HorizonRange'] = h_range

    blocked, block_reason = is_hard_blocked(direction, m_analysis)
    if blocked:
        print(f'  🚫 HARD BLOCK {clean_ticker(ticker)} {direction}: {block_reason}')
        return None

    setup['IsWatchlist'] = prob < MIN_PROBABILITY
    return setup


# ── SetupDetector — pure LONG/SHORT detection logic ──────────────────────────

def _squeeze_level(sp, ip):
    """Return squeeze risk level for a SHORT setup. Pure — no I/O."""
    if sp >= 0.15 or ip >= 1.0:
        return 'HIGH'
    if sp >= 0.10 or ip >= 0.80:
        return 'MEDIUM'
    return 'NONE'


def _fetch_market_data(ticker, is_crypto=False, is_commodity=False,
                       is_israel=False, is_intl=False, interval='1wk', period='2y'):
    """
    Fetcher seam — the ONLY place analyze() touches yfinance / the network.
    Returns a dict with everything the detectors need, or None if there isn't
    enough history yet or no clear trend (mirrors the original early returns).
    interval: '1d' / '1wk' / '1mo'
    period:   '1y' / '2y' / '5y'
    """
    asset = yf.Ticker(ticker)
    df    = asset.history(period=period, interval=interval, auto_adjust=True,
                          raise_errors=False)

    # Need fewer bars for monthly, more for daily
    min_bars = {'1d': 100, '1wk': 55, '1mo': 24}.get(interval, 55)
    if is_crypto: min_bars = max(20, min_bars - 20)
    if len(df) < min_bars:
        return None

    df['RSI'] = rsi(df['Close'])
    df['ATR'] = atr(df['High'], df['Low'], df['Close'])

    price   = float(df['Close'].iloc[-1])
    rsi_val = float(df['RSI'].iloc[-1]) if not pd.isna(df['RSI'].iloc[-1]) else 50.0
    atr_val = float(df['ATR'].iloc[-1]) if not pd.isna(df['ATR'].iloc[-1]) else price * 0.03

    # ── MACD + Bollinger Bands ────────────────────────────
    macd_data = calc_macd(df)
    boll_data = calc_bollinger(df)

    trend = get_trend(df)
    if trend is None:
        return None

    support, resistance = get_levels(df, price, atr_val)
    vol_ok = vol_declining(df)

    # ── Earnings (stocks only) ────────────────────────────
    skip_fundamentals = is_crypto or is_commodity
    earn_date, earn_days = (None, None) if skip_fundamentals else get_earnings(asset)
    earn_warn       = earn_days is not None and 0 < earn_days < EARNINGS_WARN_DAYS
    earn_approaching = earn_days is not None and EARNINGS_WARN_DAYS <= earn_days <= 30
    atr_pct         = round(atr_val / price * 100, 2) if price else 0.0
    high_volatility = atr_pct > 8.0

    # ── NEW FILTERS ───────────────────────────────────────
    # 1. Monthly chart analysis (top-down confirmation)
    m_analysis = get_monthly_analysis(ticker, asset) if not is_crypto else None

    # 2. Relative Strength vs Sector (US stocks only — no suffix)
    _is_us_stock = (not is_crypto and not is_commodity and not is_israel and not is_intl)
    rs_info = get_sector_rs(clean_ticker(ticker), df) if _is_us_stock else None

    # 3. Support quality calculated per-setup below (needs direction)

    # ── Short Squeeze + cached .info (reused by get_fundamental_analysis) ──
    short_pct   = 0.0   # short interest as % of float
    inst_pct    = 0.0   # institutional ownership %
    _cached_info = None  # passed to _finalize_setup → get_fundamental_analysis
    if not skip_fundamentals:
        try:
            _cached_info = asset.info or {}
            short_pct = float(_cached_info.get('shortPercentOfFloat', 0) or 0)
            inst_pct  = float(_cached_info.get('heldPercentInstitutions', 0) or 0)
        except Exception:
            pass

    return {
        'df': df, 'price': price, 'rsi_val': rsi_val, 'atr_val': atr_val,
        'macd_data': macd_data, 'boll_data': boll_data, 'trend': trend,
        'support': support, 'resistance': resistance, 'vol_ok': vol_ok,
        'earn_date': earn_date, 'earn_days': earn_days, 'earn_warn': earn_warn,
        'earn_approaching': earn_approaching, 'atr_pct': atr_pct,
        'high_volatility': high_volatility, 'm_analysis': m_analysis,
        'rs_info': rs_info, 'short_pct': short_pct, 'inst_pct': inst_pct,
        'cached_info': _cached_info,
    }


def _detect_long_setup(ticker, portfolio_size, market, is_crypto, asset_type, max_dist,
                       is_commodity=False, is_israel=False, is_intl=False):
    """
    SetupDetector seam (LONG side) — pure given already-fetched market data.
    No yfinance calls here. Returns a finished setup dict, or None.
    """
    df, price, rsi_val, atr_val   = market['df'], market['price'], market['rsi_val'], market['atr_val']
    support, resistance           = market['support'], market['resistance']
    vol_ok, earn_warn             = market['vol_ok'], market['earn_warn']
    earn_approaching, earn_date   = market['earn_approaching'], market['earn_date']
    earn_days, atr_pct            = market['earn_days'], market['atr_pct']
    high_volatility               = market['high_volatility']
    m_analysis, rs_info           = market['m_analysis'], market['rs_info']
    macd_data, boll_data          = market['macd_data'], market['boll_data']
    short_pct, inst_pct           = market['short_pct'], market['inst_pct']

    if not (market['trend'] == 'LONG' and rsi_val <= RSI_LONG_MAX):
        return None

    dist = (price - support) / price
    if dist > max_dist:
        return None

    entry  = price
    stop   = round(support * 0.97, 4)      # 3% cushion below support
    target = resistance

    # Make sure target is meaningfully above entry
    if target <= entry * 1.02:
        target = round(entry + atr_val * 3, 4)

    risk_u = entry - stop
    rew_u  = target - entry
    if not (risk_u > 0 and rew_u > 0):
        return None

    rratio = round(rew_u / risk_u, 2)
    if rratio < MIN_RR:
        return None

    # ── Position Sizing (3 safeguards) ──
    units, pos_val, risk_amt, pos_pct, was_capped, cap_reason = \
        calc_position_size(portfolio_size, entry, stop,
                           atr_pct=atr_pct, high_vol=high_volatility)

    score = rratio
    if vol_ok:           score *= 1.25
    if rsi_val < 45:     score *= 1.20
    if dist < 0.05:      score *= 1.15
    if not earn_warn:    score *= 1.10

    # Gann quality check (stocks only, not crypto)
    if not is_crypto:
        high52 = float(df['High'].tail(52).max())
        if price < high52 * 0.50:
            score *= 0.5   # penalise but don't reject

    # High short interest on LONG = squeeze potential (bonus)
    if short_pct >= 0.15:
        score *= 1.15   # bonus — squeeze can accelerate the move up

    # Support quality + level reliability + false breakout (N.M.S.) + ambiguity
    sup_touches, sup_q = get_support_quality(df, support)
    level_rel, _       = check_level_reliability(df, support)
    fb, fb_label, _    = check_false_breakout(df, resistance, direction='up')
    level_amb, level_amb_n, _ = check_level_ambiguity(df, support, atr_val)
    tr_conf, tr_conf_lbl, _ = check_swing_broken(df, direction='up')
    # Factor 20 — Fibonacci Retracement Zone
    fib_zone, fib_pct, fib_sl, fib_sh, fib_lvls = \
        check_fibonacci_zone(df, 'LONG', price)

    _setup = _build_setup_dict(
        'LONG', ticker, price, rsi_val, support, resistance,
        entry, stop, target, rratio, units, pos_val, risk_amt,
        pos_pct, was_capped, cap_reason, vol_ok, earn_warn,
        earn_approaching, earn_date, earn_days, asset_type, score,
        'NONE', short_pct, inst_pct, atr_pct, high_volatility,
        m_analysis, rs_info, sup_touches, sup_q, macd_data, boll_data,
        level_rel=level_rel, false_breakout=fb, fb_label=fb_label,
        level_amb=level_amb, level_amb_n=level_amb_n,
        trend_confirmed=tr_conf, trend_conf_label=tr_conf_lbl,
        fib_zone=fib_zone, fib_ret_pct=fib_pct,
        fib_swing_low=fib_sl, fib_swing_high=fib_sh, fib_levels=fib_lvls)
    return _finalize_setup(_setup, 'LONG', ticker, atr_val,
                           m_analysis, is_crypto, is_commodity,
                           is_israel, is_intl, cached_info=market['cached_info'])


def _detect_short_setup(ticker, portfolio_size, market, is_crypto, asset_type, max_dist,
                        is_commodity=False, is_israel=False, is_intl=False):
    """
    SetupDetector seam (SHORT side) — pure given already-fetched market data.
    No yfinance calls here. Returns a finished setup dict, or None.
    """
    df, price, rsi_val, atr_val   = market['df'], market['price'], market['rsi_val'], market['atr_val']
    support, resistance           = market['support'], market['resistance']
    vol_ok, earn_warn             = market['vol_ok'], market['earn_warn']
    earn_approaching, earn_date   = market['earn_approaching'], market['earn_date']
    earn_days, atr_pct            = market['earn_days'], market['atr_pct']
    high_volatility               = market['high_volatility']
    m_analysis, rs_info           = market['m_analysis'], market['rs_info']
    macd_data, boll_data          = market['macd_data'], market['boll_data']
    short_pct, inst_pct           = market['short_pct'], market['inst_pct']

    if not (market['trend'] == 'SHORT' and rsi_val >= RSI_SHORT_MIN):
        return None

    dist = (resistance - price) / price
    if dist > max_dist:
        return None

    entry  = price
    stop   = round(resistance * 1.03, 4)   # 3% cushion above resistance
    target = support

    # Make sure target is meaningfully below entry
    if target >= entry * 0.98:
        target = round(entry - atr_val * 3, 4)

    risk_u = stop - entry
    rew_u  = entry - target
    if not (risk_u > 0 and rew_u > 0):
        return None

    rratio = round(rew_u / risk_u, 2)
    if rratio < MIN_RR:
        return None

    # ── Position Sizing (3 safeguards) ──
    units, pos_val, risk_amt, pos_pct, was_capped, cap_reason = \
        calc_position_size(portfolio_size, entry, stop,
                           atr_pct=atr_pct, high_vol=high_volatility)

    score = rratio
    if vol_ok:           score *= 1.20   # volume rising on bounce = bearish
    if rsi_val > 60:     score *= 1.20
    if dist < 0.05:      score *= 1.15
    if not earn_warn:    score *= 1.10

    # ── Squeeze risk penalty ──────────────────
    sq_lvl = _squeeze_level(short_pct, inst_pct)
    if sq_lvl == 'HIGH':
        score *= 0.30   # heavy penalty — near-disqualify
    elif sq_lvl == 'MEDIUM':
        score *= 0.65   # moderate penalty

    # Resistance quality + level reliability + false breakout (N.M.S.) + ambiguity + trend conf
    res_touches, res_q = get_support_quality(df, resistance)
    level_rel, _       = check_level_reliability(df, resistance)
    fb, fb_label, _    = check_false_breakout(df, support, direction='down')
    level_amb, level_amb_n, _ = check_level_ambiguity(df, resistance, atr_val)
    tr_conf, tr_conf_lbl, _ = check_swing_broken(df, direction='down')
    # Factor 20 — Fibonacci Retracement Zone
    fib_zone, fib_pct, fib_sl, fib_sh, fib_lvls = \
        check_fibonacci_zone(df, 'SHORT', price)

    _setup = _build_setup_dict(
        'SHORT', ticker, price, rsi_val, support, resistance,
        entry, stop, target, rratio, units, pos_val, risk_amt,
        pos_pct, was_capped, cap_reason, vol_ok, earn_warn,
        earn_approaching, earn_date, earn_days, asset_type, score,
        sq_lvl, short_pct, inst_pct, atr_pct, high_volatility,
        m_analysis, rs_info, res_touches, res_q, macd_data, boll_data,
        level_rel=level_rel, false_breakout=fb, fb_label=fb_label,
        level_amb=level_amb, level_amb_n=level_amb_n,
        trend_confirmed=tr_conf, trend_conf_label=tr_conf_lbl,
        fib_zone=fib_zone, fib_ret_pct=fib_pct,
        fib_swing_low=fib_sl, fib_swing_high=fib_sh, fib_levels=fib_lvls)
    return _finalize_setup(_setup, 'SHORT', ticker, atr_val,
                           m_analysis, is_crypto, is_commodity,
                           is_israel, is_intl, cached_info=market['cached_info'])


def analyze(ticker, portfolio_size, is_crypto=False, is_israel=False,
            is_commodity=False, is_intl=False, interval='1wk', period='2y'):
    """
    Coordinator — fetch market data once (the only network I/O), then run
    both LONG/SHORT detectors against it. ~20 lines, matches Candidate B
    in architecture-review-cycles-scanner.html.
    Returns a list of valid setups (could be LONG, SHORT, or both).
    """
    setups = []
    try:
        market = _fetch_market_data(ticker, is_crypto=is_crypto, is_commodity=is_commodity,
                                    is_israel=is_israel, is_intl=is_intl,
                                    interval=interval, period=period)
        if market is None:
            return []

        if is_crypto:
            max_dist, asset_type = MAX_DIST_CRYPTO, 'CRYPTO'
        elif is_commodity:
            max_dist, asset_type = MAX_DIST_COMMODITY, 'COMMODITY'
        elif is_israel:
            max_dist, asset_type = MAX_DIST_STOCK, 'TASE'
        elif is_intl:
            max_dist, asset_type = MAX_DIST_INTL, 'INTL'
        else:
            max_dist, asset_type = MAX_DIST_STOCK, 'STOCK'

        long_setup = _detect_long_setup(ticker, portfolio_size, market, is_crypto,
                                        asset_type, max_dist, is_commodity, is_israel, is_intl)
        if long_setup:
            setups.append(long_setup)

        short_setup = _detect_short_setup(ticker, portfolio_size, market, is_crypto,
                                          asset_type, max_dist, is_commodity, is_israel, is_intl)
        if short_setup:
            setups.append(short_setup)

    except Exception:
        pass

    return setups


# ══════════════════════════════════════════════════════════════
#  PROFIT POTENTIAL BREAKDOWN
# ══════════════════════════════════════════════════════════════

def profit_breakdown(results, portfolio, risk_trade):
    """
    For every setup found, print a full profit/loss analysis:
    - Exact $ profit if target hit (T1, T2, T3)
    - Exact $ loss if stop hit
    - % gain on portfolio
    - Scenarios: Win / Lose / Partial exit
    - Weekly compounding projection (4 weeks)
    """
    print()
    print("=" * 70)
    print("  PROFIT POTENTIAL ANALYSIS — per setup")
    print("=" * 70)

    for i, r in enumerate(results, 1):
        ticker  = r['Ticker']
        direc   = 'LONG' if 'LONG' in r['Dir'] else 'SHORT'
        entry   = r['Entry']
        stop    = r['Stop']
        target  = r['Target']   # T1
        units   = r['Units']
        pos_val = r['Pos$']
        rr      = r['R:R']
        rsi_v   = r['RSI']
        typ     = r['Type']

        # Risk and reward per unit — Fibonacci extensions (Cycles Trading)
        if direc == 'LONG':
            risk_per_unit = entry - stop
            rew_t1        = target - entry
            t2            = round(entry + rew_t1 * 1.618, 2)  # Fib 161.8%
            t3            = round(entry + rew_t1 * 2.618, 2)  # Fib 261.8%
            rew_t2        = t2 - entry
            rew_t3        = t3 - entry
        else:
            risk_per_unit = stop - entry
            rew_t1        = entry - target
            t2            = round(entry - rew_t1 * 1.618, 2)  # Fib 161.8%
            t3            = round(entry - rew_t1 * 2.618, 2)  # Fib 261.8%
            rew_t2        = entry - t2
            rew_t3        = entry - t3

        # Dollar amounts
        loss_total    = round(risk_per_unit * units, 2)
        profit_t1     = round(rew_t1 * units, 2)
        profit_t2     = round(rew_t2 * units, 2)
        profit_t3     = round(rew_t3 * units, 2)
        profit_half   = round(profit_t1 * 0.5, 2)   # partial exit at T1

        # % of portfolio
        pct_loss      = round(loss_total / portfolio * 100, 2)
        pct_t1        = round(profit_t1  / portfolio * 100, 2)
        pct_t2        = round(profit_t2  / portfolio * 100, 2)
        pct_t3        = round(profit_t3  / portfolio * 100, 2)

        # Compounding: what happens if same R:R repeated over 4 weeks
        port_after = portfolio
        weekly_wins = []
        for w in range(1, 5):
            gain = port_after * RISK_PCT * rr
            port_after = round(port_after + gain, 2)
            weekly_wins.append(port_after)

        prob      = r.get('Prob', '?')
        prob_bar  = '#' * int(prob / 5) + '-' * (20 - int(prob / 5)) if isinstance(prob, int) else '?'
        sq_lvl    = r.get('SqueezeRisk', 'NONE')
        short_int = r.get('ShortInt', 0)
        inst_own  = r.get('InstOwn', 0)

        print()
        print(f"  #{i}  {ticker}  [{direc}]  {typ}  |  Entry ${entry}  |  RSI {rsi_v}")
        print(f"  Success probability: {prob}%  [{prob_bar}]")
        if direc == 'SHORT' and sq_lvl != 'NONE':
            warn = '🚨 HIGH RISK — Consider skipping!' if sq_lvl == 'HIGH' else '⚠  Manage size carefully'
            print(f"  *** SQUEEZE RISK {sq_lvl}: Short Interest {short_int}%  |  Institutional {inst_own}%  — {warn}")
        elif direc == 'LONG' and short_int >= 15:
            print(f"  >>> Squeeze Potential: Short Interest {short_int}% — forced covering could boost move")
        print(f"  " + "-" * 66)

        # Scenario table
        print(f"  {'SCENARIO':<28} {'PRICE':>10} {'PROFIT/LOSS':>12} {'% of Portfolio':>16}")
        print(f"  {'-'*28} {'-'*10} {'-'*12} {'-'*16}")
        print(f"  {'Stop hit (full loss)':<28} {'$'+str(stop):>10} {'-$'+str(abs(loss_total)):>12} {'-'+str(pct_loss)+'%':>16}")
        print(f"  {'Target T1 (full exit)':<28} {'$'+str(target):>10} {'+$'+str(profit_t1):>12} {'+'+str(pct_t1)+'%':>16}")
        print(f"  {'Target T1 (half exit)':<28} {'$'+str(target):>10} {'+$'+str(profit_half):>12} {'+'+str(round(pct_t1/2,2))+'%':>16}")
        print(f"  {'Target T2 (Fib 161.8%)':<28} {'$'+str(t2):>10} {'+$'+str(profit_t2):>12} {'+'+str(pct_t2)+'%':>16}")
        print(f"  {'Target T3 (Fib 261.8%)':<28} {'$'+str(t3):>10} {'+$'+str(profit_t3):>12} {'+'+str(pct_t3)+'%':>16}")

        # Weekly compounding projection
        print()
        print(f"  COMPOUNDING PROJECTION (if same setup repeats each week):")
        print(f"  {'Week':<8} {'Portfolio After Win':>20} {'Total Gain':>14}")
        print(f"  {'-'*8} {'-'*20} {'-'*14}")
        for w, val in enumerate(weekly_wins, 1):
            gain_total = round(val - portfolio, 2)
            print(f"  {'Week '+str(w):<8} {'$'+f'{val:,.2f}':>20} {'+$'+f'{gain_total:,.2f}':>14}")

        print()
        print(f"  Position size: ${pos_val:,}   |   Units: {units}   |   R:R: 1:{rr}")
        print(f"  Risk amount  : ${abs(loss_total):,}  ({pct_loss}% of portfolio)")
        print(f"  " + "=" * 66)


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print()
    print("=" * 62)
    n_cases = len(LEARNINGS.get('case_studies',[])) if LEARNINGS else 0
    print(f"   CYCLES TRADING SCANNER  v6  |  📚 {n_cases} case studies loaded")
    print("   LONG + SHORT  |  US · INTL · TASE · CRYPTO · COMMODITIES  |  Weekly")
    try:
        from zoneinfo import ZoneInfo as _ZI
        _now = datetime.now(_ZI('Asia/Jerusalem'))
    except Exception:
        from datetime import timezone as _tz, timedelta as _td
        _now = datetime.now(_tz(_td(hours=3)))
    print(f"   {_now.strftime('%Y-%m-%d  %H:%M')} Israel Time")
    print("=" * 62)
    print()

    # ══════════════════════════════════════════════════════════
    #  OPTION 1 — Portfolio size
    # ══════════════════════════════════════════════════════════
    global PORTFOLIO_SIZE
    if PORTFOLIO_SIZE is None:
        try:
            raw = input("  [1] Enter your portfolio size in $  (e.g. 1000): ").replace(',','').strip()
            PORTFOLIO_SIZE = float(raw) if raw else 1000
        except (EOFError, ValueError):
            PORTFOLIO_SIZE = 1000
        print(f"      -> Portfolio set to: ${PORTFOLIO_SIZE:,.0f}")

    # ══════════════════════════════════════════════════════════
    #  OPTION 2 — Specific ticker or scan all
    # ══════════════════════════════════════════════════════════
    print()
    try:
        ticker_input = input(
            "  [2] Specific ticker (press ENTER to scan ALL)\n"
            "      US: AAPL  |  Israeli: LUMI.TA  |  Intl: SAP.DE / 7203.T\n"
            "      Crypto: BTC-USD  |  Commodity: GC=F (Gold) / CL=F (Oil)\n"
            "      > "
        ).strip().upper()
    except (EOFError, KeyboardInterrupt):
        ticker_input = ''

    # Interval is always Weekly — Cycles Trading standard
    INTERVAL, PERIOD, IV_LABEL = '1wk', '2y', 'Weekly (1wk)'
    print()

    # Build the lists to scan based on input
    INTL_SUFFIXES = ('.L','.DE','.PA','.T','.HK','.TO','.AX','.SW','.NS')

    if ticker_input:
        if ticker_input.endswith('-USD') or any(ticker_input == t.replace('-USD','') for t in CRYPTO_WATCHLIST):
            full = ticker_input if ticker_input.endswith('-USD') else ticker_input + '-USD'
            if full not in CRYPTO_WATCHLIST: CRYPTO_WATCHLIST.append(full); print(f"  -> Added '{full}' to crypto list.")
            scan_stocks = []; scan_israel = []; scan_intl = []; scan_crypto = [full]; scan_commodity = []
        elif ticker_input.endswith('=F'):
            if ticker_input not in COMMODITY_WATCHLIST: COMMODITY_WATCHLIST.append(ticker_input); print(f"  -> Added '{ticker_input}' to commodities.")
            scan_stocks = []; scan_israel = []; scan_intl = []; scan_crypto = []; scan_commodity = [ticker_input]
        elif ticker_input.endswith('.TA'):
            if ticker_input not in ISRAEL_WATCHLIST: ISRAEL_WATCHLIST.append(ticker_input); print(f"  -> Added '{ticker_input}' to Israeli list.")
            scan_stocks = []; scan_israel = [ticker_input]; scan_intl = []; scan_crypto = []; scan_commodity = []
        elif any(ticker_input.endswith(s) for s in INTL_SUFFIXES):
            if ticker_input not in INTL_WATCHLIST: INTL_WATCHLIST.append(ticker_input); print(f"  -> Added '{ticker_input}' to international list.")
            scan_stocks = []; scan_israel = []; scan_intl = [ticker_input]; scan_crypto = []; scan_commodity = []
        else:
            if ticker_input not in STOCK_WATCHLIST: STOCK_WATCHLIST.append(ticker_input); print(f"  -> Added '{ticker_input}' to US stocks.")
            scan_stocks = [ticker_input]; scan_israel = []; scan_intl = []; scan_crypto = []; scan_commodity = []
        print(f"  Scanning: {ticker_input}")
    else:
        scan_stocks    = STOCK_WATCHLIST
        scan_israel    = ISRAEL_WATCHLIST
        scan_intl      = INTL_WATCHLIST
        scan_crypto    = CRYPTO_WATCHLIST
        scan_commodity = COMMODITY_WATCHLIST
        total_all = len(scan_stocks)+len(scan_israel)+len(scan_intl)+len(scan_crypto)+len(scan_commodity)
        print(f"  Scanning ALL {total_all} assets:")
        print(f"    US Stocks: {len(scan_stocks)}  |  Israeli: {len(scan_israel)}  |  International: {len(scan_intl)}")
        print(f"    Crypto: {len(scan_crypto)}  |  Commodities: {len(scan_commodity)}")

    print()

    risk_trade   = PORTFOLIO_SIZE * RISK_PCT
    profit_1win  = risk_trade * MIN_RR
    need_3k_week = (3000 / MIN_RR) / RISK_PCT

    print(f"  +--------------------------------------------------+")
    print(f"  |  Portfolio size    : ${PORTFOLIO_SIZE:>10,.0f}                  |")
    print(f"  |  Risk per trade    : ${risk_trade:>10,.0f}  ({RISK_PCT*100:.0f}%)           |")
    print(f"  |  Profit per 1 win  : ${profit_1win:>10,.0f}  (R:R {MIN_RR:.0f}:1)          |")
    print(f"  |  Need for $3k/week : ${need_3k_week:>10,.0f}  portfolio size     |")
    print(f"  |  Chart interval    : {IV_LABEL:<28}  |")
    print(f"  +--------------------------------------------------+")
    print()

    all_results = []
    total = len(scan_stocks)+len(scan_israel)+len(scan_intl)+len(scan_crypto)+len(scan_commodity)
    idx   = 0

    def scan_group(tickers, label, **kwargs):
        """Scan a list of tickers concurrently — network I/O releases the GIL."""
        nonlocal idx
        if not tickers: return
        print(f"\n  ── {label} ({'—'*40})")
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _scan_one(ticker):
            return (ticker, analyze(ticker, PORTFOLIO_SIZE, interval=INTERVAL, period=PERIOD, **kwargs))

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(_scan_one, t): t for t in tickers}
            for future in as_completed(futures):
                idx += 1
                pct = int(idx / max(total, 1) * 30)
                try:
                    ticker, setups = future.result()
                except Exception:
                    ticker = futures[future]
                    setups = []
                print(f"  [{'#'*pct}{'-'*(30-pct)}] {idx:>3}/{total}  {ticker:<14}", end='\r')
                for s in setups:
                    all_results.append(s)

    scan_group(scan_stocks,    'US Stocks',             is_crypto=False, is_israel=False, is_commodity=False, is_intl=False)
    scan_group(scan_israel,    'Israeli Stocks (TASE)',  is_crypto=False, is_israel=True,  is_commodity=False, is_intl=False)
    scan_group(scan_intl,      'International Stocks',   is_crypto=False, is_israel=False, is_commodity=False, is_intl=True)
    scan_group(scan_crypto,    'Crypto',                 is_crypto=True,  is_israel=False, is_commodity=False, is_intl=False)
    scan_group(scan_commodity, 'Commodities',            is_crypto=False, is_israel=False, is_commodity=True,  is_intl=False)

    print(f"\n  Done! Scan complete.                          ")
    print()

    # ── Sort by score ────────────────────────────────────────
    all_results.sort(key=lambda x: x['_score'], reverse=True)

    longs  = [r for r in all_results if 'LONG'  in r['Dir']]
    shorts = [r for r in all_results if 'SHORT' in r['Dir']]

    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 300)

    # ── Print LONG results ───────────────────────────────────
    print("=" * 80)
    print(f"  [LONG]  LONG SETUPS  — {len(longs)} found")
    print("=" * 80)
    if longs:
        cols = ['Ticker','Type','Price','RSI','Support','Entry','Stop','Target','R:R','Pos$','Vol','Earn']
        print(pd.DataFrame(longs)[cols].to_string(index=False))
    else:
        print("  No LONG setups found today.")
    print()

    print("=" * 80)
    print(f"  [SHORT] SHORT SETUPS — {len(shorts)} found")
    print("=" * 80)
    if shorts:
        cols = ['Ticker','Type','Price','RSI','Resist','Entry','Stop','Target','R:R','Pos$','Vol','Earn']
        print(pd.DataFrame(shorts)[cols].to_string(index=False))
    else:
        print("  No SHORT setups found today.")
    print()

    # ── Profit potential breakdown ───────────────────────────
    if all_results:
        profit_breakdown(all_results, PORTFOLIO_SIZE, risk_trade)

    # ── Setup paths ──────────────────────────────────────────
    ts       = datetime.now().strftime('%Y%m%d_%H%M')
    script_d = os.path.dirname(os.path.abspath(__file__))

    # Create REPORTS folder next to the script if it doesn't exist
    reports_d = os.path.join(script_d, 'REPORTS')
    os.makedirs(reports_d, exist_ok=True)

    generated_files = []

    # ── Save CSV ─────────────────────────────────────────────
    if all_results:
        fpath = os.path.join(reports_d, f"cycles_scan_{ts}.csv")
        out   = [{k:v for k,v in r.items() if k not in ('_score','_pfacts','_raw')} for r in all_results]
        pd.DataFrame(out).to_csv(fpath, index=False)
        generated_files.append(fpath)
        print(f"  CSV saved   : REPORTS\\cycles_scan_{ts}.csv")
        print()

    # ── HTML Report ──────────────────────────────────────────
    html_path = generate_html(all_results, reports_d, ts,
                              PORTFOLIO_SIZE, risk_trade, IV_LABEL,
                              len(scan_stocks), len(scan_israel),
                              len(scan_intl), len(scan_crypto), len(scan_commodity))
    if html_path:
        generated_files.append(html_path)
        print(f"  HTML saved  : REPORTS\\cycles_report_{ts}.html")
        webbrowser.open('file:///' + html_path.replace('\\', '/'))
        print()

        # ── Email summary notification ────────────────────────
        longs  = [r for r in all_results if '▲' in r.get('Dir','') or 'LONG' in r.get('Dir','')]
        shorts = [r for r in all_results if '▼' in r.get('Dir','') or 'SHORT' in r.get('Dir','')]
        green_l = [r for r in longs  if not r.get('IsWatchlist') and r.get('Prob',0) >= 70]
        green_s = [r for r in shorts if not r.get('IsWatchlist') and r.get('Prob',0) >= 70]
        yellow  = [r for r in all_results if not r.get('IsWatchlist') and 65 <= r.get('Prob',0) < 70]
        watch   = [r for r in all_results if r.get('IsWatchlist')]

        tickers_green  = ', '.join(r['Ticker'] for r in green_l + green_s) or 'אין'
        tickers_yellow = ', '.join(r['Ticker'] for r in yellow[:8])         or 'אין'
        tickers_watch  = ', '.join(r['Ticker'] for r in watch[:8])          or 'אין'

        subj = f"🤖 Cycles Scanner {ts} — {len(green_l+green_s)} GO setups"

        # Plain-text body
        txt_body = (
            f"Cycles Trading Scanner — {ts}\n"
            f"{'='*42}\n"
            f"🟢 GO  ({len(green_l+green_s)} setups): {tickers_green}\n"
            f"🟡 WAIT ({len(yellow)} setups): {tickers_yellow}\n"
            f"👁  Watchlist ({len(watch)}):  {tickers_watch}\n"
            f"📊 Total scanned: {len(all_results)} setups\n"
            f"💼 Portfolio: ${PORTFOLIO_SIZE:,}  |  Risk/trade: ${int(PORTFOLIO_SIZE*RISK_PCT)}\n"
            f"{'='*42}\n"
            f"Open the HTML report for full details:\n{html_path}\n"
        )

        # HTML body — colour-coded table of green setups
        rows_html = ''
        for r in green_l + green_s:
            tl = r.get('TrafficLight','')
            tl_color = '#27ae60' if tl=='GREEN' else '#f39c12' if tl=='YELLOW' else '#e74c3c'
            rows_html += (
                f"<tr>"
                f"<td style='padding:6px 10px;font-weight:bold'>{r['Ticker']}</td>"
                f"<td style='padding:6px 10px'>{r.get('Dir','')}</td>"
                f"<td style='padding:6px 10px;color:{tl_color};font-weight:bold'>{r.get('Prob',0)}%</td>"
                f"<td style='padding:6px 10px'>{r.get('Entry','')}</td>"
                f"<td style='padding:6px 10px'>{r.get('Stop','')}</td>"
                f"<td style='padding:6px 10px'>{r.get('Target','')}</td>"
                f"<td style='padding:6px 10px'>{r.get('Horizon','')}</td>"
                f"<td style='padding:6px 10px'>${r.get('Pos$',0):,.0f} ({r.get('Pos%',0)}%)</td>"
                f"</tr>"
            )
        html_body = f"""
<html><body style='font-family:Arial,sans-serif;color:#222;max-width:900px'>
<h2 style='color:#1a5276'>🤖 Cycles Trading Scanner — {ts}</h2>
<p style='font-size:15px'>
  🟢 <b>GO:</b> {len(green_l+green_s)} setups &nbsp;|&nbsp;
  🟡 <b>WAIT:</b> {len(yellow)} &nbsp;|&nbsp;
  👁 <b>Watchlist:</b> {len(watch)} &nbsp;|&nbsp;
  📊 Total: {len(all_results)}
</p>
<table border='0' cellspacing='0' style='border-collapse:collapse;width:100%;font-size:13px'>
  <thead>
    <tr style='background:#1a5276;color:#fff'>
      <th style='padding:8px 10px;text-align:left'>Ticker</th>
      <th style='padding:8px 10px;text-align:left'>Dir</th>
      <th style='padding:8px 10px;text-align:left'>Prob</th>
      <th style='padding:8px 10px;text-align:left'>Entry</th>
      <th style='padding:8px 10px;text-align:left'>Stop</th>
      <th style='padding:8px 10px;text-align:left'>Target</th>
      <th style='padding:8px 10px;text-align:left'>Horizon</th>
      <th style='padding:8px 10px;text-align:left'>Position</th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
</table>
<p style='margin-top:18px;color:#555'>
  💼 Portfolio: <b>${PORTFOLIO_SIZE:,}</b> &nbsp;|&nbsp;
  Risk/trade: <b>${int(PORTFOLIO_SIZE*RISK_PCT)}</b> (1%)
</p>
<p><a href='file:///{html_path.replace(chr(92),'/')}'>📂 Open full HTML report</a></p>
</body></html>
"""
        send_email_summary(subj, txt_body, html_body)

    # ── Pine Script ──────────────────────────────────────────
    pine_path = save_pine_script(all_results, reports_d, ts)
    if pine_path:
        generated_files.append(pine_path)

    # ── Summary ──────────────────────────────────────────────
    if generated_files:
        print(f"  All files saved to: {reports_d}")
        print()

    # ── Position Manager — check open trades after every scan ─
    manage_positions(send_email=True)


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


def generate_html(results, script_d, ts, portfolio, risk_trade, iv_label,
                  n_stocks=0, n_israel=0, n_intl=0, n_crypto=0, n_commodity=0):
    """Generate a beautiful dark-theme HTML report of all scan results.
    Coordinator: calls _render_tv_url, _render_fund_box, setup_cards.
    Card-level HTML lives in those renderers — not here.
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

    # Aliases so setup_cards() can call them without change
    tv_url       = _render_tv_url
    _build_fund_html = _render_fund_box

    def setup_cards(rows, color, direction):
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
      <a href="{tv_url(r)}" target="_blank" class="tv-link">📊 TradingView</a>
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
  {_build_fund_html(r)}
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

    # Sort by probability descending (best setups first)
    longs.sort(key=lambda x: x.get('Prob', 0),  reverse=True)
    shorts.sort(key=lambda x: x.get('Prob', 0), reverse=True)

    # Split into main (≥ MIN_PROBABILITY) and watchlist (< MIN_PROBABILITY)
    longs_main  = [r for r in longs  if not r.get('IsWatchlist')]
    longs_watch = [r for r in longs  if r.get('IsWatchlist')]
    shorts_main = [r for r in shorts if not r.get('IsWatchlist')]
    shorts_watch= [r for r in shorts if r.get('IsWatchlist')]

    long_cards        = setup_cards(longs_main,  '#00c851', 'LONG')
    long_watch_cards  = setup_cards(longs_watch, '#00c851', 'LONG')
    short_cards       = setup_cards(shorts_main, '#ff4444', 'SHORT')
    short_watch_cards = setup_cards(shorts_watch,'#ff4444', 'SHORT')

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


if __name__ == "__main__":
    import sys as _sys
    _args = _sys.argv[1:]

    if not _args:
        # Normal scan
        main()

    elif _args[0] == 'positions':
        # python cycles_trading_scanner.py positions
        # python cycles_trading_scanner.py positions --all
        list_positions(show_closed='--all' in _args)

    elif _args[0] == 'check':
        # python cycles_trading_scanner.py check
        # python cycles_trading_scanner.py check --email
        manage_positions(send_email='--email' in _args)

    elif _args[0] == 'add':
        # python cycles_trading_scanner.py add LMND LONG 54.13 45.07 71.38 87.04 100.15 11 "discord rec"
        if len(_args) >= 9:
            _, ticker, direction, entry, stop, tp1, tp2, tp3, units, *rest = _args
            notes = ' '.join(rest)
            pm_add(ticker, direction, float(entry), float(stop),
                   float(tp1), float(tp2), float(tp3), int(units), notes)
        else:
            # interactive
            print('Usage: python cycles_trading_scanner.py add TICKER DIR ENTRY STOP TP1 TP2 TP3 UNITS [notes]')
            ticker    = input('Ticker     : ').strip().upper()
            direction = input('Direction  : ').strip().upper()
            entry     = float(input('Entry      : '))
            stop_p    = float(input('Stop       : '))
            tp1       = float(input('TP1        : '))
            tp2       = float(input('TP2        : '))
            tp3       = float(input('TP3        : '))
            units     = int(input('Units      : '))
            notes     = input('Notes      : ').strip()
            pm_add(ticker, direction, entry, stop_p, tp1, tp2, tp3, units, notes)

    elif _args[0] == 'close':
        # python cycles_trading_scanner.py close LMND_202607051437 [exit_price]
        if len(_args) >= 2:
            pos_id     = _args[1]
            exit_price = float(_args[2]) if len(_args) >= 3 else None
            pm_close(pos_id, exit_price)
        else:
            print('Usage: python cycles_trading_scanner.py close <position_id> [exit_price]')

    else:
        print('Commands: (none) → full scan | positions | check [--email] | add | close <id> [price]')
