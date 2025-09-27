# setup.ps1
# Script to setup and run TELE BOT automatically

Write-Host "Starting setup..."

# 1. Create venv if not exists
if (!(Test-Path "venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv venv
}

# 2. Activate venv
Write-Host "Activating virtual environment..."
. .\venv\Scripts\Activate.ps1

# 3. Install requirements
if (Test-Path "requirements.txt") {
    Write-Host "Installing dependencies..."
    pip install --upgrade pip
    pip install -r requirements.txt
}
else {
    Write-Host "requirements.txt not found!"
}

# 4. Run the bot
try {
    Write-Host "Running TELE BOT..."
    python app.py
}
catch {
    Write-Host "Error while running TELE BOT:"
    Write-Host $_.Exception.Message
}
