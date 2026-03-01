from __future__ import annotations
from importlib.metadata import metadata
import sqlite3
from functools import wraps
from typing import Optional
import os
import io
import zipfile
import shutil

from flask import (
    Flask,
    request,
    redirect,
    render_template,
    session,
    url_for,
    abort,
    jsonify,
    Response
)

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import zipfile
import json

app = Flask(__name__)
app.secret_key = "admin123" # Production key should be a secure random value and kept secret

SEVERITY_MAP = {
    "0": "low",
    "1": "low",
    "2": "warning",
    "3": "critical",
    "resolved": "success",
    "info": "low",
    "low": "low",
    "medium": "warning",
    "critical": "critical",
    "warning": "warning",
    "success": "success",
}

def normalize_severity(value: str) -> str:
    val = str(value or "info").strip().lower()
    return SEVERITY_MAP.get(val, "info")

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect("login")
        return f(*args, **kwargs)
    return decorated_function

def analyze_data(data):
    print(data)
    return "Automated Alert", "2", "alert generated"

@app.route("/")
def index():
    return redirect("login")

@app.route("/<code>")
def entry(code):
    conn = sqlite3.connect("medical.db")
    cur = conn.execute(
        "SELECT code FROM patients WHERE code = ?",
        (code,)
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        abort(404, description="Invalid patient code")

    session["patient_code"] = row[0]
    return redirect("login")

@app.route("/login", methods=["GET", "POST"])
def login():
    error: Optional[str] = None

    if request.method == "POST":
        username = (request.form.get("username") or "")
        password = request.form.get("password") or ""

        conn = sqlite3.connect("medical.db")
        cur = conn.cursor()

        cur.execute(
            "SELECT id, password, username FROM users WHERE username = ?",
            (username,),
        )

        row = cur.fetchone()
        conn.close()

        if row and check_password_hash(row[1], password):
            session["user_id"] = row[0]
            session["username"] = row[2]

            if "patient_code" not in session:
                return redirect("dashboard")

            return redirect("pii")
        
        error = "Invalid username or password"

    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("login")

@app.route("/dashboard")
@login_required
def dashboard():
    conn = sqlite3.connect("medical.db")
    alerts = conn.execute(
        """
        SELECT id, title, message, severity, status, created_at
        FROM alerts
        ORDER BY id DESC
        """
    ).fetchall()
    conn.close()

    alerts = [
        {
            "id": row[0],
            "title": row[1],
            "message": row[2],
            "severity": normalize_severity(row[3]),
            "status": row[4],
            "created_at": row[5],
        }
        for row in alerts
    ]
    return render_template("dashboard.html", alerts=alerts)

@app.route("/pii")
@login_required
def pii():
    code = session.get("patient_code")

    if not code:
        abort(400, description="Missing patient code, error in scanning process. Contact: 864-344-4456")

    conn = sqlite3.connect("medical.db")
    cur = conn.cursor()
    patient = cur.execute("""
    SELECT id, full_name, age, blood_type, dnr_status
    FROM patients
    WHERE code = ?
    """, (code,)).fetchone()

    if not patient:
        abort(404, description="Patient not found. Contact: 864-344-4456")

    allergies = conn.execute(
    """ SELECT allergy_name, allergy_details 
    FROM allergies 
    WHERE patient_id = ?
    """, (patient[0],)).fetchall()

    medications = conn.execute(
    """ SELECT medication_category, medication_name, medication_brand, dosage, frequency, administration_route, currently_taking, high_risk
    FROM medications
    WHERE patient_id = ?
    """, (patient[0],)).fetchall()

    medical_conditions = conn.execute(
    """ SELECT condition_name, condition_category, diagnosis_date, condition_details, severity, is_active
    FROM medical_conditions
    WHERE patient_id = ?
    """, (patient[0],)).fetchall()

    emergency_contacts = conn.execute(
    """ SELECT contact_name, relationship, phone_number
    FROM emergency_contacts
    WHERE patient_id = ?
    """, (patient[0],)).fetchall()

    primary_care_physicians = conn.execute(
    """ SELECT physician_name, contact_info
    FROM primary_care_physicians
    WHERE patient_id = ?
    """, (patient[0],)).fetchall()

    surgical_history = conn.execute(
    """ SELECT surgery_name, surgery_category, surgery_date, surgery_details, surgery_high_risk
    FROM surgical_history
    WHERE patient_id = ?
    """, (patient[0],)).fetchall()

    implantable_devices = conn.execute(
    """ SELECT device_name, device_type, implantation_date, device_details
    FROM implantable_devices
    WHERE patient_id = ?
    """, (patient[0],)).fetchall()

    conn.close()

    return render_template("pii.html", person=patient, code=code, allergies=allergies, medications=medications, medical_conditions=medical_conditions, emergency_contacts=emergency_contacts, primary_care_physicians=primary_care_physicians, surgical_history=surgical_history, implantable_devices=implantable_devices)

@app.route("/api/alerts", methods=["GET", "POST"])
@login_required
def alerts_api():

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        title = (payload.get("title") or request.form.get("title") or "").strip()
        message = (payload.get("message") or request.form.get("message") or "").strip()
        severity = normalize_severity((payload.get("severity") or request.form.get("severity") or "info"))
        status = "new"

        if not title:
            return jsonify({"error": "title is required"}), 400

        conn = sqlite3.connect("medical.db")
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO alerts (title, message, severity, status, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
            (title, message, severity, status),
        )
        alert_id = cur.lastrowid
        conn.commit()
        conn.close()
        return jsonify({"id": alert_id, "title": title, "message": message, "severity": str(severity), "status": status}), 201

    # GET
    conn = sqlite3.connect("medical.db")
    conn.row_factory = sqlite3.Row
    alerts = conn.execute(
        """SELECT id, title, message, severity, status, created_at 
        FROM alerts 
        ORDER BY 
            CASE
                WHEN severity IN ('critical', '3') THEN 1
                WHEN severity IN ('warning', '2') THEN 2
                WHEN severity IN ('low', '1') THEN 3
                ELSE 4
            END ASC,
            CASE
                WHEN status = 'new' THEN 1
                WHEN status = 'responding' THEN 2
                WHEN status = 'resolved' THEN 3
                ELSE 4
            END ASC,
            created_at DESC"""
            
    ).fetchall()
    conn.close()
    return jsonify(
        [
            {
                "id": row["id"],
                "title": row["title"],
                "message": row["message"],
                "severity": normalize_severity(row["severity"]),
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in alerts
        ]
    )

@app.route("/api/alerts/<int:alert_id>", methods=["DELETE", "POST"])
@login_required
def delete_alert(alert_id: int):
    conn = sqlite3.connect("medical.db")
    cur = conn.cursor()

    cur.execute("SELECT created_at FROM alerts WHERE id = ?", (alert_id,))
    created_at = cur.fetchone()

    cur.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    
    if deleted == 0:
        return jsonify({"error": "Not found"}), 404
    
    incident_folder = os.path.join("uploads/files", f"incident{alert_id}")
    incident_zip = os.path.join("uploads/zips", f"incident{alert_id}.zip")
    archive_folder_files = os.path.join("archived_logs/files", f"incident{alert_id}-{created_at[0].replace(':', '-').replace(' ', '_')}")
    archive_zip_files = os.path.join("archived_logs/zips", f"incident{alert_id}-{created_at[0].replace(':', '-').replace(' ', '_')}.zip")

    if not os.path.exists(archive_folder_files):
        os.makedirs(archive_folder_files)

    if not os.path.exists(archive_zip_files):
        os.makedirs(archive_zip_files)

    if os.path.exists(incident_folder):
        shutil.move(incident_folder, archive_folder_files)

    if os.path.exists(incident_zip):
        shutil.move(incident_zip, archive_zip_files)

    return jsonify({"status": "deleted", "id": alert_id})

@app.route("/api/alerts/<int:alert_id>/status", methods=["POST"])
@login_required
def update_alert_status(alert_id: int):
    payload = request.get_json(silent=True) or {}
    status = (payload.get("status") or "").strip().lower()
    if status not in {"new", "responding", "resolved"}:
        return jsonify({"error": "Invalid status"}), 400

    conn = sqlite3.connect("medical.db")
    cur = conn.execute("UPDATE alerts SET status = ? WHERE id = ?", (status, alert_id))
    conn.commit()
    updated = cur.rowcount
    conn.close()
    if updated == 0:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"status": status, "id": alert_id})

@app.route("/api/pi/info", methods=["POST"])
@app.route("/api/pi/info/", methods=["POST"])
def receive_info_zip():
    print("**********Received info zip************")

    file = request.files.get("file")
    if not file:
        return "No file provided", 400
    
    conn = sqlite3.connect("medical.db")

    last_row = conn.execute("SELECT id FROM alerts ORDER BY id DESC").fetchone()
    lastid = last_row[0] if last_row else 0

    conn.close()

    newid = lastid + 1

    zip_path = os.path.join("uploads/zips", f"incident{newid}.zip")
    file.save(zip_path)

    extract_dir = os.path.join("uploads/files", f"incident{newid}")

    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(extract_dir)
    except zipfile.BadZipFile:
        return "Uploaded file is not a valid zip", 400

    incident_json_path = os.path.join(extract_dir, "incident.json")
    incident = {}
    if os.path.exists(incident_json_path):
        with open(incident_json_path) as f:
            incident = json.load(f)
            print("Incident loaded:", incident["type"], incident["severity"])

    def zip_folder_to_blob(folder_path):
        blob_io = io.BytesIO()
        with zipfile.ZipFile(blob_io, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(folder_path):
                for fn in files:
                    full_path = os.path.join(root, fn)
                    rel_path = os.path.relpath(full_path, folder_path)
                    zf.write(full_path, rel_path)
        return blob_io.getvalue()

    audio_folder = os.path.join(extract_dir, "audio")
    frames_folder = os.path.join(extract_dir, "frames")
    annotated_folder = os.path.join(extract_dir, "frames_annotated")

    audio_blob = zip_folder_to_blob(audio_folder)        
    frames_blob = zip_folder_to_blob(frames_folder)      
    annotated_blob = zip_folder_to_blob(annotated_folder)

    conn = sqlite3.connect("medical.db")
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO alerts (title, message, severity, status, created_at, audio, frames, annotated_frames)
    VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?)
    """, (
        "Automated Alert: " + incident.get("type", "Automated Alert"),
        incident.get("details", {}).get("reason", "") + " -> Audio Transcript: " + incident.get("evidence", {}).get("stitched", ""),
        3 if incident.get("severity", "") == "urgent" else 1,
        "new",
        audio_blob,
        frames_blob,
        annotated_blob
    ))

    conn.commit()
    conn.close()

    return "INFO RECEIVED", 200

@app.route("/api/alerts/<int:alert_id>/assets/<string:asset_type>")
def get_alert_asset(alert_id, asset_type):
    conn = sqlite3.connect("medical.db")
    cur = conn.cursor()
    cur.execute(f"SELECT {asset_type} FROM alerts WHERE id=?", (alert_id,))
    row = cur.fetchone()
    conn.close()

    if not row or not row[0]:
        return "Not found", 404

    blob_data = row[0]

    if asset_type == "audio":
        return Response(blob_data, mimetype="audio/wav")
    else:
        return Response(blob_data, mimetype="application/zip")
    
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
