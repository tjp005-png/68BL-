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
DB_PATH = os.path.join(APP_DIR, 'database.db')

UPLOADS_DIR = os.path.join(APP_DIR, 'uploads', 'profiles')
if not os.path.exists(UPLOADS_DIR):
    try:
        os.makedirs(UPLOADS_DIR)
    except:
        pass

local_excel = os.path.join(APP_DIR, 'MASTERPROFILE.xlsx')
hardcoded_excel = r'C:\Users\PEREIRT446445\OneDrive - cleanharbors.com\O365 Facilities Schedule - BL - WAP\MASTERPROFILE.xlsx'
MASTER_EXCEL_PATH = local_excel if os.path.exists(local_excel) else hardcoded_excel

# Multi-user sync tracker dictionary for scheduling and UI refreshes
SCHEDULE_UPDATES = {'GLOBAL': 0}

# Shared SocketIO instance for real-time WebSocket communication
socketio = SocketIO(cors_allowed_origins="*")

