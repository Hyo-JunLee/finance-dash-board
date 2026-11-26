"""국내 주요 일정: 한국은행 금통위(통화정책방향 결정회의) + 수출입동향(통상 매월 1일 09:00).

- 금통위: 한국은행 홈페이지 연도별 일정 표 (공개 페이지). 내년 일정은 보통 연말에 공개 → 공개되면 자동 반영
- 하루 한 번 받아 디스크에 저장, 실패하면 저장본 사용
- 발표일이 매달 바뀌는 지표(소비자물가 등)는 틀릴 위험이 있어 넣지 않는다
"""
import json
import re
from datetime import date, timedelta

import requests
import streamlit as st

import config

BOK_URL = "https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do"
DISK = config.CACHE_DIR / "bok_meetings.json"
_ROW = re.compile(r'<th scope="row">\s*(\d{1,2})월\s*(\d{1,2})일')


def parse_meetings(html: str, year: int) -> list[date]:
    return sorted({date(year, int(m), int(d)) for m, d in _ROW.findall(html)})


def _fetch_year(year: int) -> list[date]:
    r = requests.get(BOK_URL, params={"mtgSe": "A", "menuNo": "200755", "pYear": year},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    return parse_meetings(r.text, year)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def bok_meetings(this_year: int) -> list[date]:
    """올해·내년 금통위 날짜. this_year는 캐시 구분용."""
    try:
        saved = {date.fromisoformat(d) for d in json.loads(DISK.read_text())}
    except Exception:
        saved = set()
    got = set()
    for y in (this_year, this_year + 1):
        try:
            got |= set(_fetch_year(y))
        except Exception:
            pass
    if got:
        saved |= got
        DISK.write_text(json.dumps(sorted(d.isoformat() for d in saved)))
    return sorted(saved)


def upcoming(today: date, meetings: list[date], days: int = 45) -> list[dict]:
    """오늘부터 days일 안의 국내 일정 [{날짜, 일정, 비고}] (날짜순)."""
    end = today + timedelta(days=days)
    events = [{"날짜": d, "일정": "한은 금통위 (기준금리 결정)", "비고": "통상 오전 10시 전후 발표"}
              for d in meetings if today <= d <= end]
    first = date(today.year, today.month, 1)
    for k in range(3):  # 이번 달·다음 달·다다음 달 1일
        d = date(first.year + (first.month - 1 + k) // 12, (first.month - 1 + k) % 12 + 1, 1)
        if today <= d <= end:
            events.append({"날짜": d, "일정": "수출입동향 (산업통상자원부)", "비고": "통상 매월 1일 09:00"})
    return sorted(events, key=lambda e: e["날짜"])
