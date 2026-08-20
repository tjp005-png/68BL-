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
# 2. Explicit known workstation paths (pruettj1, PEREIRT446445, etc.) with standard hyphens and unicode en-dashes
# 3. Current logged-in user's OneDrive & Desktop
# 4. All other user profiles on the workstation with OneDrive (e.g., C:\Users\*\OneDrive*\...)
# 5. Local App directory (last resort static fallback)

def resolve_master_excel_path():
    # 1. Environment variable override
    env_excel = os.environ.get("MASTER_EXCEL_PATH", "").strip()
    if env_excel and os.path.exists(env_excel):
        try:
            if os.path.getsize(env_excel) > 0:
                print(f"[MASTER PROFILE] Found via MASTER_EXCEL_PATH env: {env_excel}")
                return env_excel
        except Exception:
            return env_excel

    # 2. Explicit known workstation paths (handles en-dashes \u2013 and regular hyphens)
    explicit_candidates = [
        r"C:\Users\pruettj1\OneDrive – cleanharbors.com\O365 Facilities Schedule - BL - WAP\MASTERPROFILE.xlsx",
        r"C:\Users\pruettj1\OneDrive - cleanharbors.com\O365 Facilities Schedule - BL - WAP\MASTERPROFILE.xlsx",
        r"C:\Users\pruettj1\OneDrive – cleanharbors.com\O365 Facilities Schedule – BL – WAP\MASTERPROFILE.xlsx",
        r"C:\Users\pruettj1\OneDrive - cleanharbors.com\O365 Facilities Schedule – BL – WAP\MASTERPROFILE.xlsx",
        r"C:\Users\PEREIRT446445\OneDrive - cleanharbors.com\O365 Facilities Schedule - BL - WAP\MASTERPROFILE.xlsx",
        r"C:\Users\PEREIRT446445\OneDrive – cleanharbors.com\O365 Facilities Schedule - BL - WAP\MASTERPROFILE.xlsx",
        r"C:\Users\PEREIRT446445\OneDrive - cleanharbors.com\O365 Facilities Schedule – BL – WAP\MASTERPROFILE.xlsx",
        r"C:\Users\PEREIRT446445\OneDrive – cleanharbors.com\O365 Facilities Schedule – BL – WAP\MASTERPROFILE.xlsx",
    ]
    for p in explicit_candidates:
        if os.path.exists(p):
            try:
                if os.path.getsize(p) > 0:
                    print(f"[MASTER PROFILE] Found via explicit candidate: {p}")
                    return p
            except Exception:
                return p

    # 3. Current logged-in user profile variants
    sub_folders = [
        os.path.join("O365 Facilities Schedule - BL - WAP", "MASTERPROFILE.xlsx"),
        os.path.join("O365 Facilities Schedule – BL – WAP", "MASTERPROFILE.xlsx"),
        os.path.join("O365 Facilities Schedule - BL – WAP", "MASTERPROFILE.xlsx"),
        os.path.join("O365 Facilities Schedule – BL - WAP", "MASTERPROFILE.xlsx"),
    ]
    onedrive_folders = [
        "OneDrive – cleanharbors.com",   # with unicode en-dash \u2013
        "OneDrive - cleanharbors.com",   # with standard hyphen \u002D
        "OneDrive – Clean Harbors, Inc",
        "OneDrive - Clean Harbors, Inc",
        "OneDrive – Clean Harbors",
        "OneDrive - Clean Harbors",
        "OneDrive",
    ]

    user_profile = os.environ.get("USERPROFILE", "")
    if user_profile:
        for od in onedrive_folders:
            for sub in sub_folders:
                p = os.path.join(user_profile, od, sub)
                if os.path.exists(p):
                    try:
                        if os.path.getsize(p) > 0:
                            print(f"[MASTER PROFILE] Found in user profile OneDrive: {p}")
                            return p
                    except Exception:
                        return p
        
        desktop_p = os.path.join(user_profile, "Desktop", "MASTERPROFILE.xlsx")
        if os.path.exists(desktop_p):
            try:
                if os.path.getsize(desktop_p) > 0:
                    print(f"[MASTER PROFILE] Found on Desktop: {desktop_p}")
                    return desktop_p
            except Exception:
                return desktop_p

    # 4. Multi-user workstation dynamic search (searches all C:\Users\*\ directories)
    try:
        import glob
        search_patterns = [
            r"C:\Users\*\OneDrive*\O365 Facilities Schedule*\MASTERPROFILE.xlsx",
            r"C:\Users\*\OneDrive*\*Schedule*\MASTERPROFILE.xlsx",
            r"C:\Users\*\OneDrive*\*WAP*\MASTERPROFILE.xlsx",
            r"C:\Users\*\OneDrive*\*\MASTERPROFILE.xlsx",
            r"C:\Users\*\*\O365 Facilities Schedule*\MASTERPROFILE.xlsx",
            r"C:\Users\*\Desktop\MASTERPROFILE.xlsx",
        ]
        for pat in search_patterns:
            matches = glob.glob(pat)
            for p in matches:
                if os.path.exists(p):
                    try:
                        if os.path.getsize(p) > 0:
                            print(f"[MASTER PROFILE] Found via workstation pattern '{pat}': {p}")
                            return p
                    except Exception:
                        return p
    except Exception as glob_err:
        print(f"[MASTER PROFILE] Workstation scan warning: {glob_err}")

    # 5. Dynamic directory crawler across C:\Users\ (finds any OneDrive folder regardless of dash characters or spaces)
    try:
        users_root = r"C:\Users"
        if os.path.exists(users_root):
            for u_entry in os.listdir(users_root):
                u_dir = os.path.join(users_root, u_entry)
                if not os.path.isdir(u_dir) or u_entry.lower() in ['public', 'default', 'default user', 'all users']:
                    continue
                for item in os.listdir(u_dir):
                    if 'onedrive' in item.lower():
                        od_dir = os.path.join(u_dir, item)
                        if os.path.isdir(od_dir):
                            for root, dirs, files in os.walk(od_dir):
                                depth = root[len(od_dir):].count(os.sep)
                                if depth > 3:
                                    dirs.clear()
                                    continue
                                for f in files:
                                    if f.strip().upper() == 'MASTERPROFILE.XLSX':
                                        found_p = os.path.join(root, f)
                                        if os.path.exists(found_p):
                                            try:
                                                if os.path.getsize(found_p) > 0:
                                                    print(f"[MASTER PROFILE] Found via recursive OneDrive scan: {found_p}")
                                                    return found_p
                                            except Exception:
                                                return found_p
    except Exception as crawl_err:
        print(f"[MASTER PROFILE] OneDrive directory crawl warning: {crawl_err}")

    # 6. Local App directory (LAST RESORT FALLBACK)
    local_excel = os.path.join(APP_DIR, 'MASTERPROFILE.xlsx')
    print(f"[MASTER PROFILE] Defaulting to local app directory copy: {local_excel}")
    return local_excel

def get_master_excel_path():
    global MASTER_EXCEL_PATH
    local_excel = os.path.join(APP_DIR, 'MASTERPROFILE.xlsx')
    # If currently uninitialized, pointing to local fallback, or pointing to a non-existent file, re-check live paths
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

