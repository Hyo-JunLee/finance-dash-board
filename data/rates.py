"""미국 국채 금리: 재무부 공식 종가(히스토리) + CNBC 실시간(최신값).

- 재무부 CSV: 당일 장 마감 후 공시되는 공식 종가. 키 불필요.
- CNBC 시세: 아시아·유럽 시간대 포함 실시간. 비공식 API라 실패 시 종가만 사용.
- 둘 다 실패하면 FRED(DGS*)로 대체.
"""
import io
from datetime import date, datetime

import pandas as pd
import requests
import streamlit as st

import config
from data.fred import get_fred_series

_HEADERS = {"User-Agent": "Mozilla/5.0"}
TREASURY_CSV = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type={kind}&page&_format=csv"
)
NOMINAL, REAL = "daily_treasury_yield_curve", "daily_treasury_real_yield_curve"  # 명목 / 실질(TIPS)
CNBC_QUOTE = (
    "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
    "?symbols={symbols}&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json&events=0"
)

# 만기: (재무부 CSV 컬럼, CNBC 심볼, FRED ID)
TENORS = {
    "3M":  ("3 Mo",  "US3M",  "DGS3MO"),
    "2Y":  ("2 Yr",  "US2Y",  "DGS2"),
    "5Y":  ("5 Yr",  "US5Y",  "DGS5"),
    "10Y": ("10 Yr", "US10Y", "DGS10"),
    "30Y": ("30 Yr", "US30Y", "DGS30"),
}
CNBC_SYMBOLS = tuple(v[1] for v in TENORS.values()) + ("US10YTIP",)  # TIPS 10년 = 실질금리


@st.cache_data(ttl=60 * 60, show_spinner=False)
def _treasury_year(year: int, kind: str = NOMINAL) -> pd.DataFrame:
    # 지난 연도는 바뀌지 않으므로 디스크에 영구 저장
    path = config.CACHE_DIR / (f"treasury_{year}.pkl" if kind == NOMINAL else f"treasury_real_{year}.pkl")
    if year < date.today().year and path.exists():
        return pd.read_pickle(path)
    r = requests.get(TREASURY_CSV.format(year=year, kind=kind), headers=_HEADERS, timeout=15)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.index = pd.to_datetime(df.pop("Date"), format="%m/%d/%Y")
    df = df.sort_index()
    if year < date.today().year:
        df.to_pickle(path)
    return df


def get_treasury_curve(start: date, kind: str = NOMINAL) -> pd.DataFrame:
    """start 이후 재무부 수익률 곡선. 명목 컬럼: '1 Mo', '2 Yr', '10 Yr' … / 실질 컬럼: '5 YR', '10 YR' …"""
    frames = [_treasury_year(y, kind) for y in range(start.year, date.today().year + 1)]
    df = pd.concat(frames).sort_index()
    return df[df.index >= pd.Timestamp(start)]


@st.cache_data(ttl=60, show_spinner=False)
def get_cnbc_quotes(symbols: tuple[str, ...] = CNBC_SYMBOLS) -> dict[str, tuple[float, datetime]]:
    """{심볼: (금리%, 시각 KST)}"""
    r = requests.get(CNBC_QUOTE.format(symbols="|".join(symbols)), headers=_HEADERS, timeout=10)
    r.raise_for_status()
    out = {}
    for q in r.json()["FormattedQuoteResult"]["FormattedQuote"]:
        try:
            val = float(str(q["last"]).rstrip("%"))
            ts = pd.Timestamp(q["last_time"]).tz_convert("Asia/Seoul").tz_localize(None)
        except (KeyError, TypeError, ValueError):
            continue
        out[q["symbol"]] = (val, ts.to_pydatetime())
    return out


def get_yield_series(tenor: str, start: date) -> pd.Series:
    """종가 시계열 + 실시간 최신값 1개를 이어붙인 Series.

    실시간 값의 인덱스는 시각까지 포함(KST)하므로 종가(자정 인덱스)와 구분된다.
    """
    col, cnbc_sym, fred_id = TENORS[tenor]
    try:
        s = get_treasury_curve(start)[col].dropna()
    except Exception:
        s = get_fred_series(fred_id)
        s = s[s.index >= pd.Timestamp(start)]

    try:
        live = get_cnbc_quotes().get(cnbc_sym)
    except Exception:
        live = None
    # 실시간 값이 마지막 종가 이후일 때만 추가 (종가 공시 이후엔 종가가 우선)
    if live and len(s) and pd.Timestamp(live[1]).normalize() > s.index[-1]:
        s = pd.concat([s, pd.Series([live[0]], index=[pd.Timestamp(live[1])])])
    return s
