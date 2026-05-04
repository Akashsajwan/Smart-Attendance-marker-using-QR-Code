"""Attendance portal backend. Serves API and static HTML from this folder."""
import os
import random
import sqlite3
import uuid
import time
import json
import qrcode
import io
import base64
from contextlib import contextmanager
from math import radians, sin, cos, sqrt, atan2
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")
CORS(app)

QR_VALID_SECONDS = 45
ATTENDANCE_WINDOW_MINUTES = 10
LOCATION_RADIUS_METERS = 100
CLASSROOM_DEFAULT = {"lat": 28.6139, "lng": 77.2090}

DB_PATH = os.path.join(BASE_DIR, "attendance.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def api_err(msg, status=400):
    return jsonify({"error": msg}), status


def now():
    return int(time.time())


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS teachers (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS students (
            id TEXT PRIMARY KEY,
            roll_no TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS subjects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            teacher_id TEXT NOT NULL,
            scheduled_start INTEGER NOT NULL,
            scheduled_end INTEGER NOT NULL,
            classroom_lat REAL,
            classroom_lng REAL,
            qr_generated_at INTEGER,
            qr_generation_count INTEGER DEFAULT 0,
            locked INTEGER DEFAULT 0,
            FOREIGN KEY (subject_id) REFERENCES subjects(id),
            FOREIGN KEY (teacher_id) REFERENCES teachers(id)
        );
        CREATE TABLE IF NOT EXISTS qr_tokens (
            token TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            marked_at INTEGER NOT NULL,
            status TEXT DEFAULT 'present',
            device_fingerprint TEXT,
            ip_address TEXT,
            lat REAL,
            lng REAL,
            UNIQUE(session_id, student_id),
            FOREIGN KEY (session_id) REFERENCES sessions(id),
            FOREIGN KEY (student_id) REFERENCES students(id)
        );
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            actor_id TEXT,
            actor_type TEXT,
            details TEXT,
            created_at INTEGER NOT NULL
        );
    """)
    conn.commit()
    # Seed only subjects (so teachers can create sessions); no demo teachers/students
    cur = conn.execute("SELECT 1 FROM subjects LIMIT 1")
    if cur.fetchone() is None:
        conn.execute(
            "INSERT INTO subjects (id, name, code) VALUES (?, ?, ?)",
            ("sub1", "Data Structures", "CS101"),
        )
        conn.execute(
            "INSERT INTO subjects (id, name, code) VALUES (?, ?, ?)",
            ("sub2", "Algorithms", "CS102"),
        )
        conn.commit()
    conn.close()


def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlam = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlam / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def log_admin(action, actor_id, actor_type, details):
    det = json.dumps(details) if isinstance(details, dict) else str(details)
    with db() as c:
        c.execute(
            "INSERT INTO admin_logs (action, actor_id, actor_type, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (action, actor_id, actor_type, det, now()),
        )
        c.commit()


@app.route("/api/sessions", methods=["POST"])
def create_session():
    data = request.get_json() or {}
    subject_id = data.get("subject_id")
    teacher_id = data.get("teacher_id")
    start_ts = data.get("scheduled_start")
    end_ts = data.get("scheduled_end")
    if not all([subject_id, teacher_id, start_ts, end_ts]):
        return api_err("Missing subject_id, teacher_id, scheduled_start, scheduled_end")
    t = now()
    start_ts = int(start_ts)
    end_ts = int(end_ts)
    if t < start_ts - 300:
        return api_err("Too early. Create within 5 min of class start.")
    if t > end_ts:
        return api_err("Class time is over. Cannot create session.")
    session_id = str(uuid.uuid4())
    lat = data.get("classroom_lat")
    lng = data.get("classroom_lng")
    with db() as c:
        c.execute(
            """INSERT INTO sessions (id, subject_id, teacher_id, scheduled_start, scheduled_end, classroom_lat, classroom_lng, qr_generation_count, locked)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)""",
            (session_id, subject_id, teacher_id, start_ts, end_ts, lat, lng),
        )
        c.commit()
    log_admin("SESSION_CREATED", teacher_id, "teacher", {"session_id": session_id, "subject_id": subject_id})
    return jsonify({"session_id": session_id, "message": "Session created. Generate QR from dashboard."})


@app.route("/api/sessions/<session_id>/qr", methods=["GET"])
def get_qr(session_id):
    t = now()
    with db() as c:
        row = c.execute("SELECT * FROM sessions WHERE id = ? AND locked = 0", (session_id,)).fetchone()
        if not row:
            return api_err("Session not found or locked.", 404)
        sess = dict(row)
        if t < sess["scheduled_start"] - 60:
            return api_err("Class not started yet.")
        if t > sess["scheduled_end"]:
            c.execute("UPDATE sessions SET locked = 1 WHERE id = ?", (session_id,))
            c.commit()
            return api_err("Class ended. Session locked.")
        for _ in range(20):
            token = str(random.randint(10000, 99999))
            if not c.execute("SELECT 1 FROM qr_tokens WHERE token = ? AND expires_at > ?", (token, t)).fetchone():
                break
        else:
            token = str(random.randint(10000, 99999))
        expires_at = t + QR_VALID_SECONDS
        c.execute("DELETE FROM qr_tokens WHERE session_id = ?", (session_id,))
        c.execute("INSERT INTO qr_tokens (token, session_id, created_at, expires_at) VALUES (?, ?, ?, ?)", (token, session_id, t, expires_at))
        c.execute("UPDATE sessions SET qr_generated_at = ?, qr_generation_count = qr_generation_count + 1 WHERE id = ?", (t, session_id))
        c.commit()
    payload = json.dumps({"token": token, "session_id": session_id, "ts": t, "exp": expires_at})
    img = qrcode.make(payload, error_correction=qrcode.constants.ERROR_CORRECT_M)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return jsonify({"qr_data_url": data_url, "code": token, "expires_at": expires_at, "valid_seconds": QR_VALID_SECONDS})


@app.route("/api/attendance", methods=["POST"])
def submit_attendance():
    data = request.get_json() or {}
    token = (data.get("token") or data.get("code") or "").strip()
    session_id = data.get("session_id")
    student_id = data.get("student_id")
    fp = data.get("device_fingerprint")
    lat, lng = data.get("lat"), data.get("lng")
    accuracy = data.get("accuracy")
    if not token or not student_id:
        return api_err("Missing code and/or student_id.")
    raw_ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "")
    ip = raw_ip.split(",")[0].strip() if raw_ip else ""
    t = now()

    with db() as c:
        if not session_id:
            row = c.execute("SELECT session_id FROM qr_tokens WHERE token = ? AND expires_at > ?", (token, t)).fetchone()
            if not row:
                return api_err("Invalid or expired code. Enter the current 5-digit code.")
            session_id = row["session_id"]
        sess_row = c.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not sess_row:
            return api_err("Invalid session.", 404)
        sess = dict(sess_row)
        if sess["locked"]:
            return api_err("Session is closed.")
        tok = c.execute("SELECT * FROM qr_tokens WHERE token = ? AND session_id = ?", (token, session_id)).fetchone()
        if not tok or t > tok["expires_at"]:
            return api_err("Invalid or expired QR. Use the current code.")
        if c.execute("SELECT id FROM attendance WHERE session_id = ? AND student_id = ?", (session_id, student_id)).fetchone():
            return api_err("Attendance already marked for this session.")
        if ip and c.execute("SELECT 1 FROM attendance WHERE session_id = ? AND ip_address = ?", (session_id, ip)).fetchone():
            return api_err("Attendance from this device/network already recorded for this session.")
        if fp:
            other = c.execute("SELECT student_id FROM attendance WHERE session_id = ? AND device_fingerprint = ?", (session_id, fp)).fetchone()
            if other and other["student_id"] != student_id:
                return api_err("This device already marked for another student.")
        clat, clng = sess.get("classroom_lat"), sess.get("classroom_lng")
        require_loc = (clat is not None and clng is not None and not (clat == CLASSROOM_DEFAULT["lat"] and clng == CLASSROOM_DEFAULT["lng"]))
        if require_loc:
            if lat is None or lng is None:
                return api_err("Location required to mark attendance for this session.")
            try:
                latf = float(lat)
                lngf = float(lng)
                dist = haversine_meters(latf, lngf, float(clat), float(clng))
                extra = 0.0
                try:
                    if accuracy is not None:
                        extra = min(100.0, max(0.0, float(accuracy)))
                except (TypeError, ValueError):
                    extra = 0.0
                allowed = float(LOCATION_RADIUS_METERS) + extra
                if dist > allowed:
                    return api_err("Outside classroom range (distance %sm). Be within ~%sm." % (int(dist), int(allowed)))
            except (TypeError, ValueError):
                return api_err("Invalid location data.")
        window_end = sess["scheduled_start"] + ATTENDANCE_WINDOW_MINUTES * 60
        status = "present" if t <= window_end else "late"
        c.execute(
            "INSERT INTO attendance (session_id, student_id, marked_at, status, device_fingerprint, ip_address, lat, lng) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, student_id, t, status, fp or None, ip, lat, lng),
        )
        c.commit()
    return jsonify({"success": True, "status": status})


@app.route("/api/teachers/register", methods=["POST"])
def teacher_register():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()
    if not email or not name:
        return api_err("Email and name are required.")
    with db() as c:
        if c.execute("SELECT id FROM teachers WHERE email = ?", (email,)).fetchone():
            return api_err("This email is already registered.")
        teacher_id = str(uuid.uuid4())
        c.execute("INSERT INTO teachers (id, email, name, created_at) VALUES (?, ?, ?, ?)", (teacher_id, email, name, now()))
        c.commit()
    log_admin("TEACHER_REGISTERED", teacher_id, "teacher", {"email": email})
    return jsonify({"teacher_id": teacher_id, "name": name, "email": email})


@app.route("/api/teachers/login", methods=["POST"])
def teacher_login():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return api_err("Email is required.")
    with db() as c:
        row = c.execute("SELECT id, name FROM teachers WHERE email = ?", (email,)).fetchone()
    if not row:
        return api_err("No account with this email. Please register first.", 401)
    return jsonify({"teacher_id": row["id"], "name": row["name"], "email": email})


@app.route("/api/students/register", methods=["POST"])
def student_register():
    data = request.get_json() or {}
    roll_no = (data.get("roll_no") or "").strip().upper()
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()
    if not roll_no or not email or not name:
        return api_err("Roll number, email and name are required.")
    with db() as c:
        if c.execute("SELECT id FROM students WHERE roll_no = ?", (roll_no,)).fetchone():
            return api_err("This roll number is already registered.")
        if c.execute("SELECT id FROM students WHERE email = ?", (email,)).fetchone():
            return api_err("This email is already registered.")
        student_id = str(uuid.uuid4())
        c.execute("INSERT INTO students (id, roll_no, email, name, created_at) VALUES (?, ?, ?, ?, ?)", (student_id, roll_no, email, name, now()))
        c.commit()
    log_admin("STUDENT_REGISTERED", student_id, "student", {"roll_no": roll_no})
    return jsonify({"student_id": student_id, "name": name, "roll_no": roll_no, "email": email})


@app.route("/api/students/identify", methods=["POST"])
def student_identify():
    data = request.get_json() or {}
    roll_no = (data.get("roll_no") or "").strip().upper()
    name = (data.get("name") or "").strip()
    if not roll_no or not name:
        return api_err("Roll number and name are required.")
    with db() as c:
        row = c.execute("SELECT id, roll_no, name FROM students WHERE roll_no = ?", (roll_no,)).fetchone()
        if row:
            if row["name"].strip().lower() != name.lower():
                return api_err("Name does not match this roll number.")
            return jsonify({"student_id": row["id"], "name": row["name"], "roll_no": row["roll_no"]})
        student_id = str(uuid.uuid4())
        email = roll_no + "@student.local"
        c.execute("INSERT INTO students (id, roll_no, email, name, created_at) VALUES (?, ?, ?, ?, ?)", (student_id, roll_no, email, name, now()))
        c.commit()
    log_admin("STUDENT_IDENTIFIED", student_id, "student", {"roll_no": roll_no})
    return jsonify({"student_id": student_id, "name": name, "roll_no": roll_no})


@app.route("/api/sessions/<session_id>/attendance", methods=["GET"])
def list_attendance(session_id):
    with db() as c:
        rows = c.execute(
            "SELECT a.marked_at, a.status, a.device_fingerprint, s.roll_no, s.name FROM attendance a JOIN students s ON s.id = a.student_id WHERE a.session_id = ? ORDER BY a.marked_at",
            (session_id,),
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/sessions", methods=["GET"])
def list_sessions():
    teacher_id = request.args.get("teacher_id")
    sql = """SELECT s.id, s.subject_id, s.teacher_id, s.scheduled_start, s.scheduled_end, s.locked, s.qr_generation_count,
             (SELECT COUNT(*) FROM attendance a WHERE a.session_id = s.id) AS attendance_count
             FROM sessions s"""
    params = ()
    if teacher_id:
        sql += " WHERE s.teacher_id = ?"
        params = (teacher_id,)
    sql += " ORDER BY s.scheduled_start DESC LIMIT 200"
    with db() as c:
        rows = c.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/sessions/<session_id>", methods=["PATCH"])
def update_session(session_id):
    data = request.get_json() or {}
    teacher_id = data.get("teacher_id")
    if not teacher_id:
        return api_err("teacher_id required.")
    with db() as c:
        row = c.execute("SELECT teacher_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return api_err("Session not found.", 404)
        if row["teacher_id"] != teacher_id:
            return api_err("You can only edit your own sessions.", 403)
        parts, params = [], []
        if data.get("subject_id") is not None:
            parts.append("subject_id = ?")
            params.append(data["subject_id"])
        if data.get("scheduled_start") is not None:
            parts.append("scheduled_start = ?")
            params.append(int(data["scheduled_start"]))
        if data.get("scheduled_end") is not None:
            parts.append("scheduled_end = ?")
            params.append(int(data["scheduled_end"]))
        if not parts:
            return jsonify({"message": "Nothing to update."})
        params.append(session_id)
        c.execute("UPDATE sessions SET " + ", ".join(parts) + " WHERE id = ?", params)
        c.commit()
    log_admin("SESSION_UPDATED", teacher_id, "teacher", {"session_id": session_id})
    return jsonify({"message": "Session updated."})


@app.route("/api/sessions/<session_id>/stop", methods=["POST"])
def stop_session(session_id):
    teacher_id = (request.get_json() or {}).get("teacher_id")
    if not teacher_id:
        return api_err("teacher_id required.")
    with db() as c:
        row = c.execute("SELECT teacher_id, locked FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return api_err("Session not found.", 404)
        if row["teacher_id"] != teacher_id:
            return api_err("Only the session owner can stop attendance.", 403)
        if row["locked"]:
            return jsonify({"message": "Attendance already stopped."})
        c.execute("UPDATE sessions SET locked = 1 WHERE id = ?", (session_id,))
        c.commit()
    log_admin("ATTENDANCE_STOPPED", teacher_id, "teacher", {"session_id": session_id})
    return jsonify({"message": "Attendance stopped. No more students can mark for this session."})


@app.route("/api/subjects", methods=["GET"])
def list_subjects():
    with db() as c:
        rows = c.execute("SELECT id, name, code FROM subjects").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/subjects", methods=["POST"])
def create_subject():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip().upper()
    if not name or not code:
        return api_err("Name and code are required.")
    with db() as c:
        if c.execute("SELECT id FROM subjects WHERE code = ?", (code,)).fetchone():
            return api_err("Subject code already exists.")
        sid = str(uuid.uuid4())
        c.execute("INSERT INTO subjects (id, name, code) VALUES (?, ?, ?)", (sid, name, code))
        c.commit()
    return jsonify({"id": sid, "name": name, "code": code})


@app.route("/api/subjects/<subject_id>", methods=["PATCH"])
def update_subject(subject_id):
    data = request.get_json() or {}
    with db() as c:
        if not c.execute("SELECT id FROM subjects WHERE id = ?", (subject_id,)).fetchone():
            return api_err("Subject not found.", 404)
        if data.get("name") is not None:
            n = str(data.get("name")).strip()
            if n:
                c.execute("UPDATE subjects SET name = ? WHERE id = ?", (n, subject_id))
        if data.get("code") is not None:
            co = str(data.get("code")).strip().upper()
            if co:
                c.execute("UPDATE subjects SET code = ? WHERE id = ?", (co, subject_id))
        c.commit()
    return jsonify({"message": "Subject updated."})


@app.route("/api/admin/logs", methods=["GET"])
def admin_logs():
    with db() as c:
        rows = c.execute("SELECT * FROM admin_logs ORDER BY created_at DESC LIMIT 200").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/<path:path>")
def serve_static(path):
    return send_from_directory(BASE_DIR, path)


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
