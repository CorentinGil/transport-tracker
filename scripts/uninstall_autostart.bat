@echo off
REM Desinstalle la tache planifiee + restore Windows Update par defaut.
REM CLIC-DROIT > "Executer en tant qu'administrateur"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERREUR] Lance ce script en Administrateur.
    pause
    exit /b 1
)

set "TASK_NAME=TransportTracker"

echo Suppression de la tache "%TASK_NAME%"...
schtasks /delete /tn "%TASK_NAME%" /f
echo.

echo Restauration Windows Update (auto-reboot autorise)...
reg delete "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" /v "NoAutoRebootWithLoggedOnUsers" /f >nul 2>&1
echo OK.
echo.

echo Desinstallation terminee.
pause
