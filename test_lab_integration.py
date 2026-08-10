# test_lab_integration.py
import os
import unittest
import sqlite3
from datetime import date, timedelta

# Override the database connection globally at sqlite3 level
TEST_DB_PATH = 'test_database.db'

import shared_state
shared_state.DB_PATH = TEST_DB_PATH

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

# Import app now that sqlite3.connect is patched
from app import app, upgrade_db
import routes_reports
TEST_VOC_DIR = os.path.abspath('test_voc_dir')
os.makedirs(TEST_VOC_DIR, exist_ok=True)
routes_reports.VOC_DIR = TEST_VOC_DIR
routes_reports.VOC_CACHE_PATH = os.path.abspath('test_voc_cache.json')
routes_reports._VOC_FILE_CACHE = {}
routes_reports.get_voc_file_counts = lambda force_refresh=False: {}

class TestCompliancePortalIntegration(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        cls.client = app.test_client()

    def setUp(self):
        # Remove any lingering test db and recreate it fresh
        self.cleanup_db()
        
        # Initialize schema via app's upgrade_db
        upgrade_db()
        
        # Populate test fixtures
        self.populate_test_data()

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

    def populate_test_data(self):
        # Get connection to the test database
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            # 1. Insert Master Profiles
            conn.execute('''
                INSERT OR REPLACE INTO profiles (profile_number, generator, waste_description, win_code, voc_percentage)
                VALUES 
                ('P35', 'Gen 35', 'Haz Waste 35', 'WIN35', 150.0),
                ('P31', 'Gen 31', 'Non-Haz 31', 'WIN31', 0.0),
                ('PSTU', 'Gen STU', 'Drum Waste', 'WINSTU', 10.0),
                ('POTHR', 'Gen Oth', 'Landfill waste', 'WINOTH', 0.0)
            ''')
            
            # 2. Insert Scheduled Loads (daily_schedule)
            conn.execute('''
                INSERT INTO daily_schedule (schedule_date, start_time, end_time, profile_number, load_count, generator, routing_code)
                VALUES 
                ('2026-05-01', '08:00', '12:00', 'P35', 4, 'Gen 35', 'BL'),
                ('2026-05-01', '09:00', '15:00', 'P31', 6, 'Gen 31', 'BL'),
                ('2026-05-02', '08:00', '12:00', 'P35', 5, 'Gen 35', 'BL'),
                ('2026-05-03', '10:00', '16:00', 'PSTU', 2, 'Gen STU', 'BL')
            ''')
            
            # 3. Insert Actual Checked-in Trucks (truck_logs)
            conn.execute('''
                INSERT INTO truck_logs (
                    truck_id, profile_number, manifest_number, load_number,
                    gross_weight, exit_weight, net_weight, cell_location, grid_location,
                    date_received, time_in, time_out, test_status
                ) VALUES 
                -- May 1st actuals
                ('T1', 'P35', 'M35-1', '1', 50000.0, 30000.0, 20000.0, '35-CELL', 'G-1', '2026-05-01', '08:15', '09:00', 'COMPLETED'),
                ('T2', 'P35', 'M35-2', '2', 48000.0, 31000.0, 17000.0, '35-CELL', 'G-1', '2026-05-01', '09:30', '10:15', 'COMPLETED'),
                ('T3', 'P31', 'M31-1', '3', 60000.0, 30000.0, 30000.0, '31-CELL', 'G-2', '2026-05-01', '10:45', '11:30', 'COMPLETED'),
                
                -- May 2nd actuals
                ('T4', 'P35', 'M35-3', '4', 51000.0, 31000.0, 20000.0, '35-CELL', 'G-1', '2026-05-02', '08:30', '09:15', 'COMPLETED'),
                ('T5', 'P35', 'M35-4', '5', 52000.0, NULL, NULL, '35-CELL', 'G-1', '2026-05-02', '11:15', '', 'RECEIVED'),
                ('T6', 'POTHR', 'MOTH-1', '6', 45000.0, 25000.0, 20000.0, 'LF-CELL', 'G-9', '2026-05-02', '14:20', '15:00', 'COMPLETED'),
                
                -- May 3rd actuals
                ('T7', 'PSTU', 'MSTU-1', '7', 40000.0, 28000.0, 12000.0, 'BAY-A', 'G-STU', '2026-05-03', '07:45', '08:30', 'COMPLETED'),
                ('T8', 'PSTU', 'MSTU-2', '8', 42000.0, 29000.0, 13000.0, 'STU-1', 'G-STU', '2026-05-03', '12:00', '12:45', 'COMPLETED')
            ''')
            conn.commit()
        finally:
            conn.close()

    def test_compliance_html_route(self):
        """Verify the main compliance template renders successfully"""
        response = self.client.get('/compliance')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'COMPLIANCE PORTAL', response.data)
        self.assertIn(b'Select Analysis Report', response.data)

    def test_api_missing_dates(self):
        """Verify the API returns 400 error when date parameters are missing"""
        response = self.client.get('/api/compliance/data?report_type=variance')
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Missing dates', response.data)

    def test_api_invalid_date_format(self):
        """Verify the API returns 400 error when dates are invalid"""
        response = self.client.get('/api/compliance/data?report_type=variance&start_date=2026/05/01&end_date=2026-05-05')
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'Invalid date format', response.data)

    def test_api_variance_report(self):
        """Verify variance report returns correct calculations and structure"""
        response = self.client.get('/api/compliance/data?report_type=variance&start_date=2026-05-01&end_date=2026-05-03')
        self.assertEqual(response.status_code, 200)
        
        json_data = response.get_json()
        self.assertIn('labels', json_data)
        self.assertIn('datasets', json_data)
        self.assertIn('summary', json_data)
        self.assertIn('table_data', json_data)
        
        # Verify Labels correspond to dates in range
        self.assertEqual(json_data['labels'], ['2026-05-01', '2026-05-02', '2026-05-03'])
        
        scheduled_ds = next(d for d in json_data['datasets'] if d['label'] == 'Scheduled Loads')
        actual_ds = next(d for d in json_data['datasets'] if d['label'] == 'Actual Loads')
        variance_ds = next(d for d in json_data['datasets'] if d['label'] == 'Variance')
        
        self.assertEqual(scheduled_ds['data'], [10, 5, 2])
        self.assertEqual(actual_ds['data'], [3, 3, 2])
        self.assertEqual(variance_ds['data'], [-7, -2, 0])
        
        summary = json_data['summary']
        self.assertEqual(summary['kpi1_val'], 17)
        self.assertEqual(summary['kpi2_val'], 8)
        self.assertEqual(summary['kpi3_val'], '-9')
        self.assertEqual(summary['kpi4_val'], -3.0)

    def test_api_traffic_report(self):
        """Verify traffic report returns correct truck check-in stats"""
        response = self.client.get('/api/compliance/data?report_type=traffic&start_date=2026-05-01&end_date=2026-05-03')
        self.assertEqual(response.status_code, 200)
        
        json_data = response.get_json()
        self.assertIn('labels', json_data)
        self.assertIn('datasets', json_data)
        self.assertIn('summary', json_data)
        self.assertIn('table_data', json_data)
        
        traffic_ds = json_data['datasets'][0]
        self.assertEqual(traffic_ds['data'], [3, 3, 2])
        
        summary = json_data['summary']
        self.assertEqual(summary['kpi1_val'], 8)
        self.assertEqual(summary['kpi2_val'], 2.7)
        self.assertEqual(summary['kpi3_val'], 3)
        self.assertIn(summary['kpi4_val'], ['2026-05-01', '2026-05-02'])

    def test_api_tonnage_ytd_report(self):
        """Verify tonnage cumulative YTD calculation by specific WMU units"""
        response = self.client.get('/api/compliance/data?report_type=tonnage_ytd&start_date=2026-05-01&end_date=2026-05-03')
        self.assertEqual(response.status_code, 200)
        
        json_data = response.get_json()
        self.assertIn('labels', json_data)
        self.assertIn('datasets', json_data)
        self.assertIn('summary', json_data)
        
        cum35_ds = next(d for d in json_data['datasets'] if d['label'] == 'WMU 35 Cumulative')
        cum31_ds = next(d for d in json_data['datasets'] if d['label'] == 'WMU 31 Cumulative')
        cum_stu_ds = next(d for d in json_data['datasets'] if d['label'] == 'STU / Decon Cumulative')
        cum_lf_ds = next(d for d in json_data['datasets'] if d['label'] == 'Landfill / Other Cumulative')
        
        self.assertEqual(cum35_ds['data'], [37000.0, 57000.0, 57000.0])
        self.assertEqual(cum31_ds['data'], [30000.0, 30000.0, 30000.0])
        self.assertEqual(cum_stu_ds['data'], [0.0, 0.0, 25000.0])
        self.assertEqual(cum_lf_ds['data'], [0.0, 20000.0, 20000.0])
        
        summary = json_data['summary']
        self.assertEqual(summary['kpi1_val'], '132,000.00 Tons')
        self.assertEqual(summary['kpi2_val'], '57,000.00 Tons')
        self.assertEqual(summary['kpi3_val'], '30,000.00 Tons')
        self.assertEqual(summary['kpi4_val'], '25,000.00 Tons')

    def test_api_timings_report(self):
        """Verify timing hourly distribution report extraction"""
        response = self.client.get('/api/compliance/data?report_type=timings&start_date=2026-05-01&end_date=2026-05-03')
        self.assertEqual(response.status_code, 200)
        
        json_data = response.get_json()
        self.assertIn('labels', json_data)
        self.assertIn('datasets', json_data)
        self.assertIn('summary', json_data)
        
        # Verify hour labels
        self.assertEqual(json_data['labels'][0], "06:00")
        self.assertEqual(json_data['labels'][-1], "18:00")
        
        volume_ds = json_data['datasets'][0]
        self.assertEqual(volume_ds['data'], [0, 1, 2, 1, 1, 1, 1, 0, 1, 0, 0, 0, 0])
        
        summary = json_data['summary']
        self.assertEqual(summary['kpi1_val'], 8)
        self.assertEqual(summary['kpi2_val'], "08:00")
        self.assertEqual(summary['kpi3_val'], 2)
        self.assertEqual(summary['kpi4_val'], "25%")

    def test_api_norm_report(self):
        """Verify NORM report returns correct NORM load statistics and table rows"""
        conn = original_connect(TEST_DB_PATH)
        # Update POTHR to CNON to make it a NORM load
        conn.execute("UPDATE profiles SET win_code = 'CNON' WHERE profile_number = 'POTHR'")
        conn.commit()
        conn.close()
        
        response = self.client.get('/api/compliance/data?report_type=norm&start_date=2026-05-01&end_date=2026-05-03')
        self.assertEqual(response.status_code, 200)
        
        json_data = response.get_json()
        self.assertIn('labels', json_data)
        self.assertIn('datasets', json_data)
        self.assertIn('summary', json_data)
        self.assertIn('table_data', json_data)
        
        # We expect 1 NORM load (POTHR on May 2nd)
        summary = json_data['summary']
        self.assertEqual(summary['kpi1_val'], 1)
        self.assertEqual(summary['kpi2_val'], 1)
        self.assertEqual(summary['kpi3_val'], '20,000.00 Tons')
        self.assertEqual(summary['kpi4_val'], '2026-05-02')
        
        # Verify table row details
        table = json_data['table_data']
        self.assertEqual(len(table), 1)
        row = table[0]
        self.assertEqual(row['date'], '2026-05-02')
        self.assertEqual(row['profile_number'], 'POTHR')
        self.assertEqual(row['load_number'], '6')
        self.assertEqual(row['generator'], 'Gen Oth')

    def test_update_lab_voc(self):
        """Verify submitting lab results with VOC data correctly persists and triggers events"""
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id FROM truck_logs WHERE truck_id = 'T1'").fetchone()
        truck_id = row['id']
        conn.close()
        
        response = self.client.post('/update_lab', data={
            'log_id': truck_id,
            'lab_results': 'VOC verified ok',
            'specific_gravity': '1.025',
            'measured_ph': '7.5',
            'measured_flashpoint': '>200',
            'measured_sulfides': 'Negative',
            'measured_cyanide': 'Negative',
            'measured_free_liquids': 'No',
            'measured_voc': '12.4',
            'voc_pass_fail': 'PASS'
        })
        
        self.assertEqual(response.status_code, 302)
        
        # Verify in DB
        conn = original_connect(TEST_DB_PATH)
        conn.row_factory = sqlite3.Row
        updated_row = conn.execute("SELECT * FROM truck_logs WHERE id = ?", (truck_id,)).fetchone()
        conn.close()
        
        self.assertEqual(updated_row['measured_voc'], 12.4)
        self.assertEqual(updated_row['voc_pass_fail'], 'PASS')
        self.assertEqual(updated_row['lab_results'], 'VOC verified ok')

    def test_unscheduled_truck_in_reports(self):
        """Verify that an unscheduled checked-out truck is tracked in the reports page"""
        conn = original_connect(TEST_DB_PATH)
        # Insert an unscheduled truck log on 2026-05-01 (PUNSCHED has no schedule)
        conn.execute('''
            INSERT INTO truck_logs (
                truck_id, profile_number, manifest_number, load_number,
                gross_weight, exit_weight, net_weight, cell_location, grid_location,
                date_received, time_in, time_out, test_status
            ) VALUES (
                'TUNSCHED', 'PUNSCHED', 'MUNSCHED', '99',
                55000.0, 35000.0, 20000.0, '35-CELL', 'G-99',
                '2026-05-01', '13:00', '13:45', 'COMPLETED'
            )
        ''')
        conn.commit()
        conn.close()
        
        # Request reports page for 2026-05-01
        response = self.client.get('/reports?date=2026-05-01')
        self.assertEqual(response.status_code, 200)
        
        # Verify that PUNSCHED is in the html response
        self.assertIn(b'PUNSCHED', response.data)

if __name__ == '__main__':
    unittest.main()
