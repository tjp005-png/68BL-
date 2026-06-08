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

def _load_excel_dataframe(excel_path):
    global _excel_cache, _excel_cache_mtime
    import pandas as pd
    mtime = os.path.getmtime(excel_path)
    if _excel_cache is not None and _excel_cache_mtime == mtime:
        return _excel_cache
    
    df = pd.read_excel(excel_path, sheet_name='Data')
    
    if 'Profile #' in df.columns:
        df['Profile #'] = df['Profile #'].astype(str).str.strip().str.upper()
    else:
        for col in df.columns:
            if 'profile' in str(col).lower():
                df['Profile #'] = df[col].astype(str).str.strip().str.upper()
                break
                
    _excel_cache = df
    _excel_cache_mtime = mtime
    return df

def ensure_profile_exists(conn, profile_number):
    if not profile_number:
        return None
        
    clean_profile = str(profile_number).strip().upper()
    excel_path = MASTER_EXCEL_PATH
    
    excel_exists = os.path.exists(excel_path)
    excel_mtime = None
    if excel_exists:
        try:
            excel_mtime = os.path.getmtime(excel_path)
        except Exception:
            excel_exists = False

    # Check local SQLite DB first
    row = conn.execute('SELECT * FROM profiles WHERE TRIM(UPPER(profile_number)) = ?', (clean_profile,)).fetchone()
    
    if row:
        if not excel_exists or row['last_synced_mtime'] == excel_mtime:
            if row['status'] == 'NOT FOUND':
                return None
            return row

    if not excel_exists:
        return row if (row and row['status'] != 'NOT FOUND') else None

    # Load and search Excel
    try:
        df = _load_excel_dataframe(excel_path)
        
        if 'Profile #' not in df.columns:
            print("Warning: 'Profile #' column not found in Excel sheet 'Data'")
            return row if (row and row['status'] != 'NOT FOUND') else None
            
        matched = df[df['Profile #'] == clean_profile]
        
        if matched.empty:
            conn.execute('''
                INSERT OR REPLACE INTO profiles (profile_number, status, last_synced_mtime)
                VALUES (?, 'NOT FOUND', ?)
            ''', (clean_profile, excel_mtime))
            conn.commit()
            return None
            
        row_data = matched.iloc[0]
        
        generator = str(row_data.get('GENERATOR', '')).strip()
        status_val = str(row_data.get('STATUS', 'ACTIVE')).strip()
        if not status_val or status_val.lower() in ['nan', 'none']:
            status_val = 'ACTIVE'
            
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
        
        if row:
            conn.execute('''
                UPDATE profiles 
                SET generator = ?, status = ?, expiration_date = ?, 
                    waste_description = ?, voc_percentage = ?, win_code = ?,
                    last_synced_mtime = ?, epa_id = ?, lab_number = ?, haz = ?, rcra = ?, comments = ?
                WHERE TRIM(UPPER(profile_number)) = ?
            ''', (generator, status_val, exp_date, waste_name, voc_percentage, win_code, excel_mtime, epa_id, lab_number, haz, rcra, comments, clean_profile))
        else:
            conn.execute('''
                INSERT INTO profiles (
                    profile_number, generator, status, expiration_date, 
                    waste_description, voc_percentage, win_code, last_synced_mtime, epa_id,
                    lab_number, haz, rcra, comments
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (clean_profile, generator, status_val, exp_date, waste_name, voc_percentage, win_code, excel_mtime, epa_id, lab_number, haz, rcra, comments))
            
        conn.commit()
        
        return conn.execute('SELECT * FROM profiles WHERE TRIM(UPPER(profile_number)) = ?', (clean_profile,)).fetchone()

    except Exception as e:
        print(f"Error syncing profile {clean_profile} from master Excel file: {e}")
        return row if (row and row['status'] != 'NOT FOUND') else None