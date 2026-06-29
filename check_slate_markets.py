import sqlite3, re, sys

def main():
    if len(sys.argv) < 2:
        print("Usage: check_slate_markets.py YYYY-MM-DD")
        sys.exit(1)

    target = sys.argv[1]
    db = 'kalshi_mlb.db'
    pat = re.compile(r'-(\d{2})([A-Z]{3})(\d{2})\d{4}')
    month_map = {
        'JAN':'01','FEB':'02','MAR':'03','APR':'04','MAY':'05','JUN':'06',
        'JUL':'07','AUG':'08','SEP':'09','OCT':'10','NOV':'11','DEC':'12'
    }

    try:
        conn = sqlite3.connect(db)
        rows = conn.execute(
            'SELECT market_ticker FROM kalshi_markets WHERE status=?', ('open',)
        ).fetchall()
        count = 0
        for (t,) in rows:
            m = pat.search(t or '')
            if m:
                yy, mon, dd = m.group(1), m.group(2), m.group(3)
                mo = month_map.get(mon)
                if mo and f'20{yy}-{mo}-{dd}' == target:
                    count += 1
        print(f'  Markets for {target}: {count}')
        if count == 0:
            print('  WARNING: No open markets found for this date.')
            print('  Run: python kalshi_discover.py --sport mlb')
            print('  Then re-run this batch file.')
            sys.exit(1)
        else:
            print(f'  OK: {count} open markets ready to poll.')
    except Exception as e:
        print(f'  ERROR: {e}')
        sys.exit(1)

if __name__ == '__main__':
    main()
