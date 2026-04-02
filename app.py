import os
from flask import Flask, render_template, request, redirect, session, url_for, flash
from dotenv import load_dotenv
import psycopg2
from werkzeug.security import generate_password_hash, check_password_hash

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev_secret")

# Config
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 5000))


# ---------------- DATABASE CONNECTION ---------------- #
def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        return conn
    except Exception as e:
        print("Database connection error:", e)
        return None


# ---------------- HOME ---------------- #
@app.route('/')
def index():
    return render_template('index.html')


# ---------------- AUTO DATABASE SETUP (IMPORTANT FOR YOU) ---------------- #
@app.route('/setup-db')
def setup_db():
    conn = get_db_connection()
    if conn is None:
        return "Database connection failed"

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100),
            email VARCHAR(100) UNIQUE,
            password TEXT,
            lat FLOAT,
            lon FLOAT
        );
    """)

    conn.commit()
    cur.close()
    conn.close()

    return "Database setup successful!"


# ---------------- REGISTER ---------------- #
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        lat = float(request.form['lat'])
        lon = float(request.form['lon'])

        conn = get_db_connection()
        if conn is None:
            return "Database error"

        cur = conn.cursor()

        try:
            cur.execute("""
                INSERT INTO users (name, email, password, lat, lon)
                VALUES (%s, %s, %s, %s, %s)
            """, (name, email, password, lat, lon))

            conn.commit()
        except Exception as e:
            print("Register error:", e)
            conn.rollback()
            return "Database error during registration"
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
        if conn is None:
            return "Database error"

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
            flash("Invalid email or password")
            return redirect('/login')

    return render_template('login.html')


# ---------------- DASHBOARD ---------------- #
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect('/login')

    return render_template('dashboard.html', name=session['name'])


# ---------------- LOGOUT ---------------- #
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# ---------------- RUN APP ---------------- #
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False)
