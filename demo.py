"""Generate realistic demo data spanning 30 days."""

import random
from datetime import datetime, timedelta
from database import get_db, init_db
from scraper import ALL_LINES

# Lines with higher disruption probability (realistic)
PROBLEMATIC_LINES = {
    ("rer", "B"): 0.25,
    ("rer", "A"): 0.18,
    ("rer", "D"): 0.20,
    ("rer", "C"): 0.17,
    ("metro", "13"): 0.15,
    ("metro", "12"): 0.10,
    ("metro", "7"): 0.09,
    ("metro", "8"): 0.09,
    ("metro", "4"): 0.08,
    ("metro", "3"): 0.07,
    ("metro", "6"): 0.06,
    ("metro", "1"): 0.02,
    ("metro", "14"): 0.02,
}

DISRUPTION_MESSAGES = [
    ("Trafic interrompu", "Suite a un incident voyageur, le trafic est interrompu entre {a} et {b}."),
    ("Trafic ralenti", "En raison d'un incident technique, le trafic est ralenti sur l'ensemble de la ligne."),
    ("Trafic perturbe", "Suite a un malaise voyageur, le trafic est perturbe."),
    ("Colis suspect", "Par mesure de securite, le trafic est interrompu suite a la decouverte d'un colis suspect."),
    ("Signal d'alarme", "Suite au declenchement d'un signal d'alarme, le trafic est perturbe."),
    ("Panne de signalisation", "En raison d'une panne de signalisation, le trafic est ralenti."),
    ("Incident technique", "Un incident technique perturbe la circulation des trains."),
    ("Affluence exceptionnelle", "En raison d'une affluence exceptionnelle, le temps d'attente est allonge."),
]

WORK_MESSAGES = [
    ("Travaux de modernisation", "Travaux de modernisation des voies. Interruption partielle du service."),
    ("Travaux de maintenance", "Travaux de maintenance nocturne. Dernier depart avance."),
    ("Travaux de renovation", "Travaux de renovation de la station. Certaines sorties sont fermees."),
]

STATIONS = [
    "Chatelet", "Gare du Nord", "Saint-Lazare", "Nation", "Republique",
    "Bastille", "Opera", "La Defense", "Gare de Lyon", "Montparnasse",
    "Auber", "Etoile", "Belleville", "Stalingrad", "Barbes",
]


def generate_demo_data(days=30):
    """Generate realistic demo data."""
    init_db()
    conn = get_db()

    # Clear existing data
    conn.execute("DELETE FROM line_status")
    conn.commit()

    now = datetime.now()
    start = now - timedelta(days=days)
    records = []

    # Generate data every 2 minutes for the past N days
    # But only during service hours (5h30 - 1h30) to be realistic
    current = start
    total_points = 0

    while current <= now:
        hour = current.hour
        # RATP service: ~5h30 to ~1h30
        in_service = (5 <= hour <= 23) or (hour == 0) or (hour == 1 and current.minute <= 30)

        if in_service:
            is_rush = hour in (7, 8, 9, 17, 18, 19)
            is_weekend = current.weekday() >= 5
            is_night = hour >= 22 or hour <= 6

            for line_type, line_id in ALL_LINES:
                key = (line_type, line_id)
                base_prob = PROBLEMATIC_LINES.get(key, 0.03)

                # Adjust probability
                prob = base_prob
                if is_rush:
                    prob *= 1.5  # More issues during rush hour
                if is_weekend:
                    prob *= 0.6  # Fewer issues on weekends
                    # But more works on weekends
                    works_prob = 0.08
                else:
                    works_prob = 0.02
                if is_night:
                    prob *= 0.3
                    works_prob *= 3  # More night works

                roll = random.random()
                if roll < prob:
                    title, msg = random.choice(DISRUPTION_MESSAGES)
                    a, b = random.sample(STATIONS, 2)
                    msg = msg.format(a=a, b=b)
                    records.append((
                        current.strftime("%Y-%m-%d %H:%M:%S"),
                        line_type, line_id, "alerte", title, msg
                    ))
                elif roll < prob + works_prob:
                    title, msg = random.choice(WORK_MESSAGES)
                    records.append((
                        current.strftime("%Y-%m-%d %H:%M:%S"),
                        line_type, line_id, "normal_trav", title, msg
                    ))
                else:
                    records.append((
                        current.strftime("%Y-%m-%d %H:%M:%S"),
                        line_type, line_id, "normal", "Trafic normal", ""
                    ))

            total_points += 1

        current += timedelta(minutes=2)

        # Bulk insert every 1000 timestamps
        if len(records) > 30000:
            conn.executemany(
                "INSERT INTO line_status (timestamp, line_type, line_id, status, title, message) VALUES (?, ?, ?, ?, ?, ?)",
                records,
            )
            conn.commit()
            records = []

    # Insert remaining
    if records:
        conn.executemany(
            "INSERT INTO line_status (timestamp, line_type, line_id, status, title, message) VALUES (?, ?, ?, ?, ?, ?)",
            records,
        )
        conn.commit()

    conn.close()

    total_records = total_points * len(ALL_LINES)
    print(f"Demo: generated {total_records:,} records over {days} days ({total_points} timestamps x {len(ALL_LINES)} lines)")
    return total_records
