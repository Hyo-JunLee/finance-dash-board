"""매크로 화면용 시계열.

- 경제지표: FRED (공식 발표 당일 반영)
- 금리·수익률 곡선: 재무부 종가 + CNBC 실시간 (data/rates.py)
- 기대인플레이션: FRED 종가 + CNBC 실시간(명목 10Y − TIPS 10Y)
- VIX·MOVE·달러인덱스: Yahoo (실시간)
- 신용 스프레드·금융여건지수: FRED (무료 실시간 대안 없음, 1일 지연)
"""
from datetime import date, timedelta

import FinanceDataReader as fdr
import pandas as pd
import streamlit as st

import config
from data.fred import fetch_series, prefetch, transform
from data.rates import get_cnbc_quotes, get_treasury_curve, get_yield_series

# 화면에서 쓰는 FRED 시계열 (첫 로딩 때 병렬로 미리 받아둠)
FRED_IDS = [
    "DFEDTARU", "CPIAUCNS", "CPILFENS", "PCEPILFE", "T10YIE", "T5YIFR",
    "PAYEMS", "UNRATE", "ICSA", "IC4WSA", "JTSJOL",
    "A191RL1Q225SBEA", "RSAFS", "INDPRO", "HOUST",
    "BAMLH0A0HYM2", "BAMLC0A0CM", "NFCI",
]

CURVE_COLS = ["1 Mo", "3 Mo", "6 Mo", "1 Yr", "2 Yr", "3 Yr", "5 Yr", "7 Yr", "10 Yr", "20 Yr", "30 Yr"]
CURVE_LABELS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]


@st.cache_data(ttl=config.TTL_MACRO, show_spinner=False)
def warmup() -> None:
    prefetch(FRED_IDS)


@st.cache_data(ttl=config.TTL_MACRO, show_spinner=False)
def fred(series_id: str, how: str = "level", years: int = 10) -> pd.Series:
    s = transform(fetch_series(series_id), how).dropna()
    return s[s.index >= pd.Timestamp.now() - pd.DateOffset(years=years)]


@st.cache_data(ttl=config.TTL_PRICE, show_spinner=False)
def yahoo(symbol: str, years: int = 10) -> pd.Series:
    start = date.today() - timedelta(days=365 * years)
    return fdr.DataReader(f"YAHOO:{symbol}", start)["Close"].dropna()


def _append_live(s: pd.Series, value: float | None, ts) -> pd.Series:
    if value is None or not len(s) or pd.Timestamp(ts).normalize() <= s.index[-1]:
        return s
    return pd.concat([s, pd.Series([value], index=[pd.Timestamp(ts)])])


@st.cache_data(ttl=config.TTL_PRICE, show_spinner=False)
def yields(tenor: str, years: int = 10) -> pd.Series:
    return get_yield_series(tenor, date.today() - timedelta(days=365 * years))


@st.cache_data(ttl=config.TTL_PRICE, show_spinner=False)
def curve_spread(long: str, short: str, years: int = 10) -> pd.Series:
    """장단기 금리차(%p). 종가끼리 빼고, 실시간 값이 둘 다 있으면 마지막에 추가."""
    a, b = yields(long, years), yields(short, years)
    closes = (a[a.index.normalize() == a.index] - b[b.index.normalize() == b.index]).dropna()
    live_a, live_b = a.index[-1], b.index[-1]
    if live_a != live_a.normalize() and live_b != live_b.normalize():
        closes = _append_live(closes, a.iloc[-1] - b.iloc[-1], max(live_a, live_b))
    return closes


@st.cache_data(ttl=config.TTL_PRICE, show_spinner=False)
def breakeven_10y(years: int = 10) -> pd.Series:
    """10년 기대인플레이션 = 명목 10Y − TIPS 10Y. FRED 종가 + CNBC 실시간."""
    s = fred("T10YIE", years=years)
    try:
        q = get_cnbc_quotes()
        (nom, ts), (real, _) = q["US10Y"], q["US10YTIP"]
        s = _append_live(s, round(nom - real, 3), ts)
    except Exception:
        pass
    return s


@st.cache_data(ttl=60 * 60, show_spinner=False)
def yield_curves() -> pd.DataFrame:
    """최근 종가 / 1개월 전 / 1년 전 수익률 곡선. 행=만기, 열=시점 라벨."""
    df = get_treasury_curve(date.today() - timedelta(days=400))[CURVE_COLS].dropna(how="all")
    last = df.index[-1]
    picks = {"최근": last, "1개월 전": last - pd.DateOffset(months=1), "1년 전": last - pd.DateOffset(years=1)}
    out = {}
    for label, target in picks.items():
        row = df[df.index <= target].iloc[-1]
        out[f"{label} ({row.name:%y/%m/%d})"] = row.values
    return pd.DataFrame(out, index=CURVE_LABELS)
