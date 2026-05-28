# schedule_utils.py
import re
import pandas as pd
from datetime import datetime

def calculate_las_status(load):
    """
    Determines if a load should be flagged as LAS based on WIN code, 
    expiration date, profile status, and specific notes.
    """
    win_code = str(load.get('routing_code', '')).strip().upper()
    profile_num = str(load.get('profile_number', '')).strip().upper()
    notes = str(load.get('special_notes', '')).strip().upper()
    special_handling = str(load.get('special_handling', '')).strip().upper()
    prof_status = str(load.get('profile_status', '')).strip().upper()
    
    raw_exp = str(load.get('expiration_date', '')).strip().lower()
    clean_exp = re.sub(r'[^a-z0-9]', '', raw_exp)

    # 1. IMMEDIATE EXEMPTION: BLCBPNONEB is NEVER LAS
    if profile_num == 'BLCBPNONEB':
        return False

    # 2. IMMEDIATE TRIGGERS: Explicitly written LAS or Asbestos (CNIA)
    is_asbestos = ('CNIA' in win_code) or ('CNIA' in profile_num)
    
    # Exemption: CNIA profiles with NONE/No Date expiration dates do not trigger LAS
    is_asbestos_trigger = is_asbestos
    if is_asbestos_trigger and clean_exp in ['nodate', '', 'blank', 'none', 'nan', 'nat', 'null', 'na', 'tbd', '0', 'false']:
        is_asbestos_trigger = False

    if 'LAS' in special_handling or 'LAS' in notes or is_asbestos_trigger:
        return True # <--- CNIA now safely returns True and stops here!

    # 3. No Date Check
    if clean_exp in ['nodate', '', 'blank']:
        if prof_status.startswith('A'):
            return False  # Active and no date = Safe
        else:
            return True   # Inactive and no date = LAS

    # 4. Never Expires Check
    if clean_exp in ['none', 'nan', 'nat', 'null', 'na', 'tbd', '0', 'false']:
        return False

    # 5. Explicit Date Expiry Parsing
    if any(char.isdigit() for char in raw_exp):
        try:
            exp_date = pd.to_datetime(raw_exp, errors='coerce')
            if pd.notna(exp_date) and exp_date < datetime.now():
                return True # Expired = LAS
        except Exception:
            pass

    return False

def clean_display_notes(notes):
    """Strips out system tags (CERCLA, LAS) for a cleaner UI display."""
    if not notes: 
        return ""
    
    notes = str(notes)
    notes = re.sub(r'(?i)\bNOT CERCLA\b', '', notes)
    notes = re.sub(r'(?i)\bCERCLA\b', '', notes)
    notes = re.sub(r'(?i)\bLAS\b', '', notes)
    notes = re.sub(r'^[,\s]+|[,\s]+$', '', notes) 
    notes = re.sub(r',\s*,', ',', notes)
    
    return notes.strip()