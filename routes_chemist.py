from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from datetime import date
from contextlib import closing
import re
import os
import pandas as pd
from collections import defaultdict
from database import get_db_connection
from shared_state import socketio


chemist_bp = Blueprint('chemist_bp', __name__)

def sync_profile_from_wvi_file(conn, profile_number):
    if not profile_number:
        return None
        
    profile_clean = str(profile_number).strip().upper()
    wvi_dir = r"I:\Buttonwillow\WAP\WVI"
    if not os.path.exists(wvi_dir):
        return None
        
    # Search for files matching the profile number (Fast exists checks only, no listdir)
    file_path = None
    for name in [profile_clean, profile_clean.lower()]:
        for ext in ['.xls', '.xlsx', '.XLS', '.XLSX']:
            p = os.path.join(wvi_dir, f"{name}{ext}")
            if os.path.exists(p):
                file_path = p
                break
        if file_path:
            break
            
    if not file_path:
        # Cache that this profile was not found on the network so we never search for it again
        try:
            conn.execute("INSERT OR IGNORE INTO profile_wvi (profile, is_synced) VALUES (?, 1)", (profile_clean,))
            conn.execute("UPDATE profile_wvi SET is_synced = 1 WHERE profile = ?", (profile_clean,))
            conn.commit()
        except Exception as ex:
            print(f"Error caching missing profile {profile_clean}: {ex}")
        return None
        
    try:
        if file_path.lower().endswith('.xlsx'):
            df = pd.read_excel(file_path, header=None, engine='openpyxl')
        else:
            df = pd.read_excel(file_path, header=None, engine='xlrd')
            
        data = {
            'profile': profile_clean, 'filename': os.path.basename(file_path),
            'generator_name': None, 'waste_name': None, 'physical_description': None,
            'ldr': None, 'state_waste_codes': None, 'federal_waste_codes': None,
            'dot_description': None, 'handling_instruction': None, 'sample_procedures': None,
            'verification_procedures': None, 'ph_min': None, 'ph_max': None, 'sulfides': None,
            'cyanide': None, 'free_liquids': None, 'flashpoint': None, 'unloading_instructions': None,
            'reactivity_codes': None, 'approved_date': None, 'expiration_date': None, 'lab_num': None,
            'voc_ppm': None, 'treatment_information': None, 'notes_revisions': None, 'is_synced': 1
        }
        
        # Clean boolean helper
        def clean_bool(val):
            if pd.isna(val): return None
            val_str = str(val).replace('*', '').strip().upper()
            if val_str in ['YES', 'TRUE', 'POS', 'POSITIVE', 'Y']: return 'Yes'
            if val_str in ['NO', 'FALSE', 'NEG', 'NEGATIVE', 'NONE', 'ND', 'N', '<0.1']: return 'No'
            return val_str.title()
            
        # Clean sulfides helper
        def clean_sulf(val):
            if pd.isna(val): return None
            raw_str = str(val).replace('*', ' ').strip()
            raw_str = re.sub(r'\s+', ' ', raw_str)
            val_upper = raw_str.upper()
            base = None
            if 'NEG' in val_upper and 'POS' in val_upper: base = "Neg or Pos"
            elif 'NEG' in val_upper: base = "Negative"
            elif 'POS' in val_upper: base = "Positive"
            if not base: return raw_str
            note_text = raw_str
            for word in ['NEG', 'POS', 'OR', 'NEGATIVE', 'POSITIVE']:
                note_text = re.sub(word, '', note_text, flags=re.IGNORECASE)
            note_text = note_text.strip(' -/,')
            return f"{base} - {note_text}" if note_text else base

        # Clean date helper
        def clean_date_val(val):
            if pd.isna(val): return None
            val_str = str(val).strip()
            if '00:00:00' in val_str:
                val_str = val_str.split()[0]
            try:
                # check if it is serial date
                serial = float(val_str)
                dt = pd.to_datetime(serial, unit='D', origin='1899-12-30')
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                return val_str

        # Clean and join multi-column text values (handles notes, descriptions, names)
        def join_row_values(row_data):
            vals = []
            for v in row_data:
                if pd.isna(v): continue
                v_str = str(v).strip()
                if not v_str: continue
                # Strip trailing .0 if it's an integer stored as float (like serials/numbers)
                if v_str.endswith('.0') and v_str[:-2].isdigit():
                    v_str = v_str[:-2]
                vals.append(v_str)
            return ' '.join(vals).strip() if vals else None
                
        capturing_notes = False
        notes_lines = []
        
        for index, row in df.iterrows():
            col0 = row.iloc[0] if len(row) > 0 else None
            col1 = row.iloc[1] if len(row) > 1 else None
            col2 = row.iloc[2] if len(row) > 2 else None
            col3 = row.iloc[3] if len(row) > 3 else None
            col4 = row.iloc[4] if len(row) > 4 else None
            
            label0 = str(col0).strip().upper() if pd.notna(col0) else ""
            label1 = str(col1).strip().upper() if pd.notna(col1) else ""
            
            # Stop capturing notes if we encounter a new header label in column 0
            if label0 and label0 != 'NOTES/REVISIONS':
                capturing_notes = False
                
            if label0 == 'PROFILE':
                data['profile'] = str(col1).strip().upper() if pd.notna(col1) else profile_clean
            elif label0 == 'GENERATOR NAME':
                data['generator_name'] = join_row_values(row.iloc[1:])
            elif label0 == 'WASTE NAME':
                data['waste_name'] = join_row_values(row.iloc[1:])
            elif label0 == 'PHYSICAL DESCRIPTION':
                data['physical_description'] = join_row_values(row.iloc[1:])
            elif label0 == 'LDR':
                data['ldr'] = join_row_values(row.iloc[1:])
            elif label0 == 'STATE WASTE CODES':
                data['state_waste_codes'] = join_row_values(row.iloc[1:])
            elif label0 == 'FEDERAL WASTE CODES':
                data['federal_waste_codes'] = join_row_values(row.iloc[1:])
            elif label0 == 'DOT DESCRIPTION':
                data['dot_description'] = join_row_values(row.iloc[1:])
            elif label0 == 'HANDLING INSTRUCTION':
                data['handling_instruction'] = join_row_values(row.iloc[1:])
            elif label0 == 'SAMPLE PROCEDURES':
                data['sample_procedures'] = join_row_values(row.iloc[1:])
            elif label0 == 'VERIFICATION PROCEDURES':
                data['verification_procedures'] = join_row_values(row.iloc[1:])
            elif label0 == 'UNLOADING INSTRUCTIONS':
                data['unloading_instructions'] = join_row_values(row.iloc[1:])
            elif label0 == 'REACTIVITY CODES':
                data['reactivity_codes'] = join_row_values(row.iloc[1:])
            elif label0 == 'APPROVED DATE':
                data['approved_date'] = clean_date_val(col1)
            elif label0 == 'EXPIRATION DATE':
                data['expiration_date'] = clean_date_val(col1)
            elif label0 == 'TREATMENT INFORMATION':
                data['treatment_information'] = join_row_values(row.iloc[1:])
            
            # Stateful notes capturing
            if label0 == 'NOTES/REVISIONS':
                capturing_notes = True
                val = join_row_values(row.iloc[1:])
                if val:
                    notes_lines.append(val)
            elif capturing_notes and not label0:
                val = join_row_values(row.iloc[1:])
                if val:
                    notes_lines.append(val)
                
            if 'PH RANGE' in label1:
                try:
                    data['ph_min'] = float(col2) if pd.notna(col2) else None
                    data['ph_max'] = float(col4) if pd.notna(col4) else None
                except:
                    pass
            elif 'SULFIDES' in label1:
                data['sulfides'] = clean_sulf(col2)
            elif 'CYANIDE' in label1:
                data['cyanide'] = clean_bool(col2)
            elif 'FREE LIQUIDS' in label1:
                data['free_liquids'] = clean_bool(col2)
            elif 'FLASHPOINT' in label1 or 'FLASH POINT' in label1:
                data['flashpoint'] = str(col2).strip().upper() if pd.notna(col2) else None
            elif 'LAB.' in label1 or 'LAB #' in label1:
                data['lab_num'] = str(col2).strip() if pd.notna(col2) else None
            elif 'VOC' in label1:
                try:
                    data['voc_ppm'] = float(col2) if pd.notna(col2) else None
                except:
                    pass
                    
        if notes_lines:
            data['notes_revisions'] = '; '.join(notes_lines)
                    
        # Update SQLite table profile_wvi
        cols_order = [
            'profile', 'filename', 'generator_name', 'waste_name', 'physical_description',
            'ldr', 'state_waste_codes', 'federal_waste_codes', 'dot_description',
            'handling_instruction', 'sample_procedures', 'verification_procedures',
            'ph_min', 'ph_max', 'sulfides', 'cyanide', 'free_liquids', 'flashpoint',
            'unloading_instructions', 'reactivity_codes', 'approved_date',
            'expiration_date', 'lab_num', 'voc_ppm', 'treatment_information',
            'notes_revisions', 'is_synced'
        ]
        
        placeholders = ', '.join(['?'] * len(cols_order))
        cols_str = ', '.join(cols_order)
        vals_tuple = tuple(data[c] for c in cols_order)
        
        conn.execute(f'''
            INSERT OR REPLACE INTO profile_wvi ({cols_str})
            VALUES ({placeholders})
        ''', vals_tuple)
        conn.commit()
        return data
    except Exception as e:
        print(f"Error parsing profile file on-the-fly: {e}")
        return None

@chemist_bp.route('/chemist')
def chemist_dashboard():
    with closing(get_db_connection()) as conn:
        pending_lab_trucks = conn.execute('''
            SELECT tl.*, 
                   w.ph_min, w.ph_max, w.sulfides, w.cyanide, w.free_liquids, w.flashpoint, 
                   w.treatment_information, w.notes_revisions, w.physical_description, w.handling_instruction,
                   w.generator_name, w.waste_name, w.approved_date, w.expiration_date, w.is_synced
            FROM truck_logs tl
            LEFT JOIN profile_wvi w ON TRIM(UPPER(tl.profile_number)) = TRIM(UPPER(w.profile))
            WHERE (tl.test_assigned LIKE '%FINGERPRINT%' OR tl.test_assigned LIKE '%VOC TEST%') 
              AND tl.test_status = 'WEIGHED IN'
        ''').fetchall()
        
        pending_list = []
        for row in pending_lab_trucks:
            truck = dict(row)
            # If this profile has never been attempted to sync, run fast on-demand parser
            if truck.get('profile_number') and not truck.get('is_synced'):
                parsed_data = sync_profile_from_wvi_file(conn, truck['profile_number'])
                if parsed_data:
                    # Update dictionary values in memory for immediate UI display
                    for k, v in parsed_data.items():
                        if k == 'profile':
                            continue
                        truck[k] = v
                    truck['is_synced'] = 1
                else:
                    # Mark in memory so we don't query again during this page load
                    truck['is_synced'] = 1
            pending_list.append(truck)
            
    return render_template('chemist.html', pending_trucks=pending_list)

@chemist_bp.route('/update_lab', methods=['POST'])
def update_lab():
    log_id = request.form.get('log_id')
    lab_results = request.form.get('lab_results', '')
    
    # Extract new fields
    specific_gravity = request.form.get('specific_gravity')
    measured_ph = request.form.get('measured_ph')
    measured_flashpoint = request.form.get('measured_flashpoint', '')
    measured_sulfides = request.form.get('measured_sulfides', '')
    measured_cyanide = request.form.get('measured_cyanide', '')
    measured_free_liquids = request.form.get('measured_free_liquids', '')
    
    # Handle optional float numbers safely
    try:
        specific_gravity = float(specific_gravity) if specific_gravity else None
    except ValueError:
        specific_gravity = None
        
    try:
        measured_ph = float(measured_ph) if measured_ph else None
    except ValueError:
        measured_ph = None
        
    with closing(get_db_connection()) as conn:
        truck = conn.execute('SELECT date_received FROM truck_logs WHERE id = ?', (log_id,)).fetchone()
        received_date = truck['date_received'] if truck else date.today().isoformat()
        
        conn.execute('''
            UPDATE truck_logs 
            SET lab_results = ?, 
                specific_gravity = ?,
                measured_ph = ?,
                measured_flashpoint = ?,
                measured_sulfides = ?,
                measured_cyanide = ?,
                measured_free_liquids = ?,
                test_status = 'LAB COMPLETED' 
            WHERE id = ?
        ''', (lab_results, specific_gravity, measured_ph, measured_flashpoint, 
              measured_sulfides, measured_cyanide, measured_free_liquids, log_id))
        conn.commit()
        socketio.emit('lab_update', {'date': received_date})
    return redirect(url_for('chemist_bp.chemist_dashboard'))

@chemist_bp.route('/chemist/drums')
def chemist_drums():
    with closing(get_db_connection()) as conn:
        raw_queue = conn.execute("SELECT * FROM drum_lab_queue WHERE status != 'COMPLETED'").fetchall()
        
        def sort_priority(drum):
            test = str(drum['tests_required']).upper()
            if 'VOC' in test or 'CP1' in test or 'RECERT' in test:
                return 0 
            return 1 
            
        sorted_queue = sorted(raw_queue, key=sort_priority)
        
        jobs = defaultdict(list)
        for d in sorted_queue:
            jobs[d['job_id']].append(dict(d))
            
    return render_template('chemist_drums.html', jobs=jobs)

@chemist_bp.route('/chemist/drums/update', methods=['POST'])
def update_drum_lab():
    job_id = request.form.get('job_id')
    drum_db_ids = request.form.getlist('drum_db_id')
    
    with closing(get_db_connection()) as conn:
        for d_id in drum_db_ids:
            status = request.form.get(f'status_{d_id}')
            notes = request.form.get(f'notes_{d_id}', '')
            flashpoint = request.form.get(f'flashpoint_{d_id}', '')
            cyanide = request.form.get(f'cyanide_{d_id}', '')
            sulfide = request.form.get(f'sulfide_{d_id}', '')
            oxidation = request.form.get(f'oxidation_{d_id}', '')
            
            try: ph_result = float(request.form.get(f'ph_{d_id}', 0))
            except: ph_result = 0.0
                
            try: voc_result = float(request.form.get(f'voc_{d_id}', 0))
            except: voc_result = 0.0
            
            conn.execute('''
                UPDATE drum_lab_queue 
                SET status = ?, ph_result = ?, voc_result = ?, flashpoint = ?, 
                    cyanide = ?, sulfide = ?, oxidation = ?, notes = ?
                WHERE id = ?
            ''', (status, ph_result, voc_result, flashpoint, cyanide, sulfide, oxidation, notes, d_id))
                    
        conn.commit()
        socketio.emit('drum_update', {'job_id': job_id})
    return redirect(url_for('chemist_bp.chemist_drums'))

@chemist_bp.route('/final_code', methods=['POST'])
def final_code():
    lab_id = request.form.get('lab_id')
    manifest = request.form.get('manifest')
    profile = request.form.get('profile')
    ph_val = request.form.get('ph_val')
    voc_val = request.form.get('voc_val')
    
    with closing(get_db_connection()) as conn:
        conn.execute('''
            UPDATE drum_inventory 
            SET process_type = 'TESTED', ph = ?, voc_ppm = ?
            WHERE manifest = ? AND inb_prof = ? AND process_type = 'PENDING SAMPLING'
        ''', (ph_val, voc_val, manifest, profile))
        
        conn.execute("UPDATE drum_lab_queue SET status = 'FINAL CODED' WHERE id = ?", (lab_id,))
        conn.commit()
        socketio.emit('drum_update', {'manifest': manifest, 'profile': profile})
        
    return redirect(url_for('approvals_bp.waste_acceptance'))
