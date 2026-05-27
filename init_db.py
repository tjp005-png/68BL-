import sqlite3

# This creates the file 'database.db' in your folder
conn = sqlite3.connect('database.db')
cursor = conn.cursor()

# 1. Create the Profiles table (Replacing cr094_MPDataTables)
cursor.execute('''
    CREATE TABLE IF NOT EXISTS profiles (
        profile_number TEXT PRIMARY KEY,
        generator TEXT,
        waste_description TEXT,
        win_code TEXT,
        voc_percentage REAL DEFAULT 0.0,
        special_handling TEXT,
        ph_range TEXT,
        physical_appearance TEXT,
        flash_point TEXT,
        expiration_date TEXT,
        status TEXT DEFAULT "A"
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
        check_in_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        lab_results TEXT,
        manifest_weight REAL,
        manifest_units TEXT,
        date_received TEXT,
        rejection_reason TEXT,
        extra_fees TEXT DEFAULT "None",
        sales_order TEXT DEFAULT "",
        time_in TEXT DEFAULT "",
        time_out TEXT DEFAULT "",
        specific_gravity REAL,
        measured_ph REAL,
        measured_flashpoint TEXT,
        measured_sulfides TEXT,
        measured_cyanide TEXT,
        measured_free_liquids TEXT,
        measured_voc REAL,
        voc_pass_fail TEXT DEFAULT "N/A"
    )
''')

# 3. Insert a dummy profile so we have something to test with in the dropdown
cursor.execute('''
    INSERT OR IGNORE INTO profiles (profile_number, status, voc_percentage, expiration_date)
    VALUES ('CH-12345', 'ACTIVE', 0.05, '2027-01-01')
''')

# 4. Create the WVI Profile Cache table
cursor.execute('''
    CREATE TABLE IF NOT EXISTS profile_wvi (
        profile TEXT PRIMARY KEY,
        filename TEXT,
        generator_name TEXT,
        waste_name TEXT,
        physical_description TEXT,
        ldr TEXT,
        state_waste_codes TEXT,
        federal_waste_codes TEXT,
        dot_description TEXT,
        handling_instruction TEXT,
        sample_procedures TEXT,
        verification_procedures TEXT,
        ph_min REAL,
        ph_max REAL,
        sulfides TEXT,
        cyanide TEXT,
        free_liquids TEXT,
        flashpoint TEXT,
        unloading_instructions TEXT,
        reactivity_codes TEXT,
        approved_date TEXT,
        expiration_date TEXT,
        lab_num TEXT,
        voc_ppm REAL,
        treatment_information TEXT,
        notes_revisions TEXT,
        is_synced INTEGER DEFAULT 0
    )
''')

conn.commit()
conn.close()

print("Database and tables created successfully!")