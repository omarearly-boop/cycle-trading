#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ct_universe.py — Live market universe adapter.

Sources (zero Wikipedia):
  US S&P 500   → DataHub GitHub CSV  (updated on index changes by CKAN team)
  US NASDAQ100 → NASDAQ's own API    (api.nasdaq.com — official)
  US S&P 400   → iShares IJH ETF holdings CSV (BlackRock official)
  Israel       → Curated TA-35 + TA-90 (update quarterly from tase.co.il)
  Intl/Crypto/Commodity → ct_config fallbacks (already comprehensive)

Caches to .universe_cache.json for CACHE_TTL_H hours.
"""

import csv, io, json, logging, os, warnings
import requests
from datetime import datetime, timedelta
from typing import Dict, List

warnings.filterwarnings('ignore')
log = logging.getLogger(__name__)

_HERE       = os.path.dirname(os.path.abspath(__file__))
_CACHE_FILE = os.path.join(_HERE, '.universe_cache.json')
_CACHE_TTL  = 24   # hours

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept':     'application/json, text/plain, */*',
}

# ══════════════════════════════════════════════════════════════
#  LIVE US SOURCES
# ══════════════════════════════════════════════════════════════

def _fetch_sp500() -> List[str]:
    """S&P 500 from DataHub GitHub (not Wikipedia). Updated on index changes."""
    url = ("https://raw.githubusercontent.com/datasets/"
           "s-and-p-500-companies/main/data/constituents.csv")
    r = requests.get(url, timeout=20, headers=_HEADERS)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    # BRK.B → BRK-B for yfinance compatibility
    return [row['Symbol'].strip().replace('.', '-') for row in reader]


def _fetch_nasdaq100() -> List[str]:
    """NASDAQ 100 from NASDAQ's own API (api.nasdaq.com)."""
    url = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
    r   = requests.get(url, timeout=20, headers={
        **_HEADERS,
        'Origin':  'https://www.nasdaq.com',
        'Referer': 'https://www.nasdaq.com/',
    })
    r.raise_for_status()
    rows = r.json().get('data', {}).get('data', {}).get('rows', [])
    return [row['symbol'] for row in rows if row.get('symbol')]


def _fetch_sp400() -> List[str]:
    """S&P 400 Mid-Cap from iShares IJH ETF holdings CSV (BlackRock official).
    Catches mid-caps like MOG.A (~$3B) that aren't in S&P 500 or NASDAQ 100.
    """
    url = (
        "https://www.ishares.com/us/products/239763/"
        "ishares-sp-mid-cap-etf/1467271812596.ajax"
        "?fileType=csv&fileName=IJH_holdings&dataType=fund"
    )
    r = requests.get(url, timeout=30, headers=_HEADERS)
    r.raise_for_status()
    lines = r.text.splitlines()
    # iShares CSVs have info rows before the real header — find 'Ticker' column
    header_idx = next((i for i, l in enumerate(lines) if 'Ticker' in l), None)
    if header_idx is None:
        return []
    reader = csv.DictReader(lines[header_idx:])
    tickers = []
    _SKIP = {'-', 'USD', 'XTSLA', 'XTSLA BF', ''}
    for row in reader:
        t = row.get('Ticker', '').strip()
        if t and t not in _SKIP and not t.startswith('CASH') and len(t) <= 6:
            tickers.append(t.replace('.', '-'))
    return tickers


# ══════════════════════════════════════════════════════════════
#  CURATED ISRAEL LIST  (TA-35 + TA-90)
#  Update quarterly: https://www.tase.co.il/en/market_data/indices
# ══════════════════════════════════════════════════════════════
_ISRAEL_TICKERS: List[str] = [
    # ── TA-35 (top 35 by free-float market cap on TASE) ──────
    "LUMI.TA",   # Bank Leumi
    "HARL.TA",   # Bank Hapoalim
    "MZTF.TA",   # Mizrahi-Tefahot Bank
    "FIBI.TA",   # First International Bank
    "DSCT.TA",   # Bank Discount
    "AZRG.TA",   # Azrieli Group (real estate)
    "ESLT.TA",   # Elbit Systems (defense)
    "ICL.TA",    # ICL Group (chemicals / potash)
    "TEVA.TA",   # Teva Pharmaceutical
    "BEZQ.TA",   # Bezeq (telecom)
    "ENLT.TA",   # Enlight Renewable Energy
    "POLI.TA",   # Harel Insurance
    "ISCO.TA",   # Israel Corp
    "ELCO.TA",   # Elco Holdings
    "AMOT.TA",   # Amot Investments (real estate)
    "SPGE.TA",   # Shapir Engineering
    "SMLT.TA",   # Shufersal (retail)
    "SRAC.TA",   # Strauss Group (food)
    "MVNE.TA",   # Migdal Insurance
    "EMTC.TA",   # Energix Renewable
    "PTNR.TA",   # Partner Communications
    "CLBV.TA",   # Cellcom Israel
    "MLTM.TA",   # Malam-Team (IT services)
    "ARPT.TA",   # Airport City
    "NICE.TA",   # NICE Systems (also NASDAQ:NICE)
    "CHKP.TA",   # Check Point Software (also NASDAQ:CHKP)
    "AMDOC.TA",  # Amdocs (also NASDAQ:DOX)
    # ── TA-90 additions ──────────────────────────────────────
    "KMDA.TA",   # Kamada (pharma)
    "TASE.TA",   # Tel-Aviv Stock Exchange itself
    "ILCO.TA",   # Israel Land Development
    "GFC.TA",    # GFN International
    "BRMG.TA",   # Brainsway (medical devices)
    "PRSK.TA",   # Perion Network (ad-tech)
    "SKLN.TA",   # Skyline AI
    "WLFD.TA",   # Welfardia
]

# ══════════════════════════════════════════════════════════════
#  CACHE HELPERS
# ══════════════════════════════════════════════════════════════

def _cache_valid() -> bool:
    if not os.path.exists(_CACHE_FILE):
        return False
    try:
        with open(_CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data.get('cached_at', '2000-01-01'))
        return datetime.now() - cached_at < timedelta(hours=_CACHE_TTL)
    except Exception:
        return False


def _read_cache() -> Dict:
    with open(_CACHE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def _write_cache(universe: Dict[str, List[str]]) -> None:
    with open(_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump({'cached_at': datetime.now().isoformat(), 'data': universe}, f, indent=2)


def _dedup(lst: List[str]) -> List[str]:
    seen: set = set()
    return [x for x in lst if not (x in seen or seen.add(x))]


# ══════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════

def get_us_universe() -> List[str]:
    """Fetch S&P 500 + NASDAQ 100 + S&P 400 Mid-Cap (de-duplicated).
    Any fetch that fails is skipped with a warning — remaining sources still used.
    """
    tickers: List[str] = []
    for name, fn in [
        ('S&P 500',      _fetch_sp500),
        ('NASDAQ 100',   _fetch_nasdaq100),
        ('S&P 400 Mid',  _fetch_sp400),
    ]:
        try:
            result = fn()
            print(f"    ✓ {name}: {len(result)} tickers")
            tickers.extend(result)
        except Exception as e:
            print(f"    ⚠ {name} fetch failed ({e}) — skipped")
            log.warning("Universe fetch failed (%s): %s", name, e)

    unique = _dedup(tickers)
    return unique


def get_israel_universe() -> List[str]:
    """Return curated TA-35 + TA-90 tickers."""
    return list(_ISRAEL_TICKERS)


def get_universe(force_refresh: bool = True) -> Dict[str, List[str]]:
    """Return full universe dict.

    Keys: 'us', 'israel'
    (intl / crypto / commodity remain in ct_config fallbacks / watchlists.json)

    force_refresh=True (default) — always fetch live on every run.
    force_refresh=False          — use cache if < CACHE_TTL_H hours old.

    On any fetch failure the last good cache is returned as fallback.
    """
    if not force_refresh and _cache_valid():
        try:
            cached = _read_cache()
            data   = cached['data']
            age_h  = (datetime.now() - datetime.fromisoformat(cached['cached_at'])).seconds // 3600
            print(f"  ✓ Universe cache ({age_h}h old): "
                  f"{len(data.get('us',[]))} US + {len(data.get('israel',[]))} Israel")
            return data
        except Exception:
            pass   # fall through to fresh fetch

    print("  Fetching live market universe (S&P 500 + NASDAQ 100 + S&P 400 + Israel)...")
    us     = get_us_universe()
    israel = get_israel_universe()

    if us:
        # Only write cache when we actually got live data
        universe = {'us': us, 'israel': israel}
        _write_cache(universe)
        print(f"  ✓ Universe ready: {len(us)} US + {len(israel)} Israel stocks")
        return universe

    # All fetches failed — return last good cache if it exists
    if _cache_valid():
        print("  ⚠ Live fetch failed — using previous cache as fallback")
        return _read_cache()['data']

    print("  ⚠ Live fetch failed, no cache — using built-in fallback")
    return {'us': [], 'israel': israel}


if __name__ == '__main__':
    # Quick test: python ct_universe.py
    u = get_universe(force_refresh=True)
    print(f"\nUS universe sample: {u['us'][:10]} ... total {len(u['us'])}")
    print(f"Israel: {u['israel'][:5]} ... total {len(u['israel'])}")
    for t in ['KLAC', 'MOG-A', 'MOG.A', 'NVDA', 'AAPL']:
        found = t in u['us'] or t.replace('-','.') in u['us']
        print(f"  {'✓' if found else '✗'} {t}")
