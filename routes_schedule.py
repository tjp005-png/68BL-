from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from datetime import date, datetime, timedelta

import time
import re
from contextlib import closing

# Import your database helper and utility functions
from database import get_db_connection 
from schedule_utils import calculate_las_status, clean_display_notes

# Create the Blueprint
schedule_bp = Blueprint('schedule_bp', __name__)

# Import shared multi-user sync tracker
from shared_state import SCHEDULE_UPDATES


# ---------------------------------------------------------
# SCHEDULE ROUTES
# ---------------------------------------------------------

@schedule_bp.route('/schedule')
def schedule_portal():
    selected_date = request.args.get('date', date.today().isoformat())
    with closing(get_db_connection()) as conn:
        
        # ONE Database Trip!
        daily_loads_raw = conn.execute('''
            SELECT ds.*, p.expiration_date, p.status AS profile_status, p.special_handling 
            FROM daily_schedule ds
            LEFT JOIN profiles p ON ds.profile_number = p.profile_number
            WHERE ds.schedule_date = ? 
            ORDER BY ds.order_index ASC, ds.id ASC
        ''', (selected_date,)).fetchall()
        
    daily_loads = []
    total_loads = 0 # <--- Start counter at 0
    
    for row in daily_loads_raw:
        load = dict(row)
        
        # --- EFFICIENCY: Add to total_loads in memory ---
        try: 
            total_loads += int(load.get('load_count') or 1)
        except: 
            total_loads += 1
        
        # --- Clean Logic ---
        load['is_las'] = calculate_las_status(load)
        load['clean_notes'] = clean_display_notes(load.get('special_notes'))
        
        voc_val = str(load.get('voc_level', '')).strip()
        load['voc_level'] = 'TBD' if voc_val in ['None', '', '?', 'TBD'] else voc_val
        
        daily_loads.append(load)

    standard_loads = []
    blcb_loads = []
    
    for load in daily_loads:
        try: load['order_index'] = int(load.get('order_index') or 0)
        except: load['order_index'] = 0
            
        try: load['id'] = int(load.get('id') or 0)
        except: load['id'] = 0
        
        try: load['load_count'] = int(load.get('load_count') or 1)
        except: load['load_count'] = 1

        try: load['is_pinned'] = int(load.get('is_pinned') or 0)
        except: load['is_pinned'] = 0

        if str(load['profile_number']).strip().upper() == 'BLCBPNONEB':
            blcb_loads.append(load)
        else:
            standard_loads.append(load)

    # --- BULLETPROOF SORT SEQUENCE ---
    standard_loads.sort(key=lambda x: (
        x['order_index'] != 0,  # 1. Inbox rows (0) stay locked on top
        not x.get('is_las', False) if x['order_index'] == 0 else x['order_index'], # 2. Inbox defaults to LAS first
        -x['is_pinned'],        # 3. Pinned profiles are anchored stably above unpinned items
        -x['load_count'] if x['order_index'] == 0 else 0, # 4. Secondary sort by load count
        -x['id']                # 5. Fallback safety
    ))

    return render_template('schedule.html', daily_loads=standard_loads, blcb_loads=blcb_loads, selected_date=selected_date, total_loads=total_loads)


@schedule_bp.route('/update_schedule_order', methods=['POST'])
def update_schedule_order():
    order_data = request.json.get('order', [])
    date_str = request.args.get('date')
    new_timestamp = time.time()
    
    with closing(get_db_connection()) as conn:
        for item in order_data:
            # Shift order by 1 to lock it into Bucket A (Pinned)
            new_order = item['order'] + 1 
            conn.execute('UPDATE daily_schedule SET order_index = ? WHERE id = ?', (new_order, item['id']))
            
        conn.commit()
        
    if date_str:
        SCHEDULE_UPDATES[date_str] = new_timestamp
    SCHEDULE_UPDATES['GLOBAL'] = new_timestamp
        
    return jsonify({
        'status': 'success',
        'new_date_timestamp': new_timestamp,
        'new_global_timestamp': new_timestamp
    })


@schedule_bp.route('/toggle_pin/<int:schedule_id>', methods=['POST'])
def toggle_pin(schedule_id):
    date_str = request.args.get('date')
    with closing(get_db_connection()) as conn:
        row = conn.execute('SELECT is_pinned FROM daily_schedule WHERE id = ?', (schedule_id,)).fetchone()
        if row:
            # Flip it: if 1 make it 0, if 0 make it 1
            new_status = 0 if row['is_pinned'] == 1 else 1
            conn.execute('UPDATE daily_schedule SET is_pinned = ? WHERE id = ?', (new_status, schedule_id))
            conn.commit()

    if date_str: SCHEDULE_UPDATES[date_str] = time.time()
    SCHEDULE_UPDATES['GLOBAL'] = time.time()
    return jsonify({'status': 'success'})


@schedule_bp.route('/clear_all_pins', methods=['POST'])
def clear_all_pins():
    date_str = request.form.get('schedule_date')
    if date_str:
        with closing(get_db_connection()) as conn:
            # Set everyone back to Unpinned (0) for the day
            conn.execute('UPDATE daily_schedule SET is_pinned = 0 WHERE schedule_date = ?', (date_str,))
            conn.commit()
            
        SCHEDULE_UPDATES[date_str] = time.time()
        SCHEDULE_UPDATES['GLOBAL'] = time.time()
        
    return redirect(url_for('schedule_bp.schedule_portal', date=date_str))


@schedule_bp.route('/add_schedule', methods=['POST'])
def add_schedule():
    start_date_str = request.form.get('schedule_date')     
    
    # 1. Handle the Multi-Day Repeating Checkboxes
    repeat_weeks = int(request.form.get('repeat_weeks', 1))
    repeat_days = request.form.getlist('repeat_days') # e.g., ['0', '1'] for Mon/Tue
    
    final_dates = []
    if repeat_days and start_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        # Find the Monday of the starting week
        monday = start_date - timedelta(days=start_date.weekday())
        
        for week in range(repeat_weeks):
            for day_offset in repeat_days:
                target_date = monday + timedelta(weeks=week, days=int(day_offset))
                # Only add if it occurs on or after the selected start date
                if target_date >= start_date:
                    final_dates.append(target_date.strftime('%Y-%m-%d'))
    else:
        # Standard Single Day
        final_dates = [start_date_str]

    if not final_dates:
        return redirect(url_for('schedule_bp.schedule_portal'))

    with closing(get_db_connection()) as conn:
        for date_str in final_dates:
            conn.execute('''
                INSERT INTO daily_schedule (
                    schedule_date, start_time, end_time, profile_number, load_count, 
                    generator, waste_type, sales_order, routing_code, scheduler_initials, special_notes, voc_level, order_index, is_pinned
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
            ''', (
                date_str, 'TBD', 'TBD', 
                request.form.get('profile_number'), int(request.form.get('load_count', 1)),
                request.form.get('generator'), request.form.get('waste_type', 'WASTE PICKUP'), # <-- FIXED: Now correctly pulls 'generator'
                request.form.get('sales_order'), request.form.get('routing_code'),
                request.form.get('scheduler_initials'), request.form.get('special_notes'),
                request.form.get('voc_level', 0)
            ))
        conn.commit()
        
    return redirect(url_for('schedule_bp.schedule_portal', date=start_date_str))


@schedule_bp.route('/delete_schedule/<int:schedule_id>', methods=['POST'])
def delete_schedule(schedule_id):
    apply_to_series = request.form.get('apply_to_series')
    schedule_date = request.form.get('schedule_date')
    
    with closing(get_db_connection()) as conn:
        if apply_to_series:
            row = conn.execute("SELECT series_id FROM daily_schedule WHERE id = ?", (schedule_id,)).fetchone()
            if row and row['series_id']:
                conn.execute("DELETE FROM daily_schedule WHERE series_id = ?", (row['series_id'],))
                SCHEDULE_UPDATES['GLOBAL'] = time.time()
            else:
                conn.execute("DELETE FROM daily_schedule WHERE id = ?", (schedule_id,))
        else:
            conn.execute("DELETE FROM daily_schedule WHERE id = ?", (schedule_id,))
            
        conn.commit()
        
    if schedule_date:
        SCHEDULE_UPDATES[schedule_date] = time.time()
        
    return redirect(url_for('schedule_bp.schedule_portal', date=schedule_date))


@schedule_bp.route('/edit_schedule/<int:id>', methods=['POST'])
def edit_schedule(id):
    new_date = request.form.get('schedule_date')
    notes = request.form.get('special_notes', '')
    initials = request.form.get('scheduler_initials', '')
    voc_level = request.form.get('voc_level', 'TBD') 
    apply_to_series = request.form.get('apply_to_series') == 'on'
    
    profile_number = request.form.get('profile_number', '').upper()
    waste_type = request.form.get('waste_type', '')
    sales_order = request.form.get('sales_order', '')
    
    try: load_count = int(request.form.get('load_count') or 1)
    except: load_count = 1

    with closing(get_db_connection()) as conn:
        old_entry = conn.execute('SELECT schedule_date, series_id, order_index FROM daily_schedule WHERE id = ?', (id,)).fetchone()
        fallback_date = old_entry['schedule_date'] if old_entry else date.today().isoformat()
        
        if not new_date:
            new_date = fallback_date

        new_order_index = old_entry['order_index'] if (old_entry and new_date == fallback_date) else 0

        if apply_to_series and old_entry and old_entry['series_id']:
            conn.execute('''
                UPDATE daily_schedule 
                SET special_notes = ?, scheduler_initials = ?, voc_level = ?, profile_number = ?, load_count = ?, waste_type = ?, sales_order = ?, order_index = ?
                WHERE series_id = ?
            ''', (notes, initials, voc_level, profile_number, load_count, waste_type, sales_order, new_order_index, old_entry['series_id']))
            
            conn.execute('''
                UPDATE daily_schedule 
                SET schedule_date = ?, order_index = ?
                WHERE id = ?
            ''', (new_date, new_order_index, id))
        else:
            conn.execute('''
                UPDATE daily_schedule 
                SET schedule_date = ?, special_notes = ?, scheduler_initials = ?, voc_level = ?, profile_number = ?, load_count = ?, waste_type = ?, sales_order = ?, order_index = ?
                WHERE id = ?
            ''', (new_date, notes, initials, voc_level, profile_number, load_count, waste_type, sales_order, new_order_index, id))
            
        SCHEDULE_UPDATES['GLOBAL'] = time.time()
        SCHEDULE_UPDATES[new_date] = time.time()
        if new_date != fallback_date:
            SCHEDULE_UPDATES[fallback_date] = time.time() 
            
        conn.commit()

    return redirect(url_for('schedule_bp.schedule_portal', date=new_date))


@schedule_bp.route('/reset_schedule_sort', methods=['POST'])
def reset_schedule_sort():
    # Support both Javascript (Fetch) and standard Form data
    if request.is_json:
        date_str = request.json.get('schedule_date')
    else:
        date_str = request.form.get('schedule_date')
        
    if date_str:
        with closing(get_db_connection()) as conn:
            daily_loads_raw = conn.execute('''
                SELECT ds.id, ds.load_count, ds.profile_number, ds.routing_code, ds.order_index, ds.is_pinned, p.expiration_date, p.status AS profile_status 
                FROM daily_schedule ds
                LEFT JOIN profiles p ON ds.profile_number = p.profile_number
                WHERE ds.schedule_date = ? 
            ''', (date_str,)).fetchall()
            
            daily_loads = []
            for row in daily_loads_raw:
                t = dict(row)
                try: t['load_count'] = int(t.get('load_count') or 1)
                except: t['load_count'] = 1
                try: t['order_index'] = int(t.get('order_index') or 0)
                except: t['order_index'] = 0
                
                # Use our clean utility function
                t['is_las'] = calculate_las_status(t)
                daily_loads.append(t)
                
            # 1. Sort to match the EXACT current visual layout on screen
            daily_loads.sort(key=lambda x: (
                x['order_index'] != 0, 
                not x.get('is_las', False) if x['order_index'] == 0 else x['order_index'],
                -x.get('is_pinned', 0),
                -x['load_count'] if x['order_index'] == 0 else 0,
                -x['id']
            ))
            
            # 2. Extract ONLY the slots that contain unpinned items
            unpinned_slots = []
            unpinned_items = []
            for idx, t in enumerate(daily_loads):
                if t.get('is_pinned') == 0:
                    unpinned_slots.append(idx)
                    unpinned_items.append(t)
            
            # 3. Math-Sort the unpinned items (LAS first, then Load Count)
            unpinned_items.sort(key=lambda x: (not x['is_las'], -x['load_count']))
            
            # 4. Put them back into their original empty slots (Pinned items never move!)
            for i, slot_idx in enumerate(unpinned_slots):
                daily_loads[slot_idx] = unpinned_items[i]
            
            # 5. Lock in the new exact visual order
            for index, truck in enumerate(daily_loads, start=1):
                conn.execute('UPDATE daily_schedule SET order_index = ? WHERE id = ?', (index, truck['id']))
                
            conn.commit()
        
        SCHEDULE_UPDATES[date_str] = time.time()
        SCHEDULE_UPDATES['GLOBAL'] = time.time()
        
    # If the request came from our new Javascript button, send JSON back
    if request.is_json:
        return jsonify({'status': 'success'})
        
    # Fallback for old forms
    return redirect(url_for('schedule_bp.schedule_portal', date=date_str))


@schedule_bp.route('/toggle_unscheduled/<int:id>', methods=['POST'])
def toggle_unscheduled(id):
    date_str = request.form.get('schedule_date')
    
    with closing(get_db_connection()) as conn:
        row = conn.execute('SELECT is_unscheduled FROM daily_schedule WHERE id = ?', (id,)).fetchone()
        
        if row:
            new_status = 0 if row['is_unscheduled'] == 1 else 1
            conn.execute('UPDATE daily_schedule SET is_unscheduled = ? WHERE id = ?', (new_status, id))
            conn.commit()

    if date_str:
        SCHEDULE_UPDATES[date_str] = time.time()
    SCHEDULE_UPDATES['GLOBAL'] = time.time()

    return redirect(url_for('schedule_bp.schedule_portal', date=date_str))


@schedule_bp.route('/api/search_schedule')
def search_schedule():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify([])

    with closing(get_db_connection()) as conn:
        results = conn.execute('''
            SELECT schedule_date, profile_number, sales_order, generator, load_count
            FROM daily_schedule
            WHERE profile_number LIKE ? OR sales_order LIKE ? OR generator LIKE ?
            ORDER BY schedule_date DESC
            LIMIT 50
        ''', (f'%{query}%', f'%{query}%', f'%{query}%')).fetchall()

    return jsonify([dict(r) for r in results])


@schedule_bp.route('/api/check_schedule_duplicate', methods=['POST'])
def check_schedule_duplicate():
    profile_number = request.json.get('profile_number', '').strip().upper()
    selected_dates_raw = request.json.get('selected_dates', '')
    
    dates = [d.strip() for d in selected_dates_raw.split(',') if d.strip()]
    if not dates and request.json.get('schedule_date'):
        dates = [request.json.get('schedule_date')]
        
    with closing(get_db_connection()) as conn:
        for d in dates:
            # STRICT PROFILE LEVEL CHECK
            dup = conn.execute('''
                SELECT id FROM daily_schedule 
                WHERE schedule_date = ? AND UPPER(profile_number) = ?
            ''', (d, profile_number)).fetchone()
            
            if dup:
                return jsonify({'duplicate': True, 'date': d})
                
    return jsonify({'duplicate': False})


@schedule_bp.route('/refresh_schedule_data', methods=['POST'])
def refresh_schedule_data():
    date_str = request.form.get('schedule_date')
    
    if date_str:
        with closing(get_db_connection()) as conn:
            # 1. Grab all scheduled loads for the current view
            schedules = conn.execute('SELECT id, profile_number FROM daily_schedule WHERE schedule_date = ?', (date_str,)).fetchall()
            
            for s in schedules:
                prof_num = str(s['profile_number']).strip().upper()
                
                # 2. Look up the latest data in the Master Profiles table
                prof = conn.execute('''
                    SELECT voc_percentage, generator, win_code 
                    FROM profiles 
                    WHERE TRIM(UPPER(profile_number)) = ?
                ''', (prof_num,)).fetchone()
                
                if prof:
                    # 3. Safely parse the updated VOC using our new logic
                    try:
                        val = float(prof['voc_percentage'])
                        new_voc = str(int(val)) if val > 0 else '0'
                    except (ValueError, TypeError):
                        new_voc = 'TBD'
                        
                    # 4. Update the existing schedule entry
                    conn.execute('''
                        UPDATE daily_schedule 
                        SET voc_level = ?, generator = ?, routing_code = ?
                        WHERE id = ?
                    ''', (new_voc, prof['generator'], prof['win_code'], s['id']))
                    
            conn.commit()
            
        # 5. Push the visual update to all connected users
        SCHEDULE_UPDATES[date_str] = time.time()
        SCHEDULE_UPDATES['GLOBAL'] = time.time()
        
    return redirect(url_for('schedule_bp.schedule_portal', date=date_str))


@schedule_bp.route('/api/check_schedule_updates')
def check_schedule_updates():
    check_date = request.args.get('date')
    return jsonify({
        'date_updated': SCHEDULE_UPDATES.get(check_date, 0),
        'global_updated': SCHEDULE_UPDATES.get('GLOBAL', 0)
    })