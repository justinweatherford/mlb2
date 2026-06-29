import csv
import os
import sqlite3
from datetime import datetime

DB = 'kalshi_mlb.db'
OUT = os.path.join('outputs', 'quick_review_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
os.makedirs(OUT, exist_ok=True)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row


def table_exists(name):
    row = conn.execute(
        'SELECT name FROM sqlite_master WHERE type = ? AND name = ?',
        ('table', name),
    ).fetchone()
    return row is not None


def dump_csv(filename, query):
    path = os.path.join(OUT, filename)
    rows = conn.execute(query).fetchall()

    with open(path, 'w', newline='', encoding='utf-8') as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows([dict(r) for r in rows])
        else:
            f.write('')

    print(filename + ': ' + str(len(rows)) + ' rows')


dump_csv(
    '01_snapshot_source_health.csv',
    '''
    SELECT
        source,
        COUNT(*) AS rows,
        MIN(snapped_at) AS first_seen,
        MAX(snapped_at) AS last_seen
    FROM kalshi_orderbook_snapshots
    GROUP BY source
    ORDER BY source
    '''
)

dump_csv(
    '02_recent_orderbook_snapshots.csv',
    '''
    SELECT *
    FROM kalshi_orderbook_snapshots
    WHERE source IN ('rest_batch', 'ws_ticker', 'ws_orderbook')
    ORDER BY snapped_at DESC
    LIMIT 1000
    '''
)

dump_csv(
    '03_recent_rest_batch.csv',
    '''
    SELECT *
    FROM kalshi_orderbook_snapshots
    WHERE source = 'rest_batch'
    ORDER BY snapped_at DESC
    LIMIT 1000
    '''
)

dump_csv(
    '04_recent_ws_snapshots.csv',
    '''
    SELECT *
    FROM kalshi_orderbook_snapshots
    WHERE source IN ('ws_ticker', 'ws_orderbook')
    ORDER BY snapped_at DESC
    LIMIT 1000
    '''
)

if table_exists('mlb_game_states'):
    dump_csv(
        '05_recent_game_states.csv',
        '''
        SELECT *
        FROM mlb_game_states
        ORDER BY checked_at DESC
        LIMIT 1000
        '''
    )

if table_exists('candidate_events'):
    dump_csv(
        '06_recent_candidates.csv',
        '''
        SELECT *
        FROM candidate_events
        ORDER BY created_at DESC
        LIMIT 1000
        '''
    )

if table_exists('signal_events'):
    dump_csv(
        '07_recent_signals.csv',
        '''
        SELECT *
        FROM signal_events
        ORDER BY created_at DESC
        LIMIT 1000
        '''
    )

if table_exists('paper_positions'):
    dump_csv(
        '08_recent_paper_positions.csv',
        '''
        SELECT *
        FROM paper_positions
        ORDER BY id DESC
        LIMIT 1000
        '''
    )

if table_exists('run_health'):
    dump_csv(
        '09_run_health.csv',
        '''
        SELECT *
        FROM run_health
        ORDER BY process
        '''
    )

print()
print('WROTE: ' + OUT)