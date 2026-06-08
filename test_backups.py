import os
import unittest
import sqlite3
import shutil
from datetime import date
from flask import session

# Patch sqlite3 connect globally for test database isolation
TEST_DB_PATH = 'test_database.db'
TEST_BACKUP_DIR = 'test_backups_dir'
TEST_I_DRIVE_DIR = 'test_i_drive_dir'

import shared_state
shared_state.DB_PATH = TEST_DB_PATH

original_connect = sqlite3.connect

def mock_connect(database, *args, **kwargs):
    # If it is the main database connection, redirect to TEST_DB_PATH
    import os
    db_abs = os.path.abspath(database)
    main_db_abs = os.path.abspath(TEST_DB_PATH)
    prod_db_abs = os.path.abspath('database.db')
    if db_abs == main_db_abs or db_abs == prod_db_abs or database == 'database.db':
        return original_connect(TEST_DB_PATH, *args, **kwargs)
    return original_connect(database, *args, **kwargs)

sqlite3.connect = mock_connect

# Set environment variables for tests
os.environ['BACKUP_ADMIN_PASSWORD'] = 'TestAdminPass123!'

# Import app and route config
from app import app, upgrade_db
import routes_backups

# Point the blueprint constants to test directories
routes_backups.DB_PATH = TEST_DB_PATH
routes_backups.BACKUP_DIR = TEST_BACKUP_DIR
routes_backups.I_DRIVE_DIR = TEST_I_DRIVE_DIR

class TestDatabaseBackups(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test-secret-key'
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
        # Remove test backups directory
        if os.path.exists(TEST_BACKUP_DIR):
            try:
                shutil.rmtree(TEST_BACKUP_DIR)
            except OSError:
                pass
        # Remove test I drive directory
        if os.path.exists(TEST_I_DRIVE_DIR):
            try:
                shutil.rmtree(TEST_I_DRIVE_DIR)
            except OSError:
                pass

    def login(self):
        return self.client.post('/backups/login', data={'password': 'TestAdminPass123!'}, follow_redirects=True)

    def test_auth_redirect(self):
        # Accessing dashboard when unauthenticated should redirect to login
        response = self.client.get('/backups', follow_redirects=True)
        self.assertIn(b'Backup Manager Login', response.data)
        self.assertIn(b'Admin Password', response.data)

    def test_login_failure(self):
        # Post bad password
        response = self.client.post('/backups/login', data={'password': 'wrong_password'}, follow_redirects=True)
        self.assertIn(b'Incorrect admin password', response.data)

    def test_login_success(self):
        # Post good password
        response = self.login()
        self.assertIn(b'Database Backups Manager', response.data)
        self.assertIn(b'Recent Backups', response.data)

    def test_backup_creation_logic(self):
        # Perform backup
        success, filename = routes_backups.run_backup_logic()
        self.assertTrue(success)
        
        # Extract base filename (excluding status messages appended at work)
        base_filename = filename.split(' ')[0]
        self.assertTrue(base_filename.startswith('database_backup_'))
        self.assertTrue(base_filename.endswith('.db'))
        
        # Verify file exists on disk
        backup_path = os.path.join(TEST_BACKUP_DIR, base_filename)
        self.assertTrue(os.path.exists(backup_path))
        
        # Verify it is a valid SQLite DB by connecting to it
        conn = original_connect(backup_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='truck_logs'")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        conn.close()

    def test_rolling_window_cleanup(self):
        os.makedirs(TEST_BACKUP_DIR, exist_ok=True)
        # Touch 12 dummy backup files
        for i in range(12):
            dummy_path = os.path.join(TEST_BACKUP_DIR, f"database_backup_20260527_1200{i:02d}.db")
            with open(dummy_path, 'w') as f:
                f.write('SQLite dummy content')
                
        # Run backup logic which will add 1 real backup and clean up older ones
        success, filename = routes_backups.run_backup_logic()
        self.assertTrue(success)
        base_filename = filename.split(' ')[0]
        
        # The rolling window should keep exactly 10 backups
        files = [f for f in os.listdir(TEST_BACKUP_DIR) if f.startswith('database_backup_') and f.endswith('.db')]
        self.assertEqual(len(files), 10)
        
        # Verify the new backup is in the list
        self.assertIn(base_filename, files)

    def test_dashboard_route(self):
        # Create a backup
        routes_backups.run_backup_logic()
        
        self.login()
        response = self.client.get('/backups')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'database_backup_', response.data)

    def test_download_backup(self):
        # Create backup
        success, filename = routes_backups.run_backup_logic()
        base_filename = filename.split(' ')[0]
        self.login()
        
        response = self.client.get(f'/backups/download/{base_filename}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('Content-Disposition'), f'attachment; filename={base_filename}')

    def test_delete_backup(self):
        # Create backup
        success, filename = routes_backups.run_backup_logic()
        base_filename = filename.split(' ')[0]
        backup_path = os.path.join(TEST_BACKUP_DIR, base_filename)
        self.assertTrue(os.path.exists(backup_path))
        
        self.login()
        # Call delete route
        response = self.client.post(f'/backups/delete/{base_filename}', follow_redirects=True)
        self.assertIn(b'deleted successfully', response.data)
        self.assertFalse(os.path.exists(backup_path))

    def test_restore_backup(self):
        # Insert a sample entry into the test database
        conn = original_connect(TEST_DB_PATH)
        conn.execute("INSERT INTO truck_logs (truck_id, profile_number, manifest_number) VALUES ('T-100', 'P-100', 'M-100')")
        conn.commit()
        conn.close()
        
        # Backup the database with this entry
        success, filename = routes_backups.run_backup_logic()
        self.assertTrue(success)
        base_filename = filename.split(' ')[0]
        
        # Modify the database (delete the record)
        conn = original_connect(TEST_DB_PATH)
        conn.execute("DELETE FROM truck_logs WHERE truck_id='T-100'")
        conn.commit()
        # Verify it's gone
        row = conn.execute("SELECT * FROM truck_logs WHERE truck_id='T-100'").fetchone()
        self.assertIsNone(row)
        conn.close()
        
        self.login()
        # Call restore route
        response = self.client.post(f'/backups/restore/{base_filename}', follow_redirects=True)
        self.assertIn(b'restored successfully', response.data)
        
        # Verify the record is restored
        conn = original_connect(TEST_DB_PATH)
        row = conn.execute("SELECT * FROM truck_logs WHERE truck_id='T-100'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[1], 'T-100')
        conn.close()

    def test_i_drive_sync_and_rotation(self):
        # Verify that running backup logic creates the database.db in I_DRIVE_DIR
        # and also a timestamped backup in I_DRIVE_DIR/backups/
        success, result = routes_backups.run_backup_logic()
        self.assertTrue(success)
        self.assertIn("Also synced to I: drive", result)
        
        # Verify startup sync DB is in I_DRIVE_DIR
        i_db_path = os.path.join(TEST_I_DRIVE_DIR, "database.db")
        self.assertTrue(os.path.exists(i_db_path))
        
        # Check that it's a valid database
        conn = original_connect(i_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='truck_logs'")
        self.assertIsNotNone(cursor.fetchone())
        conn.close()
        
        # Verify timestamped backup in backups/ subdir
        i_backup_dir = os.path.join(TEST_I_DRIVE_DIR, "backups")
        self.assertTrue(os.path.exists(i_backup_dir))
        files = os.listdir(i_backup_dir)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].startswith("database_backup_"))
        
        # Now touch 11 files in the simulated I-drive backups directory to test rotation
        for i in range(12):
            dummy_path = os.path.join(i_backup_dir, f"database_backup_20260605_1200{i:02d}.db")
            with open(dummy_path, 'w') as f:
                f.write('SQLite dummy')
                
        # Run backup logic again. It should rotate and keep exactly 10 in I_DRIVE_DIR/backups/
        success, result = routes_backups.run_backup_logic()
        self.assertTrue(success)
        
        i_files = sorted([f for f in os.listdir(i_backup_dir) if f.startswith('database_backup_')])
        self.assertEqual(len(i_files), 10)

if __name__ == '__main__':
    unittest.main()
