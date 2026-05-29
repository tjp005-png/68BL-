import sys
import os
import random
from contextlib import closing

# Add the dev portal path to python path
sys.path.insert(0, r"c:\Users\PEREIRT446445\OneDrive - cleanharbors.com\Desktop\Truck_Log_App_Dev")

from app import app, get_db_connection

def run_wap_tests():
    print("=== Initializing WAP Sampling Logic Tests (Updated Rules) ===")
    client = app.test_client()
    
    # 1. Setup Test Profiles
    print("Inserting test profiles and schedule entries...")
    with closing(get_db_connection()) as conn:
        # Delete if already existing from a crash/previous run
        conn.execute("DELETE FROM profiles WHERE profile_number IN ('TEST-WAP-LOW', 'TEST-WAP-HIGH', 'TEST-WAP-LARGE')")
        conn.execute("DELETE FROM truck_logs WHERE profile_number IN ('TEST-WAP-LOW', 'TEST-WAP-HIGH', 'TEST-WAP-LARGE')")
        conn.execute("DELETE FROM daily_schedule WHERE profile_number IN ('TEST-WAP-LOW', 'TEST-WAP-HIGH', 'TEST-WAP-LARGE')")
        
        # Insert test profiles
        conn.execute("""
            INSERT INTO profiles (profile_number, generator, status, expiration_date, voc_percentage, win_code)
            VALUES ('TEST-WAP-LOW', 'WAP TEST GEN', 'ACTIVE', 'none', 0.0, 'N')
        """)
        conn.execute("""
            INSERT INTO profiles (profile_number, generator, status, expiration_date, voc_percentage, win_code)
            VALUES ('TEST-WAP-HIGH', 'WAP TEST GEN', 'ACTIVE', 'none', 55.0, 'N')
        """)
        conn.execute("""
            INSERT INTO profiles (profile_number, generator, status, expiration_date, voc_percentage, win_code)
            VALUES ('TEST-WAP-LARGE', 'WAP TEST GEN', 'ACTIVE', 'none', 0.0, 'N')
        """)
        
        # Insert schedule entry for Large Bulk test on TEST-WAP-LARGE for 2026-05-30 (10 loads scheduled)
        conn.execute("""
            INSERT INTO daily_schedule (schedule_date, start_time, end_time, profile_number, load_count, generator, sales_order, routing_code, scheduler_initials, special_notes)
            VALUES ('2026-05-30', '08:00', '16:00', 'TEST-WAP-LARGE', 10, 'WAP TEST GEN', 'SO-12345', 'D80', 'TST', 'Test notes')
        """)
        conn.commit()

    try:
        # 2. Test Standard Bulk (Solid) Logic on TEST-WAP-LOW
        print("\n--- Testing Standard Bulk (Solid) selective sampling ---")
        
        # 2a. Unscheduled / Daily Count < 10 should be ALWAYS sampled under the new rule:
        # "Profiles with less than 10 loads per day are always sampled"
        print("Checking first 12 loads on Day 1 (2026-05-28, unscheduled/0 scheduled loads < 10)...")
        for i in range(1, 13):
            load_num = f"L-LOW-{i}"
            res = client.post('/submit_truck', data={
                'load_number': load_num,
                'profile_number': 'TEST-WAP-LOW',
                'manifest_number': f"M-LOW-{i}",
                'gross_weight': '80000',
                'shipping_mode': 'Solid',
                'container_type': 'End Dump',
                'date_received': '2026-05-28'
            })
            assert res.status_code == 302
            
            # Query db to verify test assigned
            with closing(get_db_connection()) as conn:
                row = conn.execute("SELECT test_assigned FROM truck_logs WHERE load_number = ?", (load_num,)).fetchone()
                print(f"Load {i} overall: test_assigned = {row['test_assigned']}")
                if i <= 10:
                    assert "First 10" in row['test_assigned'], f"Load {i} should be 'First 10', got: {row['test_assigned']}"
                else:
                    assert "Daily < 10" in row['test_assigned'], f"Load {i} should be 'Daily < 10', got: {row['test_assigned']}"

        # 3. Test Bins ALWAYS sampled
        print("\n--- Testing Bins always sampled regardless of job type or schedule ---")
        load_num = "L-LOW-BIN"
        res = client.post('/submit_truck', data={
            'load_number': load_num,
            'profile_number': 'TEST-WAP-LOW',
            'manifest_number': "M-LOW-BIN",
            'gross_weight': '80000',
            'shipping_mode': 'Solid',
            'container_type': 'Bin',
            'date_received': '2026-05-28'
        })
        assert res.status_code == 302
        with closing(get_db_connection()) as conn:
            row = conn.execute("SELECT test_assigned FROM truck_logs WHERE load_number = ?", (load_num,)).fetchone()
            print(f"Bin Load: test_assigned = {row['test_assigned']}")
            assert "Bin" in row['test_assigned'], f"Bin load should be sampled as Bin, got: {row['test_assigned']}"

        # 4. Test Liquid / Pneumatic ALWAYS sampled
        print("\n--- Testing Liquid and Pneumatic always sampled ---")
        for mode in ['Liquid', 'Pneumatic']:
            load_num = f"L-LOW-MODE-{mode}"
            res = client.post('/submit_truck', data={
                'load_number': load_num,
                'profile_number': 'TEST-WAP-LOW',
                'manifest_number': f"M-LOW-MODE-{mode}",
                'gross_weight': '80000',
                'shipping_mode': mode,
                'container_type': 'End Dump',
                'date_received': '2026-05-28'
            })
            assert res.status_code == 302
            with closing(get_db_connection()) as conn:
                row = conn.execute("SELECT test_assigned FROM truck_logs WHERE load_number = ?", (load_num,)).fetchone()
                print(f"Shipping Mode {mode}: test_assigned = {row['test_assigned']}")
                assert row['test_assigned'] in ['FINGERPRINT', 'LAS'], f"Mode {mode} should be sampled, got: {row['test_assigned']}"

        # 5. Test Large Bulk Volumes (10+ Loads Job on 2026-05-30) - 20% random sampling
        # We also verify that "First 10" rule is NOT applied (so even the first 10 loads are subject to random 20% rule).
        print("\n--- Testing Large Bulk (10+ Job) 20% random sampling (First 10 overall NOT applied) ---")
        sampled_count = 0
        total_large_bulk = 50
        for i in range(1, total_large_bulk + 1):
            load_num = f"L-LARGE-{i}"
            res = client.post('/submit_truck', data={
                'load_number': load_num,
                'profile_number': 'TEST-WAP-LARGE',
                'manifest_number': f"M-LARGE-{i}",
                'gross_weight': '80000',
                'shipping_mode': 'Solid',
                'container_type': 'End Dump',
                'date_received': '2026-05-30' # Scheduled day
            })
            assert res.status_code == 302
            with closing(get_db_connection()) as conn:
                row = conn.execute("SELECT test_assigned FROM truck_logs WHERE load_number = ?", (load_num,)).fetchone()
                assert "First 10" not in row['test_assigned'], f"Load {i} should NOT be 'First 10' for Large Bulk, got: {row['test_assigned']}"
                
                if "Random 20%" in row['test_assigned']:
                    sampled_count += 1
                else:
                    assert row['test_assigned'] == 'VISUAL', f"Large bulk load should be VISUAL, got: {row['test_assigned']}"

        pct = (sampled_count / total_large_bulk) * 100
        print(f"Large Bulk Stats: Sampled {sampled_count}/{total_large_bulk} ({pct:.1f}%)")
        assert sampled_count > 0, "No large bulk loads were sampled. Check random logic."
        assert sampled_count < total_large_bulk, "All large bulk loads were sampled. Check random logic."

        # 6. Test High VOC Override Rule on TEST-WAP-HIGH
        print("\n--- Testing High VOC Override (>50% VOC on every 10th load) ---")
        for i in range(1, 12):
            load_num = f"L-HIGH-{i}"
            res = client.post('/submit_truck', data={
                'load_number': load_num,
                'profile_number': 'TEST-WAP-HIGH',
                'manifest_number': f"M-HIGH-{i}",
                'gross_weight': '80000',
                'shipping_mode': 'Solid',
                'container_type': 'End Dump',
                'date_received': '2026-05-28'
            })
            assert res.status_code == 302
            
            with closing(get_db_connection()) as conn:
                row = conn.execute("SELECT test_assigned FROM truck_logs WHERE load_number = ?", (load_num,)).fetchone()
                print(f"High VOC Load {i}: test_assigned = {row['test_assigned']}")
                if i == 10:
                    assert "VOC TEST" in row['test_assigned'], f"Load 10 (10th load) must have VOC TEST, got: {row['test_assigned']}"
                else:
                    assert "VOC TEST" not in row['test_assigned'], f"Load {i} should not have VOC TEST, got: {row['test_assigned']}"

        print("\n=== All WAP Sampling Logic Tests (Updated Rules) PASSED Successfully! ===")

    finally:
        # Cleanup
        print("\nCleaning up test records...")
        with closing(get_db_connection()) as conn:
            conn.execute("DELETE FROM profiles WHERE profile_number IN ('TEST-WAP-LOW', 'TEST-WAP-HIGH', 'TEST-WAP-LARGE')")
            conn.execute("DELETE FROM truck_logs WHERE profile_number IN ('TEST-WAP-LOW', 'TEST-WAP-HIGH', 'TEST-WAP-LARGE')")
            conn.execute("DELETE FROM daily_schedule WHERE profile_number IN ('TEST-WAP-LOW', 'TEST-WAP-HIGH', 'TEST-WAP-LARGE')")
            conn.commit()
        print("Cleanup done.")

if __name__ == '__main__':
    run_wap_tests()
