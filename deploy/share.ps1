# Start LiveRAG in one container and share it through a free Cloudflare quick tunnel.
#
#   .\deploy\share.ps1            # build (first time), start, print the public https link
#   .\deploy\share.ps1 -Rebuild   # rebuild the image after code changes
#   .\deploy\stop.ps1             # stop both containers (the link stops working)
#
# Needs Docker Desktop running and Ollama running with qwen2.5:3b (answers use your GPU).
# The link is https://<random>.trycloudflare.com, changes on every start, and works only while
# this laptop is on and the containers are running. No Cloudflare account is needed.
param([switch]$Rebuild)
# Native programs (docker) write normal progress to stderr; Windows PowerShell would treat that
# as an error under "Stop", so failures are checked explicitly instead.
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot

try { Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 5 | Out-Null }
catch { throw "Ollama is not running. Start it (and 'ollama pull qwen2.5:3b' once), then try again." }

$image = docker image ls liverag:latest -q
if ($Rebuild -or -not $image) {
    Write-Host "Building the image (10-15 minutes the first time)..."
    docker build -t liverag:latest $repo
    if ($LASTEXITCODE -ne 0) { throw "Image build failed." }
}

# Windows PowerShell treats any message a native program writes to stderr as an error under
# "Stop", so check first instead of ignoring "already exists" / "no such container" messages.
if (-not (docker network ls -q --filter name=^liverag-net$)) { docker network create liverag-net | Out-Null }
foreach ($name in "liverag-tunnel", "liverag-demo") {
    if (docker ps -aq --filter name=^$name$) { docker rm -f $name | Out-Null }
}

Write-Host "Starting LiveRAG (databases, Redpanda, CDC, indexer, API)..."
docker run -d --name liverag-demo --network liverag-net -p 127.0.0.1:7860:7860 `
    -e LLM_PROVIDER=openai -e LLM_BASE_URL=http://host.docker.internal:11434/v1 `
    -e LLM_API_KEY=ollama -e LLM_MODEL=qwen2.5:3b liverag:latest | Out-Null

$ready = $false
for ($i = 0; $i -lt 100; $i++) {
    $logs = (docker logs liverag-demo 2>&1) -join "`n"
    if ($logs -match "Starting CDC reader") { $ready = $true; break }
    if ($logs -match "did not start|exited; stopping|Traceback") { break }
    Start-Sleep -Seconds 3
}
if (-not $ready) { docker logs --tail 30 liverag-demo; throw "LiveRAG did not start; see the log above." }
Start-Sleep -Seconds 5
Write-Host "Local: http://127.0.0.1:7860"

docker run -d --name liverag-tunnel --network liverag-net cloudflare/cloudflared:2025.1.0 `
    tunnel --no-autoupdate --url http://liverag-demo:7860 | Out-Null
$url = $null
for ($i = 0; $i -lt 30 -and -not $url; $i++) {
    Start-Sleep -Seconds 2
    $m = [regex]::Match(((docker logs liverag-tunnel 2>&1) -join "`n"), "https://[a-z0-9-]+\.trycloudflare\.com")
    if ($m.Success) { $url = $m.Value }
}
if (-not $url) { docker logs --tail 20 liverag-tunnel; throw "The tunnel did not start." }
Write-Host ""
Write-Host "Public link: $url" -ForegroundColor Green
Write-Host "Demo accounts: use the buttons on the sign-in page (password fills in automatically)."
Write-Host "Stop with: .\deploy\stop.ps1"
