@echo off
echo Building Truck Log App Executable...
python -m PyInstaller --noconfirm --onedir --name "Truck_Log_App" --icon="icon.ico" --add-data "templates;templates" --add-data "static;static" --hidden-import "openpyxl" --hidden-import "pandas" --hidden-import "engineio.async_drivers.threading"  app.py

if exist database.db (
    echo Copying active database.db to dist folder...
    copy /y database.db dist\Truck_Log_App\database.db
)

if exist MASTERPROFILE.xlsx (
    echo Copying MASTERPROFILE.xlsx to dist folder...
    copy /y MASTERPROFILE.xlsx dist\Truck_Log_App\MASTERPROFILE.xlsx
)

echo Packaging into ZIP archive...
powershell -Command "Compress-Archive -Path '%~dp0dist\Truck_Log_App' -DestinationPath '%~dp0Truck_Log_App.zip' -Force"

echo Build and Packaging Complete!
echo Created: Truck_Log_App.zip in the root directory.
pause
