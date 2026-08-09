import json
from pathlib import Path
from datetime import datetime


SESSION_DIR = Path("sessions")
SESSION_FILE = SESSION_DIR / "session.json"


def save_session(session):
    SESSION_DIR.mkdir(exist_ok=True)

    data = {
        "access_token": session["access_token"],
        "user_id": session["user_id"],
        "user_name": session["user_name"],
        "email": session["email"],
        "broker": session["broker"],
        "login_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    with open(SESSION_FILE, "w") as file:
        json.dump(data, file, indent=4)


def load_session():
    if not SESSION_FILE.exists():
        return None

    with open(SESSION_FILE, "r") as file:
        return json.load(file)