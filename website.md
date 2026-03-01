# Emergency Medical Alert Dashboard

Flask app for EMT/clinical teams to view a patient's critical info and manage live alerts.

## What’s here
- **Login** (`/login`): basic auth using users table; stores `user_id` in session.
- **Patient view** (`/<code>` → `/pii`): scanning a QR or typing the patient code sets `patient_code` in session, then shows demographics, allergies, meds, conditions, contacts, surgeries, devices.
- **Alert dashboard** (`/dashboard`): desktop view to create, monitor, update, and delete alerts.
- **Alerts API** (JSON):
  - `GET /api/alerts` — list alerts (id, title, message, severity, status, created_at).
  - `POST /api/alerts` — create alert. Body: `title` (required), `message`, `severity` (low|warning|critical|success or 0-3). Defaults: status `new`, severity normalized.
  - `DELETE /api/alerts/<id>` **or** `POST /api/alerts/<id>` — delete alert (POST kept for environments that block DELETE).
  - `POST /api/alerts/<id>/status` — set status to `new`, `responding`, or `resolved`.

## Quick start
1. Python 3.10+ recommended. Install deps:
   ```bash
   pip install flask werkzeug
   ```
2. Reset and seed the demo database (drops existing tables):
   ```bash
   python database.py
   ```
3. Run the app:
   ```bash
   python app.py
   ```
4. Login at `http://localhost:8080/login` with demo credentials:
   - Username: `john doe`
   - Password: `password123`
5. Patient flow: open `http://localhost:8080/EMG-QR-001`, you’ll be redirected to login, then to `/pii`.
6. Alerts flow: open `http://localhost:8080/dashboard` to watch for incoming alerts or create new ones from the form.

## Data model (key pieces)
- `users(username, password)` — passwords are hashed (Werkzeug).
- `patients` plus related tables for allergies, medications, conditions, contacts, primary care, surgeries, implantable devices.
- `alerts(id, title, message, severity, status, created_at, alert_file)` — seeded empty by default; status starts as `new`.

## Notes
- Severity integers map to text: 0/1 → low, 2 → warning, 3 → critical. “resolved” maps to success badge.
- Front-end polling for alerts is every 5s (`static/js/dashboard.js`).
- If you change the demo user or seed data, re-run `database.py` to rebuild `medical.db`.
