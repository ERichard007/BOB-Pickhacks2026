import sqlite3
from werkzeug.security import generate_password_hash

conn = sqlite3.connect("medical.db")
cursor = conn.cursor()

tables = [
    "users",
    "alerts",
    "allergies",
    "medications",
    "medical_conditions",
    "emergency_contacts",
    "primary_care_physicians",
    "surgical_history",
    "implantable_devices",
    "patients",
]

for t in tables:
    cursor.execute(f"DROP TABLE IF EXISTS {t}")

# Create Authorized Users Table
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL
)
""")

# Create Alerts Table
cursor.execute("""
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    message TEXT,
    severity INTEGER NOT NULL DEFAULT 1,  
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'new',
    audio BLOB,
    frames BLOB,
    annotated_frames BLOB
)
""") # severity: 0=resolved 1=low, 2=medium, 3=critical

# Create Patients Table
cursor.execute("""
CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    full_name TEXT NOT NULL,
    age INTEGER NOT NULL,
    blood_type TEXT,    
    dnr_status INTEGER NOT NULL DEFAULT 0   
)
""")

# patient allergy table
cursor.execute("""
CREATE TABLE IF NOT EXISTS allergies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    allergy_name TEXT NOT NULL,
    allergy_details TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id)
)
""")

# medications table
cursor.execute("""
CREATE TABLE IF NOT EXISTS medications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    medication_category TEXT NOT NULL,
    medication_name TEXT NOT NULL,
    medication_brand TEXT,
    dosage TEXT,
    frequency TEXT,
    administration_route TEXT,
    currently_taking INTEGER NOT NULL DEFAULT 1,
    high_risk INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (patient_id) REFERENCES patients(id)
)
""")

# medical conditions table
cursor.execute("""
CREATE TABLE IF NOT EXISTS medical_conditions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    condition_name TEXT NOT NULL,
    condition_category TEXT,
    diagnosis_date TEXT,
    condition_details TEXT,
    severity TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (patient_id) REFERENCES patients(id)
)
""")

# emergency contacts table
cursor.execute("""
CREATE TABLE IF NOT EXISTS emergency_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    contact_name TEXT NOT NULL,
    relationship TEXT,
    phone_number TEXT NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES patients(id)
)
""")

# Primary care physicians table
cursor.execute("""
CREATE TABLE IF NOT EXISTS primary_care_physicians (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    physician_name TEXT NOT NULL,
    contact_info TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id)
)
""")

# Surgical history table
cursor.execute("""
CREATE TABLE IF NOT EXISTS surgical_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    surgery_name TEXT NOT NULL,
    surgery_category TEXT,
    surgery_date TEXT,
    surgery_details TEXT,
    surgery_high_risk INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (patient_id) REFERENCES patients(id)
)
""")

# Implantable devices table
cursor.execute("""
CREATE TABLE IF NOT EXISTS implantable_devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    device_name TEXT NOT NULL,
    device_type TEXT,
    implantation_date TEXT,
    device_details TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id)
)
""")

# Insert demo user
cursor.execute("""
INSERT INTO users (username, password) VALUES (?, ?)
""", ("john doe", generate_password_hash("password123")))

# Insert demo patient
cursor.execute("""
INSERT INTO patients (code, full_name, age, blood_type, dnr_status)
VALUES (?, ?, ?, ?, ?)
""", (
    "EMG-QR-001",
    "Robert Martinez",
    65,
    "O+",
    0
))

patient_id = cursor.lastrowid

cursor.execute("""
INSERT INTO allergies (patient_id, allergy_name, allergy_details)
VALUES (?, ?, ?)
""", (
    patient_id,
    "Penicillin",
    "Severe anaphylactic reaction requiring intubation in 2018"
))

# Warfarin (high risk)
cursor.execute("""
INSERT INTO medications (
    patient_id,
    medication_category,
    medication_name,
    medication_brand,
    dosage,
    frequency,
    administration_route,
    currently_taking,
    high_risk
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    patient_id,
    "Anticoagulant",
    "Warfarin",
    "Coumadin",
    "5 mg",
    "Once daily",
    "Oral",
    1,
    1
))

# Insulin
cursor.execute("""
INSERT INTO medications (
    patient_id,
    medication_category,
    medication_name,
    medication_brand,
    dosage,
    frequency,
    administration_route,
    currently_taking,
    high_risk
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    patient_id,
    "Endocrine",
    "Insulin Glargine",
    "Lantus",
    "20 units",
    "Once daily",
    "Subcutaneous",
    1,
    1
))

# Type 1 Diabetes
cursor.execute("""
INSERT INTO medical_conditions (
    patient_id,
    condition_name,
    condition_category,
    diagnosis_date,
    condition_details,
    severity,
    is_active
)
VALUES (?, ?, ?, ?, ?, ?, ?)
""", (
    patient_id,
    "Type 1 Diabetes",
    "Endocrine",
    "1995-06-12",
    "Insulin dependent. Prone to hypoglycemia.",
    "Severe",
    1
))

# Coronary Artery Disease
cursor.execute("""
INSERT INTO medical_conditions (
    patient_id,
    condition_name,
    condition_category,
    diagnosis_date,
    condition_details,
    severity,
    is_active
)
VALUES (?, ?, ?, ?, ?, ?, ?)
""", (
    patient_id,
    "Coronary Artery Disease",
    "Cardiac",
    "2015-03-20",
    "History of chest pain and prior stent placement.",
    "Moderate",
    1
))

# Mechanical Valve Replacement
cursor.execute("""
INSERT INTO surgical_history (
    patient_id,
    surgery_name,
    surgery_category,
    surgery_date,
    surgery_details,
    surgery_high_risk
)
VALUES (?, ?, ?, ?, ?, ?)
""", (
    patient_id,
    "Mechanical Aortic Valve Replacement",
    "Cardiac",
    "2018-09-10",
    "Requires lifelong anticoagulation therapy.",
    1
))

cursor.execute("""
INSERT INTO implantable_devices (
    patient_id,
    device_name,
    device_type,
    implantation_date,
    device_details
)
VALUES (?, ?, ?, ?, ?)
""", (
    patient_id,
    "Pacemaker",
    "Cardiac Device",
    "2021-04-15",
    "Dual chamber pacemaker."
))

cursor.execute("""
INSERT INTO emergency_contacts (
    patient_id,
    contact_name,
    relationship,
    phone_number
)
VALUES (?, ?, ?, ?)
""", (
    patient_id,
    "Maria Martinez",
    "Spouse",
    "555-123-7890"
))

cursor.execute("""
INSERT INTO primary_care_physicians (
    patient_id,
    physician_name,
    contact_info
)
VALUES (?, ?, ?)
""", (
    patient_id,
    "Dr. Steven Clark",
    "555-987-6543"
))

conn.commit()
conn.close()


               
               