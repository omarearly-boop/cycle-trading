"""
ct_validator.py  --  post-scan sanity checks
Run after every scan to catch silent bugs early.
Usage:  python ct_validator.py REPORTS/cycles_scan_YYYYMMDD_HHMM.csv
"""
import sys, csv, json
from datetime import datetime

def validate_csv(path):
    errors = []
    warnings = []
    today = datetime.now().date()

    with open(path, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("  [!] CSV is empty -- nothing to validate")
        return

    for r in rows:
        ticker = r.get('Ticker', '?')
        prob   = float(r.get('Prob', 0))
        light  = r.get('TrafficLight', '')
        earn   = r.get('Earn', '')
        rr     = float(r.get('R:R', 0))
        entry  = float(r.get('Entry', 0))
        stop   = float(r.get('Stop', 0))
        target = float(r.get('Target', 0))
        direction = r.get('Dir', '')

        # 1. Prob vs TrafficLight consistency (only if column exists in CSV)
        if light and light not in ('?', 'None'):  # column present and non-empty
            if prob >= 70 and light != 'GREEN':
                errors.append(f"{ticker}: prob={prob}% but TrafficLight={light} (expected GREEN)")
            if prob < 65 and light not in ('RED', ''):
                warnings.append(f"{ticker}: prob={prob}% but TrafficLight={light} (should be RED/watchlist)")

        # 2. Stale earnings dates
        if earn and earn not in ('-', 'APPROACHING', 'SOON!', ''):
            try:
                ed = datetime.strptime(earn[:10], '%Y-%m-%d').date()
                if ed < today:
                    errors.append(f"{ticker}: earnings date {earn} is in the past")
            except Exception:
                pass

        # 3. R:R sanity
        if rr < 2.0:
            errors.append(f"{ticker}: R:R={rr} below MIN_RR=2.0 -- should have been filtered")

        # 4. Geometry sanity (LONG: entry > stop, target > entry)
        if 'LONG' in direction:
            if entry <= stop:
                errors.append(f"{ticker} LONG: entry {entry} <= stop {stop}")
            if target <= entry:
                errors.append(f"{ticker} LONG: target {target} <= entry {entry}")
        elif 'SHORT' in direction:
            if entry >= stop:
                errors.append(f"{ticker} SHORT: entry {entry} >= stop {stop}")
            if target >= entry:
                errors.append(f"{ticker} SHORT: target {target} >= entry {entry}")

        # 5. No setup should have prob=0
        if prob == 0:
            errors.append(f"{ticker}: prob=0 -- scoring broken?")

    print(f"\n  === ct_validator: {path} ===")
    print(f"  Rows checked : {len(rows)}")
    if errors:
        print(f"  ERRORS ({len(errors)}):") 
        for e in errors: print(f"    ✗ {e}")
    else:
        print("  No errors found")
    if warnings:
        print(f"  WARNINGS ({len(warnings)}):")
        for w in warnings: print(f"    ⚠ {w}")
    print()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python ct_validator.py REPORTS/cycles_scan_....csv")
        sys.exit(1)
    validate_csv(sys.argv[1])
