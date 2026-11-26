"""미국 대형주 실적 발표 일정 (Nasdaq 실적 캘린더, 비공식 API — 경제지표 캘린더와 같은 출처).

- 지난 날짜: 실제 EPS·서프라이즈(%) / 앞으로의 날짜: 예상 EPS·발표 시점(장 전·장 후)
- 대상: 히트맵 대형주 + TSM + 시가총액 1,000억 달러 이상
- Nasdaq이 가끔 빈 응답(HTTP 200)을 주므로 빈 결과는 저장하지 않는다
"""
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st

from data.consensus import _HEADERS
from data.global_markets import KR_NAMES, STOCK_SECTOR

API = "https://api.nasdaq.com/api/calendar/earnings"
WATCH = set(STOCK_SECTOR) | {"TSM"}
MIN_CAP = 100e9  # 감시 목록 밖이라도 이보다 크면 포함
TIMING = {"time-pre-market": "장 전", "time-after-hours": "장 후"}
# 한국 시간으로 대략 언제 결과를 보게 되는지 (미국 날짜 기준)
KST_HINT = {"장 전": "그날 밤 21~22시", "장 후": "다음 날 아침 6~7시"}


def _money(v) -> float:
    try:
        return float(str(v).replace("$", "").replace(",", "").replace("(", "-").replace(")", ""))
    except ValueError:
        return float("nan")


def parse_rows(rows: list[dict], day: date) -> pd.DataFrame:
    """Nasdaq 응답 행 → 표. 감시 종목·초대형주만 남긴다."""
    out = []
    for r in rows or []:
        sym, cap = r.get("symbol", ""), _money(r.get("marketCap"))
        if sym not in WATCH and not cap >= MIN_CAP:
            continue
        timing = TIMING.get(r.get("time"), "미정")
        out.append({"날짜": pd.Timestamp(day), "시점": timing, "티커": sym,
                    "종목": KR_NAMES.get(sym, r.get("name", sym)), "시총($bn)": cap / 1e9,
                    "예상 EPS": _money(r.get("epsForecast")), "실제 EPS": _money(r.get("eps")),
                    "서프라이즈(%)": _money(r.get("surprise")), "분기": r.get("fiscalQuarterEnding", "")})
    return pd.DataFrame(out)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_day(day: date) -> pd.DataFrame:
    if day.weekday() >= 5:
        return pd.DataFrame()
    r = requests.get(API, params={"date": day.isoformat()}, headers=_HEADERS, timeout=15)
    r.raise_for_status()
    rows = ((r.json() or {}).get("data") or {}).get("rows") or []
    if not rows:  # 빈 응답은 캐시에 오래 남기지 않도록 예외로 (다음 조회 때 다시 시도)
        raise ValueError(f"{day} 실적 일정 빈 응답")
    return parse_rows(rows, day)


@st.cache_data(ttl=30 * 60, show_spinner=False)
def upcoming(today: date, back: int = 4, ahead: int = 9) -> pd.DataFrame:
    """미국 날짜 기준 최근 back일 ~ 앞으로 ahead일 대형주 실적. 날짜·시점 순."""
    from concurrent.futures import ThreadPoolExecutor

    def one(d):
        try:
            return fetch_day(d)
        except Exception:
            return pd.DataFrame()
    days = [today + timedelta(days=i) for i in range(-back, ahead + 1)]
    with ThreadPoolExecutor(max_workers=6) as ex:
        frames = list(ex.map(one, days))
    if not any(len(f) for f in frames):  # 전부 실패·빈 응답이면 예외 → 빈 결과가 30분 캐시에 남지 않게
        raise RuntimeError("실적 일정을 하나도 받지 못함")
    df = pd.concat([f for f in frames if len(f)], ignore_index=True)
    order = {"장 전": 0, "미정": 1, "장 후": 2}
    return df.sort_values(["날짜", "시점"], key=lambda c: c.map(order) if c.name == "시점" else c).reset_index(drop=True)
