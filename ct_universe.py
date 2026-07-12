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

import csv, io, json, logging, os, re, subprocess, warnings
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


def _parse_ishares_csv(text: str) -> List[str]:
    """Parse an iShares holdings CSV body into ticker list.

    Returns [] if the response is actually an HTML page — iShares started
    serving the product page (bot-check / URL scheme change) with a
    'text/csv' content type, and the HTML contains the substring 'Ticker'
    ('fundTicker'), which fooled the old header detection into parsing
    garbage and yielding 0 tickers.
    """
    if '<html' in text[:2000].lower() or '<!doctype' in text[:2000].lower():
        return []
    lines = text.splitlines()
    # iShares CSVs have info rows before the real header — find 'Ticker' column
    header_idx = next((i for i, l in enumerate(lines) if 'Ticker' in l), None)
    if header_idx is None:
        return []
    reader = csv.DictReader(lines[header_idx:])
    tickers = []
    _SKIP = {'-', 'USD', 'XTSLA', 'XTSLA BF', ''}
    for row in reader:
        t = (row.get('Ticker') or '').strip()
        if t and t not in _SKIP and not t.startswith('CASH') and len(t) <= 6:
            tickers.append(t.replace('.', '-'))
    return tickers


def _fetch_wikipedia_index(page: str, min_expected: int = 100) -> List[str]:
    """Constituent tickers from a Wikipedia 'List of ... companies' page.

    Dependency-free wikitable parse: first wikitable whose header has a
    'Symbol' column; take the first <td> of each row. These pages are
    maintained within days of index changes and load fine with plain
    requests (unlike the iShares endpoints, which now bot-check).
    """
    url = "https://en.wikipedia.org/wiki/" + page
    r = requests.get(url, timeout=30, headers=_HEADERS)
    r.raise_for_status()
    for tbl in re.findall(r'<table[^>]*wikitable[^>]*>.*?</table>', r.text, re.S):
        header = re.search(r'<tr[^>]*>.*?</tr>', tbl, re.S)
        if not header or 'Symbol' not in header.group(0):
            continue
        out = []
        for row in re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, re.S)[1:]:
            td = re.search(r'<td[^>]*>(.*?)</td>', row, re.S)
            if not td:
                continue
            sym = re.sub(r'<[^>]+>', '', td.group(1)).strip().upper()
            if re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,7}', sym):
                out.append(sym.replace('.', '-'))
        if len(out) >= min_expected:
            return out
    return []


def _fetch_sp400() -> List[str]:
    """S&P 400 Mid-Cap. iShares IJH holdings CSV first (BlackRock official);
    falls back to Wikipedia's constituent list — the iShares .ajax endpoint
    now often returns an HTML page instead of the CSV.
    Catches mid-caps like MOG.A (~$3B) that aren't in S&P 500 or NASDAQ 100.
    """
    url = (
        "https://www.ishares.com/us/products/239763/"
        "ishares-sp-mid-cap-etf/1467271812596.ajax"
        "?fileType=csv&fileName=IJH_holdings&dataType=fund"
    )
    tickers: List[str] = []
    try:
        r = requests.get(url, timeout=30, headers=_HEADERS)
        r.raise_for_status()
        tickers = _parse_ishares_csv(r.text)
    except Exception as exc:
        log.warning("iShares IJH fetch failed: %s", exc)
    if len(tickers) >= 100:
        return tickers
    print("      (iShares IJH unavailable -- using Wikipedia S&P 400 list)")
    return _fetch_wikipedia_index("List_of_S%26P_400_companies")


def _fetch_russell2000_top() -> List[str]:
    """Russell 2000 top 300 by market weight from iShares IWM ETF holdings CSV.

    Full Russell 2000 has ~2000 tickers -- too slow for hourly scan.
    We take the top 300 by market weight = liquid small-caps like YELP, GME, etc.
    The scanner's own liquidity filter (avg vol < 100K) removes any stragglers.
    """
    url = (
        "https://www.ishares.com/us/products/239726/"
        "ishares-russell-2000-etf/1467271812596.ajax"
        "?fileType=csv&fileName=IWM_holdings&dataType=fund"
    )
    rows = []
    try:
        r = requests.get(url, timeout=30, headers=_HEADERS)
        r.raise_for_status()
        text = r.text
        if '<html' in text[:2000].lower() or '<!doctype' in text[:2000].lower():
            text = ''    # HTML page, not the CSV (see _parse_ishares_csv)
        lines = text.splitlines()
        header_idx = next((i for i, l in enumerate(lines) if 'Ticker' in l), None)
        if header_idx is not None:
            reader = csv.DictReader(lines[header_idx:])
            _SKIP = {'-', 'USD', 'XTSLA', 'XTSLA BF', ''}
            for row in reader:
                t = (row.get('Ticker') or '').strip()
                if not t or t in _SKIP or t.startswith('CASH') or len(t) > 6:
                    continue
                try:
                    weight = float(row.get('Weight (%)', 0) or 0)
                except (ValueError, TypeError):
                    weight = 0.0
                rows.append((weight, t.replace('.', '-')))
    except Exception as exc:
        log.warning("iShares IWM fetch failed: %s", exc)
    if len(rows) >= 100:
        # Sort descending by weight, take top 300
        rows.sort(reverse=True)
        return [t for _, t in rows[:300]]
    # Fallback: Wikipedia has no Russell 2000 constituent list, so use the
    # S&P 600 SmallCap list — similar liquid small-cap coverage (~600 names,
    # profitability-screened). The scanner's liquidity filter trims the rest.
    print("      (iShares IWM unavailable -- using Wikipedia S&P 600 small-cap list)")
    return _fetch_wikipedia_index("List_of_S%26P_600_companies")


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
#  TRADINGVIEW SCREENER SOURCE  (optional — requires opencli)
#  Install once:
#    npm install -g @jackwener/opencli
#    opencli plugin install github:himself65/finance-skills/tradingview
#    opencli tradingview launch   (once per desktop session)
# ══════════════════════════════════════════════════════════════

_TV_FILTERS = json.dumps([
    {"left": "market_cap_basic", "operation": "greater", "right": 500000000},
    {"left": "volume",           "operation": "greater", "right": 500000},
    {"left": "close",            "operation": "greater", "right": 5},
])

_TV_COLUMNS = "name,close,change,volume,market_cap_basic,sector.tr"
_TV_LIMIT   = 600


def _fetch_tv_screener() -> List[str]:
    """Fetch live US stock universe from TradingView Screener via opencli.
    Returns list of ticker symbols.
    Raises RuntimeError if opencli / TV plugin unavailable.
    """
    cmd = [
        "opencli", "tradingview", "screener",
        "--market",  "america",
        "--columns", _TV_COLUMNS,
        "--filter",  _TV_FILTERS,
        "--sort",    "volume:desc",
        "--limit",   str(_TV_LIMIT),
        "-f",        "json",
    ]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=60,
            shell=(os.name == "nt"),
        )
    except FileNotFoundError:
        raise RuntimeError(
            "opencli not found. Install: npm install -g @jackwener/opencli"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("opencli screener timed out (60s)")

    if res.returncode != 0:
        err = (res.stderr or res.stdout or "").strip()
        raise RuntimeError("opencli error (rc={}): {}".format(res.returncode, err[:200]))

    raw = res.stdout.strip()
    if not raw:
        raise RuntimeError("opencli returned empty output")

    try:
        rows = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("opencli output not valid JSON: {}".format(exc))

    tickers = []
    for row in rows:
        sym = (row.get("symbol") or row.get("name") or "").strip()
        if ":" in sym:
            sym = sym.split(":", 1)[1]
        sym = sym.upper()
        if sym and 1 < len(sym) <= 5 and sym.replace("-", "").isalpha():
            tickers.append(sym)

    if not tickers:
        raise RuntimeError("No tickers parsed from {} screener rows".format(len(rows)))

    return tickers


def get_tv_universe() -> List[str]:
    """Return TradingView screener universe. Raises RuntimeError if unavailable."""
    return _fetch_tv_screener()

# ══════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════

def get_us_universe(use_tv: bool = True) -> List[str]:
    """Fetch US stock universe.

    Strategy (automatic fallback):
      1. TradingView Screener via opencli -- live, volume-sorted, pre-filtered
         (needs opencli + TV plugin + TradingView desktop running)
      2. S&P 500 + NASDAQ 100 + S&P 400 Mid-Cap from public APIs

    use_tv=True  -- try TV screener first, fall back to index lists on error
    use_tv=False -- skip TV screener, use index lists directly
    """
    if use_tv:
        try:
            result = _fetch_tv_screener()
            print("    OK TradingView Screener: {} tickers (live, volume-filtered)".format(len(result)))
            return _dedup(result)
        except RuntimeError as exc:
            print("    TV Screener unavailable ({}) -- falling back to index lists".format(exc))
        except Exception as exc:
            print("    TV Screener error ({}) -- falling back to index lists".format(exc))

    # Fallback: index-based sources
    tickers = []
    for name, fn in [
        ("S&P 500",          _fetch_sp500),
        ("NASDAQ 100",       _fetch_nasdaq100),
        ("S&P 400 Mid",      _fetch_sp400),
        ("Russell 2000 Top", _fetch_russell2000_top),
    ]:
        try:
            result = fn()
            print("    OK {}: {} tickers".format(name, len(result)))
            tickers.extend(result)
        except Exception as exc:
            print("    {} fetch failed ({}) -- skipped".format(name, exc))
            log.warning("Universe fetch failed (%s): %s", name, exc)

    return _dedup(tickers)
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
            print(f"  Universe cache ({age_h}h old): "
                  f"{len(data.get('us',[]))} US + {len(data.get('israel',[]))} Israel")
            return data
        except Exception:
            pass   # fall through to fresh fetch

    print("  Fetching live market universe (S&P 500 + NASDAQ 100 + S&P 400 + Russell 2000 Top + Israel)...")
    us     = get_us_universe()
    israel = get_israel_universe()

    if us:
        universe = {'us': us, 'israel': israel}
        _write_cache(universe)
        print(f"  Universe ready: {len(us)} US + {len(israel)} Israel stocks")
        return universe

    if _cache_valid():
        print("  Live fetch failed -- using previous cache as fallback")
        return _read_cache()['data']

    print("  Live fetch failed, no cache -- using built-in fallback")
    return {'us': [], 'israel': israel}


if __name__ == '__main__':
    u = get_universe(force_refresh=True)
    print(f"\nUS universe sample: {u['us'][:10]} ... total {len(u['us'])}")
    print(f"Israel: {u['israel'][:5]} ... total {len(u['israel'])}")
    for t in ['KLAC', 'NVDA', 'AAPL', 'YELP']:
        found = t in u['us']
        print(f"  {'OK' if found else 'MISS'} {t}")
