@echo off
REM Lance le serveur Flask et le relance si plantage.
REM Appele par Task Scheduler au demarrage du systeme (via run_hidden.vbs).

cd /d "%~dp0"
if not exist data mkdir data

:loop
echo [%date% %time%] Demarrage de app.py... >> data\autostart.log
call venv\Scripts\activate.bat
python app.py >> data\autostart.log 2>&1
echo [%date% %time%] app.py s'est arrete (code %errorlevel%). Relance dans 10 s... >> data\autostart.log
timeout /t 10 /nobreak > nul
goto loop
