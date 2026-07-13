# One-time setup: venv, dependencies, Vosk + MediaPipe models.
# Run from this folder: .\setup.ps1

$Root = $PSScriptRoot
Set-Location $Root

Write-Host "=== SketchTalk setup ===" -ForegroundColor Cyan

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating .venv ..."
    python -m venv .venv
}

$py = ".\.venv\Scripts\python.exe"

Write-Host "Installing Python packages ..."
& $py -m pip install --upgrade pip 2>$null
& $py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "Package install failed." -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path models | Out-Null

$voskDir = "models\vosk-model-small-en-us-0.15"
if (-not (Test-Path $voskDir)) {
    Write-Host "Downloading Vosk English model (~40 MB) ..."
    $zip = "models\vosk-small-en.zip"
    Invoke-WebRequest -Uri "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath models -Force
    Remove-Item $zip
}

$handModel = "models\hand_landmarker.task"
if (-not (Test-Path $handModel)) {
    Write-Host "Downloading MediaPipe hand landmarker model (~8 MB) ..."
    Invoke-WebRequest -Uri "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task" -OutFile $handModel
}

Write-Host "Verifying imports ..."
& $py -c "import vosk, mediapipe, cv2, sounddevice; from vosk import Model; Model(r'models/vosk-model-small-en-us-0.15'); print('OK')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Verification failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Done. Run:" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\activate"
Write-Host "  python main.py"
