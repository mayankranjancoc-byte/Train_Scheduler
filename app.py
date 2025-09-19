import os
import sqlite3
import json
import datetime 
import math

from flask import Flask, jsonify, request, Response, render_template, redirect, url_for,flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.routing import BaseConverter



# app = Flask(__name__)
# app.secret_key = os.urandom(24)
# Custom converter for to_int
class IntConverter(BaseConverter):
    def to_python(self, value):
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_FILENAME = 'train_scheduler.db'
DB_PATH = os.path.join(BASE_DIR, DB_FILENAME)
DB_URI = f"sqlite:///{DB_PATH}"

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY", os.urandom(24))
app.config['SQLALCHEMY_DATABASE_URI'] = DB_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Register custom converter
app.url_map.converters['to_int'] = IntConverter

db = SQLAlchemy(app)

# Helper function for converting to int with default
def to_int(value, default=None):
    try:
        return int(value) if value is not None and value != '' else default
    except (ValueError, TypeError):
        return default

# --- Models (explicit tablenames to avoid ambiguity) ---
class Train(db.Model):
    __tablename__ = 'train'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50))        # express/local/freight
    priority = db.Column(db.Integer, default=1)
    route = db.Column(db.String(100))
    status = db.Column(db.String(30), default='on time')
    arrival_time = db.Column(db.String(20), nullable=True)
    departure_time = db.Column(db.String(20), nullable=True)
    platform = db.Column(db.Integer, nullable=True)


class Station(db.Model):
    __tablename__ = 'station'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    code = db.Column(db.String(10), unique=True, nullable=False)
    city = db.Column(db.String(100))


# --- Helper: ensure DB file & schema is up-to-date ---
def ensure_db_and_columns():
    """
    Create DB/tables if missing, and add missing columns to train table if needed.
    """
    db.create_all()

    # If DB file exists, inspect and add missing columns
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='train'")
        if cur.fetchone():
            cur.execute("PRAGMA table_info('train')")
            cols = [r[1] for r in cur.fetchall()]  # r[1] is column name

            expected = {
                'status': "TEXT DEFAULT 'on time'",
                'arrival_time': "TEXT",
                'departure_time': "TEXT",
                'platform': "INTEGER"
            }

            for col, col_def in expected.items():
                if col not in cols:
                    try:
                        cur.execute(f"ALTER TABLE train ADD COLUMN {col} {col_def}")
                        conn.commit()
                        print(f"[app.py] Added missing column '{col}' to table 'train'")
                    except sqlite3.OperationalError as e:
                        print(f"[app.py] Warning: could not add column {col}: {e}")
        conn.close()


def seed_if_empty():
    """Insert a few sample trains if the train table is empty."""
    if Train.query.count() == 0:
        print("[app.py] Seeding sample trains ...")
        t1 = Train(name="Express 201", type="express", priority=3, route="A-B", status="on time")
        t2 = Train(name="Freight 301", type="freight", priority=2, route="A-C", status="on time")
        t3 = Train(name="Local 101", type="local", priority=1, route="A-B", status="on time")
        db.session.add_all([t1, t2, t3])
        db.session.commit()
        print("[app.py] Seed complete.")


# ----------------------------
# Conflict Detection Function
# ----------------------------
def _parse_time_to_minutes(time_str):
    """Return minutes since midnight or None if not parseable."""
    if not time_str:
        return None
    
    # Handle various time formats
    try:
        # Handle HH:MM format
        if ':' in time_str:
            parts = time_str.split(':')
            if len(parts) >= 2:
                h = int(parts[0])
                m = int(parts[1])
                if 0 <= h <= 23 and 0 <= m <= 59:
                    return h * 60 + m
        
        # Handle HHMM format (without colon)
        elif len(time_str) == 4 and time_str.isdigit():
            h = int(time_str[:2])
            m = int(time_str[2:])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h * 60 + m
                
        # Handle H:MM format
        elif len(time_str) == 4 and time_str[1] == ':':
            h = int(time_str[0])
            m = int(time_str[2:])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h * 60 + m
                
    except (ValueError, TypeError):
        pass
        
    return None

"""def resolve_conflict(train):
    """"""
    Simple conflict resolution:
    If a platform is occupied, try to move the train to the next available platform.
    """"""
    for new_platform in range(1, 2):
        conflict = Train.query.filter(
            Train.platform == new_platform,
            Train.id != train.id,
            Train.arrival_time < train.departure_time,
            Train.departure_time > train.arrival_time
        ).first()

        if not conflict:
            train.platform = new_platform
            db.session.commit()
            print(f"✅ Resolved: Train {train.name} moved to Platform {new_platform}")
            return True

    print(f"⚠️ Could not resolve conflict for Train {train.name} — all platforms full")
    return False"""


def check_conflicts(train):
    """
    Return list of Train objects that conflict with `train` on the same platform
    based on overlapping arrival/departure times (simple HH:MM logic).
    """
    if train.platform is None:
        return []

    others = Train.query.filter(Train.platform == train.platform, Train.id != train.id).all()
    a1 = _parse_time_to_minutes(train.arrival_time)
    d1 = _parse_time_to_minutes(train.departure_time)
    if a1 is None or d1 is None:
        return []  # can't check reliably without times

    overlaps = []
    for o in others:
        a2 = _parse_time_to_minutes(o.arrival_time)
        d2 = _parse_time_to_minutes(o.departure_time)
        if a2 is None or d2 is None:
            continue
        # intervals overlap if not (d1 <= a2 or a1 >= d2)
        if not (d1 <= a2 or a1 >= d2):
            overlaps.append(o)
    return overlaps


# --- Routes ---
@app.route('/')
def home():
    return "🚆 Train Scheduler Prototype Running ✅"


@app.route('/schedule')
def schedule():
    trains = Train.query.order_by(Train.id).all()
    result = [{
        "id": t.id,
        "name": t.name,
        "type": t.type,
        "priority": t.priority,
        "route": t.route,
        "status": t.status,
        "arrival_time": t.arrival_time,
        "departure_time": t.departure_time,
        "platform": t.platform
    } for t in trains]
    return jsonify(result)


@app.route('/precedence')
def precedence():
    trains = Train.query.all()
    sorted_trains = sorted(trains, key=lambda t: (-(t.priority or 0), t.id or 0))
    result = [{
        "id": t.id,
        "name": t.name,
        "type": t.type,
        "priority": t.priority,
        "route": t.route,
        "status": t.status,
        "arrival_time": t.arrival_time,
        "departure_time": t.departure_time,
        "platform": t.platform
    } for t in sorted_trains]
    return jsonify(result)


@app.route('/update_status/<to_int:train_id>/<string:new_status>', methods=['GET', 'POST'])
def update_status(train_id, new_status):
    try:
        t = Train.query.get(train_id)
        if not t:
            return jsonify({"error": "Train not found"}), 404
        t.status = new_status
        db.session.commit()
        return jsonify({"message": f"Train {t.name} status updated to {new_status}"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/download_schedule')
def download_schedule():
    trains = Train.query.order_by(Train.id).all()
    data = [{
        "id": t.id,
        "name": t.name,
        "type": t.type,
        "priority": t.priority,
        "route": t.route,
        "status": t.status,
        "arrival_time": t.arrival_time,
        "departure_time": t.departure_time,
        "platform": t.platform
    } for t in trains]
    payload = json.dumps(data, indent=2)
    resp = Response(payload, mimetype='application/json')
    resp.headers['Content-Disposition'] = 'attachment; filename=schedule.json'
    return resp


@app.route('/debug/db_info')
def debug_db_info():
    files = [f for f in os.listdir(BASE_DIR) if f.endswith('.db')]
    return jsonify({"DB_PATH": DB_PATH, "db_files_in_project": files})


# Dashboard + CRUD
@app.route('/dashboard')
def dashboard():
    trains = Train.query.order_by(Train.id).all()

    # For the template, build a dict mapping train.id -> list of conflict names (strings)
    conflicts_by_train = {}
    for train in trains:
        conflicts = check_conflicts(train)
        conflicts_by_train[train.id] = [c.name for c in conflicts]

    return render_template('dashboard.html', trains=trains, conflicts_by_train=conflicts_by_train)


@app.route('/update_status_ui/<to_int:train_id>/<string:new_status>')
def update_status_ui(train_id, new_status):
    try:
        train = Train.query.get(train_id)
        if train:
            train.status = new_status
            db.session.commit()
        return redirect(url_for('dashboard'))
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/add_train', methods=['POST'])
def add_train():
    try:
        # Basic validation
        required_fields = ['name', 'type', 'route']
        for field in required_fields:
            if not request.form.get(field):
                flash(f"Missing required field: {field}", "danger")
                return redirect(url_for('dashboard'))
        
        # Get form data
        name = request.form.get('name').strip()
        type_ = request.form.get('type').strip()
        priority = to_int(request.form.get('priority'), 1)
        route = request.form.get('route').strip()
        arrival_time = request.form.get('arrival_time', '').strip() or None
        departure_time = request.form.get('departure_time', '').strip() or None
        platform = to_int(request.form.get('platform'), None)
        
        # Validate time formats
        if arrival_time and not _parse_time_to_minutes(arrival_time):
            flash("Invalid arrival time format. Please use HH:MM", "danger")
            return redirect(url_for('dashboard'))
            
        if departure_time and not _parse_time_to_minutes(departure_time):
            flash("Invalid departure time format. Please use HH:MM", "danger")
            return redirect(url_for('dashboard'))
        
        # Create new train
        new_train = Train(
            name=name, 
            type=type_, 
            priority=priority, 
            route=route,
            status="on time", 
            arrival_time=arrival_time, 
            departure_time=departure_time,
            platform=platform
        )
        
        db.session.add(new_train)
        db.session.commit()
        
        # Check for conflicts
        conflicts = check_conflicts(new_train)
        if conflicts:
            conflict_names = [t.name for t in conflicts]
            flash(f"Train added but conflicts detected with: {', '.join(conflict_names)}", "warning")
        else:
            flash("Train added successfully!", "success")
        
        return redirect(url_for('dashboard'))
        
    except Exception as e:
        db.session.rollback()
        flash(f"Error adding train: {str(e)}", "danger")
        return redirect(url_for('dashboard'))
   


@app.route('/delete_train/<to_int:train_id>')
def delete_train(train_id):
    try:
        train = Train.query.get(train_id)
        if train:
            db.session.delete(train)
            db.session.commit()
        return redirect(url_for('dashboard'))
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route('/edit_train/<to_int:train_id>', methods=['GET', 'POST'])
def edit_train(train_id):
    try:
        train = Train.query.get(train_id)
        if not train:
            flash("Train not found", "danger")
            return redirect(url_for('dashboard'))
            
        if request.method == 'POST':
            # Basic validation
            required_fields = ['name', 'type', 'route']
            for field in required_fields:
                if not request.form.get(field):
                    flash(f"Missing required field: {field}", "danger")
                    return render_template('edit_train.html', train=train)
            
            # Validate time formats
            arrival_time = request.form.get('arrival_time', '').strip() or None
            departure_time = request.form.get('departure_time', '').strip() or None
            
            if arrival_time and not _parse_time_to_minutes(arrival_time):
                flash("Invalid arrival time format. Please use HH:MM", "danger")
                return render_template('edit_train.html', train=train)
                
            if departure_time and not _parse_time_to_minutes(departure_time):
                flash("Invalid departure time format. Please use HH:MM", "danger")
                return render_template('edit_train.html', train=train)
            
            # Update train details
            train.name = request.form.get('name').strip()
            train.type = request.form.get('type').strip()
            train.priority = to_int(request.form.get('priority'), train.priority)
            train.route = request.form.get('route').strip()
            train.arrival_time = arrival_time
            train.departure_time = departure_time
            train.platform = to_int(request.form.get('platform'), None)
            
            db.session.commit()
            
            # Check for conflicts after update
            conflicts = check_conflicts(train)
            if conflicts:
                conflict_names = [t.name for t in conflicts]
                flash(f"⚠️ Conflict detected with: {', '.join(conflict_names)}", "warning")
            else:
                flash("Train updated successfully!", "success")
                
            return redirect(url_for('dashboard'))

        return render_template('edit_train.html', train=train)
    except Exception as e:
        db.session.rollback()
        flash(f"Error updating train: {str(e)}", "danger")
        return redirect(url_for('dashboard'))
    

# ===========================
# STATION MANAGEMENT
# ===========================
@app.route("/stations")
def stations():
    all_stations = Station.query.all()
    return render_template("stations.html", stations=all_stations)

@app.route("/add_station", methods=["POST"])
def add_station():
    name = request.form.get("name")
    code = request.form.get("code").upper()
    city = request.form.get("city")

    if not name or not code or not city:
        flash("All fields are required!", "danger")
        return redirect(url_for("stations"))

    if Station.query.filter_by(code=code).first():
        flash("Station code already exists!", "warning")
        return redirect(url_for("stations"))

    new_station = Station(name=name, code=code, city=city)
    db.session.add(new_station)
    db.session.commit()
    flash("Station added successfully!", "success")
    return redirect(url_for("stations"))

@app.route("/edit_station/<int:station_id>", methods=["GET", "POST"])
def edit_station(station_id):
    station = Station.query.get_or_404(station_id)

    if request.method == "POST":
        station.name = request.form.get("name")
        station.code = request.form.get("code").upper()
        station.city = request.form.get("city")
        db.session.commit()
        flash("Station updated successfully!", "success")
        return redirect(url_for("stations"))

    return render_template("edit_station.html", station=station)

@app.route("/delete_station/<int:station_id>")
def delete_station(station_id):
    station = Station.query.get_or_404(station_id)
    db.session.delete(station)
    db.session.commit()
    flash("Station deleted successfully!", "success")
    return redirect(url_for("stations"))


#  Precedence UI (visual) ---
@app.route('/precedence_ui')
def precedence_ui():
    trains = Train.query.order_by(Train.id).all()
    sorted_trains = sorted(trains, key=lambda t: (-(t.priority or 0), t.id or 0))
    return render_template('precedence.html', trains=sorted_trains)

@app.route("/resolve_conflict/<int:train_id>", methods=["POST"])
def resolve_conflict(train_id):
    train = Train.query.get_or_404(train_id)

    # Update with new values
    train.platform = to_int(request.form.get("platform"), train.platform)
    train.arrival_time = request.form.get("arrival_time") or train.arrival_time
    train.departure_time = request.form.get("departure_time") or train.departure_time
    train.priority = to_int(request.form.get("priority"), train.priority)

    db.session.commit()

    # Re-check for conflicts
    conflicts = check_conflicts(train)
    if conflicts:
        print(f"⚠️ Still conflicts with {[c.name for c in conflicts]}")
    else:
        print(f"✅ Conflict resolved for {train.name}")

    return redirect(url_for("dashboard"))


@app.route("/auto_resolve", methods=["POST"])
def auto_resolve():
    """Improved auto-resolve function with better error handling"""
    try:
        trains = Train.query.order_by(Train.priority.desc()).all()
        platform_availability = {}  # Track platform usage
        
        for train in trains:
            # Skip trains without valid time information
            if not train.arrival_time or not train.departure_time:
                continue
                
            arr_min = _parse_time_to_minutes(train.arrival_time)
            dep_min = _parse_time_to_minutes(train.departure_time)
            
            # Skip if times are invalid
            if arr_min is None or dep_min is None:
                continue
                
            # Initialize platform tracking if needed
            if train.platform not in platform_availability:
                platform_availability[train.platform] = []
                
            # Check for conflicts on current platform
            conflicts = check_conflicts(train)
            
            if conflicts:
                print(f"⚠️ Conflict detected for {train.name}")
                
                # Try to find an available platform
                conflict_resolved = False
                for platform in range(1, 6):  # Check platforms 1-5
                    if platform not in platform_availability:
                        platform_availability[platform] = []
                        
                    # Check if platform is available for the train's time slot
                    platform_available = True
                    for other_arr, other_dep in platform_availability[platform]:
                        if not (dep_min <= other_arr or arr_min >= other_dep):
                            platform_available = False
                            break
                            
                    if platform_available:
                        # Move train to this platform
                        train.platform = platform
                        platform_availability[platform].append((arr_min, dep_min))
                        conflict_resolved = True
                        print(f"✅ Moved {train.name} to platform {platform}")
                        break
                        
                if not conflict_resolved:
                    # Delay the train by 15 minutes
                    new_arr_min = arr_min + 15
                    new_dep_min = dep_min + 15
                    train.arrival_time = f"{new_arr_min//60:02d}:{new_arr_min%60:02d}"
                    train.departure_time = f"{new_dep_min//60:02d}:{new_dep_min%60:02d}"
                    train.status = "delayed"
                    platform_availability[train.platform].append((new_arr_min, new_dep_min))
                    print(f"⏰ Delayed {train.name} by 15 minutes")
            else:
                # No conflict, just track the platform usage
                platform_availability[train.platform].append((arr_min, dep_min))
        
        db.session.commit()
        flash("Conflicts resolved successfully!", "success")
        
    except Exception as e:
        db.session.rollback()
        print(f"Error in auto_resolve: {e}")
        flash(f"Error resolving conflicts: {str(e)}", "danger")
    
    return redirect(url_for("dashboard"))
def optimize_schedule():
    """
    Advanced scheduling algorithm using constraint satisfaction
    to resolve conflicts and optimize platform allocation
    """
    # Get all trains sorted by priority (descending) and scheduled time
    trains = Train.query.order_by(Train.priority.desc(), Train.arrival_time).all()
    
    # Initialize platform availability tracker (assuming 5 platforms)
    platforms = {i: [] for i in range(1, 6)}
    
    for train in trains:
        # Skip trains without time information
        if not train.arrival_time or not train.departure_time:
            continue
            
        # Convert times to minutes for comparison
        arr_min = _parse_time_to_minutes(train.arrival_time)
        dep_min = _parse_time_to_minutes(train.departure_time)
        
        # Find available platform with minimal disruption
        best_platform = None
        best_delay = float('inf')  # Start with a very large number
        
        for platform_id, schedules in platforms.items():
            # Check if platform is available for the requested time
            conflict = False
            for scheduled_arr, scheduled_dep in schedules:
                if not (dep_min <= scheduled_arr or arr_min >= scheduled_dep):
                    conflict = True
                    break
                    
            if not conflict:
                # Platform available without changes
                best_platform = platform_id
                break
            else:
                # Calculate minimum delay needed (5 minutes buffer between trains)
                last_departure = max([dep for arr, dep in schedules]) if schedules else 0
                delay_needed = max(0, last_departure + 5 - arr_min)
                
                if delay_needed < best_delay:
                    best_delay = delay_needed
                    best_platform = platform_id
        
        # Assign platform and adjust time if needed
        if best_platform is not None:
            train.platform = best_platform
            if best_delay > 0:
                # Adjust arrival and departure times
                new_arr = arr_min + best_delay
                new_dep = dep_min + best_delay
                train.arrival_time = f"{new_arr//60:02d}:{new_arr%60:02d}"
                train.departure_time = f"{new_dep//60:02d}:{new_dep%60:02d}"
                train.status = "delayed"
            
            # Update platform schedule
            platforms[best_platform].append((arr_min + best_delay, dep_min + best_delay))
        else:
            # No platform available, cancel train
            train.status = "cancelled"
    
    db.session.commit()




@app.route('/timeline')
def timeline():
    """
    Prepares train data (start/end in ms since epoch) for Plotly timeline visualization.
    Skips trains with missing times. Converts HH:MM to today's datetime; if departure <= arrival,
    assumes departure is next day (handles overnight).
    """
    trains = Train.query.order_by(Train.platform, Train.id).all()
    items = []
    today = datetime.date.today()

    for t in trains:
        # need both arrival and departure to draw bar
        if not t.arrival_time or not t.departure_time:
            continue

        a_minutes = _parse_time_to_minutes(t.arrival_time)
        d_minutes = _parse_time_to_minutes(t.departure_time)
        if a_minutes is None or d_minutes is None:
            continue

        start_dt = datetime.datetime.combine(today, datetime.time(hour=a_minutes // 60, minute=a_minutes % 60))
        end_dt = datetime.datetime.combine(today, datetime.time(hour=d_minutes // 60, minute=d_minutes % 60))

        # if departure is earlier or equal than arrival, assume next day (overnight)
        if end_dt <= start_dt:
            end_dt = end_dt + datetime.timedelta(days=1)

        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        duration_ms = end_ms - start_ms

        platform_label = f"Platform {t.platform}" if t.platform is not None else "Unassigned"

        # mark color red if this train has conflicts
        conflicts = check_conflicts(t)
        color = "#e74c3c" if conflicts else "#4a90e2"  # red for conflict, blue otherwise

        items.append({
            "id": t.id,
            "name": t.name,
            "platform_label": platform_label,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": duration_ms,
            "priority": t.priority,
            "status": t.status,
            "color": color,
            "arrival_time": t.arrival_time,
            "departure_time": t.departure_time
        })

    # Also gather the distinct platform labels in wanted order (so y-axis is stable)
    platform_order = []
    for it in items:
        if it["platform_label"] not in platform_order:
            platform_order.append(it["platform_label"])

    return render_template('timeline.html', items=items, platform_order=platform_order)




@app.route("/timeline")
def timeline_page():
    """Serves the HTML template with empty shell; data loaded via JS fetch"""
    return render_template("timeline.html")

@app.route("/timeline_data")
def timeline_data():
    trains = Train.query.order_by(Train.platform, Train.id).all()
    items = []

    today = datetime.date.today()

    for t in trains:
        if not t.arrival_time or not t.departure_time:
            continue

        try:
            # Parse arrival
            arr_parts = t.arrival_time.split(':')
            arr_hour = int(arr_parts[0])
            arr_min = int(arr_parts[1]) if len(arr_parts) > 1 else 0
            arr_time_obj = datetime.time(arr_hour, arr_min)

            # Parse departure
            dep_parts = t.departure_time.split(':')
            dep_hour = int(dep_parts[0])
            dep_min = int(dep_parts[1]) if len(dep_parts) > 1 else 0
            dep_time_obj = datetime.time(dep_hour, dep_min)

            arr_dt = datetime.datetime.combine(today, arr_time_obj)
            dep_dt = datetime.datetime.combine(today, dep_time_obj)

            if dep_dt <= arr_dt:
                dep_dt += datetime.timedelta(days=1)

            items.append({
                "id": t.id,
                "name": getattr(t, "name", f"Train {t.id}"),
                "platform": t.platform,
                "platform_label": f"Platform {t.platform}",
                "start_ms": int(arr_dt.timestamp() * 1000),
                "end_ms": int(dep_dt.timestamp() * 1000),
                "duration_ms": int((dep_dt - arr_dt).total_seconds() * 1000),
                "status": getattr(t, "status", "Scheduled"),
                "priority": getattr(t, "priority", "Medium"),
                "color": "#4a90e2"
            })

        except (ValueError, IndexError) as e:
            print(f"Skipping train {t.id} due to invalid time format: {e}")
            continue

    # ✅ Conflict detection & temporary resolution
    items.sort(key=lambda x: (x["platform"], x["start_ms"]))
    resolved = []
    priority_order = {"High": 3, "Medium": 2, "Low": 1}

    for train in items:
        conflict_found = False
        for other in resolved:
            if train["platform"] == other["platform"]:
                # Overlap check (back-to-back allowed)
                if not (train["end_ms"] <= other["start_ms"] or train["start_ms"] >= other["end_ms"]):
                    conflict_found = True
                    print(f"⚠️ Conflict detected between {train['name']} and {other['name']}")

                    p1 = priority_order.get(train["priority"], 2)
                    p2 = priority_order.get(other["priority"], 2)

                    if p1 < p2 or (p1 == p2 and train["start_ms"] > other["start_ms"]):
                        # This train loses → reroute or delay
                        all_platforms = sorted({tr.platform for tr in trains if tr.platform})
                        for new_p in all_platforms:
                            if all(
                                new_p != r["platform"] or
                                (train["end_ms"] <= r["start_ms"] or train["start_ms"] >= r["end_ms"])
                                for r in resolved
                            ):
                                train["platform"] = new_p
                                train["platform_label"] = f"Platform {new_p}"
                                train["status"] = "Rerouted"
                                train["color"] = "#2ecc71"
                                break
                        else:
                            # No free platform → delay by 15 min
                            delay_ms = 15 * 60 * 1000
                            train["start_ms"] = other["end_ms"] + 1
                            train["end_ms"] = train["start_ms"] + train["duration_ms"]
                            train["status"] = "Delayed"
                            train["color"] = "#f39c12"

        if not conflict_found:
            train["status"] = train.get("status", "Scheduled")

        resolved.append(train)

    platforms = sorted({f"Platform {t.platform}" for t in trains if t.platform})
    return jsonify({"items": resolved, "platform_order": platforms})

@app.route("/debug_auto_resolve")
def debug_auto_resolve():
    """Debug version to see what's causing the error"""
    try:
        trains = Train.query.order_by(Train.priority.desc()).all()
        
        for train in trains:
            print(f"Processing train: {train.name}")
            print(f"  Arrival: {train.arrival_time}, Departure: {train.departure_time}")
            
            # Check if times are valid
            if train.arrival_time and train.departure_time:
                arr_min = _parse_time_to_minutes(train.arrival_time)
                dep_min = _parse_time_to_minutes(train.departure_time)
                print(f"  Parsed times: {arr_min} min, {dep_min} min")
            
            conflicts = check_conflicts(train)
            print(f"  Conflicts: {[c.name for c in conflicts]}")
            
        return "Debug completed - check console for output"
    except Exception as e:
        print(f"Error in debug_auto_resolve: {e}")
        return f"Error: {str(e)}"

    



# --- Start-up: ensure DB + columns + seed ---
if __name__ == '__main__':
    with app.app_context():
        ensure_db_and_columns()
        seed_if_empty()
    print(f"[app.py] Using DB at: {DB_PATH}")
    app.run(debug=True, port=5000)





