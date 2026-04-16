@echo off
echo ========================================
echo    RATP Tracker - Demarrage
echo ========================================
echo.

cd /d "%~dp0"
call venv\Scripts\activate.bat
python app.py

pause
