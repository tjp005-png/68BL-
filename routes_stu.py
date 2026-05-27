from flask import Blueprint, render_template, request, redirect, url_for, send_file
from datetime import date, datetime
from contextlib import closing
from io import BytesIO
import pandas as pd
import uuid
import json
import os
import zipfile
from openpyxl.styles import Font, Alignment, Border, Side
import stu_services
from database import get_db_connection

stu_bp = Blueprint('stu_bp', __name__)

@stu_bp.route('/stu/inventory')
def stu_inventory():
    category = request.args.get('category', 'All')
    with closing(get_db_connection()) as conn:
        queries = {
            'Decon': "SELECT * FROM drum_inventory WHERE process_type IN ('direct land haz', 'directlandasbes', 'asbestos') OR inb_prof = 'cnia' ORDER BY age DESC",
            'Solidification': "SELECT * FROM drum_inventory WHERE process_type = 'solidify normal' ORDER BY age DESC",
            'T_Drums': "SELECT * FROM drum_inventory WHERE process_type IN ('stabsolids 1', 'stabsolids 2') ORDER BY age DESC",
            'TL_Drums': "SELECT * FROM drum_inventory WHERE process_type IN ('stabsolids 3', 'stabsolids 5') ORDER BY age DESC",
            'Special_Handling': "SELECT * FROM drum_inventory WHERE process_type IN ('stabsolids 6', 'stabsolids 8', 'stabsolids 9', 'stabsolids 11', 'stabsolids 12', 'stabsolids 13', 'stabsolids 14') ORDER BY age DESC",
            'All': "SELECT * FROM drum_inventory ORDER BY age DESC"
        }
        drums = conn.execute(queries.get(category, queries['All'])).fetchall()
        last_upload_row = conn.execute('SELECT MAX(import_date) FROM drum_inventory').fetchone()
        last_upload = last_upload_row[0] if last_upload_row else 'NO DATA'
    return render_template('stu_inventory.html', drums=drums, category=category, last_upload=last_upload)

@stu_bp.route('/stu/sampling', methods=['GET', 'POST'])
def stu_sampling():
    if request.method == 'POST':
        if 'label_pdf' not in request.files: return "No file uploaded", 400
        file = request.files['label_pdf']
        if file.filename == '': return "No file selected", 400

        file_bytes = file.read()
        
        raw_drums = stu_services.parse_drum_labels_from_pdf(BytesIO(file_bytes))
        if not raw_drums:
            return "<script>alert('No valid drums found in PDF.'); window.history.back();</script>"

        if not os.path.exists('temp_uploads'):
            os.makedirs('temp_uploads')
            
        temp_filename = f"{uuid.uuid4().hex}.pdf"
        temp_filepath = os.path.join('temp_uploads', temp_filename)
        
        with open(temp_filepath, 'wb') as f:
            f.write(file_bytes)
        
        return render_template('stu_sampling.html', drums=raw_drums, pdf_filename=temp_filename, raw_drums_json=json.dumps(raw_drums))
        
    return render_template('stu_sampling.html', drums=None)

@stu_bp.route('/generate_sampling_packet', methods=['POST'])
def generate_sampling_packet():
    pdf_filename = request.form.get('pdf_filename')
    selected_drums_json = request.form.get('selected_drums')

    if not pdf_filename or not selected_drums_json:
        return f"Error: Data dropped.", 400

    temp_filepath = os.path.join('temp_uploads', pdf_filename)
    try:
        with open(temp_filepath, 'rb') as f:
            file_bytes = f.read()
    except FileNotFoundError:
        return "Temporary file lost. Please start over and re-upload the PDF.", 400

    raw_drums = json.loads(selected_drums_json)
    job_name = datetime.now().strftime("%m-%d-%Y_%H%M")

    with closing(get_db_connection()) as conn:
        picklist_data, total_samples = stu_services.process_drums(conn, raw_drums)
        
        for d in raw_drums:
            today_str = date.today().isoformat()
            
            existing = conn.execute("SELECT id FROM drum_inventory WHERE track_no = ?", (d['drum_id'],)).fetchone()
            if not existing:
                conn.execute('''
                    INSERT INTO drum_inventory (track_no, inb_prof, manifest, process_type, weight, ph, age, voc_ppm, voc_weight, import_date) 
                    VALUES (?, ?, ?, 'PENDING SAMPLING', 0, 0, 0, 0, 0, ?)
                ''', (d['drum_id'], d['profile'], d['manifest'], today_str))
                
        for p in picklist_data:
            if p.get('is_sampled') == 'Yes':
                
                existing_lab = conn.execute("SELECT id FROM drum_lab_queue WHERE drum_id = ? AND status != 'COMPLETED'", (p['drum_id'],)).fetchone()
                if not existing_lab:
                    conn.execute('''
                        INSERT INTO drum_lab_queue (job_id, drum_id, profile, manifest, tests_required, status)
                        VALUES (?, ?, ?, ?, ?, 'PENDING')
                    ''', (job_name, p['drum_id'], p['profile'], p['manifest'], p.get('voc_testing_trigger', 'FingerPrint')))
                
        conn.commit()

    memory_zip = BytesIO()
    with zipfile.ZipFile(memory_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        picklist_buffer = BytesIO()
        stu_services.create_pdf_report(picklist_buffer, f"Drum Sampling Pick List - {job_name}", picklist_data, total_samples)
        zf.writestr(f"Picklist_{job_name}.pdf", picklist_buffer.getvalue())

        lab_buffer = BytesIO()
        stu_services.create_lab_sheet_pdf(lab_buffer, job_name, picklist_data)
        zf.writestr(f"LabBenchSheet_{job_name}.pdf", lab_buffer.getvalue())

        df = pd.DataFrame(picklist_data)
        excel_io = BytesIO()
        with pd.ExcelWriter(excel_io, engine='openpyxl') as writer:
            cols = ["sample_num", "drum_id", "manifest", "manifest_line", "profile", "display_profile", "waste_code", "is_sampled"]
            exist_cols = [c for c in cols if c in df.columns]
            df[exist_cols].to_excel(writer, index=False, sheet_name='Data')
        zf.writestr(f"Data_{job_name}.xlsx", excel_io.getvalue())

        annotated_buffer = BytesIO()
        stu_services.create_annotated_pdf(BytesIO(file_bytes), annotated_buffer, picklist_data)
        if annotated_buffer.getvalue():
            zf.writestr(f"LABELS_{job_name}_Marked.pdf", annotated_buffer.getvalue())

    if os.path.exists(temp_filepath):
        os.remove(temp_filepath)

    memory_zip.seek(0)
    return send_file(memory_zip, mimetype='application/zip', as_attachment=True, download_name=f"Drum_Sampling_{job_name}.zip")

@stu_bp.route('/upload_vpi', methods=['POST'])
def upload_vpi():
    if 'vpi_file' not in request.files: return "No file uploaded", 400
    file = request.files['vpi_file']
    if file.filename == '': return "No file selected", 400
    try:
        df = pd.read_csv(file)
        df.columns = [str(c).strip() for c in df.columns]
        
        required = ['Track No', 'Process Type', 'Weight', 'pH', 'Inb Prof', 'Age', 'Type']
        if not all(col in df.columns for col in required): 
            return "Error: Uploaded CSV is missing required columns from WIN.", 400
            
        df['Process Type'] = df['Process Type'].astype(str).str.strip().str.lower()
        df['Inb Prof'] = df['Inb Prof'].astype(str).str.strip().str.lower()
        df['Type'] = df['Type'].astype(str).str.strip().str.lower() 
        
        df = df[~df['Process Type'].isin(['put pile', '=', 'nan', ''])]
        df = df[~df['Type'].str.contains('cm|dt', na=False)]
        
        df['Weight'] = pd.to_numeric(df['Weight'], errors='coerce').fillna(0)
        df['pH'] = pd.to_numeric(df['pH'], errors='coerce').fillna(0)
        df['Age'] = pd.to_numeric(df['Age'], errors='coerce').fillna(0)
        
        df = df.drop_duplicates(subset=['Track No'], keep='last')
        
        with closing(get_db_connection()) as conn:
            conn.execute('DELETE FROM drum_inventory')
            profiles_df = pd.read_sql_query("SELECT LOWER(profile_number) as profile_number, voc_percentage FROM profiles", conn)
            
            # Text values mapped from DB are safely inside the database block now
            voc_dict = dict(zip(profiles_df['profile_number'], profiles_df['voc_percentage']))
            df['voc_ppm'] = df['Inb Prof'].map(voc_dict).fillna(0)
            
            # Convert text flags like 'TBD' safely to numeric 0
            df['voc_ppm'] = pd.to_numeric(df['voc_ppm'], errors='coerce').fillna(0)
            df['voc_weight'] = df['Weight'] * df['voc_ppm']
            
            cleaned_data = list(zip(df['Track No'], df['Inb Prof'], df['Process Type'], df['Weight'], df['pH'], df['Age'], df['voc_ppm'], df['voc_weight'], [date.today().isoformat()]*len(df)))
            conn.executemany("INSERT INTO drum_inventory (track_no, inb_prof, process_type, weight, ph, age, voc_ppm, voc_weight, import_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", cleaned_data)
            conn.commit()
    except Exception as e: 
        return f"Critical Error processing file: {e}", 500
    
    return redirect(url_for('stu_bp.stu_inventory'))

@stu_bp.route('/export_stu', methods=['POST'])
def export_stu():
    selected_ids = request.form.getlist('selected_drums')
    category = request.form.get('category_name', 'Custom')
    if not selected_ids: return "No drums selected for export", 400
        
    placeholders = ','.join('?' for _ in selected_ids)
    with closing(get_db_connection()) as conn:
        df = pd.read_sql_query(f"SELECT track_no, inb_prof, process_type, weight, ph, age, voc_ppm, voc_weight FROM drum_inventory WHERE id IN ({placeholders})", conn, params=selected_ids)
        
    if df.empty: return "No data found", 400
    df.rename(columns={'track_no': 'Track No', 'inb_prof': 'Inb Prof', 'process_type': 'Process Type', 'weight': 'Weight', 'ph': 'pH', 'age': 'Age', 'voc_ppm': 'VOC', 'voc_weight': 'VOC Weight'}, inplace=True)
    df.insert(0, ' ', '')
    
    output = BytesIO()
    clean_category = category.replace('_', ' ')
    date_str = date.today().strftime('%m-%d-%Y')
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, startrow=1, sheet_name=f"{category[:31]}")
        worksheet = writer.sheets[f"{category[:31]}"]
        last_row = len(df) + 2  
        
        title_cell = worksheet.cell(row=1, column=1, value=f"{clean_category} - Generated On - {date.today().strftime('%m/%d/%Y')}")
        worksheet.merge_cells('A1:I1')
        title_cell.font = Font(size=18, bold=True)
        title_cell.alignment = Alignment(horizontal='center', vertical='center')
        
        for col_num in range(1, 10):
            worksheet.cell(row=2, column=col_num).font = Font(size=16, bold=True, underline='double')
            worksheet.cell(row=2, column=col_num).alignment = Alignment(horizontal='center')
            
        worksheet.column_dimensions['A'].width = 6
        thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
        
        for r in range(3, last_row + 1): 
            worksheet.cell(row=r, column=1).border = thin_border
            worksheet.cell(row=r, column=5).number_format = '0.00' 
            worksheet.cell(row=r, column=8).number_format = '0.00' 
            worksheet.cell(row=r, column=9).number_format = '0.00' 
            
        worksheet.cell(row=last_row + 2, column=3, value="Total Weight (lbs):").font = Font(bold=True)
        wt_tot = worksheet.cell(row=last_row + 2, column=4, value=f"=SUM(E3:E{last_row})")
        wt_tot.font = Font(bold=True)
        wt_tot.number_format = '#,##0.00'
        
        worksheet.cell(row=last_row + 3, column=3, value="Total Weight (tons):").font = Font(bold=True)
        wt_tons = worksheet.cell(row=last_row + 3, column=4, value=f"=D{last_row + 2}/2000")
        wt_tons.font = Font(bold=True)
        wt_tons.number_format = '#,##0.00'
        
        worksheet.cell(row=last_row + 2, column=7, value="Total VOC Weight:").font = Font(bold=True)
        voc_tot = worksheet.cell(row=last_row + 2, column=8, value=f"=SUM(I3:I{last_row})")
        voc_tot.font = Font(bold=True)
        voc_tot.number_format = '#,##0.00'
        
        worksheet.cell(row=last_row + 3, column=7, value="Average VOC:").font = Font(bold=True)
        voc_avg = worksheet.cell(row=last_row + 3, column=8, value=f"=AVERAGE(H3:H{last_row})")
        voc_avg.font = Font(bold=True)
        voc_avg.number_format = '0.00'
        
        for col_idx, col in enumerate(worksheet.columns, start=1):
            if col_idx == 1: continue 
            max_length = 0
            for cell in col:
                if cell.value is not None:
                    max_length = max(max_length, len(str(cell.value)))
            col_letter = worksheet.cell(row=2, column=col_idx).column_letter
            worksheet.column_dimensions[col_letter].width = max_length + 3

    output.seek(0)
    export_filename = f"{clean_category} {date_str}.xlsx"
    return send_file(output, as_attachment=True, download_name=export_filename, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
