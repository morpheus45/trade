@echo off
chcp 65001 >/dev/null 2>&1
title Trading Bot IA — Installateur Windows

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║    TRADING BOT v2 — Installateur Windows         ║
echo  ║    Bot IA autonome H24 · Crypto                  ║
echo  ╚══════════════════════════════════════════════════╝
echo.

REM ════════════════════════════════════════════════════
REM  CONFIGURATION — Remplir vos clés avant de lancer
REM  (Ce fichier reste LOCAL, ne pas pousser sur GitHub)
REM ════════════════════════════════════════════════════
set ANTHROPIC_API_KEY=YOUR_ANTHROPIC_API_KEY_HERE
set BINANCE_API_KEY=YOUR_BINANCE_API_KEY_HERE
set BINANCE_API_SECRET=YOUR_BINANCE_API_SECRET_HERE
set TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN_HERE
set TELEGRAM_CHAT_ID=YOUR_TELEGRAM_CHAT_ID_HERE

REM Paper Trading (true=simulation, false=live avec vrai argent)
set PAPER_TRADING=false

REM Capital initial (USDT)
set CAPITAL=1000

REM ════════════════════════════════════════════════════

set INSTALL_DIR=%USERPROFILE%\trading-bot
set REPO=https://github.com/morpheus45/trade.git

echo [→] Repertoire d'installation : %INSTALL_DIR%
echo.

REM ── Verifier / Installer Python ──────────────────────
echo [1/8] Verification Python...
python --version >/dev/null 2>&1
if %errorlevel% neq 0 (
    echo [!] Python non trouve — telechargement Python 3.11...
    set PY_INSTALLER=%TEMP%\python-3.11.9-amd64.exe
    powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%PY_INSTALLER%'" 2>/dev/null
    if exist "%PY_INSTALLER%" (
        echo [→] Installation Python 3.11...
        "%PY_INSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
        del "%PY_INSTALLER%" >/dev/null 2>&1
        echo [✓] Python 3.11 installe
    ) else (
        echo [✗] Echec telechargement Python
        echo Installez Python 3.11 manuellement depuis https://python.org
        pause
        exit /b 1
    )
) else (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo [✓] Python %%v detecte
)

REM ── Verifier / Installer Git ─────────────────────────
echo [2/8] Verification Git...
git --version >/dev/null 2>&1
if %errorlevel% neq 0 (
    echo [!] Git non trouve — telechargement Git...
    set GIT_INSTALLER=%TEMP%\git-installer.exe
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe' -OutFile '%GIT_INSTALLER%'" 2>/dev/null
    if exist "%GIT_INSTALLER%" (
        echo [→] Installation Git...
        "%GIT_INSTALLER%" /VERYSILENT /NORESTART /NOCANCEL /SP- /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS /COMPONENTS="icons,ext\reg\shellhere,assoc,assoc_sh"
        del "%GIT_INSTALLER%" >/dev/null 2>&1
        set PATH=%PATH%;C:\Program Files\Git\bin
        echo [✓] Git installe
    ) else (
        echo [✗] Echec telechargement Git
        echo Installez Git manuellement depuis https://git-scm.com
        pause
        exit /b 1
    )
) else (
    for /f "tokens=3" %%v in ('git --version') do echo [✓] Git %%v detecte
)

REM ── Cloner ou mettre a jour le repo ──────────────────
echo [3/8] Code source depuis GitHub...
if exist "%INSTALL_DIR%\.git" (
    echo [→] Mise a jour du code...
    git -C "%INSTALL_DIR%" pull -q
    echo [✓] Code mis a jour
) else (
    echo [→] Clonage depuis GitHub...
    git clone -q "%REPO%" "%INSTALL_DIR%"
    if %errorlevel% neq 0 (
        echo [✗] Echec du clonage. Verifiez votre connexion internet.
        pause
        exit /b 1
    )
    echo [✓] Code telecharge
)

REM ── Environnement virtuel Python ─────────────────────
echo [4/8] Environnement Python...
if not exist "%INSTALL_DIR%\venv\Scripts\python.exe" (
    python -m venv "%INSTALL_DIR%\venv"
)
call "%INSTALL_DIR%\venv\Scripts\activate.bat"
pip install --upgrade pip -q
pip install -r "%INSTALL_DIR%\requirements.txt" -q
echo [✓] Dependances Python installees

REM ── Dossiers necessaires ─────────────────────────────
echo [5/8] Creation des dossiers...
mkdir "%INSTALL_DIR%\logs"      2>/dev/null
mkdir "%INSTALL_DIR%\models"    2>/dev/null
mkdir "%INSTALL_DIR%\data"      2>/dev/null
echo %CAPITAL% > "%INSTALL_DIR%\initial_capital.txt"
echo [✓] Dossiers crees

REM ── Fichier .env ─────────────────────────────────────
echo [6/8] Configuration...
(
    echo # === Trading Bot — Configuration ===
    echo BINANCE_API_KEY=%BINANCE_API_KEY%
    echo BINANCE_API_SECRET=%BINANCE_API_SECRET%
    echo API_KEY=%BINANCE_API_KEY%
    echo API_SECRET=%BINANCE_API_SECRET%
    echo TELEGRAM_BOT_TOKEN=%TELEGRAM_BOT_TOKEN%
    echo TELEGRAM_CHAT_ID=%TELEGRAM_CHAT_ID%
    echo ANTHROPIC_API_KEY=%ANTHROPIC_API_KEY%
    echo PAPER_TRADING=%PAPER_TRADING%
) > "%INSTALL_DIR%\src\.env"
echo [✓] .env configure

REM ── Entrainement ML en arriere-plan ──────────────────
echo [7/8] Modele ML...
if not exist "%INSTALL_DIR%\models\xgboost_model.json" (
    echo [→] Entrainement ML en arriere-plan ^(5-10 min^)...
    start /MIN cmd /c "cd /d "%INSTALL_DIR%" && "%INSTALL_DIR%\venv\Scripts\python.exe" src\train_xgboost.py >> logs\ml_train.log 2>&1"
    echo [✓] Entrainement ML lance ^(le bot demarre sans attendre^)
) else (
    echo [✓] Modele ML deja present
)

REM ── Tache planifiee au demarrage ─────────────────────
echo [8/8] Demarrage automatique...
schtasks /delete /tn "TradingBot" /f >/dev/null 2>&1
schtasks /create /tn "TradingBot" /tr "cmd /c cd /d \"%INSTALL_DIR%\" && \"%INSTALL_DIR%\venv\Scripts\python.exe\" src\run_forever.py >> logs\bot.log 2>&1" /sc ONLOGON /rl HIGHEST /f >/dev/null 2>&1
echo [✓] Demarrage auto configure ^(se lance au login Windows^)

REM ── Raccourci Bureau ─────────────────────────────────
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Trading Bot.lnk'); $sc.TargetPath = '%INSTALL_DIR%\venv\Scripts\python.exe'; $sc.Arguments = 'src\run_forever.py'; $sc.WorkingDirectory = '%INSTALL_DIR%'; $sc.Description = 'Trading Bot IA Crypto'; $sc.Save()" 2>/dev/null
echo [✓] Raccourci bureau cree

REM ── Lancement immediat ───────────────────────────────
echo.
echo [→] Lancement du bot...
start "Trading Bot" /MIN cmd /c "cd /d "%INSTALL_DIR%" && "%INSTALL_DIR%\venv\Scripts\python.exe" src\run_forever.py >> logs\bot.log 2>&1"

REM ── Ouverture du Dashboard ───────────────────────────
timeout /t 3 /nobreak >/dev/null
start "" "https://morpheus45.github.io/trade/"

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║         INSTALLATION TERMINEE                    ║
echo  ╚══════════════════════════════════════════════════╝
echo.
echo  Mode          : %PAPER_TRADING%
echo  Bot           : en cours de demarrage
echo  Dashboard web : https://morpheus45.github.io/trade/
echo  Dashboard local : http://localhost:5000  ^(dans ~30s^)
echo.
echo  Raccourci "Trading Bot" cree sur le bureau
echo  Le bot se relancera automatiquement au prochain login
echo.
echo  Telegram : envoie /status pour verifier l'etat
echo.
pause
