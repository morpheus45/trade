@echo off
title Bot de Trading
cd /d "%~dp0"

echo ================================================
echo   BOT DE TRADING — Demarrage
echo ================================================
echo.

python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Python introuvable. Installe Python 3.11+ et relance.
    pause & exit
)

echo [1/2] Installation / verification des dependances...
pip install -r ..\requirements.txt -q

echo [2/2] Lancement du bot...
echo.
python -u bot_trading.py

echo.
echo Bot arrete. Appuie sur une touche pour fermer.
pause
exit
