"""요약 카드용 시세 수집 (FinanceDataReader/Yahoo + FRED 금리)."""
from datetime import date, timedelta

import re

import FinanceDataReader as fdr
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

import config
from data.fred import get_fred_series
from data.rates import get_yield_series

# key: (표시명, 소스, 심볼, 종류)
# 소스: naver=네이버 증권(국내 지수), yahoo=FDR(Yahoo), ust=재무부 종가+CNBC 실시간, fred=FRED,
#       spread=미 국채 장단기 금리차(재무부+CNBC), krx=KRX 국고채 지표물 종가(다음 영업일 공개)
# 국내 지수는 Yahoo 일봉이 종종 비거나(NaN) 늦고 FDR의 KRX 소스도 멈춰 있어 네이버 사용
# 종류: index=지수, fx=환율, rate=금리(%), spread=금리차(%p), usd=달러 가격
SUMMARY_ITEMS = {
    "kospi":  ("KOSPI",        "naver", "KOSPI",   "index"),
    "kosdaq": ("KOSDAQ",       "naver", "KOSDAQ",  "index"),
    "kospi200": ("코스피 200",  "naver", "KPI200",  "index"),
    "spx":    ("S&P 500",      "yahoo", "^GSPC",   "index"),
    "ndx":    ("NASDAQ",       "yahoo", "^IXIC",   "index"),
    "es":     ("S&P 500 선물",  "yahoo", "ES=F",    "index"),   # 거의 24시간 거래 → 한국 낮에도 미국 분위기
    "nq":     ("나스닥100 선물", "yahoo", "NQ=F",    "index"),
    "us2y":   ("미국 2년물",    "ust",   "2Y",      "rate"),
    "us10y":  ("미국 10년물",   "ust",   "10Y",     "rate"),
    "us30y":  ("미국 30년물",   "ust",   "30Y",     "rate"),
    "us2s10s": ("10Y−2Y",      "spread", "10Y-2Y", "spread"),
    "kr3y":   ("국고 3년",      "krx",   "3Y",      "rate"),
    "usdkrw": ("원/달러",       "yahoo", "KRW=X",   "fx"),
    "dxy":    ("달러인덱스",     "yahoo", "DX-Y.NYB", "index"),
    "vix":    ("VIX",          "yahoo", "^VIX",    "index"),
    "wti":    ("WTI",          "yahoo", "CL=F",    "usd"),
    "gold":   ("금",            "yahoo", "GC=F",    "usd"),
    "btc":    ("비트코인",       "yahoo", "BTC-USD", "usd"),
}


@st.cache_data(ttl=config.TTL_PRICE, show_spinner=False)
def get_price_series(source: str, symbol: str, days: int = 400) -> pd.Series:
    """종가 시계열 (최근 days일). 실패 시 빈 Series."""
    start = date.today() - timedelta(days=days)
    if source == "naver":
        return _naver_daily(symbol, days)
    if source == "ust":
        return get_yield_series(symbol, start)
    if source == "spread":  # 10Y-2Y: 종가끼리 빼고 실시간 값이 있으면 마지막에 추가
        from data.macro import curve_spread
        long, short = symbol.split("-")
        return curve_spread(long, short, years=2)
    if source == "krx":
        from data.krx import kts_yields
        return kts_yields(1)[symbol].dropna()
    if source == "fred":
        s = get_fred_series(symbol)
        return s[s.index >= pd.Timestamp(start)]
    df = fdr.DataReader(f"YAHOO:{symbol}", start)
    return df["Close"].dropna()


_NAVER_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _naver_daily(code: str, days: int) -> pd.Series:
    """네이버 증권 지수 일봉 종가. 응답 형식: data="20261006|시가|고가|저가|종가|거래량" """
    r = requests.get("https://fchart.stock.naver.com/sise.nhn", headers=_NAVER_HEADERS, timeout=10,
                     params={"symbol": code, "timeframe": "day", "count": days, "requestType": 0})
    r.raise_for_status()
    rows = [x.split("|") for x in re.findall(r'data="([^"]+)"', r.text)]
    return pd.Series([float(x[4]) for x in rows], index=pd.to_datetime([x[0] for x in rows]), name=code)


@st.cache_data(ttl=60, show_spinner=False)
def get_naver_quote(code: str) -> tuple[float, pd.Timestamp, bool]:
    """(현재가, 체결 시각 KST, 장중 여부)"""
    j = requests.get(f"https://m.stock.naver.com/api/index/{code}/basic", headers=_NAVER_HEADERS, timeout=10).json()
    ts = pd.Timestamp(j["localTradedAt"]).tz_convert("Asia/Seoul").tz_localize(None)
    return float(j["closePrice"].replace(",", "")), ts, j.get("marketStatus") == "OPEN"


def _with_naver_live(s: pd.Series, code: str) -> pd.Series:
    """장중이면 마지막 값을 네이버 실시간 값으로 바꾸고 인덱스에 시각을 넣는다. 마감이면 일봉 그대로."""
    try:
        value, ts, is_open = get_naver_quote(code)
    except Exception:
        return s
    if not is_open:
        return s
    base = s.iloc[:-1] if s.index[-1].date() >= ts.date() else s
    return pd.concat([base, pd.Series([value], index=[ts])])


LIVE_MAX_AGE = pd.Timedelta(minutes=30)  # 이보다 오래된 1분봉은 장 마감으로 본다

# 정규장이 있는 지수·국채: 장 마감 후 카드에 표시할 공식 마감 시각 (현지 시각, 서머타임은 시간대 변환으로 반영)
# 국채는 재무부 수익률 곡선 기준 시각(뉴욕 연은이 15:30경 호가로 산출)
CLOSE_TIMES = {
    "kospi": ("15:30", "Asia/Seoul"), "kosdaq": ("15:30", "Asia/Seoul"), "kospi200": ("15:30", "Asia/Seoul"),
    "spx": ("16:00", "America/New_York"), "ndx": ("16:00", "America/New_York"),
    "us2y": ("15:30", "America/New_York"), "us10y": ("15:30", "America/New_York"),
    "us30y": ("15:30", "America/New_York"),
    "us2s10s": ("15:30", "America/New_York"), "kr3y": ("15:30", "Asia/Seoul"),
    "vix": ("16:15", "America/New_York"),
}
# 24시간·선물(환율·비트코인·WTI·금)은 정해진 마감이 없어 '마지막 체결 시각'을 쓴다
SESSIONLESS = {"KRW=X", "BTC-USD", "CL=F", "GC=F", "DX-Y.NYB", "ES=F", "NQ=F"}


@st.cache_data(ttl=60, show_spinner=False)
def get_intraday_last(symbols: tuple[str, ...]) -> dict[str, tuple[float, pd.Timestamp]]:
    """Yahoo 1분봉의 마지막 값과 시각(UTC). 일봉엔 시각이 없어 장중 갱신 시각을 따로 받는다."""
    df = yf.download(list(symbols), period="1d", interval="1m", auto_adjust=False,
                     progress=False, threads=True)["Close"]
    if isinstance(df, pd.Series):
        df = df.to_frame(symbols[0])
    out = {}
    for sym in df.columns:
        col = df[sym].dropna()
        if len(col):
            ts = col.index[-1]
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            out[sym] = (float(col.iloc[-1]), ts)
    return out


def _with_live(s: pd.Series, symbol: str) -> pd.Series:
    """장중이면 마지막 값을 1분봉 최신값으로 바꾸고 인덱스에 시각(KST)을 넣는다."""
    yahoo_syms = tuple(v[2] for v in SUMMARY_ITEMS.values() if v[1] == "yahoo")
    try:
        live = get_intraday_last(yahoo_syms).get(symbol)
    except Exception:
        return s
    if not live:
        return s
    stale = pd.Timestamp.now(tz="UTC") - live[1] > LIVE_MAX_AGE
    if stale and symbol not in SESSIONLESS:  # 정규장 종목은 장 마감 후 일봉 종가 그대로
        return s
    value, ts = live
    label = ts.tz_convert("Asia/Seoul").tz_localize(None)
    # 일봉 마지막이 오늘 장중 봉이면 교체, 아직 없으면 추가
    base = s.iloc[:-1] if s.index[-1].date() >= ts.date() else s
    return pd.concat([base, pd.Series([value], index=[label])])


def get_snapshot(key: str) -> dict | None:
    """카드 하나에 필요한 값: 최신값, 전일 대비, 기준일, 시계열."""
    label, source, symbol, kind = SUMMARY_ITEMS[key]
    try:
        s = get_price_series(source, symbol)
    except Exception:
        return None
    if source == "yahoo" and len(s):
        s = _with_live(s, symbol)
    elif source == "naver" and len(s):
        s = _with_naver_live(s, symbol)
    if len(s) < 2:
        return None
    last, prev = float(s.iloc[-1]), float(s.iloc[-2])
    live_prev = _cnbc_prev(source, symbol, s.index[-1])
    if live_prev is not None:  # 실시간 금리는 같은 출처(CNBC) 전일 종가 대비
        prev = live_prev
    asof, asof_kind = _asof(key, symbol, s.index[-1])
    return {
        "key": key,
        "label": label,
        "kind": kind,
        "last": last,
        "chg": last - prev,
        "pct": (last / prev - 1) * 100,
        "asof": asof,            # KST, 항상 시각 포함
        "asof_kind": asof_kind,  # live=실시간, close=공식 종가, last=마지막 체결, date=시각 모름
        "series": s,
        **card_context(s, kind, last - prev),
    }


def _cnbc_prev(source: str, symbol: str, last_ts: pd.Timestamp) -> float | None:
    """마지막 점이 CNBC 실시간 값(시각 포함)이면 CNBC 자체 전일 종가. 아니면 None(종가끼리 비교)."""
    if source not in ("ust", "spread") or (last_ts.hour, last_ts.minute, last_ts.second) == (0, 0, 0):
        return None
    from data.rates import TENORS, get_cnbc_prev_close
    try:
        prev = get_cnbc_prev_close()
        if source == "ust":
            return prev[TENORS[symbol][1]]
        long, short = symbol.split("-")
        return prev[TENORS[long][1]] - prev[TENORS[short][1]]
    except Exception:
        return None


def _changes(s: pd.Series, kind: str) -> pd.Series:
    """일간 변화: 금리·금리차는 bp, 나머지는 %."""
    return s.diff() * 100 if kind in ("rate", "spread") else s.pct_change() * 100


def card_context(s: pd.Series, kind: str, chg: float) -> dict:
    """카드 맥락 정보: 1년 범위 위치, 오늘 변화가 평소의 몇 배인지(z), 기간별 변화."""
    now = s.index[-1]
    y1 = s[s.index >= now - pd.Timedelta(days=365)]
    lo, hi, last = float(y1.min()), float(y1.max()), float(s.iloc[-1])
    pos = (last - lo) / (hi - lo) * 100 if hi > lo else 50.0
    ch = _changes(y1, kind).dropna()
    sigma = float(ch.iloc[:-1].std()) if len(ch) > 20 else float("nan")
    today = chg * 100 if kind in ("rate", "spread") else chg / float(s.iloc[-1] - chg) * 100
    z = today / sigma if sigma and sigma == sigma else float("nan")

    def since(target):
        base = s[s.index <= target]
        if base.empty:
            return float("nan")
        b = float(base.iloc[-1])
        return (last - b) * 100 if kind in ("rate", "spread") else (last / b - 1) * 100
    periods = {"1주": since(now - pd.Timedelta(days=7)), "1개월": since(now - pd.Timedelta(days=30)),
               "YTD": since(pd.Timestamp(year=now.year, month=1, day=1) - pd.Timedelta(days=1))}
    return {"range": (lo, hi, pos), "z": z, "periods": periods}


def _asof(key: str, symbol: str, ts: pd.Timestamp) -> tuple[pd.Timestamp, str]:
    """카드 기준 시각(KST)과 종류. 일봉(자정 인덱스)이면 공식 마감 시각을 붙인다."""
    has_time = (ts.hour, ts.minute, ts.second) != (0, 0, 0)
    if has_time:
        fresh = pd.Timestamp.now() - ts <= LIVE_MAX_AGE
        return ts, "live" if fresh else "last"
    if key in CLOSE_TIMES:
        hhmm, tz = CLOSE_TIMES[key]
        close = pd.Timestamp(f"{ts.date()} {hhmm}").tz_localize(tz).tz_convert("Asia/Seoul").tz_localize(None)
        return close, "close"
    return ts, "date"


ASOF_SUFFIX = {"live": "", "close": " 종가", "last": " 마지막 체결", "date": ""}
DATE_ONLY_CLOSE = {"kr3y"}  # KRX 국고채: 다음 영업일 공개, 15:30 종가 고정이라 날짜만


def fmt_asof(snap: dict) -> str:
    """카드용 기준 시각 문구(KST). 실시간은 시각만, 장 마감 후엔 '종가'·'마지막 체결'을 붙인다."""
    ts, kind = snap["asof"], snap.get("asof_kind", "date")
    if kind == "date":
        return f"{ts:%m/%d}"
    if kind == "close" and snap.get("key") in DATE_ONLY_CLOSE:  # 마감 시각이 늘 같은 다음날 공개 데이터
        return f"{ts:%m/%d} 종가"
    day = "" if ts.date() == pd.Timestamp.now().date() else f"{ts:%m/%d} "  # 오늘이면 날짜 생략 (카드 폭 절약)
    return f"{day}{ts:%H:%M}{ASOF_SUFFIX[kind]}"
