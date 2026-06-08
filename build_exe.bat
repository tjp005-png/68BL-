@echo off
echo Building Truck Log App Executable...
python -m PyInstaller --noconfirm --onedir --name "Truck_Log_App" --icon="icon.ico" --add-data "templates;templates" --add-data "static;static" --hidden-import "openpyxl" --hidden-import "pandas" --hidden-import "engineio.async_drivers.threading"  app.py
echo Build Complete! Check the 'dist' folder for your .exe
pause
