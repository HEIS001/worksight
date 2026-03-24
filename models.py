from flask_sqlalchemy import SQLAlchemy
db = SQLAlchemy()
class Organization(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150))
    email = db.Column(db.String(150), unique=True)
    password = db.Column(db.String(200))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    radius = db.Column(db.Float)
class Staff(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150))
    email = db.Column(db.String(150))
    password = db.Column(db.String(200))
    image = db.Column(db.String(200))
    org_id = db.Column(db.Integer)
class CheckInLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(db.Integer)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    status = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime)
