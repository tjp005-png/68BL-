import sqlite3
from datetime import date
from contextlib import closing
from flask import Flask, render_template

# Import Blueprints
from routes_receiving import receiving_bp
from routes_chemist import chemist_bp
from routes_reports import reports_bp
from routes_schedule import schedule_bp
from routes_stu import stu_bp
from routes_approvals import approvals_bp
from shared_state import socketio
from routes_backups import backups_bp, start_backup_scheduler

app = Flask(__name__)
app.secret_key = 'clh-secret-session-key-2026'
socketio.init_app(app)


# Register Blueprints
app.register_blueprint(receiving_bp)
app.register_blueprint(chemist_bp)
app.register_blueprint(reports_bp)
app.register_blueprint(schedule_bp)
app.register_blueprint(stu_bp)
app.register_blueprint(approvals_bp)
app.register_blueprint(backups_bp)


# ==========================================
#        CONFIGURATION & HELPER VARS
# ==========================================
TARGET_ROUTING_CODE = "BL"
PERMITTED_CODES = {'CBP', 'CNO', 'CBPS', 'CNOS', 'CNIA', 'CCS', 'CCSS', 'D23', 'D80L'}

def get_db_connection():
    conn = sqlite3.connect('database.db', timeout=15)
    conn.row_factory = sqlite3.Row 
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn

def column_exists(cursor, table_name, column_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [row['name'] for row in cursor.fetchall()]
    return column_name in columns

def upgrade_db():
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        
        # 1. TRUCK LOGS
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS truck_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                truck_id TEXT, profile_number TEXT, manifest_number TEXT, load_number TEXT,
                gross_weight REAL, test_assigned TEXT, test_status TEXT, exit_weight REAL,
                net_weight REAL, cell_location TEXT, grid_location TEXT
            )
        ''')
        if not column_exists(cursor, 'truck_logs', 'lab_results'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN lab_results TEXT')
        if not column_exists(cursor, 'truck_logs', 'manifest_weight'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN manifest_weight REAL')
        if not column_exists(cursor, 'truck_logs', 'manifest_units'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN manifest_units TEXT')
        if not column_exists(cursor, 'truck_logs', 'date_received'):
            cursor.execute('ALTER TABLE truck_logs ADD COLUMN date_received TEXT')
            cursor.execute("UPDATE truck_logs SET date_received = ? WHERE date_received IS NULL", (date.today().isoformat(),))
        if not column_exists(cursor, 'truck_logs', 'rejection_reason'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN rejection_reason TEXT')
        if not column_exists(cursor, 'truck_logs', 'extra_fees'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN extra_fees TEXT DEFAULT "None"')
        if not column_exists(cursor, 'truck_logs', 'sales_order'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN sales_order TEXT DEFAULT ""')
        if not column_exists(cursor, 'truck_logs', 'time_in'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN time_in TEXT DEFAULT ""')
        if not column_exists(cursor, 'truck_logs', 'time_out'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN time_out TEXT DEFAULT ""')
        if not column_exists(cursor, 'truck_logs', 'specific_gravity'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN specific_gravity REAL')
        if not column_exists(cursor, 'truck_logs', 'measured_ph'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN measured_ph REAL')
        if not column_exists(cursor, 'truck_logs', 'measured_flashpoint'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN measured_flashpoint TEXT')
        if not column_exists(cursor, 'truck_logs', 'measured_sulfides'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN measured_sulfides TEXT')
        if not column_exists(cursor, 'truck_logs', 'measured_cyanide'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN measured_cyanide TEXT')
        if not column_exists(cursor, 'truck_logs', 'measured_free_liquids'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN measured_free_liquids TEXT')
        if not column_exists(cursor, 'truck_logs', 'measured_voc'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN measured_voc REAL')
        if not column_exists(cursor, 'truck_logs', 'voc_pass_fail'): cursor.execute('ALTER TABLE truck_logs ADD COLUMN voc_pass_fail TEXT DEFAULT "N/A"')

        # 2. MASTER PROFILES 
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS profiles (
                profile_number TEXT PRIMARY KEY, generator TEXT, 
                waste_description TEXT, win_code TEXT, voc_percentage REAL DEFAULT 0.0,
                special_handling TEXT
            )
        ''')
        if not column_exists(cursor, 'profiles', 'generator'): cursor.execute('ALTER TABLE profiles ADD COLUMN generator TEXT')
        if not column_exists(cursor, 'profiles', 'waste_description'): cursor.execute('ALTER TABLE profiles ADD COLUMN waste_description TEXT')
        if not column_exists(cursor, 'profiles', 'win_code'): cursor.execute('ALTER TABLE profiles ADD COLUMN win_code TEXT')
        if not column_exists(cursor, 'profiles', 'special_handling'): cursor.execute('ALTER TABLE profiles ADD COLUMN special_handling TEXT')
        if not column_exists(cursor, 'profiles', 'ph_range'): cursor.execute('ALTER TABLE profiles ADD COLUMN ph_range TEXT')
        if not column_exists(cursor, 'profiles', 'physical_appearance'): cursor.execute('ALTER TABLE profiles ADD COLUMN physical_appearance TEXT')
        if not column_exists(cursor, 'profiles', 'flash_point'): cursor.execute('ALTER TABLE profiles ADD COLUMN flash_point TEXT')
        if not column_exists(cursor, 'profiles', 'expiration_date'): cursor.execute('ALTER TABLE profiles ADD COLUMN expiration_date TEXT')
        if not column_exists(cursor, 'profiles', 'expiration_date'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN expiration_date TEXT')
        if not column_exists(cursor, 'profiles', 'status'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN status TEXT DEFAULT "A"')

        # 3. DAILY SCHEDULE 
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_date TEXT, start_time TEXT,
                end_time TEXT, profile_number TEXT, load_count INTEGER, generator TEXT,
                waste_type TEXT, sales_order TEXT, routing_code TEXT, scheduler_initials TEXT, special_notes TEXT
            )
        ''')
        if not column_exists(cursor, 'daily_schedule', 'voc_level'): cursor.execute('ALTER TABLE daily_schedule ADD COLUMN voc_level REAL')
        if not column_exists(cursor, 'daily_schedule', 'is_unscheduled'): cursor.execute('ALTER TABLE daily_schedule ADD COLUMN is_unscheduled INTEGER DEFAULT 0')
        if not column_exists(cursor, 'daily_schedule', 'order_index'): cursor.execute('ALTER TABLE daily_schedule ADD COLUMN order_index INTEGER DEFAULT 0')
        if not column_exists(cursor, 'daily_schedule', 'series_id'): cursor.execute('ALTER TABLE daily_schedule ADD COLUMN series_id TEXT')
        if not column_exists(cursor, 'daily_schedule', 'is_pinned'): cursor.execute('ALTER TABLE daily_schedule ADD COLUMN is_pinned INTEGER DEFAULT 0')
        if not column_exists(cursor, 'daily_schedule', 'order_index'):
            cursor.execute('ALTER TABLE daily_schedule ADD COLUMN order_index INTEGER DEFAULT 0')
        if not column_exists(cursor, 'daily_schedule', 'is_pinned'):
            cursor.execute('ALTER TABLE daily_schedule ADD COLUMN is_pinned INTEGER DEFAULT 0')

        # 4. CREATE STU DRUM INVENTORY TABLE
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS drum_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_no TEXT,
                inb_prof TEXT,
                process_type TEXT,
                weight REAL,
                ph REAL,
                age REAL,
                voc_ppm REAL,
                voc_weight REAL,
                import_date TEXT
            )
        ''')
        if not column_exists(cursor, 'drum_inventory', 'manifest'): cursor.execute('ALTER TABLE drum_inventory ADD COLUMN manifest TEXT')
        if not column_exists(cursor, 'drum_inventory', 'job_id'): cursor.execute('ALTER TABLE drum_inventory ADD COLUMN job_id TEXT')
        if not column_exists(cursor, 'drum_inventory', 'status'): cursor.execute("ALTER TABLE drum_inventory ADD COLUMN status TEXT DEFAULT 'PENDING'")

        # 5. DRUM SAMPLING COMPLIANCE TABLES
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS compliance_tracker (
                profile TEXT PRIMARY KEY,
                last_voc_test_date DATE,
                drums_since_last_test INTEGER
            );
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audit_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME,
                profile TEXT,
                drum_id TEXT,
                manifest TEXT,
                trigger_reason TEXT,
                chemist_name TEXT,
                voc_result REAL
            );
        ''')

        # 6. DRUM LAB QUEUE
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS drum_lab_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT, drum_id TEXT, profile TEXT, manifest TEXT,
                tests_required TEXT, status TEXT DEFAULT 'PENDING',
                fp_result TEXT, ph_result REAL, voc_result REAL, notes TEXT
            )
        ''')
        if not column_exists(cursor, 'drum_lab_queue', 'flashpoint'): cursor.execute('ALTER TABLE drum_lab_queue ADD COLUMN flashpoint TEXT')
        if not column_exists(cursor, 'drum_lab_queue', 'cyanide'): cursor.execute('ALTER TABLE drum_lab_queue ADD COLUMN cyanide TEXT')
        if not column_exists(cursor, 'drum_lab_queue', 'sulfide'): cursor.execute('ALTER TABLE drum_lab_queue ADD COLUMN sulfide TEXT')
        if not column_exists(cursor, 'drum_lab_queue', 'oxidation'): cursor.execute('ALTER TABLE drum_lab_queue ADD COLUMN oxidation TEXT')
        if not column_exists(cursor, 'drum_lab_queue', 'coded_in_win'): cursor.execute('ALTER TABLE drum_lab_queue ADD COLUMN coded_in_win INTEGER DEFAULT 0')
        
        # 7. WVI PROFILE CACHE (Prevent JOIN crashes)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS profile_wvi (
                profile TEXT PRIMARY KEY,
                filename TEXT,
                generator_name TEXT,
                waste_name TEXT,
                physical_description TEXT,
                ldr TEXT,
                state_waste_codes TEXT,
                federal_waste_codes TEXT,
                dot_description TEXT,
                handling_instruction TEXT,
                sample_procedures TEXT,
                verification_procedures TEXT,
                ph_min REAL,
                ph_max REAL,
                sulfides TEXT,
                cyanide TEXT,
                free_liquids TEXT,
                flashpoint TEXT,
                unloading_instructions TEXT,
                reactivity_codes TEXT,
                approved_date TEXT,
                expiration_date TEXT,
                lab_num TEXT,
                voc_ppm REAL,
                treatment_information TEXT,
                notes_revisions TEXT,
                is_synced INTEGER DEFAULT 0
            )
        ''')
        
        conn.commit()

upgrade_db()

# ==========================================
#        ROUTES (PORTAL / HUB)
# ==========================================

@app.route('/')
def portal_hub(): 
    return render_template('index.html')

if __name__ == '__main__':
    start_backup_scheduler(app)
    socketio.run(app, host='0.0.0.0', port=5002, debug=True)


