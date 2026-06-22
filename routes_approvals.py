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
        import re
        import math
        
        def get_distance(x1, y1, x2, y2):
            return math.sqrt((x1-x2)**2 + (y1-y2)**2)

        def find_nearest_word(page, cm, max_dist=100):
            cx = (cm['x0'] + cm['x1']) / 2
            cy = (cm['top'] + cm['bottom']) / 2
            words = page.extract_words()
            closest = None
            min_d = max_dist
            for w in words:
                wx = (w['x0'] + w['x1']) / 2
                wy = (w['top'] + w['bottom']) / 2
                dist = get_distance(cx, cy, wx, wy)
                if dist < min_d:
                    min_d = dist
                    closest = w
            return closest, min_d

        with pdfplumber.open(file) as pdf:
            full_text = "".join([page.extract_text() + "\n" for page in pdf.pages if page.extract_text()])
            
            # Profile Number
            prof_match = re.search(r'Profile No\.\s*([A-Z0-9]+)', full_text, re.IGNORECASE)
            if prof_match:
                extracted_data['profile_number'] = prof_match.group(1).strip()
                
            # Generator Name
            gen_match = re.search(r'GENERATOR\s+NAME:\s*(.*?)(?:\n|$)', full_text, re.IGNORECASE)
            if gen_match:
                extracted_data['generator_name'] = gen_match.group(1).strip()
                
            # EPA ID
            epa_match = re.search(r'EPA\s+ID\s*(?:#/REGISTRATION\s*#)?\s*([A-Z0-9]+)', full_text, re.IGNORECASE)
            if epa_match:
                extracted_data['epa_id'] = epa_match.group(1).strip()
                
            # Waste Description
            desc_match = re.search(r'CUSTOMER\s+WASTE\s+DESCRIPTION:\s*(.*?)(?:\n|$)', full_text, re.IGNORECASE)
            if desc_match:
                extracted_data['waste_description'] = desc_match.group(1).strip()
                
            # DOT Description
            dot_match = re.search(r'DOT/TDG\s+PROPER\s+SHIPPING\s+NAME:\s*\n\s*(.*?)(?:\n|$)', full_text, re.IGNORECASE)
            if dot_match:
                extracted_data['dot_description'] = dot_match.group(1).strip()
                
            # State Waste Code
            state_code = ""
            m1 = re.search(r'([A-Z0-9]+)\s*\n\s*(?:Texas|State)\s+Waste\s+Code', full_text, re.IGNORECASE)
            if m1:
                state_code = m1.group(1).strip()
            else:
                m2 = re.search(r'(?:Texas|State)\s+Waste\s+Code\s*\n\s*([A-Z0-9]+)', full_text, re.IGNORECASE)
                if m2:
                    state_code = m2.group(1).strip()
            extracted_data['state_waste_code'] = state_code

            # Federal Waste Code - Only search page 2
            p2_text = pdf.pages[1].extract_text() if len(pdf.pages) >= 2 else ""
            rcra_codes = sorted(list(set(re.findall(r'\b([DFKPU][0-9]{3})\b', p2_text))))
            extracted_data['federal_waste_code'] = ", ".join(rcra_codes)

            # LDR Required
            ldr_cat_match = re.search(r'LDR\s+CATEGORY:\s*(.*?)(?:\n|$)', full_text, re.IGNORECASE)
            if ldr_cat_match:
                cat_text = ldr_cat_match.group(1).strip().lower()
                if "not subject" in cat_text:
                    extracted_data['ldr_required'] = "No"
                else:
                    extracted_data['ldr_required'] = "Yes"
            else:
                if len(pdf.pages) >= 3:
                    p3 = pdf.pages[2]
                    checkmarks = [img for img in p3.images if 8 < img['width'] < 12 and 8 < img['height'] < 12]
                    checkmarks = sorted(checkmarks, key=lambda c: c['top'])
                    if checkmarks:
                        closest, d = find_nearest_word(p3, checkmarks[0])
                        if closest:
                            ans = closest['text'].strip().capitalize()
                            extracted_data['ldr_required'] = "Yes" if ans == "Yes" else "No"

            # Checkmarks on page 1 for Physical Appearance, pH, Flash Point, Free Liquids
            p1 = pdf.pages[0]
            p1_checkmarks = [img for img in p1.images if 8 < img['width'] < 12 and 8 < img['height'] < 12]
            
            physical_state = "Solid"
            free_liquids = "No"
            ph_val = ""
            fp_val = ""
            
            for cm in p1_checkmarks:
                # 1. Physical State Checkbox (y-coordinate/top is roughly between 250 and 320)
                if 250 < cm['top'] < 320:
                    line_words = [w['text'] for w in p1.extract_words() if abs(w['top'] - cm['top']) < 8 and w['x0'] >= cm['x0'] - 2]
                    line_text = " ".join(line_words).lower()
                    if "solid without free liquid" in line_text:
                        physical_state = "Solid"
                        free_liquids = "No"
                    elif "liquid" in line_text or "sludge" in line_text:
                        physical_state = "Liquid"
                        if "free liquid" in line_text:
                            free_liquids = "Yes"
                    elif "powder" in line_text or "monolithic" in line_text:
                        physical_state = "Solid"
                
                # 2. pH Checkbox (top is roughly between 420 and 450, x is roughly between 80 and 130)
                elif 420 < cm['top'] < 450 and 80 < cm['x0'] < 130:
                    line_words = [w['text'] for w in p1.extract_words() if abs(w['top'] - cm['top']) < 8 and w['x0'] >= cm['x0'] - 2 and w['x0'] < 160]
                    line_text = " ".join(line_words)
                    m = re.search(r'(<=?\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?|\d+\s*\(Neutral\)|>=?\s*\d+(?:\.\d+)?)', line_text)
                    if m:
                        ph_val = m.group(1)

                # 3. Flash Point Checkbox (top is roughly between 420 and 450, x is roughly less than 50)
                elif 420 < cm['top'] < 450 and cm['x0'] < 50:
                    line_words = [w['text'] for w in p1.extract_words() if abs(w['top'] - cm['top']) < 8 and w['x0'] >= cm['x0'] - 2 and w['x0'] < 90]
                    line_text = " ".join(line_words)
                    m = re.search(r'(<=?\s*\d+|73\s*-\s*100|\d+\s*-\s*\d+|\d+\s*-\s*\d+|>\s*\d+)', line_text)
                    if m:
                        fp_val = m.group(1)

            extracted_data['physical_appearance'] = physical_state
            extracted_data['free_liquids'] = free_liquids
            extracted_data['ph_range'] = ph_val if ph_val else "7 (Neutral)"
            
            if physical_state == "Solid" and not fp_val:
                extracted_data['flash_point'] = "Not Required"
            elif fp_val:
                extracted_data['flash_point'] = fp_val
            else:
                extracted_data['flash_point'] = "> 140"

            # Cyanide and Sulfide (page 2 checkmarks)
            cyanide = "No"
            sulfide = "No"
            if len(pdf.pages) >= 2:
                p2 = pdf.pages[1]
                p2_checkmarks = [img for img in p2.images if 8 < img['width'] < 12 and 8 < img['height'] < 12]
                for cm in p2_checkmarks:
                    line_words = sorted([w for w in p2.extract_words() if abs(w['top'] - cm['top']) < 8], key=lambda x: x['x0'])
                    line_text = " ".join([w['text'] for w in line_words]).lower()
                    if "cyanide" in line_text:
                        if cm['x0'] < 500:
                            cyanide = "Yes"
                    elif "sulfide" in line_text:
                        if cm['x0'] < 500:
                            sulfide = "Yes"
            extracted_data['cyanide'] = cyanide
            extracted_data['sulfide'] = sulfide
            
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

    status = request.form.get('status', 'S').strip().upper()

    with closing(get_db_connection()) as conn:
        existing = conn.execute('SELECT profile_number FROM profiles WHERE TRIM(UPPER(profile_number)) = ?', (profile_number,)).fetchone()
        if existing:
            conn.execute('''
                UPDATE profiles 
                SET generator = ?, waste_description = ?, win_code = ?, voc_percentage = ?, 
                    special_handling = ?, ph_range = ?, physical_appearance = ?, flash_point = ?, 
                    expiration_date = ?, epa_id = ?, ldr_required = ?, state_waste_code = ?, 
                    federal_waste_code = ?, dot_description = ?, cyanide = ?, sulfide = ?, 
                    free_liquids = ?, status = ?
                WHERE TRIM(UPPER(profile_number)) = ?
            ''', (generator, waste_description, win_code, voc_pct, 
                  special_handling, ph_range, physical_appearance, flash_point, 
                  expiration_date, epa_id, ldr_required, state_waste_code, 
                  federal_waste_code, dot_description, cyanide, sulfide, 
                  free_liquids, status, profile_number))
        else:
            conn.execute('''
                INSERT INTO profiles (profile_number, generator, waste_description, win_code, voc_percentage, 
                                      special_handling, ph_range, physical_appearance, flash_point, expiration_date, 
                                      epa_id, status, ldr_required, state_waste_code, federal_waste_code, 
                                      dot_description, cyanide, sulfide, free_liquids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (profile_number, generator, waste_description, win_code, voc_pct, 
                  special_handling, ph_range, physical_appearance, flash_point, expiration_date, 
                  epa_id, status, ldr_required, state_waste_code, federal_waste_code, 
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
    
    files = request.files.getlist('file')
    if not files or all(file.filename == '' for file in files):
        return jsonify({'error': 'No selected files'}), 400
        
    uploaded_files = []
    with closing(get_db_connection()) as conn:
        for file in files:
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                # Prepend profile number to ensure uniqueness in the folder
                save_name = f"{secure_filename(profile_number)}_{filename}"
                file_path = os.path.join(UPLOAD_FOLDER, save_name)
                file.save(file_path)
                
                conn.execute('''
                    INSERT INTO profile_attachments (profile_number, filename, file_path)
                    VALUES (?, ?, ?)
                ''', (profile_number, filename, save_name))
                uploaded_files.append(filename)
        conn.commit()
        
    return jsonify({'success': True, 'filenames': uploaded_files})

@approvals_bp.route('/api/profile/attachment/<int:attachment_id>/delete', methods=['POST'])
def api_delete_attachment(attachment_id):
    with closing(get_db_connection()) as conn:
        attachment = conn.execute('SELECT file_path FROM profile_attachments WHERE id = ?', (attachment_id,)).fetchone()
        if not attachment:
            return jsonify({'error': 'Attachment not found'}), 404
        
        file_path = attachment['file_path']
        full_path = os.path.join(UPLOAD_FOLDER, file_path)
        
        # Delete from database
        conn.execute('DELETE FROM profile_attachments WHERE id = ?', (attachment_id,))
        conn.commit()
        
        # Delete physical file
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except Exception as e:
                print(f"Error deleting file {full_path}: {e}")
                
    return jsonify({'success': True})

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
    archived = request.args.get('archived', '0')
    archived_val = 1 if archived == '1' else 0
    with closing(get_db_connection()) as conn:
        logs = conn.execute('''
            SELECT w.*, COALESCE(w.generator_requestor, p.generator) AS generator 
            FROM waste_acceptance_log w
            LEFT JOIN profiles p ON TRIM(UPPER(w.profile_number)) = TRIM(UPPER(p.profile_number))
            WHERE COALESCE(w.is_archived, 0) = ?
            ORDER BY w.last_updated DESC
        ''', (archived_val,)).fetchall()
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
                VALUES (?, 'Needs Review', '', '')
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
    generator_requestor = data.get('generator_requestor')
    profile_number = data.get('profile_number')
    
    if profile_number is not None:
        profile_number = profile_number.strip().upper()
        if not profile_number:
            return jsonify({'success': False, 'error': 'Profile number cannot be empty'}), 400
            
    if not log_id:
        return jsonify({'success': False, 'error': 'Log ID is required'}), 400
        
    with closing(get_db_connection()) as conn:
        try:
            conn.execute('''
                UPDATE waste_acceptance_log 
                SET status = COALESCE(?, status),
                    assigned_to = COALESCE(?, assigned_to),
                    notes = COALESCE(?, notes),
                    generator_requestor = COALESCE(?, generator_requestor),
                    profile_number = COALESCE(?, profile_number),
                    last_updated = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (status, assigned_to, notes, generator_requestor, profile_number, log_id))
            conn.commit()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': 'Profile number must be unique in the log.'}), 400

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

@approvals_bp.route('/api/waste_acceptance/log/archive', methods=['POST'])
def archive_waste_acceptance_log():
    data = request.get_json() or {}
    log_id = data.get('id')
    is_archived = data.get('is_archived', 1)
    if not log_id:
        return jsonify({'error': 'Log ID is required'}), 400
        
    archived_val = 1 if is_archived else 0
    with closing(get_db_connection()) as conn:
        conn.execute('''
            UPDATE waste_acceptance_log 
            SET is_archived = ?, last_updated = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (archived_val, log_id))
        conn.commit()
    return jsonify({'success': True})

@approvals_bp.route('/api/profile/<profile_number>/wvi')
def export_wvi_excel(profile_number):
    import io
    import xlrd
    import openpyxl
    from openpyxl.styles import Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from flask import send_file
    import re
    
    profile_clean = str(profile_number).strip().upper()
    
    # 1. Fetch data from profiles and profile_wvi tables
    with closing(get_db_connection()) as conn:
        p_row = conn.execute('SELECT * FROM profiles WHERE TRIM(UPPER(profile_number)) = ?', (profile_clean,)).fetchone()
        w_row = conn.execute('SELECT * FROM profile_wvi WHERE TRIM(UPPER(profile)) = ?', (profile_clean,)).fetchone()
        
    if not p_row and not w_row:
        return "Profile not found in database", 404
        
    # Helper to parse pH range (like "7.0 to 12.4" or "7.0-12.0")
    def parse_ph_range(ph_range):
        if not ph_range:
            return None, None
        m = re.findall(r'(\d+\.?\d*)', str(ph_range))
        if len(m) >= 2:
            try:
                return float(m[0]), float(m[1])
            except:
                pass
        elif len(m) == 1:
            try:
                return float(m[0]), float(m[0])
            except:
                pass
        return None, None

    # Helper to convert/clean voc_percentage to voc_ppm
    def get_voc_ppm(percentage):
        if percentage is None:
            return None
        try:
            val = float(percentage)
            return val * 10000 if val < 10 else val
        except:
            return None

    # Combine data from tables
    combined = {
        'profile': profile_clean,
        'generator_name': w_row['generator_name'] if (w_row and w_row['generator_name']) else (p_row['generator'] if p_row else ''),
        'waste_name': w_row['waste_name'] if (w_row and w_row['waste_name']) else (p_row['waste_description'] if p_row else ''),
        'physical_description': w_row['physical_description'] if (w_row and w_row['physical_description']) else (p_row['physical_appearance'] if p_row else ''),
        'ldr': w_row['ldr'] if (w_row and w_row['ldr']) else (p_row['ldr_required'] if p_row else ''),
        'state_waste_codes': w_row['state_waste_codes'] if (w_row and w_row['state_waste_codes']) else (p_row['state_waste_code'] if p_row else ''),
        'federal_waste_codes': w_row['federal_waste_codes'] if (w_row and w_row['federal_waste_codes']) else (p_row['federal_waste_code'] if p_row else ''),
        'dot_description': w_row['dot_description'] if (w_row and w_row['dot_description']) else (p_row['dot_description'] if p_row else ''),
        'handling_instruction': w_row['handling_instruction'] if (w_row and w_row['handling_instruction']) else (p_row['special_handling'] if p_row else ''),
        'sample_procedures': w_row['sample_procedures'] if (w_row and w_row['sample_procedures']) else '',
        'verification_procedures': w_row['verification_procedures'] if (w_row and w_row['verification_procedures']) else 'VISUAL',
        'ph_min': w_row['ph_min'] if (w_row and w_row['ph_min'] is not None) else None,
        'ph_max': w_row['ph_max'] if (w_row and w_row['ph_max'] is not None) else None,
        'sulfides': w_row['sulfides'] if (w_row and w_row['sulfides']) else (p_row['sulfide'] if p_row else ''),
        'cyanide': w_row['cyanide'] if (w_row and w_row['cyanide']) else (p_row['cyanide'] if p_row else ''),
        'free_liquids': w_row['free_liquids'] if (w_row and w_row['free_liquids']) else (p_row['free_liquids'] if p_row else ''),
        'flashpoint': w_row['flashpoint'] if (w_row and w_row['flashpoint']) else (p_row['flash_point'] if p_row else ''),
        'unloading_instructions': w_row['unloading_instructions'] if (w_row and w_row['unloading_instructions']) else '',
        'reactivity_codes': w_row['reactivity_codes'] if (w_row and w_row['reactivity_codes']) else 'NONE',
        'approved_date': w_row['approved_date'] if (w_row and w_row['approved_date']) else '',
        'expiration_date': w_row['expiration_date'] if (w_row and w_row['expiration_date']) else (p_row['expiration_date'] if p_row else ''),
        'lab_num': w_row['lab_num'] if (w_row and w_row['lab_num']) else (p_row['lab_number'] if p_row else ''),
        'voc_ppm': w_row['voc_ppm'] if (w_row and w_row['voc_ppm'] is not None) else None,
        'treatment_information': w_row['treatment_information'] if (w_row and w_row['treatment_information']) else 'Not applicable',
        'notes_revisions': w_row['notes_revisions'] if (w_row and w_row['notes_revisions']) else ''
    }

    # Fill in fallback parsed range/voc
    if combined['ph_min'] is None or combined['ph_max'] is None:
        if p_row and p_row['ph_range']:
            ph_min_parsed, ph_max_parsed = parse_ph_range(p_row['ph_range'])
            if combined['ph_min'] is None:
                combined['ph_min'] = ph_min_parsed
            if combined['ph_max'] is None:
                combined['ph_max'] = ph_max_parsed

    if combined['voc_ppm'] is None and p_row and p_row['voc_percentage'] is not None:
        combined['voc_ppm'] = get_voc_ppm(p_row['voc_percentage'])

    # Format Sulfides/Cyanide/Free Liquids to NEG/POS style or YES/NO style
    def to_neg_pos(val):
        if not val:
            return 'NEG'
        v_str = str(val).strip().upper()
        if v_str in ['YES', 'TRUE', 'POS', 'POSITIVE', 'Y']:
            return 'POS'
        if v_str in ['NO', 'FALSE', 'NEG', 'NEGATIVE', 'N']:
            return 'NEG'
        return val

    def to_yes_no(val):
        if not val:
            return 'NO'
        v_str = str(val).strip().upper()
        if v_str in ['YES', 'TRUE', 'POS', 'POSITIVE', 'Y']:
            return 'YES'
        if v_str in ['NO', 'FALSE', 'NEG', 'NEGATIVE', 'N']:
            return 'NO'
        return val

    combined['sulfides'] = to_neg_pos(combined['sulfides'])
    combined['cyanide'] = to_neg_pos(combined['cyanide'])
    combined['free_liquids'] = to_yes_no(combined['free_liquids'])

    # Format date outputs
    def format_date_serial_or_string(val):
        if not val:
            return ''
        return str(val)

    combined['approved_date'] = format_date_serial_or_string(combined['approved_date'])
    combined['expiration_date'] = format_date_serial_or_string(combined['expiration_date'])

    # Locate the template Excel file dynamically
    app_dir = os.path.dirname(os.path.abspath(__file__))
    win_code = str(p_row['win_code'] if p_row else '').strip().upper()
    
    template_path = None
    if win_code:
        # Try to find a template matching the win_code (e.g. CBP.xls or CBP.xlsx)
        for ext in ['.xls', '.xlsx']:
            p = os.path.join(app_dir, f"{win_code}{ext}")
            if os.path.exists(p):
                template_path = p
                break
                
    if not template_path:
        template_path = os.path.join(app_dir, 'CH2951500.xls')
        
    if not os.path.exists(template_path):
        return f"Template file not found in app directory. Tried {win_code}.xls/xlsx and fallback CH2951500.xls.", 500

    # Load template sheet
    try:
        workbook = xlrd.open_workbook(template_path, formatting_info=True)
        sheet = workbook.sheet_by_index(0)
    except Exception as e:
        return f"Error loading template: {str(e)}", 500

    # Build openpyxl workbook
    wb = openpyxl.Workbook()
    if "Sheet" in wb.sheetnames:
        wb.remove(wb["Sheet"])
    ws = wb.create_sheet(title=f"WVI {combined['profile']}")
    ws.views.sheetView[0].showGridLines = True

    # 1. Replicate column widths
    for c in range(sheet.ncols):
        col_letter = get_column_letter(c + 1)
        width = 12
        if c in sheet.colinfo_map:
            width = max(sheet.colinfo_map[c].width / 256.0, 10)
        ws.column_dimensions[col_letter].width = width

    # 2. Build dynamic cell mapping by scanning labels in the template sheet
    cell_mapping = {}
    for r in range(sheet.nrows):
        col0_val = str(sheet.cell_value(r, 0)).strip()
        col1_val = str(sheet.cell_value(r, 1)).strip()
        
        # Check Column 0 labels
        c0_upper = col0_val.upper()
        if c0_upper == 'PROFILE':
            cell_mapping[(r, 1)] = 'profile'
        elif c0_upper == 'GENERATOR NAME':
            cell_mapping[(r, 1)] = 'generator_name'
        elif c0_upper == 'WASTE NAME':
            cell_mapping[(r, 1)] = 'waste_name'
        elif c0_upper == 'PHYSICAL DESCRIPTION':
            cell_mapping[(r, 1)] = 'physical_description'
        elif c0_upper == 'LDR':
            cell_mapping[(r, 1)] = 'ldr'
        elif c0_upper in ['STATE WASTE CODES', 'STATE WASTE CODE']:
            cell_mapping[(r, 1)] = 'state_waste_codes'
        elif c0_upper in ['FEDERAL WASTE CODES', 'FEDERAL WASTE CODE']:
            cell_mapping[(r, 1)] = 'federal_waste_codes'
        elif c0_upper == 'DOT DESCRIPTION':
            cell_mapping[(r, 1)] = 'dot_description'
        elif c0_upper == 'HANDLING INSTRUCTION':
            cell_mapping[(r, 1)] = 'handling_instruction'
        elif c0_upper == 'SAMPLE PROCEDURES':
            cell_mapping[(r, 1)] = 'sample_procedures'
        elif c0_upper == 'VERIFICATION PROCEDURES':
            cell_mapping[(r, 1)] = 'verification_procedures'
        elif c0_upper == 'UNLOADING INSTRUCTIONS':
            cell_mapping[(r, 1)] = 'unloading_instructions'
        elif c0_upper == 'REACTIVITY CODES':
            cell_mapping[(r, 1)] = 'reactivity_codes'
        elif c0_upper == 'APPROVED DATE':
            cell_mapping[(r, 1)] = 'approved_date'
        elif c0_upper == 'EXPIRATION DATE':
            cell_mapping[(r, 1)] = 'expiration_date'
        elif c0_upper == 'TREATMENT INFORMATION':
            cell_mapping[(r, 1)] = 'treatment_information'
        elif c0_upper == 'NOTES/REVISIONS':
            cell_mapping[(r, 1)] = 'notes_revisions'
            
        # Check Column 1 labels
        c1_upper = col1_val.upper()
        if 'PH RANGE' in c1_upper:
            cell_mapping[(r, 2)] = 'ph_min'
            cell_mapping[(r, 4)] = 'ph_max'
        elif 'SULFIDES' in c1_upper:
            cell_mapping[(r, 2)] = 'sulfides'
        elif 'CYANIDE' in c1_upper:
            cell_mapping[(r, 2)] = 'cyanide'
        elif 'FREE LIQUIDS' in c1_upper:
            cell_mapping[(r, 2)] = 'free_liquids'
        elif 'LAB.' in c1_upper or 'LAB #' in c1_upper:
            cell_mapping[(r, 2)] = 'lab_num'
        elif 'VOC' in c1_upper:
            cell_mapping[(r, 2)] = 'voc_ppm'

    align_map_h = {1: 'left', 2: 'center', 3: 'right', 5: 'justify'}
    align_map_v = {0: 'top', 1: 'center', 2: 'bottom'}

    def format_output_val(key, val):
        if val is None:
            return ''
        if key in ['ph_min', 'ph_max', 'voc_ppm']:
            try:
                return float(val)
            except:
                return val
        return str(val)

    # 3. Copy cells and apply styles/values
    for r in range(sheet.nrows):
        if r in sheet.rowinfo_map:
            height = sheet.rowinfo_map[r].height / 20.0
            if height > 0:
                ws.row_dimensions[r + 1].height = height
                
        for c in range(sheet.ncols):
            cell = sheet.cell(r, c)
            cell_val = cell.value
            
            if (r, c) in cell_mapping:
                key = cell_mapping[(r, c)]
                cell_val = format_output_val(key, combined.get(key, cell_val))
            
            new_cell = ws.cell(row=r + 1, column=c + 1, value=cell_val)
            
            # Formatting
            xf = workbook.xf_list[cell.xf_index]
            font = workbook.font_list[xf.font_index]
            
            # Alignments
            h_align = align_map_h.get(xf.alignment.hor_align, 'left')
            v_align = align_map_v.get(xf.alignment.vert_align, 'center')
            new_cell.alignment = Alignment(
                horizontal=h_align,
                vertical=v_align,
                wrap_text=True if (c == 1 and r in [5, 7, 9, 13, 15, 29, 37, 43]) else False
            )
            
            # Font
            new_cell.font = Font(
                name=font.name,
                size=font.height // 20,
                bold=bool(font.bold),
                italic=bool(font.italic),
                color="000000" if font.colour_index == 32767 else None
            )
            
            # Borders for parameter table (B22:E25)
            if r in range(21, 25) and c in range(1, 5):
                thin_side = Side(border_style="thin", color="000000")
                new_cell.border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    # 4. Copy merged ranges
    for crange in sheet.merged_cells:
        rlo, rhi, clo, chi = crange
        ws.merge_cells(
            start_row=rlo + 1,
            end_row=rhi,
            start_column=clo + 1,
            end_column=chi
        )

    # Save to a memory buffer
    output_io = io.BytesIO()
    wb.save(output_io)
    output_io.seek(0)

    filename = f"WVI_{combined['profile']}.xlsx"
    return send_file(
        output_io,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )

