#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"
$PROJECT = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=== GeckoHome Setup ===" -ForegroundColor Cyan
Write-Host "Project: $PROJECT"

Set-ExecutionPolicy RemoteSigned -Scope LocalMachine -Force

# --- Packages ---
Write-Host "[1/4] Installing packages..." -ForegroundColor Yellow
winget install Git.Git                   --silent --accept-package-agreements --accept-source-agreements
winget install Microsoft.OpenSSH.Preview --silent --accept-package-agreements --accept-source-agreements
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path","User")

# --- WSL2 ---
Write-Host "[2/4] WSL2..." -ForegroundColor Yellow
$wslFeature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue
if ($wslFeature.State -ne "Enabled") {
    Write-Host "Enabling WSL2 + Ubuntu (reboot required)..." -ForegroundColor Yellow
    wsl --install -d Ubuntu --no-launch
    Write-Host ""
    Write-Host "REBOOT AND RUN setup.ps1 AGAIN." -ForegroundColor Red
    Restart-Computer -Force
    exit 0
}
Write-Host "WSL2 already enabled." -ForegroundColor Green

$distros = wsl --list --quiet 2>&1
if ($distros -notmatch "Ubuntu") {
    Write-Host "Installing Ubuntu..." -ForegroundColor Yellow
    wsl --install -d Ubuntu --no-launch
    Start-Sleep -Seconds 15
}

# --- Docker Engine ---
Write-Host "[3/4] Docker Engine in WSL Ubuntu..." -ForegroundColor Yellow
$dockerCheck = wsl -d Ubuntu -u root -- which docker 2>&1
if ($dockerCheck -notlike "*/docker") {
    $tmpScript = "$env:TEMP\gecko_install_docker.sh"
    Set-Content -Path $tmpScript -Encoding utf8 -Value @'
set -e
apt-get update -qq
apt-get install -y -qq ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
'@
    $tmpWsl = "/mnt/" + ($tmpScript[0].ToString().ToLower()) + "/" + ($tmpScript.Substring(3) -replace "\\", "/")
    wsl -d Ubuntu -u root -- bash $tmpWsl
    Write-Host "Docker installed." -ForegroundColor Green
} else {
    Write-Host "Docker already installed." -ForegroundColor Green
}
wsl -d Ubuntu -u root -- service docker start

# --- Autostart task ---
Write-Host "[4/4] Registering startup task..." -ForegroundColor Yellow
$wslDir  = "/mnt/" + ($PROJECT[0].ToString().ToLower()) + "/" + ($PROJECT.Substring(3) -replace "\\", "/")
$startCmd = "service docker start; cd " + $wslDir + " && docker compose up -d"
$action   = New-ScheduledTaskAction -Execute "wsl.exe" -Argument ("-d Ubuntu -u root -- bash -c '" + $startCmd + "'")
$trigger  = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "GeckoDockerCompose" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force

# --- SSH ---
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 -ErrorAction SilentlyContinue
ssh-keygen -A 2>$null
Set-Service sshd -StartupType Automatic
Start-Service sshd -ErrorAction SilentlyContinue

git config --global --add safe.directory ($PROJECT -replace "\\", "/")

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green
Write-Host ""
Write-Host "Manual steps remaining:" -ForegroundColor Cyan
Write-Host "  1. Copy .env to project dir"
Write-Host "  2. Make sure models/gecko_yolo.pt is present"
Write-Host "  3. First run:"
Write-Host "       wsl -d Ubuntu -u root"
Write-Host "       service docker start"
Write-Host "       cd $wslDir && docker compose up --build -d"
Write-Host "  4. GitHub Actions runner: Settings -> Actions -> Runners -> New self-hosted runner"
