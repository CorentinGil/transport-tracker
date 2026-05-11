
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta

DB = r"C:\Users\cesamseed\Desktop\Bureau\Bureau Corentin\ratp-tracker\data\ratp.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# Periode
r = conn.execute("SELECT MIN(timestamp) AS a, MAX(timestamp) AS b, COUNT(DISTINCT timestamp) AS n FROM line_status").fetchone()
print(f"Periode : {r['a']}  ->  {r['b']}   ({r['n']} scrapes)")

# === 1. Classement complet (alerte pure) toutes lignes ===
print("\n=== TOP perturbations (alerte seule) toutes lignes confondues ===")
rows = conn.execute("""
    SELECT line_type, line_id,
           ROUND(100.0 * SUM(CASE WHEN status='alerte' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct
    FROM line_status
    WHERE timestamp >= '2026-04-01'
    GROUP BY line_type, line_id
    HAVING pct > 0
    ORDER BY pct DESC
    LIMIT 15
""").fetchall()
for r in rows:
    print(f"  {r['line_type']:<5} {r['line_id']:<4}  {r['pct']:>6} %")

# === 2. Heure noire (toutes lignes confondues) ===
print("\n=== Pires heures de la journee (% scrapes en alerte) ===")
rows = conn.execute("""
    SELECT CAST(strftime('%H', timestamp) AS INT) AS h,
           ROUND(100.0 * SUM(CASE WHEN status='alerte' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct
    FROM line_status
    WHERE timestamp >= '2026-04-01'
    GROUP BY h
    ORDER BY h
""").fetchall()
for r in rows:
    bar = "#" * int(r['pct'] * 3)
    print(f"  {r['h']:>2}h  {r['pct']:>5} %  {bar}")

# === 3. Jour le plus perturbe ===
print("\n=== Jour de la semaine (% alerte) ===")
days = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
rows = conn.execute("""
    SELECT CAST(strftime('%w', timestamp) AS INT) AS d,
           ROUND(100.0 * SUM(CASE WHEN status='alerte' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct
    FROM line_status
    WHERE timestamp >= '2026-04-01'
    GROUP BY d
    ORDER BY d
""").fetchall()
# strftime %w : 0=dim, 1=lun, ...
for r in rows:
    idx = (r['d'] - 1) % 7  # remap 0=dim -> 6
    print(f"  {days[idx]}  {r['pct']:>5} %")

# === 4. Top causes ===
print("\n=== Top motifs d'alerte ===")
all_titles = conn.execute("""
    SELECT title FROM line_status
    WHERE status='alerte' AND timestamp >= '2026-04-01' AND title IS NOT NULL
""").fetchall()
def extract_cause(t):
    if not t: return None
    t = t.lower()
    keywords = [
        ("bagage", "Bagage oublie"),
        ("malaise", "Malaise voyageur"),
        ("panne", "Panne materiel"),
        ("colis suspect", "Colis suspect"),
        ("accident", "Accident voyageur"),
        ("incident technique", "Incident technique"),
        ("regulation", "Regulation"),
        ("signalisation", "Signalisation"),
        ("electric", "Probleme electrique"),
        ("rame", "Probleme de rame"),
        ("voie", "Probleme voie"),
        ("incident voyageur", "Incident voyageur"),
        ("manifestation", "Manifestation"),
        ("intemperies", "Intemperies"),
    ]
    for k, label in keywords:
        if k in t:
            return label
    return "Autre"
c = Counter(extract_cause(r['title']) for r in all_titles)
for cause, n in c.most_common(10):
    pct = 100*n/len(all_titles)
    print(f"  {cause:<25} {n:>5}  ({pct:.1f} %)")

# === 5. Plus longues plages d'alerte (toutes lignes) ===
print("\n=== Top 10 plages continues d'alerte (toutes lignes) ===")
rows = conn.execute("""
    SELECT line_type, line_id, timestamp, status
    FROM line_status
    WHERE timestamp >= '2026-04-01' AND status='alerte'
    ORDER BY line_type, line_id, timestamp
""").fetchall()
plages = []
prev_line = None
prev_t = None
start = None
for r in rows:
    line = (r['line_type'], r['line_id'])
    t = datetime.fromisoformat(r['timestamp'])
    if line != prev_line:
        if start and prev_t:
            plages.append((prev_line, start, prev_t))
        start = t
    elif (t - prev_t).total_seconds() > 900:
        plages.append((prev_line, start, prev_t))
        start = t
    prev_line = line
    prev_t = t
if start: plages.append((prev_line, start, prev_t))
plages_d = [((ln, a, b), (b-a).total_seconds()/60) for ln, a, b in plages]
plages_d.sort(key=lambda x: -x[1])
for (ln, a, b), d in plages_d[:10]:
    print(f"  {ln[0]:<5} {ln[1]:<4}  {a.strftime('%a %d/%m %Hh%M')} -> {b.strftime('%a %d/%m %Hh%M')}  ({d/60:.1f} h)")

# === 6. Comparaison par mode ===
print("\n=== Taux d'alerte par mode ===")
rows = conn.execute("""
    SELECT line_type,
           ROUND(100.0 * SUM(CASE WHEN status='alerte' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct,
           COUNT(DISTINCT line_id) AS n
    FROM line_status
    WHERE timestamp >= '2026-04-01'
    GROUP BY line_type
""").fetchall()
for r in rows:
    print(f"  {r['line_type']:<5}  {r['pct']:>5} %  ({r['n']} lignes)")

# === 7. Volatilite : lignes les plus "imprevisibles" ===
# Volatilite = nombre de transitions normal<->alerte / nb scrapes
print("\n=== Lignes les plus instables (nb transitions normal/alerte par jour) ===")
rows = conn.execute("""
    SELECT line_type, line_id, timestamp, status
    FROM line_status
    WHERE timestamp >= '2026-04-01' AND status IN ('normal', 'alerte')
    ORDER BY line_type, line_id, timestamp
""").fetchall()
trans = defaultdict(int)
prev_line = None
prev_status = None
for r in rows:
    line = (r['line_type'], r['line_id'])
    if line == prev_line and r['status'] != prev_status:
        trans[line] += 1
    prev_line = line
    prev_status = r['status']
# duree de la periode
total_days = (datetime.fromisoformat(rows[-1]['timestamp']) - datetime.fromisoformat(rows[0]['timestamp'])).total_seconds() / 86400
items = sorted(trans.items(), key=lambda x: -x[1])
for (lt, li), n in items[:10]:
    print(f"  {lt:<5} {li:<4}  {n} transitions  ({n/total_days:.1f}/jour)")

# === 8. Pic chronique : meilleure heure / pire heure pour une ligne donnee ===
print("\n=== Heure noire vs heure calme pour 4 lignes phares ===")
for lt, li in [("metro","6"), ("metro","13"), ("metro","9"), ("metro","1")]:
    rows = conn.execute("""
        SELECT CAST(strftime('%H', timestamp) AS INT) AS h,
               ROUND(100.0 * SUM(CASE WHEN status='alerte' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct
        FROM line_status
        WHERE timestamp >= '2026-04-01' AND line_type=? AND line_id=?
        GROUP BY h
        ORDER BY pct DESC
    """, (lt, li)).fetchall()
    if rows:
        worst = rows[0]
        best = rows[-1]
        print(f"  {lt} {li}  pire = {worst['h']}h ({worst['pct']}%)  /  meilleure = {best['h']}h ({best['pct']}%)")

# === 9. Weekend vs semaine ===
print("\n=== Weekend vs semaine ===")
r = conn.execute("""
    SELECT
        ROUND(100.0 * SUM(CASE WHEN status='alerte' AND strftime('%w', timestamp) IN ('0','6') THEN 1 ELSE 0 END)
              / NULLIF(SUM(CASE WHEN strftime('%w', timestamp) IN ('0','6') THEN 1 ELSE 0 END), 0), 2) AS we,
        ROUND(100.0 * SUM(CASE WHEN status='alerte' AND strftime('%w', timestamp) NOT IN ('0','6') THEN 1 ELSE 0 END)
              / NULLIF(SUM(CASE WHEN strftime('%w', timestamp) NOT IN ('0','6') THEN 1 ELSE 0 END), 0), 2) AS sem
    FROM line_status
    WHERE timestamp >= '2026-04-01'
""").fetchone()
print(f"  Semaine : {r['sem']} %")
print(f"  Weekend : {r['we']} %")

conn.close()
