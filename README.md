# Smart Attendance Recorder using QR Code

Attendance system with **dynamic, time-based QR codes** and anti-fraud measures.  
**Everything is in one folder** (Python backend + HTML/CSS frontend).

## Tech stack

- **Backend:** Python (Flask)
- **Frontend:** HTML, CSS, minimal JavaScript
- **Database:** SQLite (file `attendance.db` created in the same folder)

## Security features

| Problem | Solution |
|--------|----------|
| QR code sharing (WhatsApp, etc.) | **Dynamic + time-based QR** – changes every ~45s, expires; old/screenshots useless |
| One student scans for many | **Login + one per session** – roll/email login; one attendance per student per session |
| Scanning from outside classroom | **Location check** – GPS within ~30m of classroom (optional lat/lng) |
| Screenshot scanned later | **Live QR + server check** – token and time window validated; expired = rejected |
| Multiple phones (proxy) | **Device fingerprint** – one device per student per session |
| Late entry | **Attendance window** – first 10 min = present, after = marked **Late** |
| Teacher misuse (QR after class) | **Session lock** – QR only during scheduled time; admin logs |

## How to run (single command)

From the **PBL** folder, run **one** of these:

- **Terminal:** `python run.py`
- **Windows (double‑click):** `run.bat`

That installs dependencies (if needed) and starts the server. Then open **http://127.0.0.1:5000** in your browser.
   - Home: http://127.0.0.1:5000/
   - Teacher: http://127.0.0.1:5000/teacher.html
   - Student: http://127.0.0.1:5000/student.html
   - Admin: http://127.0.0.1:5000/admin.html

## Project structure (one folder)

```
PBL/
├── app.py           # Flask backend + serves HTML/CSS
├── requirements.txt # Python dependencies (Flask, qrcode, Pillow, etc.)
├── index.html       # Landing
├── teacher.html     # Teacher dashboard (create session, live QR)
├── student.html     # Student login + scan/paste QR
├── admin.html       # Admin logs
├── styles.css       # Shared CSS
├── run.py           # Single command: python run.py (installs deps + starts server)
├── run.bat          # Windows: double‑click to run
├── attendance.db    # Created on first run
└── README.md
```

## Quick test flow

1. **Teacher:** Teacher → Create session (use current Unix time for start, start+3600 for end) → QR appears and refreshes every 45s.
2. **Student:** Student → Login with `R001` or `student1@college.edu` → Scan teacher’s QR with any QR app, paste the JSON into the box → Mark attendance (allow location if prompted).
3. **Admin:** Admin → View session and QR generation logs.

## Config (in `app.py`)

- `QR_VALID_SECONDS = 45` – QR lifetime
- `ATTENDANCE_WINDOW_MINUTES = 10` – After this, marked as **Late**
- `LOCATION_RADIUS_METERS = 30` – Max distance from classroom
- `CLASSROOM_DEFAULT` – Default lat/lng if session has no classroom set

## Demo data (seeded on first run)

- **Teacher:** `t1` / teacher@college.edu  
- **Subject:** CS101 – Data Structures  
- **Students:** R001 / student1@college.edu (Alice), R002 / student2@college.edu (Bob)
