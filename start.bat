@echo off
rem ---------------------------------------------------------------------------------------------
rem  AutoApply AI - one command on Windows: runs the full stack in Docker Desktop.
rem    start.bat          build + start (PostgreSQL, Redis, API, worker, beat, dashboard)
rem    start.bat stop     stop everything
rem  Prefer a native setup? Use WSL and run ./start.sh from the repository folder.
rem ---------------------------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

where docker >nul 2>nul || (echo [x] Docker Desktop is required: https://www.docker.com/products/docker-desktop & exit /b 1)
docker info >nul 2>nul || (echo [x] Docker Desktop is not running - start it and try again. & exit /b 1)

if /i "%1"=="stop" (
  docker compose down
  exit /b %errorlevel%
)

if exist .env goto env_ready
copy .env.example .env >nul
powershell -NoProfile -Command "$rng = [Security.Cryptography.RandomNumberGenerator]::Create(); function Key([int]$n) { $b = New-Object byte[] $n; $rng.GetBytes($b); [Convert]::ToBase64String($b).Replace('+','-').Replace('/','_') }; $secret = (Key 48).TrimEnd('='); $enc = Key 32; (Get-Content .env) -replace '^SECRET_KEY=.*', ('SECRET_KEY=' + $secret) -replace '^ENCRYPTION_KEY=.*', ('ENCRYPTION_KEY=' + $enc) | Set-Content .env"
echo [ok] Created .env with fresh secrets
:env_ready

echo [..] Building and starting AutoApply AI (the first build takes a few minutes)...
docker compose up --build -d || exit /b 1

echo [..] Waiting for the dashboard...
powershell -NoProfile -Command "for ($i=0; $i -lt 240; $i++) { try { Invoke-WebRequest -UseBasicParsing http://localhost:3000/api/health -TimeoutSec 3 | Out-Null; exit 0 } catch { Start-Sleep 1 } }; exit 1"
if errorlevel 1 (echo [x] Dashboard did not start - run: docker compose logs frontend & exit /b 1)

echo.
echo   Dashboard  http://localhost:3000
echo   API docs   http://localhost:8000/docs
echo   Stop with: start.bat stop
start "" http://localhost:3000
endlocal
