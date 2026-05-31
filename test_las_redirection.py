# test_las_redirection.py
import os
import unittest
import sqlite3
from datetime import date, datetime

# Override the database connection globally at sqlite3 level
TEST_DB_PATH = 'test_database.db'
original_connect = sqlite3.connect

def mock_connect(database, *args, **kwargs):
    if database == 'database.db':
        return original_connect(TEST_DB_PATH, *args, **kwargs)
    return original_connect(database, *args, **kwargs)

sqlite3.connect = mock_connect

# Import app now that sqlite3.connect is patched
from app import app, upgrade_db

class TestLASRedirection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        cls.client = app.test_client()

    def setUp(self):
        self.cleanup_db()
        upgrade_db()
        self.populate_test_data()

    def tearDown(self):
        self.cleanup_db()

    def cleanup_db(self):
        for ext in ['', '-wal', '-shm']:
            p = TEST_DB_PATH + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def populate_test_data(self):
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            # Insert Master Profiles:
            # 1. PLAS: non-CCS profile that triggers LAS (e.g. status='INACTIVE' or similar, we will assign test_assigned = 'LAS')
            # 2. PCCS: CCS profile that triggers LAS
            # 3. PNORMAL: active profile, standard fingerprint
            conn.execute('''
                INSERT INTO profiles (profile_number, generator, waste_description, win_code, voc_percentage, status, expiration_date)
                VALUES 
                ('PLAS', 'LAS Generator', 'LAS Waste description', 'WINLAS', 10.0, 'INACTIVE', '2020-01-01'),
                ('PCCS', 'CCS Generator', 'CCS Waste description', 'CCS-WIN', 20.0, 'INACTIVE', '2020-01-01'),
                ('PNORMAL', 'Normal Gen', 'Fingerprint waste', 'WINNORMAL', 0.0, 'ACTIVE', '2030-01-01')
            ''')

            # Insert daily schedule entries
            today_str = date.today().isoformat()
            conn.execute('''
                INSERT INTO daily_schedule (schedule_date, start_time, end_time, profile_number, load_count, generator, routing_code)
                VALUES 
                (?, '08:00', '12:00', 'PLAS', 2, 'LAS Generator', 'BL'),
                (?, '09:00', '15:00', 'PCCS', 2, 'CCS Generator', 'BL'),
                (?, '10:00', '16:00', 'PNORMAL', 1, 'Normal Gen', 'BL')
            ''', (today_str, today_str, today_str))

            # Insert weighed-in truck logs:
            # Truck 1: First load of PLAS profile, flagged as LAS
            # Truck 2: Second load of PLAS profile (subsequent load, standard test like FINGERPRINT)
            # Truck 3: First load of PCCS profile, flagged as LAS (high voc check test)
            # Truck 4: Normal truck, assigned FINGERPRINT
            conn.execute('''
                INSERT INTO truck_logs (
                    truck_id, profile_number, manifest_number, load_number,
                    gross_weight, test_assigned, test_status, date_received
                ) VALUES 
                ('TRK1', 'PLAS', 'M-LAS-1', 'L1', 50000.0, 'LAS', 'WEIGHED IN', ?),
                ('TRK2', 'PLAS', 'M-LAS-2', 'L2', 51000.0, 'FINGERPRINT', 'WEIGHED IN', ?),
                ('TRK3', 'PCCS', 'M-CCS-1', 'L3', 52000.0, 'LAS', 'WEIGHED IN', ?),
                ('TRK4', 'PNORMAL', 'M-NORM-1', 'L4', 48000.0, 'FINGERPRINT', 'WEIGHED IN', ?)
            ''', (today_str, today_str, today_str, today_str))

            conn.commit()
        finally:
            conn.close()

    def test_chemist_dashboard_excludes_las_trucks(self):
        """Verify that LAS bulk trucks are excluded from the chemist's pending list"""
        response = self.client.get('/chemist')
        self.assertEqual(response.status_code, 200)
        # Should not show the LAS truck logs (TRK1 / M-LAS-1 and TRK3 / M-CCS-1)
        self.assertNotIn(b'M-LAS-1', response.data)
        self.assertNotIn(b'M-CCS-1', response.data)
        # Should show the normal fingerprint load (TRK4 / M-NORM-1)
        self.assertIn(b'M-NORM-1', response.data)

    def test_waste_acceptance_dashboard_includes_las_trucks(self):
        """Verify that LAS bulk trucks appear in the STU hub pipeline"""
        response = self.client.get('/stu/hub?view=pipeline')
        self.assertEqual(response.status_code, 200)
        # Should show pending LAS trucks
        self.assertIn(b'M-LAS-1', response.data)
        self.assertIn(b'M-CCS-1', response.data)

    def test_release_las_truck_non_ccs(self):
        """Verify successful release of a non-CCS LAS truck and check DB values"""
        # Get truck ID for TRK1
        conn = original_connect(TEST_DB_PATH)
        row = conn.execute("SELECT id FROM truck_logs WHERE truck_id = 'TRK1'").fetchone()
        truck_log_id = row[0]
        conn.close()

        response = self.client.post('/release_las_truck', data={
            'log_id': truck_log_id,
            'measured_ph': '6.8',
            'measured_voc': '15.5',
            'measured_sulfides': 'Negative',
            'measured_cyanide': 'Negative',
            'measured_free_liquids': 'No',
            'notes': 'Verified profile and released.'
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)

        # Check DB updates
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        updated_truck = conn.execute("SELECT * FROM truck_logs WHERE truck_id = 'TRK1'").fetchone()
        conn.close()

        self.assertEqual(updated_truck['test_status'], 'LAB COMPLETED')
        self.assertEqual(updated_truck['measured_ph'], 6.8)
        self.assertEqual(updated_truck['measured_voc'], 15.5)
        self.assertEqual(updated_truck['measured_sulfides'], 'Negative')
        self.assertEqual(updated_truck['measured_cyanide'], 'Negative')
        self.assertEqual(updated_truck['measured_free_liquids'], 'No')
        self.assertEqual(updated_truck['voc_pass_fail'], 'N/A')  # Non-CCS should be N/A
        self.assertEqual(updated_truck['lab_results'], 'Released by Waste Acceptance. Verified profile and released.')

    def test_release_las_truck_ccs_pass(self):
        """Verify successful release of a CCS LAS truck with VOC <= 500 ppm (PASS)"""
        conn = original_connect(TEST_DB_PATH)
        row = conn.execute("SELECT id FROM truck_logs WHERE truck_id = 'TRK3'").fetchone()
        truck_log_id = row[0]
        conn.close()

        response = self.client.post('/release_las_truck', data={
            'log_id': truck_log_id,
            'measured_ph': '7.5',
            'measured_voc': '350.0',
            'measured_sulfides': 'Negative',
            'measured_cyanide': 'Negative',
            'measured_free_liquids': 'No',
            'measured_flashpoint': '>200F',
            'notes': 'CCS passes.'
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)

        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        updated_truck = conn.execute("SELECT * FROM truck_logs WHERE truck_id = 'TRK3'").fetchone()
        conn.close()

        self.assertEqual(updated_truck['test_status'], 'LAB COMPLETED')
        self.assertEqual(updated_truck['measured_voc'], 350.0)
        self.assertEqual(updated_truck['measured_flashpoint'], '>200F')
        self.assertEqual(updated_truck['voc_pass_fail'], 'PASS')  # CCS <= 500 should be PASS

    def test_release_las_truck_ccs_fail(self):
        """Verify release of a CCS LAS truck with VOC > 500 ppm results in FAIL"""
        conn = original_connect(TEST_DB_PATH)
        row = conn.execute("SELECT id FROM truck_logs WHERE truck_id = 'TRK3'").fetchone()
        truck_log_id = row[0]
        conn.close()

        response = self.client.post('/release_las_truck', data={
            'log_id': truck_log_id,
            'measured_ph': '7.5',
            'measured_voc': '600.0',
            'measured_sulfides': 'Negative',
            'measured_cyanide': 'Negative',
            'measured_free_liquids': 'No',
            'measured_flashpoint': '>200F',
            'notes': 'CCS fails VOC limit.'
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)

        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        updated_truck = conn.execute("SELECT * FROM truck_logs WHERE truck_id = 'TRK3'").fetchone()
        conn.close()

        self.assertEqual(updated_truck['test_status'], 'LAB COMPLETED')
        self.assertEqual(updated_truck['measured_voc'], 600.0)
        self.assertEqual(updated_truck['voc_pass_fail'], 'FAIL')  # CCS > 500 should be FAIL

    def test_weighmaster_receiving_badges_progression(self):
        """Verify the progression of Weighmaster badges for LAS and subsequent loads"""
        # Step 1: Query receiving log initial state.
        # - Truck 1 (PLAS, first load, LAS, weighed in) -> Should show test_assigned badge ('LAS')
        # - Truck 2 (PLAS, second load, FINGERPRINT, weighed in) -> Should show standard 'FINGERPRINT'
        response = self.client.get('/receiving')
        self.assertEqual(response.status_code, 200)
        
        # Should show: <span class="badge bg-danger text-white shadow-sm">LAS</span>
        self.assertIn(b'bg-danger text-white shadow-sm">LAS', response.data)
        # Should show: <span class="badge bg-warning text-dark shadow-sm">FINGERPRINT</span>
        self.assertIn(b'bg-warning text-dark shadow-sm">FINGERPRINT', response.data)
        self.assertNotIn(b'WAITING FOR LAS', response.data)

        # Step 2: Complete the laboratory test for Truck 2 (subsequent load)
        conn = original_connect(TEST_DB_PATH)
        conn.execute("UPDATE truck_logs SET test_status = 'LAB COMPLETED' WHERE truck_id = 'TRK2'")
        conn.commit()
        conn.close()

        # Re-query receiving log: now Truck 2 should show 'WAITING FOR LAS'
        response_step2 = self.client.get('/receiving')
        self.assertEqual(response_step2.status_code, 200)
        self.assertIn(b'bg-warning text-dark shadow-sm">WAITING FOR LAS', response_step2.data)

        # Step 3: Release the first load (TRK1)
        conn = original_connect(TEST_DB_PATH)
        row = conn.execute("SELECT id FROM truck_logs WHERE truck_id = 'TRK1'").fetchone()
        truck_log_id = row[0]
        conn.close()

        self.client.post('/release_las_truck', data={
            'log_id': truck_log_id,
            'measured_ph': '7.0',
            'measured_voc': '0.0',
            'measured_sulfides': 'Negative',
            'measured_cyanide': 'Negative',
            'measured_free_liquids': 'No'
        })

        # Step 4: Re-query receiving log.
        # - Truck 1 (PLAS, first load, released) -> Should show 'RELEASED' with green badge (bg-success text-white)
        # - Truck 2 (PLAS, second load, subsequent) -> Should now show 'OK TO RELEASE' with green badge (bg-success text-white)
        response2 = self.client.get('/receiving')
        self.assertEqual(response2.status_code, 200)
        
        # Truck 1: Released
        self.assertIn(b'bg-success text-white shadow-sm">RELEASED', response2.data)
        # Truck 2: OK to release
        self.assertIn(b'bg-success text-white shadow-sm">OK TO RELEASE', response2.data)

    def test_large_bulk_las_assignment(self):
        """Verify that a Large Bulk job type does not bypass LAS assignment on first check-in"""
        # Create a new profile that triggers LAS
        conn = original_connect(TEST_DB_PATH)
        conn.execute('''
            INSERT INTO profiles (profile_number, generator, waste_description, win_code, voc_percentage, status, expiration_date)
            VALUES ('PLAS-LB', 'Gen', 'Desc', 'WIN-LB', 0.0, 'INACTIVE', '2020-01-01')
        ''')
        # Insert a schedule entry with 10 loads to force Large Bulk
        today_str = date.today().isoformat()
        conn.execute('''
            INSERT INTO daily_schedule (schedule_date, start_time, end_time, profile_number, load_count, generator, routing_code)
            VALUES (?, '08:00', '12:00', 'PLAS-LB', 10, 'Gen', 'BL')
        ''', (today_str,))
        conn.commit()
        conn.close()

        # Submit the first truck for this profile
        response = self.client.post('/submit_truck', data={
            'profile_number': 'PLAS-LB',
            'manifest_number': 'M-LB-1',
            'load_number': 'L-LB-1',
            'gross_weight': '50000',
            'container_type': 'End Dump',
            'shipping_mode': 'Solid',
            'job_type': 'Large Bulk'
        }, follow_redirects=True)

        self.assertEqual(response.status_code, 200)

        # Retrieve from DB and verify that test_assigned is 'LAS (Large Bulk)'
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        truck = conn.execute("SELECT * FROM truck_logs WHERE profile_number = 'PLAS-LB' AND manifest_number = 'M-LB-1'").fetchone()
        conn.close()

        self.assertIsNotNone(truck)
        self.assertEqual(truck['test_assigned'], 'LAS (Large Bulk)')

if __name__ == '__main__':
    unittest.main()
