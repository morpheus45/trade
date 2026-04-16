@echo off
echo ===============================
echo   DEMARRAGE DU BOT DE TRADING  
echo ===============================
echo.

:: Vérifier que Python est installé
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERREUR : Python n'est pas installé ou non ajouté au PATH.
    pause
    exit
)

:: Aller dans le bon dossier
cd /d "%~dp0"
cd ../src

:: Vérifier que toutes les dépendances sont installées
echo Verification des dependances...
pip install --quiet --no-cache-dir --upgrade -r ../requirements.txt

:: Désactiver l'avertissement de TensorFlow (optionnel)
set TF_ENABLE_ONEDNN_OPTS=0

:: Lancer le bot de trading
echo Lancement du bot...
python bot_trading.py

:: Pause pour garder la console ouverte si une erreur survient
echo.
echo Si le bot s'est arrêté, vérifiez les erreurs ci-dessus.
pause
