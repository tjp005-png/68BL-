@echo off
echo Building Truck Log App Executable (using local temp directory to prevent OneDrive locks)...

set TEMP_BUILD_DIR=%LOCALAPPDATA%\Temp\Truck_Log_App_Build
if exist "%TEMP_BUILD_DIR%" rmdir /s /q "%TEMP_BUILD_DIR%"

python -m PyInstaller --noconfirm --onedir --name "Truck_Log_App" --icon="icon.ico" --add-data "templates;templates" --add-data "static;static" --hidden-import "openpyxl" --hidden-import "pandas" --hidden-import "engineio.async_drivers.threading" --distpath "%TEMP_BUILD_DIR%\dist" --workpath "%TEMP_BUILD_DIR%\work" app.py

if exist database.db (
    echo Copying active database.db to local dist folder...
    copy /y database.db "%TEMP_BUILD_DIR%\dist\Truck_Log_App\database.db"
)

if exist MASTERPROFILE.xlsx (
    echo Copying MASTERPROFILE.xlsx to local dist folder...
    copy /y MASTERPROFILE.xlsx "%TEMP_BUILD_DIR%\dist\Truck_Log_App\MASTERPROFILE.xlsx"
)

echo Packaging into ZIP archive in project root...
if exist "%~dp0Truck_Log_App.zip" del /f /q "%~dp0Truck_Log_App.zip"
powershell -Command "Compress-Archive -Path '%TEMP_BUILD_DIR%\dist\Truck_Log_App' -DestinationPath '%~dp0Truck_Log_App.zip' -Force"

echo Cleaning up temporary build files...
rmdir /s /q "%TEMP_BUILD_DIR%"

echo Build and Packaging Complete!
echo Created: Truck_Log_App.zip in the root directory.
pause
