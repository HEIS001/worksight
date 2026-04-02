import os
from flask import Flask, render_template, request, redirect, session, url_for, flash
from dotenv import load_dotenv
import psycopg2
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev_secret_key")

# Config
PORT = int(os.getenv("PORT", 5000))
DATABASE_URL = os.getenv("DATABASE_URL")

# Ensure upload folder exists
UPLOAD_FOLDER = "static/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ---------------- DATABASE CONNECTION ---------------- #
def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print("DB Connection Error:", e)
        return None


# ---------------- HOME ---------------- #
@app.route('/')
def index():
    return render_template('index.html')


# ---------------- REGISTER ---------------- #
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        lat = float(request.form['lat'])
        lon = float(request.form['lon'])

        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        if conn is None:
            return "Database error"

        cur = conn.cursor()

        try:
            cur.execute("""
                INSERT INTO users (name, email, password, lat, lon)
                VALUES (%s, %s, %s, %s, %s)
            """, (name, email, hashed_password, lat, lon))

            conn.commit()
        except Exception as e:
            print("Register Error:", e)
            conn.rollback()
        finally:
            cur.close()
            conn.close()

        return redirect('/login')

    return render_template('register.html')


# ---------------- LOGIN ---------------- #
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT id, name, email, password FROM users WHERE email=%s", (email,))
        user = cur.fetchone()

        cur.close()
        conn.close()

        if user and check_password_hash(user[3], password):
            session['user_id'] = user[0]
            session['name'] = user[1]
            return redirect('/dashboard')
        else:
            flash("Invalid login details")
            return redirect('/login')

    return render_template('login.html')


# ---------------- LOGOUT ---------------- #
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# ---------------- DASHBOARD ---------------- #
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect('/login')

    return render_template('dashboard.html', name=session['name'])


# ---------------- ADD STAFF ---------------- #
@app.route('/add_staff', methods=['POST'])
def add_staff():
    if 'user_id' not in session:
        return redirect('/login')

    name = request.form['name']
    email = request.form['email']
    password = generate_password_hash(request.form['password'])

    image = request.files['image']
    image_path = None

    if image:
        image_path = os.path.join(UPLOAD_FOLDER, image.filename)
        image.save(image_path)

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO staff (name, email, password, image)
            VALUES (%s, %s, %s, %s)
        """, (name, email, password, image_path))

        conn.commit()
    except Exception as e:
        print("Add Staff Error:", e)
        conn.rollback()
    finally:
        cur.close()
        conn.close()

    return redirect('/dashboard')


# ---------------- CHECK-IN ---------------- #
@app.route('/checkin', methods=['GET', 'POST'])
def checkin():
    if 'user_id' not in session:
        return redirect('/login')

    if request.method == 'POST':
        email = request.form['email']
        lat = float(request.form['lat'])
        lon = float(request.form['lon'])
        time_now = datetime.now()

        conn = get_db_connection()
        cur = conn.cursor()

        try:
            cur.execute("""
                INSERT INTO attendance (email, lat, lon, time)
                VALUES (%s, %s, %s, %s)
            """, (email, lat, lon, time_now))

            conn.commit()
        except Exception as e:
            print("Check-in Error:", e)
            conn.rollback()
        finally:
            cur.close()
            conn.close()

        return redirect('/dashboard')

    return render_template('checkin.html')


# ---------------- RUN APP ---------------- #
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=os.getenv('FLASK_ENV') == 'development')
