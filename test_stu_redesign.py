# test_stu_redesign.py
import os
import unittest
import sqlite3
import json

# Setup mock database path
TEST_DB_PATH = 'test_database.db'

# Patch connect globally so the Flask app uses our test database
original_connect = sqlite3.connect
def mock_connect(database, *args, **kwargs):
    import os
    db_abs = os.path.abspath(database)
    main_db_abs = os.path.abspath(TEST_DB_PATH)
    prod_db_abs = os.path.abspath('database.db')
    if db_abs == main_db_abs or db_abs == prod_db_abs or database == 'database.db':
        return original_connect(TEST_DB_PATH, *args, **kwargs)
    return original_connect(database, *args, **kwargs)
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
        """Test that D80L and LLF are part of the permitted STU WIN codes"""
        from stu_services import PERMITTED_CODES as pc_services
        from app import PERMITTED_CODES as pc_app
        self.assertIn('D80L', pc_services)
        self.assertIn('D80L', pc_app)
        self.assertIn('LLF', pc_services)
        self.assertIn('LLF', pc_app)

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
        self.assertEqual(rows[0]['generator'], 'Test Generator')
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
        
        # 1. Insert a pending sampling drum to verify it gets preserved during upload
        conn = original_connect(TEST_DB_PATH)
        conn.execute('''
            INSERT INTO drum_inventory (track_no, inb_prof, manifest, process_type, weight, ph, age, voc_ppm, voc_weight, import_date, job_id, status)
            VALUES ('DRUM-PENDING', 'P-STU-TEST', 'MAN-111', 'PENDING SAMPLING', 100.0, 7.0, 10.0, 10.0, 1000.0, '2026-06-01', 'JOB-111', 'PLANT RECEIVED')
        ''')
        conn.commit()
        conn.close()

        csv_data = (
            "Track No,Process Type,Weight,pH,Inb Prof,Age,Type,Area\n"
            "DRUM-NEW,direct land haz,450.0,6.5,P-STU-TEST,5.0,DM,Area-51\n"
            "DRUM-PENDING-OVERWRITE,pending sampling,200.0,8.0,P-STU-TEST,2.0,DM,Area-52\n"
            "DRUM-HEAVY-BULK,direct land nh,42000.0,7.0,P-STU-TEST,5.0,Roll-Off,Area-53\n"
            "DRUM-HEAVY-PUT,put pile,35000.0,7.0,P-STU-TEST,5.0,Roll-Off,Cell 34 Open\n"
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

        # The pending sampling drum DRUM-PENDING (which was not in CSV) should be preserved
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

        conn.close()

if __name__ == '__main__':
    unittest.main()
