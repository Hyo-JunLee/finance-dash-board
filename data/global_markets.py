"""미국 증시: 주요 지수, 섹터·스타일 ETF, S&P 500 대형주 히트맵 (Yahoo 일괄 조회)."""
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
}

# 거래소 정규장 마감 시각 (현지). 시간대 변환으로 서머타임 자동 반영
CLOSE_TIMES = {
    "^GSPC": ("16:00", "America/New_York"), "^IXIC": ("16:00", "America/New_York"),
    "^DJI": ("16:00", "America/New_York"), "^RUT": ("16:00", "America/New_York"),
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

# 주요 종목 시세표: 히트맵 종목 + 추가 종목 (티커 → 섹터 표시)
EXTRA_QUOTES = {"TSM": "반도체 (ADR)", "SOXL": "반도체 3배 레버리지 ETF", "SOXS": "반도체 3배 인버스 ETF"}
KR_NAMES = {
    "AAPL": "애플", "MSFT": "마이크로소프트", "NVDA": "엔비디아", "AVGO": "브로드컴", "ORCL": "오라클",
    "CRM": "세일즈포스", "AMD": "AMD", "ADBE": "어도비", "CSCO": "시스코", "ACN": "액센추어", "IBM": "IBM",
    "QCOM": "퀄컴", "TXN": "텍사스 인스트루먼트", "INTU": "인튜이트", "NOW": "서비스나우", "AMAT": "어플라이드 머티리얼즈",
    "MU": "마이크론", "PLTR": "팔란티어", "GOOGL": "알파벳 A", "META": "메타", "NFLX": "넷플릭스",
    "TMUS": "T-모바일 US", "DIS": "디즈니", "T": "AT&T", "VZ": "버라이즌", "AMZN": "아마존", "TSLA": "테슬라",
    "HD": "홈디포", "MCD": "맥도날드", "BKNG": "부킹 홀딩스", "LOW": "로우스", "NKE": "나이키", "SBUX": "스타벅스",
    "TJX": "TJX", "BRK-B": "버크셔 해서웨이 B", "JPM": "JP모건", "V": "비자", "MA": "마스터카드",
    "BAC": "뱅크오브아메리카", "WFC": "웰스파고", "GS": "골드만삭스", "MS": "모건스탠리", "AXP": "아메리칸 익스프레스",
    "BLK": "블랙록", "C": "씨티그룹", "LLY": "일라이 릴리", "UNH": "유나이티드헬스", "JNJ": "존슨앤드존슨",
    "ABBV": "애브비", "MRK": "머크", "TMO": "써모피셔", "ABT": "애보트", "ISRG": "인튜이티브 서지컬", "AMGN": "암젠",
    "PFE": "화이자", "WMT": "월마트", "COST": "코스트코", "PG": "P&G", "KO": "코카콜라", "PEP": "펩시코",
    "PM": "필립모리스", "XOM": "엑슨모빌", "CVX": "셰브론", "COP": "코노코필립스", "GE": "GE 에어로스페이스",
    "CAT": "캐터필러", "RTX": "RTX", "UBER": "우버", "HON": "허니웰", "BA": "보잉", "UNP": "유니언 퍼시픽",
    "DE": "디어", "LIN": "린데", "SHW": "셔윈윌리엄스", "NEE": "넥스트에라 에너지", "SO": "서던", "PLD": "프롤로지스",
    "AMT": "아메리칸 타워", "TSM": "TSMC", "SOXL": "디렉시온 반도체 3X", "SOXS": "디렉시온 반도체 인버스 3X",
}

# 수익률 기간: (라벨, pandas offset 또는 특수값)
PERIODS = {"1일": "1D", "1주": pd.DateOffset(weeks=1), "1개월": pd.DateOffset(months=1),
           "YTD": "YTD", "1년": pd.DateOffset(years=1)}

SHARES_CACHE = config.CACHE_DIR / "shares_outstanding.json"


def get_closes(tickers: tuple[str, ...], period: str = "2y") -> pd.DataFrame:
    """종가(수정주가) 일괄 조회. 열=티커. 실패하면 빈 표 (빈 결과는 캐시하지 않아 다음 조회 때 다시 받는다)."""
    try:
        return _closes(tickers, period)
    except Exception:
        return pd.DataFrame(columns=list(tickers))


@st.cache_data(ttl=config.TTL_PRICE, show_spinner=False)
def _closes(tickers: tuple[str, ...], period: str) -> pd.DataFrame:
    df = yf.download(list(tickers), period=period, auto_adjust=True, progress=False, threads=True)["Close"]
    df = df if isinstance(df, pd.DataFrame) else df.to_frame(tickers[0])
    if df.dropna(how="all").empty:  # Yahoo가 순간적으로 빈 결과를 주면 예외 → 빈 표가 캐시에 남지 않게
        raise RuntimeError(f"Yahoo 종가 빈 응답: {', '.join(tickers[:3])}…")
    if len(df) and df.iloc[-1].isna().any():
        from data.market import get_intraday_last  # 1분봉 일괄 조회 재사용
        try:
            df = fill_missing_last(df, get_intraday_last(tuple(df.columns[df.iloc[-1].isna()])))
        except Exception:
            pass
    return df


def fill_missing_last(df: pd.DataFrame, live: dict[str, tuple[float, pd.Timestamp]]) -> pd.DataFrame:
    """Yahoo가 개별 종목·ETF의 마지막 거래일 일봉을 거래량만 넣고 종가는 비워 보내는 경우가 있다
    (지수는 정상, 다음 날 아침까지 지속). 같은 날 1분봉이 있으면 그 마지막 값(정규장 종가와 거의 같음)으로 채운다.
    live: {티커: (값, UTC 시각)} — get_intraday_last 결과. 1분봉 날짜(뉴욕)가 마지막 행 날짜와 같을 때만 채운다."""
    if df.empty:
        return df
    df, last = df.copy(), df.index[-1]
    for t, (value, ts) in live.items():
        if t in df and pd.isna(df.at[last, t]) and ts.tz_convert("America/New_York").date() == last.date():
            df.at[last, t] = value
    return df


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
        hhmm, tz = CLOSE_TIMES.get(t, ("16:00", "America/New_York"))  # 개별 종목·ETF도 뉴욕 정규장
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


NY = "America/New_York"
PRE, REGULAR, POST, POST_END = "04:00", "09:30", "16:00", "20:00"  # 뉴욕 현지 세션 경계
EXT_LIVE_AGE = pd.Timedelta(minutes=30)  # 마지막 시간외 봉이 이보다 오래되면 '진행 중'으로 보지 않음


def _download_filled(tickers: list[str], **kw) -> pd.DataFrame:
    """yf.download 일괄 조회. Yahoo가 여러 종목을 한 번에 받을 때 일부 종목을 비워 보내는 경우가 있어
    빈 종목만 한 번 더 받아 채운다."""
    def get(ts):
        df = yf.download(ts, progress=False, threads=True, **kw)["Close"]
        return df if isinstance(df, pd.DataFrame) else df.to_frame(ts[0])
    df = get(tickers)
    missing = [t for t in tickers if t not in df or df[t].isna().all()]
    if missing:
        try:
            df = df.drop(columns=[t for t in missing if t in df]).join(get(missing), how="outer")
        except Exception:
            pass
    return df


@st.cache_data(ttl=60, show_spinner=False)
def _extended_bars(tickers: tuple[str, ...]) -> pd.DataFrame:
    """시간외 포함 5분봉 (최근 5일). 인덱스 = 뉴욕 시각."""
    df = _download_filled(list(tickers), period="5d", interval="5m", prepost=True, auto_adjust=False)
    df.index = df.index.tz_convert(NY)
    return df


def market_session(now: pd.Timestamp) -> str:
    """뉴욕 시계 기준 현재 세션: 프리마켓 / 정규장 / 애프터마켓 / 휴장 (거래소 공휴일은 반영하지 않음)."""
    now = now.tz_convert(NY)
    hhmm = f"{now:%H:%M}"
    if now.weekday() >= 5 or not PRE <= hhmm < POST_END:
        return "휴장"
    return "프리마켓" if hhmm < REGULAR else "정규장" if hhmm < POST else "애프터마켓"


def extended_quote(bars: pd.Series, daily: pd.Series, now: pd.Timestamp) -> dict | None:
    """마지막 시간외 체결과 기준 종가 대비 등락률. 마지막 봉이 정규장이면 None.
    애프터마켓(16:00~20:00)은 그날 정규장 종가 대비, 프리마켓(04:00~09:30)은 전 거래일 정규장 종가 대비.
    daily: 정규장 일봉 종가(날짜 인덱스), now: 뉴욕 현재 시각."""
    bars = bars.dropna()
    if bars.empty or daily.dropna().empty:
        return None
    ts, price = bars.index[-1], float(bars.iloc[-1])
    hhmm, day = f"{ts:%H:%M}", pd.Timestamp(ts.date())
    daily = daily.dropna()
    daily = daily[~daily.index.duplicated(keep="last")]
    if POST <= hhmm < POST_END:
        session, base = "애프터마켓", daily[daily.index.normalize() <= day]
    elif PRE <= hhmm < REGULAR:
        session, base = "프리마켓", daily[daily.index.normalize() < day]
    else:
        return None
    if base.empty:
        return None
    base_close = float(base.iloc[-1])
    return {"session": session, "price": price, "pct": (price / base_close - 1) * 100,
            "time": ts.tz_convert("Asia/Seoul").tz_localize(None), "live": now - ts <= EXT_LIVE_AGE}


@st.cache_data(ttl=60, show_spinner=False)
def quote_table() -> pd.DataFrame:
    """주요 종목 시세표 (investing.com 스타일): 현재가, 전 거래일 종가 대비 변화·등락률, 상태, 기준 시각(KST).
    장중이면 1분봉 최신값(최대 15분 지연), 아니면 정규장 종가."""
    sectors = {**STOCK_SECTOR, **EXTRA_QUOTES}
    tickers = tuple(sectors)
    daily = get_closes(tickers, "1mo")
    missing = [t for t in tickers if t not in daily or daily[t].isna().all()]
    if missing:  # 일괄 조회에서 비어 온 종목은 다시 받아 채움 (get_closes 캐시에 빈 채로 5분간 남지 않게)
        try:
            daily = daily.drop(columns=[t for t in missing if t in daily]).join(
                _download_filled(missing, period="1mo", auto_adjust=True), how="outer")
        except Exception:
            pass
    closes, status = apply_market_status(daily)
    shares = get_shares(list(STOCK_SECTOR) + ["TSM"])  # ETF는 시가총액 개념이 없어 제외
    try:
        ext_bars = _extended_bars(tickers)
    except Exception:
        ext_bars = pd.DataFrame()
    now = pd.Timestamp.now(tz=NY)
    session = market_session(now)
    rows = []
    for t in tickers:
        s = closes[t].dropna() if t in closes else pd.Series(dtype=float)
        if len(s) < 2 or t not in status.index:
            continue
        last, prev = float(s.iloc[-1]), float(s.iloc[-2])
        ext = extended_quote(ext_bars[t], s, now) if t in ext_bars else None
        # 상태는 종목별 마지막 체결이 아니라 시계로 판정 (시간외에 거래가 뜸한 종목도 세션은 같음)
        state = {"프리마켓": "🌅 프리마켓", "애프터마켓": "🌙 애프터마켓"}.get(session, status.loc[t, "상태"])
        rows.append({"종목": KR_NAMES.get(t, t), "티커": t, "섹터": sectors[t], "현재가": last,
                     "전일 대비": last - prev, "등락률": (last / prev - 1) * 100,
                     "시간외 가격": ext["price"] if ext else None, "시간외 등락률": ext["pct"] if ext else None,
                     "시간외 구분": ext["session"] if ext else None, "시간외 시각": ext["time"] if ext else None,
                     "시가총액($bn)": shares[t] * last / 1e9 if t in shares else None,
                     "상태": state, "기준 시각": status.loc[t, "기준 시각"]})
    return pd.DataFrame(rows)


def breadth_history(closes: pd.DataFrame, windows=(50, 200)) -> pd.DataFrame:
    """이동평균 위에 있는 종목 비율(%) 이력. 열 = '50일선 위' 등. 이동평균을 계산할 수 있는 종목만 센다."""
    closes = closes.ffill()
    out = {}
    for w in windows:
        ma = closes.rolling(w, min_periods=w).mean()
        valid = ma.notna() & closes.notna()
        above = ((closes > ma) & valid).sum(axis=1).astype(float)
        out[f"{w}일선 위"] = above / valid.sum(axis=1).replace(0, float("nan")) * 100
    return pd.DataFrame(out).dropna()
