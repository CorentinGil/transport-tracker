import sqlite3
import os
import time
import threading
import inspect
from functools import wraps
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "ratp.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-32000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


# --- In-memory query cache ---
# Les agrégats ne changent qu'à chaque scrape (toutes les 5 min). Un cache
# court rend les chargements de dashboard instantanés sans rendre les données
# perceptiblement obsolètes. `invalidate_query_cache()` est appelé en fin de
# scrape pour garantir des données fraîches dès qu'elles arrivent.
_query_cache = {}
_query_cache_lock = threading.Lock()
# Aligne sur l'intervalle de scrape (5 min) : la cache est de toute facon
# invalidee a chaque scrape, donc le TTL est juste un filet de securite.
_QUERY_CACHE_TTL = 300


def _cached(fn):
    # On normalise la cle en injectant les valeurs par defaut : ainsi un appel
    # sans args (warm-up) et un appel positionnel (route) avec les memes valeurs
    # produisent la meme cle, evitant de cacher deux fois la meme chose.
    sig = inspect.signature(fn)
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            key = (fn.__name__, tuple(bound.arguments.items()))
        except TypeError:
            key = (fn.__name__, args, tuple(sorted(kwargs.items())))
        now = time.time()
        with _query_cache_lock:
            hit = _query_cache.get(key)
            if hit and now - hit[0] < _QUERY_CACHE_TTL:
                return hit[1]
        result = fn(*args, **kwargs)
        with _query_cache_lock:
            _query_cache[key] = (now, result)
        return result
    return wrapper


def invalidate_query_cache(warm=True):
    with _query_cache_lock:
        _query_cache.clear()
    if warm:
        threading.Thread(target=warm_cache, daemon=True).start()


def warm_cache():
    """Pre-populate the cache with the queries the dashboard hits, so the
    first user request after a scrape doesn't pay the 5s cold-query cost.

    IMPORTANT: les arguments doivent matcher exactement ceux passes par les
    routes Flask, sinon la cle de cache differe et le warm-up ne sert a rien.
    Voir app.py:api_summary, api_ranking, etc."""
    try:
        get_stats_summary(None, None)
        get_disruption_ranking(None, None)
        get_cause_ranking(None, None, None, None)
        get_travaux_ranking(None, None)
        get_sparkline_data(7)
        get_monthly_trend()
    except Exception as e:
        print(f"warm_cache: {e}")


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS line_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            line_type TEXT NOT NULL,
            line_id TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT,
            message TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_timestamp ON line_status(timestamp);
        CREATE INDEX IF NOT EXISTS idx_line ON line_status(line_type, line_id);
        CREATE INDEX IF NOT EXISTS idx_status ON line_status(status);
        CREATE INDEX IF NOT EXISTS idx_line_time ON line_status(line_type, line_id, timestamp);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_line_status_ts_line ON line_status(timestamp, line_type, line_id);

        -- Add cause column if not exists (migration-safe)
    """)
    try:
        conn.execute("ALTER TABLE line_status ADD COLUMN cause TEXT DEFAULT ''")
    except Exception:
        pass  # Column already exists
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_cause ON line_status(cause);

        CREATE TABLE IF NOT EXISTS reimbursements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            lines TEXT,
            start_date TEXT,
            end_date TEXT,
            url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


# --- Query helpers ---

def get_current_status():
    """Get the latest status for each line."""
    conn = get_db()
    rows = conn.execute("""
        SELECT ls.line_type, ls.line_id, ls.status, ls.title, ls.message, ls.timestamp
        FROM line_status ls
        INNER JOIN (
            SELECT line_type, line_id, MAX(timestamp) as max_ts
            FROM line_status
            GROUP BY line_type, line_id
        ) latest ON ls.line_type = latest.line_type
            AND ls.line_id = latest.line_id
            AND ls.timestamp = latest.max_ts
        ORDER BY ls.line_type,
            CASE WHEN ls.line_id GLOB '[0-9]*' THEN CAST(ls.line_id AS INTEGER) ELSE 999 END,
            ls.line_id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@_cached
def get_disruption_ranking(start_date=None, end_date=None):
    """Rank lines by % of time spent in disruption.

    Pour chaque ligne en alerte continue depuis >7j, on remonte
    `long_term_since` (date du debut de la coupure) pour permettre au front
    de l'afficher distinctement : T1, M12, M7b... sont en coupure structurelle
    (pont casse, modernisation) et il est utile de les distinguer des lignes
    operationnellement instables.
    """
    conn = get_db()
    date_filter = _date_filter(start_date, end_date)

    long_term = {}
    lt_rows = conn.execute("""
        WITH current_status AS (
            SELECT ls.line_type, ls.line_id, ls.status
            FROM line_status ls
            INNER JOIN (
                SELECT line_type, line_id, MAX(timestamp) as mx
                FROM line_status GROUP BY line_type, line_id
            ) lt ON ls.line_type = lt.line_type
                AND ls.line_id = lt.line_id
                AND ls.timestamp = lt.mx
        ),
        last_normal AS (
            SELECT line_type, line_id, MAX(timestamp) as last_ok
            FROM line_status
            WHERE status != 'alerte'
            GROUP BY line_type, line_id
        )
        SELECT cs.line_type, cs.line_id,
            COALESCE(
                (SELECT MIN(timestamp) FROM line_status
                 WHERE line_type = cs.line_type AND line_id = cs.line_id
                   AND status = 'alerte'
                   AND timestamp > COALESCE(ln.last_ok, '1900-01-01')),
                (SELECT MIN(timestamp) FROM line_status
                 WHERE line_type = cs.line_type AND line_id = cs.line_id
                   AND status = 'alerte')
            ) as outage_start
        FROM current_status cs
        LEFT JOIN last_normal ln
          ON cs.line_type = ln.line_type AND cs.line_id = ln.line_id
        WHERE cs.status = 'alerte'
          AND (ln.last_ok IS NULL OR julianday('now') - julianday(ln.last_ok) > 7)
    """).fetchall()
    for r in lt_rows:
        long_term[(r["line_type"], r["line_id"])] = r["outage_start"]

    rows = conn.execute(f"""
        SELECT
            line_type,
            line_id,
            COUNT(*) as total_checks,
            SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) as disrupted_checks,
            ROUND(
                100.0 * SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) / COUNT(*),
                1
            ) as disruption_pct
        FROM line_status
        WHERE 1=1 {date_filter}
        GROUP BY line_type, line_id
        ORDER BY disruption_pct DESC
    """).fetchall()
    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        d["long_term_since"] = long_term.get((d["line_type"], d["line_id"]))
        result.append(d)
    return result


def get_line_detail_stats(line_type, line_id, start_date=None, end_date=None):
    """Detailed stats for a single line.

    Returns counts (relevés bruts + temps cumulé en minutes), incidents distincts
    (transitions vers 'alerte' via LAG), durée moyenne par incident, et pire jour.
    Le scrape tourne toutes les 5 min, donc 1 relevé = 5 min cumulées.
    """
    SCRAPE_INTERVAL_MIN = 5
    conn = get_db()
    date_filter = _date_filter(start_date, end_date)

    row = conn.execute(f"""
        WITH ranked AS (
            SELECT timestamp, status,
                LAG(status) OVER (ORDER BY timestamp) AS prev_status
            FROM line_status
            WHERE line_type = ? AND line_id = ? {date_filter}
        )
        SELECT
            COUNT(*) as total_checks,
            SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) as disrupted_checks,
            SUM(CASE WHEN status = 'alerte'
                AND (prev_status IS NULL OR prev_status != 'alerte')
                THEN 1 ELSE 0 END) as distinct_events
        FROM ranked
    """, (line_type, line_id)).fetchone()

    total_checks = row["total_checks"] or 0
    disrupted_checks = row["disrupted_checks"] or 0
    distinct_events = row["distinct_events"] or 0

    disruption_pct = round(100.0 * disrupted_checks / total_checks, 1) if total_checks else 0
    avg_duration_min = round(disrupted_checks * SCRAPE_INTERVAL_MIN / distinct_events, 1) if distinct_events else 0

    worst = conn.execute(f"""
        SELECT DATE(timestamp) as date,
            SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) as disrupted_count
        FROM line_status
        WHERE line_type = ? AND line_id = ? {date_filter}
        GROUP BY date
        HAVING disrupted_count > 0
        ORDER BY disrupted_count DESC
        LIMIT 1
    """, (line_type, line_id)).fetchone()

    conn.close()

    return {
        "total_checks": total_checks,
        "disrupted_checks": disrupted_checks,
        "disrupted_minutes": disrupted_checks * SCRAPE_INTERVAL_MIN,
        "disruption_pct": disruption_pct,
        "distinct_events": distinct_events,
        "avg_duration_min": avg_duration_min,
        "worst_day": worst["date"] if worst else None,
        "worst_day_minutes": (worst["disrupted_count"] * SCRAPE_INTERVAL_MIN) if worst else 0,
    }


def get_disruptions_by_hour(start_date=None, end_date=None, line_type=None, line_id=None):
    """Disruption rate per hour of day."""
    conn = get_db()
    date_filter = _date_filter(start_date, end_date)
    line_filter = _line_filter(line_type, line_id)

    rows = conn.execute(f"""
        SELECT
            CAST(strftime('%H', timestamp) AS INTEGER) as hour,
            COUNT(*) as total,
            SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) as disrupted,
            ROUND(100.0 * SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
        FROM line_status
        WHERE 1=1 {date_filter} {line_filter}
        GROUP BY hour
        ORDER BY hour
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_disruptions_by_day_of_week(start_date=None, end_date=None, line_type=None, line_id=None):
    """Disruption rate per day of week (0=Sunday)."""
    conn = get_db()
    date_filter = _date_filter(start_date, end_date)
    line_filter = _line_filter(line_type, line_id)

    rows = conn.execute(f"""
        SELECT
            CAST(strftime('%w', timestamp) AS INTEGER) as dow,
            COUNT(*) as total,
            SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) as disrupted,
            ROUND(100.0 * SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
        FROM line_status
        WHERE 1=1 {date_filter} {line_filter}
        GROUP BY dow
        ORDER BY dow
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_disruptions_by_date(start_date=None, end_date=None, line_type=None, line_id=None):
    """Disruption rate per calendar date."""
    conn = get_db()
    date_filter = _date_filter(start_date, end_date)
    line_filter = _line_filter(line_type, line_id)

    rows = conn.execute(f"""
        SELECT
            DATE(timestamp) as date,
            COUNT(*) as total,
            SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) as disrupted,
            ROUND(100.0 * SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
        FROM line_status
        WHERE 1=1 {date_filter} {line_filter}
        GROUP BY date
        ORDER BY date
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_disruptions_by_month(start_date=None, end_date=None, line_type=None, line_id=None):
    """Disruption rate per month."""
    conn = get_db()
    date_filter = _date_filter(start_date, end_date)
    line_filter = _line_filter(line_type, line_id)

    rows = conn.execute(f"""
        SELECT
            strftime('%Y-%m', timestamp) as month,
            COUNT(*) as total,
            SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) as disrupted,
            ROUND(100.0 * SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
        FROM line_status
        WHERE 1=1 {date_filter} {line_filter}
        GROUP BY month
        ORDER BY month
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_line_history(line_type, line_id, start_date=None, end_date=None):
    """Get full status history for a specific line."""
    conn = get_db()
    date_filter = _date_filter(start_date, end_date)

    rows = conn.execute(f"""
        SELECT timestamp, status, title, message, cause
        FROM line_status
        WHERE line_type = ? AND line_id = ? {date_filter}
        ORDER BY timestamp DESC
        LIMIT 500
    """, (line_type, line_id)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@_cached
def get_stats_summary(start_date=None, end_date=None):
    """Global summary stats. total_disruptions = line-snapshots in alert.
    distinct_events = distinct disruption events (transitions to alerte)."""
    conn = get_db()
    date_filter = _date_filter(start_date, end_date)

    row = conn.execute(f"""
        SELECT
            COUNT(DISTINCT line_type || '-' || line_id) as total_lines,
            COUNT(*) as total_checks,
            SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) as total_disruptions,
            MIN(timestamp) as first_record,
            MAX(timestamp) as last_record
        FROM line_status
        WHERE 1=1 {date_filter}
    """).fetchone()

    events_row = conn.execute(f"""
        WITH ranked AS (
            SELECT line_type, line_id, status,
                LAG(status) OVER (PARTITION BY line_type, line_id ORDER BY timestamp) AS prev_status
            FROM line_status
            WHERE 1=1 {date_filter}
        )
        SELECT COUNT(*) as distinct_events
        FROM ranked
        WHERE status = 'alerte' AND (prev_status IS NULL OR prev_status != 'alerte')
    """).fetchone()

    conn.close()
    result = dict(row) if row else {}
    result["distinct_events"] = events_row["distinct_events"] if events_row else 0
    return result


def get_available_years():
    """Get all years that have data."""
    conn = get_db()
    rows = conn.execute("""
        SELECT DISTINCT strftime('%Y', timestamp) as year
        FROM line_status
        ORDER BY year
    """).fetchall()
    conn.close()
    return [r["year"] for r in rows]


def get_available_lines():
    """Get all tracked lines."""
    conn = get_db()
    rows = conn.execute("""
        SELECT DISTINCT line_type, line_id
        FROM line_status
        ORDER BY line_type,
            CASE WHEN line_id GLOB '[0-9]*' THEN CAST(line_id AS INTEGER) ELSE 999 END,
            line_id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@_cached
def get_cause_ranking(start_date=None, end_date=None, line_type=None, line_id=None):
    """Rank disruption causes by number of distinct events (transitions into alerte)."""
    conn = get_db()
    date_filter = _date_filter(start_date, end_date)
    line_filter = _line_filter(line_type, line_id)
    rows = conn.execute(f"""
        WITH ranked AS (
            SELECT line_type, line_id, status, cause,
                LAG(status) OVER (PARTITION BY line_type, line_id ORDER BY timestamp) AS prev_status
            FROM line_status
            WHERE 1=1 {date_filter} {line_filter}
        )
        SELECT
            cause,
            COUNT(*) as count,
            COUNT(DISTINCT line_type || '-' || line_id) as lines_affected
        FROM ranked
        WHERE status = 'alerte'
          AND (prev_status IS NULL OR prev_status != 'alerte')
          AND cause != ''
        GROUP BY cause
        ORDER BY count DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_cause_by_line(start_date=None, end_date=None):
    """Top cause per line."""
    conn = get_db()
    date_filter = _date_filter(start_date, end_date)
    rows = conn.execute(f"""
        SELECT line_type, line_id, cause, COUNT(*) as count
        FROM line_status
        WHERE status = 'alerte' AND cause != '' {date_filter}
        GROUP BY line_type, line_id, cause
        ORDER BY line_type, line_id, count DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_heatmap_data(line_type=None, line_id=None):
    """Disruption % by hour (0-23) x day of week (0=Sun)."""
    conn = get_db()
    line_filter = _line_filter(line_type, line_id)
    rows = conn.execute(f"""
        SELECT
            CAST(strftime('%w', timestamp) AS INTEGER) as dow,
            CAST(strftime('%H', timestamp) AS INTEGER) as hour,
            ROUND(100.0 * SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
        FROM line_status
        WHERE 1=1 {line_filter}
        GROUP BY dow, hour
        ORDER BY dow, hour
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_prediction_data(line_type=None, line_id=None):
    """Disruption probability by dow + hour, with sample count."""
    conn = get_db()
    line_filter = _line_filter(line_type, line_id)
    rows = conn.execute(f"""
        SELECT
            CAST(strftime('%w', timestamp) AS INTEGER) as dow,
            CAST(strftime('%H', timestamp) AS INTEGER) as hour,
            COUNT(*) as samples,
            SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) as disrupted,
            ROUND(100.0 * SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
        FROM line_status
        WHERE 1=1 {line_filter}
        GROUP BY dow, hour
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@_cached
def get_travaux_ranking(start_date=None, end_date=None):
    """Rank lines by % of time spent in travaux (normal_trav status)."""
    conn = get_db()
    date_filter = _date_filter(start_date, end_date)
    rows = conn.execute(f"""
        SELECT line_type, line_id,
            COUNT(*) as total_checks,
            SUM(CASE WHEN status = 'normal_trav' THEN 1 ELSE 0 END) as travaux_checks,
            ROUND(100.0 * SUM(CASE WHEN status = 'normal_trav' THEN 1 ELSE 0 END) / COUNT(*), 1) as travaux_pct
        FROM line_status
        WHERE 1=1 {date_filter}
        GROUP BY line_type, line_id
        HAVING travaux_checks > 0
        ORDER BY travaux_pct DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_alerts(limit=5):
    """Latest alert per line currently in disruption, newest first."""
    conn = get_db()
    rows = conn.execute("""
        SELECT ls.line_type, ls.line_id, ls.status, ls.title, ls.message, ls.cause, ls.timestamp
        FROM line_status ls
        INNER JOIN (
            SELECT line_type, line_id, MAX(timestamp) as max_ts
            FROM line_status
            WHERE status = 'alerte'
            GROUP BY line_type, line_id
        ) latest ON ls.line_type = latest.line_type
            AND ls.line_id = latest.line_id
            AND ls.timestamp = latest.max_ts
        ORDER BY ls.timestamp DESC
        LIMIT ?
    """, (int(limit),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@_cached
def get_monthly_trend():
    """Current vs previous calendar month disruption % (whole network)."""
    conn = get_db()
    rows = conn.execute("""
        SELECT strftime('%Y-%m', timestamp) as ym,
            COUNT(*) as total,
            SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) as disrupted,
            ROUND(100.0 * SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) / COUNT(*), 2) as pct
        FROM line_status
        GROUP BY ym
        ORDER BY ym DESC
        LIMIT 2
    """).fetchall()
    conn.close()
    cur = dict(rows[0]) if len(rows) > 0 else None
    prev = dict(rows[1]) if len(rows) > 1 else None
    delta = None
    if cur and prev and prev["pct"] is not None:
        delta = round(cur["pct"] - prev["pct"], 2)
    return {"current": cur, "previous": prev, "delta": delta}


@_cached
def get_sparkline_data(days=7):
    """Daily disruption % per line over the last N days.
    Returns list of {line_type, line_id, series: [pct_day1, pct_day2, ...]}.
    Days are ordered oldest -> newest. Missing days are null."""
    conn = get_db()
    # Build last N day buckets based on the most recent timestamp in DB
    last = conn.execute("SELECT MAX(DATE(timestamp)) as d FROM line_status").fetchone()
    if not last or not last["d"]:
        conn.close()
        return []
    rows = conn.execute(f"""
        SELECT line_type, line_id, DATE(timestamp) as d,
            ROUND(100.0 * SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
        FROM line_status
        WHERE DATE(timestamp) > DATE(?, '-{int(days)} days')
        GROUP BY line_type, line_id, d
    """, (last["d"],)).fetchall()
    conn.close()

    # Build the N-day axis
    from datetime import datetime, timedelta
    end = datetime.strptime(last["d"], "%Y-%m-%d").date()
    axis = [(end - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]

    by_line = {}
    for r in rows:
        key = (r["line_type"], r["line_id"])
        if key not in by_line:
            by_line[key] = {d: None for d in axis}
        if r["d"] in by_line[key]:
            by_line[key][r["d"]] = r["pct"]

    out = []
    for (lt, lid), day_map in by_line.items():
        out.append({"line_type": lt, "line_id": lid, "series": [day_map[d] for d in axis]})
    return out


def get_metro_tram_ranking(line_type=None, year=None, month=None):
    """Rank metro/tram lines by average monthly disruption % (worst to best).
    Mirrors the SNCF ranking structure: avg_pct / min_pct / max_pct / months."""
    conn = get_db()
    if line_type:
        lt_filter = f"AND line_type = '{line_type}'"
    else:
        lt_filter = "AND line_type IN ('metro', 'tram')"
    date_filter = ""
    if year and month:
        date_filter = f"AND strftime('%Y-%m', timestamp) = '{year}-{month}'"
    elif year:
        date_filter = f"AND strftime('%Y', timestamp) = '{year}'"
    elif month:
        date_filter = f"AND strftime('%m', timestamp) = '{month}'"
    rows = conn.execute(f"""
        WITH monthly AS (
            SELECT line_type, line_id,
                strftime('%Y-%m', timestamp) as ym,
                ROUND(100.0 * SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
            FROM line_status
            WHERE 1=1 {lt_filter} {date_filter}
            GROUP BY line_type, line_id, ym
        )
        SELECT line_type, line_id,
            ROUND(AVG(pct), 1) as avg_pct,
            ROUND(MIN(pct), 1) as min_pct,
            ROUND(MAX(pct), 1) as max_pct,
            COUNT(*) as months
        FROM monthly
        GROUP BY line_type, line_id
        ORDER BY avg_pct DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_metro_tram_seasonal(line_type=None):
    """Average disruption % per calendar month (1-12) across all years, per line."""
    conn = get_db()
    if line_type:
        lt_filter = f"AND line_type = '{line_type}'"
    else:
        lt_filter = "AND line_type IN ('metro', 'tram')"
    rows = conn.execute(f"""
        WITH monthly AS (
            SELECT line_type, line_id,
                strftime('%Y-%m', timestamp) as ym,
                CAST(strftime('%m', timestamp) AS INTEGER) as month_num,
                ROUND(100.0 * SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
            FROM line_status
            WHERE 1=1 {lt_filter}
            GROUP BY line_type, line_id, ym
        )
        SELECT line_type, line_id, month_num,
            ROUND(AVG(pct), 1) as avg_pct
        FROM monthly
        GROUP BY line_type, line_id, month_num
        ORDER BY month_num, avg_pct DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_metro_tram_history(line_type, line_id):
    """Monthly evolution of disruption % for a given line."""
    conn = get_db()
    rows = conn.execute("""
        SELECT strftime('%Y-%m', timestamp) as date,
            ROUND(100.0 * SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) / COUNT(*), 1) as disruption_pct
        FROM line_status
        WHERE line_type = ? AND line_id = ?
        GROUP BY date
        ORDER BY date
    """, (line_type, line_id)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_metro_tram_by_year(line_type=None):
    """Yearly average disruption % per line."""
    conn = get_db()
    if line_type:
        lt_filter = f"AND line_type = '{line_type}'"
    else:
        lt_filter = "AND line_type IN ('metro', 'tram')"
    rows = conn.execute(f"""
        WITH monthly AS (
            SELECT line_type, line_id,
                strftime('%Y', timestamp) as year,
                strftime('%Y-%m', timestamp) as ym,
                ROUND(100.0 * SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
            FROM line_status
            WHERE 1=1 {lt_filter}
            GROUP BY line_type, line_id, ym
        )
        SELECT year, line_type, line_id,
            ROUND(AVG(pct), 1) as avg_pct
        FROM monthly
        GROUP BY year, line_type, line_id
        ORDER BY year, avg_pct DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_metro_tram_available_years():
    """Years for which metro/tram data is present."""
    conn = get_db()
    rows = conn.execute("""
        SELECT DISTINCT strftime('%Y', timestamp) as year
        FROM line_status
        WHERE line_type IN ('metro', 'tram')
        ORDER BY year
    """).fetchall()
    conn.close()
    return [r["year"] for r in rows]


def get_metro_tram_lines():
    """List of metro/tram lines with data (for timeline loop)."""
    conn = get_db()
    rows = conn.execute("""
        SELECT DISTINCT line_type, line_id
        FROM line_status
        WHERE line_type IN ('metro', 'tram')
        ORDER BY line_type,
            CASE WHEN line_id GLOB '[0-9]*' THEN CAST(line_id AS INTEGER) ELSE 999 END,
            line_id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_multi_line_stats(lines, start_date=None, end_date=None):
    """Get stats for multiple lines at once. lines = [(type, id), ...]"""
    if not lines:
        return []
    conn = get_db()
    date_filter = _date_filter(start_date, end_date)
    placeholders = " OR ".join(
        f"(line_type = '{lt}' AND line_id = '{li}')" for lt, li in lines
    )
    rows = conn.execute(f"""
        SELECT
            line_type, line_id,
            COUNT(*) as total,
            SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) as disrupted,
            ROUND(100.0 * SUM(CASE WHEN status = 'alerte' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
        FROM line_status
        WHERE ({placeholders}) {date_filter}
        GROUP BY line_type, line_id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Private helpers ---

def _date_filter(start_date, end_date):
    parts = ""
    if start_date:
        parts += f" AND timestamp >= '{start_date}'"
    if end_date:
        parts += f" AND timestamp <= '{end_date}'"
    return parts


def _line_filter(line_type, line_id):
    parts = ""
    if line_type:
        parts += f" AND line_type = '{line_type}'"
    if line_id:
        parts += f" AND line_id = '{line_id}'"
    return parts
