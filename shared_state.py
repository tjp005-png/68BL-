# shared_state.py
import time
import os
import sys
from flask_socketio import SocketIO

def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

APP_DIR = get_app_dir()

# Default database to APP_DIR (e.g., C:\Users\Public\Truck_Log_App) so all workstation users share the same DB
DB_PATH = os.environ.get('DB_PATH', os.path.join(APP_DIR, 'database.db'))

# Migration fallback: If DB does not exist in APP_DIR, check legacy AppData location
if not os.path.exists(DB_PATH):
    local_app_data_path = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Truck_Log_App', 'database.db')
    if local_app_data_path and os.path.exists(local_app_data_path):
        try:
            import shutil
            shutil.copy2(local_app_data_path, DB_PATH)
        except Exception:
            pass

i_drive_uploads = os.environ.get("I_DRIVE_UPLOADS_DIR", r"I:\Buttonwillow\LAB\Operations App\uploads")
drive_letter = os.path.splitdrive(i_drive_uploads)[0] + "\\"

if os.path.exists(drive_letter):
    UPLOADS_DIR = i_drive_uploads
else:
    UPLOADS_DIR = os.path.join(APP_DIR, 'uploads', 'profiles')

if not os.path.exists(UPLOADS_DIR):
    try:
        os.makedirs(UPLOADS_DIR, exist_ok=True)
    except Exception:
        pass

# Master Profile Excel File Path Resolution with multi-level fallback chain:
# 1. Environment Variable override: OS env 'MASTER_EXCEL_PATH'
# 2. User OneDrive variants (cleanharbors.com, Clean Harbors Inc, standard OneDrive)
# 3. Local App directory & Desktop fallbacks
# 4. Shared network drives (I: drive)
candidate_paths = []

env_excel = os.environ.get("MASTER_EXCEL_PATH", "").strip()
if env_excel:
    candidate_paths.append(env_excel)

user_profile = os.environ.get("USERPROFILE", "")
if user_profile:
    sub_folder = os.path.join("O365 Facilities Schedule - BL - WAP", "MASTERPROFILE.xlsx")
    candidate_paths.extend([
        os.path.join(user_profile, "OneDrive - cleanharbors.com", sub_folder),
        os.path.join(user_profile, "OneDrive - Clean Harbors, Inc", sub_folder),
        os.path.join(user_profile, "OneDrive - Clean Harbors", sub_folder),
        os.path.join(user_profile, "OneDrive", sub_folder),
        os.path.join(user_profile, "Desktop", "MASTERPROFILE.xlsx"),
    ])

local_excel = os.path.join(APP_DIR, 'MASTERPROFILE.xlsx')
candidate_paths.extend([
    local_excel,
    r"I:\Buttonwillow\LAB\Operations App\MASTERPROFILE.xlsx",
    r"I:\Buttonwillow\WAP\MASTERPROFILE.xlsx",
    r"I:\Buttonwillow\LAB\MASTERPROFILE.xlsx",
])

MASTER_EXCEL_PATH = local_excel
for path in candidate_paths:
    if path and os.path.exists(path):
        MASTER_EXCEL_PATH = path
        break

# Multi-user sync tracker dictionary for scheduling and UI refreshes
SCHEDULE_UPDATES = {'GLOBAL': 0}

# Shared SocketIO instance for real-time WebSocket communication
socketio = SocketIO(cors_allowed_origins="*")

