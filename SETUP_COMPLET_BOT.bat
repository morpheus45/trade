@echo off
chcp 65001 >nul 2>&1
title BOT TRADING - Setup Complet H24

:: ════════════════════════════════════════════════════════════
::  ELEVATION ADMIN AUTOMATIQUE
:: ════════════════════════════════════════════════════════════
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process cmd -ArgumentList '/c \"%~f0\"' -Verb RunAs -WindowStyle Normal"
    exit /b
)

cls
echo.
echo  ╔══════════════════════════════════════════════════════════╗
echo  ║        BOT TRADING - SETUP COMPLET H24                  ║
echo  ║  RDP + Tailscale + Mise a jour + Dashboard permanent    ║
echo  ╚══════════════════════════════════════════════════════════╝
echo.

set INSTALL_DIR=%USERPROFILE%\trading-bot
set PYTHON=%INSTALL_DIR%\venv\Scripts\python.exe
set REPORT=%USERPROFILE%\Desktop\BOT_ACCES_INFO.txt
set ERRORS=0

:: ════════════════════════════════════════════════════════════
::  1. BUREAU A DISTANCE (RDP)
:: ════════════════════════════════════════════════════════════
echo  [1/6] Activation Bureau a distance (RDP)...

reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server" /v fDenyTSConnections /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v UserAuthentication /t REG_DWORD /d 0 /f >nul 2>&1
netsh advfirewall firewall set rule group="remote desktop" new enable=Yes >nul 2>&1
netsh advfirewall firewall add rule name="RDP-3389" dir=in action=allow protocol=TCP localport=3389 >nul 2>&1
sc config TermService start= auto >nul 2>&1
net start TermService >nul 2>&1
powershell -NoProfile -Command "Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name 'fDenyTSConnections' -Value 0; Enable-NetFirewallRule -DisplayGroup 'Remote Desktop'" >nul 2>&1

echo       OK - RDP actif sur le port 3389

:: ════════════════════════════════════════════════════════════
::  2. PARE-FEU - ports utiles
:: ════════════════════════════════════════════════════════════
echo  [2/6] Ouverture des ports (5000 dashboard, 22 SSH)...

netsh advfirewall firewall add rule name="Bot-Dashboard-5000" dir=in action=allow protocol=TCP localport=5000 >nul 2>&1
netsh advfirewall firewall add rule name="Bot-SSH-22" dir=in action=allow protocol=TCP localport=22 >nul 2>&1

echo       OK - Ports 5000 et 22 ouverts

:: ════════════════════════════════════════════════════════════
::  3. MISE A JOUR DU BOT (git pull)
:: ════════════════════════════════════════════════════════════
echo  [3/6] Mise a jour du bot depuis GitHub...

if not exist "%INSTALL_DIR%\.git" (
    echo       WARN - %INSTALL_DIR% introuvable
    set ERRORS=1
    goto step4
)

git -C "%INSTALL_DIR%" pull --ff-only
if %errorlevel% equ 0 (
    echo       OK - Code mis a jour
) else (
    echo       WARN - git pull echoue, continuation
)

:: Arreter les anciens processus Python
taskkill /f /im python.exe >nul 2>&1
timeout /t 3 /nobreak >nul

:: Relancer le watchdog
if exist "%PYTHON%" (
    start "TradingBot-Watchdog" /MIN cmd /c "cd /d \"%INSTALL_DIR%\" && \"%PYTHON%\" src\run_forever.py >> logs\bot.log 2>&1"
    echo       OK - Bot redémarre (nouveau code charge)
) else (
    echo       ERR - Python introuvable : %PYTHON%
    set ERRORS=1
)

:step4
:: ════════════════════════════════════════════════════════════
::  4. INSTALLATION TAILSCALE
:: ════════════════════════════════════════════════════════════
echo  [4/6] Tailscale (VPN permanent sans port forwarding)...

where tailscale >nul 2>&1
if %errorlevel% equ 0 (
    echo       OK - Tailscale deja installe
    goto tailscale_connect
)

powershell -NoProfile -Command ^
  "(New-Object System.Net.WebClient).DownloadFile(" ^
  "'https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe'," ^
  "'%TEMP%\tailscale-setup.exe')"

if not exist "%TEMP%\tailscale-setup.exe" (
    echo       ERR - Echec telechargement Tailscale
    set ERRORS=1
    goto step5
)

"%TEMP%\tailscale-setup.exe" /quiet /norestart
del "%TEMP%\tailscale-setup.exe" >nul 2>&1
timeout /t 8 /nobreak >nul
echo       OK - Tailscale installe

:tailscale_connect
echo  [5/6] Connexion Tailscale - Login dans le navigateur...
start "" tailscale up
echo.
echo  ┌─────────────────────────────────────────────────────┐
echo  │  Une fenetre de login s'ouvre dans ton navigateur  │
echo  │  Connecte-toi avec Google ou GitHub (GRATUIT)      │
echo  │  Utilise le MEME compte sur tous tes appareils     │
echo  └─────────────────────────────────────────────────────┘
echo.

:: Attendre que Tailscale se connecte (max 60s)
set TAIL_IP=
set /a WAIT=0
:wait_tailscale
if %WAIT% geq 60 goto no_tailscale
timeout /t 2 /nobreak >nul
set /a WAIT=%WAIT%+2
for /f "tokens=*" %%i in ('tailscale ip -4 2^>nul') do set TAIL_IP=%%i
if defined TAIL_IP goto got_tailscale
echo       Attente login Tailscale... (%WAIT%s)
goto wait_tailscale

:no_tailscale
echo       WARN - Login Tailscale pas encore termine
echo             Relance ce script apres t'etre connecte
goto step5

:got_tailscale
echo       OK - Tailscale connecte ! IP = %TAIL_IP%

:step5
:: ════════════════════════════════════════════════════════════
::  6. RAPPORT D'ACCES COMPLET
:: ════════════════════════════════════════════════════════════
echo  [6/6] Collecte des informations d acces...

:: IP locale LAN
set LOCAL_IP=
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr /v "127." ^| findstr /v "169."') do (
    if not defined LOCAL_IP set LOCAL_IP=%%a
)
if defined LOCAL_IP set LOCAL_IP=%LOCAL_IP: =%

:: Nom PC et utilisateur
for /f %%i in ('hostname') do set PC_NAME=%%i

(
echo ════════════════════════════════════════════════════════════
echo   BOT TRADING - INFORMATIONS D ACCES H24
echo   Genere le %DATE% a %TIME%
echo ════════════════════════════════════════════════════════════
echo.
echo PC : %PC_NAME%   Utilisateur : %USERNAME%
echo.
echo ─── DASHBOARD BOT ──────────────────────────────────────────
echo   LAN ^(meme reseau^)  : http://%LOCAL_IP%:5000
if defined TAIL_IP (
echo   Tailscale ^(partout^) : http://%TAIL_IP%:5000
)
echo.
echo ─── BUREAU A DISTANCE ^(RDP^) ────────────────────────────────
echo   LAN     : Ouvre "Connexion Bureau a distance" → %LOCAL_IP%
if defined TAIL_IP (
echo   Partout : Ouvre "Connexion Bureau a distance" → %TAIL_IP%
)
echo   Login   : %PC_NAME%\%USERNAME%  ^(ton mot de passe Windows^)
echo   Statut  : ACTIVE
echo.
echo ─── TAILSCALE ──────────────────────────────────────────────
if defined TAIL_IP (
echo   IP fixe permanente : %TAIL_IP%
echo   Ne change jamais - meme apres redemarrage
) else (
echo   Tailscale : login pas encore termine
echo   Relance le script apres connexion
)
echo.
echo ─── ANDROID ^(acces externe^) ────────────────────────────────
echo   1. Installe "Tailscale" sur le Play Store
echo   2. Connecte-toi avec le MEME compte
if defined TAIL_IP (
echo   3. Accede au dashboard : http://%TAIL_IP%:5000
) else (
echo   3. Reviens ici pour l IP Tailscale apres login
)
echo.
echo ─── PC PRINCIPAL ^(cedri^) ────────────────────────────────────
echo   Lance tailscale up sur le PC principal
if defined TAIL_IP (
echo   Puis Bureau a distance vers : %TAIL_IP%
echo   Claude peut alors te connecter directement au bot
)
echo.
if %ERRORS% equ 0 (
echo   STATUT : TOUT OK
) else (
echo   STATUT : QUELQUES AVERTISSEMENTS - voir console
)
echo ════════════════════════════════════════════════════════════
) > "%REPORT%"

:: Afficher dans la console
type "%REPORT%"

:: Ouvrir le rapport
start notepad "%REPORT%"

:: Ouvrir le dashboard local
echo.
echo  Ouverture du dashboard dans 5s...
timeout /t 5 /nobreak >nul
start "" "http://localhost:5000"

echo.
echo  ╔══════════════════════════════════════════════════════════╗
echo  ║  SETUP TERMINE ! Rapport : BOT_ACCES_INFO.txt          ║
echo  ║  Donne l IP Tailscale a Claude pour acces complet      ║
echo  ╚══════════════════════════════════════════════════════════╝
echo.
pause
