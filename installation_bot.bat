@echo off
title 🚀 Installation complète du bot de trading
echo ==========================================================
echo  🚀 INSTALLATION COMPLÈTE DU BOT DE TRADING 🚀
echo ==========================================================
echo.

:: Vérification de Python
echo 🔍 Vérification de Python...
where python >nul 2>nul
IF %ERRORLEVEL% NEQ 0 (
    echo ❌ Python n'est pas installé. Téléchargement en cours...
    powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.0/python-3.12.0-amd64.exe' -OutFile 'python_installer.exe'}"
    
    echo 🛠 Installation de Python...
    start /wait python_installer.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
    del python_installer.exe

    echo 🔄 Ajout de Python au PATH...
    setx PATH "%PATH%;C:\Program Files\Python312\;C:\Program Files\Python312\Scripts\"
)

:: Vérification après installation
where python >nul 2>nul
IF %ERRORLEVEL% NEQ 0 (
    echo ❌ Python n'a pas pu être installé automatiquement.
    echo 🔗 Télécharge-le manuellement ici : https://www.python.org/downloads/
    pause
    exit
)

:: Vérifier la version de Python
python --version

:: Création de l'environnement virtuel
echo 🔧 Création d'un environnement virtuel...
python -m venv trading_env

:: Activation de l'environnement virtuel
echo 🔄 Activation de l'environnement...
call trading_env\Scripts\activate

:: Mise à jour de pip
echo 🔄 Mise à jour de pip...
python -m pip install --upgrade pip

:: Installation des dépendances
echo 📦 Installation des dépendances requises...
pip install --upgrade setuptools wheel
pip install tensorflow xgboost joblib numpy scipy pandas requests transformers binance

:: Vérification de l'installation
echo 🔍 Vérification des installations...
python -c "import tensorflow as tf; print('✅ TensorFlow:', tf.__version__)"
python -c "import xgboost as xgb; print('✅ XGBoost fonctionne !')"
python -c "import transformers; print('✅ Transformers installé !')"

:: Configuration du bot
echo 🔧 Configuration du bot...
cd %~dp0
if not exist "models" mkdir models
if not exist "logs" mkdir logs

:: Lancement du bot après installation
echo 🚀 Lancement du bot de trading...
python src\bot_trading.py

echo ✅ Installation terminée avec succès !
pause
exit
