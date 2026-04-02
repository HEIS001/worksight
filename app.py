import os
from flask import Flask, render_template, request, redirect
from dotenv import load_dotenv
import psycopg2

# Load environment variables
load_dotenv()

# Create Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')

# Get environment variables
PORT = os.environ.get('PORT')
DATABASE_URL = os.environ.get('DATABASE_URL')

# Database connection function
def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        # Handle login logic here
        return redirect('/dashboard')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        lat = request.form['lat']
        lon = request.form['lon']
        # Handle registration logic here
        return redirect('/login')
    return render_template('register.html')

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

@app.route('/add_staff', methods=['POST'])
def add_staff():
    name = request.form['name']
    email = request.form['email']
    password = request.form['password']
    image = request.files['image']
    # Handle adding staff logic here
    return redirect('/dashboard')

@app.route('/checkin', methods=['GET', 'POST'])
def checkin():
    if request.method == 'POST':
        email = request.form['email']
        lat = request.form['lat']
        lon = request.form['lon']
        # Handle checkin logic here
        return redirect('/dashboard')
    return render_template('checkin.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=os.environ.get('FLASK_ENV') == 'development')