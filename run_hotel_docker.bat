@echo off
setlocal
cd /d "%~dp0"

if not exist ".env" (
  echo Missing .env. Copy .env.example to .env and set Telegram credentials.
  exit /b 1
)

docker compose up -d hotel-scraper
if errorlevel 1 exit /b %ERRORLEVEL%
echo Hotel scraper container is running in the background.
echo It scans immediately, then every HOTEL_INTERVAL_HOURS hours.
echo View logs with: docker compose logs -f hotel-scraper
exit /b %ERRORLEVEL%
