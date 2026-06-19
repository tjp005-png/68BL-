import sqlite3

print("Preparing to scrub all operational logs and queues...")

# Connect to the database
conn = sqlite3.connect('database.db')
cursor = conn.cursor()

try:
    # 1. Define the operational tables to wipe (DO NOT include 'profiles', 'profile_wvi', 'profile_attachments', or 'waste_acceptance_log')
    tables_to_wipe = [
        'truck_logs', 
        'daily_schedule', 
        'drum_inventory', 
        'drum_lab_queue', 
        'compliance_tracker', 
        'audit_log', 
        'put_pile_retreats'
    ]
    
    for table in tables_to_wipe:
        print(f"Wiping {table}...")
        
        # 2. Delete all records from the table
        cursor.execute(f'DELETE FROM {table}')
        
        # 3. Reset the internal counter so the next entry starts back at ID #1
        cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}'")
        
    conn.commit()
    print("\nSUCCESS: All operational logs, schedules, drum logs, and tracking counters have been completely wiped!")
    print("Your master profiles, WVI database, attachments, and waste acceptance log are still safely stored.")
    
except Exception as e:
    print(f"\nError resetting database: {e}")

finally:
    conn.close()