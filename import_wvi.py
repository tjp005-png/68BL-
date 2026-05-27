import sqlite3
import pandas as pd
import os

def import_wvi_to_db(csv_path, db_path):
    print(f"Reading consolidated WVI data from: {csv_path}")
    if not os.path.exists(csv_path):
        print(f"[ERROR] CSV file not found at {csv_path}")
        return
        
    try:
        # Read the CSV file
        df = pd.read_csv(csv_path, low_memory=False)
    except Exception as e:
        print(f"[ERROR] Error reading WVI CSV file: {e}")
        return
        
    print(f"Cleaning columns and records...")
    # Normalize column names for SQL (lowercase, replace spaces/symbols with underscores)
    df.columns = [
        str(col).strip()
               .replace(' ', '_')
               .replace('.', '')
               .replace('#', 'num')
               .replace(':', '')
               .replace('/', '_')
               .lower()
        for col in df.columns
    ]
    
    # Map source_profile to profile if present, dropping duplicate 'profile' column if it exists
    if 'source_profile' in df.columns:
        if 'profile' in df.columns:
            df = df.drop(columns=['profile'])
        df = df.rename(columns={'source_profile': 'profile'})
    elif 'profile_:' in df.columns:
        if 'profile' in df.columns:
            df = df.drop(columns=['profile'])
        df = df.rename(columns={'profile_:': 'profile'})
    
    # Verify the profile column exists
    if 'profile' not in df.columns:
        print("[ERROR] 'profile' column not found in WVI data headers.")
        print(f"Available headers: {list(df.columns)}")
        return
        
    # Clean profile values: uppercase, strip spaces
    df['profile'] = df['profile'].astype(str).str.strip().str.upper()
    # Strip trailing .0 if present (e.g. from numerical profile float conversion)
    df['profile'] = df['profile'].apply(lambda x: x[:-2] if x.endswith('.0') else x)
    
    # Drop rows without profile number
    df = df.dropna(subset=['profile'])
    df = df[df['profile'] != '']
    df = df[df['profile'] != 'NAN']
    
    # Drop duplicates by profile, keeping the last record
    original_len = len(df)
    df = df.drop_duplicates(subset=['profile'], keep='last')
    print(f"Loaded {len(df)} unique profile rows (removed {original_len - len(df)} duplicate records).")
    
    # Replace NaN values with None so they become NULL in SQLite
    df = df.where(pd.notnull(df), None)
    
    print(f"Connecting to database at {db_path}...")
    conn = sqlite3.connect(db_path, timeout=15)
    cursor = conn.cursor()
    
    # Create the profile_wvi table
    print("Dropping existing profile_wvi table if it exists...")
    cursor.execute("DROP TABLE IF EXISTS profile_wvi")
    print("Creating profile_wvi table...")
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
    
    # Map exact column order for database insertion
    cols_order = [
        'profile', 'filename', 'generator_name', 'waste_name', 'physical_description',
        'ldr', 'state_waste_codes', 'federal_waste_codes', 'dot_description',
        'handling_instruction', 'sample_procedures', 'verification_procedures',
        'ph_min', 'ph_max', 'sulfides', 'cyanide', 'free_liquids', 'flashpoint',
        'unloading_instructions', 'reactivity_codes', 'approved_date',
        'expiration_date', 'lab_num', 'voc_ppm', 'treatment_information',
        'notes_revisions'
    ]
    
    # Pad columns in dataframe if any are missing
    for col in cols_order:
        if col not in df.columns:
            df[col] = None
            
    # Extract records
    records = [tuple(row[col] for col in cols_order) for _, row in df.iterrows()]
    
    print(f"Importing {len(records)} WVI records into database...")
    try:
        placeholders = ', '.join(['?'] * len(cols_order))
        cols_str = ', '.join(cols_order)
        cursor.executemany(f'''
            INSERT OR REPLACE INTO profile_wvi ({cols_str})
            VALUES ({placeholders})
        ''', records)
        
        conn.commit()
        print("[SUCCESS] The 'profile_wvi' table has been populated successfully.")
    except Exception as e:
        print(f"[ERROR] Error during SQL database insertion: {e}")
        conn.rollback()
        
    conn.close()

if __name__ == '__main__':
    # Pulls directly from the consolidated CSV inside the dev folder
    csv_path = r"C:\Users\PEREIRT446445\OneDrive - cleanharbors.com\Desktop\Truck_Log_App_Dev\consolidated_wvi_data.csv"
    db_path = "database.db"
    import_wvi_to_db(csv_path, db_path)
