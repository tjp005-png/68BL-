from flask import Blueprint, render_template, request, redirect, url_for, jsonify, send_from_directory
import os
from werkzeug.utils import secure_filename
from datetime import date
from contextlib import closing
import pdfplumber
import re
import time
from database import get_db_connection
from shared_state import SCHEDULE_UPDATES

approvals_bp = Blueprint('approvals_bp', __name__)

@approvals_bp.route('/approvals')
def approvals_portal():
    return render_template('approvals.html')

@approvals_bp.route('/waste_acceptance')
def waste_acceptance():
    return redirect(url_for('stu_bp.stu_hub', view='pipeline'))

@approvals_bp.route('/waste_acceptance/checklist/<job_id>')
def waste_acceptance_checklist(job_id):
    with closing(get_db_connection()) as conn:
        # Fetch all labs for this job_id
        labs = conn.execute('''
            SELECT * FROM drum_lab_queue 
            WHERE job_id = ?
        ''', (job_id,)).fetchall()
        
        # Fetch all related physical drums in drum_inventory for this job
        related_drums = conn.execute('''
            SELECT * FROM drum_inventory 
            WHERE job_id = ? AND process_type = 'PENDING SAMPLING'
        ''', (job_id,)).fetchall()
        
    labs_list = [dict(l) for l in labs]
    drums_list = [dict(d) for d in related_drums]
    
    # Associate physical drums with their representing lab queue sample
    associated_track_nos = set()
    for lab in labs_list:
        lab_manifest = str(lab['manifest']).strip().upper()
        lab_profile = str(lab['profile']).strip().upper()
        
        batch_drums = []
        for drum in drums_list:
            drum_manifest = str(drum['manifest']).strip().upper() if drum.get('manifest') else ""
            drum_profile = str(drum['inb_prof']).strip().upper() if drum.get('inb_prof') else ""
            
            if drum_manifest == lab_manifest and drum_profile == lab_profile:
                batch_drums.append(drum)
                associated_track_nos.add(drum['track_no'])
                
        lab['batch_drums'] = batch_drums
        
    # Filter out unsampled drums (e.g., asbestos or other low-risk profiles)
    unsampled_drums = [d for d in drums_list if d['track_no'] not in associated_track_nos]
        
    return render_template('waste_acceptance.html', 
                           job_id=job_id, 
                           labs=labs_list, 
                           related_drums=drums_list,
                           unsampled_drums=unsampled_drums)

@approvals_bp.route('/waste_acceptance/mark_coded', methods=['POST'])
def waste_acceptance_mark_coded():
    data = request.get_json() or {}
    lab_id = data.get('lab_id')
    coded = data.get('coded') # 1 or 0
    
    if lab_id is None or coded is None:
        return jsonify({'error': 'Missing lab_id or coded state'}), 400
        
    with closing(get_db_connection()) as conn:
        conn.execute('''
            UPDATE drum_lab_queue 
            SET coded_in_win = ? 
            WHERE id = ?
        ''', (int(coded), lab_id))
        
        # Fetch job_id to emit websocket update
        lab_row = conn.execute('SELECT job_id FROM drum_lab_queue WHERE id = ?', (lab_id,)).fetchone()
        job_id = lab_row['job_id'] if lab_row else None
        
        conn.commit()
        
        if job_id:
            from shared_state import socketio
            socketio.emit('drum_update', {'job_id': job_id})
            
    return jsonify({'success': True})

@approvals_bp.route('/waste_acceptance/finalize_load', methods=['POST'])
def waste_acceptance_finalize_load():
    job_id = request.form.get('job_id')
    if not job_id:
        return "Missing Load Number (job_id)", 400
        
    with closing(get_db_connection()) as conn:
        # Get all completed labs for this job_id
        labs = conn.execute('''
            SELECT * FROM drum_lab_queue 
            WHERE job_id = ? AND status = 'COMPLETED'
        ''', (job_id,)).fetchall()
        
        # 1. Update the drum_inventory drums that match the lab's manifest and profile
        for lab in labs:
            conn.execute('''
                UPDATE drum_inventory 
                SET process_type = 'TESTED', 
                    ph = ?, 
                    voc_ppm = ?, 
                    voc_weight = weight * ?
                WHERE job_id = ? AND manifest = ? AND inb_prof = ? AND process_type = 'PENDING SAMPLING'
            ''', (lab['ph_result'], lab['voc_result'], lab['voc_result'], job_id, lab['manifest'], lab['profile']))
            
        # 2. Update ALL remaining drums for this job_id that are PENDING SAMPLING to TESTED
        conn.execute('''
            UPDATE drum_inventory
            SET process_type = 'TESTED'
            WHERE job_id = ? AND process_type = 'PENDING SAMPLING'
        ''', (job_id,))
        
        # 3. Update the drum_lab_queue status to 'FINAL CODED' for this job_id
        conn.execute('''
            UPDATE drum_lab_queue 
            SET status = 'FINAL CODED' 
            WHERE job_id = ?
        ''', (job_id,))
        
        conn.commit()
        
        # Emit a socket update for the completed load
        from shared_state import socketio
        socketio.emit('drum_update', {'job_id': job_id})
        
    return redirect(url_for('stu_bp.stu_hub', view='pipeline'))

@approvals_bp.route('/api/parse_profile_pdf', methods=['POST'])
def parse_profile_pdf():
    if 'pdf_file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['pdf_file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    extracted_data = {'profile_number': '', 'generator_name': '', 'waste_description': '', 'win_code': '', 'special_handling': '', 'physical_appearance': ''}
    try:
        with pdfplumber.open(file) as pdf:
            full_text = "".join([page.extract_text() + "\n" for page in pdf.pages[:2] if page.extract_text()])
            
            # More resilient Profile Number match
            prof_match = re.search(r'(?:Profile No\.|Profile:)\s*([A-Z0-9]+)', full_text, re.IGNORECASE)
            if prof_match: extracted_data['profile_number'] = prof_match.group(1).strip()
            
            # Fix Generator Name missing (and make it case insensitive)
            gen_match = re.search(r'GENERATOR(?: NAME)?:\s*(.*?)\n', full_text, re.IGNORECASE)
            if gen_match: extracted_data['generator_name'] = gen_match.group(1).strip()
            
            desc_match = re.search(r'WASTE DESCRIPTION:\s*(.*?)\n', full_text, re.IGNORECASE)
            if desc_match: extracted_data['waste_description'] = desc_match.group(1).strip()
            
            handling_match = re.search(r'SPECIAL HANDLING.*?:\s*(.*?)\n', full_text, re.IGNORECASE)
            if handling_match: extracted_data['special_handling'] = handling_match.group(1).strip()
            
            form_code_match = re.search(r'(?:FORM CODE|WIN|Routing).*?([A-Z][0-9]{3})', full_text, re.IGNORECASE | re.DOTALL)
            if form_code_match: extracted_data['win_code'] = form_code_match.group(1).strip()
            
            # Attempt to find physical state checkmarks (look for Solid, Liquid, Sludge near a checkmark 'X' or unicode checkbox)
            state_match = re.search(r'(?:X|☒|☑|\u2611|\u2713|\[X\])\s*(Solid|Liquid|Sludge|Gas|Powder|Debris)', full_text, re.IGNORECASE)
            if state_match: extracted_data['physical_appearance'] = state_match.group(1).strip().capitalize()
            
        return jsonify(extracted_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@approvals_bp.route('/add_master_profile', methods=['POST'])
def add_master_profile():
    with closing(get_db_connection()) as conn:
        generator = request.form.get('generator') or request.form.get('generator_name') or ''
        voc_pct = 0.0
        try:
            voc_pct = float(request.form.get('voc_percentage', 0.0) or 0.0)
        except (ValueError, TypeError):
            pass

        epa_id = request.form.get('epa_id', '').strip()
        conn.execute('''
            REPLACE INTO profiles (profile_number, generator, waste_description, win_code, voc_percentage, special_handling, ph_range, physical_appearance, flash_point, expiration_date, epa_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'S')
        ''', (request.form.get('profile_number').upper(), generator, request.form.get('waste_description', ''), request.form.get('win_code', ''), voc_pct, request.form.get('special_handling', ''), request.form.get('ph_range', ''), request.form.get('physical_appearance', ''), request.form.get('flash_point', ''), request.form.get('expiration_date', ''), epa_id))
        conn.commit()
    return redirect(url_for('approvals_bp.approvals_portal', selected_profile=request.form.get('profile_number').upper()))

@approvals_bp.route('/api/auto_sync_profiles', methods=['POST'])
def auto_sync_profiles():
    date_str = request.form.get('schedule_date')
    updates_made = False
    
    if date_str:
        with closing(get_db_connection()) as conn:
            schedules = conn.execute('SELECT id, profile_number, voc_level, generator FROM daily_schedule WHERE schedule_date = ?', (date_str,)).fetchall()
            
            for s in schedules:
                prof_num = str(s['profile_number']).strip().upper()
                from database import ensure_profile_exists
                ensure_profile_exists(conn, prof_num)
                prof = conn.execute('''
                    SELECT voc_percentage, generator, win_code 
                    FROM profiles 
                    WHERE TRIM(UPPER(profile_number)) = ?
                ''', (prof_num,)).fetchone()
                
                if prof:
                    try:
                        val = float(prof['voc_percentage'])
                        new_voc = str(int(val)) if val > 0 else '0'
                    except (ValueError, TypeError):
                        new_voc = 'TBD'
                        
                    # CRITICAL: Only update the database IF the values are actually different
                    if new_voc != str(s['voc_level']) or str(prof['generator']) != str(s['generator']):
                        conn.execute('''
                            UPDATE daily_schedule 
                            SET voc_level = ?, generator = ?, routing_code = ?
                            WHERE id = ?
                        ''', (new_voc, prof['generator'], prof['win_code'], s['id']))
                        updates_made = True
                        
            if updates_made:
                conn.commit()
                
        # Only ping the connected users if we actually changed something
        if updates_made:
            SCHEDULE_UPDATES[date_str] = time.time()
            
    return jsonify({'updated': updates_made})

from shared_state import UPLOADS_DIR
UPLOAD_FOLDER = UPLOADS_DIR

@approvals_bp.route('/api/profile/search')
def api_profile_search():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify([])
        
    with closing(get_db_connection()) as conn:
        like_query = f"%{query}%"
        profiles = conn.execute('''
            SELECT *
            FROM profiles
            WHERE (profile_number LIKE ? OR generator LIKE ? OR lab_number LIKE ? OR win_code LIKE ? OR epa_id LIKE ?) AND status != 'NOT FOUND'
            ORDER BY profile_number ASC
            LIMIT 50
        ''', (like_query, like_query, like_query, like_query, like_query)).fetchall()
        
    return jsonify([dict(p) for p in profiles])

@approvals_bp.route('/api/profile/<profile_number>/history')
def api_profile_history(profile_number):
    with closing(get_db_connection()) as conn:
        loads = conn.execute('''
            SELECT manifest, job_id, import_date, weight, process_type, ph, voc_ppm
            FROM drum_inventory
            WHERE inb_prof = ?
            ORDER BY import_date DESC
        ''', (profile_number,)).fetchall()
    return jsonify([dict(l) for l in loads])

@approvals_bp.route('/api/profile/<profile_number>/upload', methods=['POST'])
def api_profile_upload(profile_number):
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if file:
        filename = secure_filename(file.filename)
        # Prepend profile number to ensure uniqueness in the folder
        save_name = f"{secure_filename(profile_number)}_{filename}"
        file_path = os.path.join(UPLOAD_FOLDER, save_name)
        file.save(file_path)
        
        with closing(get_db_connection()) as conn:
            conn.execute('''
                INSERT INTO profile_attachments (profile_number, filename, file_path)
                VALUES (?, ?, ?)
            ''', (profile_number, filename, save_name))
            conn.commit()
            
        return jsonify({'success': True, 'filename': filename})

@approvals_bp.route('/api/profile/<profile_number>/attachments')
def api_profile_attachments(profile_number):
    with closing(get_db_connection()) as conn:
        attachments = conn.execute('''
            SELECT id, filename, file_path, upload_date 
            FROM profile_attachments
            WHERE profile_number = ?
            ORDER BY upload_date DESC
        ''', (profile_number,)).fetchall()
    return jsonify([dict(a) for a in attachments])

@approvals_bp.route('/uploads/profiles/<path:filename>')
def serve_profile_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@approvals_bp.route('/release_las_truck', methods=['POST'])
def release_las_truck():
    from shared_state import socketio
    log_id = request.form.get('log_id')
    measured_ph = request.form.get('measured_ph')
    measured_voc = request.form.get('measured_voc')
    measured_sulfides = request.form.get('measured_sulfides', 'Negative')
    measured_cyanide = request.form.get('measured_cyanide', 'Negative')
    measured_free_liquids = request.form.get('measured_free_liquids', 'No')
    measured_flashpoint = request.form.get('measured_flashpoint', '')
    notes = request.form.get('notes', '')

    # Safely convert to float
    try:
        ph_val = float(measured_ph) if measured_ph else None
    except ValueError:
        ph_val = None

    try:
        voc_val = float(measured_voc) if measured_voc else None
    except ValueError:
        voc_val = None

    with closing(get_db_connection()) as conn:
        # Get the truck details to find profile number and date_received
        truck = conn.execute('SELECT profile_number, date_received FROM truck_logs WHERE id = ?', (log_id,)).fetchone()
        if not truck:
            return "Truck log not found", 404
        
        profile_number = truck['profile_number']
        received_date = truck['date_received'] or date.today().isoformat()

        # Find the profile details
        from database import ensure_profile_exists
        ensure_profile_exists(conn, profile_number)
        profile = conn.execute('SELECT win_code FROM profiles WHERE TRIM(UPPER(profile_number)) = TRIM(UPPER(?))', (profile_number,)).fetchone()
        
        is_ccs = False
        if profile_number and 'CCS' in profile_number.upper():
            is_ccs = True
        elif profile and profile['win_code'] and 'CCS' in str(profile['win_code']).upper():
            is_ccs = True

        # CCS check
        if is_ccs:
            if voc_val is not None and voc_val > 500:
                voc_pass_fail = 'FAIL'
            else:
                voc_pass_fail = 'PASS'
        else:
            voc_pass_fail = 'N/A'

        release_note = f"Released by Waste Acceptance. {notes}".strip() if notes else "Released by Waste Acceptance."

        conn.execute('''
            UPDATE truck_logs
            SET measured_ph = ?,
                measured_voc = ?,
                measured_sulfides = ?,
                measured_cyanide = ?,
                measured_free_liquids = ?,
                measured_flashpoint = ?,
                voc_pass_fail = ?,
                lab_results = ?,
                test_status = 'LAB COMPLETED'
            WHERE id = ?
        ''', (ph_val, voc_val, measured_sulfides, measured_cyanide, measured_free_liquids, measured_flashpoint, voc_pass_fail, release_note, log_id))
        
        conn.commit()

        # Emit websocket updates to keep queues in sync
        socketio.emit('lab_update', {'date': received_date})
        socketio.emit('truck_update', {'date': received_date})

    return redirect(url_for('stu_bp.stu_hub', view='pipeline'))

# --- WASTE ACCEPTANCE ACTIVE REVIEWS LOG API ---

@approvals_bp.route('/api/waste_acceptance/log', methods=['GET'])
def get_waste_acceptance_log():
    with closing(get_db_connection()) as conn:
        logs = conn.execute('''
            SELECT w.*, p.generator 
            FROM waste_acceptance_log w
            LEFT JOIN profiles p ON TRIM(UPPER(w.profile_number)) = TRIM(UPPER(p.profile_number))
            ORDER BY w.last_updated DESC
        ''').fetchall()
    return jsonify([dict(row) for row in logs])

@approvals_bp.route('/api/waste_acceptance/log/add', methods=['POST'])
def add_waste_acceptance_log():
    data = request.get_json() or {}
    profile_number = data.get('profile_number', '').strip().upper()
    if not profile_number:
        return jsonify({'error': 'Profile number is required'}), 400
        
    with closing(get_db_connection()) as conn:
        try:
            conn.execute('''
                INSERT INTO waste_acceptance_log (profile_number, status, assigned_to, notes)
                VALUES (?, 'Under Review', '', '')
            ''', (profile_number,))
            conn.commit()
            return jsonify({'success': True})
        except Exception as e:
            # Handle UNIQUE constraint failure if it already exists
            return jsonify({'error': str(e)}), 400

@approvals_bp.route('/api/waste_acceptance/log/update', methods=['POST'])
def update_waste_acceptance_log():
    data = request.get_json() or {}
    log_id = data.get('id')
    status = data.get('status')
    assigned_to = data.get('assigned_to')
    notes = data.get('notes')
    
    if not log_id:
        return jsonify({'error': 'Log ID is required'}), 400
        
    with closing(get_db_connection()) as conn:
        conn.execute('''
            UPDATE waste_acceptance_log 
            SET status = COALESCE(?, status),
                assigned_to = COALESCE(?, assigned_to),
                notes = COALESCE(?, notes),
                last_updated = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (status, assigned_to, notes, log_id))
        conn.commit()
    return jsonify({'success': True})

@approvals_bp.route('/api/waste_acceptance/log/delete', methods=['POST'])
def delete_waste_acceptance_log():
    data = request.get_json() or {}
    log_id = data.get('id')
    if not log_id:
        return jsonify({'error': 'Log ID is required'}), 400
        
    with closing(get_db_connection()) as conn:
        conn.execute('DELETE FROM waste_acceptance_log WHERE id = ?', (log_id,))
        conn.commit()
    return jsonify({'success': True})
