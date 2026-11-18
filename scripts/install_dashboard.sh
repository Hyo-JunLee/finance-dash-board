#!/bin/zsh
# 대시보드 서버를 launchd에 등록: 로그인 시 자동 실행, 꺼지면 자동 재시작 → http://localhost:8501
# 해제: ./scripts/install_dashboard.sh uninstall
set -e
LABEL="com.kevinlee.market-dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$HOME/.pyenv/versions/3.11.0/bin/python3"
PORT=8501

if [[ "$1" == "uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "해제 완료: $LABEL"
  exit 0
fi

# 같은 포트를 이미 쓰는 서버(터미널에서 직접 띄운 것 등)가 있으면 충돌하므로 먼저 알림
if lsof -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo "⚠ 포트 $PORT 를 이미 쓰는 프로세스가 있습니다. 터미널에서 띄운 streamlit을 먼저 종료(Ctrl+C)하세요:"
  lsof -iTCP:$PORT -sTCP:LISTEN
  exit 1
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
        <string>-m</string><string>streamlit</string><string>run</string>
        <string>$PROJECT/app.py</string>
        <string>--server.port</string><string>$PORT</string>
        <string>--server.headless</string><string>true</string>
        <string>--browser.gatherUsageStats</string><string>false</string>
    </array>
    <key>WorkingDirectory</key><string>$PROJECT</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardOutPath</key><string>$PROJECT/.cache/dashboard.log</string>
    <key>StandardErrorPath</key><string>$PROJECT/.cache/dashboard.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "등록 완료: $LABEL → http://localhost:$PORT (로그: .cache/dashboard.log)"
