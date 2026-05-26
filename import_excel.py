import sqlite3
import pandas as pd
import numpy as np

# 1. Point directly to your live OneDrive Excel file
excel_path = r"C:\Users\PEREIRT446445\OneDrive - cleanharbors.com\O365 Facilities Schedule - BL - WAP\MASTERPROFILE.xlsx"
print(f"Reading data from {excel_path}...")

try:
    df = pd.read_excel(excel_path, sheet_name='Data')
except Exception as e:
    print(f"❌ Error reading the Excel file: {e}")
    exit()

print("Cleaning data... (Vectorized speed)")

# 2. Drop completely empty rows and strip invisible spaces from Profile Numbers
df = df.dropna(subset=['Profile #'])
df['Profile #'] = df['Profile #'].astype(str).str.strip()
df = df[df['Profile #'] != '']
df = df[df['Profile #'].str.lower() != 'nan']

# 3. FIX #1: DROP DUPLICATES. If a profile appears twice, keep the last (most recent) one.
original_count = len(df)
df = df.drop_duplicates(subset=['Profile #'], keep='last')
if len(df) < original_count:
    print(f"⚠️ Removed {original_count - len(df)} duplicate profiles from Excel.")

# 4. FIX #2: BULLETPROOF VOC EXTRACTION
if 'VOC #' in df.columns:
    df['VOC #'] = df['VOC #'].astype(str).str.strip()
    
    # REGEX MAGIC: This extracts ONLY numbers. If someone typed "<52" or "52 ppm", it grabs exactly "52"
    # Note: If the field is '?', this regex will fail to find a number and return NaN
    df['VOC #'] = df['VOC #'].str.extract(r'(\d+\.?\d*)')[0]
    
    # Convert to numbers. Any '?' or blanks safely become NaN
    df['VOC #'] = pd.to_numeric(df['VOC #'], errors='coerce')
    
    # THE FIX: Replace NaNs with None (so SQLite stores a NULL), DO NOT fill with 0.0!
    df['VOC #'] = df['VOC #'].replace({np.nan: None})
else:
    df['VOC #'] = None

# 5. Clean Dates quickly
if 'EXP DATE' in df.columns:
    df['EXP DATE'] = pd.to_datetime(df['EXP DATE'], errors='coerce').dt.strftime('%Y-%m-%d')
    df['EXP DATE'] = df['EXP DATE'].fillna('No Date')

# 6. Clean Status
if 'STATUS' in df.columns:
    df['STATUS'] = df['STATUS'].fillna('ACTIVE').astype(str).str.strip()
    df.loc[df['STATUS'].isin(['nan', '', 'NaN']), 'STATUS'] = 'ACTIVE'

# 7. Fill any remaining NaNs in text columns with empty strings
df = df.fillna('')

print("Connecting to database...")

# 8. Use timeout=15 so it doesn't crash if the weighmaster is using the app!
conn = sqlite3.connect('database.db', timeout=15)
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS profiles (
        profile_number TEXT PRIMARY KEY,
        generator TEXT,
        status TEXT,
        expiration_date TEXT,
        waste_name TEXT,
        voc_percentage REAL,
        haz TEXT,
        rcra TEXT,
        win_code TEXT,
        lab_number TEXT,
        comments TEXT
    )
''')

# Combine all the columns into a list of tuples for the database
cleaned_data = list(zip(
    df['Profile #'],
    df.get('GENERATOR', pd.Series([''] * len(df))),
    df.get('STATUS', pd.Series(['ACTIVE'] * len(df))),
    df['EXP DATE'],
    df.get('WASTE NAME', pd.Series([''] * len(df))),
    df['VOC #'],
    df.get('HAZ', pd.Series([''] * len(df))),
    df.get('RCRA', pd.Series([''] * len(df))),
    df.get('WIN CODE', pd.Series([''] * len(df))),
    df.get('LAB #', pd.Series([''] * len(df))),
    df.get('COMMENTS', pd.Series([''] * len(df)))
))

print(f"Importing {len(cleaned_data)} profiles to database...")

# 9. Insert the entire spreadsheet in a single fraction-of-a-second transaction
try:
    cursor.executemany('''
        INSERT OR REPLACE INTO profiles (
            profile_number, generator, status, expiration_date, 
            waste_name, voc_percentage, haz, rcra, win_code, 
            lab_number, comments
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', cleaned_data)
    
    conn.commit()
    print("\n--- IMPORT COMPLETE ---")
    print(f"✅ Successfully staged and imported {len(cleaned_data)} profiles in record time!")
except Exception as e:
    print(f"\n❌ Critical Database Error during insert: {e}")

conn.close()