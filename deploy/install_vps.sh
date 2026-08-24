#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/flight-agent"
SERVICE_USER="flightagent"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install_vps.sh"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates fonts-liberation libnss3 libatk-bridge2.0-0 libgtk-3-0 libgbm1 libasound2

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

mkdir -p "$APP_DIR" "$APP_DIR/data" "$APP_DIR/artifacts/hotels"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

if [[ ! -f "$APP_DIR/requirements.txt" || ! -f "$APP_DIR/hotel_scraper.py" ]]; then
  echo "Upload the project files to $APP_DIR before running this installer."
  exit 1
fi

runuser -u "$SERVICE_USER" -- python3 -m venv "$APP_DIR/.venv"
runuser -u "$SERVICE_USER" -- "$APP_DIR/.venv/bin/pip" install --upgrade pip
runuser -u "$SERVICE_USER" -- "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
runuser -u "$SERVICE_USER" -- "$APP_DIR/.venv/bin/playwright" install chromium

install -m 0644 "$APP_DIR/deploy/hotel-scraper.service" /etc/systemd/system/hotel-scraper.service
install -m 0644 "$APP_DIR/deploy/hotel-scraper.timer" /etc/systemd/system/hotel-scraper.timer
systemctl daemon-reload
systemctl enable --now hotel-scraper.timer

echo "Installed. Add /opt/flight-agent/.env, then test with:"
echo "  sudo systemctl start hotel-scraper.service"
echo "  journalctl -u hotel-scraper.service -n 100 --no-pager"
