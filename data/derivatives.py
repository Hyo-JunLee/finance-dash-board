"""선물·옵션 관련 지표 (국내·미국 증시 화면의 '🎲' 섹션).

미국
- VIX 기간구조: VIX9D · VIX(1개월) · VIX3M · VIX6M (Yahoo). VIX/VIX3M > 1 = 백워데이션(단기 공포 > 장기)
- SKEW(꼬리위험 헤지 수요) · VVIX(VIX의 변동성) (Yahoo)
- CFTC TFF S&P 500 선물 통합(13874+): 자산운용사·레버리지 펀드 순포지션 (주간, 화요일 기준·금요일 발표)
국내
- 코스피 200 선물(근월물, 네이버): 가격·베이시스(선물 − 현물)·투자자별 순매수(단위 미표기, 계약으로 보임)
공통
- 옵션 만기일: 국내 매월 둘째 목요일, 미국 매월 셋째 금요일, 3·6·9·12월은 동시만기(규칙 계산, 휴일 미반영)
"""
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

VIX_TERM = {"^VIX9D": "9일", "^VIX": "1개월", "^VIX3M": "3개월", "^VIX6M": "6개월"}
TAIL = {"^SKEW": "SKEW", "^VVIX": "VVIX"}
CFTC_TFF = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
SPX_CONSOLIDATED = "13874+"
_H = {"User-Agent": "Mozilla/5.0"}
_NAVER = "https://m.stock.naver.com/api/index"


@st.cache_data(ttl=600, show_spinner=False)
def vol_indices(years: int = 1) -> pd.DataFrame:
    """VIX 기간구조 + SKEW·VVIX 일봉 종가 (열 = 티커)."""
    tickers = list(VIX_TERM) + list(TAIL)
    df = yf.download(tickers, period=f"{years}y", progress=False, auto_adjust=False)["Close"]
    df = df.dropna(how="all")
    if df.empty:
        raise RuntimeError("Yahoo 변동성 지수 빈 응답")
    return df


def term_state(ratio: float) -> str:
    """VIX/VIX3M 비율 해석."""
    if ratio >= 1.0:
        return "백워데이션 — 단기 공포가 장기보다 큼 (급락·스트레스 국면에 흔함)"
    if ratio >= 0.9:
        return "평평 — 경계 구간"
    return "콘탱고 — 평상시 (단기 변동성 기대가 장기보다 낮음)"


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def cftc_spx(years: int = 3) -> pd.DataFrame:
    """S&P 500 선물(통합) 자산운용사·레버리지 펀드 순포지션 (계약). 인덱스 = 기준일(화)."""
    start = (date.today() - timedelta(days=365 * years)).isoformat()
    r = requests.get(CFTC_TFF, params={
        "$select": "report_date_as_yyyy_mm_dd,asset_mgr_positions_long,asset_mgr_positions_short,"
                   "lev_money_positions_long,lev_money_positions_short",
        "$where": f"cftc_contract_market_code='{SPX_CONSOLIDATED}' AND report_date_as_yyyy_mm_dd >= '{start}'",
        "$limit": 5000}, timeout=30)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    if df.empty:
        raise RuntimeError("CFTC S&P 500 포지션 빈 응답")
    num = lambda c: pd.to_numeric(df[c], errors="coerce")
    out = pd.DataFrame({"자산운용사": num("asset_mgr_positions_long") - num("asset_mgr_positions_short"),
                        "레버리지 펀드": num("lev_money_positions_long") - num("lev_money_positions_short")})
    out.index = pd.to_datetime(df["report_date_as_yyyy_mm_dd"])
    return out.groupby(level=0).sum().sort_index()


# ── 국내: 코스피 200 선물 ───────────────────────────────
def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").replace("+", ""))
    except ValueError:
        return float("nan")


@st.cache_data(ttl=60, show_spinner=False)
def kr_futures() -> dict:
    """코스피 200 선물 근월물: 현재가·전일 대비·장중 여부·체결 시각·월물 + 당일 투자자별 순매수."""
    b = requests.get(f"{_NAVER}/FUT/basic", headers=_H, timeout=10).json()
    i = requests.get(f"{_NAVER}/FUT/integration", headers=_H, timeout=10).json()
    deal = i.get("dealTrendInfo") or {}
    name = i.get("stockName") or b.get("stockName", "코스피 200 선물")
    return {"name": name, "price": _num(b["closePrice"]), "chg": _num(b["compareToPreviousClosePrice"]),
            "pct": _num(b["fluctuationsRatio"]), "open": b.get("marketStatus") == "OPEN",
            "time": pd.Timestamp(b["localTradedAt"]).tz_convert("Asia/Seoul").tz_localize(None),
            "investors": {"외국인": _num(deal.get("foreignValue")), "기관": _num(deal.get("institutionalValue")),
                          "개인": _num(deal.get("personalValue"))}, "bizdate": deal.get("bizdate")}


@st.cache_data(ttl=120, show_spinner=False)
def kr_futures_investors(days: int = 20) -> pd.DataFrame:
    """코스피 200 선물 투자자별 순매수 최근 days 거래일 (지난 날짜는 네이버 trend?bizdate, 휴장일 = 0 은 제외)."""
    from concurrent.futures import ThreadPoolExecutor
    cands = [d.date() for d in pd.bdate_range(end=date.today(), periods=int(days * 1.5) + 3)]

    def one(d):
        try:
            j = requests.get(f"{_NAVER}/FUT/trend", params={"bizdate": f"{d:%Y%m%d}"}, headers=_H, timeout=10).json()
        except Exception:
            return d, None
        row = {"외국인": _num(j.get("foreignValue")), "기관": _num(j.get("institutionalValue")),
               "개인": _num(j.get("personalValue"))}
        return d, None if all(v == 0 for v in row.values()) else row
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = dict(ex.map(one, cands))
    df = pd.DataFrame.from_dict({d: r for d, r in rows.items() if r}, orient="index").sort_index()
    df.index = pd.to_datetime(df.index)
    return df.tail(days)


# ── 옵션 만기일 ─────────────────────────────────────────
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def expiries(today: date, months: int = 3) -> list[dict]:
    """앞으로 months개월 안의 옵션 만기일 [{날짜, 시장, 이름}]. 국내 둘째 목요일, 미국 셋째 금요일, 분기월은 동시만기."""
    out = []
    for k in range(months + 1):
        y, m = today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1
        quarter = m in (3, 6, 9, 12)
        kr = _nth_weekday(y, m, 3, 2)
        us = _nth_weekday(y, m, 4, 3)
        out.append({"날짜": kr, "시장": "국내", "이름": "선물·옵션 동시만기" if quarter else "옵션 만기"})
        out.append({"날짜": us, "시장": "미국", "이름": "쿼드러플 위칭" if quarter else "월물 옵션 만기"})
    return sorted([e for e in out if e["날짜"] >= today], key=lambda e: e["날짜"])
