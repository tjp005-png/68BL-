import sqlite3

print("Preparing to scrub all operational logs and queues...")

# Connect to the database
conn = sqlite3.connect('database.db')
cursor = conn.cursor()

try:
    # 1. Define the operational tables to wipe (DO NOT include 'profiles')
    tables_to_wipe = ['truck_logs', 'daily_schedule', 'drum_inventory']
    
    for table in tables_to_wipe:
        print(f"🧹 Wiping {table}...")
        
        # 2. Delete all records from the table
        cursor.execute(f'DELETE FROM {table}')
        
        # 3. Reset the internal counter so the next entry starts back at ID #1
        # (SQLite keeps a hidden sequence table that remembers the last ID used)
        cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}'")
        
    conn.commit()
    print("\n✅ SUCCESS: All test trucks, schedules, and STU drums have been completely wiped!")
    print("🛡️ Your 32,000+ master profiles are still safely stored in the database.")
    
except Exception as e:
    print(f"\n❌ Error resetting database: {e}")

finally:
    conn.close()