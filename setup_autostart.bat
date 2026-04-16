@echo off
:: Nécessite d'être lancé en tant qu'Administrateur
title Setup Autostart — Trading Bot
color 0B
cls

echo.
echo  =========================================
echo    TRADING BOT — Demarrage automatique
echo  =========================================
echo.

:: Vérifier les droits admin
net session >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Ce script doit etre execute en tant qu'Administrateur.
    echo Clic droit sur setup_autostart.bat → "Executer en tant qu'administrateur"
    pause
    exit /b 1
)

set SCRIPT_DIR=%~dp0
set TASK_NAME=TradingBotAutostart
set PYTHON_EXE=

:: Trouver Python
for /f "tokens=*" %%i in ('where python 2^>nul') do (
    set PYTHON_EXE=%%i
    goto :found_python
)
:found_python

if "%PYTHON_EXE%"=="" (
    echo [ERREUR] Python introuvable dans le PATH.
    pause
    exit /b 1
)

echo Python trouve : %PYTHON_EXE%
echo Dossier bot   : %SCRIPT_DIR%
echo.

:: Supprimer l'ancienne tâche si elle existe
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: Créer la tâche planifiée
:: Lance run_forever.py au démarrage Windows, dans une fenêtre cachée
:: Délai de 60s pour laisser le réseau démarrer
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "\"%PYTHON_EXE%\" \"%SCRIPT_DIR%src\run_forever.py\"" ^
    /sc onlogon ^
    /delay 0001:00 ^
    /rl HIGHEST ^
    /f >nul

if errorlevel 1 (
    echo [ERREUR] Impossible de creer la tache planifiee.
    pause
    exit /b 1
)

echo [OK] Tache planifiee creee : "%TASK_NAME%"
echo      Le bot demarrera automatiquement 60s apres chaque connexion Windows.
echo.
echo  Pour verifier : Gestionnaire des taches → Taches planifiees
echo  Pour supprimer : schtasks /delete /tn "%TASK_NAME%" /f
echo.

:: Proposer de démarrer maintenant
set /p START_NOW="Demarrer le bot maintenant ? (O/N) : "
if /i "%START_NOW%"=="O" (
    echo Demarrage...
    start "" "%SCRIPT_DIR%start_all.bat"
)

echo.
echo  Setup termine. Le bot tournera desormais en autonome.
pause
