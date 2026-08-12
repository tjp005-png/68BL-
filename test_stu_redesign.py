# test_stu_redesign.py
import os
import unittest
import sqlite3
import json

# Setup mock database path
TEST_DB_PATH = os.path.abspath('test_database.db')
os.environ['DB_PATH'] = TEST_DB_PATH

import shared_state
shared_state.DB_PATH = TEST_DB_PATH

# Patch connect globally so the Flask app uses our test database
original_connect = sqlite3.connect
def mock_connect(database, *args, **kwargs):
    return original_connect(TEST_DB_PATH, *args, **kwargs)
sqlite3.connect = mock_connect

# Now import our app and route blueprints
from app import app, upgrade_db

class TestSTURedesignFlow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        cls.client = app.test_client()

    def setUp(self):
        # Clear tables instead of deleting the DB file to avoid Windows lock issues
        conn = original_connect(TEST_DB_PATH)
        try:
            conn.execute("DELETE FROM profiles")
            conn.execute("DELETE FROM drum_inventory")
            conn.execute("DELETE FROM drum_lab_queue")
            conn.execute("DELETE FROM daily_schedule")
            conn.execute("DELETE FROM waste_acceptance_log")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

        upgrade_db()
        self.populate_base_data()

    def tearDown(self):
        # Connection close is handled, we don't force delete files on teardown
        pass

    def populate_base_data(self):
        conn = original_connect(TEST_DB_PATH)
        import os
        from shared_state import MASTER_EXCEL_PATH
        excel_mtime = None
        if os.path.exists(MASTER_EXCEL_PATH):
            try:
                excel_mtime = os.path.getmtime(MASTER_EXCEL_PATH)
            except:
                pass
        try:
            conn.execute('''
                INSERT OR REPLACE INTO profiles (profile_number, generator, status, waste_description, win_code, voc_percentage, special_handling, last_synced_mtime)
                VALUES ('P-STU-TEST', 'Test Generator', 'ACTIVE', 'STU Drum Waste Description', 'BL', 10.0, 'None', ?)
            ''', (excel_mtime,))
            conn.commit()
        finally:
            conn.close()

    def test_stu_hub_pipeline(self):
        """Test STU hub pipeline view"""
        response = self.client.get('/stu/hub?view=pipeline')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Active Sampling Pipeline', response.data)

    def test_end_to_end_stu_workflow(self):
        """Test complete flow: PDF sampling setup -> Chemist bulk -> WA Checklist -> Finalize"""
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        
        # 1. Simulating generating sampling packet: create drum inventory and lab queue records
        job_id = "LOAD-999"
        
        # Insert 3 drums in inventory
        conn.execute('''
            INSERT INTO drum_inventory (track_no, inb_prof, manifest, process_type, weight, job_id, import_date)
            VALUES 
            ('DRUM-001', 'P-STU-TEST', 'MAN-999', 'PENDING SAMPLING', 400.0, ?, '2026-05-27'),
            ('DRUM-002', 'P-STU-TEST', 'MAN-999', 'PENDING SAMPLING', 410.0, ?, '2026-05-27'),
            ('DRUM-003', 'P-STU-TEST', 'MAN-999', 'PENDING SAMPLING', 390.0, ?, '2026-05-27')
        ''', (job_id, job_id, job_id))
        
        # Insert 1 lab record for DRUM-001
        conn.execute('''
            INSERT INTO drum_lab_queue (job_id, drum_id, profile, manifest, tests_required, status)
            VALUES (?, 'DRUM-001', 'P-STU-TEST', 'MAN-999', 'FingerPrint', 'PENDING')
        ''', (job_id,))
        conn.commit()

        # Let's verify they exist
        inv_count = conn.execute("SELECT COUNT(*) FROM drum_inventory WHERE job_id = ?", (job_id,)).fetchone()[0]
        self.assertEqual(inv_count, 3)
        lab_count = conn.execute("SELECT COUNT(*) FROM drum_lab_queue WHERE job_id = ?", (job_id,)).fetchone()[0]
        self.assertEqual(lab_count, 1)
        conn.close()

        # 2. Chemist bulk grid GET
        res_grid = self.client.get(f'/chemist/drums/bulk/{job_id}')
        self.assertEqual(res_grid.status_code, 200)
        self.assertIn(b'CHEMIST DRUM ENTRY GRID', res_grid.data)

        # 3. Chemist bulk submit POST
        # Fetch the lab record id from DB
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        lab_row = conn.execute("SELECT id FROM drum_lab_queue WHERE job_id = ?", (job_id,)).fetchone()
        lab_db_id = lab_row['id']
        conn.close()

        # Submit data
        res_submit = self.client.post('/chemist/drums/bulk/submit', data={
            'job_id': job_id,
            'drum_db_id': [str(lab_db_id)],
            f'status_{lab_db_id}': 'COMPLETED',
            f'notes_{lab_db_id}': 'Looks clean',
            f'flashpoint_{lab_db_id}': '>200',
            f'cyanide_{lab_db_id}': 'No',
            f'sulfide_{lab_db_id}': 'Negative',
            f'oxidation_{lab_db_id}': 'No',
            f'ph_{lab_db_id}': '7.2',
            f'voc_{lab_db_id}': '4.5'
        })
        self.assertEqual(res_submit.status_code, 302) # Redirect to STU Hub

        # Verify database updated
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        updated_lab = conn.execute("SELECT * FROM drum_lab_queue WHERE id = ?", (lab_db_id,)).fetchone()
        self.assertEqual(updated_lab['status'], 'COMPLETED')
        self.assertEqual(updated_lab['ph_result'], 7.2)
        self.assertEqual(updated_lab['voc_result'], 4.5)
        self.assertEqual(updated_lab['notes'], 'Looks clean')
        conn.close()

        # 4. Waste Acceptance Checklist GET
        res_checklist = self.client.get(f'/waste_acceptance/checklist/{job_id}')
        self.assertEqual(res_checklist.status_code, 200)
        self.assertIn(b'WIN ENTRY CHECKLIST', res_checklist.data)
        self.assertIn(b'DRUM-001', res_checklist.data)

        # 5. Waste Acceptance check off /mark_coded AJAX POST
        res_mark = self.client.post('/waste_acceptance/mark_coded', 
                                    data=json.dumps({'lab_id': lab_db_id, 'coded': 1}),
                                    content_type='application/json')
        self.assertEqual(res_mark.status_code, 200)
        mark_data = json.loads(res_mark.data.decode('utf-8'))
        self.assertTrue(mark_data['success'])

        # Verify DB coded state
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        coded_lab = conn.execute("SELECT coded_in_win FROM drum_lab_queue WHERE id = ?", (lab_db_id,)).fetchone()
        self.assertEqual(coded_lab['coded_in_win'], 1)
        conn.close()

        # 6. Waste Acceptance Finalize Load POST
        res_finalize = self.client.post('/waste_acceptance/finalize_load', data={'job_id': job_id})
        self.assertEqual(res_finalize.status_code, 302) # Redirect to pipeline

        # Verify entire load is pushed to Active STU Inventory (process_type='TESTED')
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        drums = conn.execute("SELECT * FROM drum_inventory WHERE job_id = ?", (job_id,)).fetchall()
        for d in drums:
            self.assertEqual(d['process_type'], 'TESTED')
            # Sampled drum should have result transferred
            if d['track_no'] == 'DRUM-001':
                self.assertEqual(d['ph'], 7.2)
                self.assertEqual(d['voc_ppm'], 4.5)
            else:
                # Non-sampled drums shouldn't have sample results, or at least they are now TESTED
                pass
                
        # Verify lab queue is FINAL CODED
        lab_row = conn.execute("SELECT status FROM drum_lab_queue WHERE job_id = ?", (job_id,)).fetchone()
        self.assertEqual(lab_row['status'], 'FINAL CODED')
        conn.close()

    def test_chemist_drums_queue_and_scan(self):
        """Test STU drum queue list view and barcode scanner check-in API"""
        job_id = "LOAD-777"
        conn = original_connect(TEST_DB_PATH)
        conn.execute('''
            INSERT INTO drum_lab_queue (job_id, drum_id, profile, manifest, tests_required, status)
            VALUES (?, 'DRUM-SCAN', 'P-STU-TEST', 'MAN-777', 'FingerPrint', 'PENDING')
        ''', (job_id,))
        conn.commit()
        conn.close()

        # Check queue view
        res = self.client.get('/chemist/drums')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'LOAD-777', res.data)

        # Check-in scan API POST
        res_scan = self.client.post('/api/chemist/check_in_drum', 
                                    data=json.dumps({'drum_id': 'DRUM-SCAN'}),
                                    content_type='application/json')
        self.assertEqual(res_scan.status_code, 200)
        data = json.loads(res_scan.data.decode('utf-8'))
        self.assertTrue(data['success'])
        self.assertEqual(data['job_id'], job_id)

        # Verify DB state
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        drum = conn.execute("SELECT status FROM drum_lab_queue WHERE drum_id = 'DRUM-SCAN'").fetchone()
        self.assertEqual(drum['status'], 'RECEIVED')
        conn.close()

    def test_permitted_codes(self):
        """Test that D80L, LLF, and CBPR are part of the permitted STU WIN codes"""
        from stu_services import PERMITTED_CODES as pc_services
        from app import PERMITTED_CODES as pc_app
        self.assertIn('D80L', pc_services)
        self.assertIn('D80L', pc_app)
        self.assertIn('LLF', pc_services)
        self.assertIn('LLF', pc_app)
        self.assertIn('CBPR', pc_services)
        self.assertIn('CBPR', pc_app)

    def test_add_schedule_dates(self):
        """Test that scheduling a profile via /add_schedule correctly parses dates and inserts them"""
        res = self.client.post('/add_schedule', data={
            'selected_dates': '2026-05-30, 2026-05-31',
            'profile_number': 'P-STU-TEST',
            'load_count': '2',
            'generator': 'Test Generator',
            'waste_type': 'WASTE PICKUP',
            'sales_order': 'SO-1234',
            'routing_code': 'BL',
            'scheduler_initials': 'TS',
            'special_notes': 'Testing schedule addition',
            'voc_level': '10'
        })
        self.assertEqual(res.status_code, 302) # Redirects to schedule portal

        # Verify DB entries
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM daily_schedule WHERE profile_number = 'P-STU-TEST' ORDER BY schedule_date ASC").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['schedule_date'], '2026-05-30')
        self.assertEqual(rows[1]['schedule_date'], '2026-05-31')
        self.assertEqual(rows[0]['sales_order'], 'SO-1234')
        self.assertEqual(rows[0]['generator'].upper(), 'TEST GENERATOR')
        self.assertIsNotNone(rows[0]['series_id'])
        self.assertEqual(rows[0]['series_id'], rows[1]['series_id'])
        conn.close()

    def test_container_ba_parsing(self):
        """Test regex parsing of container quantity/size with 'BA' and '5 BA'"""
        import re
        regex = r'\b(?:(\d{1,4}\s?(?:DM|DF|DP|CF|TP|GAL|G|TT|FBIN|BIN|BA))|((?:PAL|BAG|BA|CTN|BOX|CY|YARD|YD|FBIN|BIN)))\b'
        
        # Test digit-preceded container size
        match1 = re.search(regex, "5 BA", re.IGNORECASE)
        self.assertIsNotNone(match1)
        size1 = (match1.group(1) if match1.group(1) else match1.group(2)).upper()
        self.assertEqual(size1, "5 BA")

        # Test standalone container size
        match2 = re.search(regex, "BA", re.IGNORECASE)
        self.assertIsNotNone(match2)
        size2 = (match2.group(1) if match2.group(1) else match2.group(2)).upper()
        self.assertEqual(size2, "BA")

        # Test another digit-preceded size
        match3 = re.search(regex, "12 DM", re.IGNORECASE)
        self.assertIsNotNone(match3)
        size3 = (match3.group(1) if match3.group(1) else match3.group(2)).upper()
        self.assertEqual(size3, "12 DM")

    def test_upload_vpi(self):
        """Test uploading a VPI CSV file and verifying database ingestion and pending preservation"""
        import io
        from datetime import date, timedelta
        
        today = date.today()
        scan_date_4_days_ago = (today - timedelta(days=4)).strftime('%m/%d/%Y')
        
        # 1. Insert a pending sampling drum received today to verify it gets preserved during upload
        conn = original_connect(TEST_DB_PATH)
        conn.execute('''
            INSERT INTO drum_inventory (track_no, inb_prof, manifest, process_type, weight, ph, age, voc_ppm, voc_weight, import_date, job_id, status)
            VALUES ('DRUM-PENDING', 'P-STU-TEST', 'MAN-111', 'PENDING SAMPLING', 100.0, 7.0, 10.0, 10.0, 1000.0, ?, 'JOB-111', 'PLANT RECEIVED')
        ''', (today.isoformat(),))
        # Insert a rejected drum that WILL be in the VPI file (case-insensitive test)
        conn.execute('''
            INSERT INTO drum_inventory (track_no, inb_prof, manifest, process_type, weight, ph, age, voc_ppm, voc_weight, import_date, job_id, status, reject_notes, outgoing_manifest)
            VALUES ('drum-rejected-in-vpi', 'P-STU-TEST', 'MAN-111', 'direct land haz', 300.0, 7.0, 5.0, 0.0, 0.0, '2026-06-01', 'JOB-111', 'REJECTED', 'Failed pH test', 'OUT-123')
        ''')
        # Insert an old rejected drum that WILL NOT be in the VPI file (older than 3 days, no active job -> cleaned out)
        conn.execute('''
            INSERT INTO drum_inventory (track_no, inb_prof, manifest, process_type, weight, ph, age, voc_ppm, voc_weight, import_date, job_id, status, reject_notes, outgoing_manifest)
            VALUES ('DRUM-REJECTED-MISSING', 'P-STU-TEST', 'MAN-111', 'direct land haz', 320.0, 7.0, 5.0, 0.0, 0.0, '2026-06-01', NULL, 'REJECTED', 'Leaking container', 'OUT-456')
        ''')
        # Insert an old PLANT RECEIVED drum that WILL NOT be in the VPI file (should be deleted)
        conn.execute('''
            INSERT INTO drum_inventory (track_no, inb_prof, manifest, process_type, weight, ph, age, voc_ppm, voc_weight, import_date, job_id, status)
            VALUES ('DRUM-PLANT-MISSING', 'P-STU-TEST', 'MAN-111', 'direct land haz', 100.0, 7.0, 10.0, 10.0, 1000.0, '2026-06-01', NULL, 'PLANT RECEIVED')
        ''')
        # Insert a PLANT RECEIVED drum that WILL be in the VPI file (should change to FINAL CODED)
        conn.execute('''
            INSERT INTO drum_inventory (track_no, inb_prof, manifest, process_type, weight, ph, age, voc_ppm, voc_weight, import_date, job_id, status)
            VALUES ('DRUM-PLANT-FOUND', 'P-STU-TEST', 'MAN-111', 'direct land haz', 100.0, 7.0, 10.0, 10.0, 1000.0, '2026-06-01', 'JOB-111', 'PLANT RECEIVED')
        ''')
        conn.commit()
        conn.close()

        csv_data = (
            f"Track No,Process Type,Weight,pH,Inb Prof,Age,Type,Area,Last Scan Date\n"
            f"DRUM-NEW,direct land haz,450.0,6.5,P-STU-TEST,5.0,DM,Area-51,{scan_date_4_days_ago}\n"
            f"DRUM-PENDING-OVERWRITE,pending sampling,200.0,8.0,P-STU-TEST,2.0,DM,Area-52,{scan_date_4_days_ago}\n"
            f"DRUM-HEAVY-BULK,direct land nh,42000.0,7.0,P-STU-TEST,5.0,Roll-Off,Area-53,{scan_date_4_days_ago}\n"
            f"DRUM-HEAVY-PUT,put pile,35000.0,7.0,P-STU-TEST,5.0,Roll-Off,Cell 34 Open,{scan_date_4_days_ago}\n"
            f"DRUM-REJECTED-IN-VPI,direct land haz,300.0,7.0,P-STU-TEST,5.0,DM,Area-51,{scan_date_4_days_ago}\n"
            f"DRUM-PLANT-FOUND,direct land haz,100.0,7.0,P-STU-TEST,5.0,DM,Area-51,{scan_date_4_days_ago}\n"
        )
        
        data = {
            'vpi_file': (io.BytesIO(csv_data.encode('utf-8')), 'test_vpi.csv')
        }
        
        response = self.client.post('/upload_vpi', data=data, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 302)

        # Verify DB contents
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        
        # The new drum DRUM-NEW should be in inventory with status 'FINAL CODED'
        row_new = conn.execute("SELECT * FROM drum_inventory WHERE track_no = 'DRUM-NEW'").fetchone()
        self.assertIsNotNone(row_new)
        self.assertEqual(row_new['process_type'], 'direct land haz')
        self.assertEqual(row_new['location'], 'Area-51')
        self.assertEqual(row_new['status'], 'FINAL CODED')
        self.assertEqual(row_new['last_scan_date'], scan_date_4_days_ago)

        # The pending sampling drum DRUM-PENDING (which was not in CSV, but received recently) should be preserved
        row_pending = conn.execute("SELECT * FROM drum_inventory WHERE track_no = 'DRUM-PENDING'").fetchone()
        self.assertIsNotNone(row_pending)
        self.assertEqual(row_pending['process_type'], 'PENDING SAMPLING')
        self.assertEqual(row_pending['status'], 'PLANT RECEIVED')

        # Verify that DRUM-HEAVY-BULK was excluded (since weight > 5000 and not put pile)
        row_heavy_bulk = conn.execute("SELECT * FROM drum_inventory WHERE track_no = 'DRUM-HEAVY-BULK'").fetchone()
        self.assertIsNone(row_heavy_bulk)

        # Verify that DRUM-HEAVY-PUT was imported (since it is a put pile)
        row_heavy_put = conn.execute("SELECT * FROM drum_inventory WHERE track_no = 'DRUM-HEAVY-PUT'").fetchone()
        self.assertIsNotNone(row_heavy_put)
        self.assertEqual(row_heavy_put['process_type'], 'put pile')

        # Verify that drum-rejected-in-vpi preserved its REJECTED status and notes
        row_rejected_in = conn.execute("SELECT * FROM drum_inventory WHERE TRIM(UPPER(track_no)) = 'DRUM-REJECTED-IN-VPI'").fetchone()
        self.assertIsNotNone(row_rejected_in)
        self.assertEqual(row_rejected_in['status'], 'REJECTED')
        self.assertEqual(row_rejected_in['reject_notes'], 'Failed pH test')
        self.assertEqual(row_rejected_in['outgoing_manifest'], 'OUT-123')

        # Verify that DRUM-REJECTED-MISSING (omitted from CSV, old date, no active job) was cleaned out / deleted
        row_rejected_missing = conn.execute("SELECT * FROM drum_inventory WHERE TRIM(UPPER(track_no)) = 'DRUM-REJECTED-MISSING'").fetchone()
        self.assertIsNone(row_rejected_missing)

        # Verify that DRUM-PLANT-MISSING was deleted
        row_plant_missing = conn.execute("SELECT * FROM drum_inventory WHERE track_no = 'DRUM-PLANT-MISSING'").fetchone()
        self.assertIsNone(row_plant_missing)

        # Verify that DRUM-PLANT-FOUND was changed to FINAL CODED
        row_plant_found = conn.execute("SELECT * FROM drum_inventory WHERE track_no = 'DRUM-PLANT-FOUND'").fetchone()
        self.assertIsNotNone(row_plant_found)
        self.assertEqual(row_plant_found['status'], 'FINAL CODED')

        conn.close()
        
        # Now verify the STU hub returns the scan date calculations
        hub_res = self.client.get('/stu/hub?view=inventory')
        self.assertEqual(hub_res.status_code, 200)
        self.assertIn(b'4 days', hub_res.data)
        self.assertIn(b'Last Scanned', hub_res.data)

    def test_manual_status_update(self):
        """Test manually updating drum status using the UPDATE_STATUS action (both Form POST and AJAX)"""
        conn = original_connect(TEST_DB_PATH)
        # Insert a drum
        conn.execute('''
            INSERT INTO drum_inventory (track_no, inb_prof, process_type, status, reject_notes, outgoing_manifest)
            VALUES ('DRUM-UPDATE-TEST', 'P-STU-TEST', 'direct land haz', 'FINAL CODED', 'Some old notes', 'OUT-OLD')
        ''')
        conn.commit()
        
        drum_row = conn.execute("SELECT id FROM drum_inventory WHERE track_no = 'DRUM-UPDATE-TEST'").fetchone()
        drum_id = drum_row[0]
        conn.close()
        
        # 1. Update status to MISSING via redirect POST
        response = self.client.post('/stu/drum_action', data={
            'drum_id': str(drum_id),
            'action': 'UPDATE_STATUS',
            'status': 'MISSING'
        })
        self.assertEqual(response.status_code, 302)
        
        # Verify status is updated and reject_notes/manifest are cleared
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM drum_inventory WHERE id = ?", (drum_id,)).fetchone()
        self.assertEqual(row['status'], 'MISSING')
        self.assertIsNone(row['reject_notes'])
        self.assertIsNone(row['outgoing_manifest'])
        
        # 2. Update status to REJECTED via redirect POST
        response = self.client.post('/stu/drum_action', data={
            'drum_id': str(drum_id),
            'action': 'UPDATE_STATUS',
            'status': 'REJECTED',
            'reject_notes': 'Damaged seal',
            'outgoing_manifest': 'OUT-NEW'
        })
        self.assertEqual(response.status_code, 302)
        
        row = conn.execute("SELECT * FROM drum_inventory WHERE id = ?", (drum_id,)).fetchone()
        self.assertEqual(row['status'], 'REJECTED')
        self.assertEqual(row['reject_notes'], 'Damaged seal')
        self.assertEqual(row['outgoing_manifest'], 'OUT-NEW')
        conn.close()

        # 3. Update status to RESAMPLE via AJAX/XMLHttpRequest
        res = self.client.post('/stu/drum_action', data={
            'drum_id': drum_id,
            'action': 'UPDATE_STATUS',
            'status': 'RESAMPLE'
        }, headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data.decode('utf-8'))
        self.assertTrue(data['status'] == 'success')

        # Verify DB updated and reject notes cleared
        conn = original_connect(TEST_DB_PATH)
        row = conn.execute("SELECT status, reject_notes, outgoing_manifest FROM drum_inventory WHERE id = ?", (drum_id,)).fetchone()
        self.assertEqual(row[0], 'RESAMPLE')
        self.assertIsNone(row[1])
        conn.close()

        # 4. Update status to REJECTED via AJAX/XMLHttpRequest
        res_reject = self.client.post('/stu/drum_action', data={
            'drum_id': drum_id,
            'action': 'UPDATE_STATUS',
            'status': 'REJECTED',
            'reject_notes': 'Drum is leaking',
            'outgoing_manifest': 'OUT-MAN-999'
        }, headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(res_reject.status_code, 200)
        data_rej = json.loads(res_reject.data.decode('utf-8'))
        self.assertTrue(data_rej['status'] == 'success')

        # Verify DB rejected fields
        conn = original_connect(TEST_DB_PATH)
        row_rej = conn.execute("SELECT status, reject_notes, outgoing_manifest FROM drum_inventory WHERE id = ?", (drum_id,)).fetchone()
        self.assertEqual(row_rej[0], 'REJECTED')
        self.assertEqual(row_rej[1], 'Drum is leaking')
        self.assertEqual(row_rej[2], 'OUT-MAN-999')
        conn.close()

        # 5. Update status to MISSING via AJAX/XMLHttpRequest
        res_missing = self.client.post('/stu/drum_action', data={
            'drum_id': drum_id,
            'action': 'UPDATE_STATUS',
            'status': 'MISSING'
        }, headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(res_missing.status_code, 200)

        # Verify status is MISSING
        conn = original_connect(TEST_DB_PATH)
        row_missing = conn.execute("SELECT status FROM drum_inventory WHERE id = ?", (drum_id,)).fetchone()
        self.assertEqual(row_missing[0], 'MISSING')
        conn.close()

        # 6. Verify that upload_vpi preserves a MISSING drum even if it's NOT in the upload CSV
        csv_data = (
            "Track No,Process Type,Weight,pH,Inb Prof,Age,Type,Area,Last Scan Date\n"
            "DRUM-NEW,direct land haz,450.0,6.5,P-STU-TEST,5.0,DM,Area-51,06/15/2026\n"
        )
        import io
        data_upload = {
            'vpi_file': (io.BytesIO(csv_data.encode('utf-8')), 'test_vpi.csv')
        }
        res_upload = self.client.post('/upload_vpi', data=data_upload, content_type='multipart/form-data')
        self.assertEqual(res_upload.status_code, 302)

        # The MISSING drum should still be in the database
        conn = original_connect(TEST_DB_PATH)
        row_preserved = conn.execute("SELECT status FROM drum_inventory WHERE track_no = 'DRUM-UPDATE-TEST'").fetchone()
        self.assertIsNotNone(row_preserved)
        self.assertEqual(row_preserved[0], 'MISSING')
        conn.close()

    def test_waste_acceptance_log(self):
        """Test the waste acceptance active reviews log CRUD endpoints"""
        # Ensure profile exists for join testing
        conn = original_connect(TEST_DB_PATH)
        conn.execute("INSERT OR REPLACE INTO profiles (profile_number, generator) VALUES ('P-TEST-LOG', 'Log Test Gen')")
        conn.commit()
        conn.close()

        # Add to log
        res_add = self.client.post('/api/waste_acceptance/log/add', 
                                   data=json.dumps({'profile_number': 'P-TEST-LOG'}),
                                   content_type='application/json')
        self.assertEqual(res_add.status_code, 200)
        self.assertTrue(json.loads(res_add.data)['success'])

        # Get log
        res_get = self.client.get('/api/waste_acceptance/log')
        self.assertEqual(res_get.status_code, 200)
        logs = json.loads(res_get.data)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]['profile_number'], 'P-TEST-LOG')
        self.assertEqual(logs[0]['generator'], 'Log Test Gen')
        self.assertEqual(logs[0]['status'], 'Needs Review')
        log_id = logs[0]['id']

        # Update log
        res_upd = self.client.post('/api/waste_acceptance/log/update', 
                                   data=json.dumps({'id': log_id, 'status': 'Pending Lab', 'assigned_to': 'TestUser', 'notes': 'Some notes'}),
                                   content_type='application/json')
        self.assertEqual(res_upd.status_code, 200)

        # Get log again to verify update
        res_get_upd = self.client.get('/api/waste_acceptance/log')
        logs_upd = json.loads(res_get_upd.data)
        self.assertEqual(logs_upd[0]['status'], 'Pending Lab')
        self.assertEqual(logs_upd[0]['assigned_to'], 'TestUser')
        self.assertEqual(logs_upd[0]['notes'], 'Some notes')

        # Archive log testing
        res_arch = self.client.post('/api/waste_acceptance/log/archive',
                                    data=json.dumps({'id': log_id, 'is_archived': 1}),
                                    content_type='application/json')
        self.assertEqual(res_arch.status_code, 200)
        self.assertTrue(json.loads(res_arch.data)['success'])

        # Verify it shows up in archived GET
        res_get_arch = self.client.get('/api/waste_acceptance/log?archived=1')
        logs_arch = json.loads(res_get_arch.data)
        self.assertEqual(len(logs_arch), 1)
        self.assertEqual(logs_arch[0]['profile_number'], 'P-TEST-LOG')

        # Verify it does NOT show up in active GET
        res_get_act = self.client.get('/api/waste_acceptance/log?archived=0')
        logs_act = json.loads(res_get_act.data)
        self.assertEqual(len(logs_act), 0)

        # Restore log testing (unarchive)
        res_rest = self.client.post('/api/waste_acceptance/log/archive',
                                    data=json.dumps({'id': log_id, 'is_archived': 0}),
                                    content_type='application/json')
        self.assertEqual(res_rest.status_code, 200)
        self.assertTrue(json.loads(res_rest.data)['success'])

        # Verify it's back in active GET
        res_get_act2 = self.client.get('/api/waste_acceptance/log?archived=0')
        logs_act2 = json.loads(res_get_act2.data)
        self.assertEqual(len(logs_act2), 1)

        # Delete log
        res_del = self.client.post('/api/waste_acceptance/log/delete', 
                                   data=json.dumps({'id': log_id}),
                                   content_type='application/json')
        self.assertEqual(res_del.status_code, 200)

        # Get log to verify deletion
        res_get_del = self.client.get('/api/waste_acceptance/log')
        logs_del = json.loads(res_get_del.data)
        self.assertEqual(len(logs_del), 0)

    def test_stu_audit_trail(self):
        """Test the STU audit trail endpoint renders without table errors"""
        conn = original_connect(TEST_DB_PATH)
        conn.execute("INSERT INTO put_pile_retreats (track_no, retreat_date, recipe, notes) VALUES ('TRACK-99', '2026-08-11', 'Recipe A', 'Test retreat')")
        conn.commit()
        conn.close()

        res = self.client.get('/stu/audit_trail')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'TRACK-99', res.data)

    def test_cnon_norm_win_code(self):
        """Test CNON WIN code handling and procedure generation"""
        conn = original_connect(TEST_DB_PATH)
        conn.execute('''
            INSERT OR REPLACE INTO profiles (profile_number, generator, status, win_code)
            VALUES ('P-NORM-TEST', 'NORM Generator', 'ACTIVE', 'CNON')
        ''')
        conn.commit()
        conn.close()

        res = self.client.get('/api/profile/search?q=P-NORM-TEST')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertTrue(any(p['win_code'] == 'CNON' for p in data))

    def test_custom_procedure_overrides(self):
        """Test one-off custom procedure overrides in profiles and WVI output"""
        conn = original_connect(TEST_DB_PATH)
        conn.execute('''
            INSERT OR REPLACE INTO profiles (profile_number, generator, status, win_code, sample_procedures, unloading_instructions)
            VALUES ('P-CUSTOM-TEST', 'Custom Generator', 'ACTIVE', 'CBP', 'CUSTOM SAMPLING SCOOP', 'CUSTOM UNLOAD BAYS')
        ''')
        conn.commit()
        conn.close()

        res = self.client.get('/api/profile/search?q=P-CUSTOM-TEST')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        match = next(p for p in data if p['profile_number'] == 'P-CUSTOM-TEST')
        self.assertEqual(match['sample_procedures'], 'CUSTOM SAMPLING SCOOP')
        self.assertEqual(match['unloading_instructions'], 'CUSTOM UNLOAD BAYS')

    def test_cbpr_win_code_ldr_validation(self):
        """Test that saving a master profile with WIN code CBPR requires LDR Required = Yes and an LDR option"""
        # Case 1: LDR Required = No -> Fail 400
        res = self.client.post('/add_master_profile', data={
            'profile_number': 'P-CBPR-FAIL1',
            'generator': 'Test Gen',
            'win_code': 'CBPR',
            'ldr_required': 'No',
            'ldr_option': '2',
            'status': 'ACTIVE'
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn(b"LDR Required must be set to 'Yes'", res.data)

        # Case 2: LDR Option missing -> Fail 400
        res = self.client.post('/add_master_profile', data={
            'profile_number': 'P-CBPR-FAIL2',
            'generator': 'Test Gen',
            'win_code': 'CBPR',
            'ldr_required': 'Yes',
            'ldr_option': '',
            'status': 'ACTIVE'
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn(b"an LDR Option must be selected", res.data)

        # Case 3: LDR Required = Yes & LDR Option = 2 -> Success 200/302
        res = self.client.post('/add_master_profile', data={
            'profile_number': 'P-CBPR-PASS',
            'generator': 'Test Gen',
            'win_code': 'CBPR',
            'ldr_required': 'Yes',
            'ldr_option': '2',
            'status': 'ACTIVE'
        })
        self.assertIn(res.status_code, [200, 302])

if __name__ == '__main__':
    unittest.main()
