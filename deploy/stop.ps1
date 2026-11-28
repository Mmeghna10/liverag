# Stop the shared LiveRAG demo: the public link stops working and the demo data is discarded.
foreach ($name in "liverag-tunnel", "liverag-demo") {
    if (docker ps -aq --filter name=^$name$) { docker rm -f $name | Out-Null }
}
Write-Host "Stopped LiveRAG demo and tunnel."
