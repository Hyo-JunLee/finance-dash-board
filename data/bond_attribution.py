"""10년물 금리 변화의 교차 판정 (패널 ① 실질+BEI × 패널 ② ACM 기대경로+텀프리미엄).

명목 10년 = A + B + C + D
  A = 기대 실질 단기금리 경로      B = 기대 인플레 경로
  C = 실질 텀프리미엄              D = 인플레 리스크 프리미엄
관측: 실질(TIPS) = A + C, BEI = B + D, ACM 기대경로 = A + B, ACM 텀프리미엄 = C + D

미지수 4개·독립식 3개라 가정 하나가 필요하다.
기본 가정 'ΔB ≈ 0' → ΔA = Δ기대경로, ΔC = Δ실질 − Δ기대경로, ΔD = ΔBEI

빠진 항: TIPS 유동성 프리미엄 L. 실제로는 실질(TIPS) = A + C + L, BEI = B + D − L이라
시장 경색으로 TIPS가 먼저 팔리면(L↑) 프레임은 이를 C 상승·D 하락으로 잘못 읽는다 (2020년 3월).
L은 관측할 수 없어 분해하지 않고, 경색 신호(HY 스프레드·MOVE 급등)가 있을 때 경고만 한다.

Streamlit에 의존하지 않는 순수 함수 (tests/test_bond_attribution.py).
임계값은 모두 인자로 받는다 (나중에 기간별 임계값으로 바꾸기 쉽게).
"""
from dataclasses import dataclass, field

LEADER_LABELS = {
    "A": "정책·성장 기대 주도",
    "C": "실질 텀프리미엄 주도",
    "D": "인플레 리스크 프리미엄 주도",
}
# 주도 요인 해석 (상승 시, 하락 시) — 원인은 단정하지 않고 '시사' 수준
LEADER_MEANING = {
    "A": ("연준 정책·성장 전망이 더 높은 실질 금리 경로 쪽으로 재평가됐음을 시사합니다",
          "연준 정책·성장 전망이 더 낮은 실질 금리 경로 쪽으로 재평가됐음을 시사합니다"),
    "C": ("재정·국채 수급·듀레이션 위험·헤지 가치 변화 등으로 장기채 보유 보상(실질)이 커졌음을 시사합니다",
          "재정·국채 수급·듀레이션 위험·헤지 가치 변화 등으로 장기채 보유 보상(실질)이 줄었음을 시사합니다"),
    "D": ("인플레 불확실성에 대한 보상이 커졌음을 시사합니다",
          "인플레 불확실성에 대한 보상이 줄었음을 시사합니다"),
}
COMPONENT_NAMES = {"A": "기대 실질 단기금리 경로(A)", "B": "기대 인플레 경로(B)",
                   "C": "실질 텀프리미엄(C)", "D": "인플레 리스크 프리미엄(D)"}


@dataclass
class Attribution:
    nominal: float                      # Δ명목 = Δ실질 + ΔBEI
    components: dict[str, float]        # B≈0 가정: {"A", "C", "D"}
    significant: dict[str, bool]        # |Δ| ≥ 노이즈 임계값
    implied_tp: float                   # ΔC + ΔD
    consistency_gap: float              # ΔACM TP − 내재 TP (= ACM 무이표채 vs 재무부 이표채 금리 측정 차이)
    leader: str | None                  # "A"/"C"/"D", 판정 불가면 None
    verdict: str                        # 판정 라벨
    assumption_violation: bool = False  # R4 조건 성립 → B≈0 가정 위반 진단
    alt_components: dict[str, float] = field(default_factory=dict)  # 대안 가정 D≈0 분해 {"A","B","C"}


def _sig(v: float, noise_bp: float) -> bool:
    return abs(v) >= noise_bp


def attribute(d_real: float, d_bei: float, d_path: float, d_acm_tp: float, noise_bp: float = 3.0) -> Attribution:
    """B≈0 가정 분해 → 주도 요인 판정 → R4(가정 위반) 진단."""
    nominal = d_real + d_bei
    comp = {"A": d_path, "C": d_real - d_path, "D": d_bei}
    sig = {k: _sig(v, noise_bp) for k, v in comp.items()}
    implied_tp = comp["C"] + comp["D"]

    # 주도 요인: 명목 변화와 같은 방향이면서 유의한 성분 중 절댓값 최대. 1·2위 차이 < 임계값이면 복합
    leader, verdict = None, "복합 요인, 단일 요인으로 판정 불가"
    if not _sig(nominal, noise_bp):
        verdict = "10년물 변화가 유의하지 않음 (주도 요인 판정 생략)"
    else:
        direction = 1 if nominal > 0 else -1
        cands = sorted(((k, abs(v)) for k, v in comp.items() if sig[k] and v * direction > 0),
                       key=lambda kv: kv[1], reverse=True)
        if cands and (len(cands) == 1 or cands[0][1] - cands[1][1] >= noise_bp):
            leader = cands[0][0]
            verdict = LEADER_LABELS[leader]

    # R4 → 가정 위반 진단: BEI와 기대경로가 같은 방향으로 유의하게 움직였는데 ACM TP는 보합
    violation = (_sig(d_bei, noise_bp) and _sig(d_path, noise_bp) and d_bei * d_path > 0
                 and not _sig(d_acm_tp, noise_bp))
    alt = {"B": d_bei, "A": d_path - d_bei, "C": d_acm_tp} if violation else {}

    return Attribution(nominal=nominal, components=comp, significant=sig, implied_tp=implied_tp,
                       consistency_gap=d_acm_tp - implied_tp, leader=leader, verdict=verdict,
                       assumption_violation=violation, alt_components=alt)


def tp_model_check(d_acm_tp: float, d_kw_tp: float | None, gap_warn_bp: float = 5.0) -> tuple[str, bool]:
    """ACM vs Kim-Wright 텀프리미엄 변화 비교. 반환: (문구, 경고 여부). d_kw_tp=None이면 비교 불가."""
    if d_kw_tp is None:
        return "Kim-Wright 미갱신으로 비교 불가 (공통 기간을 커버하지 않음)", False
    gap = d_acm_tp - d_kw_tp
    if abs(gap) > gap_warn_bp:
        return (f"⚠ 두 텀프리미엄 모형의 변화가 {gap:+.0f}bp 차이 나 해석 신뢰도에 주의가 필요합니다 "
                f"(ACM {d_acm_tp:+.0f}bp, Kim-Wright {d_kw_tp:+.0f}bp)."), True
    return f"ACM {d_acm_tp:+.0f}bp, Kim-Wright {d_kw_tp:+.0f}bp로 두 모형이 대체로 일치합니다.", False


def curve_check(d2: float, d10: float, d30: float, noise_bp: float = 3.0) -> tuple[str, bool]:
    """모형 없는 보조 검증. 2년물은 보합인데 10·30년만 같은 방향으로 움직였으면 프리미엄 주도와 일치."""
    text = (f"2년 {d2:+.0f}bp · 2s10s {d10 - d2:+.0f}bp · 10s30s {d30 - d10:+.0f}bp "
            f"(10년 {d10:+.0f}bp, 30년 {d30:+.0f}bp)")
    premium_like = (not _sig(d2, noise_bp) and _sig(d10, noise_bp) and _sig(d30, noise_bp) and d10 * d30 > 0)
    if premium_like:
        text += " → 단기물은 거의 움직이지 않고 장기물만 움직여 **프리미엄 주도와 일치**합니다."
    return text, premium_like


def liquidity_check(d_hy_bp: float | None, hy_level: float | None, move_chg_pct: float | None,
                    move_level: float | None, d_component_d: float, noise_bp: float = 3.0,
                    hy_widen_bp: float = 75.0, hy_level_warn: float = 6.0,
                    move_rise_pct: float = 30.0, move_level_warn: float = 150.0) -> tuple[str, bool]:
    """시장 경색 신호 점검. 신호가 있으면 BEI 하락(D)·실질 상승(C)에 TIPS 유동성 프리미엄이 섞였을 수 있다.
    반환: (문구, 경고 여부). 값이 None이면 해당 지표는 데이터 없음으로 건너뛴다."""
    triggers, status = [], []
    if d_hy_bp is not None or hy_level is not None:
        status.append("HY 스프레드 " + " · ".join(
            ([f"{d_hy_bp:+.0f}bp"] if d_hy_bp is not None else []) + ([f"현재 {hy_level:.2f}%"] if hy_level is not None else [])))
        if d_hy_bp is not None and d_hy_bp >= hy_widen_bp:
            triggers.append(f"HY 스프레드 {d_hy_bp:+.0f}bp 확대(기준 +{hy_widen_bp:g}bp)")
        if hy_level is not None and hy_level >= hy_level_warn:
            triggers.append(f"HY 스프레드 {hy_level:.2f}%(기준 {hy_level_warn:g}%)")
    if move_chg_pct is not None or move_level is not None:
        status.append("MOVE " + " · ".join(
            ([f"{move_chg_pct:+.0f}%"] if move_chg_pct is not None else []) + ([f"현재 {move_level:.0f}"] if move_level is not None else [])))
        if move_chg_pct is not None and move_chg_pct >= move_rise_pct:
            triggers.append(f"MOVE {move_chg_pct:+.0f}% 급등(기준 +{move_rise_pct:g}%)")
        if move_level is not None and move_level >= move_level_warn:
            triggers.append(f"MOVE {move_level:.0f}(기준 {move_level_warn:g})")
    if not status:
        return "HY 스프레드·MOVE 데이터를 불러오지 못해 유동성 왜곡 여부를 점검하지 못했습니다.", False
    if not triggers:
        return " · ".join(status) + " → 시장 경색 신호 없음.", False
    text = ("⚠ **유동성 왜곡 가능**: " + ", ".join(triggers) + ". 시장이 경색되면 TIPS가 명목채보다 먼저 팔려 "
            "실질금리는 오르고 BEI는 떨어지는데, 이 프레임은 그 TIPS 유동성 프리미엄을 실질 텀프리미엄(C) 상승과 "
            "인플레 리스크 프리미엄(D) 하락으로 읽습니다 (예: 2020년 3월).")
    if _sig(d_component_d, noise_bp) and d_component_d < 0:
        text += f" 이번 기간 D {d_component_d:+.0f}bp 하락은 상당 부분 유동성 요인일 수 있어 판정을 그대로 믿기 어렵습니다."
    return text, True


def summary_text(a: Attribution, common_end: str, panel1_end: str, acm_end: str, noise_bp: float) -> str:
    """종합 판정 문구 (결론 먼저, 원인은 '시사'·'해석 가능' 수준, 가정·기간 안내 항상 포함)."""
    fmt = lambda v: f"{v:+.0f}bp"
    lines = []
    if a.leader:
        v = a.components[a.leader]
        lines.append(f"**{a.verdict}**로 해석 가능합니다. 10년물 {fmt(a.nominal)} 중 "
                     f"{COMPONENT_NAMES[a.leader]}의 변화({fmt(v)})가 가장 컸으며, "
                     f"{LEADER_MEANING[a.leader][0 if v > 0 else 1]}.")
    else:
        lines.append(f"**{a.verdict}**. 10년물 {fmt(a.nominal)}.")
    parts = []
    for k in ("A", "C", "D"):
        v = a.components[k]
        parts.append(f"{COMPONENT_NAMES[k]} {fmt(v)}" + ("" if a.significant[k] else " (유의미한 변화 아님)"))
    lines.append("성분: " + " · ".join(parts) + f" · 내재 텀프리미엄(C+D) {fmt(a.implied_tp)}")
    lines.append(f"가정: 선택 기간 동안 기대 인플레 경로(B) 변화 ≈ 0 · 노이즈 임계값 ±{noise_bp:g}bp")
    if a.assumption_violation:
        alt = a.alt_components
        lines.append(f"⚠ **가정 위반 진단**: BEI와 기대경로가 같은 방향으로 움직였는데 ACM 텀프리미엄은 보합입니다. "
                     f"이 기간은 **기대 인플레 경로 변화로 해석 가능**합니다. 대안 가정(D≈0) 분해: "
                     f"{COMPONENT_NAMES['B']} {fmt(alt['B'])} · {COMPONENT_NAMES['A']} {fmt(alt['A'])} · "
                     f"{COMPONENT_NAMES['C']} {fmt(alt['C'])}")
    if panel1_end != acm_end:
        lines.append(f"기간: 패널 ①은 ~{panel1_end}, ACM은 ~{acm_end}까지라 종합 판정은 공통 마지막 날짜 "
                     f"**{common_end}** 기준으로 다시 계산했습니다 (패널별 수치와 다를 수 있음).")
    else:
        lines.append(f"기간: 두 패널 모두 ~{common_end} 기준.")
    return "  \n".join(lines)
