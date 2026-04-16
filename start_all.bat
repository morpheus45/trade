@echo off
title Trading Bot — Lanceur
color 0A
cls

echo.
echo  =========================================
echo    TRADING BOT v2 — Demarrage
echo  =========================================
echo.

:: Aller dans le dossier du script
cd /d "%~dp0"

:: Vérifier que Python est installé
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Python introuvable. Installe Python 3.11+
    pause
    exit /b 1
)

:: Vérifier que le .env existe
if not exist "src\.env" (
    echo [ERREUR] Fichier src\.env introuvable.
    echo Copie src\.env.example vers src\.env et configure tes cles API.
    pause
    exit /b 1
)

:: Créer les dossiers nécessaires
if not exist "logs"   mkdir logs
if not exist "models" mkdir models
if not exist "data"   mkdir data

:: Installer les dépendances si nécessaire
echo [1/3] Verification des dependances...
pip install -r requirements.txt -q --no-warn-script-location
echo       OK

:: Vérifier si le modèle ML existe
if not exist "models\xgboost_model.json" (
    echo.
    echo [2/3] Modele ML non trouve.
    echo       Lancement de l'entrainement ^(~5-10 minutes^)...
    echo       Les donnees sont telechargees depuis Binance.
    echo.
    python src\train_xgboost.py
    if errorlevel 1 (
        echo [AVERTISSEMENT] Entrainement echoue. Le bot fonctionnera sans ML.
    ) else (
        echo       Modele entraine avec succes.
    )
) else (
    echo [2/3] Modele ML trouve. OK
)

:: Afficher l'IP locale pour le dashboard Android
echo.
echo [3/3] Demarrage du bot et du dashboard...
echo.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| find "IPv4"') do (
    set LOCAL_IP=%%a
    goto :found_ip
)
:found_ip
set LOCAL_IP=%LOCAL_IP: =%
echo  Dashboard Android : http://%LOCAL_IP%:5000
echo  Dashboard local   : http://127.0.0.1:5000
echo.
echo  Telegram : envoie /status au bot pour verifier
echo.
echo  Pour arreter : ferme cette fenetre ou Ctrl+C
echo  =========================================
echo.

:: Lancer le watchdog (qui lance bot + dashboard en sous-processus)
python src\run_forever.py

pause
