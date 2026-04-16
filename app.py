from flask import Flask, render_template, jsonify, request, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
from database import init_db, get_current_status, get_disruption_ranking, \
    get_disruptions_by_hour, get_disruptions_by_day_of_week, get_disruptions_by_date, \
    get_disruptions_by_month, get_line_history, get_stats_summary, get_available_lines, \
    get_available_years, get_db, get_heatmap_data, get_prediction_data, get_multi_line_stats, \
    get_cause_ranking, get_cause_by_line, get_metro_tram_ranking, get_metro_tram_seasonal, \
    get_metro_tram_history, get_metro_tram_by_year, get_metro_tram_available_years, \
    get_metro_tram_lines, get_monthly_trend, get_sparkline_data, get_recent_alerts
from scraper import scrape_and_store, test_api_key
from config import load_config, save_config, get_api_key
from demo import generate_demo_data
from sncf_data import init_sncf_tables, sync_sncf_data, get_sncf_lines, \
    get_sncf_by_line, get_sncf_history, get_sncf_by_year, get_sncf_available_years, \
    get_sncf_by_month_avg

app = Flask(__name__)
scheduler = None

# --- Pages ---

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/line/<line_type>/<line_id>")
def line_detail(line_type, line_id):
    return render_template("line_detail.html", line_type=line_type, line_id=line_id)


@app.route("/punctuality")
def punctuality():
    return render_template("punctuality.html")


@app.route("/worst-metro-tram")
def worst_metro_tram():
    return render_template("worst_metro_tram.html")


@app.route("/reimbursements")
def reimbursements():
    return render_template("reimbursements.html")


@app.route("/my-commute")
def my_commute():
    return render_template("my_commute.html")


@app.route("/compare")
def compare():
    return render_template("compare.html")


@app.route("/map")
def network_map():
    return render_template("map.html")


@app.route("/settings", methods=["GET", "POST"])
def settings():
    config = load_config()
    message = None
    error = None

    if request.method == "POST":
        api_key = request.form.get("prim_api_key", "").strip()
        if api_key:
            if test_api_key(api_key):
                config["prim_api_key"] = api_key
                save_config(config)
                scrape_and_store()
                _restart_scheduler()
                return redirect(url_for("index"))
            else:
                error = "Cle API invalide. Verifiez-la et reessayez."
        else:
            error = "Veuillez entrer une cle API."

    return render_template("settings.html", config=config, message=message, error=error)


# --- API: Real-time disruptions ---

@app.route("/api/current")
def api_current():
    return jsonify(get_current_status())

@app.route("/api/ranking")
def api_ranking():
    start, end = _get_dates()
    return jsonify(get_disruption_ranking(start, end))

@app.route("/api/by-hour")
def api_by_hour():
    start, end = _get_dates()
    lt, li = request.args.get("line_type"), request.args.get("line_id")
    return jsonify(get_disruptions_by_hour(start, end, lt, li))

@app.route("/api/by-dow")
def api_by_dow():
    start, end = _get_dates()
    lt, li = request.args.get("line_type"), request.args.get("line_id")
    return jsonify(get_disruptions_by_day_of_week(start, end, lt, li))

@app.route("/api/by-date")
def api_by_date():
    start, end = _get_dates()
    lt, li = request.args.get("line_type"), request.args.get("line_id")
    return jsonify(get_disruptions_by_date(start, end, lt, li))

@app.route("/api/by-month")
def api_by_month():
    start, end = _get_dates()
    lt, li = request.args.get("line_type"), request.args.get("line_id")
    return jsonify(get_disruptions_by_month(start, end, lt, li))

@app.route("/api/heatmap")
def api_heatmap():
    lt, li = request.args.get("line_type"), request.args.get("line_id")
    return jsonify(get_heatmap_data(lt, li))

@app.route("/api/predict")
def api_predict():
    lt, li = request.args.get("line_type"), request.args.get("line_id")
    return jsonify(get_prediction_data(lt, li))

@app.route("/api/multi-stats")
def api_multi_stats():
    # lines param format: "metro-8,rer-A"
    lines_str = request.args.get("lines", "")
    start, end = _get_dates()
    lines = []
    for l in lines_str.split(","):
        parts = l.strip().split("-", 1)
        if len(parts) == 2:
            lines.append((parts[0], parts[1]))
    return jsonify(get_multi_line_stats(lines, start, end))

@app.route("/api/causes")
def api_causes():
    start, end = _get_dates()
    lt, li = request.args.get("line_type"), request.args.get("line_id")
    return jsonify(get_cause_ranking(start, end, lt, li))

@app.route("/api/causes-by-line")
def api_causes_by_line():
    start, end = _get_dates()
    return jsonify(get_cause_by_line(start, end))

@app.route("/api/line-history/<line_type>/<line_id>")
def api_line_history(line_type, line_id):
    start, end = _get_dates()
    return jsonify(get_line_history(line_type, line_id, start, end))

@app.route("/api/summary")
def api_summary():
    start, end = _get_dates()
    return jsonify(get_stats_summary(start, end))

@app.route("/api/trend")
def api_trend():
    return jsonify(get_monthly_trend())

@app.route("/api/sparklines")
def api_sparklines():
    days = request.args.get("days", 7, type=int)
    return jsonify(get_sparkline_data(days))

@app.route("/api/recent-alerts")
def api_recent_alerts():
    limit = request.args.get("limit", 5, type=int)
    return jsonify(get_recent_alerts(limit))

@app.route("/api/lines")
def api_lines():
    return jsonify(get_available_lines())

@app.route("/api/years")
def api_years():
    return jsonify(get_available_years())

@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    ok = scrape_and_store()
    return jsonify({"success": ok})

@app.route("/api/demo", methods=["POST"])
def api_demo():
    days = request.args.get("days", 30, type=int)
    count = generate_demo_data(days)
    return jsonify({"success": True, "records": count})


# --- API: SNCF Punctuality ---

@app.route("/api/sncf/sync", methods=["POST"])
def api_sncf_sync():
    count = sync_sncf_data()
    return jsonify({"success": True, "records": count})

@app.route("/api/sncf/lines")
def api_sncf_lines():
    return jsonify(get_sncf_lines())

@app.route("/api/sncf/ranking")
def api_sncf_ranking():
    service = request.args.get("service")
    year = request.args.get("year")
    month = request.args.get("month")
    return jsonify(get_sncf_by_line(service, year, month))

@app.route("/api/sncf/seasonal")
def api_sncf_seasonal():
    service = request.args.get("service")
    return jsonify(get_sncf_by_month_avg(service))

@app.route("/api/sncf/years")
def api_sncf_years():
    return jsonify(get_sncf_available_years())

@app.route("/api/sncf/history/<line>")
def api_sncf_history(line):
    service = request.args.get("service")
    return jsonify(get_sncf_history(line, service))

@app.route("/api/sncf/by-year")
def api_sncf_by_year():
    service = request.args.get("service")
    return jsonify(get_sncf_by_year(service))


# --- API: Metro & Tram monthly stats (mirrors SNCF structure) ---

@app.route("/api/mt/ranking")
def api_mt_ranking():
    lt = request.args.get("line_type")
    year = request.args.get("year")
    month = request.args.get("month")
    return jsonify(get_metro_tram_ranking(lt, year, month))

@app.route("/api/mt/seasonal")
def api_mt_seasonal():
    lt = request.args.get("line_type")
    return jsonify(get_metro_tram_seasonal(lt))

@app.route("/api/mt/history/<line_type>/<line_id>")
def api_mt_history(line_type, line_id):
    return jsonify(get_metro_tram_history(line_type, line_id))

@app.route("/api/mt/by-year")
def api_mt_by_year():
    lt = request.args.get("line_type")
    return jsonify(get_metro_tram_by_year(lt))

@app.route("/api/mt/years")
def api_mt_years():
    return jsonify(get_metro_tram_available_years())

@app.route("/api/mt/lines")
def api_mt_lines():
    return jsonify(get_metro_tram_lines())


# --- API: Reimbursements ---

@app.route("/api/reimbursements")
def api_reimbursements_list():
    conn = get_db()
    rows = conn.execute("SELECT * FROM reimbursements ORDER BY end_date DESC, created_at DESC").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/reimbursements", methods=["POST"])
def api_reimbursements_add():
    data = request.json
    conn = get_db()
    conn.execute(
        "INSERT INTO reimbursements (title, description, lines, start_date, end_date, url) VALUES (?, ?, ?, ?, ?, ?)",
        (data.get("title"), data.get("description"), data.get("lines"), data.get("start_date"), data.get("end_date"), data.get("url"))
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route("/api/reimbursements/<int:rid>", methods=["DELETE"])
def api_reimbursements_delete(rid):
    conn = get_db()
    conn.execute("DELETE FROM reimbursements WHERE id = ?", (rid,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# --- Helpers ---

def _get_dates():
    return request.args.get("start"), request.args.get("end")

def _restart_scheduler():
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
    config = load_config()
    interval = config.get("scrape_interval_minutes", 5)
    scheduler = BackgroundScheduler()
    scheduler.add_job(scrape_and_store, "interval", minutes=interval)
    scheduler.start()
    print(f"Scheduler started (every {interval} min)")


# --- Startup ---

if __name__ == "__main__":
    init_db()
    init_sncf_tables()

    # Sync SNCF data on first run
    try:
        from sncf_data import get_sncf_lines
        if not get_sncf_lines():
            print("Syncing SNCF punctuality data...")
            sync_sncf_data()
    except Exception as e:
        print(f"SNCF sync error: {e}")

    if get_api_key():
        print("API key found. Starting scraper...")
        scrape_and_store()
        _restart_scheduler()
    else:
        print("No API key configured. Go to http://localhost:5555/settings to set up.")

    app.run(debug=True, host="0.0.0.0", port=5555, use_reloader=False)
