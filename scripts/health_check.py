"""Health check — lance avec: python scripts/health_check.py
Analyse la base et les logs pour detecter coupures, gaps, et anomalies."""

import sqlite3
import os
import sys
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ratp.db")
LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "autostart.log")
SCRAPE_INTERVAL = 5  # minutes

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

def ok(msg): print(f"  {GREEN}OK{RESET}  {msg}")
def warn(msg): print(f"  {YELLOW}!!{RESET}  {msg}")
def fail(msg): print(f"  {RED}XX{RESET}  {msg}")
def info(msg): print(f"  {CYAN}--{RESET}  {msg}")

def check_db():
    if not os.path.exists(DB_PATH):
        fail("Base de donnees introuvable: " + DB_PATH)
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def main():
    print(f"\n{BOLD}{'='*50}")
    print(f"  Transport Tracker — Health Check")
    print(f"  {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*50}{RESET}\n")

    conn = check_db()
    if not conn:
        return

    # --- Global stats ---
    print(f"{BOLD}1. Vue d'ensemble{RESET}")
    r = conn.execute("SELECT MIN(timestamp) as mn, MAX(timestamp) as mx, COUNT(*) as n FROM line_status").fetchone()
    if not r["n"]:
        fail("Aucune donnee dans la base")
        return

    first = r["mn"]
    last = r["mx"]
    total = r["n"]
    lines = conn.execute("SELECT COUNT(DISTINCT line_type || '-' || line_id) FROM line_status").fetchone()[0]
    ok(f"{total:,} releves | {lines} lignes | {first} -> {last}")

    last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
    age = datetime.now() - last_dt
    if age.total_seconds() > 600:
        fail(f"Dernier releve il y a {int(age.total_seconds()//60)} min — scraper peut-etre arrete")
    else:
        ok(f"Dernier releve il y a {int(age.total_seconds()//60)} min")

    # --- Daily coverage ---
    print(f"\n{BOLD}2. Couverture par jour (7 derniers jours){RESET}")
    days = conn.execute("""
        SELECT DATE(timestamp) as d,
            COUNT(*) as n,
            MIN(strftime('%H:%M', timestamp)) as first_h,
            MAX(strftime('%H:%M', timestamp)) as last_h,
            COUNT(DISTINCT strftime('%H', timestamp)) as hours_covered
        FROM line_status
        WHERE DATE(timestamp) >= DATE('now', '-7 days')
        GROUP BY d ORDER BY d
    """).fetchall()

    expected_per_day = (24 * 60 / SCRAPE_INTERVAL) * lines
    for d in days:
        pct = 100 * d["n"] / expected_per_day
        bar = "#" * int(pct / 5) + "." * (20 - int(pct / 5))
        status = ok if pct > 90 else warn if pct > 50 else fail
        hours = d["hours_covered"]
        status(f"{d['d']}  {bar} {pct:5.1f}%  ({d['n']:>5} rel, {d['first_h']}-{d['last_h']}, {hours}h/24)")

    # --- Gap detection ---
    print(f"\n{BOLD}3. Detection des coupures (gaps > 15 min){RESET}")
    gaps = conn.execute("""
        WITH ordered AS (
            SELECT timestamp,
                LAG(timestamp) OVER (ORDER BY timestamp) as prev_ts
            FROM (SELECT DISTINCT timestamp FROM line_status)
        )
        SELECT prev_ts as gap_start, timestamp as gap_end,
            ROUND((JULIANDAY(timestamp) - JULIANDAY(prev_ts)) * 24 * 60, 0) as gap_minutes
        FROM ordered
        WHERE prev_ts IS NOT NULL
            AND (JULIANDAY(timestamp) - JULIANDAY(prev_ts)) * 24 * 60 > 15
        ORDER BY gap_minutes DESC
        LIMIT 10
    """).fetchall()

    if not gaps:
        ok("Aucune coupure detectee")
    else:
        warn(f"{len(gaps)} coupure(s) trouvee(s):")
        for g in gaps:
            mins = int(g["gap_minutes"])
            h = mins // 60
            m = mins % 60
            duration = f"{h}h{m:02d}" if h else f"{m} min"
            fail(f"  {g['gap_start']} -> {g['gap_end']}  ({duration} de trou)")

    # --- Current disruptions ---
    print(f"\n{BOLD}4. Etat actuel du reseau{RESET}")
    current = conn.execute("""
        SELECT ls.line_type, ls.line_id, ls.status, ls.title
        FROM line_status ls
        INNER JOIN (
            SELECT line_type, line_id, MAX(timestamp) as max_ts
            FROM line_status GROUP BY line_type, line_id
        ) latest ON ls.line_type = latest.line_type
            AND ls.line_id = latest.line_id
            AND ls.timestamp = latest.max_ts
        ORDER BY ls.status DESC, ls.line_type, ls.line_id
    """).fetchall()

    alerts = [c for c in current if c["status"] == "alerte"]
    travaux = [c for c in current if c["status"] == "normal_trav"]
    normal = [c for c in current if c["status"] == "normal"]

    ok(f"{len(normal)} lignes normales")
    if travaux:
        warn(f"{len(travaux)} lignes en travaux: {', '.join(c['line_type']+' '+c['line_id'] for c in travaux)}")
    if alerts:
        fail(f"{len(alerts)} lignes perturbees:")
        for a in alerts:
            title = (a["title"] or "")[:80]
            info(f"  {a['line_type']} {a['line_id']}: {title}")

    # --- Restarts ---
    print(f"\n{BOLD}5. Redemarrages Flask (depuis autostart.log){RESET}")
    if os.path.exists(LOG_PATH):
        restarts = []
        crashes = []
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "Demarrage de app.py" in line:
                    restarts.append(line.strip()[:25])
                if "s'est arrete" in line or "error" in line.lower():
                    crashes.append(line.strip()[:80])
        if len(restarts) <= 1:
            ok(f"1 seul demarrage (pas de crash)")
        else:
            warn(f"{len(restarts)} demarrage(s) detecte(s):")
            for r in restarts[-5:]:
                info(f"  {r}")
        if crashes:
            warn(f"{len(crashes)} erreur(s)/arret(s):")
            for c in crashes[-3:]:
                info(f"  {c}")
    else:
        warn("autostart.log introuvable")

    # --- Summary ---
    print(f"\n{BOLD}{'='*50}")
    if not gaps and age.total_seconds() <= 600:
        print(f"  {GREEN}TOUT EST OK — collecte continue, aucune coupure{RESET}")
    elif age.total_seconds() > 600:
        print(f"  {RED}ATTENTION — scraper semble arrete{RESET}")
    else:
        print(f"  {YELLOW}DES COUPURES DETECTEES — voir details ci-dessus{RESET}")
    print(f"{'='*50}{RESET}\n")

    conn.close()

if __name__ == "__main__":
    main()
