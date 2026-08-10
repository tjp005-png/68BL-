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

# Store live database in local AppData to prevent OneDrive sync lock collisions & cloud file reverts
app_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'Truck_Log_App')
os.makedirs(app_data_dir, exist_ok=True)
DB_PATH = os.environ.get('DB_PATH', os.path.join(app_data_dir, 'database.db'))

# Fallback migration if new location does not exist
old_db = os.path.join(APP_DIR, 'database.db')
if not os.path.exists(DB_PATH) and os.path.exists(old_db):
    try:
        import shutil
        shutil.copy2(old_db, DB_PATH)
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

local_excel = os.path.join(APP_DIR, 'MASTERPROFILE.xlsx')

# Resolve OneDrive path dynamically for the currently logged-in user.
# OneDrive automatically synchronizes the cloud file locally in the background.
user_profile = os.environ.get("USERPROFILE", "")
dynamic_excel = ""
if user_profile:
    dynamic_excel = os.path.join(user_profile, "OneDrive - cleanharbors.com", "O365 Facilities Schedule - BL - WAP", "MASTERPROFILE.xlsx")

if dynamic_excel and os.path.exists(dynamic_excel):
    MASTER_EXCEL_PATH = dynamic_excel
elif os.path.exists(local_excel):
    MASTER_EXCEL_PATH = local_excel
else:
    MASTER_EXCEL_PATH = local_excel

# Multi-user sync tracker dictionary for scheduling and UI refreshes
SCHEDULE_UPDATES = {'GLOBAL': 0}

# Shared SocketIO instance for real-time WebSocket communication
socketio = SocketIO(cors_allowed_origins="*")

