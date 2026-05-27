import sqlite3

print("Preparing to clear only the STU Drum Inventory and Lab Queue logs...")

# Connect to the database
conn = sqlite3.connect('database.db')
cursor = conn.cursor()

try:
    # Define STU tables to wipe
    tables_to_wipe = ['drum_inventory', 'drum_lab_queue']
    
    for table in tables_to_wipe:
        print(f"Wiping {table}...")
        
        # Delete all records from the table
        cursor.execute(f'DELETE FROM {table}')
        
        # Reset the internal counter
        cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}'")
        
    conn.commit()
    print("\nSUCCESS: STU Drum Inventory and STU Lab Queue have been completely wiped!")
    print("Daily schedules, truck logs, and master profiles remain untouched.")
    
except Exception as e:
    print(f"\nError resetting STU tables: {e}")

finally:
    conn.close()
