print("[DEBUG] 1. Importing python modules...")
import sys
import sqlite3
from datetime import date
from contextlib import closing
from flask import Flask, jsonify, render_template, request
print("[DEBUG] 2. Python standard/Flask modules imported.")

# Import Blueprints
print("[DEBUG] 2.1 Importing routes_receiving...")
from routes_receiving import receiving_bp
print("[DEBUG] 2.2 Importing routes_chemist...")
from routes_chemist import chemist_bp
print("[DEBUG] 2.3 Importing routes_reports...")
from routes_reports import reports_bp
print("[DEBUG] 2.4 Importing routes_schedule...")
from routes_schedule import schedule_bp
print("[DEBUG] 2.5 Importing routes_stu...")
from routes_stu import stu_bp
print("[DEBUG] 2.6 Importing routes_approvals...")
from routes_approvals import approvals_bp
print("[DEBUG] 2.7 Importing shared_state...")
from shared_state import socketio
import sys
import os

print("[DEBUG] 2.8 Importing routes_backups...")
from routes_backups import backups_bp, start_backup_scheduler
print("[DEBUG] 2.9 All imports complete.")

if getattr(sys, 'frozen', False):
    template_folder = os.path.join(sys._MEIPASS, 'templates')
    static_folder = os.path.join(sys._MEIPASS, 'static')
    app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
else:
    app = Flask(__name__)

app.secret_key = 'clh-secret-session-key-2026'
app.config['TEMPLATES_AUTO_RELOAD'] = True
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
PERMITTED_CODES = {'CBP', 'CNO', 'CBPS', 'CNOS', 'CNIA', 'CCS', 'CCSS', 'D23', 'D80L', 'LLF'}

from shared_state import socketio, DB_PATH

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row 
    
    # Check if database is on a network drive to avoid WAL mode locks/hangs
    is_network = False
    if DB_PATH.startswith(r'\\'):
        is_network = True
    elif os.name == 'nt':
        try:
            import ctypes
            drive = os.path.splitdrive(os.path.abspath(DB_PATH))[0]
            if drive:
                is_network = (ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4)
        except:
            pass
            
    if is_network:
        conn.execute('PRAGMA journal_mode=DELETE;')
    else:
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
        if not column_exists(cursor, 'truck_logs', 'shipping_mode'): cursor.execute("ALTER TABLE truck_logs ADD COLUMN shipping_mode TEXT DEFAULT 'Solid'")
        if not column_exists(cursor, 'truck_logs', 'job_type'): cursor.execute("ALTER TABLE truck_logs ADD COLUMN job_type TEXT DEFAULT 'Standard'")
        if not column_exists(cursor, 'truck_logs', 'container_type'): cursor.execute("ALTER TABLE truck_logs ADD COLUMN container_type TEXT DEFAULT 'End Dump'")

        # 2. MASTER PROFILES 
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS profiles (
                profile_number TEXT PRIMARY KEY, generator TEXT, 
                waste_description TEXT, win_code TEXT, voc_percentage REAL DEFAULT 0.0,
                special_handling TEXT, last_synced_mtime REAL
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
        if not column_exists(cursor, 'profiles', 'last_synced_mtime'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN last_synced_mtime REAL')
        if not column_exists(cursor, 'profiles', 'epa_id'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN epa_id TEXT')
        if not column_exists(cursor, 'profiles', 'lab_number'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN lab_number TEXT')
        if not column_exists(cursor, 'profiles', 'haz'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN haz TEXT')
        if not column_exists(cursor, 'profiles', 'rcra'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN rcra TEXT')
        if not column_exists(cursor, 'profiles', 'comments'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN comments TEXT')
        if not column_exists(cursor, 'profiles', 'ldr_required'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN ldr_required TEXT DEFAULT "No"')
        if not column_exists(cursor, 'profiles', 'state_waste_code'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN state_waste_code TEXT')
        if not column_exists(cursor, 'profiles', 'federal_waste_code'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN federal_waste_code TEXT')
        if not column_exists(cursor, 'profiles', 'dot_description'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN dot_description TEXT')
        if not column_exists(cursor, 'profiles', 'cyanide'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN cyanide TEXT DEFAULT "No"')
        if not column_exists(cursor, 'profiles', 'sulfide'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN sulfide TEXT DEFAULT "No"')
        if not column_exists(cursor, 'profiles', 'free_liquids'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN free_liquids TEXT DEFAULT "No"')
        if not column_exists(cursor, 'profiles', 'color'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN color TEXT')
        if not column_exists(cursor, 'profiles', 'treatment_recipe'):
            cursor.execute('ALTER TABLE profiles ADD COLUMN treatment_recipe TEXT')

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
        if not column_exists(cursor, 'drum_inventory', 'status'): 
            cursor.execute("ALTER TABLE drum_inventory ADD COLUMN status TEXT DEFAULT 'FINAL CODED'")
        else:
            # Fix legacy PENDING status values to FINAL CODED
            cursor.execute("UPDATE drum_inventory SET status = 'FINAL CODED' WHERE status = 'PENDING'")
        if not column_exists(cursor, 'drum_inventory', 'location'): cursor.execute('ALTER TABLE drum_inventory ADD COLUMN location TEXT')
        if not column_exists(cursor, 'drum_inventory', 'reject_notes'): cursor.execute('ALTER TABLE drum_inventory ADD COLUMN reject_notes TEXT')
        if not column_exists(cursor, 'drum_inventory', 'outgoing_manifest'): cursor.execute('ALTER TABLE drum_inventory ADD COLUMN outgoing_manifest TEXT')
        if not column_exists(cursor, 'drum_inventory', 'last_scan_date'): cursor.execute('ALTER TABLE drum_inventory ADD COLUMN last_scan_date TEXT')

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
        if not column_exists(cursor, 'profile_wvi', 'is_synced'):
            cursor.execute('ALTER TABLE profile_wvi ADD COLUMN is_synced INTEGER DEFAULT 0')
        if not column_exists(cursor, 'profile_wvi', 'color'):
            cursor.execute('ALTER TABLE profile_wvi ADD COLUMN color TEXT')
            
        # 8. WASTE ACCEPTANCE ACTIVE REVIEWS LOG
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS waste_acceptance_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_number TEXT UNIQUE,
                status TEXT DEFAULT 'Needs Review',
                assigned_to TEXT,
                notes TEXT,
                date_added DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        if not column_exists(cursor, 'waste_acceptance_log', 'generator_requestor'):
            cursor.execute('ALTER TABLE waste_acceptance_log ADD COLUMN generator_requestor TEXT')
        if not column_exists(cursor, 'waste_acceptance_log', 'is_archived'):
            cursor.execute('ALTER TABLE waste_acceptance_log ADD COLUMN is_archived INTEGER DEFAULT 0')
        if not column_exists(cursor, 'waste_acceptance_log', 'date_added'):
            cursor.execute('ALTER TABLE waste_acceptance_log ADD COLUMN date_added DATETIME')
            cursor.execute('UPDATE waste_acceptance_log SET date_added = last_updated WHERE date_added IS NULL')
        if not column_exists(cursor, 'waste_acceptance_log', 'expiration_date'):
            cursor.execute('ALTER TABLE waste_acceptance_log ADD COLUMN expiration_date TEXT')
        if not column_exists(cursor, 'waste_acceptance_log', 'cp1_lab_number'):
            cursor.execute('ALTER TABLE waste_acceptance_log ADD COLUMN cp1_lab_number TEXT')
        
        # Add performance indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_profiles_win_code ON profiles (win_code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_truck_logs_profile_number ON truck_logs (profile_number)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_truck_logs_date_received ON truck_logs (date_received)")
        
        conn.commit()

print("[DEBUG] 3. Running upgrade_db()...")
upgrade_db()
print("[DEBUG] 4. upgrade_db() completed successfully.")

# ==========================================
#        ROUTES (PORTAL / HUB)
# ==========================================

@app.route('/')
def portal_hub(): 
    with closing(get_db_connection()) as conn:
        logistics_count = conn.execute("SELECT COUNT(*) FROM truck_logs WHERE exit_weight IS NULL AND test_status != 'REJECTED'").fetchone()[0]
        drums_count = conn.execute("SELECT COUNT(*) FROM drum_inventory WHERE process_type = 'PENDING SAMPLING'").fetchone()[0]
        
        lab_bulk = conn.execute("SELECT COUNT(*) FROM truck_logs WHERE test_status = 'WEIGHED IN' AND test_assigned NOT LIKE 'LAS%'").fetchone()[0]
        lab_drums = conn.execute("SELECT COUNT(*) FROM drum_lab_queue WHERE status = 'PENDING'").fetchone()[0]
        lab_total = lab_bulk + lab_drums
        
        las_count = conn.execute("SELECT COUNT(*) FROM truck_logs WHERE test_assigned LIKE 'LAS%' AND test_status = 'WEIGHED IN'").fetchone()[0]
        drum_jobs_count = conn.execute("SELECT COUNT(DISTINCT job_id) FROM drum_lab_queue WHERE status != 'FINAL CODED' AND job_id IS NOT NULL").fetchone()[0]
        approvals_total = las_count + drum_jobs_count
        
    return render_template('index.html', 
                           logistics_count=logistics_count, 
                           drums_count=drums_count, 
                           lab_total=lab_total, 
                           approvals_total=approvals_total,
                           las_count=las_count,
                           drum_jobs_count=drum_jobs_count)

@app.route('/tonnage')
def tonnage_dashboard():
    return render_template('tonnage_dashboard.html')

@app.route('/api/tonnage/schedule-impact')
def tonnage_schedule_impact():
    try:
        forecast_year = int(request.args.get('year', date.today().year))
    except (TypeError, ValueError):
        forecast_year = date.today().year

    start_date = max(date.today(), date(forecast_year, 1, 1))
    end_date = date(forecast_year, 12, 31)
    if start_date > end_date:
        return jsonify({
            'year': forecast_year,
            'as_of': date.today().isoformat(),
            'scheduled_loads': 0,
            'estimated_tons': 0,
            'matched_loads': 0,
            'fallback_loads': 0,
            'excluded_unit31_loads': 0,
            'monthly': []
        })

    with closing(get_db_connection()) as conn:
        facility_average_row = conn.execute('''
            SELECT AVG(net_weight) AS avg_tons
            FROM truck_logs
            WHERE exit_weight IS NOT NULL
              AND net_weight > 0
              AND test_status != 'REJECTED'
              AND COALESCE(cell_location, '') NOT LIKE '31%'
        ''').fetchone()
        facility_average = float(facility_average_row['avg_tons'] or 20.0)

        profile_averages = {
            str(row['profile_number'] or '').strip().upper(): {
                'avg_tons': float(row['avg_tons']),
                'sample_size': int(row['sample_size'])
            }
            for row in conn.execute('''
                SELECT
                    TRIM(UPPER(profile_number)) AS profile_number,
                    AVG(net_weight) AS avg_tons,
                    COUNT(*) AS sample_size
                FROM truck_logs
                WHERE exit_weight IS NOT NULL
                  AND net_weight > 0
                  AND test_status != 'REJECTED'
                  AND COALESCE(cell_location, '') NOT LIKE '31%'
                GROUP BY TRIM(UPPER(profile_number))
            ''').fetchall()
        }

        schedule_rows = conn.execute('''
            SELECT schedule_date, profile_number, routing_code, SUM(COALESCE(load_count, 1)) AS load_count
            FROM daily_schedule
            WHERE schedule_date BETWEEN ? AND ?
            GROUP BY schedule_date, TRIM(UPPER(profile_number)), TRIM(UPPER(routing_code))
            ORDER BY schedule_date
        ''', (start_date.isoformat(), end_date.isoformat())).fetchall()

    monthly = {}
    scheduled_loads = 0
    estimated_tons = 0.0
    matched_loads = 0
    fallback_loads = 0
    excluded_unit31_loads = 0

    for row in schedule_rows:
        loads = max(0, int(row['load_count'] or 0))
        profile_number = str(row['profile_number'] or '').strip().upper()
        routing_code = str(row['routing_code'] or '').strip().upper()
        if 'CNOS' in profile_number or 'CNOS' in routing_code:
            excluded_unit31_loads += loads
            continue

        profile_average = profile_averages.get(profile_number)
        if profile_average and profile_average['sample_size'] >= 3:
            tons_per_load = profile_average['avg_tons']
            matched_loads += loads
        else:
            tons_per_load = facility_average
            fallback_loads += loads

        row_tons = loads * tons_per_load
        month_key = row['schedule_date'][:7]
        month = monthly.setdefault(month_key, {'month': month_key, 'loads': 0, 'estimated_tons': 0.0})
        month['loads'] += loads
        month['estimated_tons'] += row_tons
        scheduled_loads += loads
        estimated_tons += row_tons

    for month in monthly.values():
        month['estimated_tons'] = round(month['estimated_tons'], 1)

    return jsonify({
        'year': forecast_year,
        'as_of': date.today().isoformat(),
        'through': end_date.isoformat(),
        'scheduled_loads': scheduled_loads,
        'estimated_tons': round(estimated_tons, 1),
        'matched_loads': matched_loads,
        'fallback_loads': fallback_loads,
        'excluded_unit31_loads': excluded_unit31_loads,
        'facility_average_tons_per_load': round(facility_average, 2),
        'monthly': list(monthly.values())
    })

if __name__ == '__main__':
    # -------------------------------------------------------------
    # STARTUP SYNC: Pull latest database from I: drive if newer
    # -------------------------------------------------------------
    try:
        import shutil
        import time
        from shared_state import DB_PATH
        
        i_drive_dir = os.environ.get("I_DRIVE_DIR", r"I:\Buttonwillow\LAB\Operations App")
        i_db_path = os.path.join(i_drive_dir, "database.db")
        drive_letter = os.path.splitdrive(i_drive_dir)[0] + "\\"
        
        if os.path.exists(drive_letter) and os.path.exists(i_db_path):
            if os.path.abspath(DB_PATH) != os.path.abspath(i_db_path):
                network_mtime = os.path.getmtime(i_db_path)
                local_exists = os.path.exists(DB_PATH)
                local_mtime = os.path.getmtime(DB_PATH) if local_exists else 0
                
                if network_mtime > local_mtime:
                    print("\n" + "="*70)
                    print("  [DATABASE SYNC] A newer database was found on the network I: drive!")
                    print(f"  Network version: {time.ctime(network_mtime)}")
                    print(f"  Local version:   {time.ctime(local_mtime) if local_exists else 'None (New Install)'}")
                    print("="*70)
                    
                    # Auto-sync on new install (local_exists is False) or when running non-interactively
                    is_interactive = sys.stdin and sys.stdin.isatty()
                    
                    choice = "n"
                    if not local_exists:
                        choice = "y"
                        print("  [DATABASE SYNC] New installation detected. Auto-syncing from network...")
                    elif not is_interactive:
                        choice = "y"
                        print("  [DATABASE SYNC] Non-interactive environment. Auto-syncing newer network database...")
                    else:
                        try:
                            choice = input("  Would you like to sync the latest database from I: drive locally? (y/n) [default: n]: ").strip().lower()
                        except (KeyboardInterrupt, EOFError):
                            choice = "n"
                        
                    if choice in ['y', 'yes']:
                        print("  Syncing database from I: drive... please wait...")
                        if local_exists:
                            try:
                                shutil.copy2(DB_PATH, DB_PATH + ".bak")
                            except Exception as backup_err:
                                print(f"  Warning: Could not create local database backup: {backup_err}")
                        try:
                            shutil.copy2(i_db_path, DB_PATH)
                            print("  Sync complete! Running database upgrades...")
                            upgrade_db()
                        except Exception as copy_err:
                            print(f"  Error copying database from network: {copy_err}")
                    else:
                        print("  Sync skipped. Proceeding with local database.")
    except Exception as e:
        print(f"  [DATABASE SYNC] Sync check failed: {e}")

    print("[DEBUG] 5. Starting backup scheduler...")
    try:
        from routes_backups import sync_uploads_with_network
        s_count, r_count = sync_uploads_with_network()
        if s_count > 0 or r_count > 0:
            print(f"  [UPLOADS SYNC] Network uploads sync complete: {s_count} uploaded to network, {r_count} restored to local machine.")
    except Exception as uploads_sync_err:
        print(f"  [UPLOADS SYNC] Startup uploads sync check warning: {uploads_sync_err}")

    start_backup_scheduler(app)
    print("[DEBUG] 6. Backup scheduler initialized.")
    
    # Dev runs on 5002, Live/Production runs on 5000
    port = 5002 if 'Truck_Log_App_Dev' in os.getcwd() else 5000
    
    if getattr(sys, 'frozen', False):
        import logging
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)
        print("=========================================================")
        print("  Truck Log Production Server is now Running!")
        print(f"  Please navigate to: http://localhost:{port} in your browser")
        print("  Keep this window open. Press Ctrl+C to stop the server.")
        print("=========================================================")
        
    print("[DEBUG] 7. Starting socketio.run()...")
    if getattr(sys, 'frozen', False):
        socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
    else:
        socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True, debug=True)


