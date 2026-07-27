from flask import Blueprint, render_template, request, redirect, url_for, send_file, jsonify
from datetime import date, datetime, timedelta
from contextlib import closing
from io import BytesIO
import pandas as pd
from collections import defaultdict
from database import get_db_connection


reports_bp = Blueprint('reports_bp', __name__)

def build_2026voc_excel(trucks, selected_date):
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    
    # Setup exact 5 sheets matching I:\Buttonwillow\VOCs and TONNAGE by YEAR\2026VOC
    ws35 = wb.active
    ws35.title = '35'
    ws_non = wb.create_sheet(title='NON-VOC')
    ws31 = wb.create_sheet(title='31 GALLONS ')
    ws_s34 = wb.create_sheet(title='STORAGE TO 34')
    ws_tw = wb.create_sheet(title='TREATED WASTE (34 TO 35)')

    # Sheet 1: '35'
    ws35['A1'] = 'DAILY WMU 35 VOC TRACKING'
    ws35['A1'].font = Font(bold=True, size=12)
    ws35.append([]) # Row 2 blank
    headers_35 = ['RECEIVED', 'VOCs (ppm)', 'Weight (Pounds)', 'Weight (TONs)', 'VOCSxWt.', 'MANIFEST #', 'APPROVAL #', 'WMU', 'Weighmaster Load No.']
    ws35.append(headers_35)
    for col in range(1, 10):
        ws35.cell(row=3, column=col).font = Font(bold=True)

    # Sheet 2: 'NON-VOC'
    ws_non['A1'] = "DAILY NON-VOC'S REPORT"
    ws_non['A1'].font = Font(bold=True, size=12)
    headers_non = ['RECEIVED', 'VOCs ppm', 'Pounds', 'TONs', 'VOCS/Wt.', 'MANIFEST #', 'APPROVAL #', 'WMU', 'Weighmaster Load No.']
    ws_non.append(headers_non)
    for col in range(1, 10):
        ws_non.cell(row=2, column=col).font = Font(bold=True)

    # Sheet 3: '31 GALLONS '
    ws31.append([])
    ws31['A2'] = 'GALLONS PER LOAD CALCULATOR'
    ws31['A2'].font = Font(bold=True, size=12)
    ws31.append([])
    headers_31 = ['SPECIFIC GRAVITY', 'NET (LBS) WEIGHT OF LOAD', 'GALLONS PER LOAD', 'WEIGHT TICKET #', 'WMU #']
    ws31.append(headers_31)
    for col in range(1, 6):
        ws31.cell(row=4, column=col).font = Font(bold=True)

    # Sheet 4: 'STORAGE TO 34'
    ws_s34['A1'] = 'TREATED WASTE TRANSFER TO WMU 34 (for storage) '
    ws_s34['A1'].font = Font(bold=True, size=12)
    headers_s34 = ['DATE', 'STU Batch #s', 'Waste (Tons)', 'Additive (Tons)', 'Waste + Additive (Tons)', 'Water (Tons)', 'VOCs (ppm)', 'VOC/Wt. (PPM)', 'Final Grid', 'Date Moved', 'Cell 35-?', 'Note:', 'ZERO VOC']
    ws_s34.append(headers_s34)
    for col in range(1, 14):
        ws_s34.cell(row=2, column=col).font = Font(bold=True)

    # Sheet 5: 'TREATED WASTE (34 TO 35)'
    ws_tw['A1'] = 'TREATED WASTE TRANSFER TO WMU 35 from STORAGE (from map)'
    ws_tw['A1'].font = Font(bold=True, size=12)
    headers_tw = ['Date ', 'STU Batch #s', 'Waste (Tons)', 'Additive (Tons)', 'Waste + Additive (Tons)', 'Water (Tons) ', 'VOCs (ppm)', 'VOC/Wt.', 'Final Grid', 'Date', 'Cell 35-?']
    ws_tw.append(headers_tw)
    for col in range(1, 12):
        ws_tw.cell(row=2, column=col).font = Font(bold=True)

    row_35_idx = 4
    row_non_idx = 3
    row_31_idx = 5

    try:
        dt_received = datetime.strptime(selected_date, '%Y-%m-%d').date()
    except Exception:
        dt_received = selected_date

    for t in trucks:
        wmu_cell = str(t.get('cell_location', '') or '').strip().upper()
        wmu_grid = str(t.get('grid_location', '') or '').strip().upper()
        
        if wmu_cell and wmu_grid:
            wmu = f"{wmu_cell}/{wmu_grid}"
        elif wmu_cell:
            wmu = wmu_cell
        elif wmu_grid:
            wmu = wmu_grid
        else:
            wmu = '35-7' if '35' in str(t.get('win_code', '')) else ('31' if '31' in str(t.get('win_code', '')) else '35-7')

        try: voc = float(t.get('measured_voc') if t.get('measured_voc') is not None else (t.get('voc_percentage') or 0.0))
        except (ValueError, TypeError): voc = 0.0

        try:
            gross = float(t.get('gross_weight') or 0)
            tare = float(t.get('exit_weight') or 0)
            lbs = gross - tare if gross > tare else gross
        except (ValueError, TypeError):
            lbs = 0.0

        manifest = str(t.get('manifest_number', '') or '').strip()
        profile = str(t.get('profile_number', '') or '').strip()
        ticket = t.get('truck_id') or t.get('load_number', '')
        try:
            ticket = int(ticket)
        except Exception:
            pass

        # Sheet 1: '35'
        if wmu.startswith('35') or '35' in wmu:
            ws35.cell(row=row_35_idx, column=1, value=dt_received)
            ws35.cell(row=row_35_idx, column=2, value=voc)
            ws35.cell(row=row_35_idx, column=3, value=lbs)
            ws35.cell(row=row_35_idx, column=4, value=f"=C{row_35_idx}/2000")
            ws35.cell(row=row_35_idx, column=5, value=f"=D{row_35_idx}*B{row_35_idx}")
            ws35.cell(row=row_35_idx, column=6, value=manifest)
            ws35.cell(row=row_35_idx, column=7, value=profile)
            ws35.cell(row=row_35_idx, column=8, value=wmu)
            ws35.cell(row=row_35_idx, column=9, value=ticket)
            row_35_idx += 1

        # Sheet 2: 'NON-VOC'
        is_stu = '34' in wmu or 'STU' in wmu or 'BAY' in wmu or wmu in ['CCS', 'CCSF', 'CCSM']
        if '31' in wmu or is_stu or (('35' in wmu) and voc == 0):
            ws_non.cell(row=row_non_idx, column=1, value=dt_received)
            ws_non.cell(row=row_non_idx, column=2, value=voc)
            ws_non.cell(row=row_non_idx, column=3, value=lbs)
            ws_non.cell(row=row_non_idx, column=4, value=f"=C{row_non_idx}/2000")
            ws_non.cell(row=row_non_idx, column=5, value=f"=D{row_non_idx}*B{row_non_idx}")
            ws_non.cell(row=row_non_idx, column=6, value=manifest)
            ws_non.cell(row=row_non_idx, column=7, value=profile)
            ws_non.cell(row=row_non_idx, column=8, value=wmu)
            ws_non.cell(row=row_non_idx, column=9, value=ticket)
            row_non_idx += 1

        # Sheet 3: '31 GALLONS '
        if '31' in wmu or str(t.get('win_code', '')).upper() in ['CBPS', 'CNOS']:
            try: sg = float(t.get('specific_gravity') or 1.0)
            except Exception: sg = 1.0
            if sg <= 0: sg = 1.0

            ws31.cell(row=row_31_idx, column=1, value=sg)
            ws31.cell(row=row_31_idx, column=2, value=lbs)
            ws31.cell(row=row_31_idx, column=3, value=f"=IFERROR(B{row_31_idx}/(A{row_31_idx}*8.33), 0)")
            ws31.cell(row=row_31_idx, column=4, value=ticket)
            ws31.cell(row=row_31_idx, column=5, value=wmu)
            row_31_idx += 1

    # Totals for Sheet '35'
    if row_35_idx > 4:
        ws35.cell(row=row_35_idx, column=1, value="TOTAL").font = Font(bold=True)
        ws35.cell(row=row_35_idx, column=3, value=f"=SUM(C4:C{row_35_idx-1})").font = Font(bold=True)
        ws35.cell(row=row_35_idx, column=4, value=f"=SUM(D4:D{row_35_idx-1})").font = Font(bold=True)
        ws35.cell(row=row_35_idx, column=5, value=f"=SUM(E4:E{row_35_idx-1})").font = Font(bold=True)

    # Totals for Sheet 'NON-VOC'
    if row_non_idx > 3:
        ws_non.cell(row=row_non_idx, column=1, value="TOTAL").font = Font(bold=True)
        ws_non.cell(row=row_non_idx, column=3, value=f"=SUM(C3:C{row_non_idx-1})").font = Font(bold=True)
        ws_non.cell(row=row_non_idx, column=4, value=f"=SUM(D3:D{row_non_idx-1})").font = Font(bold=True)
        ws_non.cell(row=row_non_idx, column=5, value=f"=SUM(E3:E{row_non_idx-1})").font = Font(bold=True)

    # Totals for Sheet '31 GALLONS '
    if row_31_idx > 5:
        ws31.cell(row=row_31_idx, column=1, value="TOTAL").font = Font(bold=True)
        ws31.cell(row=row_31_idx, column=2, value=f"=SUM(B5:B{row_31_idx-1})").font = Font(bold=True)
        ws31.cell(row=row_31_idx, column=3, value=f"=SUM(C5:C{row_31_idx-1})").font = Font(bold=True)

    # Auto-fit column widths generously across all sheets
    from openpyxl.utils import get_column_letter
    for ws in [ws35, ws_non, ws31, ws_s34, ws_tw]:
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val = str(cell.value or '')
                if val.startswith('='):
                    max_len = max(max_len, 14)
                else:
                    max_len = max(max_len, len(val))
            ws.column_dimensions[col_letter].width = max(max_len + 5, 18)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output

@reports_bp.route('/export')
def export_excel():
    selected_date = request.args.get('date', date.today().isoformat())

    with closing(get_db_connection()) as conn:
        trucks_raw = conn.execute('''
            SELECT t.*, p.voc_percentage, p.win_code
            FROM truck_logs t
            LEFT JOIN profiles p ON TRIM(UPPER(t.profile_number)) = TRIM(UPPER(p.profile_number))
            WHERE t.date_received = ? AND t.test_status != 'REJECTED'
            ORDER BY CAST(t.load_number AS INTEGER) ASC
        ''', (selected_date,)).fetchall()
        
    trucks = [dict(r) for r in trucks_raw]
    excel_stream = build_2026voc_excel(trucks, selected_date)
    
    try:
        dt = datetime.strptime(selected_date, '%Y-%m-%d')
        file_name = f"{dt.strftime('%m%d%Y')}VOC.xlsx"
    except Exception:
        file_name = f"Daily_VOC_Tracking_{selected_date}.xlsx"

    return send_file(
        excel_stream, 
        as_attachment=True, 
        download_name=file_name, 
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@reports_bp.route('/voc_audit')
def voc_audit():
    import openpyxl, glob, os, re, difflib

    selected_date = request.args.get('date', '')
    filter_typos_only = request.args.get('typos_only', 'false').lower() == 'true'

    with closing(get_db_connection()) as conn:
        master_rows = conn.execute('SELECT profile_number, generator, win_code FROM profiles').fetchall()
        master_profiles = {str(r['profile_number']).strip().upper(): dict(r) for r in master_rows if r['profile_number']}
        master_list = list(master_profiles.keys())

        sched_query = 'SELECT schedule_date, profile_number, load_count FROM daily_schedule'
        params = ()
        if selected_date:
            sched_query += ' WHERE schedule_date = ?'
            params = (selected_date,)
            
        sched_rows = conn.execute(sched_query, params).fetchall()
        schedule_by_date = {}
        for r in sched_rows:
            sdate = str(r['schedule_date']).strip()
            prof = str(r['profile_number'] or '').strip().upper()
            if not sdate or not prof: continue
            if sdate not in schedule_by_date:
                schedule_by_date[sdate] = {}
            schedule_by_date[sdate][prof] = schedule_by_date[sdate].get(prof, 0) + (r['load_count'] or 1)

    voc_folder = r'I:\Buttonwillow\VOCs and TONNAGE  by YEAR\2026VOC'
    if os.path.exists(voc_folder):
        voc_files = glob.glob(os.path.join(voc_folder, '*VOC.xlsx'))
    else:
        voc_files = []

    audit_records = []
    total_files = len(voc_files)
    total_typos = 0

    for fpath in sorted(voc_files, reverse=True):
        fname = os.path.basename(fpath)
        match = re.match(r'(\d{2})(\d{2})(\d{4})VOC\.xlsx', fname, re.IGNORECASE)
        if not match: continue
        mm, dd, yyyy = match.groups()
        iso_date = f"{yyyy}-{mm}-{dd}"

        if selected_date and iso_date != selected_date:
            continue

        try:
            wb = openpyxl.load_workbook(fpath, data_only=True, read_only=True)
        except Exception:
            continue

        file_actual_counts = {}

        for sheet_name in ['35', 'NON-VOC']:
            if sheet_name not in wb.sheetnames: continue
            ws = wb[sheet_name]
            
            header_found = False
            approval_col_idx = None
            
            for row in ws.iter_rows(values_only=True):
                if not row: continue
                row_str = [str(c or '').strip().upper() for c in row]
                if 'APPROVAL #' in row_str or 'APPROVAL#' in row_str:
                    header_found = True
                    approval_col_idx = row_str.index('APPROVAL #') if 'APPROVAL #' in row_str else row_str.index('APPROVAL#')
                    continue
                
                if header_found and approval_col_idx is not None and len(row) > approval_col_idx:
                    raw_prof = row[approval_col_idx]
                    if not raw_prof or str(raw_prof).strip().upper() in ['TOTAL', 'APPROVAL #', 'NONE', '']:
                        continue
                    
                    prof_clean = str(raw_prof).strip().upper()
                    file_actual_counts[prof_clean] = file_actual_counts.get(prof_clean, 0) + 1

        wb.close()
        scheduled_for_date = schedule_by_date.get(iso_date, {})

        for prof, actual_cnt in file_actual_counts.items():
            if re.match(r'^\d+\.\d+$', prof) or len(prof) < 4:
                continue

            if prof in master_profiles:
                is_exact = True
                suggested = prof
                score = 100.0
                gen_name = master_profiles[prof].get('generator', '---')
            else:
                is_exact = False
                matches = difflib.get_close_matches(prof, master_list, n=1, cutoff=0.55)
                if matches:
                    suggested = matches[0]
                    score = round(difflib.SequenceMatcher(None, prof, suggested).ratio() * 100, 1)
                    gen_name = master_profiles[suggested].get('generator', '---')
                else:
                    suggested = None
                    score = 0.0
                    gen_name = '---'

            if not is_exact:
                total_typos += 1

            if filter_typos_only and is_exact:
                continue

            sched_cnt = scheduled_for_date.get(prof, 0) or (scheduled_for_date.get(suggested, 0) if suggested else 0)

            audit_records.append({
                'date': iso_date,
                'file_name': fname,
                'file_profile': prof,
                'suggested_master': suggested or 'UNKNOWN',
                'is_exact': is_exact,
                'score': score,
                'generator': gen_name,
                'actual_count': actual_cnt,
                'scheduled_count': sched_cnt,
                'variance': actual_cnt - sched_cnt
            })

    return render_template(
        'voc_audit.html',
        records=audit_records,
        selected_date=selected_date,
        filter_typos_only=filter_typos_only,
        total_files=total_files,
        total_typos=total_typos
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

