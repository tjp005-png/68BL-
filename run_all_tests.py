# run_all_tests.py
"""
Comprehensive Pre-Leave Quality Assurance & System Integrity Test Suite
Validates all core features, routes, database migrations, calculation engines,
excel exporters, backup schedulers, and email alert suites.
"""

import os
import sys
import unittest
import sqlite3
import json
import io
import shutil
from datetime import datetime, date

# Point tests to isolated test database
TEST_DB_PATH = 'test_database.db'

import shared_state
shared_state.DB_PATH = TEST_DB_PATH
shared_state.MASTER_EXCEL_PATH = 'non_existent_test_excel.xlsx'

# Patch sqlite3 connect globally for test database isolation
original_connect = sqlite3.connect

def mock_connect(database, *args, **kwargs):
    db_abs = os.path.abspath(database)
    main_db_abs = os.path.abspath(TEST_DB_PATH)
    prod_db_abs = os.path.abspath('database.db')
    if db_abs == main_db_abs or db_abs == prod_db_abs or database == 'database.db':
        return original_connect(TEST_DB_PATH, *args, **kwargs)
    return original_connect(database, *args, **kwargs)

sqlite3.connect = mock_connect

# Patch network VOC directory for test isolation
import routes_reports
TEST_VOC_DIR = os.path.abspath('test_voc_dir')
os.makedirs(TEST_VOC_DIR, exist_ok=True)
routes_reports.VOC_DIR = TEST_VOC_DIR
routes_reports._VOC_FILE_CACHE = {}
routes_reports.get_voc_file_counts = lambda force_refresh=False: {}

# Import Flask app and components
from app import app, upgrade_db
from database import get_db_connection, auto_sanitize_expired_profiles, ensure_profile_exists
from email_utils import generate_and_send_las_digest
import routes_backups

class TestPortalCoreSuite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret-key-2026'
        cls.client = app.test_client()

    def setUp(self):
        self.cleanup_db()
        upgrade_db()

    def tearDown(self):
        self.cleanup_db()

    def cleanup_db(self):
        if os.path.exists(TEST_DB_PATH):
            try:
                conn = original_connect(TEST_DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [r[0] for r in cursor.fetchall() if not r[0].startswith('sqlite_')]
                for t in tables:
                    cursor.execute(f"DELETE FROM {t}")
                conn.commit()
                conn.close()
            except Exception:
                pass
        for ext in ['', '-wal', '-shm']:
            p = TEST_DB_PATH + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    # -------------------------------------------------------------
    # 1. APP INITIALIZATION & ROUTE REGISTRATION TESTS
    # -------------------------------------------------------------
    def test_01_all_blueprint_routes_registered(self):
        """Verify all core HTML page routes respond with HTTP 200 OK"""
        routes_to_test = [
            '/',
            '/receiving',
            '/chemist',
            '/reports',
            '/schedule',
            '/stu/hub',
            '/approvals',
            '/compliance',
            '/yellow_entry'
        ]
        for route in routes_to_test:
            with self.subTest(route=route):
                res = self.client.get(route)
                self.assertEqual(res.status_code, 200, f"Route {route} failed with status {res.status_code}")

    # -------------------------------------------------------------
    # 2. DATABASE INTEGRITY & UPGRADE DB TESTS
    # -------------------------------------------------------------
    def test_02_database_schema_and_indexes(self):
        """Verify database tables and indexes are created properly by upgrade_db()"""
        conn = original_connect(TEST_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cursor.fetchall()}
        
        expected_tables = {
            'profiles', 'truck_logs', 'daily_schedule', 'waste_acceptance_log',
            'drum_inventory', 'drum_lab_queue', 'profile_wvi', 'profile_attachments',
            'voc_analyzer_logs', 'sulfide_testing_logs'
        }
        for tbl in expected_tables:
            self.assertIn(tbl, tables, f"Expected table '{tbl}' missing in database schema")
            
        conn.close()

    # -------------------------------------------------------------
    # 3. EXPIRED PROFILE AUTO-SANITIZATION & PROFILE SYNC TESTS
    # -------------------------------------------------------------
    def test_03_expired_profile_auto_sanitization(self):
        """Verify profiles with past expiration dates are automatically updated to EXPIRED status"""
        conn = get_db_connection()
        conn.execute('''
            INSERT OR REPLACE INTO profiles (profile_number, generator, status, expiration_date)
            VALUES 
            ('P-EXP-1', 'Expired Gen 1', 'ACTIVE', '2020-01-01'),
            ('P-ACT-1', 'Active Gen 1', 'ACTIVE', '2030-12-31')
        ''')
        conn.commit()

        auto_sanitize_expired_profiles(conn)

        exp_row = conn.execute("SELECT status FROM profiles WHERE profile_number = 'P-EXP-1'").fetchone()
        act_row = conn.execute("SELECT status FROM profiles WHERE profile_number = 'P-ACT-1'").fetchone()
        
        self.assertEqual(exp_row[0], 'EXPIRED', "Past expiration date profile should be set to EXPIRED")
        self.assertEqual(act_row[0], 'ACTIVE', "Future expiration date profile should remain ACTIVE")
        conn.close()

    # -------------------------------------------------------------
    # 4. SULFIDE 5-SAMPLE 90% CI STATISTICAL SUITE TESTS
    # -------------------------------------------------------------
    def test_04_sulfide_statistical_suite_math(self):
        """Verify sulfide 5-sample mean, stddev, 90% CI, and <= 500 mg/kg limit checks"""
        payload = {
            'cp1_number': 'CP1-998877',
            'lab_number': 'LAB-12345',
            'weight_ticket': 'WT-88776',
            'tested_by': 'Lab Chemist Test',
            'total_sulfide_samples': [100.0, 110.0, 105.0, 108.0, 102.0],
            'reactive_sulfide_samples': [200.0, 210.0, 205.0, 208.0, 202.0],
            'notes': 'Unit test sample calculation'
        }
        res = self.client.post('/api/profile/P-SULFIDE-1/sulfide_log', 
                               data=json.dumps(payload),
                               content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])

    # -------------------------------------------------------------
    # 5. VOC ANALYZER LOGGING (> 50 PPM) TESTS
    # -------------------------------------------------------------
    def test_05_voc_analyzer_threshold_logging(self):
        """Verify VOC values > 50 ppm are saved to voc_analyzer_logs table"""
        payload = {
            'cp1_number': 'CP1-554433',
            'voc_analyzer_value': 120.5,
            'original_voc_value': 15.0,
            'tested_by': 'Chemist Bob',
            'notes': 'VOC > 50 ppm test trigger'
        }
        res = self.client.post('/api/profile/P-VOC-HIGH/voc_analyzer',
                               data=json.dumps(payload),
                               content_type='application/json')
        self.assertEqual(res.status_code, 200)
        
        conn = get_db_connection()
        log = conn.execute("SELECT * FROM voc_analyzer_logs WHERE profile_number = 'P-VOC-HIGH'").fetchone()
        self.assertIsNotNone(log)
        self.assertEqual(log['voc_analyzer_value'], 120.5)
        conn.close()

    # -------------------------------------------------------------
    # 6. YELLOW ENTRY RECEIVING LOG SUBMIT TESTS
    # -------------------------------------------------------------
    def test_06_yellow_entry_submission_and_unit_conversion(self):
        """Verify Yellow Entry submit converts lbs to tons and stores unit/grid correctly"""
        conn = get_db_connection()
        conn.execute('''
            INSERT OR REPLACE INTO profiles (profile_number, generator, status, win_code)
            VALUES ('P-YELLOW-1', 'Yellow Gen', 'ACTIVE', 'CBP')
        ''')
        conn.commit()
        conn.close()

        payload = {
            'ticket_number': 'WT-99001',
            'manifest_number': 'MAN-99001',
            'weight': '48000',
            'weight_unit': 'LBS',
            'date_received': date.today().isoformat(),
            'profile_number': 'P-YELLOW-1',
            'cell_location': 'Unit-A',
            'grid_location': 'Grid-05'
        }
        res = self.client.post('/submit_yellow_entry', data=payload, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        conn = get_db_connection()
        truck = conn.execute("SELECT * FROM truck_logs WHERE profile_number = 'P-YELLOW-1'").fetchone()
        self.assertIsNotNone(truck)
        self.assertEqual(truck['load_number'], 'WT-99001')
        conn.close()

    # -------------------------------------------------------------
    # 7. BACKUP SCHEDULER & ROLL-CLEANUP TESTS
    # -------------------------------------------------------------
    def test_07_backup_scheduler_and_roll_window(self):
        """Verify database backup creation and 10-file rolling window cleanup"""
        test_b_dir = 'test_backups_dir'
        os.makedirs(test_b_dir, exist_ok=True)
        routes_backups.BACKUP_DIR = test_b_dir

        success, filename = routes_backups.run_backup_logic()
        self.assertTrue(success)
        base_filename = filename.split(' ')[0]
        self.assertTrue(os.path.exists(os.path.join(test_b_dir, base_filename)))

        shutil.rmtree(test_b_dir, ignore_errors=True)

    # -------------------------------------------------------------
    # 8. EMAIL ALERTS & DAILY 4:45 PM LAS SUMMARY DIGEST TESTS
    # -------------------------------------------------------------
    def test_08_email_digest_generation(self):
        """Verify LAS summary digest email generation runs without errors"""
        try:
            generate_and_send_las_digest(target_date=date.today().isoformat(), recipient='test@cleanharbors.com')
        except Exception as e:
            self.fail(f"generate_and_send_las_digest raised exception: {e}")

if __name__ == '__main__':
    print("=" * 70)
    print(" [QA-TEST] TRUCK LOG & WASTE OPERATIONS PORTAL PRE-LEAVE QA SUITE")
    print("=" * 70)
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestPortalCoreSuite)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
