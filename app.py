from flask import Flask, render_template, request, redirect, session
from models import db, Organization, Staff, CheckInLog
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from ai_model import predict_location
import math, os

app = Flask(__name__)
app.secret_key = "secret"

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///worksight.db"
app.config["UPLOAD_FOLDER"] = "static/uploads"

db.init_app(app)

def login_required(func):
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        return func(*args, **kwargs)
    return wrapper

def distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2-lat1)
    dlambda = math.radians(lon2-lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * (2*math.atan2(math.sqrt(a), math.sqrt(1-a)))

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        org = Organization(
            name=request.form["name"],
            email=request.form["email"],
            password=generate_password_hash(request.form["password"]),
            latitude=float(request.form["lat"]),
            longitude=float(request.form["lon"]),
            radius=50
        )
        db.session.add(org)
        db.session.commit()
        return redirect("/login")
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        user = Organization.query.filter_by(email=request.form["email"]).first()
        if user and check_password_hash(user.password, request.form["password"]):
            session["user_id"] = user.id
            return redirect("/dashboard")
    return render_template("login.html")

@app.route("/dashboard")
@login_required
def dashboard():
    logs = CheckInLog.query.all()
    staff = Staff.query.all()
    return render_template("dashboard.html", logs=logs, staff=staff)

@app.route("/add_staff", methods=["POST"])
@login_required
def add_staff():
    file = request.files["image"]
    filename = file.filename
    file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
    staff = Staff(
        name=request.form["name"],
        email=request.form["email"],
        password=generate_password_hash(request.form["password"]),
        image=filename,
        org_id=session["user_id"]
    )
    db.session.add(staff)
    db.session.commit()
    return redirect("/dashboard")

@app.route("/checkin", methods=["POST"])
def checkin():
    staff = Staff.query.filter_by(email=request.form["email"]).first()
    lat = float(request.form["lat"])
    lon = float(request.form["lon"])
    org = Organization.query.get(staff.org_id)
    dist = distance(lat, lon, org.latitude, org.longitude)
    gps_ok = dist <= org.radius
    ai_ok = predict_location(lat, lon)
    status = "Valid" if gps_ok and ai_ok else "Suspicious"
    log = CheckInLog(
        staff_id=staff.id,
        latitude=lat,
        longitude=lon,
        status=status,
        timestamp=datetime.now()
    )
    db.session.add(log)
    db.session.commit()
    return "Checked"

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=10000)
