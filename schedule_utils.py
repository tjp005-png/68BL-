# schedule_utils.py
import re
import pandas as pd
from datetime import datetime

def calculate_las_tags(load):
    """
    Determines applicable LAS tag badges for a load:
    - 'LAS RECERT': Profile expiration date has passed or profile status is EXPIRED
    - 'LAS VOC': VOC value is TBD
    - 'LAS': Standard LAS trigger (inactive status, explicit LAS note/handling, CNIA requirement)
    """
    tags = []
    
    profile_num = str(load.get('profile_number', '')).strip().upper()
    # 1. IMMEDIATE EXEMPTION: BLCBPNONEB is NEVER LAS
    if profile_num == 'BLCBPNONEB':
        return tags

    win_code = str(load.get('routing_code', '')).strip().upper()
    notes = str(load.get('special_notes', '')).strip().upper()
    special_handling = str(load.get('special_handling', '')).strip().upper()
    prof_status = str(load.get('profile_status', '')).strip().upper()
    
    raw_exp = str(load.get('expiration_date', '')).strip().lower()
    clean_exp = re.sub(r'[^a-z0-9]', '', raw_exp)

    voc_val = str(load.get('voc_level', '')).strip().upper()
    p_voc = load.get('profile_voc_percentage')
    p_voc_str = str(p_voc).strip().upper() if p_voc is not None else ''

    # 1. LAS VOC Check: VOC value is TBD
    is_voc_tbd = (voc_val in ['TBD', '?']) or (p_voc_str in ['TBD', '?'])
    if is_voc_tbd:
        tags.append('LAS VOC')

    # 2. LAS RECERT Check: Expiration date has passed or profile status is EXPIRED
    is_expired = False
    if prof_status in ['EXPIRED', 'RECERTIFICATION', 'EXP']:
        is_expired = True
    elif any(char.isdigit() for char in raw_exp):
        try:
            exp_date = pd.to_datetime(raw_exp, errors='coerce')
            if pd.notna(exp_date) and exp_date.date() < datetime.now().date():
                is_expired = True
        except Exception:
            pass

    if is_expired:
        tags.append('LAS RECERT')

    # 3. Standard LAS Check (Inactive status or CNIA asbestos requirement)
    is_asbestos = ('CNIA' in win_code) or ('CNIA' in profile_num)
    is_asbestos_trigger = is_asbestos
    if is_asbestos_trigger and clean_exp in ['nodate', '', 'blank', 'none', 'nan', 'nat', 'null', 'na', 'tbd', '0', 'false']:
        is_asbestos_trigger = False

    has_las_handling = bool(re.search(r'\bLAS\b', special_handling))

    is_inactive_status = False
    if clean_exp in ['nodate', '', 'blank']:
        if not (prof_status.startswith('A') or 'HISTORICAL' in prof_status or prof_status == 'ACTIVE'):
            is_inactive_status = True
    elif prof_status in ['S', 'PENDING', 'NEEDS REVIEW', 'INACTIVE', 'NOT APPROVED']:
        is_inactive_status = True

    # If "LAS" is typed in notes, prevent the standard LAS tag from triggering
    las_in_notes = bool(re.search(r'\bLAS\b', notes))

    if (has_las_handling or is_asbestos_trigger or is_inactive_status) and not las_in_notes:
        if 'LAS RECERT' not in tags and 'LAS VOC' not in tags:
            tags.append('LAS')

    return tags

def calculate_las_status(load):
    """
    Determines if a load should be flagged as LAS based on WIN code, 
    expiration date, profile status, and specific notes.
    """
    tags = calculate_las_tags(load)
    return len(tags) > 0

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

def evaluate_untested_las(las_tags, p_info, wa_info, measured_voc=None):
    """
    Determines if a received load with LAS tags is missing testing/recertification updates.
    Returns True if load is untested/unrecertified, False otherwise.
    """
    if not las_tags:
        return False

    wa_status = str(wa_info.get('status') if isinstance(wa_info, dict) else (wa_info or '')).strip()

    # 1. If LAS RECERT is present (expiration date passed or EXPIRED status)
    if 'LAS RECERT' in las_tags:
        if wa_status in ['Approved', 'Recertified', 'Complete', 'Released']:
            return False
        return True

    # 2. If LAS VOC is present (VOC level is TBD or unknown)
    if 'LAS VOC' in las_tags:
        try:
            m_voc = float(measured_voc) if measured_voc is not None else None
        except (ValueError, TypeError):
            m_voc = None
        if m_voc is not None and m_voc > 0:
            return False
        if wa_status in ['Approved', 'Recertified', 'Complete', 'Released']:
            return False
        return True

    # 3. Standard LAS (Pending status, special handling, CNIA)
    if 'LAS' in las_tags:
        p_status = str(p_info.get('status') if isinstance(p_info, dict) else '').strip().upper()
        if p_status == 'ACTIVE' or wa_status in ['Approved', 'Recertified', 'Complete', 'Released', 'ACTIVE']:
            return False
        return True

    return False