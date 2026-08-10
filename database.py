# database.py
import sqlite3
import os
import re

_excel_cache = None
_excel_cache_mtime = None

import shared_state
from shared_state import MASTER_EXCEL_PATH

def get_db_connection(db_path=None):
    target_db = db_path or shared_state.DB_PATH
    conn = sqlite3.connect(target_db, timeout=30)
    conn.row_factory = sqlite3.Row 
    conn.execute('PRAGMA busy_timeout = 30000;')
    
    # Check if database is on a network drive to avoid WAL mode locks/hangs
    is_network = False
    if target_db.startswith(r'\\'):
        is_network = True
    elif os.name == 'nt':
        try:
            import ctypes
            drive = os.path.splitdrive(os.path.abspath(target_db))[0]
            if drive:
                is_network = (ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4)
        except:
            pass
            
    if is_network:
        conn.execute('PRAGMA journal_mode=DELETE;')
    else:
        conn.execute('PRAGMA journal_mode=WAL;')
        
    return conn

def parse_ph_from_any_text(*text_sources):
    for src in text_sources:
        if not src:
            continue
        s = str(src).strip().upper()
        if 'PH' not in s:
            continue

        range_match = re.search(r'PH[^\d]*(\d+\.?\d*)\s*(?:TO|-|UNTIL)\s*(\d+\.?\d*)', s)
        if range_match:
            try:
                min_v = float(range_match.group(1))
                max_v = float(range_match.group(2))
                if min_v <= max_v and max_v <= 14.0:
                    return f"{min_v} - {max_v}"
            except:
                pass

        if 'NEUTRAL' in s or 'PH 7' in s or 'PH: 7' in s:
            return "4.0 - 10.0"

        gt_match = re.search(r'PH\s*[>=]+\s*(\d+\.?\d*)', s)
        if gt_match:
            try:
                val = float(gt_match.group(1))
                if val <= 14.0:
                    return f"{val} - 14.0"
            except:
                pass

        lt_match = re.search(r'PH\s*[<=]+\s*(\d+\.?\d*)', s)
        if lt_match:
            try:
                val = float(lt_match.group(1))
                if val <= 14.0:
                    return f"0.0 - {val}"
            except:
                pass

    return None

def extract_color_from_text(text):
    if not text:
        return None
    t = str(text).upper()
    colors = ['BROWN', 'BLACK', 'GRAY', 'GREY', 'YELLOW', 'RED', 'WHITE', 'TAN', 'GREEN', 'BLUE', 'CREAM', 'CLEAR', 'VARIOUS']
    matched = [c for c in colors if c in t]
    return "/".join(matched) if matched else None

def sync_profile_from_wvi_file(conn, profile_number):
    """
    On-demand WVI File Sync (Per-Profile).
    Reads from I:\Buttonwillow\WAP\WVI\<PROFILE>.xls / .xlsx
    Caches parsed specs in SQLite profile_wvi table.
    If no WVI file is found, caches is_synced = 1 in profile_wvi to prevent repeated network searches.
    """
    if not profile_number:
        return None
        
    profile_clean = str(profile_number).strip().upper()

    # Fast cached check: if already in profile_wvi with is_synced = 1, return cached record
    try:
        w_row = conn.execute("SELECT * FROM profile_wvi WHERE TRIM(UPPER(profile)) = ?", (profile_clean,)).fetchone()
        if w_row and w_row['is_synced'] == 1:
            return dict(w_row)
    except Exception:
        w_row = None
        
    wvi_dir = r"I:\Buttonwillow\WAP\WVI"
    if not os.path.exists(wvi_dir):
        return dict(w_row) if w_row else None
        
    # Search for files matching the profile number
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
        # Cache that no WVI file was found on network so we don't repeat network searches
        try:
            conn.execute("INSERT OR IGNORE INTO profile_wvi (profile, is_synced) VALUES (?, 1)", (profile_clean,))
            conn.execute("UPDATE profile_wvi SET is_synced = 1 WHERE TRIM(UPPER(profile)) = ?", (profile_clean,))
            conn.commit()
        except Exception as ex:
            print(f"Error caching missing WVI for profile {profile_clean}: {ex}")
        return dict(w_row) if w_row else None
        
    try:
        import pandas as pd
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
            'voc_ppm': None, 'treatment_information': None, 'notes_revisions': None, 'color': None, 'is_synced': 1
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
                serial = float(val_str)
                dt = pd.to_datetime(serial, unit='D', origin='1899-12-30')
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                return val_str

        def join_row_values(row_data):
            vals = []
            for v in row_data:
                if pd.isna(v): continue
                v_str = str(v).strip()
                if not v_str: continue
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
                data['color'] = extract_color_from_text(data['physical_description'])
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
            if not data['color']:
                data['color'] = extract_color_from_text(data['notes_revisions'])
                    
        cols_order = [
            'profile', 'filename', 'generator_name', 'waste_name', 'physical_description',
            'ldr', 'state_waste_codes', 'federal_waste_codes', 'dot_description',
            'handling_instruction', 'sample_procedures', 'verification_procedures',
            'ph_min', 'ph_max', 'sulfides', 'cyanide', 'free_liquids', 'flashpoint',
            'unloading_instructions', 'reactivity_codes', 'approved_date',
            'expiration_date', 'lab_num', 'voc_ppm', 'treatment_information',
            'notes_revisions', 'color', 'is_synced'
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
        print(f"Error parsing WVI file {file_path} for {profile_clean}: {e}")
        return dict(w_row) if w_row else None

def enrich_profile_from_wvi(conn, clean_profile):
    """Enriches a profile in the 'profiles' table with WVI specs (pH, color, sulfides, cyanide, etc.)."""
    try:
        # Trigger on-demand sync from WVI file if not already cached
        sync_profile_from_wvi_file(conn, clean_profile)

        p_row = conn.execute("SELECT * FROM profiles WHERE TRIM(UPPER(profile_number)) = ?", (clean_profile,)).fetchone()
        w_row = conn.execute("SELECT * FROM profile_wvi WHERE TRIM(UPPER(profile)) = ?", (clean_profile,)).fetchone()
        
        if p_row and w_row:
            updates = []
            params = []
            
            # 1. pH Range
            if not p_row['ph_range'] or p_row['ph_range'] == '---':
                ph_str = f"{w_row['ph_min']} - {w_row['ph_max']}".strip(" -") if (w_row['ph_min'] is not None or w_row['ph_max'] is not None) else None
                if not ph_str:
                    ph_str = parse_ph_from_any_text(
                        w_row['notes_revisions'],
                        w_row['verification_procedures'],
                        w_row['physical_description'],
                        w_row['handling_instruction']
                    )
                if ph_str:
                    updates.append("ph_range = ?")
                    params.append(ph_str)

            # 2. Color
            if not p_row['color'] or p_row['color'] == '---':
                color_str = w_row['color'] if (w_row['color'] and str(w_row['color']).strip() != 'None') else None
                if not color_str:
                    color_str = extract_color_from_text(w_row['physical_description']) or extract_color_from_text(w_row['notes_revisions'])
                if color_str:
                    updates.append("color = ?")
                    params.append(color_str)

            # 3. Physical appearance
            if not p_row['physical_appearance'] and w_row['physical_description']:
                updates.append("physical_appearance = ?")
                params.append(w_row['physical_description'])

            # 4. DOT Description
            if not p_row['dot_description'] and w_row['dot_description']:
                updates.append("dot_description = ?")
                params.append(w_row['dot_description'])

            # 5. Handling
            if not p_row['special_handling'] and w_row['handling_instruction']:
                updates.append("special_handling = ?")
                params.append(w_row['handling_instruction'])

            # 6. Flashpoint
            if not p_row['flash_point'] and w_row['flashpoint']:
                updates.append("flash_point = ?")
                params.append(w_row['flashpoint'])

            # 7. VOC percentage
            if (p_row['voc_percentage'] is None or p_row['voc_percentage'] == 0.0) and w_row['voc_ppm'] is not None:
                updates.append("voc_percentage = ?")
                params.append(w_row['voc_ppm'])

            # 8. State / Federal Waste Codes
            if not p_row['state_waste_code'] and w_row['state_waste_codes']:
                updates.append("state_waste_code = ?")
                params.append(w_row['state_waste_codes'])
            if not p_row['federal_waste_code'] and w_row['federal_waste_codes']:
                updates.append("federal_waste_code = ?")
                params.append(w_row['federal_waste_codes'])

            # 9. Treatment Recipe / Information
            if not p_row['treatment_recipe'] and w_row['treatment_information']:
                updates.append("treatment_recipe = ?")
                params.append(w_row['treatment_information'])

            if updates:
                sql = f"UPDATE profiles SET {', '.join(updates)} WHERE TRIM(UPPER(profile_number)) = ?"
                params.append(clean_profile)
                conn.execute(sql, params)
                conn.commit()
    except Exception as e:
        print(f"Error enriching profile {clean_profile} from WVI: {e}")

def _load_excel_dataframe(excel_path):
    global _excel_cache, _excel_cache_mtime
    import pandas as pd
    import shutil
    import io
    
    try:
        mtime = os.path.getmtime(excel_path)
    except Exception:
        if _excel_cache is not None:
            return _excel_cache
        raise
        
    if _excel_cache is not None and _excel_cache_mtime == mtime:
        return _excel_cache
    
    def _read_file(target_path):
        with open(target_path, 'rb') as f:
            file_bytes = io.BytesIO(f.read())
        with pd.ExcelFile(file_bytes, engine='openpyxl') as xl:
            sheet_to_use = 'Data' if 'Data' in xl.sheet_names else 0
            if sheet_to_use == 0 and len(xl.sheet_names) > 1:
                for sname in xl.sheet_names:
                    sample_df = pd.read_excel(xl, sheet_name=sname, nrows=5)
                    for col in sample_df.columns:
                        if str(col).strip().upper() in ['PROFILE', 'PROFILE #', 'PROFILE_NUMBER', 'PROFILE NUMBER']:
                            sheet_to_use = sname
                            break
                    if sheet_to_use != 0:
                        break
            return pd.read_excel(xl, sheet_name=sheet_to_use)

    try:
        df = _read_file(excel_path)
        _excel_cache = df
        _excel_cache_mtime = mtime
        return df
    except PermissionError:
        temp_copy = excel_path + ".tmp"
        try:
            shutil.copy2(excel_path, temp_copy)
            df = _read_file(temp_copy)
            if os.path.exists(temp_copy):
                try: os.remove(temp_copy)
                except: pass
            _excel_cache = df
            _excel_cache_mtime = mtime
            return df
        except Exception as e:
            if os.path.exists(temp_copy):
                try: os.remove(temp_copy)
                except: pass
            if _excel_cache is not None:
                return _excel_cache
            raise e
    except Exception as e:
        if _excel_cache is not None:
            return _excel_cache
        raise e

def ensure_profile_exists(conn, profile_number, excel_path=None):
    if not profile_number or not str(profile_number).strip():
        return None
        
    clean_profile = str(profile_number).strip().upper()
    excel_path = excel_path or shared_state.MASTER_EXCEL_PATH
    
    row = conn.execute('SELECT * FROM profiles WHERE TRIM(UPPER(profile_number)) = ?', (clean_profile,)).fetchone()
    
    excel_mtime = None
    try:
        if os.path.exists(excel_path):
            excel_mtime = os.path.getmtime(excel_path)
    except:
        pass
        
    if row and row['last_synced_mtime'] and excel_mtime and row['last_synced_mtime'] == excel_mtime:
        enrich_profile_from_wvi(conn, clean_profile)
        return conn.execute('SELECT * FROM profiles WHERE TRIM(UPPER(profile_number)) = ?', (clean_profile,)).fetchone()

    try:
        if not os.path.exists(excel_path):
            enrich_profile_from_wvi(conn, clean_profile)
            return row

        df = _load_excel_dataframe(excel_path)
        
        prof_col = None
        for col in df.columns:
            if str(col).strip().upper() in ['PROFILE', 'PROFILE #', 'PROFILE_NUMBER', 'PROFILE NUMBER']:
                prof_col = col
                break
                
        if not prof_col:
            enrich_profile_from_wvi(conn, clean_profile)
            return row
            
        matching = df[df[prof_col].astype(str).str.strip().str.upper() == clean_profile]
        if matching.empty:
            raise ValueError(f"Profile {clean_profile} not found in Excel")
            
        # Handle potential duplicates in the master Excel sheet by preferring the Active entry or the latest one
        if len(matching) > 1:
            if 'STATUS' in df.columns:
                active_matches = matching[matching['STATUS'].astype(str).str.strip().str.upper().isin(['A', 'ACTIVE'])]
                if not active_matches.empty:
                    row_data = active_matches.iloc[-1]
                else:
                    row_data = matching.iloc[-1]
            else:
                row_data = matching.iloc[-1]
        else:
            row_data = matching.iloc[0]
        
        generator = str(row_data.get('GENERATOR', '')).strip()
        status_val = str(row_data.get('STATUS', 'ACTIVE')).strip()
        if not status_val or status_val.lower() in ['nan', 'none']:
            status_val = 'ACTIVE'
        elif status_val.upper() in ['A', 'ACTIVE']:
            status_val = 'ACTIVE'
        elif status_val.upper() in ['E', 'EXP', 'EXPIRED']:
            status_val = 'EXPIRED'
            
        exp_date = 'No Date'
        if 'EXP DATE' in df.columns:
            val = row_data.get('EXP DATE')
            import pandas as pd
            from datetime import datetime
            if not pd.isna(val):
                try:
                    exp_date = pd.to_datetime(val, errors='coerce').strftime('%Y-%m-%d')
                    if not exp_date or pd.isna(exp_date):
                        exp_date = str(val)
                except:
                    exp_date = str(val)

        # PRIORITY RULE: Expiration date in the past automatically overrides status to 'EXPIRED'
        if exp_date and exp_date != 'No Date':
            try:
                import pandas as pd
                from datetime import datetime
                dt = pd.to_datetime(exp_date, errors='coerce')
                if pd.notna(dt) and dt.date() < datetime.now().date():
                    status_val = 'EXPIRED'
            except Exception:
                pass
                    
        waste_name = str(row_data.get('WASTE NAME', '')).strip()
        
        voc_percentage = 0.0
        if 'VOC #' in df.columns:
            voc_str = str(row_data.get('VOC #', '')).strip()
            if voc_str.upper() in ['TBD', '?']:
                voc_percentage = None
            else:
                voc_match = re.search(r'(\d+\.?\d*)', voc_str)
                if voc_match:
                    try:
                        voc_percentage = float(voc_match.group(1))
                    except:
                        voc_percentage = 0.0
                else:
                    voc_percentage = 0.0
                    
        win_code = str(row_data.get('WIN CODE', '')).strip()
        lab_number = str(row_data.get('LAB #', '')).strip()
        if lab_number.lower() in ['nan', 'none']:
            lab_number = ''
        haz = str(row_data.get('HAZ', '')).strip()
        rcra = str(row_data.get('RCRA', '')).strip()
        comments = str(row_data.get('COMMENTS', '')).strip()
        
        # Preserve user-edited CP1/lab_number in SQLite if Excel lab_number is blank
        if row and row['lab_number'] and str(row['lab_number']).strip() and not lab_number:
            lab_number = str(row['lab_number']).strip()

        # Preserve user-edited status in SQLite if Excel status is blank/nan
        if row and row['status'] and str(row['status']).strip() and status_val.lower() in ['', 'nan', 'none']:
            status_val = str(row['status']).strip()

        epa_id = ''
        if 'EPA ID' in df.columns:
            epa_id = str(row_data.get('EPA ID', '')).strip()
        elif 'EPA_ID' in df.columns:
            epa_id = str(row_data.get('EPA_ID', '')).strip()
            
        c_upper = str(comments or '').upper()
        w_upper = str(win_code or '').strip().upper()
        if 'SIP' in c_upper or 'TREA' in c_upper:
            container_type = 'Containerized'
        elif w_upper in ['CNOS', 'CBPS']:
            container_type = 'Bulk Liquid'
        else:
            container_type = 'Bulk Solid'

        # Waste Acceptance Log override check (Waste Acceptance Log is HIGHEST PRIORITY)
        wa_override = conn.execute('''
            SELECT expiration_date, status FROM waste_acceptance_log 
            WHERE TRIM(UPPER(profile_number)) = ? AND COALESCE(is_archived, 0) = 0
        ''', (clean_profile,)).fetchone()
        if wa_override:
            if wa_override['expiration_date'] and str(wa_override['expiration_date']).strip():
                exp_date = str(wa_override['expiration_date']).strip()
            if wa_override['status'] in ['Recertified', 'Complete', 'Released', 'ACTIVE']:
                status_val = 'ACTIVE'

        if row:
            conn.execute('''
                UPDATE profiles 
                SET generator = ?, status = ?, expiration_date = ?, 
                    waste_description = ?, voc_percentage = ?, win_code = ?,
                    last_synced_mtime = ?, epa_id = ?, lab_number = ?, haz = ?, rcra = ?, comments = ?,
                    shipping_container_type = ?
                WHERE TRIM(UPPER(profile_number)) = ?
            ''', (generator, status_val, exp_date, waste_name, voc_percentage, win_code, excel_mtime, epa_id, lab_number, haz, rcra, comments, container_type, clean_profile))
        else:
            conn.execute('''
                INSERT INTO profiles (
                    profile_number, generator, status, expiration_date, 
                    waste_description, voc_percentage, win_code, last_synced_mtime, epa_id,
                    lab_number, haz, rcra, comments, shipping_container_type
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (clean_profile, generator, status_val, exp_date, waste_name, voc_percentage, win_code, excel_mtime, epa_id, lab_number, haz, rcra, comments, container_type))
            
        conn.commit()
        enrich_profile_from_wvi(conn, clean_profile)
        
        return conn.execute('SELECT * FROM profiles WHERE TRIM(UPPER(profile_number)) = ?', (clean_profile,)).fetchone()

    except Exception as e:
        print(f"Profile {clean_profile} not found in master Excel or error occurred: {e}")
        try:
            wvi = conn.execute("SELECT * FROM profile_wvi WHERE TRIM(UPPER(profile)) = ?", (clean_profile,)).fetchone()
            if wvi:
                ph_str = f"{wvi['ph_min']} - {wvi['ph_max']}".strip(" -") if (wvi['ph_min'] is not None or wvi['ph_max'] is not None) else None
                if not ph_str:
                    ph_str = parse_ph_from_any_text(
                        wvi['notes_revisions'],
                        wvi['verification_procedures'],
                        wvi['physical_description'],
                        wvi['handling_instruction']
                    )
                color_str = wvi['color'] if (wvi['color'] and str(wvi['color']).strip() != 'None') else extract_color_from_text(wvi['physical_description'])
                import time
                conn.execute('''
                    INSERT OR REPLACE INTO profiles (
                        profile_number, generator, status, expiration_date, waste_description,
                        voc_percentage, ph_range, physical_appearance, flash_point, special_handling,
                        state_waste_code, federal_waste_code, dot_description, color, treatment_recipe,
                        lab_number, ldr_required, last_synced_mtime
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    clean_profile, wvi['generator_name'], 'ACTIVE', wvi['expiration_date'], wvi['waste_name'],
                    wvi['voc_ppm'], ph_str, wvi['physical_description'], wvi['flashpoint'], wvi['handling_instruction'],
                    wvi['state_waste_codes'], wvi['federal_waste_codes'], wvi['dot_description'], color_str, wvi['treatment_information'],
                    wvi['lab_num'], wvi['ldr'], time.time()
                ))
                conn.commit()
                return conn.execute('SELECT * FROM profiles WHERE TRIM(UPPER(profile_number)) = ?', (clean_profile,)).fetchone()
        except Exception as wvi_e:
            print(f"Error querying profile_wvi for {clean_profile}: {wvi_e}")
        return row if (row and row['status'] != 'NOT FOUND' and 'HISTORIC' not in str(row['generator'] or '').upper()) else None

def auto_sanitize_expired_profiles(conn=None):
    """
    Automated Priority Task: Enforces EXPIRED status on all database profiles whose expiration_date has passed.
    Overrides any legacy or Excel 'ACTIVE' statuses.
    """
    import pandas as pd
    from datetime import datetime
    
    close_at_end = False
    if conn is None:
        conn = get_db_connection()
        close_at_end = True
        
    try:
        conn.row_factory = sqlite3.Row
        today_date = datetime.now().date()
        rows = conn.execute("SELECT profile_number, status, expiration_date FROM profiles WHERE expiration_date IS NOT NULL AND expiration_date != ''").fetchall()
        
        expired_profiles = []
        for r in rows:
            p_num = r['profile_number']
            exp_str = str(r['expiration_date']).strip()
            curr_status = str(r['status']).strip().upper()
            try:
                dt = pd.to_datetime(exp_str, errors='coerce')
                if pd.notna(dt) and dt.date() < today_date:
                    if curr_status not in ['EXPIRED', 'EXPIRED (AUTO)']:
                        expired_profiles.append(p_num)
            except Exception:
                pass

        if expired_profiles:
            batch_size = 500
            for i in range(0, len(expired_profiles), batch_size):
                batch = expired_profiles[i:i+batch_size]
                placeholders = ','.join(['?'] * len(batch))
                conn.execute(f"UPDATE profiles SET status = 'EXPIRED' WHERE profile_number IN ({placeholders})", batch)
            conn.commit()
            print(f"Auto-sanitized {len(expired_profiles)} expired profiles in database.")
    except Exception as e:
        print(f"Error during auto_sanitize_expired_profiles: {e}")
    finally:
        if close_at_end:
            conn.close()