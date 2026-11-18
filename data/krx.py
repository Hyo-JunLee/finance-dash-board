"""KRX 정보데이터시스템 Open API — 국채전문유통시장 지표물 금리.

- 하루치씩만 조회되고 다음 영업일에 공개된다 (T+1). 장중 시세는 제공하지 않음
- 확정된 날짜(데이터가 있었던 날·지난 휴장일)는 디스크에 영구 저장해 다시 받지 않는다
- 서비스별 이용신청 승인 필요 (https://openapi.krx.co.kr)
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st

import config

API = "https://data-dbg.krx.co.kr/svc/apis"
DISK_DIR = config.CACHE_DIR / "krx"
DISK_DIR.mkdir(exist_ok=True)
_limit = threading.BoundedSemaphore(6)

KTS_TENORS = ["2Y", "3Y", "5Y", "10Y", "20Y", "30Y"]
REAL_10Y = "물가 10Y"


def _call(service: str, day: date) -> list[dict]:
    with _limit:
        r = requests.get(f"{API}/{service}", params={"basDd": day.strftime("%Y%m%d")},
                         headers={"AUTH_KEY": config.KRX_API_KEY}, timeout=20)
    r.raise_for_status()
    j = r.json()
    if "OutBlock_1" not in j:
        raise RuntimeError(f"KRX 응답 오류: {j}")
    return j["OutBlock_1"]


def fetch_day(service: str, day: date) -> list[dict]:
    """하루치 응답. 데이터가 있거나, 3일 이상 지난 빈 날(휴장일)은 영구 저장."""
    path = DISK_DIR / f"{service.replace('/', '_')}_{day:%Y%m%d}.json"
    if path.exists():
        return json.loads(path.read_text())
    rows = _call(service, day)
    if rows or day < date.today() - timedelta(days=3):
        path.write_text(json.dumps(rows, ensure_ascii=False))
    return rows


def _kts_row(rows: list[dict]) -> dict[str, float]:
    """지표물만: 만기별 종가 금리 + 물가채 10년."""
    out = {}
    for x in rows:
        if x.get("GOVBND_ISU_TP_NM") != "지표" or not x.get("CLSPRC_YD"):
            continue
        tenor = f"{x['BND_EXP_TP_NM']}Y"
        key = REAL_10Y if x["ISU_NM"].startswith("물가") else tenor
        out[key] = float(x["CLSPRC_YD"])
    return out


@st.cache_data(ttl=60 * 60, show_spinner=False)
def kts_yields(years: int = 1) -> pd.DataFrame:
    """국고채 지표물 일별 종가 금리(%). 열: 2Y … 30Y, 물가 10Y. 휴장일은 행 없음."""
    # '1년 전' 비교용으로 2주 여유를 더 받는다
    days = [d.date() for d in pd.bdate_range(date.today() - timedelta(days=365 * years + 14), date.today())]

    def one(d):
        try:
            return d, _kts_row(fetch_day("bon/kts_bydd_trd", d))
        except Exception:
            return d, {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = {d: r for d, r in ex.map(one, days) if r}
    df = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    df.index = pd.to_datetime(df.index)
    return df[[c for c in KTS_TENORS + [REAL_10Y] if c in df]]


# ── 국내 증시 (주식·ETF·지수) ─────────────────────────
STOCK_SERVICES = {"KOSPI": "sto/stk_bydd_trd", "KOSDAQ": "sto/ksq_bydd_trd"}
INDEX_SERVICES = {"KOSPI": "idx/kospi_dd_trd", "KOSDAQ": "idx/kosdaq_dd_trd"}
# KRX 업종 지수 (시장별). 코스피200 섹터·규모별 지수 등은 제외
SECTOR_INDICES = {
    "KOSPI": ["음식료·담배", "섬유·의류", "종이·목재", "화학", "제약", "비금속", "금속", "기계·장비", "전기전자",
              "의료·정밀기기", "운송장비·부품", "유통", "전기·가스", "건설", "운송·창고", "통신", "금융", "증권",
              "보험", "일반서비스", "오락·문화", "IT 서비스", "부동산"],
    "KOSDAQ": ["음식료·담배", "섬유·의류", "종이·목재", "출판·매체복제", "화학", "제약", "비금속", "금속",
               "기계·장비", "전기전자", "의료·정밀기기", "운송장비·부품", "기타제조", "건설", "유통", "운송·창고",
               "금융", "오락·문화", "통신", "IT 서비스", "일반서비스"],
}


def _num(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for c in cols:
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
    return df


@st.cache_data(ttl=60 * 10, show_spinner=False)
def latest_trading_day(service: str = "idx/kospi_dd_trd") -> date:
    """데이터가 있는 가장 최근 날짜 (KRX는 다음 영업일 공개라 보통 전 영업일)."""
    d = date.today()
    for _ in range(12):
        if fetch_day(service, d):
            return d
        d -= timedelta(days=1)
    raise RuntimeError("최근 12일 안에 KRX 데이터가 없습니다")


@st.cache_data(ttl=60 * 60, show_spinner=False)
def stocks_day(day: date) -> pd.DataFrame:
    """KOSPI·KOSDAQ 전 종목 일별 시세. 금액 단위: 원."""
    frames = [pd.DataFrame(fetch_day(svc, day)).assign(시장=mkt) for mkt, svc in STOCK_SERVICES.items()]
    df = pd.concat(frames, ignore_index=True)
    df = _num(df, ["TDD_CLSPRC", "CMPPREVDD_PRC", "FLUC_RT", "ACC_TRDVAL", "MKTCAP"])
    return df.rename(columns={"ISU_CD": "코드", "ISU_NM": "종목", "TDD_CLSPRC": "종가", "CMPPREVDD_PRC": "대비",
                              "FLUC_RT": "등락률", "ACC_TRDVAL": "거래대금", "MKTCAP": "시가총액"})[
        ["시장", "코드", "종목", "종가", "대비", "등락률", "거래대금", "시가총액"]]


@st.cache_data(ttl=60 * 60, show_spinner=False)
def etfs_day(day: date) -> pd.DataFrame:
    df = pd.DataFrame(fetch_day("etp/etf_bydd_trd", day))
    df = _num(df, ["TDD_CLSPRC", "FLUC_RT", "ACC_TRDVAL", "INVSTASST_NETASST_TOTAMT", "NAV"])
    return df.rename(columns={"ISU_NM": "ETF", "TDD_CLSPRC": "종가", "FLUC_RT": "등락률", "ACC_TRDVAL": "거래대금",
                              "INVSTASST_NETASST_TOTAMT": "순자산", "IDX_IND_NM": "기초지수"})[
        ["ETF", "종가", "등락률", "거래대금", "순자산", "NAV", "기초지수"]]


@st.cache_data(ttl=60 * 60, show_spinner=False)
def index_history(market: str, start: date, end: date) -> pd.DataFrame:
    """지수 종가 이력. 행=날짜, 열=지수명. 지난 날짜는 디스크 캐시라 처음 한 번만 오래 걸림."""
    days = [d.date() for d in pd.bdate_range(start, end)]

    def one(d):
        try:
            return d, {x["IDX_NM"]: x["CLSPRC_IDX"] for x in fetch_day(INDEX_SERVICES[market], d)}
        except Exception:
            return d, {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = {d: r for d, r in ex.map(one, days) if r}
    df = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    df.index = pd.to_datetime(df.index)
    return df.apply(lambda c: pd.to_numeric(c.astype(str).str.replace(",", ""), errors="coerce"))
