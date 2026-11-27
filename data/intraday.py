"""지수 카드 미니 차트용 장중 시계열 (차트 기간 1일·1주).

- 국내: 네이버 증권 분봉 API (KOSPI·KOSDAQ·KPI200), 1분봉 → 1주는 10분 간격으로 줄임
- 미국·해외: Yahoo 5분봉(1일) / 15분봉(1주) — 지수는 정규장, 선물·환율·달러인덱스는 거의 24시간, 비트코인은 24시간
- 미 국채 금리: CNBC 1분봉 (최근 5거래일)
- 1일 = 마지막 거래일 장중(비트코인은 최근 24시간), 1주 = 최근 5거래일. 인덱스는 한국 시각(시간대 없음). 2분 캐시
"""
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

KST = "Asia/Seoul"
NAVER_MINUTE = "https://api.stock.naver.com/chart/domestic/index/{code}/minute"
SESSIONS = {"1일": 1, "1주": 5}


def _last_sessions(s: pd.Series, n: int) -> pd.Series:
    """마지막 n개 거래일(날짜) 분만 남긴다."""
    days = sorted(set(s.index.date))[-n:]
    return s[pd.Index(s.index.date).isin(days)]


@st.cache_data(ttl=120, show_spinner=False)
def kr_intraday(code: str, label: str) -> pd.Series:
    """국내 지수 장중 시계열. code: KOSPI·KOSDAQ·KPI200, label: '1일'|'1주'."""
    now = pd.Timestamp.now(tz=KST).tz_localize(None)
    start = now - pd.Timedelta(days=10)
    rows = requests.get(NAVER_MINUTE.format(code=code), headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
                        params={"startDateTime": f"{start:%Y%m%d}0900", "endDateTime": f"{now:%Y%m%d%H%M}"}).json()
    s = pd.Series([float(r["currentPrice"]) for r in rows],
                  index=pd.to_datetime([r["localDateTime"] for r in rows], format="%Y%m%d%H%M%S")).sort_index()
    s = _last_sessions(s, SESSIONS[label])
    if s.empty:
        raise RuntimeError(f"네이버 {code} 장중 데이터 빈 응답")
    return s.resample("10min").last().dropna() if label == "1주" else s


ROLL_6H = {"KRW=X", "DX-Y.NYB"}      # 거의 24시간 거래 → 선물처럼 미 동부 18시 기준 세션
ROLLING_24H = {"BTC-USD"}             # 24시간·365일 → 1일 = 최근 24시간


@st.cache_data(ttl=120, show_spinner=False)
def us_intraday(tickers: tuple[str, ...], label: str) -> dict[str, pd.Series]:
    """미국 지수·선물 장중 시계열 {티커: Series}. label: '1일'|'1주'."""
    period, interval = ("5d", "5m") if label == "1일" else ("5d", "15m")
    df = yf.download(list(tickers), period=period, interval=interval, progress=False, auto_adjust=False)["Close"]
    if isinstance(df, pd.Series):
        df = df.to_frame(tickers[0])
    out = {}
    for t in tickers:
        if t not in df:
            continue
        s = df[t].dropna()
        if s.empty:
            continue
        ny = s.index.tz_convert("America/New_York")
        if t in ROLLING_24H and label == "1일":  # 24시간 거래: 최근 24시간
            s = s[s.index >= s.index[-1] - pd.Timedelta(hours=24)]
        else:
            # 거래일은 뉴욕 날짜 기준 (선물·환율은 전날 18시(17시)에 다음 거래일 세션이 시작되므로 +6시간으로 묶음)
            session = (ny + pd.Timedelta(hours=6)).date if t in ROLL_6H or t.endswith("=F") else ny.date
            days = sorted(set(session))[-SESSIONS[label]:]
            s = s[pd.Index(session).isin(days)]
        s.index = s.index.tz_convert(KST).tz_localize(None)
        out[t] = s
    if not out:  # Yahoo가 순간적으로 빈 결과를 주면 예외 → 빈 결과가 캐시에 남지 않게
        raise RuntimeError("Yahoo 장중 데이터 빈 응답")
    return out


def session_bounds(s: pd.Series, market: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """마지막 거래일 장 시작·마감 시각 (한국 시각, 시간대 없음). market: kr | us | fut.
    미국 정규장 09:30~16:00, 선물 전날 18:00~당일 17:00 (미 동부) → 서머타임은 시간대 변환으로 자동 반영.
    (미국 조기 폐장일은 반영하지 않아 오른쪽 끝이 16:00으로 남음)"""
    if s is None or s.empty:
        return None
    last = s.index[-1]
    if market == "24h":
        return last - pd.Timedelta(hours=24), last
    if market == "kr":
        d = last.normalize()
        return d + pd.Timedelta(hours=9), d + pd.Timedelta(hours=15, minutes=30)
    ny = last.tz_localize(KST).tz_convert("America/New_York")
    if market == "vix":  # VIX는 정규장 전 시간외부터: 03:15~16:15 ET
        day = ny.normalize()
        start, end = day + pd.Timedelta(hours=3, minutes=15), day + pd.Timedelta(hours=16, minutes=15)
        return tuple(t.tz_convert(KST).tz_localize(None) for t in (start, end))
    if market == "fut":
        day = (ny + pd.Timedelta(hours=6)).normalize()
        start, end = day - pd.Timedelta(hours=6), day + pd.Timedelta(hours=17)
    else:
        day = ny.normalize()
        start, end = day + pd.Timedelta(hours=9, minutes=30), day + pd.Timedelta(hours=16)
    return tuple(t.tz_convert(KST).tz_localize(None) for t in (start, end))


@st.cache_data(ttl=120, show_spinner=False)
def cnbc_intraday(symbol: str, label: str) -> pd.Series:
    """미 국채 금리 장중 시계열 (CNBC 1분봉, 최근 5거래일). 1주는 15분 간격으로 줄임. 세션 = 미 동부 18시 기준."""
    bars = requests.get("https://ts-api.cnbc.com/harmony/app/charts/5D.json", params={"symbol": symbol},
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=15).json()["barData"]["priceBars"]
    s = pd.Series([float(b["close"]) for b in bars],
                  index=pd.to_datetime([b["tradeTime"] for b in bars], format="%Y%m%d%H%M%S")).sort_index()
    if s.empty:
        raise RuntimeError(f"CNBC {symbol} 장중 데이터 빈 응답")
    ny = s.index.tz_localize("America/New_York")
    session = (ny + pd.Timedelta(hours=6)).date
    days = sorted(set(session))[-SESSIONS[label]:]
    s = pd.Series(s.values, index=ny.tz_convert(KST).tz_localize(None))[pd.Index(session).isin(days)]
    return s.resample("15min").last().dropna() if label == "1주" else s
