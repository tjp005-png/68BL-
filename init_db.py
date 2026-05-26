import sqlite3

# This creates the file 'database.db' in your folder
conn = sqlite3.connect('database.db')
cursor = conn.cursor()

# 1. Create the Profiles table (Replacing cr094_MPDataTables)
cursor.execute('''
    CREATE TABLE IF NOT EXISTS profiles (
        profile_number TEXT PRIMARY KEY,
        status TEXT,
        voc_percentage REAL,
        expiration_date DATE
    )
''')

# 2. Create the Truck Logs table (Replacing TR_Logs)
cursor.execute('''
    CREATE TABLE IF NOT EXISTS truck_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        truck_id TEXT,
        profile_number TEXT,
        load_number TEXT,
        manifest_number TEXT,
        gross_weight REAL,
        exit_weight REAL,
        net_weight REAL,
        test_assigned TEXT,
        test_status TEXT,
        cell_location TEXT,
        grid_location TEXT,
        check_in_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )
''')

# 3. Insert a dummy profile so we have something to test with in the dropdown
cursor.execute('''
    INSERT OR IGNORE INTO profiles (profile_number, status, voc_percentage, expiration_date)
    VALUES ('CH-12345', 'ACTIVE', 0.05, '2027-01-01')
''')

conn.commit()
conn.close()

print("Database and tables created successfully!")