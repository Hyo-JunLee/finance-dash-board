"""해외 증시: 주요 지수, 섹터·스타일 ETF, S&P 500 대형주 히트맵 (Yahoo 일괄 조회)."""
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st
import yfinance as yf

import config

# 티커: (이름, 지역)
INDICES = {
    "^GSPC":     ("S&P 500",       "미국"),
    "^IXIC":     ("나스닥",         "미국"),
    "^DJI":      ("다우",           "미국"),
    "^RUT":      ("러셀 2000",      "미국"),
    "^STOXX50E": ("유로스톡스 50",  "유럽"),
    "^GDAXI":    ("독일 DAX",       "유럽"),
    "^FTSE":     ("영국 FTSE 100",  "유럽"),
    "^FCHI":     ("프랑스 CAC 40",  "유럽"),
    "^N225":     ("닛케이 225",     "아시아"),
    "^HSI":      ("항셍",           "아시아"),
    "000001.SS": ("상하이 종합",    "아시아"),
    "^TWII":     ("대만 가권",      "아시아"),
}

# 거래소 정규장 마감 시각 (현지). 시간대 변환으로 서머타임 자동 반영
CLOSE_TIMES = {
    "^GSPC": ("16:00", "America/New_York"), "^IXIC": ("16:00", "America/New_York"),
    "^DJI": ("16:00", "America/New_York"), "^RUT": ("16:00", "America/New_York"),
    "^STOXX50E": ("17:30", "Europe/Berlin"), "^GDAXI": ("17:30", "Europe/Berlin"),
    "^FTSE": ("16:30", "Europe/London"), "^FCHI": ("17:30", "Europe/Paris"),
    "^N225": ("15:30", "Asia/Tokyo"), "^HSI": ("16:00", "Asia/Hong_Kong"),
    "000001.SS": ("15:00", "Asia/Shanghai"), "^TWII": ("13:30", "Asia/Taipei"),
}
LIVE_MAX_AGE = pd.Timedelta(minutes=30)  # 이보다 오래된 1분봉은 장 마감으로 본다

SECTOR_ETFS = {
    "XLK": "기술", "XLC": "커뮤니케이션", "XLY": "경기소비재", "XLF": "금융", "XLV": "헬스케어",
    "XLI": "산업재", "XLP": "필수소비재", "XLE": "에너지", "XLB": "소재", "XLU": "유틸리티", "XLRE": "부동산",
}

# 상대강도 = 분자 ETF / 분모 ETF
STYLE_PAIRS = {
    "성장/가치 (IWF/IWD)": ("IWF", "IWD"),
    "소형/대형 (IWM/SPY)": ("IWM", "SPY"),
    "동일가중/시총가중 (RSP/SPY)": ("RSP", "SPY"),
}

# S&P 500 대형주 (섹터별). 구성은 연 1회 정도 점검
HEATMAP_STOCKS = {
    "기술": ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "ADBE", "CSCO", "ACN", "IBM",
             "QCOM", "TXN", "INTU", "NOW", "AMAT", "MU", "PLTR"],
    "커뮤니케이션": ["GOOGL", "META", "NFLX", "TMUS", "DIS", "T", "VZ"],
    "경기소비재": ["AMZN", "TSLA", "HD", "MCD", "BKNG", "LOW", "NKE", "SBUX", "TJX"],
    "금융": ["BRK-B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP", "BLK", "C"],
    "헬스케어": ["LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "ISRG", "AMGN", "PFE"],
    "필수소비재": ["WMT", "COST", "PG", "KO", "PEP", "PM"],
    "에너지": ["XOM", "CVX", "COP"],
    "산업재": ["GE", "CAT", "RTX", "UBER", "HON", "BA", "UNP", "DE"],
    "소재": ["LIN", "SHW"],
    "유틸리티": ["NEE", "SO"],
    "부동산": ["PLD", "AMT"],
}
STOCK_SECTOR = {t: sec for sec, ts in HEATMAP_STOCKS.items() for t in ts}

# 수익률 기간: (라벨, pandas offset 또는 특수값)
PERIODS = {"1일": "1D", "1주": pd.DateOffset(weeks=1), "1개월": pd.DateOffset(months=1),
           "YTD": "YTD", "1년": pd.DateOffset(years=1)}

SHARES_CACHE = config.CACHE_DIR / "shares_outstanding.json"


@st.cache_data(ttl=config.TTL_PRICE, show_spinner=False)
def get_closes(tickers: tuple[str, ...], period: str = "2y") -> pd.DataFrame:
    """종가(수정주가) 일괄 조회. 열=티커."""
    df = yf.download(list(tickers), period=period, auto_adjust=True, progress=False, threads=True)["Close"]
    return df if isinstance(df, pd.DataFrame) else df.to_frame(tickers[0])


def period_return(s: pd.Series, period) -> float | None:
    """마지막 값 기준 기간 수익률(%). 휴장일이 달라 티커별로 계산."""
    s = s.dropna()
    if len(s) < 2:
        return None
    last_date, last = s.index[-1], s.iloc[-1]
    if period == "1D":
        base = s.iloc[-2]
    elif period == "YTD":
        prior = s[s.index.year < last_date.year]
        base = prior.iloc[-1] if len(prior) else None
    else:
        base = s.asof(last_date - period)
    return None if base is None or pd.isna(base) else (last / base - 1) * 100


def returns_table(closes: pd.DataFrame) -> pd.DataFrame:
    """행=티커, 열=기간별 수익률 + 현재값·기준일."""
    rows = {}
    for t in closes.columns:
        s = closes[t].dropna()
        if s.empty:
            continue
        rows[t] = {"현재": s.iloc[-1], "기준일": s.index[-1].date(),
                   **{label: period_return(s, p) for label, p in PERIODS.items()}}
    return pd.DataFrame.from_dict(rows, orient="index")


def _fetch_shares(ticker: str) -> float | None:
    try:
        return float(yf.Ticker(ticker).fast_info["shares"])
    except Exception:
        return None


def get_shares(tickers: list[str]) -> dict[str, float]:
    """발행주식수. 자주 바뀌지 않으므로 하루 한 번만 받아 디스크에 저장."""
    cached = json.loads(SHARES_CACHE.read_text()) if SHARES_CACHE.exists() else {}
    fresh = SHARES_CACHE.exists() and time.time() - SHARES_CACHE.stat().st_mtime < 86400
    missing = [t for t in tickers if t not in cached or not fresh]
    if missing:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for t, n in zip(missing, ex.map(_fetch_shares, missing)):
                if n:
                    cached[t] = n
        SHARES_CACHE.write_text(json.dumps(cached))
    return {t: cached[t] for t in tickers if t in cached}


@st.cache_data(ttl=config.TTL_PRICE, show_spinner=False)
def heatmap_data(period_label: str) -> pd.DataFrame:
    """히트맵용: 종목, 섹터, 시가총액(=주식수×현재가), 기간 수익률."""
    tickers = list(STOCK_SECTOR)
    closes = get_closes(tuple(tickers), "2y")
    shares = get_shares(tickers)
    rows = []
    for t in tickers:
        if t not in closes or t not in shares:
            continue
        s = closes[t].dropna()
        ret = period_return(s, PERIODS[period_label])
        if s.empty or ret is None:
            continue
        rows.append({"종목": t, "섹터": STOCK_SECTOR[t], "현재가": s.iloc[-1],
                     "시가총액": shares[t] * s.iloc[-1], "수익률": ret})
    return pd.DataFrame(rows)


def apply_market_status(closes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """장중 지수는 마지막 값을 1분봉 최신값으로 갱신하고, 지수별 상태·기준 시각(KST)을 돌려준다.

    반환: (갱신된 종가표, 상태표[상태, 기준 시각])
    """
    from data.market import get_intraday_last  # 1분봉 일괄 조회 재사용

    try:
        live = get_intraday_last(tuple(closes.columns))
    except Exception:
        live = {}
    now = pd.Timestamp.now(tz="UTC")
    closes = closes.copy()
    status = {}
    for t in closes.columns:
        s = closes[t].dropna()
        if s.empty:
            continue
        hit = live.get(t)
        hhmm, tz = CLOSE_TIMES.get(t, ("16:00", "UTC"))
        # 1분봉이 최근이어도 현지 마감 시각이 지났으면 마감 (지연 시세가 장중으로 보이지 않게)
        today_close = pd.Timestamp(f"{now.tz_convert(tz).date()} {hhmm}").tz_localize(tz)
        if hit and now - hit[1] <= LIVE_MAX_AGE and now < today_close:
            value, ts = hit
            last_day = s.index[-1]
            # 오늘 장중 봉이 이미 있으면 교체, 없으면 오늘 날짜로 추가
            today = pd.Timestamp(ts.tz_convert(tz).date())
            closes.loc[today if today >= last_day else last_day, t] = value
            status[t] = ("🟢 장중", ts.tz_convert("Asia/Seoul").tz_localize(None))
        else:
            close_at = pd.Timestamp(f"{s.index[-1].date()} {hhmm}").tz_localize(tz)
            status[t] = ("마감", close_at.tz_convert("Asia/Seoul").tz_localize(None))
    closes = closes.sort_index()
    return closes, pd.DataFrame.from_dict(status, orient="index", columns=["상태", "기준 시각"])
