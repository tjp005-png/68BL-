from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from datetime import date, datetime
from contextlib import closing
import re
from database import get_db_connection

receiving_bp = Blueprint('receiving_bp', __name__)

@receiving_bp.route('/receiving')
def home():
    today_str = date.today().isoformat() 
    with closing(get_db_connection()) as conn:
        pending_trucks = conn.execute("SELECT * FROM truck_logs WHERE exit_weight IS NULL AND test_status != 'REJECTED' ORDER BY id DESC").fetchall()
        flagged_query = conn.execute("SELECT DISTINCT profile_number FROM daily_schedule WHERE schedule_date = ? AND is_unscheduled = 1", (today_str,)).fetchall()
        scheduled_query = conn.execute("SELECT DISTINCT profile_number FROM daily_schedule WHERE schedule_date = ?", (today_str,)).fetchall()
        
    return render_template('receiving_log.html', pending_trucks=pending_trucks, flagged_profiles=[row['profile_number'] for row in flagged_query], scheduled_profiles=[row['profile_number'] for row in scheduled_query], today_str=today_str)

@receiving_bp.route('/api/search_profiles')
def search_profiles():
    query = request.args.get('q', '').strip() 
    with closing(get_db_connection()) as conn:
        results = conn.execute('SELECT profile_number FROM profiles WHERE profile_number LIKE ? LIMIT 50', ('%' + query + '%',)).fetchall()
    return jsonify([{'value': row['profile_number'], 'text': row['profile_number']} for row in results])

@receiving_bp.route('/api/get_profile_details/<path:profile_number>')
def get_profile_details(profile_number):
    clean_profile = profile_number.strip().upper()
    
    with closing(get_db_connection()) as conn:
        profile = conn.execute('SELECT * FROM profiles WHERE TRIM(UPPER(profile_number)) = ?', (clean_profile,)).fetchone()
    
    if profile:
        p_dict = dict(profile)
        
        voc_raw = str(p_dict.get('voc_percentage', '')).strip()
        if voc_raw in ['?', '', 'None']:
            p_dict['voc_percentage'] = 'TBD'
        else:
            p_dict['voc_percentage'] = voc_raw
            
        exp_raw = str(p_dict.get('expiration_date', '')).strip().lower()
        if exp_raw == 'none':
            p_dict['expiration_date'] = ''
            
        notes = p_dict.get('special_handling') or ""
        notes = re.sub(r'(?i)\bNOT CERCLA\b', '', notes)
        notes = re.sub(r'(?i)\bCERCLA\b', '', notes)
        notes = re.sub(r'^[,\s]+|[,\s]+$', '', notes) 
        p_dict['special_handling'] = notes.strip()
        
        gen = p_dict.get('generator')
        p_dict['generator'] = str(gen).strip() if gen and str(gen).strip().lower() != 'none' else ''
            
        return jsonify(p_dict)
        
    return jsonify({'error': 'Profile not found'}), 404

@receiving_bp.route('/api/check_truck_duplicate')
def check_truck_duplicate():
    manifest = request.args.get('manifest', '').strip()
    load = request.args.get('load', '').strip()
    check_date = request.args.get('date', date.today().isoformat())

    with closing(get_db_connection()) as conn:
        existing = conn.execute('''
            SELECT load_number, manifest_number 
            FROM truck_logs 
            WHERE date_received = ? AND test_status != 'REJECTED' 
            AND (manifest_number = ? OR load_number = ?)
        ''', (check_date, manifest, load)).fetchone()

        if existing:
            return jsonify({'exists': True, 'load': existing['load_number'], 'manifest': existing['manifest_number']})
        return jsonify({'exists': False})

@receiving_bp.route('/submit_truck', methods=['POST'])
def submit_truck():
    # We removed truck_id from the UI, so we default it to empty string to prevent errors
    truck_id = request.form.get('truck_id', '') 
    profile_number = request.form.get('profile_number', '').strip().upper()
    manifest_number = request.form.get('manifest_number')
    load_number = request.form.get('load_number')
    
    try: gross_weight = float(request.form.get('gross_weight', 0))
    except: gross_weight = 0.0
    
    received_date = request.form.get('date_received')
    if not received_date:
        received_date = date.today().isoformat()
        
    # Capture the exact check-in time
    time_in = datetime.now().strftime('%H:%M')
    
    with closing(get_db_connection()) as conn:
        profile = conn.execute('SELECT * FROM profiles WHERE profile_number = ?', (profile_number,)).fetchone()
        previous_trucks = conn.execute('SELECT COUNT(*) FROM truck_logs WHERE profile_number = ?', (profile_number,)).fetchone()[0]
        
        # Auto-fetch the Sales Order from today's schedule
        schedule_entry = conn.execute('''
            SELECT sales_order FROM daily_schedule 
            WHERE profile_number = ? AND schedule_date = ?
        ''', (profile_number, received_date)).fetchone()
        sales_order = schedule_entry['sales_order'] if schedule_entry else 'UNSCHEDULED'
        
        test_assigned = 'FINGERPRINT' 
        try: voc_percentage = float(profile['voc_percentage']) if profile and profile['voc_percentage'] is not None else 0.0
        except: voc_percentage = 0.0

        is_las_profile = False
        is_asbestos = False
        
        if profile:
            p_dict = dict(profile)
            win_code = str(p_dict.get('win_code', '')).strip().upper()
            
            if 'CNIA' in win_code or 'CNIA' in profile_number:
                is_asbestos = True
                
            # --- PURE EXPIRATION DATE & STATUS LAS LOGIC ---
            raw_exp = str(p_dict.get('expiration_date') or '').strip().lower()
            clean_exp = re.sub(r'[^a-z0-9]', '', raw_exp)
            prof_status = str(p_dict.get('status') or '').strip().upper()
            
            is_las_profile = False
            
            # 1. No Date -> Check the Status!
            if clean_exp in ['nodate', '', 'blank']:
                if prof_status == 'A':
                    is_las_profile = False  # Active + No Date = Safe
                else:
                    is_las_profile = True   # Not Active + No Date = LAS
                    
            # 2. "None" -> Safe, never expires
            elif clean_exp in ['none', 'nan', 'nat', 'null', 'na', 'tbd', '0', 'false']:
                is_las_profile = False
                
            # 3. Has a Date -> Expired Date ALWAYS triggers LAS
            else:
                if any(char.isdigit() for char in raw_exp):
                    try:
                        import pandas as pd
                        exp_date = pd.to_datetime(raw_exp, errors='coerce')
                        if pd.notna(exp_date) and exp_date < datetime.now():
                            is_las_profile = True
                    except: 
                        pass

        if is_asbestos or profile_number == 'BLCBPNONEB':
            is_las_profile = False

        if is_las_profile and previous_trucks == 0:
            test_assigned = 'LAS'

        if voc_percentage >= 50:
            if (previous_trucks + 1) % 10 == 0: test_assigned += " + VOC TEST"
            
        # Added sales_order and time_in to the INSERT statement
        conn.execute('''
            INSERT INTO truck_logs (
                truck_id, profile_number, manifest_number, load_number, 
                gross_weight, test_assigned, test_status, date_received, 
                sales_order, time_in
            )
            VALUES (?, ?, ?, ?, ?, ?, 'WEIGHED IN', ?, ?, ?)
        ''', (truck_id, profile_number, manifest_number, load_number, gross_weight, test_assigned, received_date, sales_order, time_in))
        conn.commit()
        
    return redirect(url_for('receiving_bp.home'))

@receiving_bp.route('/checkout_truck', methods=['POST'])
def checkout_truck():
    log_id = request.form.get('log_id')
    extra_fees_list = request.form.getlist('extra_fees')
    extra_fees = ", ".join(extra_fees_list) if extra_fees_list else "None"
    
    # NEW: Capture the exact check-out time
    time_out = datetime.now().strftime('%H:%M')
    
    try:
        exit_wt_raw = request.form.get('exit_weight', '0').replace(',', '')
        exit_weight = float(exit_wt_raw) if exit_wt_raw.strip() != '' else 0.0
        man_wt_raw = request.form.get('manifest_weight', '0').replace(',', '')
        manifest_weight = float(man_wt_raw) if man_wt_raw.strip() != '' else 0.0
    except: exit_weight, manifest_weight = 0.0, 0.0

    cell_location = request.form.get('cell_location', '')
    grid_location = request.form.get('grid_location', '')
    manifest_units = request.form.get('manifest_units', 'Pounds')

    with closing(get_db_connection()) as conn:
        truck = conn.execute('SELECT gross_weight FROM truck_logs WHERE id = ?', (log_id,)).fetchone()
        gross_weight = float(truck['gross_weight']) if truck and truck['gross_weight'] else 0.0
        
        # Keeps your critical weight validation!
        if exit_weight >= gross_weight and exit_weight > 0: 
            return "Critical Error: Tare weight cannot be greater than or equal to Gross weight.", 400
            
        net_weight_tons = (gross_weight - exit_weight) / 2000.0
        
        # NEW: Added time_out to the UPDATE statement
        conn.execute('''
            UPDATE truck_logs 
            SET exit_weight = ?, net_weight = ?, cell_location = ?, grid_location = ?,
                manifest_weight = ?, manifest_units = ?, extra_fees = ?, test_status = 'COMPLETED', time_out = ? 
            WHERE id = ?
        ''', (exit_weight, net_weight_tons, cell_location, grid_location, manifest_weight, manifest_units, extra_fees, time_out, log_id))
        conn.commit()
        
    return redirect(url_for('receiving_bp.home'))

@receiving_bp.route('/reject_truck', methods=['POST'])
def reject_truck():
    with closing(get_db_connection()) as conn:
        conn.execute("UPDATE truck_logs SET exit_weight = NULL, net_weight = 0, test_status = 'REJECTED', rejection_reason = ? WHERE id = ?", (request.form.get('rejection_reason').strip(), request.form.get('log_id')))
        conn.commit()
    return redirect(url_for('receiving_bp.home'))

@receiving_bp.route('/edit_truck/<int:log_id>', methods=['POST'])
def edit_truck(log_id):
    manifest_number = request.form.get('manifest_number', '').strip()
    load_number = request.form.get('load_number', '').strip()
    profile_number = request.form.get('profile_number', '').strip().upper()
    time_in = request.form.get('time_in', '').strip()
    time_out = request.form.get('time_out', '').strip()
    
    try: gross_weight = float(request.form.get('gross_weight', '0').replace(',', ''))
    except: gross_weight = 0.0
    
    source = request.form.get('source', 'reports')
    
    with closing(get_db_connection()) as conn:
        if source == 'receiving':
            # --- ACTIVE TRUCK EDIT (RECEIVING SCREEN) ---
            conn.execute('''
                UPDATE truck_logs 
                SET manifest_number = ?, load_number = ?, profile_number = ?, gross_weight = ?, time_in = ?
                WHERE id = ?
            ''', (manifest_number, load_number, profile_number, gross_weight, time_in, log_id))
            conn.commit()
            return redirect(url_for('receiving_bp.home'))
            
        else:
            # --- COMPLETED TRUCK EDIT (REPORTS SCREEN) ---
            exit_weight_raw = request.form.get('exit_weight', '0')
            try: exit_weight = float(exit_weight_raw.replace(',', '')) if exit_weight_raw.strip() else 0.0
            except: exit_weight = 0.0
            
            cell_location = request.form.get('cell_location', '')
            grid_location = request.form.get('grid_location', '')
            date_received = request.form.get('date_received', date.today().isoformat())
            
            net_weight_tons = (gross_weight - exit_weight) / 2000.0 if exit_weight > 0 else 0.0
            
            # Extract lab values
            specific_gravity = request.form.get('specific_gravity')
            measured_ph = request.form.get('measured_ph')
            measured_flashpoint = request.form.get('measured_flashpoint', '')
            measured_sulfides = request.form.get('measured_sulfides', '')
            measured_cyanide = request.form.get('measured_cyanide', '')
            measured_free_liquids = request.form.get('measured_free_liquids', '')
            
            try:
                specific_gravity = float(specific_gravity) if specific_gravity else None
            except ValueError:
                specific_gravity = None
                
            try:
                measured_ph = float(measured_ph) if measured_ph else None
            except ValueError:
                measured_ph = None
                
            conn.execute('''
                UPDATE truck_logs 
                SET manifest_number = ?, load_number = ?, profile_number = ?, 
                    gross_weight = ?, exit_weight = ?, net_weight = ?, cell_location = ?, grid_location = ?,
                    time_in = ?, time_out = ?,
                    specific_gravity = ?, measured_ph = ?, measured_flashpoint = ?,
                    measured_sulfides = ?, measured_cyanide = ?, measured_free_liquids = ?
                WHERE id = ?
            ''', (manifest_number, load_number, profile_number, gross_weight, exit_weight, net_weight_tons, 
                  cell_location, grid_location, time_in, time_out, 
                  specific_gravity, measured_ph, measured_flashpoint, 
                  measured_sulfides, measured_cyanide, measured_free_liquids, log_id))
            conn.commit()
            return redirect(url_for('reports_bp.reports', date=date_received))

@receiving_bp.route('/delete_truck/<int:log_id>', methods=['POST'])
def delete_truck(log_id):
    date_received = request.form.get('date_received', date.today().isoformat())
    with closing(get_db_connection()) as conn:
        conn.execute('DELETE FROM truck_logs WHERE id = ?', (log_id,))
        conn.commit()
    return redirect(url_for('reports_bp.reports', date=date_received))
