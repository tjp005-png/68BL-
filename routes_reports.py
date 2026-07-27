from flask import Blueprint, render_template, request, redirect, url_for, send_file, jsonify
from datetime import date, datetime, timedelta
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
                
            worksheet.page_setup.fitToPage = True
            worksheet.page_setup.fitToWidth = 1
            worksheet.page_setup.fitToHeight = 0
            worksheet.page_setup.orientation = 'landscape'
        
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
        voc_dict = {str(p['profile_number'] or '').strip().upper(): p['voc_percentage'] for p in all_profiles}
        
        sched_data = conn.execute("SELECT profile_number, SUM(load_count) as sched_loads FROM daily_schedule WHERE schedule_date = ? GROUP BY profile_number", (selected_date,)).fetchall()
        schedule_dict = {}
        for row in sched_data:
            prof_clean = str(row['profile_number'] or '').strip().upper()
            schedule_dict[prof_clean] = schedule_dict.get(prof_clean, 0) + row['sched_loads']
        total_scheduled = sum(schedule_dict.values())
        
        # Pulls truck counts and exact tonnage sent to Unit 35
        actual_data = conn.execute('''
            SELECT 
                TRIM(UPPER(t.profile_number)) as profile_number, 
                COUNT(t.id) as total_trucks, 
                SUM(CASE WHEN t.cell_location LIKE '35%' THEN (CASE WHEN t.net_weight > 500 THEN t.net_weight / 2000.0 ELSE t.net_weight END) ELSE 0 END) as total_tons, 
                MAX(p.voc_percentage) as profile_voc 
            FROM truck_logs t 
            LEFT JOIN profiles p ON TRIM(UPPER(t.profile_number)) = TRIM(UPPER(p.profile_number)) 
            WHERE t.exit_weight IS NOT NULL AND t.date_received = ? 
            GROUP BY TRIM(UPPER(t.profile_number))
        ''', (selected_date,)).fetchall()
        
        # Pulls logs details, joining case-insensitively
        truck_logs_raw = conn.execute('''
            SELECT t.*, p.generator
            FROM truck_logs t
            LEFT JOIN profiles p ON TRIM(UPPER(t.profile_number)) = TRIM(UPPER(p.profile_number))
            WHERE t.date_received = ? AND t.test_status != 'REJECTED'
            ORDER BY CAST(t.load_number AS INTEGER) ASC
        ''', (selected_date,)).fetchall()
        
        truck_logs = []
        for row in truck_logs_raw:
            t = dict(row)
            gross = float(t.get('gross_weight') or 0)
            tare = float(t.get('exit_weight') or 0)
            net_val = float(t.get('net_weight') or 0)
            
            # Ensure net_weight is always in Tons
            if net_val > 500:
                t['net_weight'] = net_val / 2000.0
            elif net_val == 0 and gross > 0:
                t['net_weight'] = (gross - tare) / 2000.0
                
            truck_logs.append(t)
        
    master_report, grand_trucks, grand_tons = [], 0, 0.0
    
    # --- NEW: TONNAGE-WEIGHTED TRACKING VARIABLES ---
    voc_x_tons_sum = 0.0
    unit_35_total_tons = 0.0
    
    actual_profiles = set()
    for a in actual_data:
        prof = str(a['profile_number'] or '').strip().upper()
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
            
    for prof, sched in schedule_dict.items():
        if prof not in actual_profiles: 
            try:
                safe_voc = float(voc_dict.get(prof, 0.0))
            except (ValueError, TypeError):
                safe_voc = 0.0
                
            master_report.append({
                'profile_number': prof, 
                'total_trucks': 0, 
                'total_tons': 0.0, 
                'avg_voc_ppm': safe_voc, 
                'scheduled': sched, 
                'variance': -sched
            })
            
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
        prof_clean = str(t['profile_number'] or '').strip().upper()
        trucks_by_profile[prof_clean].append(t)

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


@reports_bp.route('/compliance')
def compliance():
    today = date.today()
    # Default date range: Jan 1st of current year to today
    start_date = date(today.year, 1, 1).isoformat()
    end_date = today.isoformat()
    return render_template('compliance.html', start_date=start_date, end_date=end_date)


@reports_bp.route('/api/compliance/data')
def compliance_data():
    report_type = request.args.get('report_type', 'variance')
    start_str = request.args.get('start_date')
    end_str = request.args.get('end_date')
    
    if not start_str or not end_str:
        return jsonify({'error': 'Missing dates'}), 400
        
    try:
        start_dt = datetime.strptime(start_str, '%Y-%m-%d').date()
        end_dt = datetime.strptime(end_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400
        
    # Generate all dates in range
    date_list = []
    curr = start_dt
    while curr <= end_dt:
        date_list.append(curr.isoformat())
        curr += timedelta(days=1)
        
    with closing(get_db_connection()) as conn:
        if report_type == 'variance':
            # 1. Scheduled
            sched_rows = conn.execute('''
                SELECT schedule_date, SUM(load_count) as scheduled
                FROM daily_schedule
                WHERE schedule_date BETWEEN ? AND ?
                GROUP BY schedule_date
            ''', (start_str, end_str)).fetchall()
            sched_map = {r['schedule_date']: r['scheduled'] for r in sched_rows}
            
            # 2. Actual
            actual_rows = conn.execute('''
                SELECT date_received, COUNT(id) as actual
                FROM truck_logs
                WHERE date_received BETWEEN ? AND ? AND test_status != 'REJECTED' AND exit_weight IS NOT NULL
                GROUP BY date_received
            ''', (start_str, end_str)).fetchall()
            actual_map = {r['date_received']: r['actual'] for r in actual_rows}
            
            labels = date_list
            scheduled_data = [sched_map.get(d, 0) for d in labels]
            actual_data = [actual_map.get(d, 0) for d in labels]
            variance_data = [actual_data[i] - scheduled_data[i] for i in range(len(labels))]
            
            # Summary Metrics
            total_sched = sum(scheduled_data)
            total_act = sum(actual_data)
            net_variance = total_act - total_sched
            avg_variance = round(net_variance / len(labels), 1) if labels else 0
            
            summary = {
                'kpi1_val': total_sched, 'kpi1_label': 'Total Scheduled',
                'kpi2_val': total_act, 'kpi2_label': 'Total Actual Weighed',
                'kpi3_val': f"{net_variance:+d}", 'kpi3_label': 'Net Variance',
                'kpi4_val': avg_variance, 'kpi4_label': 'Avg Daily Variance'
            }
            
            # Details table data
            table_data = []
            for i, d in enumerate(labels):
                table_data.append({
                    'date': d, 'scheduled': scheduled_data[i], 'actual': actual_data[i], 'variance': f"{variance_data[i]:+d}"
                })
                
            return jsonify({
                'labels': labels,
                'datasets': [
                    {'label': 'Scheduled Loads', 'data': scheduled_data},
                    {'label': 'Actual Loads', 'data': actual_data},
                    {'label': 'Variance', 'data': variance_data}
                ],
                'summary': summary,
                'table_data': table_data
            })
            
        elif report_type == 'traffic':
            traffic_rows = conn.execute('''
                SELECT date_received, COUNT(id) as traffic
                FROM truck_logs
                WHERE date_received BETWEEN ? AND ? AND test_status != 'REJECTED'
                GROUP BY date_received
            ''', (start_str, end_str)).fetchall()
            traffic_map = {r['date_received']: r['traffic'] for r in traffic_rows}
            
            labels = date_list
            counts = [traffic_map.get(d, 0) for d in labels]
            
            # Summary metrics
            total_trucks = sum(counts)
            avg_daily = round(total_trucks / len(labels), 1) if labels else 0
            max_daily = max(counts) if counts else 0
            
            # Find busiest day
            busiest_day = 'N/A'
            if counts:
                busiest_idx = counts.index(max_daily)
                busiest_day = labels[busiest_idx]
                
            summary = {
                'kpi1_val': total_trucks, 'kpi1_label': 'Total Trucks Weighed',
                'kpi2_val': avg_daily, 'kpi2_label': 'Avg Daily Trucks',
                'kpi3_val': max_daily, 'kpi3_label': 'Busiest Day Traffic',
                'kpi4_val': busiest_day, 'kpi4_label': 'Busiest Day Date'
            }
            
            table_data = [{'date': d, 'count': counts[i]} for i, d in enumerate(labels)]
            
            return jsonify({
                'labels': labels,
                'datasets': [
                    {'label': 'Total Checked-in Trucks', 'data': counts}
                ],
                'summary': summary,
                'table_data': table_data
            })
            
        elif report_type == 'tonnage_ytd':
            # Aggregation query
            rows = conn.execute('''
                SELECT date_received, cell_location, SUM(net_weight) as tons
                FROM truck_logs
                WHERE date_received BETWEEN ? AND ? AND exit_weight IS NOT NULL AND test_status != 'REJECTED'
                GROUP BY date_received, cell_location
                ORDER BY date_received ASC
            ''', (start_str, end_str)).fetchall()
            
            labels = date_list
            # Pre-populate daily bins
            daily_wmu35 = {d: 0.0 for d in labels}
            daily_wmu31 = {d: 0.0 for d in labels}
            daily_stu = {d: 0.0 for d in labels}
            daily_landfill = {d: 0.0 for d in labels}
            
            for r in rows:
                d = r['date_received']
                if d not in daily_wmu35:
                    continue
                wmu = str(r['cell_location'] or '').strip().upper()
                tons = float(r['tons'] or 0.0)
                
                is_stu = wmu.startswith('34') or wmu.startswith('STU') or 'BAY' in wmu or wmu in ['CCS', 'CCSF', 'CCSM']
                
                if wmu.startswith('35'):
                    daily_wmu35[d] += tons
                elif wmu.startswith('31'):
                    daily_wmu31[d] += tons
                elif is_stu:
                    daily_stu[d] += tons
                else:
                    daily_landfill[d] += tons
            
            # Compute running cumulative sums (YTD Curves)
            cum_wmu35, cum_wmu31, cum_stu, cum_landfill = [], [], [], []
            sum35, sum31, sum_s, sum_l = 0.0, 0.0, 0.0, 0.0
            
            for d in labels:
                sum35 += daily_wmu35[d]
                sum31 += daily_wmu31[d]
                sum_s += daily_stu[d]
                sum_l += daily_landfill[d]
                cum_wmu35.append(round(sum35, 2))
                cum_wmu31.append(round(sum31, 2))
                cum_stu.append(round(sum_s, 2))
                cum_landfill.append(round(sum_l, 2))
                
            grand_total = round(sum35 + sum31 + sum_s + sum_l, 2)
            
            summary = {
                'kpi1_val': f"{grand_total:,.2f} Tons", 'kpi1_label': 'Total YTD Tonnage',
                'kpi2_val': f"{sum35:,.2f} Tons", 'kpi2_label': 'WMU 35 Total',
                'kpi3_val': f"{sum31:,.2f} Tons", 'kpi3_label': 'WMU 31 Total',
                'kpi4_val': f"{sum_s:,.2f} Tons", 'kpi4_label': 'STU / Decon Total'
            }
            
            table_data = []
            for i, d in enumerate(labels):
                table_data.append({
                    'date': d,
                    'wmu35': f"{cum_wmu35[i]:,.2f}",
                    'wmu31': f"{cum_wmu31[i]:,.2f}",
                    'stu': f"{cum_stu[i]:,.2f}",
                    'landfill': f"{cum_landfill[i]:,.2f}",
                    'total': f"{(cum_wmu35[i] + cum_wmu31[i] + cum_stu[i] + cum_landfill[i]):,.2f}"
                })
                
            return jsonify({
                'labels': labels,
                'datasets': [
                    {'label': 'WMU 35 Cumulative', 'data': cum_wmu35},
                    {'label': 'WMU 31 Cumulative', 'data': cum_wmu31},
                    {'label': 'STU / Decon Cumulative', 'data': cum_stu},
                    {'label': 'Landfill / Other Cumulative', 'data': cum_landfill}
                ],
                'summary': summary,
                'table_data': table_data
            })
            
        elif report_type == 'timings':
            rows = conn.execute('''
                SELECT time_in, COUNT(id) as count
                FROM truck_logs
                WHERE date_received BETWEEN ? AND ? AND time_in IS NOT NULL AND time_in != ''
                GROUP BY time_in
            ''', (start_str, end_str)).fetchall()
            
            # Map hours 00 to 23
            hourly_counts = {f"{h:02d}": 0 for h in range(24)}
            for r in rows:
                time_str = r['time_in']
                try:
                    hour_part = time_str.split(':')[0].strip()
                    if hour_part.isdigit():
                        h_int = int(hour_part)
                        if 0 <= h_int < 24:
                            hourly_counts[f"{h_int:02d}"] += r['count']
                except:
                    pass
            
            # Select working hours range to show in chart for clean design (e.g. 06:00 to 18:00)
            hours_keys = [f"{h:02d}" for h in range(6, 19)]
            labels = [f"{h}:00" for h in hours_keys]
            counts = [hourly_counts[h] for h in hours_keys]
            
            total_weighed = sum(hourly_counts.values())
            
            # Find peak hour
            peak_val = max(counts) if counts else 0
            peak_hour = 'N/A'
            if peak_val > 0:
                peak_idx = counts.index(peak_val)
                peak_hour = labels[peak_idx]
                
            summary = {
                'kpi1_val': total_weighed, 'kpi1_label': 'Total Checked-in Trucks',
                'kpi2_val': peak_hour, 'kpi2_label': 'Busiest Hour (Peak)',
                'kpi3_val': peak_val, 'kpi3_label': 'Peak Hour Truck Volume',
                'kpi4_val': f"{round((peak_val/total_weighed*100) if total_weighed > 0 else 0)}%", 'kpi4_label': 'Peak Hour Traffic Ratio'
            }
            
            table_data = [{'hour': labels[i], 'count': counts[i]} for i, h in enumerate(labels)]
            
            return jsonify({
                'labels': labels,
                'datasets': [
                    {'label': 'Truck Entry Count', 'data': counts}
                ],
                'summary': summary,
                'table_data': table_data
            })
            
        elif report_type == 'norm':
            # Retrieve NORM loads (profiles with win_code = 'CNON')
            rows = conn.execute('''
                SELECT t.id, t.date_received, t.profile_number, t.load_number, t.manifest_number, 
                       t.net_weight, t.cell_location, p.generator
                FROM truck_logs t
                INNER JOIN profiles p ON t.profile_number = p.profile_number
                WHERE p.win_code = 'CNON'
                  AND t.date_received BETWEEN ? AND ?
                ORDER BY t.date_received ASC, CAST(t.load_number AS INTEGER) ASC
            ''', (start_str, end_str)).fetchall()

            labels = date_list
            daily_counts = {d: 0 for d in labels}
            total_tons = 0.0
            unique_profiles = set()
            
            table_data = []
            for r in rows:
                d = r['date_received']
                if d in daily_counts:
                    daily_counts[d] += 1
                unique_profiles.add(r['profile_number'])
                total_tons += float(r['net_weight'] or 0.0)
                
                table_data.append({
                    'date': d,
                    'profile_number': r['profile_number'],
                    'load_number': r['load_number'],
                    'manifest_number': r['manifest_number'] or '---',
                    'generator': r['generator'] or 'Unknown Generator',
                    'weight': f"{float(r['net_weight'] or 0.0):.2f}",
                    'cell_location': r['cell_location'] or '---'
                })

            counts = [daily_counts[d] for d in labels]
            total_norm_loads = len(rows)
            
            busiest_day = 'N/A'
            max_daily = max(counts) if counts else 0
            if max_daily > 0:
                busiest_idx = counts.index(max_daily)
                busiest_day = labels[busiest_idx]

            summary = {
                'kpi1_val': total_norm_loads, 'kpi1_label': 'Total NORM Loads',
                'kpi2_val': len(unique_profiles), 'kpi2_label': 'Unique Profiles',
                'kpi3_val': f"{total_tons:,.2f} Tons", 'kpi3_label': 'Total NORM Tonnage',
                'kpi4_val': busiest_day if max_daily > 0 else 'None', 'kpi4_label': 'Busiest NORM Day'
            }

            return jsonify({
                'labels': labels,
                'datasets': [
                    {'label': 'NORM Loads Checked-in', 'data': counts}
                ],
                'summary': summary,
                'table_data': table_data
            })
            
    return jsonify({'error': 'Invalid report type'}), 400

