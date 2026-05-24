@echo off
chcp 65001 >nul 2>&1
title SETUP COMPLET - Bot Trading H24

:: ════════════════════════════════════════════════════════════
::  Auto-elevation administrateur
:: ════════════════════════════════════════════════════════════
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Droits admin requis - relancement automatique...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"%~f0\"' -Verb RunAs"
    exit /b
)

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║   SETUP COMPLET BOT TRADING H24                        ║
echo ║   RDP + Tailscale + Mise a jour + Acces permanent      ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

set REPORT=%USERPROFILE%\Desktop\BOT_ACCES_INFO.txt
set INSTALL_DIR=%USERPROFILE%\trading-bot
set PYTHON=%INSTALL_DIR%\venv\Scripts\python.exe

:: ════════════════════════════════════════════════════════════
::  ETAPE 1 : Activer le Bureau a distance (RDP)
:: ════════════════════════════════════════════════════════════
echo [1/5] Activation du Bureau a distance (RDP)...
reg add "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Terminal Server" /v fDenyTSConnections /t REG_DWORD /d 0 /f >nul
reg add "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v UserAuthentication /t REG_DWORD /d 0 /f >nul
netsh advfirewall firewall set rule group="remote desktop" new enable=Yes >nul 2>&1
sc config TermService start= auto >nul
net start TermService >nul 2>&1
echo [OK] RDP active

:: ════════════════════════════════════════════════════════════
::  ETAPE 2 : Ouvrir le port 5000 (dashboard bot)
:: ════════════════════════════════════════════════════════════
echo [2/5] Ouverture port 5000 (dashboard)...
netsh advfirewall firewall add rule name="TradingBot Dashboard" dir=in action=allow protocol=TCP localport=5000 >nul 2>&1
echo [OK] Port 5000 ouvert

:: ════════════════════════════════════════════════════════════
::  ETAPE 3 : Mise a jour du bot depuis GitHub
:: ════════════════════════════════════════════════════════════
echo [3/5] Mise a jour du bot...
if exist "%INSTALL_DIR%\.git" (
    git -C "%INSTALL_DIR%" pull --ff-only
    if %errorlevel% equ 0 (
        echo [OK] Bot mis a jour depuis GitHub
    ) else (
        echo [!] git pull echoue - continuation
    )
) else (
    echo [!] Dossier trading-bot introuvable : %INSTALL_DIR%
)

:: Redemarrer le bot
echo [->] Redemarrage du bot...
taskkill /f /im python.exe >nul 2>&1
timeout /t 3 /nobreak >nul
if exist "%PYTHON%" (
    start "TradingBot" /MIN cmd /c "cd /d \"%INSTALL_DIR%\" && \"%PYTHON%\" src\run_forever.py >> logs\bot.log 2>&1"
    echo [OK] Bot redémarre avec le nouveau code
) else (
    echo [!] Python venv introuvable : %PYTHON%
)

:: ════════════════════════════════════════════════════════════
::  ETAPE 4 : Installer Tailscale
:: ════════════════════════════════════════════════════════════
echo [4/5] Installation Tailscale...
where tailscale >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Tailscale deja installe
    goto do_tailscale_up
)

echo [->] Telechargement...
powershell -NoProfile -Command "(New-Object System.Net.WebClient).DownloadFile('https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe','%TEMP%\ts.exe')"
if not exist "%TEMP%\ts.exe" (
    echo [ERR] Echec telechargement Tailscale
    goto skip_tailscale
)
echo [->] Installation silencieuse...
"%TEMP%\ts.exe" /quiet /norestart
del "%TEMP%\ts.exe" >nul 2>&1
timeout /t 8 /nobreak >nul
echo [OK] Tailscale installe

:do_tailscale_up
echo [->] Connexion Tailscale...
start "" tailscale up
timeout /t 15 /nobreak >nul

:skip_tailscale

:: ════════════════════════════════════════════════════════════
::  ETAPE 5 : Recupérer les infos d acces et créer le rapport
:: ════════════════════════════════════════════════════════════
echo [5/5] Collecte des informations d acces...

:: IP locale
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr "192.168"') do set LOCAL_IP=%%a
set LOCAL_IP=%LOCAL_IP: =%

:: IP Tailscale
for /f "tokens=*" %%i in ('tailscale ip -4 2^>nul') do set TAIL_IP=%%i

:: Nom PC
for /f %%i in ('hostname') do set PC_NAME=%%i

:: Nom utilisateur Windows
set WIN_USER=%USERNAME%

:: Ecrire le rapport
(
echo ════════════════════════════════════════════════════════
echo   BOT TRADING - INFORMATIONS D ACCES COMPLET
echo   Genere le %DATE% %TIME%
echo ════════════════════════════════════════════════════════
echo.
echo [PC BOT]
echo   Nom          : %PC_NAME%
echo   Utilisateur  : %WIN_USER%
echo   IP LAN       : %LOCAL_IP%
echo   IP Tailscale : %TAIL_IP%
echo.
echo [ACCES DASHBOARD]
echo   LAN           : http://%LOCAL_IP%:5000
echo   Tailscale     : http://%TAIL_IP%:5000
echo.
echo [ACCES BUREAU A DISTANCE]
echo   LAN           : mstsc /v:%LOCAL_IP%
echo   Tailscale     : mstsc /v:%TAIL_IP%
echo   Utilisateur   : %PC_NAME%\%WIN_USER%
echo   RDP           : ACTIVE
echo.
echo [BOT STATUS]
echo   Dossier       : %INSTALL_DIR%
echo   Watchdog      : run_forever.py
echo   Logs          : %INSTALL_DIR%\logs\bot.log
echo.
echo [TAILSCALE - ETAPES ANDROID]
echo   1. Installe "Tailscale" sur le Play Store
echo   2. Connecte-toi avec le MEME compte Google/GitHub
echo   3. Ouvre http://%TAIL_IP%:5000 dans Chrome Android
echo   4. C est permanent - pas besoin de VPN ni tunnel !
echo.
echo [TAILSCALE - ACCES CLAUDE/PC PRINCIPAL]
echo   Sur le PC principal, fait aussi : tailscale up
echo   Puis Bureau a distance vers : %TAIL_IP%
echo ════════════════════════════════════════════════════════
) > "%REPORT%"

:: Afficher le rapport
type "%REPORT%"

echo.
echo ════════════════════════════════════════════════════════
echo   RAPPORT SAUVEGARDE : %REPORT%
echo ════════════════════════════════════════════════════════
echo.

:: Ouvrir automatiquement le rapport
start notepad "%REPORT%"

:: Ouvrir aussi le dashboard local pour verifier
timeout /t 5 /nobreak >nul
start "" "http://localhost:5000"

echo.
echo [TERMINE] Setup complet. Consulte le fichier BOT_ACCES_INFO.txt
echo           sur le bureau pour toutes les infos d acces.
echo.
pause
