# test_profile_sync.py
import os
import unittest
import sqlite3
import pandas as pd
import time
from datetime import date

# Set testing environment variables before importing app modules
TEST_DB_PATH = 'test_database.db'
TEST_EXCEL_PATH = 'test_masterprofile.xlsx'
os.environ['MASTERPROFILE_PATH'] = TEST_EXCEL_PATH

import shared_state
shared_state.DB_PATH = TEST_DB_PATH
shared_state.MASTER_EXCEL_PATH = TEST_EXCEL_PATH

# Override database connection locally
import database
database.MASTER_EXCEL_PATH = TEST_EXCEL_PATH
original_connect = sqlite3.connect

def mock_connect(database_name, *args, **kwargs):
    import os
    db_abs = os.path.abspath(database_name)
    main_db_abs = os.path.abspath(TEST_DB_PATH)
    prod_db_abs = os.path.abspath('database.db')
    if db_abs == main_db_abs or db_abs == prod_db_abs or database_name == 'database.db':
        return original_connect(TEST_DB_PATH, *args, **kwargs)
    return original_connect(database_name, *args, **kwargs)

sqlite3.connect = mock_connect

# Import the database module after patching connection
from database import ensure_profile_exists
from app import app, upgrade_db

class TestProfileSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        cls.client = app.test_client()

    def setUp(self):
        self.cleanup_files()
        upgrade_db()
        
    def tearDown(self):
        self.cleanup_files()

    def cleanup_files(self):
        # Remove test database
        for ext in ['', '-wal', '-shm']:
            p = TEST_DB_PATH + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        # Remove test excel
        if os.path.exists(TEST_EXCEL_PATH):
            try:
                os.remove(TEST_EXCEL_PATH)
            except OSError:
                pass

    def create_mock_excel(self, data):
        df = pd.DataFrame(data)
        df.to_excel(TEST_EXCEL_PATH, sheet_name='Data', index=False)
        # Ensure we set a clean modification time
        os.utime(TEST_EXCEL_PATH, (time.time(), time.time()))

    def test_lazy_loading_profile_success(self):
        """Verify that a profile missing in DB is successfully loaded from Excel"""
        # Create Excel with two profiles
        mock_data = {
            'Profile #': ['P-EXCEL-1', 'P-EXCEL-2'],
            'GENERATOR': ['Gen One', 'Gen Two'],
            'STATUS': ['ACTIVE', 'EXPIRED'],
            'EXP DATE': ['2030-01-01', '2020-01-01'],
            'WASTE NAME': ['Excel Waste 1', 'Excel Waste 2'],
            'VOC #': ['15.5 ppm', '250.0'],
            'WIN CODE': ['W1', 'W2']
        }
        self.create_mock_excel(mock_data)

        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row

        # Before lookup: database has no such profile
        row_before = conn.execute("SELECT * FROM profiles WHERE profile_number = 'P-EXCEL-1'").fetchone()
        self.assertIsNone(row_before)

        # Trigger ensure_profile_exists
        row_after = ensure_profile_exists(conn, 'P-EXCEL-1')
        self.assertIsNotNone(row_after)
        self.assertEqual(row_after['profile_number'], 'P-EXCEL-1')
        self.assertEqual(row_after['generator'], 'Gen One')
        self.assertEqual(row_after['status'], 'ACTIVE')
        self.assertEqual(row_after['expiration_date'], '2030-01-01')
        self.assertEqual(row_after['waste_description'], 'Excel Waste 1')
        self.assertEqual(row_after['voc_percentage'], 15.5)
        self.assertEqual(row_after['win_code'], 'W1')

        # Query directly from DB to verify it persists
        db_row = conn.execute("SELECT * FROM profiles WHERE profile_number = 'P-EXCEL-1'").fetchone()
        self.assertIsNotNone(db_row)
        self.assertEqual(db_row['generator'], 'Gen One')
        
        conn.close()

    def test_profile_update_propagation(self):
        """Verify that profile changes (e.g. status expired to active) in Excel auto-propagate to DB"""
        # Step 1: Initial load
        mock_data_1 = {
            'Profile #': ['P-EXCEL-UPD'],
            'GENERATOR': ['Gen One'],
            'STATUS': ['EXPIRED'],
            'EXP DATE': ['2020-01-01'],
            'WASTE NAME': ['Expired Waste'],
            'VOC #': ['10'],
            'WIN CODE': ['W-UPD']
        }
        self.create_mock_excel(mock_data_1)

        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row

        # Fetch and verify initial import status is EXPIRED
        row_1 = ensure_profile_exists(conn, 'P-EXCEL-UPD')
        self.assertIsNotNone(row_1)
        self.assertEqual(row_1['status'], 'EXPIRED')
        mtime_1 = row_1['last_synced_mtime']
        self.assertIsNotNone(mtime_1)

        # Step 2: Update Excel status to ACTIVE and change expiration date, then force different mtime
        # Wait a moment to ensure modification times differ, or adjust with os.utime
        time.sleep(1.1)
        mock_data_2 = {
            'Profile #': ['P-EXCEL-UPD'],
            'GENERATOR': ['Gen One'],
            'STATUS': ['ACTIVE'],
            'EXP DATE': ['2035-12-31'],
            'WASTE NAME': ['Expired Waste'],
            'VOC #': ['10'],
            'WIN CODE': ['W-UPD']
        }
        self.create_mock_excel(mock_data_2)
        
        # Alter mtime manually to ensure it is forward-shifted
        new_mtime = time.time() + 10
        os.utime(TEST_EXCEL_PATH, (new_mtime, new_mtime))

        # Query details: it should reload Excel and update DB to ACTIVE automatically
        row_2 = ensure_profile_exists(conn, 'P-EXCEL-UPD')
        self.assertIsNotNone(row_2)
        self.assertEqual(row_2['status'], 'ACTIVE')
        self.assertEqual(row_2['expiration_date'], '2035-12-31')
        self.assertEqual(row_2['last_synced_mtime'], new_mtime)

        conn.close()

    def test_not_found_caching(self):
        """Verify that non-existent profiles cache a NOT FOUND state to avoid repeatedly reading Excel"""
        mock_data = {
            'Profile #': ['P-VALID'],
            'GENERATOR': ['Gen Valid'],
            'STATUS': ['ACTIVE'],
            'EXP DATE': ['2030-01-01'],
            'WASTE NAME': ['Valid Waste'],
            'VOC #': ['0.0'],
            'WIN CODE': ['W-VAL']
        }
        self.create_mock_excel(mock_data)

        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row

        # Lookup invalid profile. It should return None.
        row = ensure_profile_exists(conn, 'P-INVALID')
        self.assertIsNone(row)

        # Check DB to verify a row was inserted with status 'NOT FOUND'
        db_row = conn.execute("SELECT * FROM profiles WHERE profile_number = 'P-INVALID'").fetchone()
        self.assertIsNotNone(db_row)
        self.assertEqual(db_row['status'], 'NOT FOUND')

        # Rename Excel file to simulate network outage. Subsequent calls should still return None instantly
        # without throwing file-not-found errors because the result is cached!
        os.remove(TEST_EXCEL_PATH)

        row_cached = ensure_profile_exists(conn, 'P-INVALID')
        self.assertIsNone(row_cached)

        conn.close()

if __name__ == '__main__':
    unittest.main()
