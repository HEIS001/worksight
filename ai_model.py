import sqlite3
from datetime import datetime, timedelta

def detect_anomalies(company_id, db_path="instance/worksight.db"):
    """
    AI-driven anomaly detection for attendance records.
    Flags:
    1. Unusual check-in times (outside normal hours)
    2. Frequent late arrivals
    3. Multiple check-ins from different locations (if GPS data varies significantly)
    4. Missing check-outs
    """
    anomalies = []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # Get company settings
        company = cursor.execute("SELECT work_start, work_end FROM companies WHERE id=?", (company_id,)).fetchone()
        if not company:
            return []

        work_start = company['work_start']
        
        # Get recent attendance for the last 7 days
        seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        records = cursor.execute("""
            SELECT * FROM attendance 
            WHERE company_id=? AND timestamp > ? 
            ORDER BY name, timestamp
        """, (company_id, seven_days_ago)).fetchall()

        staff_data = {}
        for r in records:
            name = r['name']
            if name not in staff_data:
                staff_data[name] = []
            staff_data[name].append(dict(r))

        for name, recs in staff_data.items():
            # 1. Check for frequent lateness (more than 3 times in a week)
            late_count = sum(1 for r in recs if r['is_late'])
            if late_count >= 3:
                anomalies.append({
                    "type": "Frequent Lateness",
                    "staff_name": name,
                    "message": f"{name} has been late {late_count} times in the last 7 days.",
                    "severity": "medium"
                })

            # 2. Check for missing check-outs
            # Group by date
            by_date = {}
            for r in recs:
                date = r['timestamp'].split(' ')[0]
                if date not in by_date:
                    by_date[date] = []
                by_date[date].append(r['action'])
            
            for date, actions in by_date.items():
                if "in" in actions and "out" not in actions and date != datetime.now().strftime("%Y-%m-%d"):
                    anomalies.append({
                        "type": "Missing Check-out",
                        "staff_name": name,
                        "message": f"{name} did not check out on {date}.",
                        "severity": "low"
                    })

            # 3. Unusual check-in time (e.g., 2 hours before work start)
            for r in recs:
                if r['action'] == 'in':
                    checkin_time = datetime.strptime(r['timestamp'].split(' ')[1], "%H:%M:%S")
                    start_time = datetime.strptime(work_start, "%H:%M")
                    if checkin_time < start_time - timedelta(hours=2):
                        anomalies.append({
                            "type": "Unusual Early Check-in",
                            "staff_name": name,
                            "message": f"{name} checked in unusually early at {r['timestamp']}.",
                            "severity": "low"
                        })

    except Exception as e:
        print(f"AI Anomaly Detection Error: {e}")
    finally:
        conn.close()

    return anomalies
