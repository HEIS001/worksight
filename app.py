from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from datetime import datetime, timedelta
import sqlite3, os, base64, math, secrets, string, json, csv, io
import smtplib
from werkzeug.security import generate_password_hash, check_password_hash
from face_verify import verify_face
from ai_model import detect_anomalies
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
    from fpdf2 import FPDF
    HAS_PDF = True
except ImportError:
    HAS_PDF = False
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False

app = Flask(__name__)
# WARNING: If SECRET_KEY is not set, sessions will be lost on every server restart.
# Always set SECRET_KEY as a persistent environment variable in production.
_default_key = secrets.token_hex(32)
app.secret_key = os.environ.get("SECRET_KEY", _default_key)
if not os.environ.get("SECRET_KEY"):
    print("WARNING: SECRET_KEY not set. Sessions will not persist across restarts.")

DB = "instance/worksight.db"

#── Helpers ───────────────────────────────────────────────────────────────────

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
    """Hash a password using werkzeug's PBKDF2-HMAC-SHA256 (salted)."""
    return generate_password_hash(pw)

def check_pw(pw, pw_hash):
    """Verify password — supports new werkzeug hashes and legacy unsalted SHA-256."""
    # Legacy path: raw 64-char hex with no method prefix (old SHA-256 records)
    if pw_hash and len(pw_hash) == 64 and ":" not in pw_hash and not pw_hash.startswith("pbkdf2"):
        import hashlib
        return hashlib.sha256(pw.encode()).hexdigest() == pw_hash
    return check_password_hash(pw_hash, pw)

def gen_code(length=8):
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(length))

def send_email(to_email, subject, html_body):
    """Send an email. Returns (True, None) on success, (False, reason_str) on failure."""
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")

    if not smtp_user or not smtp_pass:
        reason = "SMTP credentials (SMTP_USER / SMTP_PASS) are not configured on the server."
        print(f"WARNING: {reason}  Cannot send email to {to_email}")
        return False, reason

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = smtp_user
        msg["To"]      = to_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, to_email, msg.as_string())
        return True, None
    except Exception as e:
        print(f"Email error sending to {to_email}: {e}")
        return False, str(e)

def _add_alert(company_id, alert_type, message, staff_name=None):
    with get_db() as conn:
        conn.execute("INSERT INTO alerts (company_id,type,message,staff_name,created_at) VALUES (?,?,?,?,?)",
                    (company_id, alert_type, message, staff_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

def init_db():
    os.makedirs("instance", exist_ok=True)
    os.makedirs("static/selfies", exist_ok=True)
    os.makedirs("static/qrcodes", exist_ok=True)
    os.makedirs("static/profiles", exist_ok=True)
    
    with get_db() as conn:
        conn.executescript("""
CREATE TABLE IF NOT EXISTS companies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    owner_name      TEXT NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    join_code       TEXT UNIQUE NOT NULL,
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

CREATE TABLE IF NOT EXISTS staff (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL,
    name            TEXT  NOT NULL,
    staff_id_code   TEXT,
    department      TEXT,
    email           TEXT UNIQUE,
    password_hash   TEXT,
    profile_image   TEXT,
    joined_at       TEXT NOT NULL,
    active          INTEGER DEFAULT 1,
    qr_code         TEXT,
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS invitations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL,
    email           TEXT NOT NULL,
    token           TEXT UNIQUE NOT NULL,
    name            TEXT,
    department      TEXT,
    staff_id_code   TEXT,
    created_at      TEXT NOT NULL,
    accepted        INTEGER DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL,
    token       TEXT UNIQUE NOT NULL,
    created_at  TEXT NOT NULL,
    used        INTEGER DEFAULT 0
);
        """)

#── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/register")
def register():
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    from flask import flash
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        
        with get_db() as conn:
            company = conn.execute(
                "SELECT * FROM companies WHERE email=?",
                (email,)).fetchone()
        
        if not company or not check_pw(password, company["password_hash"]):
            flash("Invalid email or password.", "error")
            return render_template("login.html")
        
        session["company_id"]   = company["id"]
        session["company_name"] = company["name"]
        session["owner_name"]   = company["owner_name"]
        return redirect(url_for("admin"))
    
    return render_template("login.html")

@app.route("/staff")
def staff_portal():
    # Staff must authenticate via the login form first.
    # We do NOT auto-redirect here so the HTML login form is always shown;
    # the JS in staff.html handles the post-login redirect to /staff/history.
    return render_template("staff.html")

@app.route("/admin")
def admin():
    if "company_id" not in session:
        return redirect(url_for("index"))
    return render_template("admin.html")

@app.route("/staff/history")
def staff_history():
    return render_template("staff.html")

#── Company register/login ─────────────────────────────────────────────────────

@app.route("/api/company/register", methods=["POST"])
def company_register():
    d        = request.json or {}
    name     = d.get("company_name", "").strip()
    owner    = d.get("owner_name", "").strip()
    email    = d.get("email", "").strip().lower()
    password = d.get("password", "")
    bname    = d.get("building_name", "").strip()
    lat      = d.get("latitude")
    lng      = d.get("longitude")
    
    if not all([name, owner, email, password]) or lat is None or lng is None:
        return jsonify({"error": "All fields and building location are required."}), 400
    
    join_code = gen_code(8)
    try:
        with get_db() as conn:
            conn.execute("""INSERT INTO companies 
                (name,owner_name,email,password_hash,join_code,building_lat,building_lng,building_name,registered_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (name, owner, email, hash_pw(password), join_code, lat, lng, bname, 
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        return jsonify({"success": True, "join_code": join_code, "company": name})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Email already registered."}), 409

@app.route("/api/company/login", methods=["POST"])
def company_login():
    d        = request.json or {}
    email    = d.get("email", "").strip().lower()
    password = d.get("password", "")
    
    with get_db() as conn:
        company = conn.execute(
            "SELECT * FROM companies WHERE email=?",
            (email,)).fetchone()
    
    if not company or not check_pw(password, company["password_hash"]):
        return jsonify({"error": "Invalid email or password."}), 401
    
    session["company_id"]   = company["id"]
    session["company_name"] = company["name"]
    session["owner_name"]   = company["owner_name"]
    # Clear any staff session so admin login doesn't inherit a staff identity
    session.pop("staff_id",   None)
    session.pop("staff_name", None)
    
    return jsonify({"success": True, "company": company["name"], "join_code": company["join_code"]})

@app.route("/api/company/logout", methods=["GET", "POST"])
def company_logout():
    session.clear()
    if request.method == "POST":
        return jsonify({"success": True})
    return redirect(url_for("index"))

@app.route("/logout")
def general_logout():
    session.clear()
    return redirect(url_for("index"))

#── Staff Invitation & Join ───────────────────────────────────────────────────

@app.route("/api/admin/staff/invite", methods=["POST"])
def admin_invite_staff():
    """Send a staff invitation email. Handles resend and SMTP-not-configured gracefully."""
    import re
    try:
        if "company_id" not in session:
            return jsonify({"error": "Unauthorized. Please login again."}), 401

        d = request.json or {}
        email = d.get("email", "").strip().lower()
        name  = d.get("name", "").strip()
        dept  = d.get("department", "").strip()
        sid   = d.get("staff_id", "").strip()

        if not email or not name:
            return jsonify({"error": "Email and name are required."}), 400

        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
            return jsonify({"error": "Invalid email address format."}), 400

        with get_db() as conn:
            # Already a full staff member?
            existing_staff = conn.execute(
                "SELECT id FROM staff WHERE email=?", (email,)).fetchone()
            if existing_staff:
                return jsonify({"error": "This email is already registered as a staff member."}), 409

            # Pending invitation already exists — reuse token so admin can resend
            existing_invite = conn.execute(
                "SELECT id, token FROM invitations WHERE email=? AND company_id=? AND accepted=0",
                (email, session["company_id"])).fetchone()

            if existing_invite:
                token  = existing_invite["token"]
                resend = True
            else:
                token  = secrets.token_urlsafe(32)
                resend = False
                conn.execute(
                    """INSERT INTO invitations
                       (company_id, email, token, name, department, staff_id_code, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (session["company_id"], email, token, name, dept, sid,
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

        invite_link = url_for("accept_invite_page", token=token, _external=True)
        html = f"""
        <h3>Invitation to join {session['company_name']} on WorkSight</h3>
        <p>Hello {name},</p>
        <p>You have been invited to join the company dashboard.
           Click the link below to create your account:</p>
        <p><a href="{invite_link}" style="
             display:inline-block;padding:12px 24px;background:#4F46E5;
             color:#fff;text-decoration:none;border-radius:6px;font-weight:bold;">
           Accept Invitation
        </a></p>
        <p>Or copy this link into your browser:<br>
           <small>{invite_link}</small></p>
        <p style="color:#888;font-size:12px;">
           If you did not expect this email, you can safely ignore it.
        </p>
        """

        email_sent, email_error = send_email(
            email, f"Invitation to join {session['company_name']}", html)

        if not email_sent:
            # Invitation row is saved — give admin the link to share manually
            return jsonify({
                "success": True,
                "email_sent": False,
                "message": (
                    "Invitation saved, but the email could not be sent automatically. "
                    "Copy the link below and share it with the staff member."
                ),
                "link": invite_link,
                "email_error": email_error,
            })

        action = "re-sent" if resend else "sent"
        return jsonify({
            "success": True,
            "email_sent": True,
            "message": f"Invitation {action} to {email} successfully!",
        })

    except sqlite3.IntegrityError as e:
        print(f"DB integrity error in invite: {e}")
        return jsonify({"error": "A database conflict occurred. Please try again."}), 409
    except Exception as e:
        print(f"Error in admin_invite_staff: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"error": f"Server error: {str(e)}"}), 500
@app.route("/invite/accept")
def accept_invite_page():
    token = request.args.get("token", "").strip()
    if not token:
        return render_template("accept_invite.html", error="Missing invitation token.")
    
    with get_db() as conn:
        invite = conn.execute(
            "SELECT * FROM invitations WHERE token=? AND accepted=0", (token,)
        ).fetchone()
    
    if not invite:
        return render_template("accept_invite.html", error="This invitation link is invalid or has already been used.")
    
    return render_template("accept_invite.html", token=token, invite_name=invite["name"])

@app.route("/api/staff/invite/accept", methods=["POST"])
def staff_accept_invite():
    d = request.json or {}
    token = d.get("token")
    password = d.get("password")
    profile_b64 = d.get("profile_image")
    
    if not token or not password or not profile_b64:
        return jsonify({"error": "Token, password, and profile image required."}), 400
    
    with get_db() as conn:
        invite = conn.execute("SELECT * FROM invitations WHERE token=? AND accepted=0", (token,)).fetchone()
        if not invite:
            return jsonify({"error": "Invalid or expired invitation."}), 404
        
        # Save profile image for face verification
        try:
            img_data = base64.b64decode(profile_b64.split(",")[-1])
            fname = f"profile_{invite['company_id']}_{secrets.token_hex(8)}.jpg"
            profile_path = f"static/profiles/{fname}"
            with open(profile_path, "wb") as f:
                f.write(img_data)
        except Exception as e:
            return jsonify({"error": f"Profile image save failed: {e}"}), 500

        # Create staff account
        try:
            conn.execute("""INSERT INTO staff (company_id, name, staff_id_code, department, email, password_hash, profile_image, joined_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (invite["company_id"], invite["name"], invite["staff_id_code"], invite["department"], 
                 invite["email"], hash_pw(password), profile_path, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            
            conn.execute("UPDATE invitations SET accepted=1 WHERE id=?", (invite["id"],))
        except sqlite3.IntegrityError:
            return jsonify({"error": "Staff account with this email already exists."}), 409
    
    return jsonify({"success": True})

@app.route("/api/staff/login", methods=["POST"])
def staff_login():
    d = request.json or {}
    email = d.get("email","").strip().lower()
    password = d.get("password","")
    selfie_b64 = d.get("selfie")
    
    if not email or not password or not selfie_b64:
        return jsonify({"error": "Email, password, and face verification required."}), 400

    with get_db() as conn:
        staff = conn.execute("SELECT * FROM staff WHERE email=? AND active=1",
                            (email,)).fetchone()
        if not staff or not check_pw(password, staff["password_hash"]):
            return jsonify({"error": "Invalid email or password."}), 401
            
        # Face verification
        ok, msg = verify_face(selfie_b64, staff["profile_image"])
        if not ok:
            return jsonify({"error": f"Face verification failed: {msg}"}), 403

        company = conn.execute("SELECT * FROM companies WHERE id=?", (staff["company_id"],)).fetchone()
        
        session["staff_id"] = staff["id"]
        session["staff_name"] = staff["name"]
        session["company_id"] = staff["company_id"]
        
        return jsonify({
            "success": True,
            "staff": dict(staff),
            "company": dict(company)
        })

#── Attendance register ───────────────────────────────────────────────────────

@app.route("/api/attendance/register", methods=["POST"])
def attendance_register():
    d           = request.json
    company_id  = d.get("company_id")
    name        = d.get("name", "").strip()
    dept        = d.get("department", "").strip()
    purpose     = d.get("purpose", "").strip()
    action      = d.get("action", "")
    lat         = d.get("latitude")
    lng         = d.get("longitude")
    selfie_b64  = d.get("selfie")
    staff_code  = d.get("staff_id", "").strip()
    staff_email = d.get("email", "").strip().lower()
    
    if not company_id or not name or action not in ("in", "out"):
        return jsonify({"error": "Missing required fields."}), 400

    with get_db() as conn:
        company = conn.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
    if not company:
        return jsonify({"error": "Company not found."}), 404

    # Gmail check
    with get_db() as conn:
        registered = conn.execute(
            "SELECT email FROM staff WHERE company_id=? AND name=?",
            (company_id, name)).fetchone()
    if registered and registered["email"]:
        if not staff_email or registered["email"].lower() != staff_email:
            return jsonify({"error": "Gmail does not match your registered account."}), 403

    # GPS check — location is mandatory
    if lat is None or lng is None:
        return jsonify({"error": "GPS location is required to sign in or out."}), 400

    gps_ok = False; distance_m = None
    if company["building_lat"] is None or company["building_lng"] is None:
        return jsonify({"error": "Company building location not configured. Contact your administrator."}), 400
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
    except ValueError:
        # work_start/work_end format invalid — skip late/overtime detection
        print(f"Warning: could not parse work hours for company {company_id}: "
              f"work_start={company['work_start']!r}, work_end={company['work_end']!r}")

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
 
    # Email notification on sign-in
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

#── Staff personal history ────────────────────────────────────────────────────

@app.route("/api/staff/history")
def staff_history_api():
    email = request.args.get("email", "").strip().lower()
    if not email:
        return jsonify({"error": "Email required."}), 400
    
    with get_db() as conn:
        staff = conn.execute(
            "SELECT * FROM staff WHERE email=?",
            (email,)).fetchone()
        if not staff:
            return jsonify({"error": "No staff found with this email."}), 404
        
        company = conn.execute("SELECT * FROM companies WHERE id=?", (staff["company_id"],)).fetchone()
    
    records    = [dict(r) for r in conn.execute(
        "SELECT * FROM attendance WHERE company_id=? AND name=? ORDER BY timestamp DESC LIMIT 60",
        (company["id"], staff["name"])).fetchall()]
    total_in   = len([r for r in records if r["action"]=="in"])
    total_late = len([r for r in records if r["is_late"]])
    score      = max(0, 100 - (total_late * 5)) if total_in else 0
    
    return jsonify({
        "staff": dict(staff), "company": company["name"],
        "records": records, "total_in": total_in,
        "total_late": total_late, "punctuality_score": score
    })

#── Admin dashboard ───────────────────────────────────────────────────────────

@app.route("/api/admin/dashboard")
def admin_dashboard():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    cid  = session["company_id"]
    date = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
    
    with get_db() as conn:
        company_row = conn.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone()
        if not company_row:
            return jsonify({"error": "Company not found"}), 404
        
        company      = dict(company_row)
        total_staff  = conn.execute("SELECT COUNT(*) FROM staff WHERE company_id=? AND active=1", (cid,)).fetchone()[0]
        today_recs   = [dict(r) for r in conn.execute(
            "SELECT * FROM attendance WHERE company_id=? AND timestamp LIKE ? ORDER BY timestamp DESC",
            (cid, f"{date}%")).fetchall()]
        
        in_names     = conn.execute("""
            SELECT DISTINCT name FROM attendance WHERE company_id=? AND timestamp LIKE ? AND action='in'
            AND name NOT IN (SELECT name FROM attendance WHERE company_id=? AND timestamp LIKE ?  AND action='out')
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
        
        hourly = [0] * 24
        for r in today_recs:
            try:
                h = int(r["timestamp"].split(" ")[1].split(":")[0])
                hourly[h] += 1
            except (IndexError, ValueError):
                pass
        
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
    
    return jsonify({
        "company": company, "total_staff": total_staff,
        "currently_in": currently_in,
        "signed_out": len([r for r in today_recs if r["action"]=="out"]),
        "total_today": len(today_recs),
        "records": today_recs, "weekly": weekly, "hourly": hourly,
        "dept_stats": dept_stats, "staff_list": staff_list,
        "punctuality": punc, "alerts": alerts_list,
        "leave_requests": leave_list
    })

@app.route("/api/admin/records")
def admin_records():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    cid       = session["company_id"]
    date_from = request.args.get("from", (datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d"))
    date_to   = request.args.get("to",   datetime.now().strftime("%Y-%m-%d"))
    name_f    = request.args.get("name", "").lower()
    
    query     = "SELECT * FROM attendance WHERE company_id=? AND date(timestamp) BETWEEN ? AND ?"
    params    = [cid, date_from, date_to]
    if name_f: query += " AND LOWER(name) LIKE ?"; params.append(f"%{name_f}%")
    query += " ORDER BY timestamp DESC LIMIT 500"
    
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    
    return jsonify(rows)

#── Export CSV ────────────────────────────────────────────────────────────────

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
    writer.writerow(["Name", "Staff ID", "Department", "Action", "Timestamp", "GPS OK", "Distance(m)", "Late", "Overtime", "Flagged", "Flag Reason"])
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

#── Staff search ──────────────────────────────────────────────────────────────

@app.route("/api/admin/staff/search")
def search_staff():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    q   = request.args.get("q", "").strip().lower()
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
    
    d = request.json or {}
    
    with get_db() as conn:
        conn.execute("UPDATE staff SET active=0 WHERE id=? AND company_id=?",
                    (d.get("staff_id"), session["company_id"]))
    
    return jsonify({"success": True})

#── QR Code ───────────────────────────────────────────────────────────────────

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
        
        # Generate a unique sign-in token for this QR code session
        qr_token = secrets.token_urlsafe(16)

        # Store the token in the DB so the checkin page can validate it
        with get_db() as conn:
            conn.execute("UPDATE staff SET qr_code=? WHERE id=?", (qr_token, staff_id))

        # Build a full checkin URL so scanning with any phone/Google Lens opens
        # the correct checkin page pre-filled with this staff member's details.
        base_url = request.host_url.rstrip("/")
        checkin_url = (
            f"{base_url}/checkin"
            f"?token={qr_token}"
            f"&sid={staff['id']}"
            f"&name={staff['name'].replace(' ', '+')}"
            f"&dept={staff.get('department', '').replace(' ', '+')}"
            f"&cid={cid}"
        )

        qr_img = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr_img.add_data(checkin_url)
        qr_img.make(fit=True)
        img   = qr_img.make_image(fill_color="black", back_color="white")
        fname = f"static/qrcodes/qr_{cid}_{staff_id}.png"
        img.save(fname)

        return jsonify({
            "success": True,
            "qr_path": "/" + fname,
            "checkin_url": checkin_url,
            "staff_name": staff["name"],
            "staff_id_code": staff["staff_id_code"]
        })

#── Leave requests ────────────────────────────────────────────────────────────

@app.route("/checkin", methods=["GET", "POST"])
def checkin_page():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        lat   = request.form.get("lat")
        lng   = request.form.get("lon")
        
        if not email:
            return "Email required", 400
        
        with get_db() as conn:
            staff = conn.execute("SELECT * FROM staff WHERE email=?", (email,)).fetchone()
            if not staff:
                return "Staff not found", 404
        
        # Redirect to staff portal for full check-in flow
        return redirect(url_for("staff_portal"))
    
    return render_template("checkin.html")

@app.route("/api/leave/request", methods=["POST"])
def leave_request():
    d          = request.json or {}
    email      = d.get("email", "").strip().lower()
    leave_date = d.get("leave_date", "").strip()
    reason     = d.get("reason", "").strip()
    
    if not email or not leave_date:
        return jsonify({"error": "Email and leave date are required."}), 400
    
    with get_db() as conn:
        staff = conn.execute("SELECT * FROM staff WHERE email=?", (email,)).fetchone()
        if not staff:
            return jsonify({"error": "Staff not found."}), 404
        
        conn.execute("""INSERT INTO leave_requests (company_id,staff_name,staff_email,leave_date,reason,requested_at)
            VALUES (?,?,?,?,?,?)""",
            (staff["company_id"], staff["name"], email, leave_date, reason,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    
    _add_alert(staff["company_id"], "leave", f"{staff['name']} requested leave on {leave_date}", staff["name"])
    return jsonify({"success": True, "message": "Leave request submitted!"})

@app.route("/api/admin/leave/review", methods=["POST"])
def review_leave():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    d = request.json or {}
    status = d.get("status", "")
    
    if status not in ("approved", "rejected"):
        return jsonify({"error": "Status must be 'approved' or 'rejected'."}), 400
    
    with get_db() as conn:
        conn.execute("UPDATE leave_requests SET status=?, reviewed_at=? WHERE id=? AND company_id=?",
                    (status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     d.get("leave_id"), session["company_id"]))
    
    return jsonify({"success": True})

#── Alerts ────────────────────────────────────────────────────────────────────

@app.route("/api/admin/alerts/read", methods=["POST"])
def mark_alerts_read():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    with get_db() as conn:
        conn.execute("UPDATE alerts SET read=1 WHERE company_id=?", (session["company_id"],))
    
    return jsonify({"success": True})

#── Settings ──────────────────────────────────────────────────────────────────

@app.route("/api/admin/settings", methods=["POST"])
def update_settings():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    d = request.json or {}
    
    with get_db() as conn:
        conn.execute("""UPDATE companies SET building_name=?, max_distance=?,
            work_start=?, work_end=?, notify_signin=?, notify_daily=? WHERE id=?""",
            (d.get("building_name"), d.get("max_distance", 300),
             d.get("work_start", "09:00"), d.get("work_end", "17:00"),
             int(d.get("notify_signin", 0)), int(d.get("notify_daily", 1)),
             session["company_id"]))
    
    return jsonify({"success": True})

#── DeepSeek R1 AI via Groq (FREE) ───────────────────────────────────────────

def _ai_call(prompt):
    import urllib.request, urllib.error
    
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return "AI analysis unavailable (no API key)."
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    data = {
        "model": "deepseek-r1-distill-llama-70b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.6
    }
    
    req = urllib.request.Request(url, data=json.dumps(data).encode(), 
                                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            res = json.loads(response.read().decode())
            return res['choices'][0]['message']['content']
    except Exception as e:
        return f"AI error: {e}"

@app.route("/api/admin/ai/insights")
def ai_insights():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    cid = session["company_id"]
    
    with get_db() as conn:
        recs = [dict(r) for r in conn.execute(
            "SELECT name, action, timestamp, is_late, flagged FROM attendance WHERE company_id=? ORDER BY timestamp DESC LIMIT 100",
            (cid,)).fetchall()]
    
    if not recs:
        return jsonify({"insights": "Not enough data yet for AI analysis."})
    
    prompt = f"Analyze these attendance records and give 3 short, professional insights for the manager: {json.dumps(recs)}"
    return jsonify({"insights": _ai_call(prompt)})

@app.route("/api/admin/ai/anomalies")
def ai_anomalies():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    cid = session["company_id"]
    
    with get_db() as conn:
        recs = [dict(r) for r in conn.execute(
            "SELECT * FROM attendance WHERE company_id=? ORDER BY timestamp DESC LIMIT 200", (cid,)).fetchall()]
    
    anomalies = detect_anomalies(recs)
    return jsonify({
        "count": len(anomalies),
        "anomalies": anomalies
    })

#── Daily summary email ───────────────────────────────────────────────────────

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
        
        html += "</table><br><p><a href='https://worksight-2x06.onrender.com/admin'>Open Dashboard</a></p>"
        
        send_email(company["email"], f"WorkSight Daily Summary — {date}", html)

@app.route("/api/admin/send-summary", methods=["POST"])
def manual_summary():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    send_daily_summary()
    return jsonify({"success": True, "message": "Daily summary sent to your email!"})

#── Scheduler ─────────────────────────────────────────────────────────────────

# Initialise DB on startup (runs under gunicorn too, not just main)
init_db()

# Guard against running multiple scheduler instances under gunicorn multi-worker mode.
# The WERKZEUG_RUN_MAIN check only works with Flask dev server (not gunicorn).
# Instead we use an explicit env flag: set SCHEDULER_ENABLED=1 AND use --workers 1
# in your Procfile/gunicorn config, or replace this with an external cron job.
_scheduler_pid_file = "/tmp/worksight_scheduler.pid"

def _should_start_scheduler():
    """Only allow one scheduler process, identified by PID file."""
    import atexit
    
    pid = str(os.getpid())
    if os.path.exists(_scheduler_pid_file):
        try:
            with open(_scheduler_pid_file) as f:
                existing_pid = f.read().strip()
            if existing_pid and os.path.exists(f"/proc/{existing_pid}"):
                return False  # another worker already owns the scheduler
        except Exception:
            pass
    
    with open(_scheduler_pid_file, "w") as f:
        f.write(pid)
    atexit.register(lambda: os.path.exists(_scheduler_pid_file) and os.remove(_scheduler_pid_file))
    return True

if HAS_SCHEDULER and os.environ.get("SCHEDULER_ENABLED", "1") == "1":
    if _should_start_scheduler():
        scheduler = BackgroundScheduler()
        scheduler.add_job(send_daily_summary, 'cron', hour=18, minute=0)
        scheduler.start()
        print("WorkSight: background scheduler started.")

if __name__ == "__main__":
    print("\n✦ WorkSight V3 + DeepSeek R1 → http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000)

#── Password Reset ────────────────────────────────────────────────────────────

@app.route("/forgot-password")
def forgot_password_page():
    return render_template("forgot_password.html")

@app.route("/api/password-reset/request", methods=["POST"])
def password_reset_request():
    d     = request.json or {}
    email = d.get("email", "").strip().lower()
    if not email:
        return jsonify({"error": "Email is required."}), 400

    with get_db() as conn:
        company = conn.execute(
            "SELECT * FROM companies WHERE email=?", (email,)).fetchone()
        if not company:
            # Return success anyway to avoid email enumeration
            return jsonify({"success": True})

        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO password_reset_tokens (email, token, created_at) VALUES (?,?,?)",
            (email, token, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

    base_url  = request.host_url.rstrip("/")
    reset_url = f"{base_url}/reset-password?token={token}"

    html_body = f"""
    <div style="font-family:sans-serif;max-width:480px;">
      <h2 style="color:#4f46e5;">WorkSight Password Reset</h2>
      <p>Hi {company['owner_name']},</p>
      <p>We received a request to reset your WorkSight admin password.</p>
      <p style="margin:24px 0;">
        <a href="{reset_url}"
           style="background:#4f46e5;color:#fff;padding:12px 28px;border-radius:8px;
                  text-decoration:none;font-weight:700;display:inline-block;">
          Reset My Password
        </a>
      </p>
      <p style="color:#888;font-size:13px;">
        This link expires in 1 hour. If you didn't request this, ignore this email.
      </p>
    </div>"""

    try:
        send_email(email, "WorkSight: Reset your password", html_body)
        return jsonify({"success": True})
    except Exception as e:
        # Fall back: return the link directly so user is never stuck
        return jsonify({"success": True, "reset_url": reset_url,
                        "warning": "Email could not be sent. Use the link below."})


@app.route("/reset-password")
def reset_password_page():
    token = request.args.get("token", "")
    if not token:
        return redirect(url_for("login"))
    return render_template("reset_password.html", token=token)


@app.route("/api/password-reset/confirm", methods=["POST"])
def password_reset_confirm():
    d        = request.json or {}
    token    = d.get("token", "").strip()
    new_pass = d.get("password", "")

    if not token or not new_pass or len(new_pass) < 6:
        return jsonify({"error": "Token and a password of at least 6 characters are required."}), 400

    with get_db() as conn:
        row = conn.execute(
            """SELECT * FROM password_reset_tokens
               WHERE token=? AND used=0
               AND created_at >= datetime('now','-1 hour')""",
            (token,)).fetchone()
        if not row:
            return jsonify({"error": "This reset link is invalid or has expired."}), 400

        conn.execute("UPDATE companies SET password_hash=? WHERE email=?",
                     (hash_pw(new_pass), row["email"]))
        conn.execute("UPDATE password_reset_tokens SET used=1 WHERE token=?", (token,))

    return jsonify({"success": True})


#── Reports API ───────────────────────────────────────────────────────────────

@app.route("/api/admin/report")
def admin_report():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    cid   = session["company_id"]
    from_ = request.args.get("from", "")
    to_   = request.args.get("to",   "")

    with get_db() as conn:
        # Summary
        rows = conn.execute("""
            SELECT action, is_late, name FROM attendance
            WHERE company_id=?
              AND (? = '' OR date(timestamp) >= ?)
              AND (? = '' OR date(timestamp) <= ?)
        """, (cid, from_, from_, to_, to_)).fetchall()

        total_signins = sum(1 for r in rows if r["action"] == "in")
        total_late    = sum(1 for r in rows if r["action"] == "in" and r["is_late"])

        active_staff = conn.execute(
            "SELECT COUNT(*) FROM staff WHERE company_id=? AND active=1", (cid,)
        ).fetchone()[0]

        # Per-staff breakdown
        staff_rows = conn.execute("""
            SELECT name,
                   SUM(CASE WHEN action='in' THEN 1 ELSE 0 END) AS total_in,
                   SUM(CASE WHEN action='in' AND is_late=1 THEN 1 ELSE 0 END) AS late
            FROM attendance
            WHERE company_id=?
              AND (? = '' OR date(timestamp) >= ?)
              AND (? = '' OR date(timestamp) <= ?)
            GROUP BY name
            ORDER BY total_in DESC
        """, (cid, from_, from_, to_, to_)).fetchall()

        staff_list = [dict(r) for r in staff_rows]

        if total_signins > 0:
            avg_punctuality = round((1 - total_late / total_signins) * 100)
        else:
            avg_punctuality = 100

    return jsonify({
        "total_signins":    total_signins,
        "total_late":       total_late,
        "active_staff":     active_staff,
        "avg_punctuality":  avg_punctuality,
        "staff":            staff_list,
    })


@app.route("/api/admin/report/csv")
def admin_report_csv():
    if "company_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    cid   = session["company_id"]
    from_ = request.args.get("from", "")
    to_   = request.args.get("to",   "")

    with get_db() as conn:
        rows = conn.execute("""
            SELECT timestamp, name AS staff_name, action, is_late, gps_ok, department
            FROM attendance
            WHERE company_id=?
              AND (? = '' OR date(timestamp) >= ?)
              AND (? = '' OR date(timestamp) <= ?)
            ORDER BY timestamp DESC
        """, (cid, from_, from_, to_, to_)).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Timestamp", "Staff Name", "Department", "Action", "Late", "GPS OK"])
    for r in rows:
        writer.writerow([
            r["timestamp"], r["staff_name"], r["department"] or "",
            r["action"], "Yes" if r["is_late"] else "No",
            "Yes" if r["gps_ok"] else "No",
        ])

    filename = f"attendance_{from_ or 'all'}_{to_ or 'all'}.csv"
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )
