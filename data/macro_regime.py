"""매크로 국면·통화정책 일정·서프라이즈 누적 (규칙 기반, AI 없음).

국면 (성장 × 물가 사분면, 월별)
- 성장 축 = 시카고 연은 활동지수 3개월 평균(CFNAIMA3): 0 = 추세 성장, 양수 = 추세 이상
- 물가 축 = 근원 PCE 3개월 연율 − 2%(연준 목표): 양수 = 목표 위
- 골디락스(성장↑·물가↓) / 리플레이션·과열(성장↑·물가↑) / 스태그플레이션(성장↓·물가↑) / 디플레이션·둔화(성장↓·물가↓)
FOMC 일정: 연준 공식 페이지(federalreserve.gov) 연도별 표, 하루 한 번
"""
import json
import re
from datetime import date

import pandas as pd
import requests
import streamlit as st

import config
from data.fred import fetch_series, transform

INFLATION_TARGET = 2.0
QUADRANTS = {  # (성장 ≥ 0, 물가 ≥ 0)
    (True, False): ("골디락스", "성장 추세 이상 · 물가 목표 이하 — 위험자산에 가장 우호적"),
    (True, True): ("리플레이션·과열", "성장 추세 이상 · 물가 목표 위 — 긴축 압력, 금리 상승 쪽"),
    (False, True): ("스태그플레이션", "성장 추세 이하 · 물가 목표 위 — 정책 딜레마, 위험자산에 가장 불리"),
    (False, False): ("디플레이션·둔화", "성장 추세 이하 · 물가 목표 이하 — 완화 기대, 금리 하락 쪽"),
}


def quadrant(growth: float, inflation_gap: float) -> tuple[str, str]:
    return QUADRANTS[(growth >= 0, inflation_gap >= 0)]


def regime_history(months: int = 12) -> pd.DataFrame:
    """월별 [성장(CFNAIMA3), 물가(근원 PCE 3개월 연율 − 2%), 국면]. 두 값이 모두 있는 달만."""
    growth = fetch_series("CFNAIMA3").dropna()
    infl = transform(fetch_series("PCEPILFE"), "ann3").dropna() - INFLATION_TARGET
    df = pd.concat({"성장": growth, "물가": infl}, axis=1).dropna().tail(months)
    df["국면"] = [quadrant(g, i)[0] for g, i in zip(df["성장"], df["물가"])]
    return df


# ── FOMC 일정 ──────────────────────────────────────────
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FOMC_DISK = config.CACHE_DIR / "fomc_meetings.json"
_MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                         "September", "October", "November", "December"], 1)}


def parse_fomc(html: str) -> list[date]:
    """연도별 'YYYY FOMC Meetings' 표에서 회의 마지막 날(결정 발표일). '*' = 경제전망(SEP) 포함."""
    out = []
    heads = [(m.start(), int(m.group(1))) for m in re.finditer(r"(\d{4}) FOMC Meetings", html)]
    for k, (pos, year) in enumerate(heads):
        seg = html[pos: heads[k + 1][0] if k + 1 < len(heads) else len(html)]
        for mon, days in re.findall(r"fomc-meeting__month[^>]*>\s*<strong>([A-Za-z/]+)</strong>.*?"
                                    r"fomc-meeting__date[^>]*>([^<]+)<", seg, re.S):
            last_mon = mon.split("/")[-1]
            nums = re.findall(r"\d+", days)
            if last_mon in _MONTHS and nums:
                try:
                    out.append(date(year, _MONTHS[last_mon], int(nums[-1])))
                except ValueError:
                    continue
    return sorted(set(out))


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fomc_meetings() -> list[date]:
    try:
        r = requests.get(FOMC_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        days = parse_fomc(r.text)
        if days:
            FOMC_DISK.write_text(json.dumps([d.isoformat() for d in days]))
            return days
    except Exception:
        pass
    try:
        return [date.fromisoformat(d) for d in json.loads(FOMC_DISK.read_text())]
    except Exception:
        return []


def next_fomc(today: date) -> date | None:
    nxt = [d for d in fomc_meetings() if d >= today]
    return nxt[0] if nxt else None


# ── 서프라이즈 누적 (성장 vs 물가) ─────────────────────
GROWTH_FIELDS = ("고용", "주택", "성장·소비", "심리·제조업")


def surprise_index(sup: pd.DataFrame) -> pd.DataFrame:
    """일별 누적 서프라이즈 (상회 +1, 하회 −1의 누적 합). 열 = 성장(고용·주택·성장·소비·심리·제조업), 물가.
    지표 단위가 달라 크기 대신 방향만 센다 (씨티 서프라이즈 지수의 단순판)."""
    if sup.empty:
        return pd.DataFrame()
    d = sup.assign(day=sup["발표(KST)"].dt.normalize(), s=sup["_diff"].apply(lambda v: (v > 0) - (v < 0)),
                   group=sup["분야"].map(lambda f: "물가" if f == "물가" else "성장" if f in GROWTH_FIELDS else None))
    daily = d.dropna(subset=["group"]).pivot_table(index="day", columns="group", values="s", aggfunc="sum").fillna(0)
    return daily.sort_index().cumsum()
