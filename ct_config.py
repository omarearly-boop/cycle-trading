#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ct_config.py — Configuration + watchlist loader."""
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

# ── Yahoo Finance rate-limit protection ─────────────────────
SCAN_MAX_WORKERS = 2     # parallel fetch threads (was 6 — tripped Yahoo rate limits)
YF_THROTTLE_SEC  = 0.6   # min global spacing between Yahoo requests (~100/min)
YF_MAX_RETRIES   = 3     # retries with exponential backoff on YFRateLimitError

# ── Liquidity / pre-filters (course lesson 30) ──────────────
MIN_WEEKLY_VOL_US    = 5_000_000  # weekly bars ≈ 1M shares/day (course: Avg Vol > 1M/day)
MIN_WEEKLY_VOL_OTHER = 10_000     # crypto / commodity / TASE / intl
PE_PREFILTER         = None       # set to 25 to enforce course P/E < 25 hard gate (None = off)

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
        return [], [], [], [], [], {}
    with open(path, 'r', encoding='utf-8') as f:
        d = json.load(f)
    return (d.get('stocks', []), d.get('israel', []), d.get('intl', []),
            d.get('crypto', []), d.get('commodity', []), d.get('sector_etf', {}))

# ── Watchlist containers (populated lazily on first get_watchlists() call) ─
# Using mutable containers so `from ct_config import STOCK_WATCHLIST` callers
# automatically see the data after get_watchlists() is called — no rebinding needed.
STOCK_WATCHLIST:     list = []
ISRAEL_WATCHLIST:    list = []
INTL_WATCHLIST:      list = []
CRYPTO_WATCHLIST:    list = []
COMMODITY_WATCHLIST: list = []
SECTOR_ETF:          dict = {}

_watchlists_loaded: bool = False

def get_watchlists():
    """
    Lazy-load full market universe on first call.  Safe to call multiple times.
    Mutates module-level list/dict objects in place so callers that did
    `from ct_config import STOCK_WATCHLIST` see populated data without re-import.

    Priority (highest → lowest):
      1. ct_universe — live S&P 500 + NASDAQ 100 + S&P 400 Mid-Cap (24h cache)
      2. _STOCK_FALLBACK / _INTL_FALLBACK — embedded curated lists (always included)
      3. watchlists.json — custom overrides / additions
    """
    global _watchlists_loaded
    if not _watchlists_loaded:
        # ── Step 1: custom additions from watchlists.json ─────────
        stocks, israel, intl, crypto, commodity, sector_etf = _load_watchlists()

        # ── Step 2: live universe (S&P 500 + NASDAQ 100 + S&P 400) ─
        # force_refresh=True → always fetch fresh on every scanner run
        base_us, base_israel = _STOCK_FALLBACK, []
        try:
            from ct_universe import get_universe
            u           = get_universe(force_refresh=True)
            base_us     = u.get('us',     _STOCK_FALLBACK)
            base_israel = u.get('israel', [])
        except Exception as _e:
            print(f"  ⚠ ct_universe unavailable ({_e}) — using built-in fallback list")

        # ── Step 3: merge, deduplicate, preserve order ────────────
        def _merge(*lists):
            seen = set()
            return [x for lst in lists for x in lst if not (x in seen or seen.add(x))]

        STOCK_WATCHLIST.extend(_merge(base_us,     stocks))
        ISRAEL_WATCHLIST.extend(_merge(base_israel, israel))
        INTL_WATCHLIST.extend(_merge(_INTL_FALLBACK, intl))
        CRYPTO_WATCHLIST.extend(crypto)
        COMMODITY_WATCHLIST.extend(commodity)
        SECTOR_ETF.update(sector_etf)
        _watchlists_loaded = True

    return (STOCK_WATCHLIST, ISRAEL_WATCHLIST, INTL_WATCHLIST,
            CRYPTO_WATCHLIST, COMMODITY_WATCHLIST, SECTOR_ETF)

# Perf: sector ETF weekly data is fetched once per scan, not once per stock
_SECTOR_CACHE: dict = {}  # sector_etf → sec_df
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
    # ── Hong Kong / China (.HK) ────────────────────────────
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
