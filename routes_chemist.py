from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from datetime import date
from contextlib import closing
import re
import os
import pandas as pd
from collections import defaultdict
from database import get_db_connection, sync_profile_from_wvi_file
from shared_state import socketio


chemist_bp = Blueprint('chemist_bp', __name__)

@chemist_bp.route('/chemist')
def chemist_dashboard():
    with closing(get_db_connection()) as conn:
        pending_lab_trucks = conn.execute('''
            SELECT tl.*, 
                   w.ph_min, w.ph_max, w.sulfides, w.cyanide, w.free_liquids, w.flashpoint, w.voc_ppm, w.color AS w_color,
                   w.treatment_information, w.notes_revisions, w.physical_description, w.handling_instruction,
                   w.generator_name, w.waste_name, w.approved_date, w.expiration_date, w.is_synced,
                   p.win_code, p.generator AS p_generator, p.waste_description AS p_waste_description,
                   p.ph_range AS p_ph_range, p.flash_point AS p_flash_point, p.voc_percentage AS p_voc_percentage,
                   p.special_handling AS p_special_handling, p.cyanide AS p_cyanide, p.sulfide AS p_sulfide,
                   p.free_liquids AS p_free_liquids, p.physical_appearance AS p_physical_appearance,
                   p.treatment_recipe AS p_treatment_recipe, p.color AS p_color
            FROM truck_logs tl
            LEFT JOIN profile_wvi w ON TRIM(UPPER(tl.profile_number)) = TRIM(UPPER(w.profile))
            LEFT JOIN profiles p ON TRIM(UPPER(tl.profile_number)) = TRIM(UPPER(p.profile_number))
            WHERE (tl.test_assigned LIKE '%FINGERPRINT%' OR tl.test_assigned LIKE '%VOC TEST%' OR tl.test_assigned LIKE 'LAS%') 
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
                    
            # Fall back to MASTERPROFILE values if WVI-sourced fields are empty.
            if not truck.get('generator_name') and truck.get('p_generator'):
                truck['generator_name'] = truck['p_generator']
            if not truck.get('waste_name') and truck.get('p_waste_description'):
                truck['waste_name'] = truck['p_waste_description']
            if not truck.get('flashpoint') and truck.get('p_flash_point'):
                truck['flashpoint'] = truck['p_flash_point']
            
            # Resolve color: WVI data takes priority over MASTERPROFILE.
            truck['color'] = truck.get('w_color') or truck.get('p_color') or ''
            
            if truck.get('ph_min') is None and truck.get('ph_max') is None and truck.get('p_ph_range'):
                import re
                ph_str = str(truck['p_ph_range']).strip().upper()
                if "7 (NEUTRAL)" in ph_str or ph_str == "7" or "7 NEUTRAL" in ph_str:
                    truck['ph_min'] = 4.0
                    truck['ph_max'] = 10.0
                else:
                    m = re.findall(r'(\d+\.?\d*)', ph_str)
                    if len(m) >= 2:
                        try:
                            truck['ph_min'] = float(m[0])
                            truck['ph_max'] = float(m[1])
                        except:
                            pass
                    elif len(m) == 1:
                        try:
                            truck['ph_min'] = float(m[0])
                            truck['ph_max'] = float(m[0])
                        except:
                            pass
            if truck.get('voc_ppm') is None and truck.get('p_voc_percentage') is not None:
                try:
                    val = float(truck['p_voc_percentage'])
                    truck['voc_ppm'] = val
                except:
                    pass
            if not truck.get('sulfides') and truck.get('p_sulfide'):
                truck['sulfides'] = truck['p_sulfide']
            if not truck.get('cyanide') and truck.get('p_cyanide'):
                truck['cyanide'] = truck['p_cyanide']
            if not truck.get('free_liquids') and truck.get('p_free_liquids'):
                truck['free_liquids'] = truck['p_free_liquids']
            if not truck.get('physical_description') and truck.get('p_physical_appearance'):
                truck['physical_description'] = truck['p_physical_appearance']
            if not truck.get('treatment_information') and truck.get('p_treatment_recipe'):
                truck['treatment_information'] = truck['p_treatment_recipe']
            if not truck.get('handling_instruction') and truck.get('p_special_handling'):
                truck['handling_instruction'] = truck['p_special_handling']
                
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
    
    # Extract broken-out VOC fields
    measured_voc = request.form.get('measured_voc')
    voc_pass_fail = request.form.get('voc_pass_fail', 'N/A')
    
    # Handle optional float numbers safely
    try:
        specific_gravity = float(specific_gravity) if specific_gravity else None
    except ValueError:
        specific_gravity = None
        
    try:
        measured_ph = float(measured_ph) if measured_ph else None
    except ValueError:
        measured_ph = None

    try:
        measured_voc = float(measured_voc) if measured_voc else None
    except ValueError:
        measured_voc = None
        
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
                measured_voc = ?,
                voc_pass_fail = ?,
                test_status = 'LAB COMPLETED' 
            WHERE id = ?
        ''', (lab_results, specific_gravity, measured_ph, measured_flashpoint, 
              measured_sulfides, measured_cyanide, measured_free_liquids, 
              measured_voc, voc_pass_fail, log_id))
        conn.commit()
        socketio.emit('lab_update', {'date': received_date})
    return redirect(url_for('chemist_bp.chemist_dashboard'))

@chemist_bp.route('/chemist/drums')
def chemist_drums():
    with closing(get_db_connection()) as conn:
        # Fetch all active jobs with pending/received labs in the queue
        active_jobs = conn.execute('''
            SELECT DISTINCT job_id 
            FROM drum_lab_queue 
            WHERE status != 'FINAL CODED' AND status != 'COMPLETED'
        ''').fetchall()
        
        jobs_list = []
        for row in active_jobs:
            job_id = row['job_id']
            if not job_id: continue
            
            # Fetch count of pending samples and received samples
            total_samples = conn.execute("SELECT COUNT(*) FROM drum_lab_queue WHERE job_id = ?", (job_id,)).fetchone()[0]
            pending_samples = conn.execute("SELECT COUNT(*) FROM drum_lab_queue WHERE job_id = ? AND status = 'PENDING'", (job_id,)).fetchone()[0]
            received_samples = conn.execute("SELECT COUNT(*) FROM drum_lab_queue WHERE job_id = ? AND status = 'RECEIVED'", (job_id,)).fetchone()[0]
            
            jobs_list.append({
                'job_id': job_id,
                'total_samples': total_samples,
                'pending_samples': pending_samples,
                'received_samples': received_samples
            })
            
    return render_template('chemist_drums.html', jobs=jobs_list)

@chemist_bp.route('/api/chemist/check_in_drum', methods=['POST'])
def api_chemist_check_in_drum():
    data = request.get_json() or {}
    drum_id = data.get('drum_id', '').strip().upper()
    
    if not drum_id:
        return jsonify({'error': 'Missing Drum ID'}), 400
        
    with closing(get_db_connection()) as conn:
        # Check if the drum exists in the lab queue and is pending check-in
        drum = conn.execute('''
            SELECT * FROM drum_lab_queue 
            WHERE TRIM(UPPER(drum_id)) = ? AND status = 'PENDING'
        ''', (drum_id,)).fetchone()
        
        if not drum:
            return jsonify({'error': f'Drum {drum_id} not found or already checked in.'}), 404
            
        conn.execute('''
            UPDATE drum_lab_queue 
            SET status = 'RECEIVED' 
            WHERE id = ?
        ''', (drum['id'],))
        conn.commit()
        
        # Notify all connected clients that this drum job has been updated.
        socketio.emit('drum_update', {'job_id': drum['job_id']})
        
    return jsonify({'success': True, 'job_id': drum['job_id']})

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

@chemist_bp.route('/chemist/drums/bulk/<job_id>')
def chemist_drums_bulk(job_id):
    with closing(get_db_connection()) as conn:
        drums = conn.execute('''
            SELECT q.*, 
                   w.ph_min, w.ph_max, w.sulfides as wvi_sulfides, w.cyanide as wvi_cyanide, 
                   w.free_liquids as wvi_free_liquids, w.flashpoint as wvi_flashpoint, 
                   w.voc_ppm as wvi_voc_ppm
            FROM drum_lab_queue q
            LEFT JOIN profile_wvi w ON TRIM(UPPER(q.profile)) = TRIM(UPPER(w.profile))
            WHERE q.job_id = ?
        ''', (job_id,)).fetchall()
        
    return render_template('chemist_drums_bulk.html', job_id=job_id, drums=drums)

@chemist_bp.route('/chemist/drums/bulk/submit', methods=['POST'])
def chemist_drums_bulk_submit():
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
    return redirect(url_for('stu_bp.stu_hub', view='pipeline'))


# --- YELLOW ENTRY LOG ---

@chemist_bp.route('/yellow_entry')
@chemist_bp.route('/chemist/yellow_entry')
def yellow_entry():
    selected_date = request.args.get('date', date.today().isoformat()).strip()
    sort_mode = request.args.get('sort', 'entry').strip()
    
    with closing(get_db_connection()) as conn:
        logs_raw = conn.execute('''
            SELECT * FROM truck_logs 
            WHERE date_received = ?
            ORDER BY id DESC
        ''', (selected_date,)).fetchall()
        
        logs = [dict(r) for r in logs_raw]
        
        # Batch lookup profile details & testing approvals for distinct profile numbers
        profile_nums = list({str(log.get('profile_number') or '').strip().upper() for log in logs if log.get('profile_number')})
        profile_map = {}
        wa_map = {}
        if profile_nums:
            placeholders = ','.join(['?'] * len(profile_nums))
            p_rows = conn.execute(f'''
                SELECT profile_number, generator, win_code, waste_description, shipping_container_type, status, expiration_date, special_handling, voc_percentage
                FROM profiles
                WHERE TRIM(UPPER(profile_number)) IN ({placeholders})
            ''', profile_nums).fetchall()
            for pr in p_rows:
                p_dict = dict(pr)
                key = str(p_dict.get('profile_number') or '').strip().upper()
                profile_map[key] = p_dict

            wa_rows = conn.execute(f'''
                SELECT profile_number, status 
                FROM waste_acceptance_log
                WHERE TRIM(UPPER(profile_number)) IN ({placeholders}) AND COALESCE(is_archived, 0) = 0
            ''', profile_nums).fetchall()
            for wa in wa_rows:
                key = str(wa['profile_number']).strip().upper()
                wa_map[key] = wa['status']

        from schedule_utils import calculate_las_tags, evaluate_untested_las

        for log in logs:
            p_key = str(log.get('profile_number') or '').strip().upper()
            p_info = profile_map.get(p_key, {})
            log['generator'] = p_info.get('generator')
            log['win_code'] = p_info.get('win_code')
            log['waste_description'] = p_info.get('waste_description')
            log['shipping_container_type'] = p_info.get('shipping_container_type')

            eval_load = {
                'profile_number': p_key,
                'routing_code': p_info.get('win_code', log.get('win_code', '')),
                'special_notes': log.get('notes', ''),
                'special_handling': p_info.get('special_handling', ''),
                'profile_status': p_info.get('status', ''),
                'expiration_date': p_info.get('expiration_date', ''),
                'voc_level': str(log.get('voc_percentage', '')),
                'profile_voc_percentage': p_info.get('voc_percentage')
            }
            las_tags = calculate_las_tags(eval_load)
            log['las_tags'] = las_tags

            wa_info = wa_map.get(p_key)
            is_void = str(log.get('test_status', '')).upper() in ['VOID', 'VOIDED']
            is_untested = evaluate_untested_las(las_tags, p_info, wa_info, measured_voc=log.get('measured_voc'))
            log['is_untested_las'] = is_untested and (not is_void)

        if sort_mode in ('ticket', 'ticket_asc'):
            def ticket_sort_key(log):
                tid = str(log.get('truck_id') or log.get('load_number') or '0').strip()
                digits = re.findall(r'\d+', tid)
                num = int(digits[0]) if digits else 0
                return (num, tid)
            logs.sort(key=ticket_sort_key)
        elif sort_mode == 'ticket_desc':
            def ticket_sort_key_desc(log):
                tid = str(log.get('truck_id') or log.get('load_number') or '0').strip()
                digits = re.findall(r'\d+', tid)
                num = int(digits[0]) if digits else 0
                return (num, tid)
            logs.sort(key=ticket_sort_key_desc, reverse=True)
        elif sort_mode == 'manifest':
            logs.sort(key=lambda log: str(log.get('manifest_number') or '').upper())
        
    error_msg = request.args.get('error', '').strip()
    return render_template('yellow_entry.html', 
                           logs=logs, 
                           selected_date=selected_date,
                           sort_mode=sort_mode,
                           error_msg=error_msg,
                           today_str=date.today().isoformat())


@chemist_bp.route('/submit_yellow_entry', methods=['POST'])
def submit_yellow_entry():
    from routes_receiving import submit_truck
    
    ticket_number = request.form.get('ticket_number', '').strip()
    weight_val = request.form.get('weight', '').strip()
    weight_unit = request.form.get('weight_unit', 'LBS').strip().upper()
    date_received = request.form.get('date_received', '').strip() or date.today().isoformat()
    sort_mode = request.form.get('sort_mode', 'entry').strip()
    
    try:
        weight_num = float(weight_val)
    except ValueError:
        weight_num = 0.0
        
    gross_lbs = weight_num * 2000.0 if weight_unit == 'TONS' else weight_num

    # Mutate request.form to include receiving log retroactive fields
    form_data = request.form.copy()
    form_data['truck_id'] = ticket_number
    form_data['load_number'] = ticket_number
    form_data['gross_weight'] = str(gross_lbs)
    form_data['exit_weight'] = '0'
    form_data['is_retroactive'] = 'true'
    form_data['container_type'] = request.form.get('container_type', 'End Dump')
    request.form = form_data
    
    res = submit_truck()
    if isinstance(res, tuple) and res[1] == 400:
        err_msg = res[0]
        return redirect(url_for('chemist_bp.yellow_entry', date=date_received, sort=sort_mode, error=err_msg))

    # Instant alert check for new Yellow Entry
    try:
        prof_num = str(request.form.get('profile_number', '')).strip().upper()
        if prof_num and ticket_number:
            from schedule_utils import calculate_las_tags, evaluate_untested_las
            with closing(get_db_connection()) as conn:
                p_info = conn.execute('SELECT * FROM profiles WHERE TRIM(UPPER(profile_number)) = ?', (prof_num,)).fetchone()
                wa_info = conn.execute('SELECT * FROM waste_acceptance_log WHERE TRIM(UPPER(profile_number)) = ? AND COALESCE(is_archived, 0) = 0', (prof_num,)).fetchone()

                p_dict = dict(p_info) if p_info else {}
                wa_dict = dict(wa_info) if wa_info else {}
                eval_load = {
                    'profile_number': prof_num,
                    'routing_code': request.form.get('routing_code', p_dict.get('win_code', '')),
                    'special_notes': request.form.get('notes', ''),
                    'special_handling': p_dict.get('special_handling', ''),
                    'profile_status': p_dict.get('status', ''),
                    'expiration_date': p_dict.get('expiration_date', ''),
                    'voc_level': str(request.form.get('voc_percentage', '')),
                    'profile_voc_percentage': p_dict.get('voc_percentage')
                }
                las_tags = calculate_las_tags(eval_load)
                is_untested = evaluate_untested_las(las_tags, p_dict, wa_dict, measured_voc=request.form.get('measured_voc'))

                if is_untested:
                    from email_utils import send_email_alert
                    subj = f"[LAS ALERT] UNTESTED LAS TRUCK RECEIVED - Profile {prof_num} (Ticket #{ticket_number})"
                    body = f"""UNTESTED LAS TRUCK RECEIVED AT SCALE

Ticket #: {ticket_number}
Manifest #: {request.form.get('manifest_number', 'N/A')}
Profile #: {prof_num}
Generator: {request.form.get('generator', p_dict.get('generator', 'N/A'))}
WIN Code: {request.form.get('routing_code', p_dict.get('win_code', 'N/A'))}
Date Received: {date_received}
LAS Tags: {', '.join(las_tags)}

Note: This truck requires Load Acceptance Sampling / profile recertification / approval verification before processing.
"""
                    send_email_alert(subj, body, recipients=['pereira.taylor@cleanharbors.com'])
    except Exception as alert_err:
        print(f"Error checking instant LAS alert: {alert_err}")
        
    return redirect(url_for('chemist_bp.yellow_entry', date=date_received, sort=sort_mode))


@chemist_bp.route('/edit_yellow_entry/<int:log_id>', methods=['POST'])
def edit_yellow_entry(log_id):
    ticket_number = request.form.get('ticket_number', '').strip()
    manifest_number = request.form.get('manifest_number', '').strip()
    profile_number = request.form.get('profile_number', '').strip().upper()
    weight_val = request.form.get('weight', '').strip()
    weight_unit = request.form.get('weight_unit', 'LBS').strip().upper()
    voc_val = request.form.get('voc_percentage', '').strip()
    date_received = request.form.get('date_received', '').strip() or date.today().isoformat()
    container_type = request.form.get('container_type', 'End Dump').strip()
    cell_location = request.form.get('cell_location', '').strip().upper()
    grid_location = request.form.get('grid_location', '').strip().upper()
    specific_gravity_val = request.form.get('specific_gravity', '').strip()
    sort_mode = request.form.get('sort_mode', 'entry').strip()
    
    load_number = ticket_number
    
    try:
        weight_num = float(weight_val)
    except ValueError:
        weight_num = 0.0
        
    gross_lbs = weight_num * 2000.0 if weight_unit == 'TONS' else weight_num
    net_tons = gross_lbs / 2000.0
        
    voc_num = 0.0
    if voc_val != '':
        try:
            voc_num = float(voc_val)
        except ValueError:
            voc_num = 0.0
            
    sg_num = None
    if specific_gravity_val:
        try:
            sg_num = float(specific_gravity_val)
        except ValueError:
            sg_num = None
            
    with closing(get_db_connection()) as conn:
        conn.execute('''
            UPDATE truck_logs 
            SET truck_id = ?, manifest_number = ?, profile_number = ?, load_number = ?,
                gross_weight = ?, net_weight = ?, date_received = ?, measured_voc = ?,
                container_type = ?, cell_location = ?, grid_location = ?, specific_gravity = ?
            WHERE id = ?
        ''', (ticket_number, manifest_number, profile_number, load_number,
              gross_lbs, net_tons, date_received, voc_num,
              container_type, cell_location, grid_location, sg_num, log_id))
        conn.commit()
        
    socketio.emit('truck_update', {'date': date_received})
    return redirect(url_for('chemist_bp.yellow_entry', date=date_received, sort=sort_mode))


@chemist_bp.route('/void_yellow_entry/<int:log_id>', methods=['POST'])
def void_yellow_entry(log_id):
    date_received = request.form.get('date_received', date.today().isoformat())
    sort_mode = request.form.get('sort_mode', 'entry').strip()
    with closing(get_db_connection()) as conn:
        current_status = conn.execute('SELECT test_status FROM truck_logs WHERE id = ?', (log_id,)).fetchone()
        if current_status and current_status['test_status'] == 'VOID':
            conn.execute("UPDATE truck_logs SET test_status = 'COMPLETED' WHERE id = ?", (log_id,))
        else:
            conn.execute("UPDATE truck_logs SET test_status = 'VOID' WHERE id = ?", (log_id,))
        conn.commit()
    socketio.emit('truck_update', {'date': date_received})
    return redirect(url_for('chemist_bp.yellow_entry', date=date_received, sort=sort_mode))


@chemist_bp.route('/delete_yellow_entry/<int:log_id>', methods=['POST'])
def delete_yellow_entry(log_id):
    date_received = request.form.get('date_received', date.today().isoformat())
    sort_mode = request.form.get('sort_mode', 'entry').strip()
    with closing(get_db_connection()) as conn:
        conn.execute('DELETE FROM truck_logs WHERE id = ?', (log_id,))
        conn.commit()
    socketio.emit('truck_update', {'date': date_received})
    return redirect(url_for('chemist_bp.yellow_entry', date=date_received, sort=sort_mode))

