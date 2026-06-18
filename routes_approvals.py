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
        
    extracted_data = {
        'profile_number': '', 'generator_name': '', 'waste_description': '', 
        'special_handling': '', 'physical_appearance': '',
        'epa_id': '', 'dot_description': '', 'state_waste_code': '', 
        'federal_waste_code': '', 'ph_range': '', 'flash_point': '',
        'cyanide': 'No', 'sulfide': 'No', 'free_liquids': 'No', 'ldr_required': 'No'
    }
    try:
        import pdfplumber
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
            
            # EPA ID
            epa_match = re.search(r'EPA\s*(?:ID)?\s*(?:No\.)?:\s*([A-Z0-9]+)', full_text, re.IGNORECASE)
            if epa_match: extracted_data['epa_id'] = epa_match.group(1).strip()
            
            # DOT Description
            dot_match = re.search(r'(?:DOT Description|Shipping Name|Proper Shipping Name):\s*(.*?)\n', full_text, re.IGNORECASE)
            if dot_match: extracted_data['dot_description'] = dot_match.group(1).strip()
            
            # State/Fed waste codes
            state_match = re.search(r'(?:State Waste Code|State Code|State Codes):\s*([A-Z0-9,\s]+)\n', full_text, re.IGNORECASE)
            if state_match: extracted_data['state_waste_code'] = state_match.group(1).strip()
            
            fed_match = re.search(r'(?:Federal Waste Code|EPA Waste Code|EPA Codes|Federal Codes):\s*([A-Z0-9,\s]+)\n', full_text, re.IGNORECASE)
            if fed_match: extracted_data['federal_waste_code'] = fed_match.group(1).strip()
            
            # pH / Flash point
            ph_match = re.search(r'pH\s*(?:Range)?:\s*([0-9\.\-\s]+)', full_text, re.IGNORECASE)
            if ph_match: extracted_data['ph_range'] = ph_match.group(1).strip()
            
            fp_match = re.search(r'(?:Flash Point|FP):\s*([A-Z0-9\.\-\s\>\<]+)', full_text, re.IGNORECASE)
            if fp_match: extracted_data['flash_point'] = fp_match.group(1).strip()
            
            # Cyanide / Sulfide / Free Liquids / LDR
            cyanide_match = re.search(r'Cyanide:\s*(Yes|No)', full_text, re.IGNORECASE)
            if cyanide_match: extracted_data['cyanide'] = cyanide_match.group(1).strip().capitalize()
            
            sulfide_match = re.search(r'Sulfide:\s*(Yes|No)', full_text, re.IGNORECASE)
            if sulfide_match: extracted_data['sulfide'] = sulfide_match.group(1).strip().capitalize()

            free_liq_match = re.search(r'Free Liquids:\s*(Yes|No)', full_text, re.IGNORECASE)
            if free_liq_match: extracted_data['free_liquids'] = free_liq_match.group(1).strip().capitalize()

            ldr_match = re.search(r'LDR\s*(?:Required)?:\s*(Yes|No)', full_text, re.IGNORECASE)
            if ldr_match: extracted_data['ldr_required'] = ldr_match.group(1).strip().capitalize()
            
            # Attempt to find physical state checkmarks and normalize to Solid or Liquid
            state_chk_match = re.search(r'(?:X|☒|☑|\u2611|\u2713|\[X\])\s*(Solid|Liquid|Sludge|Gas|Powder|Debris)', full_text, re.IGNORECASE)
            if state_chk_match:
                matched_state = state_chk_match.group(1).strip().lower()
                if 'liquid' in matched_state or 'sludge' in matched_state:
                    extracted_data['physical_appearance'] = 'Liquid'
                else:
                    extracted_data['physical_appearance'] = 'Solid'
            
        return jsonify(extracted_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@approvals_bp.route('/add_master_profile', methods=['POST'])
def add_master_profile():
    profile_number = (request.form.get('profile_number') or '').strip().upper()
    if not profile_number:
        return "Profile Number is required", 400

    generator = request.form.get('generator') or request.form.get('generator_name') or ''
    epa_id = request.form.get('epa_id', '').strip()
    waste_description = request.form.get('waste_description', '').strip()
    win_code = request.form.get('win_code', '').strip()
    special_handling = request.form.get('special_handling', '').strip()
    ph_range = request.form.get('ph_range', '').strip()
    physical_appearance = request.form.get('physical_appearance', '').strip()
    flash_point = request.form.get('flash_point', '').strip()
    expiration_date = request.form.get('expiration_date', '').strip()
    
    ldr_required = request.form.get('ldr_required', 'No').strip()
    state_waste_code = request.form.get('state_waste_code', '').strip()
    federal_waste_code = request.form.get('federal_waste_code', '').strip()
    dot_description = request.form.get('dot_description', '').strip()
    cyanide = request.form.get('cyanide', 'No').strip()
    sulfide = request.form.get('sulfide', 'No').strip()
    free_liquids = request.form.get('free_liquids', 'No').strip()

    voc_pct = 0.0
    try:
        voc_pct = float(request.form.get('voc_percentage', 0.0) or 0.0)
    except (ValueError, TypeError):
        pass

    with closing(get_db_connection()) as conn:
        existing = conn.execute('SELECT profile_number FROM profiles WHERE TRIM(UPPER(profile_number)) = ?', (profile_number,)).fetchone()
        if existing:
            conn.execute('''
                UPDATE profiles 
                SET generator = ?, waste_description = ?, win_code = ?, voc_percentage = ?, 
                    special_handling = ?, ph_range = ?, physical_appearance = ?, flash_point = ?, 
                    expiration_date = ?, epa_id = ?, ldr_required = ?, state_waste_code = ?, 
                    federal_waste_code = ?, dot_description = ?, cyanide = ?, sulfide = ?, 
                    free_liquids = ?
                WHERE TRIM(UPPER(profile_number)) = ?
            ''', (generator, waste_description, win_code, voc_pct, 
                  special_handling, ph_range, physical_appearance, flash_point, 
                  expiration_date, epa_id, ldr_required, state_waste_code, 
                  federal_waste_code, dot_description, cyanide, sulfide, 
                  free_liquids, profile_number))
        else:
            conn.execute('''
                INSERT INTO profiles (profile_number, generator, waste_description, win_code, voc_percentage, 
                                      special_handling, ph_range, physical_appearance, flash_point, expiration_date, 
                                      epa_id, status, ldr_required, state_waste_code, federal_waste_code, 
                                      dot_description, cyanide, sulfide, free_liquids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'S', ?, ?, ?, ?, ?, ?, ?)
            ''', (profile_number, generator, waste_description, win_code, voc_pct, 
                  special_handling, ph_range, physical_appearance, flash_point, expiration_date, 
                  epa_id, ldr_required, state_waste_code, federal_waste_code, 
                  dot_description, cyanide, sulfide, free_liquids))
        conn.commit()
        
    return redirect(url_for('approvals_bp.approvals_portal', selected_profile=profile_number))

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
            WHERE profile_number LIKE ? OR generator LIKE ? OR lab_number LIKE ? OR win_code LIKE ? OR epa_id LIKE ?
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
