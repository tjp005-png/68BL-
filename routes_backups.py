import os
import sqlite3
import time
import threading
from datetime import datetime
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, send_file, flash, current_app

backups_bp = Blueprint('backups_bp', __name__)

DEFAULT_PASSWORD = "CleanHarbors2026!"
BACKUP_DIR = os.environ.get("BACKUP_DIR", "backups")
DB_PATH = 'database.db'

def get_admin_password():
    return os.environ.get("BACKUP_ADMIN_PASSWORD", DEFAULT_PASSWORD)

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('backups_logged_in'):
            return redirect(url_for('backups_bp.login', next=request.url))
        return f(*args, **kwargs)
    return decorated

SECONDARY_BACKUP_DIR = os.environ.get("SECONDARY_BACKUP_DIR", r"F:\Truck_Log_Backups")
I_DRIVE_DIR = os.environ.get("I_DRIVE_DIR", r"I:\Buttonwillow\LAB\Operations App")

def run_backup_logic():
    if not os.path.exists(DB_PATH):
        return False, "Database file not found."
        
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"database_backup_{timestamp}.db"
    dest_path = os.path.join(BACKUP_DIR, filename)
    
    secondary_msg = ""
    try:
        src = sqlite3.connect(DB_PATH)
        dest = sqlite3.connect(dest_path)
        with dest:
            src.backup(dest)
        dest.close()
        
        # Attempt secondary backup to thumbdrive
        if os.path.exists("F:\\"):
            try:
                os.makedirs(SECONDARY_BACKUP_DIR, exist_ok=True)
                thumb_dest_path = os.path.join(SECONDARY_BACKUP_DIR, filename)
                dest_thumb = sqlite3.connect(thumb_dest_path)
                with dest_thumb:
                    src.backup(dest_thumb)
                dest_thumb.close()
                secondary_msg += " (Also saved to F: drive)"
                
                # Maintain rolling window of 10 backups on thumbdrive
                all_thumb = sorted([f for f in os.listdir(SECONDARY_BACKUP_DIR) if f.startswith('database_backup_') and f.endswith('.db')])
                if len(all_thumb) > 10:
                    for old_file in all_thumb[:-10]:
                        try:
                            os.remove(os.path.join(SECONDARY_BACKUP_DIR, old_file))
                        except OSError:
                            pass
            except Exception as e:
                secondary_msg += f" (F: drive save failed: {e})"
                
        # Attempt I: drive sync (if the drive of I_DRIVE_DIR is mounted and we are not running directly from it)
        i_db_path = os.path.join(I_DRIVE_DIR, "database.db")
        drive_letter = os.path.splitdrive(I_DRIVE_DIR)[0] + "\\"
        if os.path.exists(drive_letter) and os.path.abspath(DB_PATH) != os.path.abspath(i_db_path):
            try:
                os.makedirs(I_DRIVE_DIR, exist_ok=True)
                
                # 1. Hot backup to I:\Buttonwillow\LAB\Inventory\database.db for startup sync
                dest_i_db = sqlite3.connect(i_db_path)
                with dest_i_db:
                    src.backup(dest_i_db)
                dest_i_db.close()
                
                # 2. Rolling timestamped backup in I:\Buttonwillow\LAB\Inventory\backups\
                i_backup_dir = os.path.join(I_DRIVE_DIR, "backups")
                os.makedirs(i_backup_dir, exist_ok=True)
                i_timestamped_path = os.path.join(i_backup_dir, filename)
                dest_i_timestamped = sqlite3.connect(i_timestamped_path)
                with dest_i_timestamped:
                    src.backup(dest_i_timestamped)
                dest_i_timestamped.close()
                
                secondary_msg += " (Also synced to I: drive)"
                
                # Maintain rolling window of 10 backups on I: drive
                all_i_backups = sorted([f for f in os.listdir(i_backup_dir) if f.startswith('database_backup_') and f.endswith('.db')])
                if len(all_i_backups) > 10:
                    for old_file in all_i_backups[:-10]:
                        try:
                            os.remove(os.path.join(i_backup_dir, old_file))
                        except OSError:
                            pass
            except Exception as e:
                secondary_msg += f" (I: drive sync failed: {e})"
            
        src.close()
        
        # Maintain rolling window of maximum 10 backups on primary
        all_files = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('database_backup_') and f.endswith('.db')])
        if len(all_files) > 10:
            for old_file in all_files[:-10]:
                try:
                    os.remove(os.path.join(BACKUP_DIR, old_file))
                except OSError:
                    pass
                    
        return True, filename + secondary_msg
    except Exception as e:
        return False, str(e)

def run_restore_logic(filename):
    src_path = os.path.join(BACKUP_DIR, filename)
    
    if not os.path.exists(src_path):
        return False, "Backup file not found."
        
    try:
        # SQLite online backup in reverse to restore hot
        src = sqlite3.connect(src_path)
        dest = sqlite3.connect(DB_PATH)
        with dest:
            src.backup(dest)
        dest.close()
        src.close()
        return True, "Database restored successfully."
    except Exception as e:
        return False, str(e)

def start_backup_scheduler(app):
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        def scheduler_loop():
            # Wait 10 seconds on startup to make sure system is fully initialized, then run initial backup
            time.sleep(10)
            while True:
                with app.app_context():
                    try:
                        success, result = run_backup_logic()
                        if success:
                            app.logger.info(f"Automatic startup/periodic backup created: {result}")
                        else:
                            app.logger.error(f"Automatic backup failed: {result}")
                    except Exception as e:
                        app.logger.error(f"Error running automatic backup: {e}")
                # Sleep for 24 hours
                time.sleep(24 * 60 * 60)
                  
        t = threading.Thread(target=scheduler_loop, daemon=True)
        t.start()

@backups_bp.route('/backups/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == get_admin_password():
            session['backups_logged_in'] = True
            next_url = request.args.get('next') or url_for('backups_bp.backups_dashboard')
            return redirect(next_url)
        else:
            flash("Incorrect admin password. Please try again.", "danger")
    return render_template('backups_login.html')

@backups_bp.route('/backups/logout', methods=['POST'])
@require_auth
def logout():
    session.pop('backups_logged_in', None)
    flash("You have been logged out of the backups manager.", "info")
    return redirect(url_for('backups_bp.login'))

@backups_bp.route('/backups')
@require_auth
def backups_dashboard():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    files = []
    total_size = 0
    backup_files = sorted(
        [f for f in os.listdir(BACKUP_DIR) if f.startswith('database_backup_') and f.endswith('.db')],
        reverse=True
    )
    
    for f in backup_files:
        path = os.path.join(BACKUP_DIR, f)
        size_bytes = os.path.getsize(path)
        total_size += size_bytes
        
        try:
            ts_str = f.replace('database_backup_', '').replace('.db', '')
            dt = datetime.strptime(ts_str, '%Y%m%d_%H%M%S')
            formatted_date = dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            formatted_date = datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M:%S')
            
        files.append({
            'filename': f,
            'size': f"{size_bytes / 1024:.1f} KB",
            'date': formatted_date
        })
        
    last_backup_time = files[0]['date'] if files else "Never"
    
    return render_template(
        'backups.html',
        backups=files,
        total_backups=len(files),
        total_size=f"{total_size / 1024:.1f} KB",
        backup_dir=os.path.abspath(BACKUP_DIR),
        last_backup_time=last_backup_time
    )

@backups_bp.route('/backups/create', methods=['POST'])
@require_auth
def create_backup():
    success, result = run_backup_logic()
    if success:
        flash(f"Hot backup created successfully: {result}", "success")
    else:
        flash(f"Failed to create database backup: {result}", "danger")
    return redirect(url_for('backups_bp.backups_dashboard'))

@backups_bp.route('/backups/download/<filename>')
@require_auth
def download_backup(filename):
    if '/' in filename or '\\' in filename or not filename.endswith('.db'):
        return "Invalid filename", 400
        
    path = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(path):
        return "File not found", 404
        
    return send_file(path, as_attachment=True, download_name=filename)

@backups_bp.route('/backups/restore/<filename>', methods=['POST'])
@require_auth
def restore_backup(filename):
    if '/' in filename or '\\' in filename or not filename.endswith('.db'):
        flash("Invalid backup file.", "danger")
        return redirect(url_for('backups_bp.backups_dashboard'))
        
    success, message = run_restore_logic(filename)
    if success:
        flash(message, "success")
    else:
        flash(message, "danger")
    return redirect(url_for('backups_bp.backups_dashboard'))

@backups_bp.route('/backups/delete/<filename>', methods=['POST'])
@require_auth
def delete_backup(filename):
    if '/' in filename or '\\' in filename or not filename.endswith('.db'):
        flash("Invalid backup file.", "danger")
        return redirect(url_for('backups_bp.backups_dashboard'))
        
    path = os.path.join(BACKUP_DIR, filename)
    if os.path.exists(path):
        try:
            os.remove(path)
            flash(f"Backup {filename} deleted successfully.", "success")
        except OSError as e:
            flash(f"Failed to delete backup file: {e}", "danger")
    else:
        flash("Backup file not found.", "danger")
        
    return redirect(url_for('backups_bp.backups_dashboard'))
