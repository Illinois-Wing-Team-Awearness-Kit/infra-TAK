import shutil
import os
import zipfile
from datetime import datetime, timedelta

# Define source and base destination directories
source_file = '/var/www/html/cot-logged.txt'
base_destination_dir = '/var/www/html/cot-messages-logged/'

# ── Archive today's snapshot ──────────────────────────────────────────────────
current_utc_time = datetime.utcnow()
date_folder = current_utc_time.strftime('%d-%B-%Y')
formatted_time = current_utc_time.strftime('%H-%M-%S')   # colons are invalid in zip filenames

# Create destination directory path for the current day
destination_dir = os.path.join(base_destination_dir, date_folder)
os.makedirs(destination_dir, exist_ok=True)

# Zip the source file directly into the day's archive folder
zip_filename = f'cot-messages-pulled-{formatted_time}.zip'
zip_path = os.path.join(destination_dir, zip_filename)

with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
    zf.write(source_file, arcname=f'cot-messages-pulled-{formatted_time}.txt')

print(f'File archived to {zip_path}')

# ── Purge folders older than two days ────────────────────────────────────────
cutoff = current_utc_time - timedelta(days=2)

if os.path.isdir(base_destination_dir):
    for entry in os.listdir(base_destination_dir):
        folder_path = os.path.join(base_destination_dir, entry)
        if not os.path.isdir(folder_path):
            continue
        # Try to parse the folder name back to a date (DD-MonthName-YYYY)
        try:
            folder_date = datetime.strptime(entry, '%d-%B-%Y')
        except ValueError:
            continue  # skip folders that don't match the naming convention
        if folder_date < cutoff:
            shutil.rmtree(folder_path)
            print(f'Purged old archive folder: {folder_path}')
