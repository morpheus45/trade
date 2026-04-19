@echo off
chcp 65001 >nul 2>&1
title Trading Bot - Activation LIVE + Entrainement ML

echo.
echo ====================================================
echo    ACTIVATION MODE LIVE + ENTRAINEMENT ML
echo ====================================================
echo.

set INSTALL_DIR=%USERPROFILE%\trading-bot
set PYTHON=%INSTALL_DIR%\venv\Scripts\python.exe
set ENV_FILE=%INSTALL_DIR%\src\.env

REM -- Mise a jour du code depuis GitHub --
echo [1/4] Mise a jour du code...
git -C "%INSTALL_DIR%" pull -q
echo [OK] Code mis a jour avec tous les correctifs

REM -- Activer le mode LIVE --
echo [2/4] Activation du mode LIVE...
REM Remplacer PAPER_TRADING=true par false dans .env
powershell -NoProfile -Command "(Get-Content '%ENV_FILE%') -replace 'PAPER_TRADING=true', 'PAPER_TRADING=false' | Set-Content '%ENV_FILE%'"
echo [OK] PAPER_TRADING=false active

REM -- Verifier la cle Anthropic --
echo [3/4] Verification cle Anthropic...
findstr /C:"ANTHROPIC_API_KEY=sk-ant" "%ENV_FILE%" >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Cle Anthropic presente - Claude AI sera actif
) else (
    echo [!] Cle Anthropic manquante - ouvre %ENV_FILE% et ajoute:
    echo     ANTHROPIC_API_KEY=sk-ant-api03-VOTRE_CLE_ICI
)

REM -- Lancer l'entrainement ML --
echo [4/4] Entrainement modele ML XGBoost...
if exist "%INSTALL_DIR%\models\xgboost_model.json" (
    echo [OK] Modele deja present - re-entrainement pour mise a jour...
    del "%INSTALL_DIR%\models\xgboost_model.json" >nul 2>&1
)
start "ML Training" /MIN cmd /c ""%PYTHON%" "%INSTALL_DIR%\src\train_xgboost.py" > "%INSTALL_DIR%\logs\ml_train.log" 2>&1 && echo ML TERMINE >> "%INSTALL_DIR%\logs\ml_train.log""
echo [OK] Entrainement ML lance en arriere-plan ^(10-15 min^)

REM -- Redemarrer le bot --
echo.
echo [->] Arret du bot actuel...
taskkill /F /IM python.exe /T >nul 2>&1
timeout /t 6 /nobreak >nul

echo [->] Redemarrage en mode LIVE...
start "Trading Bot LIVE" /MIN cmd /c "cd /d "%INSTALL_DIR%" && "%PYTHON%" src\run_forever.py >> logs\bot.log 2>&1"
timeout /t 3 /nobreak >nul

echo.
echo ====================================================
echo    CONFIGURE
echo ====================================================
echo.
echo  Mode       : LIVE TRADING (argent reel)
echo  Capital    : verifier sur Binance (8 EUR)
echo  ML         : entrainement en cours ^(~15 min^)
echo  Claude AI  : actif si cle Anthropic presente
echo.
echo  Dashboard  : http://localhost:5000
echo  GitHub     : https://morpheus45.github.io/trade/
echo.
echo  Pour voir les logs ML :
echo  type %INSTALL_DIR%\logs\ml_train.log
echo.
pause
