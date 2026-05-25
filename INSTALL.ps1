# ============================================================
#  INSTALL.ps1 - Bot Trading - Installation complete
#  Usage: clic droit > Executer avec PowerShell
# ============================================================

# --- Elevation admin automatique ---
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

$REPO_URL  = "https://github.com/morpheus45/trade.git"
$BOT_DIR   = "$env:USERPROFILE\trading-bot"
$VENV      = "$BOT_DIR\venv"
$PYTHON    = "$VENV\Scripts\python.exe"
$PIP       = "$VENV\Scripts\pip.exe"
$LOG       = "$BOT_DIR\logs\install.log"

function Log($msg) {
    $ts = (Get-Date).ToString("HH:mm:ss")
    $line = "[$ts] $msg"
    Write-Host $line
    if (Test-Path (Split-Path $LOG)) { Add-Content $LOG $line -Encoding UTF8 }
}

function Die($msg) {
    Write-Host ""
    Write-Host "ERREUR: $msg" -ForegroundColor Red
    Write-Host "Appuie sur une touche pour fermer..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 1
}

Clear-Host
Write-Host "============================================================"
Write-Host "  BOT TRADING - Installation complete"
Write-Host "============================================================"
Write-Host ""

# ============================================================
#  1. Python
# ============================================================
Write-Host "[1/7] Verification Python..."
$pyCmd = $null

foreach ($candidate in @("python","python3","py")) {
    try {
        $v = & $candidate --version 2>&1
        if ($v -match "Python 3\.(9|10|11|12)") { $pyCmd = $candidate; break }
    } catch {}
}

if (-not $pyCmd) {
    Write-Host "     Python non trouve - telechargement Python 3.11..."
    $pyInstaller = "$env:TEMP\python-3.11.9.exe"
    Invoke-WebRequest "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -OutFile $pyInstaller
    Write-Host "     Installation Python (cela prend ~2min)..."
    Start-Process $pyInstaller -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1" -Wait
    Remove-Item $pyInstaller -Force -ErrorAction SilentlyContinue
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" + [System.Environment]::GetEnvironmentVariable("Path","Machine")
    $pyCmd = "python"
    Write-Host "     Python installe."
} else {
    $ver = & $pyCmd --version 2>&1
    Write-Host "     OK - $ver"
}

# ============================================================
#  2. Git
# ============================================================
Write-Host "[2/7] Verification Git..."
$gitOk = $false
try { git --version | Out-Null; $gitOk = $true } catch {}

if (-not $gitOk) {
    Write-Host "     Git non trouve - telechargement..."
    $gitInstaller = "$env:TEMP\git-setup.exe"
    Invoke-WebRequest "https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/Git-2.45.2-64-bit.exe" -OutFile $gitInstaller
    Write-Host "     Installation Git..."
    Start-Process $gitInstaller -ArgumentList "/VERYSILENT /NORESTART /NOCANCEL /SP- /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS /COMPONENTS=icons,ext\reg\shellhere,assoc,assoc_sh" -Wait
    Remove-Item $gitInstaller -Force -ErrorAction SilentlyContinue
    $env:Path += ";C:\Program Files\Git\cmd"
    Write-Host "     Git installe."
} else {
    Write-Host "     OK - $(git --version)"
}

# ============================================================
#  3. Clone ou mise a jour du repo
# ============================================================
Write-Host "[3/7] Depot GitHub..."

if (Test-Path "$BOT_DIR\.git") {
    Write-Host "     Dossier existant - mise a jour..."
    $result = git -C $BOT_DIR pull --ff-only 2>&1
    Write-Host "     $result"
} else {
    Write-Host "     Clonage de $REPO_URL..."
    if (Test-Path $BOT_DIR) { Remove-Item $BOT_DIR -Recurse -Force }
    git clone $REPO_URL $BOT_DIR 2>&1 | Write-Host
}

New-Item -ItemType Directory -Force -Path "$BOT_DIR\logs"  | Out-Null
New-Item -ItemType Directory -Force -Path "$BOT_DIR\models" | Out-Null
New-Item -ItemType Directory -Force -Path "$BOT_DIR\data"   | Out-Null
Write-Host "     OK"

# ============================================================
#  4. Environnement virtuel + dependances
# ============================================================
Write-Host "[4/7] Environnement Python (venv)..."

if (-not (Test-Path $PYTHON)) {
    Write-Host "     Creation du venv..."
    & $pyCmd -m venv $VENV
}

Write-Host "     Mise a jour pip..."
& $PYTHON -m pip install --upgrade pip --quiet

Write-Host "     Installation des dependances (peut prendre 3-5min)..."
& $PIP install -r "$BOT_DIR\requirements.txt" --quiet
Write-Host "     OK - dependances installees"

# ============================================================
#  5. Fichier .env
# ============================================================
Write-Host "[5/7] Configuration .env..."

$envFile = "$BOT_DIR\src\.env"
if (-not (Test-Path $envFile)) {
    Write-Host ""
    Write-Host "     CONFIGURATION REQUISE - remplis les valeurs suivantes :"
    Write-Host "     (Appuie Entree pour laisser vide / utiliser la valeur par defaut)"
    Write-Host ""

    $binanceKey    = Read-Host "     Binance API Key"
    $binanceSecret = Read-Host "     Binance API Secret"
    $groqKey       = Read-Host "     Groq API Key (gratuit sur console.groq.com)"
    $telegramToken = Read-Host "     Telegram Bot Token (optionnel)"
    $telegramChat  = Read-Host "     Telegram Chat ID  (optionnel)"

    $envContent = @"
# Bot Trading - Configuration
BINANCE_API_KEY=$binanceKey
BINANCE_API_SECRET=$binanceSecret

GROQ_API_KEY=$groqKey

TELEGRAM_BOT_TOKEN=$telegramToken
TELEGRAM_CHAT_ID=$telegramChat

# Parametres bot
INITIAL_CAPITAL=100
RISK_PCT=0.05
PAPER_TRADING=false
"@
    $envContent | Out-File -FilePath $envFile -Encoding UTF8
    Write-Host "     .env cree."
} else {
    Write-Host "     .env existant conserve."
}

# ============================================================
#  6. Tache planifiee : demarrage automatique au boot
# ============================================================
Write-Host "[6/7] Demarrage automatique..."

$taskName   = "TradingBot-Watchdog"
$taskScript = @"
Set-Location '$BOT_DIR'
Start-Process cmd -ArgumentList '/c cd /d $BOT_DIR && $PYTHON src\run_forever.py >> logs\bot.log 2>&1' -WindowStyle Hidden
"@
$taskScriptPath = "$BOT_DIR\start_bot.ps1"
$taskScript | Out-File -FilePath $taskScriptPath -Encoding UTF8

schtasks /delete /tn $taskName /f 2>$null | Out-Null
schtasks /create /tn $taskName `
    /tr "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$taskScriptPath`"" `
    /sc ONLOGON /rl HIGHEST /f 2>&1 | Out-Null
Write-Host "     OK - bot demarre automatiquement a chaque connexion"

# ============================================================
#  7. Tache planifiee : mise a jour automatique toutes les 30min
# ============================================================
Write-Host "[7/7] Mise a jour automatique (toutes les 30min)..."

$updateScript = @"
# Auto-update : git pull + redemarrage si nouveau code
Set-Location '$BOT_DIR'
`$before = git rev-parse HEAD 2>`$null
git pull --ff-only 2>`$null | Out-Null
`$after  = git rev-parse HEAD 2>`$null
if (`$before -ne `$after) {
    Add-Content '$BOT_DIR\logs\autoupdate.log' "[(Get-Date)] Mise a jour appliquee - redemarrage bot"
    Start-Sleep 2
    Stop-Process -Name python -Force -ErrorAction SilentlyContinue
    Start-Sleep 3
    Start-Process cmd -ArgumentList '/c cd /d $BOT_DIR && $PYTHON src\run_forever.py >> logs\bot.log 2>&1' -WindowStyle Hidden
}
"@
$updateScriptPath = "$BOT_DIR\auto_update.ps1"
$updateScript | Out-File -FilePath $updateScriptPath -Encoding UTF8

$updateTask = "TradingBot-AutoUpdate"
schtasks /delete /tn $updateTask /f 2>$null | Out-Null
schtasks /create /tn $updateTask `
    /tr "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$updateScriptPath`"" `
    /sc MINUTE /mo 30 /rl HIGHEST /f 2>&1 | Out-Null
Write-Host "     OK - mise a jour auto toutes les 30 minutes"

# ============================================================
#  Demarrage immediat du bot
# ============================================================
Write-Host ""
Write-Host "Demarrage du bot..."
Stop-Process -Name python -Force -ErrorAction SilentlyContinue
Start-Sleep 2
Start-Process cmd -ArgumentList "/c cd /d $BOT_DIR && $PYTHON src\run_forever.py >> logs\bot.log 2>&1" -WindowStyle Hidden
Start-Sleep 5

# ============================================================
#  Rapport final
# ============================================================
$localIP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch "^(127\.|169\.)" -and $_.PrefixOrigin -ne "WellKnown" } | Select-Object -First 1).IPAddress

$tailIP = try { & "C:\Program Files\Tailscale\tailscale.exe" ip -4 2>$null } catch { "non connecte" }

$reportPath = "$env:USERPROFILE\Desktop\BOT_INFO.txt"
@"
============================================================
  BOT TRADING - INSTALLATION COMPLETE
  $(Get-Date)
============================================================

ACCES DASHBOARD :
  LAN (meme reseau) : http://${localIP}:5000
  Tailscale (partout): http://${tailIP}:5000

MISES A JOUR :
  Automatique toutes les 30min via GitHub
  Manuelle : double-clic sur INSTALL.ps1

LOGS :
  Bot      : $BOT_DIR\logs\bot.log
  Watchdog : $BOT_DIR\logs\watchdog.log
  Updates  : $BOT_DIR\logs\autoupdate.log

TACHES PLANIFIEES :
  TradingBot-Watchdog   - demarre au login
  TradingBot-AutoUpdate - git pull toutes les 30min

============================================================
"@ | Out-File -FilePath $reportPath -Encoding UTF8

Write-Host ""
Write-Host "============================================================"
Write-Host "  INSTALLATION TERMINEE !"
Write-Host "  Dashboard : http://${localIP}:5000"
if ($tailIP -ne "non connecte") {
    Write-Host "  Tailscale  : http://${tailIP}:5000"
}
Write-Host "  Rapport   : BOT_INFO.txt sur le bureau"
Write-Host "============================================================"
Write-Host ""
Write-Host "Appuie sur une touche pour fermer..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
