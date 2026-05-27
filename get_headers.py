import pandas as pd

# 1. The exact path to your OneDrive file (the 'r' keeps the backslashes safe)
excel_path = r"C:\Users\PEREIRT446445\OneDrive - cleanharbors.com\O365 Facilities Schedule - BL - WAP\MASTERPROFILE.xlsx"

try:
    print(f"Opening {excel_path}...\n")
    
    # 2. We are going to tell Pandas to skip the first 3 rows of fluff (header=3)
    # We only read 0 rows of actual data because we only care about the headers!
    df = pd.read_excel(excel_path, header=3, nrows=0)
    
    print("🎯 EXACT HEADERS FOUND:")
    print("-" * 30)
    
    # Print each header surrounded by quotes so you can see if there are sneaky spaces
    for col in df.columns:
        print(f"'{col}'")

except Exception as e:
    print(f"Whoops, ran into an error: {e}")