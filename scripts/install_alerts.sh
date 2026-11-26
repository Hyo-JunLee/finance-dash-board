#!/bin/zsh
# 이례적 변동 휴대폰 알림(alerts.py)을 launchd에 등록: 10분마다 점검 (.env에 텔레그램 토큰·채팅 id가 있을 때만 보냄)
# 해제: ./scripts/install_alerts.sh uninstall
set -e
LABEL="com.kevinlee.market-dashboard-alerts"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$HOME/.pyenv/versions/3.11.0/bin/python3"

if [[ "$1" == "uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "해제 완료: $LABEL"
  exit 0
fi

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$PROJECT/alerts.py</string>
    </array>
    <key>WorkingDirectory</key><string>$PROJECT</string>
    <key>StartInterval</key><integer>600</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>$PROJECT/collected/alerts.log</string>
    <key>StandardErrorPath</key><string>$PROJECT/collected/alerts.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "등록 완료: $LABEL (10분마다 점검, 로그: collected/alerts.log)"
