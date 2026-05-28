import os
import sqlite3
import time
import threading
from datetime import datetime
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, send_file, flash, current_app

backups_bp = Blueprint('backups_bp', __name__)

DEFAULT_PASSWORD = "CleanHarbors2026!"
BACKUP_DIR = 'backups'
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

def run_backup_logic():
    if not os.path.exists(DB_PATH):
        return False, "Database file not found."
        
    os.makedirs(BACKUP_DIR, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"database_backup_{timestamp}.db"
    dest_path = os.path.join(BACKUP_DIR, filename)
    
    try:
        src = sqlite3.connect(DB_PATH)
        dest = sqlite3.connect(dest_path)
        with dest:
            src.backup(dest)
        dest.close()
        src.close()
        
        # Maintain rolling window of maximum 10 backups
        all_files = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('database_backup_') and f.endswith('.db')])
        if len(all_files) > 10:
            for old_file in all_files[:-10]:
                try:
                    os.remove(os.path.join(BACKUP_DIR, old_file))
                except OSError:
                    pass
                    
        return True, filename
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
