# database.py
import sqlite3
import os
import re

_excel_cache = None
_excel_cache_mtime = None

from shared_state import DB_PATH, MASTER_EXCEL_PATH

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row 
    
    # Check if database is on a network drive to avoid WAL mode locks/hangs
    is_network = False
    if DB_PATH.startswith(r'\\'):
        is_network = True
    elif os.name == 'nt':
        try:
            import ctypes
            drive = os.path.splitdrive(os.path.abspath(DB_PATH))[0]
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

def enrich_profile_from_wvi(conn, clean_profile):
    """Enriches a profile in the 'profiles' table with WVI pH, color, and physical details if missing."""
    try:
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
    
    try:
        mtime = os.path.getmtime(excel_path)
    except Exception:
        if _excel_cache is not None:
            return _excel_cache
        raise
        
    if _excel_cache is not None and _excel_cache_mtime == mtime:
        return _excel_cache
    
    try:
        df = pd.read_excel(excel_path, engine='openpyxl')
        _excel_cache = df
        _excel_cache_mtime = mtime
        return df
    except PermissionError:
        temp_copy = excel_path + ".tmp"
        try:
            shutil.copy2(excel_path, temp_copy)
            df = pd.read_excel(temp_copy, engine='openpyxl')
            if os.path.exists(temp_copy):
                os.remove(temp_copy)
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
    excel_path = excel_path or MASTER_EXCEL_PATH
    
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
            if not pd.isna(val):
                try:
                    exp_date = pd.to_datetime(val, errors='coerce').strftime('%Y-%m-%d')
                    if not exp_date or pd.isna(exp_date):
                        exp_date = 'No Date'
                except:
                    exp_date = 'No Date'
                    
        waste_name = str(row_data.get('WASTE NAME', '')).strip()
        
        voc_percentage = None
        if 'VOC #' in df.columns:
            voc_str = str(row_data.get('VOC #', '')).strip()
            voc_match = re.search(r'(\d+\.?\d*)', voc_str)
            if voc_match:
                try:
                    voc_percentage = float(voc_match.group(1))
                except:
                    pass
                    
        win_code = str(row_data.get('WIN CODE', '')).strip()
        lab_number = str(row_data.get('LAB #', '')).strip()
        haz = str(row_data.get('HAZ', '')).strip()
        rcra = str(row_data.get('RCRA', '')).strip()
        comments = str(row_data.get('COMMENTS', '')).strip()
        
        epa_id = ''
        if 'EPA ID' in df.columns:
            epa_id = str(row_data.get('EPA ID', '')).strip()
        elif 'EPA_ID' in df.columns:
            epa_id = str(row_data.get('EPA_ID', '')).strip()
            
        c_upper = str(comments or '').upper()
        container_type = 'Containerized' if ('SIP' in c_upper or 'TREA' in c_upper) else 'Bulk Solid'

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
        return row if (row and row['status'] != 'NOT FOUND') else None