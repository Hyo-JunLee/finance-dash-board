"""채권 화면: 수익률 곡선 분해, 곡선 움직임 분류, 국채 입찰, 연준 유동성 (모두 무료 소스).

- 실질금리·기대인플레: 재무부 명목/실질(TIPS) 수익률 곡선 CSV (당일 종가)
- 텀 프리미엄: 뉴욕 연은 ACM 모델 일별 엑셀 (수일 지연)
- 국채 입찰: 재무부 FiscalData auctions_query API
- 연준 유동성: FRED (연준 자산 WALCL, 재무부 계좌 WTREGEN, 역레포 RRPONTSYD, 지준 WRESBAL)
"""
import io
import time
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st

import config
from data.fred import fetch_series
from data.rates import NOMINAL, REAL, get_treasury_curve

_HEADERS = {"User-Agent": "Mozilla/5.0"}


# ── 1. 수익률 곡선 분해 ──────────────────────────────
@st.cache_data(ttl=60 * 60, show_spinner=False)
def real_bei_10y(years: int = 10) -> pd.DataFrame:
    """10년물 명목 = 실질(TIPS) + 기대인플레(BEI). 재무부 종가 기준, 단위 %."""
    start = date.today() - timedelta(days=365 * years)
    nom = get_treasury_curve(start, NOMINAL)["10 Yr"]
    real = get_treasury_curve(start, REAL)["10 YR"]
    df = pd.DataFrame({"명목 10년": nom, "실질 10년": real}).dropna()
    df["기대인플레(BEI)"] = df["명목 10년"] - df["실질 10년"]
    return df


ACM_URL = "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xls"
ACM_CACHE = config.CACHE_DIR / "acm_daily.pkl"


@st.cache_data(ttl=60 * 60, show_spinner=False)
def acm_10y() -> pd.DataFrame:
    """ACM 모델 10년물 = 기대 단기금리 경로(위험중립 금리) + 텀 프리미엄. 엑셀이 10MB라 12시간마다만 받음."""
    if ACM_CACHE.exists() and time.time() - ACM_CACHE.stat().st_mtime < 12 * 3600:
        return pd.read_pickle(ACM_CACHE)
    try:
        r = requests.get(ACM_URL, headers=_HEADERS, timeout=60)
        r.raise_for_status()
        raw = pd.read_excel(io.BytesIO(r.content), sheet_name="ACM Daily")
    except Exception:
        if ACM_CACHE.exists():
            return pd.read_pickle(ACM_CACHE)
        raise
    raw.index = pd.to_datetime(raw.pop("DATE"), format="%d-%b-%Y")
    df = raw.rename(columns={"ACMY10": "ACM 10년", "ACMRNY10": "기대 단기금리 경로", "ACMTP10": "텀 프리미엄"})
    df = df[["ACM 10년", "기대 단기금리 경로", "텀 프리미엄"]].sort_index()
    df.to_pickle(ACM_CACHE)
    return df


def change_bp(df: pd.DataFrame, since: pd.Timestamp) -> pd.Series:
    """since 시점(그 이전 마지막 관측) 대비 각 열의 변화(bp)."""
    base = df[df.index <= since]
    if base.empty:
        base = df.iloc[:1]
    return (df.iloc[-1] - base.iloc[-1]) * 100


# 요인별 해석 문장 (상승 시, 하락 시). 원인은 단정하지 않고 '시사'·'해석 가능' 수준으로.
# 실질금리의 원인(기대 경로 vs 실질 텀프리미엄)은 패널 하나로는 구분할 수 없어 종합 판정(data/bond_attribution.py)에 맡긴다
FACTOR_SENTENCE = {
    "실질금리": ("실질금리 상승의 원인(기대 경로 vs 실질 텀프리미엄)은 아래 종합 판정을 참조하세요",
               "실질금리 하락의 원인(기대 경로 vs 실질 텀프리미엄)은 아래 종합 판정을 참조하세요"),
    "기대인플레": ("기대인플레 상승은 물가 전망 또는 인플레 리스크 프리미엄의 상향을 시사합니다",
                "기대인플레 하락은 물가 전망 또는 인플레 리스크 프리미엄의 하향을 시사합니다"),
    "기대 금리경로": ("기대 금리경로 상승은 시장이 기준금리 경로를 더 높게 보게 됐음을 시사합니다",
                  "기대 금리경로 하락은 시장이 기준금리 경로를 더 낮게 보게 됐음을 시사합니다"),
    "텀 프리미엄": ("텀 프리미엄 상승은 장기채 보유 보상의 확대로 해석 가능하며, 재정 우려·국채 공급·불확실성 등이 원인일 수 있습니다",
                "텀 프리미엄 하락은 장기채 보유 보상의 축소로 해석 가능하며, 안전자산 수요·불확실성 완화 등이 원인일 수 있습니다"),
}
SMALL_BP = 3      # 이보다 작은 변화는 '미미'
LEAD_SHARE = 0.7  # 같은 방향일 때 한쪽 기여가 이 이상이면 '주도'


def josa(word: str, with_final: str, without_final: str) -> str:
    """마지막 글자 받침 유무로 조사 선택: josa('실질금리', '은', '는') → '는'."""
    last = word.strip()[-1]
    if "가" <= last <= "힣":
        return with_final if (ord(last) - ord("가")) % 28 else without_final
    return without_final


def explain_change(total: float, parts: dict[str, float]) -> str:
    """금리 변화(bp)를 두 요인으로 나눈 결과를 규칙 기반 문장으로 설명. parts = {요인명: 변화bp} (2개)."""
    (n1, v1), (n2, v2) = parts.items()
    move = "상승" if total > 0 else "하락"
    fmt = lambda v: f"{v:+.0f}bp"

    if abs(total) < SMALL_BP and abs(v1) < SMALL_BP and abs(v2) < SMALL_BP:
        return f"10년물 {fmt(total)}로 변화가 거의 없었습니다 (두 요인 모두 ±{SMALL_BP}bp 미만)."

    def sentence(n, v):
        return FACTOR_SENTENCE[n][0 if v > 0 else 1]

    if v1 * v2 < 0 and min(abs(v1), abs(v2)) >= SMALL_BP:
        lead, lv = (n1, v1) if abs(v1) >= abs(v2) else (n2, v2)
        return (f"{n1} {fmt(v1)}, {n2} {fmt(v2)}가 서로 상쇄돼 10년물은 {fmt(total)}에 그쳤습니다. "
                f"더 크게 움직인 쪽은 {lead}입니다. {sentence(lead, lv)}.")

    share1 = abs(v1) / (abs(v1) + abs(v2)) if (abs(v1) + abs(v2)) else 0.5
    if share1 >= LEAD_SHARE or share1 <= 1 - LEAD_SHARE:
        lead, lv, share = (n1, v1, share1) if share1 >= LEAD_SHARE else (n2, v2, 1 - share1)
        return (f"10년물 {fmt(total)} {move}은 주로 **{lead}**({fmt(lv)}, 기여 {share:.0%}){josa(lead, '이', '가')} "
                f"이끌었습니다. {sentence(lead, lv)}.")

    return (f"10년물 {fmt(total)} {move}에 **{n1}**({fmt(v1)}){josa(n1, '과', '와')} **{n2}**({fmt(v2)}){josa(n2, '이', '가')} "
            f"비슷하게 기여했습니다. {sentence(n1, v1)}. {sentence(n2, v2)}.")


# ── 2. 곡선 움직임 분류 ──────────────────────────────
REGIMES = {
    "불 스티프닝": "금리 하락 + 곡선 가팔라짐 (보통 단기물이 더 하락: 금리 인하 기대)",
    "베어 스티프닝": "금리 상승 + 곡선 가팔라짐 (보통 장기물이 더 상승: 텀 프리미엄·재정 우려)",
    "불 플래트닝": "금리 하락 + 곡선 평탄해짐 (보통 장기물이 더 하락: 경기 둔화·안전자산 선호)",
    "베어 플래트닝": "금리 상승 + 곡선 평탄해짐 (보통 단기물이 더 상승: 긴축 기대)",
    "변화 미미": "수준·기울기 변화가 모두 0.5bp 미만",
}


def classify_move(d2: float, d10: float, eps: float = 0.5) -> str:
    """2년·10년 변화(bp)로 국면 분류. 수준 = 두 변화의 평균, 기울기 = 10년 변화 − 2년 변화."""
    level, slope = (d2 + d10) / 2, d10 - d2
    if abs(level) < eps and abs(slope) < eps:
        return "변화 미미"
    bull = level < 0
    steep = slope > 0
    return f"{'불' if bull else '베어'} {'스티프닝' if steep else '플래트닝'}"


@st.cache_data(ttl=60 * 60, show_spinner=False)
def curve_moves(freq: str = "D", years: int = 3) -> pd.DataFrame:
    """기간별(일/주/월) 2년·10년 변화와 국면. freq: D, W(금요일 기준), M(월말)."""
    curve = get_treasury_curve(date.today() - timedelta(days=365 * years + 40), NOMINAL)[["2 Yr", "10 Yr"]].dropna()
    last_day = curve.index[-1]
    if freq in ("W", "M"):
        curve = curve.resample("W-FRI" if freq == "W" else "ME").last()
        # 진행 중인 주·월은 미래 날짜(이번 주 금요일·월말) 대신 실제 마지막 거래일로 표시
        curve = curve.rename(index={curve.index[-1]: last_day})
    d = (curve.diff() * 100).dropna()
    out = pd.DataFrame({"2년 변화(bp)": d["2 Yr"], "10년 변화(bp)": d["10 Yr"]})
    out["기울기 변화(bp)"] = out["10년 변화(bp)"] - out["2년 변화(bp)"]
    out["국면"] = [classify_move(a, b) for a, b in zip(out["2년 변화(bp)"], out["10년 변화(bp)"])]
    return out


# ── 3. 국채 입찰 ─────────────────────────────────────
# 재무부 FiscalData(auctions_query)는 2026-10 말 이후 결과 갱신이 멈춰 TreasuryDirect로 교체 (2026-11-18 확인)
TD_API = "https://www.treasurydirect.gov/TA_WS/securities"
_TD_HEADERS = {"User-Agent": "Mozilla/5.0"}
# 원래 만기 → 재무부 수익률 곡선 컬럼 (근사 테일 계산용)
TERM_TO_CURVE = {"2-Year": "2 Yr", "3-Year": "3 Yr", "5-Year": "5 Yr", "7-Year": "7 Yr",
                 "10-Year": "10 Yr", "20-Year": "20 Yr", "30-Year": "30 Yr"}


def _num(x):
    return pd.to_numeric(pd.Series(x).replace({"null": None, "": None}), errors="coerce")


def _td(kind: str, sec_type: str, size: int) -> list[dict]:
    """kind: auctioned(결과) / announced(예정)."""
    r = requests.get(f"{TD_API}/{kind}", params={"format": "json", "type": sec_type, "pagesize": size},
                     headers=_TD_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=60 * 60, show_spinner=False)
def auctions(months: int = 24) -> pd.DataFrame:
    """명목 고정금리 국채(Note·Bond) 입찰. TIPS·FRN 제외. 예정된 입찰도 포함(결과 열은 NaN)."""
    rows = [x for t in ("Note", "Bond") for kind in ("auctioned", "announced") for x in _td(kind, t, 250)]
    df = pd.DataFrame(rows).drop_duplicates("cusip", keep="first")  # 결과(auctioned)를 우선
    df = df[(df["floatingRate"] != "Yes") & (df["inflationProtected"] != "Yes")].copy()
    df["auction_date"] = pd.to_datetime(df["competitiveAuctionCloseDate"]).dt.tz_localize(None).dt.normalize()
    df = df[df["auction_date"] >= pd.Timestamp(date.today() - timedelta(days=30 * months))]
    for src, dst in [("highYield", "high_yield"), ("bidToCoverRatio", "bid_to_cover_ratio"),
                     ("indirectBiddersCompetitiveParAwarded", "indirect_bidder_accepted"),
                     ("directBiddersCompetitiveParAwarded", "direct_bidder_accepted"),
                     ("primaryDealersCompetitiveParAwarded", "primary_dealer_accepted"),
                     ("subtotalCompetitiveParAwardedInsideOfOfferingAmount", "comp_accepted"),
                     ("offeringAmount", "offering_amt")]:
        df[dst] = _num(df[src]).values
    df = df.rename(columns={"originalSecurityTerm": "original_security_term", "securityTerm": "security_term"})
    comp = df["comp_accepted"]
    out = pd.DataFrame({
        "입찰일": df["auction_date"],
        "만기": df["original_security_term"],
        "종목": df["security_term"],
        "재개행": df["reopening"] == "Yes",
        "발행액($bn)": df["offering_amt"] / 1e9,
        "낙찰금리(%)": df["high_yield"],
        "응찰률": df["bid_to_cover_ratio"],
        "해외(%)": df["indirect_bidder_accepted"] / comp * 100,
        "직접(%)": df["direct_bidder_accepted"] / comp * 100,
        "딜러(%)": df["primary_dealer_accepted"] / comp * 100,
    }).sort_values("입찰일").reset_index(drop=True)

    # 같은 만기 직전 6회 평균 대비 (예정 입찰은 계산 안 함)
    done = out["응찰률"].notna()
    for col in ["응찰률", "해외(%)", "딜러(%)"]:
        avg = out[done].groupby("만기")[col].transform(lambda s: s.shift(1).rolling(6, min_periods=3).mean())
        out.loc[done, f"{col} 6회평균"] = avg

    # 근사 테일: 낙찰금리 − 입찰 전 영업일 재무부 종가(같은 만기 상수만기 금리). 실제 테일은 WI 금리(유료) 기준
    curve = get_treasury_curve(date.today() - timedelta(days=30 * months + 10), NOMINAL)
    tails = []
    for day, term, high_yield in zip(out["입찰일"], out["만기"], out["낙찰금리(%)"]):
        col = TERM_TO_CURVE.get(term)
        prev = curve[curve.index < day]
        if col and col in prev and len(prev) and pd.notna(high_yield):
            tails.append((high_yield - prev[col].iloc[-1]) * 100)
        else:
            tails.append(float("nan"))
    out["근사 테일(bp)"] = tails
    out["평가"] = [_grade(r) for r in out.to_dict("records")]
    return out.sort_values("입찰일", ascending=False).reset_index(drop=True)


def _grade(r: dict) -> str:
    """응찰률이 높고 딜러 비중이 낮으면 수요 강함. 직전 6회 평균과 비교."""
    if pd.isna(r.get("응찰률")):
        return "예정"
    btc, btc_avg, dealer, dealer_avg = r["응찰률"], r.get("응찰률 6회평균"), r["딜러(%)"], r.get("딜러(%) 6회평균")
    if pd.isna(btc_avg) or pd.isna(dealer_avg):
        return "-"
    if btc >= btc_avg and dealer <= dealer_avg:
        return "💪 강함"
    if btc < btc_avg and dealer > dealer_avg:
        return "⚠️ 약함"
    return "보통"


# ── 4. 연준 유동성 ───────────────────────────────────
@st.cache_data(ttl=config.TTL_MACRO, show_spinner=False)
def fed_liquidity(years: int = 5) -> pd.DataFrame:
    """주간(수요일) 기준, 단위 $bn. 순유동성 = 연준 자산 − 재무부 계좌(TGA) − 역레포(RRP)."""
    walcl = fetch_series("WALCL") / 1000          # 백만 달러 → 십억 달러
    tga = fetch_series("WTREGEN") / 1000
    reserves = fetch_series("WRESBAL") / 1000
    rrp = fetch_series("RRPONTSYD")                # 이미 십억 달러 (일별)
    weekly_idx = walcl.index
    rrp_w = rrp.reindex(rrp.index.union(weekly_idx)).ffill().reindex(weekly_idx)
    df = pd.DataFrame({"연준 자산": walcl, "재무부 계좌(TGA)": tga.reindex(weekly_idx),
                       "역레포(RRP)": rrp_w, "지급준비금": reserves.reindex(weekly_idx)}).dropna()
    df["순유동성"] = df["연준 자산"] - df["재무부 계좌(TGA)"] - df["역레포(RRP)"]
    return df[df.index >= pd.Timestamp.now() - pd.DateOffset(years=years)]
