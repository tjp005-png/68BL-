from flask import Blueprint, render_template, request, redirect, url_for, send_file
from datetime import date, datetime
from contextlib import closing
from io import BytesIO
import pandas as pd
from collections import defaultdict
from database import get_db_connection

reports_bp = Blueprint('reports_bp', __name__)

@reports_bp.route('/export')
def export_excel():
    selected_date = request.args.get('date', date.today().isoformat())

    with closing(get_db_connection()) as conn:
        # Pull all completed/weighed-out trucks for the selected date
        trucks_raw = conn.execute('''
            SELECT t.*, p.voc_percentage
            FROM truck_logs t
            LEFT JOIN profiles p ON t.profile_number = p.profile_number
            WHERE t.date_received = ? AND t.test_status != 'REJECTED' AND t.exit_weight IS NOT NULL
            ORDER BY CAST(t.load_number AS INTEGER) ASC
        ''', (selected_date,)).fetchall()

    # Lists to hold data for each specific Excel tab
    wmu35_data = []
    non_voc_data = []
    gallons_data = []

    for row in trucks_raw:
        t = dict(row)
        wmu = str(t.get('cell_location', '')).strip().upper()
        
        # Safely parse VOC
        try: voc = float(t.get('voc_percentage'))
        except (ValueError, TypeError): voc = 0.0
            
        # Safely calculate Net LBS from Gross and Tare
        try:
            gross = float(t.get('gross_weight') or 0)
            tare = float(t.get('exit_weight') or 0)
            lbs = gross - tare
        except ValueError:
            lbs = 0.0
            
        # Do the Tonnage and VOC math
        tons = lbs / 2000.0 if lbs else 0.0
        voc_x_wt = voc * tons
        
        # Build the standard base row (Removed EXTRA FEES & RECEIVED, moved Load # to front)
        base_row = {
            'Weighmaster Load No.': t.get('load_number', ''),
            'VOCs (ppm)': voc,
            'Weight (Pounds)': lbs,
            'Weight (TONs)': round(tons, 2),
            'VOCSxWt.': round(voc_x_wt, 2),
            'MANIFEST #': t.get('manifest_number', ''),
            'APPROVAL #': t.get('profile_number', ''),
            'WMU': wmu
        }

        # ---------------------------------------------------------
        # SHEET 1: WMU 35 VOC TRACKING (All 35 loads)
        # ---------------------------------------------------------
        if wmu.startswith('35'):
            wmu35_data.append(base_row.copy())
            
        # ---------------------------------------------------------
        # SHEET 2: DAILY NON-VOC'S (WMU 31, STU, BAY, and WMU 35 0-VOCs)
        # ---------------------------------------------------------
        # Added 'BAY' to the check list
        is_stu = wmu.startswith('34') or wmu.startswith('STU') or 'BAY' in wmu or wmu in ['CCS', 'CCSF', 'CCSM']
        
        if wmu.startswith('31') or is_stu or (wmu.startswith('35') and voc == 0):
            non_voc_row = base_row.copy()
            # Rename columns to match the exact format of the Non-VOC sheet
            non_voc_row['Pounds'] = non_voc_row.pop('Weight (Pounds)')
            non_voc_row['TONs'] = non_voc_row.pop('Weight (TONs)')
            non_voc_row['VOCS/Wt.'] = non_voc_row.pop('VOCSxWt.')
            non_voc_data.append(non_voc_row)
            
        # ---------------------------------------------------------
        # SHEET 3: GALLONS CALCULATOR (WMU 31 loads only)
        # ---------------------------------------------------------
        if wmu.startswith('31'):
            # Retrieve Specific Gravity from database or default to 1.0
            sg = t.get('specific_gravity')
            try:
                sg_val = float(sg) if sg is not None else 1.0
            except (ValueError, TypeError):
                sg_val = 1.0
            if sg_val <= 0:
                sg_val = 1.0
                
            # Convert LBS to Gallons (Using Specific Gravity)
            gallons = (lbs / (8.34 * sg_val)) if lbs else 0.0 
            gallons_data.append({
                'SPECIFIC GRAVITY': sg_val,
                'NET (LBS) WEIGHT OF LOAD': lbs,
                'GALLONS PER LOAD': round(gallons, 2),
                'WEIGHT TICKET #': t.get('load_number', ''),
                'WMU #': wmu
            })

    # Generate Excel File in memory
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        
        # Helper function to generate sheets with Totals and auto-width
        def write_formatted_sheet(data, sheet_name, columns, sum_cols):
            df = pd.DataFrame(data, columns=columns) if data else pd.DataFrame(columns=columns)
            
            # Append a Totals Row at the bottom
            if not df.empty and sum_cols:
                totals = {col: '' for col in columns}
                totals[columns[0]] = 'TOTAL'
                for col in sum_cols:
                    if col in df.columns:
                        totals[col] = df[col].sum()
                df = pd.concat([df, pd.DataFrame([totals])], ignore_index=True)
            
            # Write to Excel
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            
            # Auto-adjust column widths for a polished look
            worksheet = writer.sheets[sheet_name]
            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).map(len).max(), len(str(col))) + 2
                worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 30)
        
        # 1. Write WMU 35 Sheet
        write_formatted_sheet(wmu35_data, 'WMU 35 VOC TRACKING', 
                    ['Weighmaster Load No.', 'VOCs (ppm)', 'Weight (Pounds)', 'Weight (TONs)', 'VOCSxWt.', 'MANIFEST #', 'APPROVAL #', 'WMU'],
                    ['Weight (Pounds)', 'Weight (TONs)', 'VOCSxWt.'])
                    
        # 2. Write NON-VOC Sheet
        write_formatted_sheet(non_voc_data, "DAILY NON-VOC'S REPORT", 
                    ['Weighmaster Load No.', 'VOCs (ppm)', 'Pounds', 'TONs', 'VOCS/Wt.', 'MANIFEST #', 'APPROVAL #', 'WMU'],
                    ['Pounds', 'TONs', 'VOCS/Wt.'])
                    
        # 3. Write GALLONS Sheet
        write_formatted_sheet(gallons_data, 'GALLONS CALCULATOR', 
                    ['SPECIFIC GRAVITY', 'NET (LBS) WEIGHT OF LOAD', 'GALLONS PER LOAD', 'WEIGHT TICKET #', 'WMU #'],
                    ['NET (LBS) WEIGHT OF LOAD', 'GALLONS PER LOAD'])

    output.seek(0)
    
    # Return the dynamic multi-sheet workbook to the user
    return send_file(
        output, 
        as_attachment=True, 
        download_name=f"Daily_Tracking_Logs_{selected_date}.xlsx", 
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@reports_bp.route('/reports')
def reports():
    today_str = date.today().isoformat()
    selected_date = request.args.get('date', today_str)
    
    with closing(get_db_connection()) as conn:
        all_profiles = conn.execute('SELECT profile_number, voc_percentage FROM profiles').fetchall()
        voc_dict = {p['profile_number']: p['voc_percentage'] for p in all_profiles}
        
        sched_data = conn.execute("SELECT profile_number, SUM(load_count) as sched_loads FROM daily_schedule WHERE schedule_date = ? GROUP BY profile_number", (selected_date,)).fetchall()
        schedule_dict = {row['profile_number']: row['sched_loads'] for row in sched_data}
        total_scheduled = sum(schedule_dict.values())
        
        # Pulls truck counts and exact tonnage sent to Unit 35
        actual_data = conn.execute('''
            SELECT 
                t.profile_number, 
                COUNT(t.id) as total_trucks, 
                SUM(CASE WHEN t.cell_location LIKE '35%' THEN t.net_weight ELSE 0 END) as total_tons, 
                MAX(p.voc_percentage) as profile_voc 
            FROM truck_logs t 
            LEFT JOIN profiles p ON t.profile_number = p.profile_number 
            WHERE t.exit_weight IS NOT NULL AND t.date_received = ? 
            GROUP BY t.profile_number
        ''', (selected_date,)).fetchall()
        
        truck_logs_raw = conn.execute('''
            SELECT t.*, p.generator
            FROM truck_logs t
            LEFT JOIN profiles p ON t.profile_number = p.profile_number
            WHERE t.date_received = ? AND t.test_status != 'REJECTED'
            ORDER BY CAST(t.load_number AS INTEGER) ASC
        ''', (selected_date,)).fetchall()
        truck_logs = [dict(row) for row in truck_logs_raw]
        
    master_report, grand_trucks, grand_tons = [], 0, 0.0
    
    # --- NEW: TONNAGE-WEIGHTED TRACKING VARIABLES ---
    voc_x_tons_sum = 0.0
    unit_35_total_tons = 0.0
    
    actual_profiles = set()
    for a in actual_data:
        prof = a['profile_number']
        actual_profiles.add(prof)
        sched = schedule_dict.get(prof, 0)
        
        try:
            safe_voc = float(a['profile_voc'])
        except (ValueError, TypeError):
            safe_voc = 0.0
            
        unit_35_tons = a['total_tons'] or 0.0
        
        master_report.append({
            'profile_number': prof, 
            'total_trucks': a['total_trucks'], 
            'total_tons': unit_35_tons, 
            'avg_voc_ppm': safe_voc, 
            'scheduled': sched, 
            'variance': a['total_trucks'] - sched
        })
        
        grand_trucks += a['total_trucks']
        grand_tons += unit_35_tons
        
        # -------------------------------------------------------------
        # EXACT EXCEL MATH: (VOC x Tons) / Total Tons
        # -------------------------------------------------------------
        if unit_35_tons > 0: 
            voc_x_tons_sum += (safe_voc * unit_35_tons) 
            unit_35_total_tons += unit_35_tons
            
    for s in sched_data:
        prof = s['profile_number']
        if prof not in actual_profiles: 
            try:
                safe_voc = float(voc_dict.get(prof, 0.0))
            except (ValueError, TypeError):
                safe_voc = 0.0
                
            master_report.append({'profile_number': prof, 'total_trucks': 0, 'total_tons': 0.0, 'avg_voc_ppm': safe_voc, 'scheduled': s['sched_loads'], 'variance': -s['sched_loads']})
            
    master_report.sort(key=lambda x: (x['scheduled'], x['total_trucks']), reverse=True)
            
    # Calculate Final Tonnage-Weighted Average
    final_avg_voc = (voc_x_tons_sum / unit_35_total_tons) if unit_35_total_tons > 0 else 0.0
    
    grand_totals = {
        'grand_trucks': grand_trucks, 
        'grand_tons': grand_tons, 
        'grand_avg_voc_ppm': final_avg_voc, 
        'grand_scheduled': total_scheduled
    }
    
    trucks_by_profile = defaultdict(list)
    for t in truck_logs:
        trucks_by_profile[t['profile_number']].append(t)

    # ---------------------------------------------------------
    # GAP FILLING LOGIC FOR PRINTED COVER SHEET (SAFEGUARDED)
    # ---------------------------------------------------------
    filled_trucks = []
    if truck_logs:
        def get_load_int(t):
            try: return int(t['load_number'])
            except: return -1
            
        numbered_trucks = [t for t in truck_logs if get_load_int(t) > 0]
        
        if numbered_trucks:
            truck_dict = {get_load_int(t): dict(t) for t in numbered_trucks}
            sorted_load_nums = sorted(truck_dict.keys())
            
            for idx, current_num in enumerate(sorted_load_nums):
                # 1. Add the actual truck
                filled_trucks.append(truck_dict[current_num])
                
                # 2. Check the gap to the NEXT truck
                if idx < len(sorted_load_nums) - 1:
                    next_num = sorted_load_nums[idx + 1]
                    gap = next_num - current_num - 1
                    
                    if 0 < gap <= 20:
                        # Safe to fill small missing gaps individually
                        for i in range(current_num + 1, next_num):
                            filled_trucks.append({
                                'load_number': str(i),
                                'manifest_number': '---',
                                'profile_number': 'VOID / NOT ISSUED',
                                'sales_order': '',
                                'generator': 'Awaiting Entry / Missing Ticket',
                                'gross_weight': None,
                                'exit_weight': None,
                                'net_weight': None,
                                'cell_location': '---',
                                'grid_location': '---',
                                'time_in': '',
                                'time_out': '',
                                'test_status': 'VOID'
                            })
                    elif gap > 20:
                        # DANGER: Huge gap detected (likely a test, typo, or late entry). 
                        # Print ONE summary row to prevent crashing the server.
                        filled_trucks.append({
                            'load_number': f"{current_num + 1} ➔ {next_num - 1}",
                            'manifest_number': '⚠️ LARGE GAP',
                            'profile_number': 'SYSTEM SAFEGUARD',
                            'sales_order': '',
                            'generator': 'Multiple load numbers skipped to prevent system lag.',
                            'gross_weight': None,
                            'exit_weight': None,
                            'net_weight': None,
                            'cell_location': '---',
                            'grid_location': '---',
                            'time_in': '',
                            'time_out': '',
                            'test_status': 'VOID'
                        })
                        
        # Add any letters/non-numbers (like "12A") at the very end just in case
        for t in truck_logs:
            if get_load_int(t) == -1:
                filled_trucks.append(dict(t))

    return render_template('reports.html', 
                           report_data=master_report, 
                           grand_totals=grand_totals, 
                           selected_date=selected_date, 
                           today_str=today_str, 
                           trucks_by_profile=trucks_by_profile, 
                           all_trucks=filled_trucks)
