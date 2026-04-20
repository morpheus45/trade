@echo off
chcp 65001 >nul 2>&1
title Trading Bot IA - Installateur Windows

echo.
echo ====================================================
echo    TRADING BOT v2 - Installateur Windows
echo    Bot IA autonome H24 - Crypto
echo ====================================================
echo.

REM ====================================================
REM  CONFIGURATION - Remplir vos cles avant de lancer
REM  (Ce fichier reste LOCAL, ne pas pousser sur GitHub)
REM ====================================================
set ANTHROPIC_API_KEY=YOUR_ANTHROPIC_API_KEY_HERE
set BINANCE_API_KEY=YOUR_BINANCE_API_KEY_HERE
set BINANCE_API_SECRET=YOUR_BINANCE_API_SECRET_HERE
set TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN_HERE
set TELEGRAM_CHAT_ID=YOUR_TELEGRAM_CHAT_ID_HERE

REM Paper Trading : true=simulation sans risque / false=live argent reel
set PAPER_TRADING=false

REM Capital initial en USDT
set CAPITAL=1000

REM ====================================================

set INSTALL_DIR=%USERPROFILE%\trading-bot
set REPO=https://github.com/morpheus45/trade.git
set PYTHON=%INSTALL_DIR%\venv\Scripts\python.exe
set PIP=%INSTALL_DIR%\venv\Scripts\pip.exe

echo [->] Repertoire : %INSTALL_DIR%
echo.

REM -- 1/8 : Verifier Python --
echo [1/8] Verification Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python non trouve - telechargement Python 3.11...
    set PY_INSTALLER=%TEMP%\python-3.11.9-amd64.exe
    powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%TEMP%\python-3.11.9-amd64.exe'" 2>nul
    if exist "%TEMP%\python-3.11.9-amd64.exe" (
        echo [->] Installation Python 3.11...
        "%TEMP%\python-3.11.9-amd64.exe" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
        del "%TEMP%\python-3.11.9-amd64.exe" >nul 2>&1
        echo [OK] Python 3.11 installe
    ) else (
        echo [ERR] Echec telechargement Python.
        echo       Installez Python 3.11 depuis https://python.org puis relancez.
        pause
        exit /b 1
    )
) else (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo [OK] Python %%v detecte
)

REM -- 2/8 : Verifier Git --
echo [2/8] Verification Git...
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Git non trouve - telechargement Git...
    powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe' -OutFile '%TEMP%\git-installer.exe'" 2>nul
    if exist "%TEMP%\git-installer.exe" (
        echo [->] Installation Git...
        "%TEMP%\git-installer.exe" /VERYSILENT /NORESTART /NOCANCEL /SP-
        del "%TEMP%\git-installer.exe" >nul 2>&1
        set PATH=%PATH%;C:\Program Files\Git\bin
        echo [OK] Git installe
    ) else (
        echo [ERR] Echec telechargement Git.
        echo       Installez Git depuis https://git-scm.com puis relancez.
        pause
        exit /b 1
    )
) else (
    for /f "tokens=3" %%v in ('git --version') do echo [OK] Git %%v detecte
)

REM -- 3/8 : Cloner ou mettre a jour le repo --
echo [3/8] Code source depuis GitHub...
if exist "%INSTALL_DIR%\.git" (
    echo [->] Mise a jour...
    git -C "%INSTALL_DIR%" pull -q
    echo [OK] Code mis a jour
) else (
    echo [->] Clonage depuis GitHub...
    git clone -q "%REPO%" "%INSTALL_DIR%"
    if %errorlevel% neq 0 (
        echo [ERR] Echec du clonage. Verifiez votre connexion internet.
        pause
        exit /b 1
    )
    echo [OK] Code telecharge
)
git config -C "%INSTALL_DIR%" user.email "tradingbot@auto.local" >nul 2>&1
git config -C "%INSTALL_DIR%" user.name "TradingBot" >nul 2>&1
echo [OK] Git config utilisateur configure

REM -- 4/8 : Environnement virtuel Python --
echo [4/8] Environnement Python...
if not exist "%PYTHON%" (
    echo [->] Creation du venv...
    python -m venv "%INSTALL_DIR%\venv"
)
echo [->] Installation des dependances ^(peut prendre 2-3 min^)...
"%PYTHON%" -m pip install --upgrade pip --quiet 2>nul
"%PYTHON%" -m pip install -r "%INSTALL_DIR%\requirements.txt" --quiet
if %errorlevel% neq 0 (
    echo [!] Erreur pip - nouvelle tentative sans --quiet...
    "%PYTHON%" -m pip install -r "%INSTALL_DIR%\requirements.txt"
)
echo [OK] Dependances installees

REM -- 5/8 : Dossiers --
echo [5/8] Creation des dossiers...
if not exist "%INSTALL_DIR%\logs"   mkdir "%INSTALL_DIR%\logs"
if not exist "%INSTALL_DIR%\models" mkdir "%INSTALL_DIR%\models"
if not exist "%INSTALL_DIR%\data"   mkdir "%INSTALL_DIR%\data"
echo %CAPITAL%> "%INSTALL_DIR%\initial_capital.txt"
echo [OK] Dossiers crees

REM -- 6/8 : Fichier .env --
echo [6/8] Configuration .env...
if exist "%INSTALL_DIR%\src\.env" (
    echo [OK] .env deja present -- conserve tel quel ^(cles API preservees^)
) else (
    echo [->] Creation .env depuis le modele...
    copy "%INSTALL_DIR%\src\.env.example" "%INSTALL_DIR%\src\.env" >nul
    echo [!] IMPORTANT: ouvre %INSTALL_DIR%\src\.env et remplis tes vraies cles API
    notepad "%INSTALL_DIR%\src\.env"
)

REM -- 7/8 : Entrainement ML en arriere-plan --
echo [7/8] Modele ML...
if not exist "%INSTALL_DIR%\models\xgboost_model.json" (
    echo [->] Entrainement ML en arriere-plan ^(5-10 min, ne bloque pas^)...
    start "ML Training" /MIN cmd /c ""%PYTHON%" "%INSTALL_DIR%\src\train_xgboost.py" >> "%INSTALL_DIR%\logs\ml_train.log" 2>&1"
    echo [OK] ML lance en fond
) else (
    echo [OK] Modele ML deja present
)

REM -- 8/8 : Demarrage automatique --
echo [8/8] Demarrage automatique au login...
schtasks /delete /tn "TradingBot" /f >nul 2>&1
schtasks /create /tn "TradingBot" /sc ONLOGON /rl HIGHEST /f ^
  /tr "cmd /c cd /d \"%INSTALL_DIR%\" ^&^& \"%PYTHON%\" src\run_forever.py >> logs\bot.log 2>^&1" >nul 2>&1
echo [OK] Task Scheduler configure

REM -- Raccourci bureau --
powershell -NoProfile -Command "$ws=New-Object -ComObject WScript.Shell; $sc=$ws.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Trading Bot.lnk'); $sc.TargetPath='%PYTHON%'; $sc.Arguments='src\run_forever.py'; $sc.WorkingDirectory='%INSTALL_DIR%'; $sc.Description='Trading Bot IA Crypto'; $sc.Save()" >nul 2>&1
echo [OK] Raccourci bureau cree

REM -- Lancement immediat --
echo.
echo [->] Lancement du bot...
start "Trading Bot" /MIN cmd /c "cd /d "%INSTALL_DIR%" && "%PYTHON%" src\run_forever.py >> logs\bot.log 2>&1"

REM -- Ouverture dashboard --
timeout /t 4 /nobreak >nul
start "" "https://morpheus45.github.io/trade/"

echo.
echo ====================================================
echo    INSTALLATION TERMINEE
echo ====================================================
echo.
echo  Mode      : PAPER_TRADING=%PAPER_TRADING%
echo  Bot       : en cours de demarrage
echo  Dashboard : https://morpheus45.github.io/trade/
echo  Local     : http://localhost:5000  (dans ~30s)
echo.
echo  Raccourci "Trading Bot" cree sur le bureau.
echo  Le bot redemarrera automatiquement au prochain login.
echo.
echo  Telegram : envoie /status pour verifier l'etat
echo.
pause
