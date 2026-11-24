"""데이터 소스 상태 점검. 대시보드 '데이터 상태' 화면과 수집기 알림이 같이 쓴다.

지금까지의 장애는 대부분 에러 없이 '옛날 값'을 보여주는 형태였으므로(Yahoo 일봉 NaN, FRED 저장본 대체,
iCloud로 멈춘 수집기) 성공 여부뿐 아니라 '마지막 데이터가 얼마나 오래됐는지'를 함께 본다.

상태: ok(정상) / warn(주의: 늦어짐) / fail(실패: 못 받음·너무 오래됨)
"""
import re
import subprocess
import time
from datetime import date, datetime, timedelta

import pandas as pd

import config

OK, WARN, FAIL = "ok", "warn", "fail"
ICON = {OK: "✅", WARN: "⚠️", FAIL: "❌"}


def _bdays_since(d) -> int:
    """d 이후 지난 평일 수 (주말 제외, 공휴일은 고려 안 함 → 임계값에 여유를 둔다)."""
    d = pd.Timestamp(d).normalize()
    return max(len(pd.bdate_range(d, pd.Timestamp.now().normalize())) - 1, 0)


def _age_status(bdays: int, warn_after: int, fail_after: int) -> str:
    return OK if bdays <= warn_after else WARN if bdays <= fail_after else FAIL


def _result(name, used_in, status, last, detail):
    return {"소스": name, "쓰는 곳": used_in, "상태": status, "마지막 데이터": last, "설명": detail}


# ── 개별 점검 ────────────────────────────────────────
def check_treasury():
    from data.rates import get_treasury_curve
    df = get_treasury_curve(date.today() - timedelta(days=20))
    last = df.index[-1]
    b = _bdays_since(last)
    return _result("미 재무부 국채 금리", "오늘의 시황·매크로·채권", _age_status(b, 2, 5),
                   f"{last:%Y-%m-%d}", f"종가 기준 {b}영업일 전")


def check_cnbc():
    from data.rates import get_cnbc_quotes
    q = get_cnbc_quotes()
    ts = max(t for _, t in q.values())
    hours = (pd.Timestamp.now() - pd.Timestamp(ts)).total_seconds() / 3600
    weekend = datetime.now().weekday() >= 5
    status = OK if hours <= (60 if weekend else 6) else WARN if hours <= 72 else FAIL
    return _result("CNBC 실시간 금리", "오늘의 시황·매크로", status, f"{ts:%m/%d %H:%M}",
                   f"{len(q)}개 심볼, 최근 체결 {hours:.0f}시간 전")


def check_fred():
    from data.fred import DISK_DIR, fetch_series
    s = fetch_series("DGS10")
    b = _bdays_since(s.index[-1])
    # 받기에 실패하면 저장본을 쓰므로, 저장본이 마지막으로 갱신된 시점도 본다
    files = list(DISK_DIR.glob("*.pkl"))
    newest = max((f.stat().st_mtime for f in files), default=0)
    stale_h = (time.time() - newest) / 3600
    status = _age_status(b, 3, 6)
    detail = f"10년물 기준 {b}영업일 전 · 저장본 갱신 {stale_h:.0f}시간 전"
    if stale_h > 24:
        status, detail = WARN if status == OK else status, detail + " (새로 받기 실패 중일 수 있음)"
    return _result("FRED 경제지표", "매크로·채권·오늘의 시황", status, f"{s.index[-1]:%Y-%m-%d}", detail)


def check_yahoo():
    import yfinance as yf
    d = yf.download("^GSPC", period="10d", auto_adjust=True, progress=False)["Close"]
    d = d.iloc[:, 0] if isinstance(d, pd.DataFrame) else d
    if d.empty:
        return _result("Yahoo 시세", "오늘의 시황·미국 증시", FAIL, "-", "S&P 500 데이터 없음")
    nan_last = pd.isna(d.iloc[-1])
    valid = d.dropna()
    b = _bdays_since(valid.index[-1])
    status = _age_status(b, 2, 5)
    detail = f"S&P 500 기준 {b}영업일 전"
    if nan_last:  # 10/6 KOSPI처럼 마지막 일봉이 빈 값으로 오는 경우
        status, detail = WARN, detail + " · 마지막 일봉이 빈 값(NaN)"
    return _result("Yahoo 시세", "오늘의 시황·미국 증시", status, f"{valid.index[-1]:%Y-%m-%d}", detail)


def check_naver():
    from data.market import get_naver_quote
    value, ts, is_open = get_naver_quote("KOSPI")
    b = _bdays_since(ts)
    return _result("네이버 (국내 지수)", "오늘의 시황·국내 증시", _age_status(b, 1, 4), f"{ts:%m/%d %H:%M}",
                   f"KOSPI {value:,.2f} · {'장중' if is_open else '마감'}")


def check_krx():
    if not config.KRX_API_KEY:
        return _result("KRX Open API", "국내 증시·채권(한국)", FAIL, "-", "KRX_API_KEY 없음")
    from data.krx import latest_trading_day
    d = latest_trading_day()
    b = _bdays_since(d)
    return _result("KRX Open API", "국내 증시·채권(한국)", _age_status(b, 2, 5), f"{d:%Y-%m-%d}",
                   f"다음 영업일 공개 · {b}영업일 전")


def check_nasdaq():
    from data.consensus import fetch_day
    d = pd.Timestamp.now(tz="America/New_York").normalize() - pd.offsets.BDay(1)
    rows = fetch_day(d.date())
    return _result("Nasdaq 경제 캘린더", "오늘의 시황(예상·실제치)·매크로", OK if rows else WARN,
                   f"{d:%Y-%m-%d}", f"미국 지표 {len(rows)}건")


def check_forexfactory():
    from data.econ_calendar import ff_fetched_at
    t = ff_fetched_at()
    if t is None:
        return _result("ForexFactory 일정", "오늘의 시황(이번 주 일정)", FAIL, "-", "받은 적 없음")
    hours = (datetime.now() - t).total_seconds() / 3600
    return _result("ForexFactory 일정", "오늘의 시황(이번 주 일정)", OK if hours <= 4 else WARN if hours <= 24 else FAIL,
                   f"{t:%m/%d %H:%M}", f"{hours:.0f}시간 전 수신 (1시간마다 갱신)")


def check_news():
    from data import news
    ok_feeds = sum(1 for f in news.FEEDS if news._fetch_feed(f))
    ratio = ok_feeds / len(news.FEEDS)
    return _result("뉴스 RSS", "뉴스·오늘의 시황", OK if ratio >= 0.8 else WARN if ratio >= 0.5 else FAIL,
                   datetime.now().strftime("%m/%d %H:%M"), f"피드 {ok_feeds}/{len(news.FEEDS)}개 정상")


def check_telegram():
    """AI 요약(리서치·뉴스, 하루 3회)이 제때 만들어지는지 + 채널 글을 읽을 수 있는지.
    밤사이(18:30→08:30) 14시간 공백은 정상이라 15시간까지 정상으로 본다."""
    import json
    from data.telegram import fetch_channel
    name, used = "AI 요약 (리서치·뉴스·바이사이드)", "오늘의 시황 (이슈·리서치·관점 요약)"
    if not fetch_channel(next(iter(config.TELEGRAM_CHANNELS))):
        return _result(name, used, WARN, "-", "텔레그램 채널 글을 읽지 못함 (웹 미리보기 구조 변경?)")
    ages, parts = [], []
    for label, path in (("리서치", config.SUMMARY_FILE), ("뉴스", config.NEWS_SUMMARY_FILE),
                        ("바이사이드", config.BUYSIDE_SUMMARY_FILE)):
        try:
            made = datetime.fromisoformat(json.loads(path.read_text())["generated_at"])
        except (FileNotFoundError, ValueError, KeyError):
            return _result(name, used, WARN, "-", f"{label} 요약 파일 없음")
        hours = (datetime.now() - made).total_seconds() / 3600
        ages.append((hours, made))
        parts.append(f"{label} {hours:.0f}시간 전")
    oldest_h, oldest = max(ages)
    status = OK if oldest_h <= 15 else WARN if oldest_h <= 30 else FAIL
    for label, path in (("아침 인사이트", config.INSIGHT_FILE), ("10년물 판단", config.BOND_VIEW_FILE)):
        try:  # 아침 08:30 하루 1회라 26시간까지 정상
            made = datetime.fromisoformat(json.loads(path.read_text())["generated_at"])
            hours = (datetime.now() - made).total_seconds() / 3600
            parts.append(f"{label} {hours:.0f}시간 전")
            if hours > 26 and status == OK:
                status = WARN
        except (FileNotFoundError, ValueError, KeyError):
            parts.append(f"{label} 없음")
    return _result(name, used, status, f"{oldest:%m/%d %H:%M}", " · ".join(parts) + " (하루 3회 08:30·13:30·18:30)")


def check_acm():
    from data.bonds import acm_10y
    df = acm_10y()
    b = _bdays_since(df.index[-1])
    return _result("뉴욕 연은 ACM 텀 프리미엄", "채권(곡선 분해)", _age_status(b, 7, 15), f"{df.index[-1]:%Y-%m-%d}",
                   f"{b}영업일 전 (원래 며칠 늦게 공개)")


def check_auctions():
    """미 국채 입찰은 거의 매주 있으므로 결과가 3주 넘게 없거나, 지난 입찰의 결과가 비어 있으면 이상.
    (2026-10 말 FiscalData가 결과 갱신을 멈춘 걸 '정상'으로 놓친 뒤 강화)"""
    from data.bonds import auctions
    df = auctions(3)
    done = df[df["평가"] != "예정"]
    last = done["입찰일"].max() if len(done) else None
    if last is None:
        return _result("재무부 입찰 결과", "채권(국채 입찰)", FAIL, "-", "최근 3개월 결과 없음")
    days = (pd.Timestamp.now().normalize() - last).days
    missing = df[(df["평가"] == "예정") & (df["입찰일"] < pd.Timestamp.now().normalize() - pd.Timedelta(days=2))]
    status = OK if days <= 21 else WARN if days <= 35 else FAIL
    detail = f"마지막 결과 {days}일 전 · 최근 3개월 {len(done)}건"
    if len(missing):
        status = WARN if status == OK else status
        detail += f" · 결과 없는 지난 입찰 {len(missing)}건"
    return _result("재무부 입찰 결과", "채권(국채 입찰)", status, f"{last:%Y-%m-%d}", detail)


def check_collector():
    log = config.BASE_DIR / "collected" / "collect.log"
    if not log.exists():
        return _result("수집기 (3시간마다)", "collected/ CSV 저장", FAIL, "-", "실행 기록 없음")
    ends = re.findall(r"^(\S+ \S+),\d+ \[INFO\] === 수집 종료 \(실패 (\d+)개", log.read_text(), re.M)
    if not ends:
        return _result("수집기 (3시간마다)", "collected/ CSV 저장", FAIL, "-", "완료 기록 없음")
    last, failed = datetime.fromisoformat(ends[-1][0]), int(ends[-1][1])
    hours = (datetime.now() - last).total_seconds() / 3600
    status = OK if hours <= 4 else WARN if hours <= 8 else FAIL
    if failed and status == OK:
        status = WARN
    return _result("수집기 (3시간마다)", "collected/ CSV 저장", status, f"{last:%m/%d %H:%M}",
                   f"{hours:.1f}시간 전 완료 · 실패 단계 {failed}개")


def check_dashboard_service():
    out = subprocess.run(["launchctl", "print", f"gui/{_uid()}/com.kevinlee.market-dashboard"],
                         capture_output=True, text=True).stdout
    running = "state = running" in out
    return _result("대시보드 자동 실행", "재부팅 후 자동 시작", OK if running else WARN, "-",
                   "실행 중" if running else "launchd에 등록되지 않았거나 멈춤 (터미널에서 직접 실행 중일 수 있음)")


def _uid() -> int:
    import os
    return os.getuid()


CHECKS = [check_collector, check_dashboard_service, check_treasury, check_cnbc, check_fred, check_yahoo,
          check_naver, check_krx, check_nasdaq, check_forexfactory, check_news, check_telegram, check_acm,
          check_auctions]


NAMES = {"collector": "수집기 (3시간마다)", "dashboard_service": "대시보드 자동 실행", "treasury": "미 재무부 국채 금리",
         "cnbc": "CNBC 실시간 금리", "fred": "FRED 경제지표", "yahoo": "Yahoo 시세", "naver": "네이버 (국내 지수)",
         "krx": "KRX Open API", "nasdaq": "Nasdaq 경제 캘린더", "forexfactory": "ForexFactory 일정",
         "news": "뉴스 RSS", "telegram": "AI 요약 (리서치·뉴스·바이사이드)", "acm": "뉴욕 연은 ACM 텀 프리미엄", "auctions": "재무부 입찰 결과"}


def check_all() -> pd.DataFrame:
    rows = []
    for fn in CHECKS:
        try:
            rows.append(fn())
        except Exception as e:
            name = NAMES.get(fn.__name__.removeprefix("check_"), fn.__name__)
            rows.append(_result(name, "-", FAIL, "-", f"{type(e).__name__}: {str(e)[:120]}"))
    return pd.DataFrame(rows)


# ── 알림 (수집기가 매 실행 끝에 호출) ──────────────────
STATE_FILE = config.CACHE_DIR / "health_state.json"


def notify(title: str, message: str) -> None:
    """macOS 알림 센터. 실패해도 수집에는 영향 없게 조용히 넘어간다."""
    script = f'display notification {_osa(message)} with title {_osa(title)}'
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)


def _osa(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def alert_changes(df: pd.DataFrame) -> list[str]:
    """직전 점검 대비 새로 나빠진 소스·회복된 소스만 알림 (같은 장애로 3시간마다 반복 알림하지 않도록)."""
    import json
    prev = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    now = dict(zip(df["소스"], df["상태"]))
    worse = [n for n, st in now.items() if st != OK and prev.get(n, OK) == OK]
    recovered = [n for n, st in now.items() if st == OK and prev.get(n, OK) != OK]
    STATE_FILE.write_text(json.dumps(now, ensure_ascii=False))
    messages = []
    if worse:
        detail = df.set_index("소스").loc[worse]
        messages.append("문제 발생: " + " / ".join(f"{n}({ICON[s]} {d})" for n, s, d in
                                               zip(worse, detail["상태"], detail["설명"])))
    if recovered:
        messages.append("회복: " + ", ".join(recovered))
    for m in messages:
        notify("시황 대시보드 데이터 상태", m[:230])
    return messages
