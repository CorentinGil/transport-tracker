
import sqlite3
from datetime import datetime, timedelta
from collections import defaultdict

DB = r"C:\Users\cesamseed\Desktop\Bureau\Bureau Corentin\ratp-tracker\data\ratp.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# 1. timestamps uniques de scrape (1 timestamp = 1 collecte sur 35 lignes)
rows = conn.execute("""
    SELECT DISTINCT timestamp FROM line_status
    WHERE timestamp >= '2026-04-20'
    ORDER BY timestamp
""").fetchall()
ts = [datetime.fromisoformat(r["timestamp"]) for r in rows]
print(f"Scrapes uniques depuis le 2026-04-20 : {len(ts)}")
print(f"Premier : {ts[0]}")
print(f"Dernier : {ts[-1]}")

# 2. Trous > 10 min
gaps = []
for a, b in zip(ts, ts[1:]):
    d = (b - a).total_seconds() / 60
    if d > 10:
        gaps.append((a, b, d))
print(f"\nTrous > 10 min : {len(gaps)}")
for a, b, d in gaps[:30]:
    print(f"  {a}  ->  {b}   ({d:.0f} min, {d/60:.1f} h)")
if len(gaps) > 30:
    print(f"  ... et {len(gaps)-30} autres")

# 3. Total trou cumule
total_missing_min = sum(d for _, _, d in gaps) - len(gaps) * 5  # 5 min = intervalle normal
print(f"\nTemps cumule perdu : {total_missing_min/60:.1f} h")

# 4. Couverture totale theorique vs reelle
expected = (ts[-1] - ts[0]).total_seconds() / 60 / 5
print(f"Couverture : {len(ts)} / {expected:.0f} scrapes ({100*len(ts)/expected:.1f} %)")

# 5. Classement metro 2026 par taux de perturbation
print("\n=== Classement metro 2026 (taux d'alerte) ===")
ranking = conn.execute("""
    SELECT line_id,
           COUNT(*) AS total,
           SUM(CASE WHEN status='alerte' THEN 1 ELSE 0 END) AS alerts,
           SUM(CASE WHEN status='normal_trav' THEN 1 ELSE 0 END) AS travaux,
           ROUND(100.0 * SUM(CASE WHEN status='alerte' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_alert,
           ROUND(100.0 * SUM(CASE WHEN status IN ('alerte','normal_trav') THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_disrupt
    FROM line_status
    WHERE line_type='metro' AND timestamp >= '2026-01-01'
    GROUP BY line_id
    ORDER BY pct_alert DESC
""").fetchall()
print(f"{'Ligne':<6} {'Total':>7} {'Alertes':>8} {'Travaux':>8} {'%Alert':>7} {'%Total':>7}")
for r in ranking:
    print(f"{r['line_id']:<6} {r['total']:>7} {r['alerts']:>8} {r['travaux']:>8} {r['pct_alert']:>7} {r['pct_disrupt']:>7}")

# 6. Focus ligne 6 : quand a-t-elle ete en alerte ?
print("\n=== Periodes d'alerte ligne 6 (echantillon) ===")
m6 = conn.execute("""
    SELECT timestamp, status, substr(title, 1, 60) AS title_short
    FROM line_status
    WHERE line_type='metro' AND line_id='6' AND status='alerte'
      AND timestamp >= '2026-01-01'
    ORDER BY timestamp
""").fetchall()
print(f"Total enregistrements alerte ligne 6 en 2026 : {len(m6)}")
if m6:
    # premiers et derniers
    print("Premiers :")
    for r in m6[:5]:
        print(f"  {r['timestamp']}  {r['title_short']}")
    print("Derniers :")
    for r in m6[-5:]:
        print(f"  {r['timestamp']}  {r['title_short']}")

    # Plages continues (alerte d'affilee)
    print("\nPlages continues d'alerte (>= 30 min) :")
    prev = None
    start = None
    plages = []
    for r in m6:
        t = datetime.fromisoformat(r['timestamp'])
        if prev is None:
            start = t
        elif (t - prev).total_seconds() > 600:  # gap > 10 min = nouvelle plage
            plages.append((start, prev))
            start = t
        prev = t
    if start:
        plages.append((start, prev))
    long_plages = [(a, b, (b-a).total_seconds()/60) for a, b in plages if (b-a).total_seconds() >= 1800]
    long_plages.sort(key=lambda x: -x[2])
    for a, b, d in long_plages[:15]:
        print(f"  {a}  ->  {b}   ({d:.0f} min, {d/60:.1f} h)")
    print(f"... total plages >=30min : {len(long_plages)}")

conn.close()
