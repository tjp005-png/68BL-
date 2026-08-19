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
# 2. Current logged-in user's OneDrive & Desktop
# 3. All other user profiles on the workstation with OneDrive (e.g., C:\Users\*\OneDrive*\...)
# 4. Shared network drives (I: drive)
# 5. Local App directory (last resort static fallback)

def resolve_master_excel_path():
    env_excel = os.environ.get("MASTER_EXCEL_PATH", "").strip()
    if env_excel and os.path.exists(env_excel):
        try:
            if os.path.getsize(env_excel) > 0:
                return env_excel
        except Exception:
            return env_excel

    sub_folder = os.path.join("O365 Facilities Schedule - BL - WAP", "MASTERPROFILE.xlsx")
    
    # Check current user's profile
    user_profile = os.environ.get("USERPROFILE", "")
    if user_profile:
        for od in [
            "OneDrive - cleanharbors.com",
            "OneDrive - Clean Harbors, Inc",
            "OneDrive - Clean Harbors",
            "OneDrive",
        ]:
            p = os.path.join(user_profile, od, sub_folder)
            if os.path.exists(p):
                try:
                    if os.path.getsize(p) > 0:
                        return p
                except Exception:
                    return p
        
        desktop_p = os.path.join(user_profile, "Desktop", "MASTERPROFILE.xlsx")
        if os.path.exists(desktop_p):
            try:
                if os.path.getsize(desktop_p) > 0:
                    return desktop_p
            except Exception:
                return desktop_p

    # Check other user profiles on the workstation (solves C:\Users\Public running when another user synced OneDrive)
    try:
        import glob
        for p in glob.glob(r"C:\Users\*\OneDrive*\O365 Facilities Schedule - BL - WAP\MASTERPROFILE.xlsx"):
            if os.path.exists(p):
                try:
                    if os.path.getsize(p) > 0:
                        return p
                except Exception:
                    return p
        for p in glob.glob(r"C:\Users\*\Desktop\MASTERPROFILE.xlsx"):
            if os.path.exists(p):
                try:
                    if os.path.getsize(p) > 0:
                        return p
                except Exception:
                    return p
    except Exception:
        pass

    # Check shared network drives (I: drive)
    network_paths = [
        r"I:\Buttonwillow\LAB\Operations App\MASTERPROFILE.xlsx",
        r"I:\Buttonwillow\WAP\MASTERPROFILE.xlsx",
        r"I:\Buttonwillow\LAB\MASTERPROFILE.xlsx",
    ]
    for p in network_paths:
        drive_letter = os.path.splitdrive(p)[0] + "\\"
        if os.path.exists(drive_letter) and os.path.exists(p):
            try:
                if os.path.getsize(p) > 0:
                    return p
            except Exception:
                return p

    # Local App directory (LAST RESORT FALLBACK)
    local_excel = os.path.join(APP_DIR, 'MASTERPROFILE.xlsx')
    return local_excel

def get_master_excel_path():
    global MASTER_EXCEL_PATH
    local_excel = os.path.join(APP_DIR, 'MASTERPROFILE.xlsx')
    # If currently pointing to local fallback or non-existent file, re-check if a live file is available
    if not MASTER_EXCEL_PATH or MASTER_EXCEL_PATH == local_excel or not os.path.exists(MASTER_EXCEL_PATH):
        resolved = resolve_master_excel_path()
        if resolved and os.path.exists(resolved):
            MASTER_EXCEL_PATH = resolved
    return MASTER_EXCEL_PATH

MASTER_EXCEL_PATH = resolve_master_excel_path()

# Multi-user sync tracker dictionary for scheduling and UI refreshes
SCHEDULE_UPDATES = {'GLOBAL': 0}

# Shared SocketIO instance for real-time WebSocket communication
socketio = SocketIO(cors_allowed_origins="*")

