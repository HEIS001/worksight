-- WorkSight SQLite Schema
-- This matches the tables created by init_db() in app.py

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
    name            TEXT NOT NULL,
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
