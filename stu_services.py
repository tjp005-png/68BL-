import re
import math
import io
from io import BytesIO
from datetime import datetime
from collections import defaultdict
import pdfplumber

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet
from pypdf import PdfWriter, PdfReader

TARGET_ROUTING_CODE = "BL"
PERMITTED_CODES = {'CBP', 'CNO', 'CBPS', 'CNOS', 'CNIA', 'CCS', 'CCSS', 'D23', 'D80L', 'LLF'}

def safe_xml(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def evaluate_and_update_voc_requirement(conn, profile, drum_id, manifest, status):
    if not profile or profile == "N/A": return "UNKNOWN PROFILE"

    today_str = datetime.now().strftime("%Y-%m-%d")
    trigger_alert = ""
    
    from database import ensure_profile_exists
    ensure_profile_exists(conn, profile)
    
    master_row = conn.execute("SELECT voc_percentage FROM profiles WHERE profile_number = ?", (profile,)).fetchone()
    try:
        baseline_voc = float(master_row[0]) if (master_row and master_row[0] is not None) else -1.0
    except (ValueError, TypeError):
        baseline_voc = -1.0
    cp_number = "________" 

    tracker_row = conn.execute("SELECT drums_since_last_test, last_voc_test_date FROM compliance_tracker WHERE profile = ?", (profile,)).fetchone()

    if not tracker_row:
        conn.execute("INSERT INTO compliance_tracker (profile, last_voc_test_date, drums_since_last_test) VALUES (?, ?, ?)", (profile, today_str, 1))
        drums_count = 1
    else:
        try:
            drums_count = int(tracker_row[0]) + 1
        except (ValueError, TypeError):
            drums_count = 1
    
    if status == "NOT FOUND" or (not tracker_row and baseline_voc < 0):
        trigger_alert = f"NEW PROFILE CP1 / VOC (CP: {cp_number})"
        drums_count = 1
        conn.execute("UPDATE compliance_tracker SET last_voc_test_date = ? WHERE profile = ?", (today_str, profile))
        
    elif status == "EXPIRED":
        trigger_alert = f"EXPIRED PROFILE RECERT / VOC (CP: {cp_number})"
        drums_count = 1
        conn.execute("UPDATE compliance_tracker SET last_voc_test_date = ? WHERE profile = ?", (today_str, profile))
        
    elif baseline_voc >= 50.0 and drums_count >= 10:
        trigger_alert = "Fingerprint / VOC Test"
        drums_count = 1
        conn.execute("UPDATE compliance_tracker SET last_voc_test_date = ? WHERE profile = ?", (today_str, profile))
        
    else:
        trigger_alert = "FingerPrint"
        
    conn.execute("UPDATE compliance_tracker SET drums_since_last_test = ? WHERE profile = ?", (drums_count, profile))

    if "VOC" in trigger_alert or "CP1" in trigger_alert or "RECERT" in trigger_alert:
        existing = conn.execute("SELECT log_id FROM audit_log WHERE drum_id = ?", (drum_id,)).fetchone()
        if existing:
            conn.execute('''
                UPDATE audit_log SET timestamp = ?, trigger_reason = ? WHERE drum_id = ?
            ''', (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), trigger_alert, drum_id))
        else:
            conn.execute('''
                INSERT INTO audit_log (timestamp, profile, drum_id, manifest, trigger_reason, chemist_name, voc_result)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), profile, drum_id, manifest, trigger_alert, "PENDING", None))

    return trigger_alert

def process_drums(conn, drums):
    picklist = []
    total_samples = 0
    sample_counter = 1 
    session_tested_profiles = set()
    
    processed_drums = []
    for d in drums:
        status = "VALID"
        profile_num = d.get('profile','')
        from database import ensure_profile_exists
        ensure_profile_exists(conn, profile_num)
        prof_row = conn.execute("SELECT expiration_date, special_handling FROM profiles WHERE profile_number = ?", (profile_num,)).fetchone()
        
        if not prof_row: 
            status = "NOT FOUND"
        elif prof_row['expiration_date']:
            exp_str = str(prof_row['expiration_date']).strip().lower()
            if exp_str != "no date":
                try:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d")
                    if exp_date < datetime.now(): status = "EXPIRED"
                except: pass
                
        if d.get('profile', '') in session_tested_profiles:
            status = "VALID"
            
        voc_req = evaluate_and_update_voc_requirement(conn, d['profile'], d['drum_id'], d['manifest'], status)
        
        if "VOC" in voc_req or "RECERT" in voc_req or "NEW" in voc_req:
            session_tested_profiles.add(d.get('profile', ''))
            is_magnet = True
        else:
            is_magnet = False
            
        coding_notes = prof_row['special_handling'] if prof_row and prof_row['special_handling'] else ""
        processed_drums.append({
            **d, "voc_testing_trigger": voc_req, "coding_notes": coding_notes, "is_magnet": is_magnet
        })

    asbestos_drums = [d for d in processed_drums if d.get('is_asbestos')]
    other_drums = [d for d in processed_drums if not d.get('is_asbestos')]

    for d in asbestos_drums:
        # SURFACES ASBESTOS IN THE REPORTS
        picklist.append({**d, "reason_not_sampled": "Asbestos - DO NOT SAMPLE", "is_sampled": "No", "sample_num": "ASBESTOS"})

    groups = defaultdict(list)
    for d in other_drums:
        groups[(d['manifest'], d['manifest_line'])].append(d)

    for _, drum_list in groups.items():
        count = len(drum_list)
        base_required = math.ceil(count / 10.0) 
        
        drum_list.sort(key=lambda x: not x['is_magnet'])
        
        for i, d in enumerate(drum_list):
            is_target = (i < base_required) or d['is_magnet']
            s_num = ""
            if is_target:
                s_num = f"#{sample_counter}"
                sample_counter += 1
                total_samples += 1
            
            picklist.append({
                **d, "reason_not_sampled": "" if is_target else "Not Selected",
                "is_sampled": "Yes" if is_target else "No", "sample_num": s_num
            })

    return picklist, total_samples

def parse_drum_labels_from_pdf(file_stream):
    all_drums = []
    try:
        with pdfplumber.open(file_stream) as pdf:
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text(x_tolerance=1, y_tolerance=3)
                words = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=True)
                if not text: continue
                
                header_text = text[:800].upper()
                
                # UPGRADED: Allows CNIA through even if the routing code isn't BL, and supports 68BL style routing codes
                has_bl = re.search(rf'\b\d*{TARGET_ROUTING_CODE}\b', header_text)
                has_cnia = 'CNIA' in header_text
                if not has_bl and not has_cnia: 
                    continue

                drum_x, drum_y, page_h = 100, 700, page.height
                excluded_values = set()
                
                drum_id_match = re.search(r'\b(A00[A-Z0-9]{5,})\b', text, re.IGNORECASE)
                drum_id_val = drum_id_match.group(1).upper() if drum_id_match else None
                if drum_id_val: 
                    excluded_values.add(drum_id_val)
                    for w in words:
                        if drum_id_val in w['text']:
                            drum_x, drum_y = w['x1'], page_h - w['top'] 
                            break

                # Support PROFILE#: style labels
                profile_match = re.search(r'(?:Profile|Prof)[:\s#]*([A-Z0-9-]+)', text, re.IGNORECASE)
                profile_val = profile_match.group(1).upper() if profile_match else None
                if profile_val: excluded_values.add(profile_val)

                display_profile = profile_val if profile_val else "N/A"

                if profile_val and ('INTER' in profile_val or profile_val == 'SBND'):
                    comments_match = re.search(r'Comments?[\s:]+([A-Z0-9\-]{5,})', text, re.IGNORECASE)
                    if comments_match:
                        orig_profile = comments_match.group(1).upper()
                        display_profile = f"{profile_val} (Orig: {orig_profile})"
                        profile_val = orig_profile 

                manifest_str, min_x0 = "N/A", page.width 
                IGNORE_LIST = ['WEIGHT', 'PROFILE', 'FACILITY', 'GENERATOR', 'WASTE', 'UN/NA', 'INVENTORY', 'CUSTOMER', 'DRUM', 'MGT', 'NUMBER', 'SHIPPER', 'DATE', 'TRACKING', 'ADDRESS', 'TELEPHONE', 'ZIP', 'CAR', 'EPA', 'CLASS', 'HAZARD', 'ACCUMULATION']

                for word in words:
                    # Split by whitespace to handle combined words caused by keep_blank_chars=True
                    parts = word['text'].split()
                    for part in parts:
                        clean_part = re.sub(r'[^\w\-]', '', part).upper()
                        if (len(clean_part) >= 5 and any(c.isdigit() for c in clean_part) and 
                            re.match(r'^[A-Z0-9-]+$', clean_part) and clean_part not in excluded_values and 
                            not any(x in clean_part for x in IGNORE_LIST)):
                            if word['x0'] < min_x0:
                                min_x0 = word['x0']
                                manifest_str = clean_part

                is_asbestos, manifest_line_num, container_size, waste_code = False, "N/A", "N/A", "N/A"
                
                # UPGRADED: Immediately catch CNIA from the profile
                if profile_val and 'CNIA' in profile_val.upper():
                    is_asbestos = True
                    
                search_anchor = manifest_str if manifest_str != "N/A" else profile_val

                if search_anchor:
                    lines = text.split('\n')
                    for line in lines:
                        line_upper = line.upper()
                        cleaned_line = re.sub(r'\s+', '', line_upper)
                        
                        if search_anchor in cleaned_line:
                            if 'CNIA' in line_upper: is_asbestos = True
                            try:
                                # Find where search anchor is in the raw line (so we preserve spaces for rest of the line)
                                idx = line_upper.find(search_anchor) + len(search_anchor)
                                rest_of_line = line[idx:]
                                
                                # Unified line number match: supports both " 1" and " Line#: 1"
                                line_num_match = re.search(r'^\s*(?:Line#?[:\s]*)?([0-9]+[A-Z]?)', rest_of_line, re.IGNORECASE)
                                if line_num_match: 
                                    manifest_line_num = line_num_match.group(1)
                                    
                                # Search manifest line for container size
                                size_match = re.search(r'\b(?:(\d{1,4}\s?(?:DM|DF|DP|CF|TP|GAL|G|TT|FBIN|BIN|BA))|((?:PAL|BAG|BA|CTN|BOX|CY|YARD|YD|FBIN|BIN)))\b', rest_of_line, re.IGNORECASE)
                                if size_match: 
                                    container_size = (size_match.group(1) if size_match.group(1) else size_match.group(2)).upper()
                                    wc_match = re.search(r'\b([A-Z0-9]{3,4})\b', rest_of_line[size_match.end():].strip())
                                    if wc_match: 
                                        waste_code = wc_match.group(1).upper()
                            except Exception as ex: 
                                print(f"Error parsing detail lines: {ex}")
                            break 
                
                # Fallback searches if container size or waste code are not found on the manifest line
                if container_size == "N/A":
                    size_match = re.search(r'\b(?:(\d{1,4}\s?(?:DM|DF|DP|CF|TP|GAL|G|TT|FBIN|BIN|BA))|((?:PAL|BAG|BA|CTN|BOX|CY|YARD|YD|FBIN|BIN)))\b', text, re.IGNORECASE)
                    if size_match: 
                        container_size = (size_match.group(1) if size_match.group(1) else size_match.group(2)).upper()
                        
                if waste_code not in PERMITTED_CODES:
                    for code in PERMITTED_CODES:
                        if re.search(rf'\b{code}\b', text.upper()):
                            waste_code = code
                            break
                            
                # UPGRADED: Never delete asbestos drums just because they lack a waste code
                if waste_code not in PERMITTED_CODES and not is_asbestos: 
                    continue
                
                all_drums.append({
                    "drum_id": drum_id_val if drum_id_val else "UNKNOWN",
                    "profile": profile_val if profile_val else "N/A", "display_profile": display_profile,
                    "manifest": manifest_str, "manifest_line": manifest_line_num,
                    "is_asbestos": is_asbestos, "container_size": container_size,
                    "waste_code": waste_code, "page_index": page_num, 
                    "coord_x": drum_x, "coord_y": drum_y,
                    "page_width": float(page.width), "page_height": float(page.height)
                })
        return all_drums
    except Exception as e:
        print(f"Error parsing PDF: {e}")
        return []

def create_lab_sheet_pdf(output_buffer, job_name, picklist_data):
    doc = SimpleDocTemplate(output_buffer, pagesize=landscape(letter), topMargin=0.5*inch, bottomMargin=0.5*inch, leftMargin=0.5*inch, rightMargin=0.5*inch)
    story = []
    styles = getSampleStyleSheet()
    
    # Lab Sheet correctly ignores Asbestos
    sampled_drums = [d for d in picklist_data if d['is_sampled'] == "Yes"]
    if not sampled_drums: return 

    story.append(Spacer(1, 0.2*inch)) 
    sig_data = [["Chemist's Name:", "________________________", "Time Testing Start:", "________________", "Time Finished:", "________________"]]
    sig_table = Table(sig_data, colWidths=[1.3*inch, 2.5*inch, 1.4*inch, 1.8*inch, 1.2*inch, 1.8*inch])
    sig_table.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'LEFT'), ('VALIGN', (0,0), (-1,-1), 'BOTTOM'), ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'), ('FONTSIZE', (0,0), (-1,-1), 10)]))
    story.append(sig_table)
    story.append(Spacer(1, 0.3*inch))

    # Split drums into groups
    cp1_drums = []
    voc_fp_drums = []
    remaining_drums = []
    
    for row in sampled_drums:
        trig = str(row.get('voc_testing_trigger', '')).upper()
        if "CP1" in trig or "RECERT" in trig or "NEW" in trig:
            cp1_drums.append(row)
        elif "VOC" in trig:
            voc_fp_drums.append(row)
        else:
            remaining_drums.append(row)

    col_widths = [0.6*inch, 1.0*inch, 1.3*inch, 0.5*inch, 1.4*inch, 0.8*inch, 0.8*inch, 1.2*inch, 0.8*inch, 1.6*inch]
    header = ["Sample #", "Drum ID", "Profile", "Code", "Tests Required", "FP Result", "pH Result", "VOC (Pass/Fail)", "Treatment", "Notes"]

    def build_group_table(drums_list, title_text):
        story.append(Paragraph(f"<b>{title_text}</b>", styles['h4']))
        story.append(Spacer(1, 0.05*inch))
        
        table_data = [header]
        for row in drums_list:
            voc_trigger = safe_xml(row.get('voc_testing_trigger', ''))
            disp_profile = safe_xml(row.get('display_profile', row['profile']))
            coding_notes = safe_xml(row.get('coding_notes', ''))
            waste_code = safe_xml(row.get('waste_code', ''))
            
            if "VOC" in voc_trigger or "RECERT" in voc_trigger or "NEW" in voc_trigger:
                test_para = Paragraph(f"<font color='red'><b>{voc_trigger}</b></font>", styles['Normal'])
            else:
                test_para = Paragraph(f"{voc_trigger}", styles['Normal'])
                
            notes_para = Paragraph(f"<i>{coding_notes}</i>", styles['Normal'])
            table_data.append([
                row.get('sample_num', '-'), 
                row['drum_id'], 
                disp_profile, 
                waste_code, 
                test_para, 
                "", 
                "", 
                "", 
                "", 
                notes_para
            ])
            
        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('TEXTCOLOR', (0,0), (-1,0), colors.black), ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,0), 9), ('GRID', (0,0), (-1,-1), 1, colors.black),
            ('BACKGROUND', (0,0), (-1,0), colors.lightgrey), ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('TOPPADDING', (0,0), (-1,-1), 8),
        ]))
        story.append(t)
        story.append(Spacer(1, 0.25*inch))

    if cp1_drums:
        build_group_table(cp1_drums, "1. NEW PROFILE & RECERTIFICATION (CP1 / RECERT VOC TESTS REQUIRED)")
    if voc_fp_drums:
        build_group_table(voc_fp_drums, "2. COMPLIANCE FINGERPRINT with VOC TEST (1-in-10 Rule)")
    if remaining_drums:
        build_group_table(remaining_drums, "3. STANDARD FINGERPRINT ONLY (No VOC Required)")

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawCentredString(landscape(letter)[0]/2, landscape(letter)[1]-0.5*inch, f"Lab Bench Sheet - {job_name}")
        canvas.setFont("Helvetica", 10)
        canvas.drawString(0.5*inch, landscape(letter)[1]-0.5*inch, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)

def create_annotated_pdf(original_pdf_stream, output_buffer, picklist_data):
    items_to_stamp = [d for d in picklist_data if d['is_sampled'] == "Yes" and d.get('page_index', -1) != -1]
    if not items_to_stamp: return

    reader, writer = PdfReader(original_pdf_stream), PdfWriter()
    stamps_by_page = defaultdict(list)
    for item in items_to_stamp: stamps_by_page[item['page_index']].append(item)

    for page_num, page in enumerate(reader.pages):
        if page_num in stamps_by_page:
            packet = io.BytesIO()
            w, h = stamps_by_page[page_num][0]['page_width'], stamps_by_page[page_num][0]['page_height']
            can = canvas.Canvas(packet, pagesize=(w, h))
            for item in stamps_by_page[page_num]:
                x, y, text = item['coord_x'], item['coord_y'], item['sample_num']
                
                # Position stamp in lower right quadrant if it is a new landscape format label, otherwise use original coordinates
                if w > h:
                    cx, cy = w - 80, 70
                else:
                    cx, cy = x - 15, y + 25
                    
                is_double = len(text) > 2
                radius, font_size = (35, 34) if is_double else (30, 36)

                can.setFillColor(colors.white); can.setStrokeColor(colors.white)
                can.circle(cx, cy + 5, radius, stroke=1, fill=1)
                can.setStrokeColor(colors.black); can.setLineWidth(4)
                can.circle(cx, cy + 5, radius, stroke=1, fill=0)
                can.setFillColor(colors.black); can.setFont("Helvetica-Bold", font_size)
                can.drawCentredString(cx, cy - (font_size/3) + 5, text)
            
            can.save(); packet.seek(0)
            page.merge_page(PdfReader(packet).pages[0])
        writer.add_page(page)

    writer.write(output_buffer)

def create_pdf_report(output_buffer, title_text, picklist_data, total_samples):
    doc = SimpleDocTemplate(output_buffer, pagesize=letter, topMargin=1.0*inch, bottomMargin=1.0*inch, leftMargin=0.75*inch, rightMargin=0.75*inch)
    story, styles = [], getSampleStyleSheet()
    total_items_in_load = len(picklist_data) 
    
    # REVERTED: Only include items that are marked as "Yes" for sampling (ignores Asbestos)
    pdf_rows = [row for row in picklist_data if row['is_sampled'] == "Yes"]
    
    # Sorts regular samples numerically by their Sample ID (#1, #2, etc.)
    pdf_rows.sort(key=lambda x: (
        int(x.get('sample_num', '').replace('#', '')) if str(x.get('sample_num', '')).startswith('#') else 999999,
        x.get('drum_id', '')
    ))
    
    header = ["#", "Sample ID", "Drum ID", "Manifest", "Profile", "Code", "Pulled"]
    data = [header]

    for i, row in enumerate(pdf_rows, 1):
        dp = safe_xml(row.get('display_profile', row['profile']))
        waste_code = safe_xml(row.get('waste_code', ''))
        sample_val = row.get('sample_num', '-')
            
        data.append([i, sample_val, row['drum_id'], row['manifest'], dp, waste_code, ""])

    if len(data) == 1:
        story.append(Paragraph("No items in list.", styles['Normal']))
    else:
        t = Table(data, colWidths=[0.3*inch, 1.1*inch, 1.3*inch, 1.2*inch, 1.4*inch, 0.7*inch, 1.0*inch], repeatRows=1)
        t.setStyle(TableStyle([
            ('TEXTCOLOR', (0,0), (-1,0), colors.black), ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('GRID', (0,0), (-1,-1), 1, colors.black), ('FONTSIZE', (0,1), (-1,-1), 9),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
        ]))
        story.append(t)

    story.append(Spacer(1, 0.4*inch))
    s_style = styles['h4']
    s_style.leading = 16
    story.append(Paragraph(f"Total Samples Required: {total_samples}", s_style))
    story.append(Paragraph(f"Total Drums on Trailer: {total_items_in_load}", s_style))
    story.append(Spacer(1, 0.3*inch))
    
    story.append(Paragraph("<b>Discrepancies (Missing / Extra Drums)</b>", styles['h4']))
    disc_data = [["Drum ID", "Missing or Extra?", "Profile / Notes"]]
    for _ in range(4): disc_data.append(["", "", ""])
        
    disc_table = Table(disc_data, colWidths=[2.0*inch, 1.5*inch, 3.5*inch])
    disc_table.setStyle(TableStyle([
        ('TEXTCOLOR', (0,0), (-1,0), colors.black), ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'), ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey), ('GRID', (0,0), (-1,-1), 1, colors.black), 
        ('BOTTOMPADDING', (0,1), (-1,-1), 16), 
    ]))
    story.append(disc_table)
    
    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawCentredString(letter[0]/2, letter[1]-0.75*inch, title_text)
        canvas.setFont("Helvetica", 10)
        canvas.drawString(inch, letter[1]-0.95*inch, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)