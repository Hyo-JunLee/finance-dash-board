"""수급·포지션 판정: 10년물 금리 변동이 '정보에 의한 재가격'인지 '포지션 정리·강제 매매'인지.

기존 종합 판정(bond_attribution.attribute)이 커브·실질/BEI·기대경로/텀프리미엄으로 원인을 고른다면,
이 모듈은 그 움직임이 수급 스트레스를 동반했는지를 본다.

스트레스 지표 (각각 1년 롤링 z-score)
- 레버리지 펀드 국채 선물 순포지션(10년물 환산)의 주간 변화 — 수준은 베이시스 트레이드라 방향성이 아니므로 급변을 본다
- SOFR − IORB (레포 자금 압박)
- 금리 변동성 (MOVE, 없으면 10년물 실현변동성)
- 입찰 약세 (응찰률 하락·딜러 인수 비율 상승, 같은 만기 직전 6회 평균 대비)
|z| ≥ 임계값인 지표 수로 '높음·주의·조용함'을 정하고, 데이터 없는 지표는 판정에서 빼고 문구에 적는다.

Streamlit에 의존하지 않는 순수 함수 (tests/test_flow_regime.py). 임계값은 모두 인자로 받는다.
"""
import math

import pandas as pd

LEVELS = ("조용함", "주의", "높음")


def rolling_z(s: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    """직전 window개(현재 포함) 평균·표준편차 기준 z. 표준편차 0이면 NaN."""
    mp = min_periods or max(window // 2, 10)
    roll = s.rolling(window, min_periods=mp)
    sd = roll.std()
    return (s - roll.mean()) / sd.where(sd > 0)


def window_peak(z: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> tuple[float, pd.Timestamp] | None:
    """(start, end] 안에서 |z| 최대값과 날짜. 그 안에 관측이 없으면(주간 데이터 등) end 이전 마지막 값.
    end 이전 데이터가 아예 없으면 None. start=end로 주면 '현재 상태'(마지막 값)."""
    z = z.dropna()
    z = z[z.index <= end]
    if z.empty:
        return None
    w = z[z.index > start]
    if w.empty:
        w = z.iloc[-1:]
    at = w.abs().idxmax()
    return float(w[at]), at


def ten_year_equiv(net: pd.DataFrame, dv01: dict[str, float], ref: str) -> pd.Series:
    """계약별 순포지션(계약 수) × 계약당 DV01 합계를 ref 계약 DV01로 나눈 '10년물 환산 계약 수'."""
    cols = [c for c in net.columns if c in dv01]
    risk = net[cols].mul(pd.Series(dv01)[cols], axis=1).sum(axis=1, min_count=1)
    return risk / dv01[ref]


def stock_bond_corr(spx: pd.Series, y10: pd.Series, window: int = 60) -> pd.Series:
    """주식·채권 수익률 롤링 상관 (업계 관례). 채권 일간 수익률은 −Δ10년물 금리로 근사
    (듀레이션을 곱해도 상관계수는 그대로).
    음수 = 주가가 빠지는 날 채권값이 오름 → 채권 헤지 작동.
    양수 = 주가가 빠지는 날 채권값도 빠짐 → 주식·채권 동반 움직임 (2022년 같은 인플레 국면)."""
    df = pd.concat({"spx": spx, "y": y10}, axis=1).dropna()
    return df["spx"].pct_change().rolling(window).corr(-df["y"].diff())


def auction_weakness(df: pd.DataFrame, window_days: int = 365, min_obs: int = 8) -> pd.Series:
    """입찰 약세 z (입찰일 인덱스, 같은 날 여러 입찰이면 가장 약한 값).
    입찰마다 ① −(응찰률 − 직전 6회 평균) ② 딜러 비율 − 직전 6회 평균을 직전 window_days 동안의 입찰
    분포로 각각 z화해 평균. 양수일수록 약한 입찰. 필요한 열: 입찰일, 응찰률, 응찰률 6회평균, 딜러(%), 딜러(%) 6회평균."""
    d = df.dropna(subset=["응찰률", "응찰률 6회평균", "딜러(%)", "딜러(%) 6회평균"]).sort_values("입찰일")
    dev = pd.DataFrame({"btc": -(d["응찰률"] - d["응찰률 6회평균"]).values,
                        "dealer": (d["딜러(%)"] - d["딜러(%) 6회평균"]).values}, index=d["입찰일"].values)
    scores = []
    for day, row in dev.iterrows():
        ref = dev[(dev.index > day - pd.Timedelta(days=window_days)) & (dev.index <= day)]
        if len(ref) < min_obs:
            scores.append(float("nan"))
            continue
        sd = ref.std()
        zs = [(row[c] - ref[c].mean()) / sd[c] for c in ("btc", "dealer") if sd[c] > 0]
        scores.append(sum(zs) / len(zs) if zs else float("nan"))
    return pd.Series(scores, index=dev.index, dtype=float).groupby(level=0).max()


def stress_level(zs: dict[str, float | None], z_threshold: float = 2.0, count_high: int = 2) -> dict:
    """지표별 z(없으면 None)로 수급 스트레스 단계. 반환: level, hits(임계 초과), used, excluded(데이터 없음)."""
    excluded = [k for k, v in zs.items() if v is None or (isinstance(v, float) and math.isnan(v))]
    used = {k: v for k, v in zs.items() if k not in excluded}
    hits = [k for k, v in used.items() if abs(v) >= z_threshold]
    level = "높음" if len(hits) >= count_high else "주의" if hits else "조용함"
    return {"level": level, "hits": hits, "used": list(used), "excluded": excluded}


def curve_mismatch(leader: str | None, premium_like: bool, d2_significant: bool) -> bool:
    """종합 판정과 커브 지문이 어긋나는지. 프리미엄(C·D) 주도인데 2년물이 유의하게 움직였거나,
    정책·성장(A) 주도인데 단기물은 그대로고 장기물만 움직인(프리미엄형) 경우."""
    if leader in ("C", "D"):
        return d2_significant
    if leader == "A":
        return premium_like
    return False


def combine(leader: str | None, nominal_significant: bool, mismatch: bool, level: str) -> tuple[str, str]:
    """기존 종합 판정 × 수급 스트레스 → (구분, 해석 문구)."""
    if not nominal_significant:
        return "생략", "10년물 변화가 유의하지 않아 정보·수급 결합 판정은 생략합니다."
    if leader and not mismatch and level == "조용함":
        return "정보", "**정보에 의한 재가격으로 해석 가능합니다.** 원인이 단일 요인으로 뚜렷하고 수급 스트레스가 동반되지 않았습니다."
    if (leader is None or mismatch) and level == "높음":
        return "수급", ("**포지션 정리·강제 매매 가능성이 있습니다.** 원인이 하나로 모이지 않거나 커브 모양과 맞지 않는데 "
                        "수급 스트레스가 높습니다. 펀더멘털 대비 과도한 움직임일 수 있어 되돌림 여부를 지켜볼 필요가 있습니다.")
    return "혼합", "**펀더멘털과 수급이 함께 작용한 것으로 해석 가능합니다.**"


def flow_summary(kind_text: str, stress: dict, z_threshold: float, basis: str) -> str:
    """결론 → 스트레스 단계·임계 초과 지표 → 판정 제외 지표 → 기준."""
    hits = ", ".join(stress["hits"]) if stress["hits"] else "없음"
    line = f"{kind_text}  \n수급 스트레스 **{stress['level']}** (|z| ≥ {z_threshold:g}: {hits})"
    if stress["excluded"]:
        line += f" · 데이터가 없어 판정에서 제외: {', '.join(stress['excluded'])}"
    return line + f" · {basis}"
