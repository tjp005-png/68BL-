# shared_state.py
import time
from flask_socketio import SocketIO

# Multi-user sync tracker dictionary for scheduling and UI refreshes
SCHEDULE_UPDATES = {'GLOBAL': 0}

# Shared SocketIO instance for real-time WebSocket communication
socketio = SocketIO(cors_allowed_origins="*")

