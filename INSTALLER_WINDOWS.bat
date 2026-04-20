@echo off
chcp 65001 >nul 2>&1
title Trading Bot IA - Installateur Windows

echo.
echo ====================================================
echo    TRADING BOT v2 - Installateur Windows
echo    Bot IA autonome H24 - Crypto EUR
echo ====================================================
echo.
echo  IMPORTANT : Tes cles API doivent etre saisies dans
echo  le fichier  src\.env  APRES l'installation.
echo  Ce fichier ne contient JAMAIS tes cles.
echo.

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
echo [OK] Dossiers crees

REM -- 6/8 : Fichier .env (cles API) --
echo [6/8] Configuration des cles API...
if exist "%INSTALL_DIR%\src\.env" (
    echo [OK] .env deja present -- cles API preservees
) else (
    echo [->] Creation du fichier de configuration...
    copy "%INSTALL_DIR%\src\.env.example" "%INSTALL_DIR%\src\.env" >nul
    echo.
    echo  ============================================================
    echo   ETAPE OBLIGATOIRE : Remplis tes cles API dans le fichier
    echo   qui va s'ouvrir maintenant.
    echo.
    echo   Cles a remplir :
    echo   - BINANCE_API_KEY     ^(https://binance.com ^> API Management^)
    echo   - BINANCE_API_SECRET
    echo   - GROQ_API_KEY        ^(https://console.groq.com/keys^)
    echo   - TELEGRAM_BOT_TOKEN  ^(https://t.me/BotFather^)
    echo   - TELEGRAM_CHAT_ID    ^(envoie /start a ton bot, recupere l'ID^)
    echo   - PAPER_TRADING       false = live reel / true = simulation
    echo  ============================================================
    echo.
    pause
    notepad "%INSTALL_DIR%\src\.env"
    echo [OK] Configuration .env ouverte - ferme le notepad quand c'est fait
    pause
)

REM -- 7/8 : Entrainement ML en arriere-plan --
echo [7/8] Modele ML...
if not exist "%INSTALL_DIR%\models\xgboost_model.json" (
    echo [->] Entrainement ML en arriere-plan ^(5-10 min, ne bloque pas le bot^)...
    start "ML Training" /MIN cmd /c ""%PYTHON%" "%INSTALL_DIR%\src\train_xgboost.py" >> "%INSTALL_DIR%\logs\ml_train.log" 2>&1"
    echo [OK] ML lance en fond ^(surveille logs\ml_train.log^)
) else (
    echo [OK] Modele ML deja present
)

REM -- 8/8 : Demarrage automatique au login --
echo [8/8] Demarrage automatique au login...
schtasks /delete /tn "TradingBot" /f >nul 2>&1
schtasks /create /tn "TradingBot" /sc ONLOGON /rl HIGHEST /f ^
  /tr "cmd /c cd /d \"%INSTALL_DIR%\" ^&^& \"%PYTHON%\" src\run_forever.py >> logs\bot.log 2>^&1" >nul 2>&1
echo [OK] Task Scheduler configure

REM -- Raccourci bureau --
powershell -NoProfile -Command "$ws=New-Object -ComObject WScript.Shell; $sc=$ws.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Trading Bot.lnk'); $sc.TargetPath='%PYTHON%'; $sc.Arguments='src\run_forever.py'; $sc.WorkingDirectory='%INSTALL_DIR%'; $sc.Description='Trading Bot IA Crypto EUR'; $sc.Save()" >nul 2>&1
echo [OK] Raccourci bureau cree

REM -- Lancement immediat --
echo.
echo [->] Lancement du bot...
start "Trading Bot" /MIN cmd /c "cd /d "%INSTALL_DIR%" && "%PYTHON%" src\run_forever.py >> logs\bot.log 2>&1"

REM -- Ouverture dashboard local --
echo [->] Ouverture du dashboard dans 8 secondes...
timeout /t 8 /nobreak >nul
start "" "http://localhost:5000"

echo.
echo ====================================================
echo    INSTALLATION TERMINEE
echo ====================================================
echo.
echo  Mode      : LIVE ^(argent reel^) -- modifiable dans src\.env
echo  Bot       : en cours de demarrage
echo  Dashboard : http://localhost:5000  ^(ouvert dans le navigateur^)
echo.
echo  Raccourci "Trading Bot" cree sur le bureau.
echo  Le bot redemarrera automatiquement au prochain login.
echo.
echo  Telegram : envoie /start puis /status a ton bot pour verifier
echo.
echo  ML XGBoost : entrainement en cours en fond ^(5-10 min^)
echo             : surveille le fichier logs\ml_train.log
echo.
pause
