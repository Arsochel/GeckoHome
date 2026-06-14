# Wait for Docker to start
Start-Sleep -Seconds 10

# Change to project directory
cd "C:\path\to\your\geckohome"

# Start containers
docker compose up -d

Write-Host "GeckoHome containers started"
