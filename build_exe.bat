@echo off
cd /d "%~dp0"
echo Building Truck Log App Executable (using local temp directory to prevent OneDrive locks)...

set TEMP_BUILD_DIR=%LOCALAPPDATA%\Temp\Truck_Log_App_Build
if exist "%TEMP_BUILD_DIR%" rmdir /s /q "%TEMP_BUILD_DIR%"

python -m PyInstaller --noconfirm --onedir --name "Truck_Log_App" --icon="icon.ico" --add-data "templates;templates" --add-data "static;static" --add-data "ldr_options.json;." --add-data "voc_cache.json;." --add-data "voc_profile_cache.json;." --hidden-import "openpyxl" --hidden-import "pandas" --hidden-import "engineio.async_drivers.threading" --distpath "%TEMP_BUILD_DIR%\dist" --workpath "%TEMP_BUILD_DIR%\work" app.py

if exist "%~dp0database.db" (
    echo Copying active database.db to local dist folder...
    copy /y "%~dp0database.db" "%TEMP_BUILD_DIR%\dist\Truck_Log_App\database.db"
) else if exist "%LOCALAPPDATA%\Truck_Log_App\database.db" (
    echo Copying database.db from AppData to local dist folder...
    copy /y "%LOCALAPPDATA%\Truck_Log_App\database.db" "%TEMP_BUILD_DIR%\dist\Truck_Log_App\database.db"
)

if exist "%~dp0MASTERPROFILE.xlsx" (
    echo Copying MASTERPROFILE.xlsx to local dist folder...
    copy /y "%~dp0MASTERPROFILE.xlsx" "%TEMP_BUILD_DIR%\dist\Truck_Log_App\MASTERPROFILE.xlsx"
)

echo Packaging into ZIP archive in project root...
if exist "%~dp0Truck_Log_App.zip" del /f /q "%~dp0Truck_Log_App.zip"
powershell -Command "Add-Type -Assembly 'System.IO.Compression.FileSystem'; [System.IO.Compression.ZipFile]::CreateFromDirectory('%TEMP_BUILD_DIR%\dist\Truck_Log_App', '%~dp0Truck_Log_App.zip')"
if exist "%~dp0Truck_Log_App.zip" (
    echo Cleaning up temporary build files...
    rmdir /s /q "%TEMP_BUILD_DIR%"
) else (
    echo [WARNING] ZIP creation failed. Built files preserved at: %TEMP_BUILD_DIR%\dist\Truck_Log_App
)

echo Build and Packaging Complete!
echo Created: Truck_Log_App.zip in the root directory.
pause
