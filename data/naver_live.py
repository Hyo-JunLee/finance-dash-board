"""국내 증시 장중 실시간 목록 (네이버 증권 모바일 API, 비공식).

KRX 공식 데이터는 다음 영업일 공개(T+1)라 장중·당일 마감 직후엔 전 거래일 값만 있다.
그 사이에는 이 모듈의 값으로 '1일' 화면을 채운다. 반환 형식은 data/krx.py와 같은 열·단위(원)를 맞춘다.
실패하면 예외를 그대로 올려 화면이 KRX 전 거래일 데이터로 돌아가게 한다. 1분 캐시.
"""
import pandas as pd
import requests
import streamlit as st

_H = {"User-Agent": "Mozilla/5.0"}
_API = "https://m.stock.naver.com/api"
MARKETS = ("KOSPI", "KOSDAQ")
TTL = 60


def _get(url: str, **params) -> dict:
    r = requests.get(url, params=params, headers=_H, timeout=10)
    r.raise_for_status()
    return r.json()


def _num(v) -> float:
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return float("nan")


def _stocks(rows: list[dict], market: str) -> pd.DataFrame:
    """네이버 종목 목록 → KRX stocks_day와 같은 열 (금액 단위: 원) + 체결 시각·장 상태."""
    return pd.DataFrame([{
        "시장": market, "코드": x["itemCode"], "종목": x["stockName"],
        "종가": _num(x.get("closePriceRaw") or x["closePrice"]),
        "대비": _num(x.get("compareToPreviousClosePriceRaw") or x["compareToPreviousClosePrice"]),
        "등락률": _num(x["fluctuationsRatio"]),
        "거래대금": _num(x.get("accumulatedTradingValueRaw")),
        "시가총액": _num(x.get("marketValueRaw")),
        "시각": pd.Timestamp(x["localTradedAt"]).tz_convert("Asia/Seoul").tz_localize(None),
        "장중": x.get("marketStatus") == "OPEN",
    } for x in rows if x.get("stockEndType", "stock") == "stock"])  # ETF·ETN은 빼고 주식만 (KRX 종목 목록과 같게)


@st.cache_data(ttl=TTL, show_spinner=False)
def stock_list(sort: str, market: str, n: int = 100) -> pd.DataFrame:
    """sort: marketValue(시총) · up(상승률) · down(하락률) · priceTop(거래대금). 한 번에 최대 100개."""
    return _stocks(_get(f"{_API}/stocks/{sort}/{market}", page=1, pageSize=min(n, 100))["stocks"], market)


def stock_lists(sort: str, n: int = 100) -> pd.DataFrame:
    return pd.concat([stock_list(sort, m, n) for m in MARKETS], ignore_index=True)


@st.cache_data(ttl=TTL, show_spinner=False)
def industries() -> tuple[pd.DataFrame, bool]:
    """네이버 업종 분류(KOSPI·KOSDAQ 합산) 당일 등락률. 반환: (표[업종, 등락률, 상승, 하락, 보합, 종목수], 장중 여부)."""
    d = _get(f"{_API}/stocks/industry", page=1, pageSize=100)
    df = pd.DataFrame([{"업종": g["name"], "등락률": _num(g["changeRate"]), "상승": g["riseCount"],
                        "하락": g["fallCount"], "보합": g["steadyCount"], "종목수": g["totalCount"]}
                       for g in d["groups"]])
    return df, d.get("marketStatus") == "OPEN"


@st.cache_data(ttl=TTL, show_spinner=False)
def breadth() -> dict:
    """KOSPI·KOSDAQ 합산 상승·하락·보합 종목 수와 시장별 거래대금(원)."""
    out = {"상승": 0, "하락": 0, "보합": 0, "거래대금": {}}
    for m in MARKETS:
        d = _get(f"{_API}/index/{m}/integration")
        ud = d["upDownStockInfo"]
        out["상승"] += int(_num(ud["riseCount"]) + _num(ud["upperCount"]))
        out["하락"] += int(_num(ud["fallCount"]) + _num(ud["lowerCount"]))
        out["보합"] += int(_num(ud["steadyCount"]))
        value = next(x["value"] for x in d["totalInfos"] if x["code"] == "accumulatedTradingValue")
        out["거래대금"][m] = _num(value.replace("백만", "")) * 1e6
    return out


@st.cache_data(ttl=TTL, show_spinner=False)
def etfs() -> pd.DataFrame:
    """전체 ETF 현재가·등락률·거래대금·시가총액(원). 기초지수는 없음 (화면에서 KRX 것을 이름으로 붙인다)."""
    r = requests.get("https://finance.naver.com/api/sise/etfItemList.nhn", headers=_H, timeout=10)
    r.raise_for_status()
    r.encoding = "euc-kr"
    rows = r.json()["result"]["etfItemList"]
    return pd.DataFrame([{"ETF": x["itemname"], "종가": x["nowVal"], "등락률": x["changeRate"],
                          "거래대금": x["amonut"] * 1e6,      # 백만원 → 원
                          "순자산": x["marketSum"] * 1e8,     # 시가총액(억원) → 원. ETF는 순자산과 거의 같음
                          "NAV": x["nav"]} for x in rows])


def regular_session(now: pd.Timestamp | None = None) -> bool:
    """한국 정규장 시간(평일 09:00~15:30)인지. 네이버는 시간외 거래 동안에도 OPEN을 주므로 시각으로 한 번 더 거른다."""
    now = now or pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None)
    return now.weekday() < 5 and "09:00" <= f"{now:%H:%M}" < "15:31"


def live_status(df: pd.DataFrame, now: pd.Timestamp | None = None) -> tuple[pd.Timestamp, bool]:
    """목록의 최신 체결 시각과 장중 여부 (네이버 OPEN이면서 정규장 시간)."""
    return df["시각"].max(), bool(df["장중"].any()) and regular_session(now)


# ── 투자자별 매매동향 (순매수, 억원) ─────────────────────────
INVESTORS = {"foreignValue": "외국인", "institutionalValue": "기관", "personalValue": "개인"}
_TREND_DIR = None


def _trend_dir():
    global _TREND_DIR
    if _TREND_DIR is None:
        import config
        _TREND_DIR = config.CACHE_DIR / "naver_trend"
        _TREND_DIR.mkdir(exist_ok=True)
    return _TREND_DIR


def investor_day(market: str, day) -> dict | None:
    """그날 투자자별 순매수(억원) {외국인, 기관, 개인}. 휴장일(모두 0)은 None.
    지난 날짜는 디스크에 영구 저장 (오늘 값은 장중에 바뀌므로 저장하지 않음)."""
    import json
    from datetime import date
    f = _trend_dir() / f"{market}_{day:%Y%m%d}.json"
    if day < date.today() and f.exists():
        d = json.loads(f.read_text())
    else:
        d = _get(f"{_API}/index/{market}/trend", bizdate=f"{day:%Y%m%d}")
        if day < date.today():
            f.write_text(json.dumps(d))
    row = {name: _num(d.get(k, "0")) for k, name in INVESTORS.items()}
    return None if all(v == 0 for v in row.values()) else row


@st.cache_data(ttl=TTL, show_spinner=False)
def investor_history(market: str, days: int = 20) -> pd.DataFrame:
    """최근 영업일 투자자별 순매수(억원). 행=날짜(오래된 것부터), 열=외국인·기관·개인. 오늘(장중)은 잠정치."""
    from concurrent.futures import ThreadPoolExecutor
    from datetime import date
    cands = [d.date() for d in pd.bdate_range(end=date.today(), periods=int(days * 1.5) + 3)]
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = dict(zip(cands, ex.map(lambda d: investor_day(market, d), cands)))
    df = pd.DataFrame.from_dict({d: r for d, r in rows.items() if r}, orient="index").sort_index()
    df.index = pd.to_datetime(df.index)
    return df.tail(days)


@st.cache_data(ttl=TTL, show_spinner=False)
def program_today(market: str) -> dict:
    """오늘 프로그램 순매수(억원) {차익, 비차익, 합계, 날짜}."""
    p = _get(f"{_API}/index/{market}/integration")["programTrendInfo"]
    return {"차익": _num(p["indexDifferenceReal"]), "비차익": _num(p["indexBiDifferenceReal"]),
            "합계": _num(p["indexTotalReal"]), "날짜": pd.Timestamp(p["bizdate"])}
