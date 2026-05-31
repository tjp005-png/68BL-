# populate_dev_data.py
import sqlite3
from datetime import date, datetime

print("🧹 Wiping operational logs...")
conn = sqlite3.connect('database.db')
cursor = conn.cursor()

# 1. Wipe operational tables
tables = ['truck_logs', 'daily_schedule', 'drum_inventory', 'drum_lab_queue', 'compliance_tracker', 'audit_log']
for table in tables:
    cursor.execute(f"DELETE FROM {table}")
    try:
        cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}'")
    except sqlite3.OperationalError:
        pass

print("📦 Populating master profiles...")
# 2. Insert/replace master profiles representing different rules (CCS, Expired LAS, Normal, etc.)
profiles = [
    ('CH-12345', 'Clean Harbors', 'General hazardous waste', 'CBP', 0.05, 'Keep cool', '6.0-9.0', 'Solid', '>200F', '2028-12-31', 'ACTIVE'),
    ('P-LAS-EXP', 'Schenectady Gen', 'Expired solvent mixture', 'CNO', 12.5, 'Flame/corrosion suit required', '4.0-10.0', 'Solid', '>100F', '2020-01-01', 'INACTIVE'),
    ('P-LAS-NO-DATE', 'Albany Corp', 'Unspecified chemical sludge', 'CBPS', 0.0, 'Ventilation required', '7.0-12.0', 'Solid', '', 'nodate', 'INACTIVE'),
    ('P-CCS-LIQ', 'Boston Labs', 'Organic liquid wash', 'CCS', 450.0, 'Level C PPE', '2.0-12.5', 'Liquid', '140F', '2029-06-30', 'ACTIVE'),
    ('P-CCS-SOL', 'Denver MFG', 'Contaminated filter cakes', 'CCS', 10.0, 'Dust mask', '6.0-8.5', 'Solid', '', '2029-06-30', 'ACTIVE'),
    ('P-NORMAL', 'Seattle Paint', 'Seattle Paint latex', 'CBP', 0.0, 'Wash skin after contact', '6.5-8.5', 'Solid', '', '2030-12-31', 'ACTIVE'),
    ('CUTEST', 'me', 'soil', 'CBP', 0.0, '', '4-10', 'Liquid', '', '', 'ACTIVE'),
    ('TEST', 'test', 'test', 'CBP', 0.0, '', '4.0-10.0', 'Solid', '>140', '', 'ACTIVE'),
    ('CHTEST', 'clh', 'soil', 'cpb', 0.0, '', '7.1-12.4', 'Solid', '', '', 'ACTIVE')
]

cursor.executemany('''
    REPLACE INTO profiles (profile_number, generator, waste_description, win_code, voc_percentage, special_handling, ph_range, physical_appearance, flash_point, expiration_date, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
''', profiles)

print("📅 Populating daily schedule for today...")
# 3. Daily schedule for today
today_str = date.today().isoformat()
schedules = [
    (today_str, '08:00', '10:00', 'P-LAS-EXP', 2, 'Schenectady Gen', 'CNO'),
    (today_str, '09:00', '11:00', 'P-LAS-NO-DATE', 1, 'Albany Corp', 'CBPS'),
    (today_str, '10:00', '12:00', 'P-CCS-LIQ', 3, 'Boston Labs', 'CCS'),
    (today_str, '11:00', '13:00', 'P-CCS-SOL', 1, 'Denver MFG', 'CCS'),
    (today_str, '13:00', '15:00', 'P-NORMAL', 5, 'Seattle Paint', 'CBP'),
    (today_str, '14:00', '16:00', 'CH-12345', 2, 'Clean Harbors', 'CBP')
]

cursor.executemany('''
    INSERT INTO daily_schedule (schedule_date, start_time, end_time, profile_number, load_count, generator, routing_code)
    VALUES (?, ?, ?, ?, ?, ?, ?)
''', schedules)

print("🚚 Populating truck check-in logs...")
# 4. Populate truck logs
# test_status can be 'WEIGHED IN' or 'LAB COMPLETED'
truck_logs = [
    # 1. P-LAS-EXP: 1st load (LAS, weighed in) -> Pending Waste Acceptance release
    ('TRK-LAS-1', 'P-LAS-EXP', 'M-LAS-101', 'L-01', 52000.0, 'LAS', 'WEIGHED IN', today_str, '13:10'),
    # 2. P-LAS-EXP: 2nd load (Fingerprint, lab completed but waiting for LAS release) -> Should show WAITING FOR LAS
    ('TRK-LAS-2', 'P-LAS-EXP', 'M-LAS-102', 'L-02', 51000.0, 'FINGERPRINT', 'LAB COMPLETED', today_str, '13:20'),
    # 3. P-LAS-NO-DATE: 1st load (LAS, released/completed) -> Shows RELEASED
    ('TRK-LAS-3', 'P-LAS-NO-DATE', 'M-LAS-201', 'L-03', 48000.0, 'LAS', 'LAB COMPLETED', today_str, '13:30'),
    # 4. P-LAS-NO-DATE: 2nd load (Fingerprint, lab completed, first load released) -> Shows OK TO RELEASE
    ('TRK-LAS-4', 'P-LAS-NO-DATE', 'M-LAS-202', 'L-04', 50000.0, 'FINGERPRINT', 'LAB COMPLETED', today_str, '13:35'),
    # 5. P-CCS-LIQ: 1st load (LAS, weighed in, Liquid) -> Shows LAS (requires Measured Flash Point in modal)
    ('TRK-CCS-1', 'P-CCS-LIQ', 'M-CCS-301', 'L-05', 53000.0, 'LAS', 'WEIGHED IN', today_str, '13:40'),
    # 6. P-NORMAL: standard fingerprint (completed) -> Shows LAB COMPLETED
    ('TRK-NORM-1', 'P-NORMAL', 'M-NORM-401', 'L-06', 47000.0, 'FINGERPRINT', 'LAB COMPLETED', today_str, '13:50'),
    # 7. P-NORMAL: standard fingerprint (weighed in) -> Shows FINGERPRINT
    ('TRK-NORM-2', 'P-NORMAL', 'M-NORM-402', 'L-07', 49000.0, 'FINGERPRINT', 'WEIGHED IN', today_str, '14:00')
]

for log in truck_logs:
    truck_id, profile_number, manifest_number, load_number, gross_weight, test_assigned, test_status, date_received, time_in = log
    
    measured_ph = None
    measured_voc = None
    voc_pass_fail = 'N/A'
    measured_sulfides = None
    measured_cyanide = None
    measured_free_liquids = None
    measured_flashpoint = None
    lab_results = None
    
    if test_status == 'LAB COMPLETED':
        measured_ph = 7.4
        measured_voc = 12.0
        measured_sulfides = 'Negative'
        measured_cyanide = 'Negative'
        measured_free_liquids = 'No'
        lab_results = 'Released.'
        
        if 'CCS' in profile_number or 'CCS' in test_assigned:
            voc_pass_fail = 'PASS'
            
        if profile_number == 'P-LAS-NO-DATE':
            if test_assigned == 'LAS':
                measured_ph = 8.1
                measured_voc = 0.0
                lab_results = 'Released by Waste Acceptance. Expired profile specs verified.'
            else:
                measured_ph = 7.9
                measured_voc = 15.0
                lab_results = 'Fingerprint test completed.'

    cursor.execute('''
        INSERT INTO truck_logs (
            truck_id, profile_number, manifest_number, load_number, gross_weight, 
            test_assigned, test_status, date_received, time_in,
            measured_ph, measured_voc, voc_pass_fail, measured_sulfides, measured_cyanide, measured_free_liquids, measured_flashpoint, lab_results
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (truck_id, profile_number, manifest_number, load_number, gross_weight, 
          test_assigned, test_status, date_received, time_in,
          measured_ph, measured_voc, voc_pass_fail, measured_sulfides, measured_cyanide, measured_free_liquids, measured_flashpoint, lab_results))

conn.commit()
conn.close()
print("✅ SUCCESS: Dev environment has been fully reset and populated with diverse test trucks!")
