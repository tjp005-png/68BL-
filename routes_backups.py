import os
import sqlite3
import time
import threading
from datetime import datetime
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, send_file, flash, current_app

from shared_state import DB_PATH, APP_DIR

backups_bp = Blueprint('backups_bp', __name__)

DEFAULT_PASSWORD = "CleanHarbors2026!"
I_DRIVE_DIR = os.environ.get("I_DRIVE_DIR", r"I:\Buttonwillow\LAB\Operations App")
i_drive_backup_dir = os.path.join(I_DRIVE_DIR, "backups")
drive_letter = os.path.splitdrive(I_DRIVE_DIR)[0] + "\\"

if os.path.exists(drive_letter):
    BACKUP_DIR = os.environ.get("BACKUP_DIR", i_drive_backup_dir)
else:
    BACKUP_DIR = os.environ.get("BACKUP_DIR", os.path.join(APP_DIR, "backups"))

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
I_DRIVE_UPLOADS_DIR = os.environ.get("I_DRIVE_UPLOADS_DIR", os.path.join(I_DRIVE_DIR, "uploads_backup"))

def sync_uploads_with_network():
    import shutil
    from shared_state import UPLOADS_DIR
    
    if not os.path.exists(UPLOADS_DIR):
        try:
            os.makedirs(UPLOADS_DIR, exist_ok=True)
        except Exception:
            pass

    synced_to_net = 0
    restored_from_net = 0
    
    drive_letter = os.path.splitdrive(I_DRIVE_UPLOADS_DIR)[0] + "\\"
    if not os.path.exists(drive_letter):
        return synced_to_net, restored_from_net
        
    try:
        os.makedirs(I_DRIVE_UPLOADS_DIR, exist_ok=True)
        
        # 1. Sync local uploads to network I: drive
        if os.path.exists(UPLOADS_DIR):
            for root, dirs, files in os.walk(UPLOADS_DIR):
                for f in files:
                    local_path = os.path.join(root, f)
                    rel_path = os.path.relpath(local_path, UPLOADS_DIR)
                    net_path = os.path.join(I_DRIVE_UPLOADS_DIR, rel_path)
                    
                    if not os.path.exists(net_path) or os.path.getmtime(local_path) > os.path.getmtime(net_path):
                        os.makedirs(os.path.dirname(net_path), exist_ok=True)
                        shutil.copy2(local_path, net_path)
                        synced_to_net += 1
                        
        # 2. Restore network uploads to local machine (if local is missing them)
        if os.path.exists(I_DRIVE_UPLOADS_DIR):
            for root, dirs, files in os.walk(I_DRIVE_UPLOADS_DIR):
                for f in files:
                    net_path = os.path.join(root, f)
                    rel_path = os.path.relpath(net_path, I_DRIVE_UPLOADS_DIR)
                    local_path = os.path.join(UPLOADS_DIR, rel_path)
                    
                    if not os.path.exists(local_path):
                        os.makedirs(os.path.dirname(local_path), exist_ok=True)
                        shutil.copy2(net_path, local_path)
                        restored_from_net += 1
    except Exception as e:
        print(f"Warning syncing uploads with I: drive: {e}")
        
    return synced_to_net, restored_from_net

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
                
        # Also sync profile attachment uploads to I: drive
        try:
            s_count, r_count = sync_uploads_with_network()
            if s_count > 0 or r_count > 0:
                secondary_msg += f" (Synced {s_count} uploads to network, restored {r_count})"
        except Exception as upload_sync_err:
            print(f"Uploads sync error: {upload_sync_err}")
            
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
    import sys
    # Prevent duplicate background threads in Flask debug auto-reloader mode
    # When running from source (not frozen), app.py forces socketio.run(debug=True)
    if not getattr(sys, 'frozen', False) and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return
    if getattr(app, '_background_scheduler_started', False):
        return
    app._background_scheduler_started = True

    def scheduler_loop():
        # Wait 5 seconds on startup to make sure system is fully initialized
        time.sleep(5)
        last_backup_time = 0
        last_digest_date = None
        while True:
            current_time = time.time()
            # Run hourly backup (every 3600 seconds)
            if current_time - last_backup_time >= 3600:
                with app.app_context():
                    try:
                        success, result = run_backup_logic()
                        if success:
                            app.logger.info(f"Automatic startup/periodic backup created: {result}")
                            last_backup_time = current_time
                        else:
                            app.logger.error(f"Automatic backup failed: {result}")
                    except Exception as e:
                        app.logger.error(f"Error running automatic backup: {e}")

            # Check for 4:45 PM (16:45) Daily LAS Summary Digest trigger
            with app.app_context():
                try:
                    now = datetime.now()
                    today_str = now.strftime('%Y-%m-%d')
                    if now.hour == 16 and now.minute >= 45 and last_digest_date != today_str:
                        from email_utils import generate_and_send_las_digest
                        generate_and_send_las_digest(target_date=today_str, recipient='pereira.taylor@cleanharbors.com, pruett.jacob@cleanharbors.com')
                        last_digest_date = today_str
                except Exception as digest_err:
                    app.logger.error(f"Error running 4:45PM LAS digest: {digest_err}")

            # Sleep for 5 minutes between checks
            time.sleep(300)
              
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

@backups_bp.route('/api/send_las_digest', methods=['POST', 'GET'])
def api_send_las_digest():
    target_date = request.args.get('date') or request.form.get('date')
    recipient = request.args.get('recipient') or request.form.get('recipient') or 'pereira.taylor@cleanharbors.com, pruett.jacob@cleanharbors.com'
    from email_utils import generate_and_send_las_digest
    res = generate_and_send_las_digest(target_date=target_date, recipient=recipient)
    if request.is_json or request.args.get('format') == 'json':
        return jsonify({'success': res, 'message': 'LAS summary digest sent.' if res else 'Failed or logged to fallback.'})
    
    msg_type = "success" if res else "info"
    msg_text = "LAS summary digest email sent successfully." if res else "LAS summary digest logged to fallback log (email_alerts.log)."
    flash(msg_text, msg_type)
    
    referrer = request.referrer
    if referrer and '/yellow_entry' in referrer:
        redirect_url = referrer
    elif target_date:
        redirect_url = url_for('chemist_bp.yellow_entry', date=target_date)
    else:
        redirect_url = request.referrer or url_for('backups_bp.backups_dashboard')
        
    return redirect(redirect_url)

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
