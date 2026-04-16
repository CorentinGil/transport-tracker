"""Fetch and cache SNCF Transilien punctuality data."""

import requests
import sqlite3
from database import get_db, DB_PATH


def init_sncf_tables():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sncf_punctuality (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            service TEXT NOT NULL,
            line TEXT NOT NULL,
            line_name TEXT,
            punctuality_pct REAL,
            ratio_ontime_per_late REAL,
            UNIQUE(date, service, line)
        );
        CREATE INDEX IF NOT EXISTS idx_sncf_date ON sncf_punctuality(date);
        CREATE INDEX IF NOT EXISTS idx_sncf_line ON sncf_punctuality(service, line);
    """)
    conn.commit()
    conn.close()


def sync_sncf_data():
    """Fetch all SNCF punctuality data and upsert into local DB."""
    url = "https://data.sncf.com/api/explore/v2.1/catalog/datasets/ponctualite-mensuelle-transilien/records"
    all_records = []
    offset = 0
    limit = 100

    while True:
        resp = requests.get(url, params={"limit": limit, "offset": offset, "order_by": "date ASC"}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break
        all_records.extend(results)
        offset += limit
        if offset >= data.get("total_count", 0):
            break

    conn = get_db()
    for r in all_records:
        conn.execute("""
            INSERT OR REPLACE INTO sncf_punctuality (date, service, line, line_name, punctuality_pct, ratio_ontime_per_late)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            r.get("date"),
            r.get("service"),
            r.get("ligne"),
            r.get("nom_de_la_ligne"),
            r.get("taux_de_ponctualite"),
            r.get("nombre_de_voyageurs_a_l_heure_pour_un_voyageur_en_retard"),
        ))
    conn.commit()
    conn.close()
    print(f"SNCF: synced {len(all_records)} records")
    return len(all_records)


def get_sncf_lines():
    conn = get_db()
    rows = conn.execute("""
        SELECT DISTINCT service, line, line_name
        FROM sncf_punctuality
        ORDER BY service, line
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_sncf_by_line(service=None, year=None, month=None):
    conn = get_db()
    conditions = []
    if service:
        conditions.append(f"service = '{service}'")
    if year and month:
        conditions.append(f"date = '{year}-{month}'")
    elif year:
        conditions.append(f"date LIKE '{year}%'")
    elif month:
        conditions.append(f"date LIKE '%-{month}'")
    filt = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = conn.execute(f"""
        SELECT service, line, line_name,
            ROUND(AVG(punctuality_pct), 1) as avg_pct,
            ROUND(MIN(punctuality_pct), 1) as min_pct,
            ROUND(MAX(punctuality_pct), 1) as max_pct,
            COUNT(*) as months
        FROM sncf_punctuality
        {filt}
        GROUP BY service, line
        ORDER BY avg_pct ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_sncf_by_month_avg(service=None):
    """Average punctuality per calendar month across all years (seasonal view)."""
    conn = get_db()
    filt = f"WHERE service = '{service}'" if service else ""
    rows = conn.execute(f"""
        SELECT
            CAST(SUBSTR(date, 6, 2) AS INTEGER) as month_num,
            line,
            ROUND(AVG(punctuality_pct), 1) as avg_pct
        FROM sncf_punctuality
        {filt}
        GROUP BY month_num, line
        ORDER BY month_num, avg_pct ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_sncf_available_years():
    conn = get_db()
    rows = conn.execute("""
        SELECT DISTINCT SUBSTR(date, 1, 4) as year
        FROM sncf_punctuality
        ORDER BY year
    """).fetchall()
    conn.close()
    return [r["year"] for r in rows]


def get_sncf_history(line, service=None):
    conn = get_db()
    filt = f"AND service = '{service}'" if service else ""
    rows = conn.execute(f"""
        SELECT date, punctuality_pct, ratio_ontime_per_late
        FROM sncf_punctuality
        WHERE line = ? {filt}
        ORDER BY date
    """, (line,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_sncf_by_year(service=None):
    conn = get_db()
    filt = f"WHERE service = '{service}'" if service else ""
    rows = conn.execute(f"""
        SELECT SUBSTR(date, 1, 4) as year, line,
            ROUND(AVG(punctuality_pct), 1) as avg_pct
        FROM sncf_punctuality
        {filt}
        GROUP BY year, line
        ORDER BY year, avg_pct ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]
