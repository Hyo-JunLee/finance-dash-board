"""10년물 '왜 움직였나'의 규칙 기반 근거를 한 번에 계산 (화면·AI 판단·아침 인사이트 공통).

채권 › 곡선 분해 화면과 같은 계산을 Streamlit 화면 없이 수행해 dict와 사람이 읽을 텍스트로 돌려준다.
AI 판단(summarize.py)은 이 텍스트를 '근거'로 받고, 규칙 판정과 같은지 다른지를 함께 적는다.
"""
import pandas as pd

import config
from data import bonds as b
from data import flow_data as fd
from data import flow_regime as fr
from data.bond_attribution import attribute, curve_check, liquidity_check, tp_model_check
from data.fred import get_fred_series
from data.rates import NOMINAL, get_treasury_curve

WINDOWS = {"1주": pd.DateOffset(weeks=1), "1개월": pd.DateOffset(months=1)}  # AI 판단을 만드는 기간


def _window_values(s: pd.Series, start: pd.Timestamp, end: pd.Timestamp):
    s = s[s.index <= end]
    if s.empty:
        return None, None
    first = s[s.index <= start]
    return (first.iloc[-1] if len(first) else None), s.iloc[-1]


def rule_snapshot(window: str, noise_bp: float = config.ATTR_NOISE_BP) -> dict:
    """규칙 기반 분해·판정·보조 검증·수급을 window 기간(공통 마지막 날짜 기준)으로 계산."""
    off = WINDOWS[window]
    rb, acm = b.real_bei_10y(), b.acm_10y()
    end = min(rb.index[-1], acm.index[-1])
    since = end - off
    rc = b.change_bp(rb[rb.index <= end], since)
    ac = b.change_bp(acm[acm.index <= end], since)
    attr = attribute(rc["실질 10년"], rc["기대인플레(BEI)"], ac["기대 단기금리 경로"], ac["텀 프리미엄"], noise_bp)
    fresh = b.change_bp(rb, rb.index[-1] - off)  # 패널 ①은 ACM보다 최신까지 있음

    curve = get_treasury_curve((since - pd.Timedelta(days=10)).date(), NOMINAL)
    cc = b.change_bp(curve[curve.index <= end][["2 Yr", "10 Yr", "30 Yr"]], since)
    curve_text, premium_like = curve_check(cc["2 Yr"], cc["10 Yr"], cc["30 Yr"], noise_bp)

    try:
        kw = get_fred_series("THREEFYTP10")
        d_kw = (kw[kw.index <= end].iloc[-1] - kw[kw.index <= since].iloc[-1]) * 100 if kw.index[-1] >= end else None
    except Exception:
        d_kw = None
    kw_text, _ = tp_model_check(ac["텀 프리미엄"], d_kw, config.ATTR_TP_MODEL_GAP_BP)

    try:
        hy0, hy1 = _window_values(get_fred_series("BAMLH0A0HYM2"), since, end)
    except Exception:
        hy0, hy1 = None, None
    try:
        mv0, mv1 = _window_values(fd.rate_vol()[0], since, end)
    except Exception:
        mv0, mv1 = None, None
    liq_text, liq_warn = liquidity_check(
        (hy1 - hy0) * 100 if hy0 is not None and hy1 is not None else None, hy1,
        (mv1 / mv0 - 1) * 100 if mv0 is not None and mv1 is not None else None, mv1,
        attr.components["D"], noise_bp, config.ATTR_HY_WIDEN_BP, config.ATTR_HY_LEVEL,
        config.ATTR_MOVE_RISE_PCT, config.ATTR_MOVE_LEVEL)

    zs, _ = fd.stress_series(config.FLOW_Z_WINDOW_DAYS)
    peaks = {k: (fr.window_peak(z, since, end) if z is not None else None) for k, z in zs.items()}
    stress = fr.stress_level({k: (p[0] if p else None) for k, p in peaks.items()},
                             config.FLOW_Z_THRESHOLD, config.FLOW_STRESS_COUNT)
    mismatch = fr.curve_mismatch(attr.leader, premium_like, abs(cc["2 Yr"]) >= noise_bp)
    _, combined = fr.combine(attr.leader, abs(attr.nominal) >= noise_bp, mismatch, stress["level"])

    try:
        auc = fd.auction_table()
        auc = auc[(auc["입찰일"] > since) & (auc["입찰일"] <= end + pd.Timedelta(days=7))]
        auctions = [f"{r['입찰일']:%m/%d} {r['만기']}: 응찰률 {r['응찰률']:.2f}(직전 6회 평균 {r['응찰률 6회평균']:.2f}), "
                    f"딜러 {r['딜러(%)']:.0f}%(평균 {r['딜러(%) 6회평균']:.0f}%), 해외 {r['해외(%)']:.0f}%(평균 {r['해외(%) 6회평균']:.0f}%)"
                    for _, r in auc.iterrows()]
    except Exception:
        auctions = []

    return {
        "window": window, "end": end, "since": since, "panel1_end": rb.index[-1], "acm_end": acm.index[-1],
        "attr": attr, "fresh": fresh, "curve": cc, "curve_text": curve_text, "premium_like": premium_like,
        "kw_text": kw_text, "liq_text": liq_text, "liq_warn": liq_warn, "stress": stress, "peaks": peaks,
        "combined": combined, "auctions": auctions,
    }


def snapshot_text(s: dict) -> str:
    """AI·아침 인사이트에 넘길 근거 텍스트. 기호(A/C/D) 대신 이름을 함께 적는다."""
    a, cc, fresh = s["attr"], s["curve"], s["fresh"]
    names = {"A": "정책·성장 기대(실질 기대경로)", "C": "실질 텀프리미엄(장기채 보유 보상)", "D": "인플레 리스크 프리미엄"}
    comps = ", ".join(f"{names[k]} {v:+.0f}bp" + ("" if a.significant[k] else "(노이즈 수준)") for k, v in a.components.items())
    peaks = ", ".join(f"{k} z {p[0]:+.1f}({p[1]:%m/%d})" for k, p in s["peaks"].items() if p)
    lines = [
        f"- 기간: {s['window']} (~{s['end']:%m/%d}, 실질·BEI는 ~{s['panel1_end']:%m/%d}, ACM 모형은 ~{s['acm_end']:%m/%d}까지 공개)",
        f"- 최신 기준({s['window']}, ~{s['panel1_end']:%m/%d}): 10년물 {fresh['명목 10년']:+.0f}bp = "
        f"실질금리 {fresh['실질 10년']:+.0f}bp + 기대인플레(BEI) {fresh['기대인플레(BEI)']:+.0f}bp",
        f"- 공통 기간 분해: 10년물 {a.nominal:+.0f}bp → {comps}",
        f"- 규칙 판정: {a.verdict}" + (" · 가정 위반 진단: 기대 인플레 경로 변화로 볼 여지" if a.assumption_violation else ""),
        f"- 커브: 2년 {cc['2 Yr']:+.0f}bp, 10년 {cc['10 Yr']:+.0f}bp, 30년 {cc['30 Yr']:+.0f}bp"
        + (" → 단기물은 그대로고 장기물만 움직인 프리미엄형" if s["premium_like"] else ""),
        f"- 텀프리미엄 모형 비교: {s['kw_text']}",
        f"- 유동성 왜곡 점검: {s['liq_text']}".replace("**", "").replace("⚠ ", "경고: "),
        f"- 수급 스트레스: {s['stress']['level']} (|z|≥{config.FLOW_Z_THRESHOLD:g}: {', '.join(s['stress']['hits']) or '없음'}; "
        f"지표별 최대 {peaks})" + (f"; 데이터 없음: {', '.join(s['stress']['excluded'])}" if s["stress"]["excluded"] else ""),
        f"- 규칙의 정보·수급 결합 해석: {s['combined'].replace('**', '')}",
        "- 기간 중 국채 입찰: " + ("; ".join(s["auctions"]) if s["auctions"] else "없음"),
    ]
    return "\n".join(lines)
