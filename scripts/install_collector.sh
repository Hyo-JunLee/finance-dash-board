#!/bin/zsh
# 수집기(collect.py)를 launchd에 등록: 3시간마다 + 로그인/부팅 시 1회 실행
# 해제: ./scripts/install_collector.sh uninstall
set -e
LABEL="com.kevinlee.market-dashboard-collector"
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
        <string>$PROJECT/collect.py</string>
    </array>
    <key>WorkingDirectory</key><string>$PROJECT</string>
    <key>StartInterval</key><integer>10800</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>$PROJECT/collected/collect.log</string>
    <key>StandardErrorPath</key><string>$PROJECT/collected/collect.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "등록 완료: $LABEL (3시간마다 실행, 로그: collected/collect.log)"
