from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from datetime import datetime, timedelta
import sqlite3, os, base64, math, secrets, string, hashlib, json, csv, io
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import qrcode
    HAS_QR = True
except ImportError:
    HAS_QR = False

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
DB = "instance/worksight.db"

# ── Helpers ───────────────────────────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def gen_code(length=8):
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(length))

def send_email(to_email, subject, html_body):
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    if not smtp_user or not smtp_pass:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = smtp_user
        msg["To"]      = to_email
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

def init_db():
    os.makedirs("instance", exist_ok=True)
    os.makedirs("static/selfies", exist_ok=True)
    os.makedirs("static/qrcodes", exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS companies (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            owner_name      TEXT NOT NULL,
            email           TEXT UNIQUE NOT NULL,
            password_hash   TEXT NOT NULL,
            building_lat    REAL,
            building_lng    REAL,
            building_name   TEXT,
            max_distance    INTEGER DEFAULT 300,
            registered_at   TEXT NOT NULL,
            work_start      TEXT DEFAULT '09:00',
            work_end        TEXT DEFAULT '17:00',
            notify_signin   INTEGER DEFAULT 0,
            notify_daily    INTEGER DEFAULT 1,
            plan            TEXT DEFAULT 'free'
        );
        CREATE TABLE IF NOT EXISTS staff_invitations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id      INTEGER NOT NULL,
            name            TEXT NOT NULL,
            email           TEXT NOT NULL,
            department      TEXT,
            staff_id_code   TEXT,
            token           TEXT UNIQUE NOT NULL,
            status          TEXT DEFAULT 'pending',
            created_at      TEXT NOT NULL,
            accepted_at     TEXT,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );
        CREATE TABLE IF NOT EXISTS staff (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id      INTEGER NOT NULL,
            name            TEXT NOT NULL,
            staff_id_code   TEXT,
            department      TEXT,
            email           TEXT,
            password_hash   TEXT,
            joined_at       TEXT NOT NULL,
            active          INTEGER DEFAULT 1,
            qr_code         TEXT,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );
        CREATE TABLE IF NOT EXISTS attendance (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id      INTEGER NOT NULL,
            staff_fk        INTEGER,
            name            TEXT NOT NULL,
            staff_code      TEXT,
            department      TEXT,
            purpose         TEXT,
            action          TEXT NOT NULL,
            timestamp       TEXT NOT NULL,
            latitude        REAL,
            longitude       REAL,
            gps_ok          INTEGER DEFAULT 0,
            distance_m      REAL,
            selfie_path     TEXT,
            is_late         INTEGER DEFAULT 0,
            is_overtime     INTEGER DEFAULT 0,
            flagged         INTEGER DEFAULT 0,
            flag_reason     TEXT,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );
        CREATE TABLE IF NOT EXISTS leave_requests (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id      INTEGER NOT NULL,
            staff_name      TEXT NOT NULL,
            staff_email     TEXT,
            leave_date      TEXT NOT NULL,
            reason          TEXT,
            status          TEXT DEFAULT 'pending',
            requested_at    TEXT NOT NULL,
            reviewed_at     TEXT,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id      INTEGER NOT NULL,
            type            TEXT NOT NULL,
            message         TEXT NOT NULL,
            staff_name      TEXT,
            created_at      TEXT NOT NULL,
            read            INTEGER DEFAULT 0,
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );
        """)

# ── Pages ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/staff")
def staff_portal():
    return render_template("staff.html")

@app.route("/admin")
def admin():
    if "company_id" not in session:
        return redirect(url_for("index"))
    return render_template("admin.html")

@app.route("/staff/history")
def staff_history():
    return render_template("history.html")

@app.route("/staff/accept-invite")
def accept_invite_page():
    token = request.args.get("token", "")
    if not token:
        return redirect(url_for("index"))
    return render_template("accept_invite.html", token=token)

# ── Company register/login ─────────────────────────────────────────────────────
@app.route("/api/company/register", methods=["POST"])
def company_register():
    d        = request.json
    name     = d.get("company_name","").strip()
    owner    = d.get("owner_name","").strip()
    email    = d.get("email","").strip().lower()
    password = d.get("password","")
    bname    = d.get("building_name","").strip()
    lat      = d.get("latitude")
    lng      = d.get("longitude")
    if not all([name, owner, email, password, lat, lng]):
        return jsonify({"error": "All fields and building location are required."}), 400
    try:
        with get_db() as conn:
            conn.execute("""INSERT INTO companies
                (name,owner_name,email,password_hash,building_lat,building_lng,building_name,registered_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (name, owner, email, hash_pw(password), lat, lng, bname,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        return jsonify({"success": True, "company": name})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Email already registered."}), 409

@app.route("/api/company/login", methods=["POST"])
def company_login():
    d        = request.json
    email    = d.get("email","").strip().lower()
    password = d.get("password","")
    with get_db() as conn:
        company = conn.execute(
            "SELECT * FROM companies WHERE email=? AND password_hash=?",
            (email, hash_pw(password))).fetchone()
    if not company:
        return jsonify({"error": "Invalid email or password."}), 401
    session["company_id"]   = company["id"]
    session["company_name"] = company["name"]
    session["owner_name"]   = company["owner_name"]
    return jsonify({"success": True, "company": company["name"]})

@app.route("/api/company/logout", methods=["POST"])
def company_logout():
    session.clear()
    return jsonify({"success": True})

# ── Staff login (email + password) ────────────────────────────────────────────
@app.route("/api/staff/login", methods=["POST"])
def staff_login():
    d        = request.json
    email    = d.get("email", "").strip().lower()
    password = d.get("password", "")
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400
    with get_db() as conn:
        staff = conn.execute(
            "SELECT s.*, c.building_lat, c.building_lng, c.max_distance, c.building_name, c.name AS company_name, c.id AS cid FROM staff s JOIN companies c ON c.id=s.company_id WHERE s.email=? AND s.password_hash=? AND s.active=1",
            (email, hash_pw(password))).fetchone()
    if not staff:
        return jsonify({"error": "Invalid email or password, or account not active."}), 401
    return jsonify({
        "success": True,
        "name": staff["name"],
        "company": staff["company_name"],
        "company_id": staff["cid"],
        "department": staff["department"],
        "building_lat": staff["building_lat"],
        "building_lng": staff["building_lng"],
        "max_distance": staff["max_distance"],
        "building_name": staff["building_name"] or "the building"
    })

# ── Admin: invite staff ────────────────────────────────────────────────────────
@app.route("/api/admin/staff/invite", methods=["POST"])
def invite_staff():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    d     = request.json
    name  = d.get("name", "").strip()
    email = d.get("email", "").strip().lower()
    dept  = d.get("department", "").strip()
    sid   = d.get("staff_id", "").strip()
    if not name or not email:
        return jsonify({"error": "Name and email are required."}), 400
    cid   = session["company_id"]
    cname = session["company_name"]
    # Check if already an active staff member
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM staff WHERE company_id=? AND email=? AND active=1", (cid, email)).fetchone()
        if existing:
            return jsonify({"error": "A staff member with this email already exists."}), 409
        # Expire any old pending invites for this email
        conn.execute(
            "UPDATE staff_invitations SET status='expired' WHERE company_id=? AND email=? AND status='pending'",
            (cid, email))
        token = secrets.token_urlsafe(32)
        conn.execute("""INSERT INTO staff_invitations
            (company_id,name,email,department,staff_id_code,token,created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (cid, name, email, dept, sid, token,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    base_url = os.environ.get("APP_URL", request.host_url.rstrip("/"))
    invite_link = f"{base_url}/staff/accept-invite?token={token}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:32px;border:1px solid #e5e7eb;border-radius:12px;">
      <h2 style="color:#1d4ed8;">You've been invited to WorkSight</h2>
      <p>Hi <b>{name}</b>,</p>
      <p><b>{cname}</b> has invited you to join their WorkSight attendance system.</p>
      <p>Click the button below to set up your account:</p>
      <a href="{invite_link}" style="display:inline-block;background:#1d4ed8;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:bold;margin:16px 0;">Accept Invitation</a>
      <p style="font-size:13px;color:#6b7280;">This link is unique to you. If you didn't expect this email, you can ignore it.</p>
      <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
      <p style="font-size:12px;color:#9ca3af;">WorkSight &mdash; Workforce Attendance Management</p>
    </div>
    """
    email_sent = send_email(email, f"You're invited to join {cname} on WorkSight", html)
    return jsonify({
        "success": True,
        "message": f"Invitation sent to {email}." if email_sent else f"Invite created (email not sent — check SMTP settings). Share this link: {invite_link}",
        "invite_link": invite_link,
        "email_sent": email_sent
    })

# ── Staff: accept invite (fetch details by token) ─────────────────────────────
@app.route("/api/staff/invite/details", methods=["GET"])
def invite_details():
    token = request.args.get("token", "").strip()
    if not token:
        return jsonify({"error": "Token required."}), 400
    with get_db() as conn:
        inv = conn.execute(
            "SELECT i.*, c.name AS company_name FROM staff_invitations i JOIN companies c ON c.id=i.company_id WHERE i.token=?",
            (token,)).fetchone()
    if not inv:
        return jsonify({"error": "Invalid or expired invitation link."}), 404
    if inv["status"] != "pending":
        return jsonify({"error": f"This invitation has already been {inv['status']}."}), 410
    return jsonify({
        "name": inv["name"],
        "email": inv["email"],
        "department": inv["department"],
        "staff_id_code": inv["staff_id_code"],
        "company_name": inv["company_name"]
    })

# ── Staff: complete account setup (set password) ──────────────────────────────
@app.route("/api/staff/invite/accept", methods=["POST"])
def accept_invite():
    d        = request.json
    token    = d.get("token", "").strip()
    password = d.get("password", "")
    if not token or not password:
        return jsonify({"error": "Token and password are required."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400
    with get_db() as conn:
        inv = conn.execute(
            "SELECT i.*, c.name AS company_name, c.building_lat, c.building_lng, c.max_distance, c.building_name FROM staff_invitations i JOIN companies c ON c.id=i.company_id WHERE i.token=? AND i.status='pending'",
            (token,)).fetchone()
        if not inv:
            return jsonify({"error": "Invalid or already-used invitation."}), 404
        # Check not already registered
        existing = conn.execute(
            "SELECT id FROM staff WHERE company_id=? AND email=? AND active=1",
            (inv["company_id"], inv["email"])).fetchone()
        if existing:
            return jsonify({"error": "An account with this email already exists. Please log in."}), 409
        # Create staff account
        conn.execute("""INSERT INTO staff (company_id,name,staff_id_code,department,email,password_hash,joined_at)
            VALUES (?,?,?,?,?,?,?)""",
            (inv["company_id"], inv["name"], inv["staff_id_code"], inv["department"],
             inv["email"], hash_pw(password), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        # Mark invite as accepted
        conn.execute("UPDATE staff_invitations SET status='accepted', accepted_at=? WHERE token=?",
                     (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), token))
    return jsonify({
        "success": True,
        "message": f"Account created! Welcome to {inv['company_name']}.",
        "company": inv["company_name"],
        "company_id": inv["company_id"],
        "name": inv["name"],
        "building_lat": inv["building_lat"],
        "building_lng": inv["building_lng"],
        "max_distance": inv["max_distance"],
        "building_name": inv["building_name"] or "the building"
    })

# ── List pending invitations ──────────────────────────────────────────────────
@app.route("/api/admin/staff/invitations")
def list_invitations():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    with get_db() as conn:
        invites = [dict(r) for r in conn.execute(
            "SELECT id,name,email,department,staff_id_code,status,created_at,accepted_at FROM staff_invitations WHERE company_id=? ORDER BY created_at DESC LIMIT 50",
            (session["company_id"],)).fetchall()]
    return jsonify(invites)

@app.route("/api/admin/staff/invite/cancel", methods=["POST"])
def cancel_invite():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    d = request.json
    with get_db() as conn:
        conn.execute(
            "UPDATE staff_invitations SET status='cancelled' WHERE id=? AND company_id=? AND status='pending'",
            (d.get("invite_id"), session["company_id"]))
    return jsonify({"success": True})

# ── Attendance register ───────────────────────────────────────────────────────
@app.route("/api/attendance/register", methods=["POST"])
def attendance_register():
    d           = request.json
    company_id  = d.get("company_id")
    name        = d.get("name","").strip()
    dept        = d.get("department","").strip()
    purpose     = d.get("purpose","").strip()
    action      = d.get("action","")
    lat         = d.get("latitude")
    lng         = d.get("longitude")
    selfie_b64  = d.get("selfie")
    staff_code  = d.get("staff_id","").strip()
    staff_email = d.get("email","").strip().lower()

    if not company_id or not name or action not in ("in","out"):
        return jsonify({"error": "Missing required fields."}), 400

    with get_db() as conn:
        company = conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if not company:
        return jsonify({"error": "Company not found."}), 404

    # Verify staff is registered (invited & accepted)
    with get_db() as conn:
        registered = conn.execute(
            "SELECT email, password_hash FROM staff WHERE company_id=? AND name=? AND active=1",
            (company_id, name)).fetchone()
    if not registered:
        return jsonify({"error": "You are not registered. Please accept your invitation first."}), 403
    if registered["email"] and registered["email"].lower() != staff_email:
        return jsonify({"error": "Email does not match your registered account."}), 403

    # GPS check
    gps_ok = False; distance_m = None
    if lat is not None and lng is not None:
        distance_m = haversine(lat, lng, company["building_lat"], company["building_lng"])
        if distance_m <= company["max_distance"]:
            gps_ok = True
        else:
            return jsonify({"error": f"You are {int(distance_m)}m from {company['building_name'] or 'the building'}. Must be within {company['max_distance']}m."}), 403

    # Late / overtime detection
    ts     = datetime.now()
    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
    is_late = 0; is_overtime = 0; flag = 0; flag_reason = None

    try:
        work_start = datetime.strptime(f"{ts.strftime('%Y-%m-%d')} {company['work_start']}", "%Y-%m-%d %H:%M")
        work_end   = datetime.strptime(f"{ts.strftime('%Y-%m-%d')} {company['work_end']}", "%Y-%m-%d %H:%M")
        if action == "in" and ts > work_start + timedelta(minutes=15):
            is_late = 1
        if action == "out" and ts > work_end + timedelta(minutes=30):
            is_overtime = 1
    except:
        pass

    # Suspicious duplicate detection
    with get_db() as conn:
        today = ts.strftime("%Y-%m-%d")
        last  = conn.execute("""
            SELECT action FROM attendance WHERE company_id=? AND name=?
            AND timestamp LIKE ? ORDER BY timestamp DESC LIMIT 1""",
            (company_id, name, f"{today}%")).fetchone()
    if last and last["action"] == action:
        flag = 1
        flag_reason = f"Duplicate {action} — already signed {action} earlier today"
        _add_alert(company_id, "suspicious",
                   f"{name} signed {action} twice without signing {('out' if action=='in' else 'in')}", name)

    if is_late:
        _add_alert(company_id, "late", f"{name} arrived late at {ts.strftime('%H:%M')}", name)

    # Save selfie
    selfie_path = None
    if selfie_b64:
        try:
            img_data = base64.b64decode(selfie_b64.split(",")[-1])
            fname = f"selfie_{company_id}_{ts.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}.jpg"
            selfie_path = f"static/selfies/{fname}"
            with open(selfie_path, "wb") as f:
                f.write(img_data)
        except Exception as e:
            return jsonify({"error": f"Selfie save failed: {e}"}), 500

    with get_db() as conn:
        staff = conn.execute("SELECT id FROM staff WHERE company_id=? AND name=?", (company_id, name)).fetchone()
        conn.execute("""INSERT INTO attendance
            (company_id,staff_fk,name,staff_code,department,purpose,action,timestamp,
             latitude,longitude,gps_ok,distance_m,selfie_path,is_late,is_overtime,flagged,flag_reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (company_id, staff["id"] if staff else None, name, staff_code,
             dept, purpose, action, ts_str, lat, lng, int(gps_ok), distance_m,
             selfie_path, is_late, is_overtime, flag, flag_reason))

    if company["notify_signin"]:
        send_email(company["email"],
            f"WorkSight: {name} signed {action}",
            f"<p><b>{name}</b> signed <b>{action}</b> at <b>{ts.strftime('%H:%M')}</b>.<br>"
            f"Department: {dept or '—'}<br>GPS: {'✓ Verified' if gps_ok else '✗ Failed'}</p>")

    return jsonify({
        "success": True, "message": f"{name} signed {action} successfully.",
        "timestamp": ts_str, "is_late": bool(is_late),
        "is_overtime": bool(is_overtime), "flagged": bool(flag)
    })

# ── Staff personal history ────────────────────────────────────────────────────
@app.route("/api/staff/history")
def staff_history_api():
    email    = request.args.get("email","").strip().lower()
    password = request.args.get("password","").strip()
    if not email or not password:
        return jsonify({"error": "Email and password required."}), 400
    with get_db() as conn:
        staff = conn.execute(
            "SELECT s.*, c.name AS company_name FROM staff s JOIN companies c ON c.id=s.company_id WHERE s.email=? AND s.password_hash=? AND s.active=1",
            (email, hash_pw(password))).fetchone()
        if not staff:
            return jsonify({"error": "Invalid credentials or account not found."}), 401
        records    = [dict(r) for r in conn.execute(
            "SELECT * FROM attendance WHERE company_id=? AND name=? ORDER BY timestamp DESC LIMIT 60",
            (staff["company_id"], staff["name"])).fetchall()]
        total_in   = len([r for r in records if r["action"]=="in"])
        total_late = len([r for r in records if r["is_late"]])
        score      = max(0, 100 - (total_late * 5)) if total_in else 0
    return jsonify({
        "staff": dict(staff), "company": staff["company_name"],
        "records": records, "total_in": total_in,
        "total_late": total_late, "punctuality_score": score
    })

# ── Admin dashboard ───────────────────────────────────────────────────────────
@app.route("/api/admin/dashboard")
def admin_dashboard():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    cid  = session["company_id"]
    date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    with get_db() as conn:
        company      = dict(conn.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone())
        total_staff  = conn.execute("SELECT COUNT(*) FROM staff WHERE company_id=? AND active=1", (cid,)).fetchone()[0]
        today_recs   = [dict(r) for r in conn.execute(
            "SELECT * FROM attendance WHERE company_id=? AND timestamp LIKE ? ORDER BY timestamp DESC",
            (cid, f"{date}%")).fetchall()]
        in_names     = conn.execute("""
            SELECT DISTINCT name FROM attendance WHERE company_id=? AND timestamp LIKE ? AND action='in'
            AND name NOT IN (SELECT name FROM attendance WHERE company_id=? AND timestamp LIKE ? AND action='out')
        """, (cid, f"{date}%", cid, f"{date}%")).fetchall()
        currently_in  = len(in_names)
        late_today    = conn.execute(
            "SELECT COUNT(*) FROM attendance WHERE company_id=? AND timestamp LIKE ? AND is_late=1",
            (cid, f"{date}%")).fetchone()[0]
        flagged_today = conn.execute(
            "SELECT COUNT(*) FROM attendance WHERE company_id=? AND timestamp LIKE ? AND flagged=1",
            (cid, f"{date}%")).fetchone()[0]
        weekly = []
        for i in range(6, -1, -1):
            dobj = datetime.now() - timedelta(days=i)
            ds   = dobj.strftime("%Y-%m-%d")
            cnt  = conn.execute(
                "SELECT COUNT(DISTINCT name) FROM attendance WHERE company_id=? AND timestamp LIKE ? AND action='in'",
                (cid, f"{ds}%")).fetchone()[0]
            late = conn.execute(
                "SELECT COUNT(*) FROM attendance WHERE company_id=? AND timestamp LIKE ? AND is_late=1",
                (cid, f"{ds}%")).fetchone()[0]
            weekly.append({"date": ds, "label": dobj.strftime("%a"), "count": cnt, "late": late})
        hourly = [0]*24
        for r in today_recs:
            try:
                h = int(r["timestamp"].split(" ")[1].split(":")[0])
                hourly[h] += 1
            except: pass
        dept_stats  = [dict(r) for r in conn.execute("""
            SELECT department, COUNT(*) as cnt FROM attendance
            WHERE company_id=? AND timestamp LIKE ? AND action='in'
            GROUP BY department ORDER BY cnt DESC LIMIT 8""", (cid, f"{date}%")).fetchall()]
        staff_list  = [dict(r) for r in conn.execute(
            "SELECT * FROM staff WHERE company_id=? AND active=1 ORDER BY joined_at DESC", (cid,)).fetchall()]
        punc = []
        for s in staff_list:
            total_in   = conn.execute("SELECT COUNT(*) FROM attendance WHERE company_id=? AND name=? AND action='in'", (cid, s["name"])).fetchone()[0]
            total_late = conn.execute("SELECT COUNT(*) FROM attendance WHERE company_id=? AND name=? AND is_late=1", (cid, s["name"])).fetchone()[0]
            score      = max(0, 100 - (total_late * 5)) if total_in else 0
            punc.append({"name": s["name"], "score": score, "total_in": total_in, "late": total_late})
        punc.sort(key=lambda x: x["score"], reverse=True)
        alerts_list  = [dict(r) for r in conn.execute(
            "SELECT * FROM alerts WHERE company_id=? AND read=0 ORDER BY created_at DESC LIMIT 20", (cid,)).fetchall()]
        leave_list   = [dict(r) for r in conn.execute(
            "SELECT * FROM leave_requests WHERE company_id=? ORDER BY requested_at DESC LIMIT 20", (cid,)).fetchall()]
        pending_invites = conn.execute(
            "SELECT COUNT(*) FROM staff_invitations WHERE company_id=? AND status='pending'", (cid,)).fetchone()[0]
    return jsonify({
        "company": company, "total_staff": total_staff,
        "currently_in": currently_in,
        "signed_out": len([r for r in today_recs if r["action"]=="out"]),
        "total_today": len(today_recs),
        "late_today": late_today, "flagged_today": flagged_today,
        "records": today_recs, "weekly": weekly, "hourly": hourly,
        "dept_stats": dept_stats, "staff_list": staff_list,
        "punctuality": punc, "alerts": alerts_list,
        "leave_requests": leave_list,
        "pending_invites": pending_invites
    })

@app.route("/api/admin/records")
def admin_records():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    cid       = session["company_id"]
    date_from = request.args.get("from", (datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d"))
    date_to   = request.args.get("to",   datetime.now().strftime("%Y-%m-%d"))
    name_f    = request.args.get("name","").lower()
    query     = "SELECT * FROM attendance WHERE company_id=? AND date(timestamp) BETWEEN ? AND ?"
    params    = [cid, date_from, date_to]
    if name_f: query += " AND LOWER(name) LIKE ?"; params.append(f"%{name_f}%")
    query += " ORDER BY timestamp DESC LIMIT 500"
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    return jsonify(rows)

# ── Export CSV ────────────────────────────────────────────────────────────────
@app.route("/api/admin/export/csv")
def export_csv():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    cid       = session["company_id"]
    date_from = request.args.get("from", datetime.now().strftime("%Y-%m-%d"))
    date_to   = request.args.get("to",   datetime.now().strftime("%Y-%m-%d"))
    with get_db() as conn:
        rows = conn.execute(
            "SELECT name,staff_code,department,action,timestamp,gps_ok,distance_m,is_late,is_overtime,flagged,flag_reason FROM attendance WHERE company_id=? AND date(timestamp) BETWEEN ? AND ? ORDER BY timestamp DESC",
            (cid, date_from, date_to)).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name","Staff ID","Department","Action","Timestamp","GPS OK","Distance(m)","Late","Overtime","Flagged","Flag Reason"])
    for r in rows:
        writer.writerow([r["name"],r["staff_code"],r["department"],r["action"],r["timestamp"],
                         "Yes" if r["gps_ok"] else "No",
                         round(r["distance_m"]) if r["distance_m"] else "",
                         "Yes" if r["is_late"] else "No",
                         "Yes" if r["is_overtime"] else "No",
                         "Yes" if r["flagged"] else "No",
                         r["flag_reason"] or ""])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()),
                     mimetype="text/csv", as_attachment=True,
                     download_name=f"worksight_{date_from}_{date_to}.csv")

# ── Staff search / remove ─────────────────────────────────────────────────────
@app.route("/api/admin/staff/search")
def search_staff():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    q   = request.args.get("q","").strip().lower()
    cid = session["company_id"]
    with get_db() as conn:
        staff = [dict(r) for r in conn.execute("""
            SELECT * FROM staff WHERE company_id=? AND active=1
            AND (LOWER(name) LIKE ? OR LOWER(department) LIKE ? OR LOWER(email) LIKE ? OR LOWER(staff_id_code) LIKE ?)
            ORDER BY name""", (cid, f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()]
    return jsonify(staff)

@app.route("/api/admin/staff/remove", methods=["POST"])
def remove_staff():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    d = request.json
    with get_db() as conn:
        conn.execute("UPDATE staff SET active=0 WHERE id=? AND company_id=?",
                     (d.get("staff_id"), session["company_id"]))
    return jsonify({"success": True})

# ── QR Code ───────────────────────────────────────────────────────────────────
@app.route("/api/admin/staff/qr/<int:staff_id>")
def generate_qr(staff_id):
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    if not HAS_QR:
        return jsonify({"error": "qrcode library not installed."}), 500
    cid = session["company_id"]
    with get_db() as conn:
        staff = conn.execute("SELECT * FROM staff WHERE id=? AND company_id=?", (staff_id, cid)).fetchone()
    if not staff:
        return jsonify({"error": "Staff not found."}), 404
    qr_data = json.dumps({"name": staff["name"], "email": staff["email"],
                           "staff_id": staff["staff_id_code"], "department": staff["department"]})
    img   = qrcode.make(qr_data)
    fname = f"static/qrcodes/qr_{cid}_{staff_id}.png"
    img.save(fname)
    with get_db() as conn:
        conn.execute("UPDATE staff SET qr_code=? WHERE id=?", (fname, staff_id))
    return jsonify({"success": True, "qr_path": "/"+fname})

# ── Leave requests ────────────────────────────────────────────────────────────
@app.route("/api/leave/request", methods=["POST"])
def leave_request():
    d          = request.json
    email      = d.get("email","").strip().lower()
    password   = d.get("password","").strip()
    leave_date = d.get("leave_date","")
    reason     = d.get("reason","").strip()
    with get_db() as conn:
        staff = conn.execute(
            "SELECT s.*, c.id AS cid FROM staff s JOIN companies c ON c.id=s.company_id WHERE s.email=? AND s.password_hash=? AND s.active=1",
            (email, hash_pw(password))).fetchone()
        if not staff:
            return jsonify({"error": "Invalid credentials."}), 401
        conn.execute("""INSERT INTO leave_requests (company_id,staff_name,staff_email,leave_date,reason,requested_at)
            VALUES (?,?,?,?,?,?)""",
            (staff["cid"], staff["name"], email, leave_date, reason,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    _add_alert(staff["cid"], "leave", f"{staff['name']} requested leave on {leave_date}", staff["name"])
    return jsonify({"success": True, "message": "Leave request submitted!"})

@app.route("/api/admin/leave/review", methods=["POST"])
def review_leave():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    d = request.json
    with get_db() as conn:
        conn.execute("UPDATE leave_requests SET status=?, reviewed_at=? WHERE id=? AND company_id=?",
                     (d.get("status"), datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      d.get("leave_id"), session["company_id"]))
    return jsonify({"success": True})

# ── Alerts ────────────────────────────────────────────────────────────────────
def _add_alert(company_id, alert_type, message, staff_name=None):
    with get_db() as conn:
        conn.execute("INSERT INTO alerts (company_id,type,message,staff_name,created_at) VALUES (?,?,?,?,?)",
                     (company_id, alert_type, message, staff_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

@app.route("/api/admin/alerts/read", methods=["POST"])
def mark_alerts_read():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    with get_db() as conn:
        conn.execute("UPDATE alerts SET read=1 WHERE company_id=?", (session["company_id"],))
    return jsonify({"success": True})

# ── Settings ──────────────────────────────────────────────────────────────────
@app.route("/api/admin/settings", methods=["POST"])
def update_settings():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    d = request.json
    with get_db() as conn:
        conn.execute("""UPDATE companies SET building_name=?, max_distance=?,
            work_start=?, work_end=?, notify_signin=?, notify_daily=? WHERE id=?""",
            (d.get("building_name"), d.get("max_distance", 300),
             d.get("work_start","09:00"), d.get("work_end","17:00"),
             int(d.get("notify_signin", 0)), int(d.get("notify_daily", 1)),
             session["company_id"]))
    return jsonify({"success": True})

# ── AI ────────────────────────────────────────────────────────────────────────
def _ai_call(prompt):
    import urllib.request, urllib.error
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return "⚠ GROQ_API_KEY not set in environment variables."
    payload = json.dumps({
        "model": "deepseek-r1-distill-llama-70b",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 500,
        "temperature": 0.6
    }).encode()
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return f"AI error: {e.read().decode()[:120]}"
    except Exception as e:
        return f"AI unavailable: {str(e)[:80]}"

def _build_summary(cid):
    date = datetime.now().strftime("%Y-%m-%d")
    with get_db() as conn:
        records     = conn.execute(
            "SELECT name, action, timestamp, department, is_late, is_overtime FROM attendance WHERE company_id=? AND timestamp LIKE ? ORDER BY timestamp",
            (cid, f"{date}%")).fetchall()
        total_staff = conn.execute("SELECT COUNT(*) FROM staff WHERE company_id=? AND active=1", (cid,)).fetchone()[0]
        company     = conn.execute("SELECT name FROM companies WHERE id=?", (cid,)).fetchone()
    summary = f"Company: {company['name']}. Registered staff: {total_staff}. Date: {date}. Total records: {len(records)}.\n"
    for r in records:
        tags = (" [LATE]" if r["is_late"] else "") + (" [OVERTIME]" if r["is_overtime"] else "")
        summary += f"- {r['name']} ({r['department'] or 'N/A'}) signed {r['action']} at {r['timestamp']}{tags}\n"
    return summary

@app.route("/api/ai/insight", methods=["POST"])
def ai_insight():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    summary = _build_summary(session["company_id"])
    prompt  = f"""You are WorkSight AI. Analyze this workplace attendance data and provide:
1. A brief attendance summary
2. Notable patterns or anomalies (late arrivals, overtime, suspicious activity)
3. A productivity insight
4. One clear actionable recommendation for management
Be concise, under 180 words, and professional.\n\nData:\n{summary}"""
    text = _ai_call(prompt)
    return jsonify({"insight": text, "response": text})

@app.route("/api/ai/chat", methods=["POST"])
def ai_chat():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    question = request.json.get("question","").strip()
    if not question:
        return jsonify({"error": "No question provided."}), 400
    summary = _build_summary(session["company_id"])
    prompt  = f"You are WorkSight AI. Here is today's attendance data:\n{summary}\n\nAnswer this question clearly and concisely: {question}"
    text    = _ai_call(prompt)
    return jsonify({"insight": text, "response": text})

# ── Daily summary email ───────────────────────────────────────────────────────
def send_daily_summary():
    with get_db() as conn:
        companies = conn.execute("SELECT * FROM companies WHERE notify_daily=1").fetchall()
    for company in companies:
        date = datetime.now().strftime("%Y-%m-%d")
        with get_db() as conn:
            recs = conn.execute(
                "SELECT * FROM attendance WHERE company_id=? AND timestamp LIKE ?",
                (company["id"], f"{date}%")).fetchall()
        if not recs:
            continue
        ins   = len([r for r in recs if r["action"]=="in"])
        outs  = len([r for r in recs if r["action"]=="out"])
        lates = len([r for r in recs if r["is_late"]])
        html  = f"""<h2>WorkSight Daily Summary — {date}</h2>
<p><b>Company:</b> {company['name']}</p>
<p><b>Sign-ins:</b> {ins} | <b>Sign-outs:</b> {outs} | <b>Late arrivals:</b> {lates}</p>
<table border='1' cellpadding='6' style='border-collapse:collapse'>
<tr><th>Name</th><th>Action</th><th>Time</th><th>Late?</th></tr>"""
        for r in recs:
            html += f"<tr><td>{r['name']}</td><td>{r['action']}</td><td>{r['timestamp'].split(' ')[1][:5]}</td><td>{'Yes' if r['is_late'] else 'No'}</td></tr>"
        html += "</table>"
        send_email(company["email"], f"WorkSight Daily Summary — {date}", html)

@app.route("/api/admin/send-summary", methods=["POST"])
def manual_summary():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    send_daily_summary()
    return jsonify({"success": True, "message": "Daily summary sent to your email!"})

# ── Scheduler ─────────────────────────────────────────────────────────────────
if HAS_SCHEDULER:
    scheduler = BackgroundScheduler()
    scheduler.add_job(send_daily_summary, 'cron', hour=18, minute=0)
    scheduler.start()

if __name__ == "__main__":
    init_db()
    print("\n✦ WorkSight → http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000)
PYEOF
echo "app.py written"
