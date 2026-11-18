#!/bin/zsh
# AI 요약(summarize.py: 텔레그램 리서치 + 뉴스 이슈)을 launchd에 등록: 매일 08:30 · 13:30 · 18:30
# 맥이 잠자기였다면 깨어난 뒤 밀린 실행을 한 번 한다. 해제: zsh scripts/install_summarizer.sh uninstall
set -e
LABEL="com.kevinlee.market-dashboard-summarizer"
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
        <string>$PROJECT/summarize.py</string>
    </array>
    <key>WorkingDirectory</key><string>$PROJECT</string>
    <key>EnvironmentVariables</key>
    <dict>
        <!-- claude CLI(/opt/homebrew/bin)와 그 실행에 필요한 node를 찾을 수 있게 -->
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Hour</key><integer>13</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>30</integer></dict>
    </array>
    <key>StandardOutPath</key><string>$PROJECT/collected/summarize.log</string>
    <key>StandardErrorPath</key><string>$PROJECT/collected/summarize.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "등록 완료: $LABEL (매일 08:30·13:30·18:30, 로그: collected/summarize.log)"
