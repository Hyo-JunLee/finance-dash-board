"""Nasdaq 경제 캘린더: 과거 날짜의 실제치·컨센서스·이전치 (API 키 불필요).

주의
- date=D로 조회하면 미 동부 D-1일 발표분이 나온다 (하루 밀림) → fetch_day()가 보정
- 전월비·전년비가 같은 이름으로 온다 → 기준값과 가장 가까운 행을 고른다
- 일부 실제치는 원 지수와도 맞지 않아(예: CPI 전년비) 실제치는 FRED를 쓰고 여기선 컨센서스 위주로 사용
"""
import json
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import config

API = "https://api.nasdaq.com/api/calendar/economicevents"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/129.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
DISK_DIR = config.CACHE_DIR / "nasdaq"
DISK_DIR.mkdir(exist_ok=True)

_NUM = re.compile(r"^(-?[\d.]+)\s*([%KMBT]?)")


def parse_value(text: str | None) -> float | None:
    """'0.3%' → 0.3, '197K' → 197, '1,774K' → 1774. 빈칸·&nbsp; → None."""
    m = _NUM.match((text or "").replace(",", "").replace("&nbsp;", "").strip())
    return float(m.group(1)) if m else None


def fetch_day(release_day: date) -> list[dict]:
    """미 동부 release_day에 발표된 미국 지표 목록. 지난 날짜는 디스크에 영구 저장."""
    path = DISK_DIR / f"{release_day.isoformat()}.json"
    us_today = datetime.now(ZoneInfo("America/New_York")).date()
    settled = release_day < us_today - timedelta(days=1)  # 실제치까지 확정된 날짜 (미 동부 기준)
    cached = json.loads(path.read_text()) if path.exists() else None
    age = time.time() - path.stat().st_mtime if path.exists() else 0
    # Nasdaq이 가끔 평일을 통째로 빈 목록으로 준다(11/19·11/23 사례) → 빈 결과는 확정으로 보지 않고 30분마다 다시 받는다
    fresh = 60 if release_day >= us_today else 300  # 오늘 발표분은 1분 (속보용)
    if cached is not None and ((settled and cached) or age < (fresh if cached or not settled else 1800)):
        return cached
    query_day = release_day + timedelta(days=1)  # API 날짜가 하루 밀려 있음
    try:
        r = requests.get(API, params={"date": query_day.isoformat()}, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        rows = (r.json().get("data") or {}).get("rows") or []
    except Exception:
        if cached is not None:
            return cached
        raise
    us = [
        {k: x.get(k) for k in ("gmt", "eventName", "actual", "consensus", "previous")}
        for x in rows if x.get("country") == "United States"
    ]
    if not us and cached:  # 일시적인 빈 응답이 이미 받은 값을 지우지 않게
        path.touch()
        return cached
    path.write_text(json.dumps(us, ensure_ascii=False))
    return us


def find_event(release_day: date, event_name: str, near: float | None) -> dict | None:
    """이름이 같은 행 중 실제치가 near(FRED 값)에 가장 가까운 행.

    반환: {"actual", "consensus", "previous"} (숫자, 없으면 None), "time" (미 동부 "HH:MM")
    """
    # 대소문자 무시: Nasdaq은 PCE 전월비/전년비를 'PCE price index'/'PCE Price index'로 준다
    rows = [x for x in fetch_day(release_day) if x["eventName"].lower() == event_name.lower()]
    rows = [
        {"actual": parse_value(x["actual"]), "consensus": parse_value(x["consensus"]),
         "previous": parse_value(x["previous"]), "time": (x.get("gmt") or "").strip()}
        for x in rows
    ]
    rows = [x for x in rows if x["actual"] is not None or x["consensus"] is not None]
    if not rows:
        return None
    if near is None or len(rows) == 1:
        return rows[0]
    return min(rows, key=lambda x: abs((x["actual"] if x["actual"] is not None else x["previous"] or 0) - near))


def release_time_kst(release_day: date, et_time: str | None) -> datetime | None:
    """미 동부 발표일·시각 → KST. Nasdaq의 'gmt' 필드는 이름과 달리 미 동부 시각이다."""
    if not et_time or not re.fullmatch(r"\d{1,2}:\d{2}", et_time):
        return None
    h, m = map(int, et_time.split(":"))
    et = datetime(release_day.year, release_day.month, release_day.day, h, m, tzinfo=ZoneInfo("America/New_York"))
    return et.astimezone(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)


_SUFFIX = re.compile(r"[%KMBT]$")


def _decimals(text: str | None) -> int:
    m = re.search(r"\.(\d+)", text or "")
    return len(m.group(1)) if m else 0


def match_actual(release_et: datetime, title: str, forecast: str | None, previous: str | None) -> str | None:
    """ForexFactory 일정 1건에 해당하는 Nasdaq 실제치를 FF 표기 형식으로 반환 (없으면 None).

    두 사이트의 지표 이름이 달라(예: 'ISM Services PMI' ↔ 'ISM Non-Manufacturing PMI')
    같은 날 ±5분에 발표된 항목 중 이전치·예상치 일치와 이름 유사도로 가장 그럴듯한 것을 고른다.
    """
    from difflib import SequenceMatcher

    rows = []
    for x in fetch_day(release_et.date()):
        t = (x.get("gmt") or "").strip()
        if not re.fullmatch(r"\d{1,2}:\d{2}", t) or parse_value(x["actual"]) is None:
            continue
        h, m = map(int, t.split(":"))
        if abs((h * 60 + m) - (release_et.hour * 60 + release_et.minute)) <= 5:
            rows.append(x)
    fc, prev = parse_value(forecast), parse_value(previous)

    def same(a, b):
        return a is not None and b is not None and abs(a - b) < 1e-6

    def score(x):
        s = SequenceMatcher(None, title.lower(), x["eventName"].lower()).ratio()
        s += 2 if same(prev, parse_value(x["previous"])) else 0
        s += 2 if same(fc, parse_value(x["consensus"])) else 0
        return s

    if not rows:
        return None
    best = max(rows, key=score)
    if score(best) < 0.9:  # 이름도 안 비슷하고 수치도 안 맞으면 다른 지표로 본다
        return None
    value = parse_value(best["actual"])
    ref = forecast or previous or ""
    suffix = (_SUFFIX.search(ref.strip()) or _SUFFIX.search(best["actual"].strip()) or [""])[0]
    return f"{value:,.{_decimals(ref) if ref else _decimals(best['actual'])}f}{suffix}"
