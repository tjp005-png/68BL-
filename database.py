# database.py
import sqlite3

def get_db_connection():
    conn = sqlite3.connect('database.db', timeout=15)
    conn.row_factory = sqlite3.Row 
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn