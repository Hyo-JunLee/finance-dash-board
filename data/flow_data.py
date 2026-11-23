"""수급·포지션 지표 수집 (채권 › 곡선 분해 › 수급·포지션). 판정은 data/flow_regime.py.

소스 (2026-11-20 실제 호출로 확인)
- CFTC TFF Futures Only (publicreporting.cftc.gov, 데이터셋 gpe5-46if): 레버리지 펀드 국채 선물 포지션.
  화요일 기준·보통 금요일 발표. 울트라본드처럼 계약명이 바뀐 적이 있어 계약 코드로 조회
- FRED SOFR·IORB·RPONTSYD(연준 익일물 레포 국채 매입 = 상설 레포기구 SRF 이용액, $bn)·SP500·DGS10
- Yahoo ^MOVE. 실패하면 DGS10 20영업일 일간 변화 표준편차(bp)로 대체
- 입찰: data.bonds.auctions() (TreasuryDirect. FiscalData는 2026-10 말 갱신이 멈춤). 테일은 WI 금리가 없어 생략
- 스왑스프레드: 무료 SOFR 스왑금리 소스 없음 (FRED DSWP10·30은 LIBOR 기준으로 2016년 중단) → 표시만 '데이터 없음'
"""
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st

import config
from data import flow_regime as fr
from data.fred import fetch_series

CFTC_TFF = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
DAY, HALF_DAY = 60 * 60 * 24, 60 * 60 * 12
AUCTION_TERMS = ["2-Year", "5-Year", "7-Year", "10-Year", "20-Year", "30-Year"]

# 판정에 쓰는 스트레스 지표 이름 (화면·문구 공통)
LEV, REPO, VOL, AUCTION = "레버리지 펀드 주간 변화", "SOFR−IORB", "금리 변동성", "입찰 약세"


@st.cache_data(ttl=HALF_DAY, show_spinner=False)
def leveraged_funds(years: int = 4) -> pd.DataFrame:
    """레버리지 펀드 순포지션(long − short, 계약 수). 열 = 계약 이름, 인덱스 = 기준일(화)."""
    start = (date.today() - timedelta(days=365 * years)).isoformat()
    codes = ",".join(f"'{c}'" for c in config.TSY_FUTURES)
    r = requests.get(CFTC_TFF, params={
        "$select": "report_date_as_yyyy_mm_dd,cftc_contract_market_code,lev_money_positions_long,lev_money_positions_short",
        "$where": f"cftc_contract_market_code in({codes}) AND report_date_as_yyyy_mm_dd >= '{start}'",
        "$limit": 50000}, timeout=30)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    df["date"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"])
    df["net"] = pd.to_numeric(df["lev_money_positions_long"]) - pd.to_numeric(df["lev_money_positions_short"])
    df["name"] = df["cftc_contract_market_code"].map(lambda c: config.TSY_FUTURES[c][0])
    return df.pivot_table(index="date", columns="name", values="net", aggfunc="sum").sort_index()


def lev_10y_equiv() -> pd.Series:
    dv01 = {name: v for name, v in config.TSY_FUTURES.values()}
    return fr.ten_year_equiv(leveraged_funds(), dv01, "10년")


@st.cache_data(ttl=DAY, show_spinner=False)
def fred_daily(series_id: str) -> pd.Series:
    return fetch_series(series_id).dropna()


def sofr_iorb() -> pd.Series:
    """SOFR − IORB (bp). IORB는 2021-07-29부터."""
    df = pd.concat({"sofr": fred_daily("SOFR"), "iorb": fred_daily("IORB")}, axis=1).dropna()
    return (df["sofr"] - df["iorb"]) * 100


@st.cache_data(ttl=DAY, show_spinner=False)
def rate_vol() -> tuple[pd.Series, str, bool]:
    """(시계열, 이름, 대체 여부). MOVE를 못 받으면 10년물 20영업일 실현변동성(bp/일)."""
    try:
        from data.macro import yahoo
        s = yahoo("^MOVE", years=4)
        if len(s) > 260:
            return s, "MOVE 지수", False
    except Exception:
        pass
    y = fred_daily("DGS10")
    return (y.diff() * 100).rolling(20).std().dropna(), "10년물 20일 실현변동성 (MOVE 대체)", True


def stock_bond_corr(window: int) -> pd.Series:
    return fr.stock_bond_corr(fred_daily("SP500"), fred_daily("DGS10"), window).dropna()


def auction_table() -> pd.DataFrame:
    from data.bonds import auctions
    a = auctions(months=36)
    return a[a["만기"].isin(AUCTION_TERMS) & a["응찰률"].notna()].sort_values("입찰일")


def stress_series(z_window: int) -> tuple[dict[str, pd.Series | None], dict[str, str]]:
    """스트레스 지표별 z 시계열(실패하면 None)과 오류 메모. z_window는 영업일 기준(주간 데이터는 /5)."""
    out, errors = {}, {}

    def attempt(name, fn):
        try:
            out[name] = fn()
        except Exception as e:
            out[name], errors[name] = None, f"{type(e).__name__}: {str(e)[:80]}"

    attempt(LEV, lambda: fr.rolling_z(lev_10y_equiv().diff(), max(round(z_window * 52 / 252), 8)))
    attempt(REPO, lambda: fr.rolling_z(sofr_iorb(), z_window))
    attempt(VOL, lambda: fr.rolling_z(rate_vol()[0], z_window))
    attempt(AUCTION, lambda: fr.auction_weakness(auction_table(), window_days=round(z_window * 365 / 252)))
    return out, errors
