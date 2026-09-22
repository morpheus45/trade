# =============================================================================
#  INSTALL.ps1 — Bot de trading, installation sur une machine Windows dediee
#
#  Usage : clic droit sur ce fichier > "Executer avec PowerShell"
#          (le script demande lui-meme les droits administrateur)
#
#  Ce qu'il fait :
#    - installe Python et Git s'ils manquent
#    - clone ou met a jour le depot
#    - cree l'environnement Python et installe les dependances
#    - genere un .env avec un mot de passe de dashboard aleatoire
#    - empeche la machine de se mettre en veille
#    - installe une tache planifiee qui demarre le bot AU BOOT, sans qu'une
#      session utilisateur soit ouverte, et le relance s'il s'arrete
# =============================================================================

#Requires -Version 5.1

# --- Elevation administrateur automatique ------------------------------------
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell -Verb RunAs -ArgumentList `
        "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

$REPO_URL = "https://github.com/morpheus45/trade.git"
$BRANCH   = "main"
$BOT_DIR  = "$env:ProgramData\trading-bot"
$VENV     = "$BOT_DIR\venv"
$PYTHON   = "$VENV\Scripts\python.exe"
$PIP      = "$VENV\Scripts\pip.exe"
$TASK     = "TradingBot"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    !   $msg" -ForegroundColor Yellow }
function Die($msg) {
    Write-Host "`nERREUR : $msg" -ForegroundColor Red
    Write-Host "`nAppuie sur une touche pour fermer..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 1
}

Clear-Host
Write-Host "==============================================================="
Write-Host "  BOT DE TRADING - Installation machine dediee"
Write-Host "==============================================================="

# =============================================================================
#  1. Python
# =============================================================================
Step "Python"
$pyCmd = $null
foreach ($c in @("py", "python", "python3")) {
    try {
        $v = & $c --version 2>&1
        # 3.13+ n'a pas encore de wheels pour toutes les dependances scientifiques
        if ($v -match "Python 3\.(10|11|12)\.") { $pyCmd = $c; break }
    } catch {}
}

if (-not $pyCmd) {
    Warn "Python 3.10-3.12 introuvable - telechargement de Python 3.11..."
    $inst = "$env:TEMP\python-3.11.9-amd64.exe"
    Invoke-WebRequest "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -OutFile $inst
    Start-Process $inst -Wait -ArgumentList `
        "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_launcher=1"
    Remove-Item $inst -Force -ErrorAction SilentlyContinue
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path","User")
    $pyCmd = "py"
    if (-not (Get-Command $pyCmd -ErrorAction SilentlyContinue)) {
        Die "Python installe mais introuvable. Redemarre la machine et relance ce script."
    }
}
Ok (& $pyCmd --version 2>&1)

# =============================================================================
#  2. Git
# =============================================================================
Step "Git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Warn "Git introuvable - telechargement..."
    $inst = "$env:TEMP\git-setup.exe"
    Invoke-WebRequest "https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/Git-2.47.1-64-bit.exe" -OutFile $inst
    Start-Process $inst -Wait -ArgumentList "/VERYSILENT /NORESTART /NOCANCEL /SP-"
    Remove-Item $inst -Force -ErrorAction SilentlyContinue
    $env:Path += ";C:\Program Files\Git\cmd"
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Die "Git indisponible." }
Ok (git --version)

# =============================================================================
#  3. Code source
# =============================================================================
# Installe dans ProgramData et non dans le profil utilisateur : la tache
# planifiee tourne au demarrage, avant toute ouverture de session, et n'aurait
# alors pas acces a un dossier utilisateur (surtout si OneDrive le synchronise).
Step "Code source dans $BOT_DIR"
if (Test-Path "$BOT_DIR\.git") {
    git -C $BOT_DIR fetch --quiet origin $BRANCH
    git -C $BOT_DIR checkout --quiet $BRANCH
    git -C $BOT_DIR pull --ff-only --quiet
    Ok "depot mis a jour"
} else {
    if (Test-Path $BOT_DIR) { Remove-Item $BOT_DIR -Recurse -Force }
    git clone --quiet --branch $BRANCH $REPO_URL $BOT_DIR
    Ok "depot clone"
}
foreach ($d in @("data","logs","models")) {
    New-Item -ItemType Directory -Force -Path "$BOT_DIR\$d" | Out-Null
}

# =============================================================================
#  4. Environnement Python
# =============================================================================
Step "Environnement Python"
if (-not (Test-Path $PYTHON)) { & $pyCmd -m venv $VENV }
& $PYTHON -m pip install --upgrade pip --quiet
Write-Host "    Installation des dependances (3 a 6 minutes)..."
& $PIP install -r "$BOT_DIR\requirements.txt" --quiet
if ($LASTEXITCODE -ne 0) { Die "Installation des dependances echouee." }
Ok "dependances installees"

# =============================================================================
#  5. Configuration
# =============================================================================
Step "Configuration"
$envFile = "$BOT_DIR\src\.env"
$newEnv  = $false

if (Test-Path $envFile) {
    Ok ".env existant conserve"
    $dashPw = (Select-String -Path $envFile -Pattern '^DASHBOARD_PASSWORD=(.*)$').Matches.Groups[1].Value
} else {
    Copy-Item "$BOT_DIR\src\.env.example" $envFile

    # Mot de passe genere ici : 24 caracteres tires d'un generateur
    # cryptographique, personne n'a a en inventer un.
    $bytes = [byte[]]::new(18)
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $dashPw = [Convert]::ToBase64String($bytes).Replace('+','A').Replace('/','B').Replace('=','')

    (Get-Content $envFile) -replace '^DASHBOARD_PASSWORD=.*', "DASHBOARD_PASSWORD=$dashPw" |
        Set-Content $envFile -Encoding UTF8
    Ok ".env cree avec un mot de passe genere"
    $newEnv = $true
}

# Seul l'administrateur et SYSTEM doivent pouvoir lire les cles API.
icacls $envFile /inheritance:r /grant:r "SYSTEM:(R)" "Administrateurs:(F)" "Administrators:(F)" 2>&1 | Out-Null

# =============================================================================
#  6. Empecher la mise en veille
# =============================================================================
# Une machine endormie ne surveille plus ses stop-loss. Le bot demande deja a
# Windows de rester eveille, mais la politique d'alimentation prime.
Step "Mise en veille"
powercfg /change standby-timeout-ac 0   | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null
powercfg /change disk-timeout-ac 0      | Out-Null
powercfg /hibernate off                 2>&1 | Out-Null
Ok "veille et hibernation desactivees (sur secteur)"

# =============================================================================
#  7. Demarrage automatique au boot
# =============================================================================
# La tache tourne sous SYSTEM avec un declencheur AtStartup : le bot repart
# apres une coupure de courant meme si personne n'ouvre de session. L'ancienne
# version utilisait ONLOGON et restait donc a l'arret sur l'ecran de connexion.
Step "Demarrage automatique"

Unregister-ScheduledTask -TaskName $TASK -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction `
    -Execute $PYTHON `
    -Argument "src\main.py" `
    -WorkingDirectory $BOT_DIR

$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" `
                                        -LogonType ServiceAccount `
                                        -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TASK `
                       -Action $action `
                       -Trigger $trigger `
                       -Principal $principal `
                       -Settings $settings `
                       -Description "Bot de trading crypto + dashboard" | Out-Null
Ok "tache '$TASK' installee (demarrage au boot, relance auto)"

# =============================================================================
#  8. Demarrage
# =============================================================================
Step "Demarrage du bot"
Start-ScheduledTask -TaskName $TASK
Start-Sleep -Seconds 12

$state = (Get-ScheduledTask -TaskName $TASK).State
if ($state -eq "Running") {
    Ok "bot en cours d'execution"
} else {
    Warn "etat de la tache : $state - consulte $BOT_DIR\logs\bot.log"
}

# =============================================================================
#  Recapitulatif
# =============================================================================
$report = @"
===============================================================
  BOT DE TRADING - installation terminee
  $(Get-Date -Format "dd/MM/yyyy HH:mm")
===============================================================

DOSSIER        $BOT_DIR
CONFIGURATION  $envFile
LOGS           $BOT_DIR\logs\bot.log

DASHBOARD      http://127.0.0.1:5000
MOT DE PASSE   $dashPw

MODE           PAPER (simulation) - aucun ordre reel n'est passe.
               Pour passer en reel, mettre PAPER_TRADING=false dans le .env,
               mais seulement apres plusieurs jours d'observation en paper.

COMMANDES (PowerShell administrateur)
  Demarrer     Start-ScheduledTask  -TaskName $TASK
  Arreter      Stop-ScheduledTask   -TaskName $TASK
  Etat         Get-ScheduledTask    -TaskName $TASK
  Logs         Get-Content "$BOT_DIR\logs\bot.log" -Tail 50 -Wait

ACCES DEPUIS LE TELEPHONE
  Lance : powershell -ExecutionPolicy Bypass -File "$BOT_DIR\deploy\windows\setup-tunnel.ps1"

===============================================================
"@

$reportPath = "$env:PUBLIC\Desktop\BOT_INFO.txt"
try { $report | Out-File -FilePath $reportPath -Encoding UTF8 } catch {
    $reportPath = "$BOT_DIR\BOT_INFO.txt"
    $report | Out-File -FilePath $reportPath -Encoding UTF8
}

Write-Host ""
Write-Host $report
Write-Host "Recapitulatif enregistre : $reportPath" -ForegroundColor Cyan

if ($newEnv) {
    Write-Host ""
    Write-Host "ETAPE SUIVANTE — renseigne tes cles :" -ForegroundColor Yellow
    Write-Host "  notepad $envFile"
    Write-Host ""
    Write-Host "  BINANCE_API_KEY / BINANCE_API_SECRET"
    Write-Host "    Sur Binance, autorise 'Lecture' + 'Spot Trading'."
    Write-Host "    N'autorise JAMAIS les retraits." -ForegroundColor Yellow
    Write-Host "  GROQ_API_KEY                (optionnel, gratuit)"
    Write-Host "  TELEGRAM_BOT_TOKEN / _CHAT_ID (optionnel)"
    Write-Host ""
    Write-Host "  Puis : Stop-ScheduledTask -TaskName $TASK ; Start-ScheduledTask -TaskName $TASK"
}

Write-Host ""
Write-Host "Appuie sur une touche pour fermer..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
