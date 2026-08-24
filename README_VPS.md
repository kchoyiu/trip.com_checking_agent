# VPS 自動運行

適用於 Ubuntu/Debian VPS，包括現有 Google Compute Engine instance。這個部署使用 systemd timer，每 6 小時執行一次；程式使用 headless Chromium，不需要桌面環境。

## 1. 上傳專案

在本機 PowerShell，先從專案根目錄上傳到 VPS：

    gcloud compute scp --recurse hotel_scraper.py hotel_engine.py hotel_history.py flight_agent requirements.txt .env.example deploy tests README_HOTELS.md INSTANCE_NAME:~/flight-agent --zone=ZONE

如果是一般 VPS：

    scp -r hotel_scraper.py hotel_engine.py hotel_history.py flight_agent requirements.txt .env.example deploy tests README_HOTELS.md user@VPS_IP:~/flight-agent

不要上傳 .venv、.env、data、artifacts；這些會在 VPS 建立或保留。

## 2. 安裝

SSH 進入 VPS：

    cd ~/flight-agent
    sudo mkdir -p /opt/flight-agent
    sudo cp -r . /opt/flight-agent/
    cd /opt/flight-agent
    sudo bash deploy/install_vps.sh

安裝器會建立無登入服務帳戶 flightagent、Python virtualenv、Playwright Chromium、systemd service 及 6 小時 timer。

## 3. 設定 Telegram

在 VPS 建立檔案：

    sudo nano /opt/flight-agent/.env

內容：

    TELEGRAM_BOT_TOKEN=新的token
    TELEGRAM_CHAT_ID=你的chat_id
    HOTEL_ALERT_DROP_PERCENTAGE=10
    HOTEL_ALERT_COOLDOWN_HOURS=24

保護檔案：

    sudo chown flightagent:flightagent /opt/flight-agent/.env
    sudo chmod 600 /opt/flight-agent/.env

測試 Telegram：

    sudo -u flightagent /opt/flight-agent/.venv/bin/python /opt/flight-agent/hotel_scraper.py --test-telegram

## 4. 啟動及查看狀態

手動執行一次：

    sudo systemctl start hotel-scraper.service

查看結果：

    sudo systemctl status hotel-scraper.timer
    sudo systemctl list-timers hotel-scraper.timer
    sudo journalctl -u hotel-scraper.service -n 100 --no-pager

即時查看：

    sudo journalctl -u hotel-scraper.service -f

## 5. 修改日期、城市或頻率

編輯：

    sudo nano /etc/systemd/system/hotel-scraper.service

修改 ExecStart 的 --city、--check-in、--check-out。修改 timer 的 OnUnitActiveSec 後執行：

    sudo systemctl daemon-reload
    sudo systemctl restart hotel-scraper.timer

不要加 --headful；VPS 沒有圖形桌面。遇到 CAPTCHA、403、Akamai 或 selector 失敗時，程式會保存證據並停止，不會繞過封鎖。
