"""FOMC 회의별 금리 변경 확률 (CME FedWatch와 같은 방식, 30일 연방기금금리 선물로 직접 계산).

- 월물 가격 P → 그 달 평균 실효금리 기대 R = 100 − P (Yahoo ZQ{월코드}{연도}.CBT, 지연 시세)
- 회의가 있는 달: R = (회의 전 금리 S × 회의일까지 일수 + 회의 후 금리 E × 나머지 일수) / 그 달 일수
  · S = 직전 달에 회의가 없으면 직전 달 R, 있으면 직전 회의의 E
  · E = 다음 달에 회의가 없으면 다음 달 R, 있으면 위 식을 E로 풀어 계산
- 회의 변화 = E − S → 25bp 단위로 나눠 확률 (예: +16bp = 인상 25bp 64% · 동결 36%)
- CME 내부 데이터(실효금리 등)와 미세하게 달라 소수점 차이는 날 수 있음. 먼 월물은 거래가 적어 덜 정확
"""
import calendar
from datetime import date

import pandas as pd
import streamlit as st
import yfinance as yf

MONTH_CODES = "FGHJKMNQUVXZ"
STEP = 0.25


def _symbol(y: int, m: int) -> str:
    return f"ZQ{MONTH_CODES[m - 1]}{y % 100:02d}.CBT"


def _months(today: date, n: int) -> list[tuple[int, int]]:
    return [(today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1) for k in range(n)]


@st.cache_data(ttl=600, show_spinner=False)
def futures_rates(today: date, n: int = 10) -> dict[tuple[int, int], float]:
    """{(연, 월): 시장이 반영한 그 달 평균 실효금리 %} — 이번 달부터 n개월."""
    months = _months(today, n)
    syms = {_symbol(y, m): (y, m) for y, m in months}
    df = yf.download(list(syms), period="10d", progress=False, auto_adjust=False)["Close"]
    if isinstance(df, pd.Series):
        df = df.to_frame(next(iter(syms)))
    out = {}
    for sym, ym in syms.items():
        if sym in df and df[sym].dropna().size:
            out[ym] = 100 - float(df[sym].dropna().iloc[-1])
    if not out:
        raise RuntimeError("Yahoo 연방기금금리 선물 빈 응답")
    return out


def split(move_bp: float) -> dict[str, float]:
    """회의 변화(bp) → {변화 크기(bp): 확률}. 예: +16 → {0: 0.36, 25: 0.64}, +40 → {25: 0.4, 50: 0.6}."""
    steps = abs(move_bp) / (STEP * 100)
    k = int(steps)
    frac = steps - k
    sign = 1 if move_bp >= 0 else -1
    out = {sign * k * 25: 1 - frac}
    if frac > 1e-9:
        out[sign * (k + 1) * 25] = frac
    return {key: v for key, v in out.items() if v > 1e-9}


def meeting_probs(rates: dict, meetings: list[date], today: date) -> list[dict]:
    """회의별 [{날짜, 전, 후, 변화bp, 누적bp, 인하·동결·인상 확률, 분포}]. 선물 가격이 있는 달의 회의만."""
    meet_month = {(d.year, d.month): d for d in meetings if d >= today}
    months = sorted(rates)
    if not months:
        return []
    start_rate = rates[months[0]]
    s, out = None, []
    for ym in months:
        if ym not in meet_month:
            continue
        d = meet_month[ym]
        prev = (ym[0] - (ym[1] == 1), (ym[1] - 2) % 12 + 1)
        nxt = (ym[0] + (ym[1] == 12), ym[1] % 12 + 1)
        if s is None or (prev in rates and prev not in meet_month):
            s = rates.get(prev, start_rate) if prev in rates and prev not in meet_month else (s if s is not None else start_rate)
        n_days = calendar.monthrange(*ym)[1]
        if nxt in rates and nxt not in meet_month:
            e = rates[nxt]
        else:
            e = (rates[ym] * n_days - s * d.day) / (n_days - d.day)
        move = (e - s) * 100
        dist = split(move)
        out.append({"날짜": d, "전": s, "후": e, "변화bp": move, "누적bp": (e - start_rate) * 100,
                    "인하": sum(p for k, p in dist.items() if k < 0), "동결": dist.get(0, 0.0),
                    "인상": sum(p for k, p in dist.items() if k > 0), "분포": dist})
        s = e
    return out


def fedwatch(today: date | None = None) -> list[dict]:
    from data.macro_regime import fomc_meetings
    today = today or date.today()
    return meeting_probs(futures_rates(today), fomc_meetings(), today)
