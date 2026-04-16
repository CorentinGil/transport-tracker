import requests
import logging
import os
import re
from datetime import datetime
from database import get_db
from config import get_api_key

# Log to file + console
LOG_PATH = os.path.join(os.path.dirname(__file__), "data", "scraper.log")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("scraper")

TRAFFIC_REPORTS_URL = "https://prim.iledefrance-mobilites.fr/marketplace/v2/navitia/traffic_reports"

# Lines we track: line name in API -> (type, display_id)
TRACKED_LINES = {
    # Metro
    ("Métro", "1"): ("metro", "1"),
    ("Métro", "2"): ("metro", "2"),
    ("Métro", "3"): ("metro", "3"),
    ("Métro", "3B"): ("metro", "3b"),
    ("Métro", "4"): ("metro", "4"),
    ("Métro", "5"): ("metro", "5"),
    ("Métro", "6"): ("metro", "6"),
    ("Métro", "7"): ("metro", "7"),
    ("Métro", "7B"): ("metro", "7b"),
    ("Métro", "8"): ("metro", "8"),
    ("Métro", "9"): ("metro", "9"),
    ("Métro", "10"): ("metro", "10"),
    ("Métro", "11"): ("metro", "11"),
    ("Métro", "12"): ("metro", "12"),
    ("Métro", "13"): ("metro", "13"),
    ("Métro", "14"): ("metro", "14"),
    # RER
    ("RER", "A"): ("rer", "A"),
    ("RER", "B"): ("rer", "B"),
    ("RER", "C"): ("rer", "C"),
    ("RER", "D"): ("rer", "D"),
    ("RER", "E"): ("rer", "E"),
    # Tram
    ("Tramway", "T1"): ("tram", "T1"),
    ("Tramway", "T2"): ("tram", "T2"),
    ("Tramway", "T3A"): ("tram", "T3a"),
    ("Tramway", "T3B"): ("tram", "T3b"),
    ("Tramway", "T4"): ("tram", "T4"),
    ("Tramway", "T5"): ("tram", "T5"),
    ("Tramway", "T6"): ("tram", "T6"),
    ("Tramway", "T7"): ("tram", "T7"),
    ("Tramway", "T8"): ("tram", "T8"),
    ("Tramway", "T9"): ("tram", "T9"),
    ("Tramway", "T10"): ("tram", "T10"),
    ("Tramway", "T11"): ("tram", "T11"),
    ("Tramway", "T12"): ("tram", "T12"),
    ("Tramway", "T13"): ("tram", "T13"),
}

ALL_LINES = list(set(TRACKED_LINES.values()))

# Severity mapping from Navitia to our status
SEVERITY_TO_STATUS = {
    "NO_SERVICE": "alerte",
    "SIGNIFICANT_DELAYS": "alerte",
    "REDUCED_SERVICE": "alerte",
    "DETOUR": "alerte",
    "MODIFIED_SERVICE": "normal_trav",
    "OTHER_EFFECT": "normal_trav",
    "UNKNOWN_EFFECT": "normal_trav",
}


def _clean_html(text):
    """Remove HTML tags from message text."""
    return re.sub(r'<[^>]+>', ' ', text).strip()


def _extract_detailed_cause(title, api_cause):
    """Extract a more specific cause from the message title."""
    tl = title.lower()
    if any(w in tl for w in ["panne de signal", "signalisation"]):
        return "panne signalisation"
    if any(w in tl for w in ["metro en panne", "panne de train", "panne technique", "incident technique"]):
        return "panne materiel"
    if any(w in tl for w in ["panne de porte", "portes de train"]):
        return "panne de portes"
    if "bagage" in tl or "colis" in tl:
        return "bagage/colis suspect"
    if "voyageur" in tl and ("malaise" in tl or "accident" in tl):
        return "accident voyageur"
    if any(w in tl for w in ["manifestation", "marathon"]):
        return "manifestation/evenement"
    if any(w in tl for w in ["malveillance", "vandalisme"]):
        return "acte de malveillance"
    if "greve" in tl or "mouvement social" in tl:
        return "greve"
    if any(w in tl for w in ["signal d'alarme", "alarme"]):
        return "signal d'alarme"
    if "affluence" in tl:
        return "affluence"
    if any(w in tl for w in ["vitesse reduite", "vitesse réduite"]):
        return "ralentissement"
    if "travaux" in tl or api_cause == "travaux":
        return "travaux"
    if any(w in tl for w in ["mesures de securite", "mesures de sécurité"]):
        return "mesures de securite"
    if any(w in tl for w in ["suppression", "suppressions"]):
        return "suppressions de trains"
    if "interrompu" in tl:
        return "trafic interrompu"
    if api_cause:
        return api_cause
    return "autre"


def scrape_and_store():
    """Fetch all line statuses via traffic_reports (single API call) and store in DB."""
    api_key = get_api_key()
    if not api_key:
        log.warning("No API key configured. Skipping scrape.")
        return False

    try:
        resp = requests.get(
            TRAFFIC_REPORTS_URL,
            params={"count": 100, "disable_geojson": "true"},
            headers={"apikey": api_key, "Accept": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error(f"Scrape error: {e}")
        return False

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Build disruption lookup
    disruptions = {}
    for d in data.get("disruptions", []):
        disruptions[d["id"]] = d

    # Parse traffic reports to find our lines
    found_lines = {}  # (type, id) -> {"status", "title", "cause"}

    for report in data.get("traffic_reports", []):
        for line in report.get("lines", []):
            mode = line.get("commercial_mode", {}).get("name", "")
            name = line.get("name", "")
            key = (mode, name.upper() if mode == "Tramway" else name)

            tracked = TRACKED_LINES.get(key)
            if not tracked:
                key2 = (mode, name)
                tracked = TRACKED_LINES.get(key2)
            if not tracked:
                continue

            # Check ALL disruptions linked to this line (active + future)
            line_disruptions = []
            for link in line.get("links", []):
                if link.get("type") == "disruption":
                    d = disruptions.get(link.get("id"))
                    if d and d.get("status") in ("active", "future"):
                        line_disruptions.append(d)

            if not line_disruptions:
                if tracked not in found_lines:
                    found_lines[tracked] = {"status": "normal", "title": "Trafic normal", "cause": ""}
                continue

            # Find worst disruption (active > future, then by severity)
            worst_status = "normal"
            worst_title = ""
            worst_cause = ""
            worst_prio = 0

            for d in line_disruptions:
                severity = d.get("severity", {}).get("effect", "")
                status = SEVERITY_TO_STATUS.get(severity, "normal_trav")
                cause = d.get("cause", "")
                is_active = d.get("status") == "active"

                # Get message text
                msgs = d.get("messages", [])
                title = ""
                for m in msgs:
                    title = _clean_html(m.get("text", ""))
                    if title:
                        break

                # Travaux = always "normal_trav", never "alerte"
                detailed_cause = _extract_detailed_cause(title, cause)
                if cause == "travaux" or detailed_cause == "travaux":
                    status = "normal_trav"

                # Priority: active alerte > active travaux > future alerte > future travaux
                prio = {"normal": 0, "normal_trav": 1, "alerte": 2}
                p = prio.get(status, 0) + (10 if is_active else 0)

                if p > worst_prio:
                    worst_prio = p
                    worst_status = status
                    worst_title = title[:500]
                    worst_cause = detailed_cause

            found_lines[tracked] = {
                "status": worst_status,
                "title": worst_title or f"Perturbation ({worst_cause})",
                "cause": worst_cause,
            }

    # Build records for ALL tracked lines
    records = []
    for line_type, line_id in ALL_LINES:
        key = (line_type, line_id)
        if key in found_lines:
            info = found_lines[key]
            records.append((timestamp, line_type, line_id, info["status"], info["title"], "", info["cause"]))
        else:
            records.append((timestamp, line_type, line_id, "normal", "Trafic normal", "", ""))

    conn = get_db()
    conn.executemany(
        "INSERT INTO line_status (timestamp, line_type, line_id, status, title, message, cause) VALUES (?, ?, ?, ?, ?, ?, ?)",
        records,
    )
    conn.commit()
    conn.close()

    disrupted = sum(1 for r in records if r[3] == "alerte")
    works = sum(1 for r in records if r[3] == "normal_trav")
    normal = sum(1 for r in records if r[3] == "normal")

    log.info(f"Scraped {len(records)} lines: {normal} OK, {disrupted} perturbees, {works} travaux")

    for r in records:
        if r[3] == "alerte":
            log.info(f"  ALERTE {r[1]} {r[2]}: {r[4]}")
        elif r[3] == "normal_trav":
            log.info(f"  TRAVAUX {r[1]} {r[2]}: {r[4]}")

    return True


def test_api_key(api_key):
    """Test if an API key works."""
    try:
        resp = requests.get(
            TRAFFIC_REPORTS_URL,
            params={"count": 1, "disable_geojson": "true"},
            headers={"apikey": api_key, "Accept": "application/json"},
            timeout=10,
        )
        data = resp.json()
        return "traffic_reports" in data
    except Exception:
        return False
