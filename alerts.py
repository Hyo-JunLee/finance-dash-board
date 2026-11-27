"""10분마다 실행 (launchd, scripts/install_alerts.sh): 이례적 변동 알림 + 미국 지표 발표 AI 해석.

1) 이례적 변동 휴대폰 알림 (텔레그램 봇)
- 오늘의 시황 카드 항목 중 오늘 변화가 평소 하루 변동의 Z_ALERT배(⚡와 같은 기준) 이상이면 보낸다
- 같은 항목·같은 기준일·같은 방향은 한 번만 (.cache/alert_state.json)
- 시간대 제한 없이 생기는 즉시 보냄 (미국장 새벽 변동 포함)
- .env에 TELEGRAM_BOT_TOKEN·TELEGRAM_CHAT_ID가 없으면 보내지 않는다
2) 🧠 발표 해석 (data/release_insight.py): 중요 지표 발표 30분 뒤 한 번 claude -p 로 해석 → 화면·텔레그램
3) 미 국채 2년·10년 큰 변동 (data/rate_alerts.py): 하루 ±8/12/16bp, 30분 ±4bp, 10Y−2Y 하루 ±6bp
   (이 세 항목은 1)의 이례적 변동 점검에서 뺀다)

  python alerts.py            # 점검 후 보냄
  python alerts.py --test     # 테스트 메시지 1건
  python alerts.py --chat-id  # 봇에게 말을 건 내 채팅 id 찾기
"""
import json
import logging
import sys
import warnings
from datetime import datetime

warnings.filterwarnings("ignore")
logging.getLogger("streamlit").setLevel(logging.ERROR)
import streamlit.runtime.caching.cache_data_api  # noqa: E402,F401  (아래 경고 로거를 먼저 만들어 두고 끔)
logging.getLogger("streamlit.runtime.caching.cache_data_api").disabled = True

import requests  # noqa: E402

import config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("alerts")
STATE = config.CACHE_DIR / "alert_state.json"
RATE_KEYS = {"us2y", "us10y", "us2s10s"}
DASHBOARD_URL = config.DASHBOARD_URL  # 휴대폰에서 바로 열 주소 (.env, 예: Tailscale 주소)


def send(text: str) -> None:
    r = requests.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage", timeout=15,
                      data={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": "true"})
    r.raise_for_status()


def alert_text(snap: dict) -> str:
    rate = snap["kind"] in ("rate", "spread")
    chg = f"{snap['chg'] * 100:+.1f}bp" if rate else f"{snap['pct']:+.2f}%"
    last = f"{snap['last']:.3f}%" if rate else f"{snap['last']:,.2f}"
    up = snap["z"] > 0
    return (f"⚡ {snap['label']} 이례적 {'상승' if up else '하락'} {chg} ({last})\n"
            f"평소 하루 변동의 {abs(snap['z']):.1f}배 · 기준 {snap['asof']:%m/%d %H:%M}")


def check(now: datetime | None = None) -> list[str]:
    from data.market import SUMMARY_ITEMS, get_snapshot
    from ui import Z_ALERT
    now = now or datetime.now()
    try:
        sent = set(json.loads(STATE.read_text()))
    except Exception:
        sent = set()
    out, new_keys = [], []
    for key in SUMMARY_ITEMS:
        if key in RATE_KEYS:  # 미 2년·10년·10Y−2Y는 금리 전용 알림(data/rate_alerts.py)이 맡음
            continue
        try:
            snap = get_snapshot(key)
        except Exception:
            continue
        if not snap or snap["z"] != snap["z"] or abs(snap["z"]) < Z_ALERT:
            continue
        k = f"{key}:{snap['asof']:%Y-%m-%d}:{'+' if snap['z'] > 0 else '-'}"
        if k not in sent:
            out.append(alert_text(snap))
            new_keys.append(k)
    if out:
        send("\n\n".join(out) + (f"\n\n📊 {DASHBOARD_URL}" if DASHBOARD_URL else ""))
        STATE.write_text(json.dumps(sorted(sent | set(new_keys))[-300:]))
        log.info(f"알림 {len(out)}건 보냄: {', '.join(new_keys)}")
    return out


def main() -> int:
    telegram = bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)
    if "--chat-id" in sys.argv and config.TELEGRAM_BOT_TOKEN:
        r = requests.get(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates", timeout=15).json()
        ids = {(u["message"]["chat"]["id"], u["message"]["chat"].get("first_name", ""))
               for u in r.get("result", []) if "message" in u}
        print(ids or "봇에게 아무 메시지나 먼저 보낸 뒤 다시 실행하세요")
        return 0
    if "--test" in sys.argv and telegram:
        send(f"✅ 시황 대시보드 알림 연결 확인 ({datetime.now():%m/%d %H:%M})" + (f"\n📊 {DASHBOARD_URL}" if DASHBOARD_URL else ""))
        print("테스트 메시지를 보냈습니다")
        return 0
    failed = False
    if telegram:
        try:
            check()
        except Exception as e:
            log.exception(f"알림 점검 실패: {e}")
            failed = True
        try:
            from data.rate_alerts import check_and_send
            check_and_send(send, DASHBOARD_URL, log=log)
        except Exception as e:
            log.exception(f"금리 알림 실패: {e}")
            failed = True
    try:  # 발표 해석은 텔레그램 설정이 없어도 만들어 화면에 둔다
        from data.release_insight import check_and_run
        check_and_run(send=send if telegram else None, log=log)
    except Exception as e:
        log.exception(f"발표 해석 실패: {e}")
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
