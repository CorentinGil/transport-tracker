@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "LOG=%~dp0install.log"
echo. > "%LOG%"
echo [%date% %time%] Demarrage de install_autostart.bat >> "%LOG%"

REM ========================================================
REM Installation du demarrage automatique de Transport Tracker
REM CLIC-DROIT > "Executer en tant qu'administrateur"
REM ========================================================

echo.
echo === Transport Tracker - Installation du demarrage auto ===
echo.

REM --- Verifier admin ---
net session >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Lance ce script en tant qu'Administrateur.
    echo Clic-droit ^> Executer en tant qu'administrateur.
    goto :end
)
echo [OK] Privileges admin confirmes.
echo [%date% %time%] Admin OK >> "%LOG%"

REM Script vit dans scripts/ ; le projet est le dossier parent
pushd "%~dp0\.."
set "PROJECT_DIR=%CD%"
popd
set "TASK_NAME=TransportTracker"
set "BAT_PATH=%PROJECT_DIR%\autostart.bat"

echo [INFO] Repertoire projet : %PROJECT_DIR%
echo [%date% %time%] PROJECT_DIR=%PROJECT_DIR% >> "%LOG%"

REM --- Verifier les fichiers ---
if not exist "%BAT_PATH%" (
    echo [ERREUR] autostart.bat introuvable.
    goto :end
)
if not exist "%PROJECT_DIR%\venv\Scripts\python.exe" (
    echo [ERREUR] venv\Scripts\python.exe introuvable.
    echo Cree le venv d'abord avec :
    echo   python -m venv venv
    echo   venv\Scripts\activate.bat
    echo   pip install -r requirements.txt
    goto :end
)
echo [OK] Fichiers verifies.

REM --- Supprimer ancienne tache si existe ---
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Tache existante trouvee, suppression...
    schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
)

REM --- Creer la tache (au boot, en SYSTEM) ---
echo [INFO] Creation de la tache "%TASK_NAME%"...
echo [%date% %time%] Creation tache... >> "%LOG%"
schtasks /create /tn "%TASK_NAME%" /tr "\"%BAT_PATH%\"" /sc onstart /ru SYSTEM /rl highest /f >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERREUR] Echec creation tache. Voir %LOG%
    goto :end
)
echo [OK] Tache planifiee creee.

REM --- Empecher Windows Update de redemarrer pendant session ouverte ---
echo [INFO] Configuration Windows Update (no auto-reboot)...
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" /v "NoAutoRebootWithLoggedOnUsers" /t REG_DWORD /d 1 /f >nul 2>&1
if errorlevel 1 (
    echo [WARN] Echec modif registre (non-bloquant).
) else (
    echo [OK] Windows Update ne forcera plus le reboot tant que ta session est ouverte.
)

echo.
echo ============================================
echo  Installation reussie !
echo ============================================
echo.
echo Pour lancer maintenant (sans reboot) :
echo   schtasks /run /tn "%TASK_NAME%"
echo.
echo Verifier le statut :
echo   schtasks /query /tn "%TASK_NAME%"
echo.
echo Logs : %PROJECT_DIR%\data\autostart.log
echo Log d'install : %LOG%
echo.

:end
echo.
echo --- Appuie sur une touche pour fermer ---
pause >nul
endlocal
