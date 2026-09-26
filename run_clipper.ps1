param (
    [string]$Url = "https://www.youtube.com/watch?v=4mTLpuQpB80",
    [int]$Top = 2,
    [int]$Candidates = 4
)

Write-Host "Starting Autonomous Video Clipper..." -ForegroundColor Cyan
python "$PSScriptRoot\clipper.py" --url "$Url" --top $Top --candidates $Candidates
