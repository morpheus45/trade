# =============================================================================
#  setup-tunnel.ps1 — expose le dashboard sur une URL HTTPS publique
#
#  Principe : cloudflared ouvre une connexion SORTANTE vers Cloudflare, qui
#  relaie ensuite le trafic entrant. Aucun port ouvert sur la box, l'IP de ta
#  maison n'est jamais exposee.
#
#  A faire d'abord, une seule fois, cote Cloudflare :
#    1. Un compte Cloudflare avec un nom de domaine (meme a 1 EUR/an)
#    2. https://one.dash.cloudflare.com > Networks > Tunnels > Create a tunnel
#    3. Type "Cloudflared", nomme-le, copie le JETON (longue chaine "ey...")
#    4. Onglet "Public hostname" : choisis ton sous-domaine (bot.mondomaine.fr)
#       et pointe-le sur le service   http://localhost:5000
#
#  Puis ici :
#    powershell -ExecutionPolicy Bypass -File setup-tunnel.ps1 -Token "ey..."
#
#  Pas de nom de domaine ? Utilise -UseTailscale a la place (voir plus bas).
# =============================================================================

param(
    [string] $Token,
    [switch] $UseTailscale
)

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($Token)       { $argList += " -Token `"$Token`"" }
    if ($UseTailscale){ $argList += " -UseTailscale" }
    Start-Process powershell -Verb RunAs -ArgumentList $argList
    exit
}

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

$BOT_DIR = "$env:ProgramData\trading-bot"
$ENVFILE = "$BOT_DIR\src\.env"
$TASK    = "TradingBot"

function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "    OK  $m" -ForegroundColor Green }
function Die($m)  { Write-Host "`nERREUR : $m" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $ENVFILE)) { Die "$ENVFILE introuvable. Lance d'abord INSTALL.ps1." }

# =============================================================================
#  Option A — Tailscale (aucun nom de domaine requis)
# =============================================================================
if ($UseTailscale) {
    Step "Installation de Tailscale"
    if (-not (Test-Path "C:\Program Files\Tailscale\tailscale.exe")) {
        $inst = "$env:TEMP\tailscale-setup.exe"
        Invoke-WebRequest "https://pkgs.tailscale.com/stable/tailscale-setup-latest-amd64.msi" -OutFile $inst
        Start-Process msiexec -Wait -ArgumentList "/i `"$inst`" /quiet"
        Remove-Item $inst -Force -ErrorAction SilentlyContinue
    }
    & "C:\Program Files\Tailscale\tailscale.exe" up
    $tsIp = & "C:\Program Files\Tailscale\tailscale.exe" ip -4

    # Le dashboard doit accepter les connexions venant de l'interface Tailscale.
    (Get-Content $ENVFILE) -replace '^DASHBOARD_HOST=.*', 'DASHBOARD_HOST=0.0.0.0' |
        Set-Content $ENVFILE -Encoding UTF8

    Stop-ScheduledTask  -TaskName $TASK -ErrorAction SilentlyContinue
    Start-Sleep 3
    Start-ScheduledTask -TaskName $TASK

    Write-Host ""
    Write-Host "Tailscale actif." -ForegroundColor Green
    Write-Host "  Dashboard : http://${tsIp}:5000"
    Write-Host "  Installe l'application Tailscale sur ton telephone, connecte-toi"
    Write-Host "  avec le meme compte, et cette adresse fonctionnera partout."
    Write-Host ""
    Write-Host "  Le dashboard n'est PAS expose sur Internet : seuls tes appareils"
    Write-Host "  Tailscale peuvent l'atteindre." -ForegroundColor Green
    exit 0
}

# =============================================================================
#  Option B — Cloudflare Tunnel (URL publique HTTPS)
# =============================================================================
if (-not $Token) {
    Write-Host @"

Jeton de tunnel manquant.

  Usage :  powershell -ExecutionPolicy Bypass -File setup-tunnel.ps1 -Token "ey..."

  Pour obtenir le jeton :
    1. https://one.dash.cloudflare.com
    2. Networks > Tunnels > Create a tunnel > Cloudflared
    3. Copie le jeton affiche
    4. Onglet "Public hostname" : pointe ton sous-domaine sur
       le service  http://localhost:5000

  Pas de nom de domaine ? Relance avec -UseTailscale :
    powershell -ExecutionPolicy Bypass -File setup-tunnel.ps1 -UseTailscale

"@ -ForegroundColor Yellow
    exit 1
}

Step "Installation de cloudflared"
$cfExe = "$env:ProgramFiles\cloudflared\cloudflared.exe"
if (-not (Test-Path $cfExe)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $cfExe) | Out-Null
    Invoke-WebRequest `
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" `
        -OutFile $cfExe
}
Ok (& $cfExe --version 2>&1 | Select-Object -First 1)

Step "Configuration du dashboard"
$pw = (Select-String -Path $ENVFILE -Pattern '^DASHBOARD_PASSWORD=(.+)$').Matches.Groups[1].Value
if (-not $pw) {
    Die "DASHBOARD_PASSWORD est vide dans $ENVFILE.
       Le bot refuse de servir le dashboard sur le reseau sans mot de passe."
}

# Le tunnel se connecte en local : 127.0.0.1 suffit et reste le plus sur.
# TRUST_PROXY indique a Flask qu'un proxy HTTPS est devant : cookies securises
# et vraie IP client dans les logs de tentative de connexion.
(Get-Content $ENVFILE) -replace '^TRUST_PROXY=.*', 'TRUST_PROXY=true' |
    Set-Content $ENVFILE -Encoding UTF8
Ok "TRUST_PROXY active"

Step "Service du tunnel"
& $cfExe service uninstall 2>&1 | Out-Null
& $cfExe service install $Token
if ($LASTEXITCODE -ne 0) { Die "Installation du service cloudflared echouee." }
Start-Service cloudflared -ErrorAction SilentlyContinue
Set-Service  cloudflared -StartupType Automatic
Ok "tunnel installe et demarre au boot"

Step "Redemarrage du bot"
Stop-ScheduledTask  -TaskName $TASK -ErrorAction SilentlyContinue
Start-Sleep 3
Start-ScheduledTask -TaskName $TASK
Ok "bot redemarre"

Write-Host @"

Tunnel actif.

  Ton dashboard est joignable en HTTPS sur le sous-domaine configure dans
  Cloudflare, depuis n'importe ou.

  Mot de passe : $pw

  Etat du tunnel   Get-Service cloudflared
  Logs du tunnel   Get-EventLog -LogName Application -Source cloudflared -Newest 20

  Verifie maintenant : ouvre l'URL depuis ton telephone en 4G (pas en Wi-Fi)
  pour confirmer que l'acces fonctionne depuis l'exterieur.

"@ -ForegroundColor Green
