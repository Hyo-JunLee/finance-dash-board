import pytest

from data.bond_attribution import attribute, curve_check, liquidity_check, summary_text, tp_model_check

NOISE = 3.0


def test_required_case_real_term_premium():
    """필수: Δ실질 +5, ΔBEI +2, Δ기대경로 −3, ΔACM TP +14 → C 주도, ΔC≈+8, ΔD≈+2, 내재 TP≈+10, 정합성 차이 4."""
    a = attribute(d_real=5, d_bei=2, d_path=-3, d_acm_tp=14, noise_bp=NOISE)
    assert a.leader == "C"
    assert a.verdict == "실질 텀프리미엄 주도"
    assert a.components["C"] == pytest.approx(8)
    assert a.components["D"] == pytest.approx(2)
    assert a.implied_tp == pytest.approx(10)
    assert a.consistency_gap == pytest.approx(4)
    assert not a.significant["D"]          # +2bp는 노이즈
    assert not a.assumption_violation


@pytest.mark.parametrize("sign", [1, -1])
def test_policy_growth_leads(sign):
    """A 주도: 실질·기대경로가 같이 크게 움직이고 실질 텀프리미엄은 작음 (상승·하락 대칭)."""
    a = attribute(d_real=12 * sign, d_bei=1 * sign, d_path=10 * sign, d_acm_tp=3 * sign, noise_bp=NOISE)
    assert a.leader == "A"
    assert a.verdict == "정책·성장 기대 주도"


@pytest.mark.parametrize("sign", [1, -1])
def test_real_term_premium_leads_symmetric(sign):
    a = attribute(d_real=10 * sign, d_bei=0, d_path=-1 * sign, d_acm_tp=11 * sign, noise_bp=NOISE)
    assert a.leader == "C"


@pytest.mark.parametrize("sign", [1, -1])
def test_inflation_risk_premium_leads(sign):
    a = attribute(d_real=1 * sign, d_bei=12 * sign, d_path=1 * sign, d_acm_tp=12 * sign, noise_bp=NOISE)
    assert a.leader == "D"
    assert a.verdict == "인플레 리스크 프리미엄 주도"


def test_overlap_case_picks_largest_component():
    """규칙 중첩: Δ실질 +4, Δ기대경로 −3, ΔBEI +20, ΔACM TP +25 → C(+7)·D(+20) 모두 후보지만 D 주도."""
    a = attribute(d_real=4, d_bei=20, d_path=-3, d_acm_tp=25, noise_bp=NOISE)
    assert a.components["C"] == pytest.approx(7)
    assert a.leader == "D"


def test_composite_when_top_two_close():
    """복합: A +5, C +5, D +6 → 1·2위 차이 1bp < 3bp."""
    a = attribute(d_real=10, d_bei=6, d_path=5, d_acm_tp=11, noise_bp=NOISE)
    assert a.leader is None
    assert a.verdict == "복합 요인, 단일 요인으로 판정 불가"


def test_opposite_direction_component_excluded():
    """명목과 반대 방향 성분은 후보에서 제외: 명목 +10인데 A −8 → C·D 중에서만 판정."""
    a = attribute(d_real=4, d_bei=6, d_path=-8, d_acm_tp=18, noise_bp=NOISE)
    assert a.components == {"A": -8, "C": 12, "D": 6}
    assert a.leader == "C"


def test_insignificant_nominal_change():
    a = attribute(d_real=1, d_bei=1, d_path=0, d_acm_tp=2, noise_bp=NOISE)
    assert a.leader is None
    assert "유의하지 않음" in a.verdict


@pytest.mark.parametrize("sign", [1, -1])
def test_r4_assumption_violation(sign):
    """R4: BEI↑·기대경로↑·ACM TP 보합 → 가정 위반 진단 + 대안 D≈0 분해 (상승·하락 대칭)."""
    a = attribute(d_real=2 * sign, d_bei=8 * sign, d_path=9 * sign, d_acm_tp=1 * sign, noise_bp=NOISE)
    assert a.assumption_violation
    assert a.alt_components == {"B": 8 * sign, "A": 1 * sign, "C": 1 * sign}
    text = summary_text(a, "10/02", "10/02", "10/02", NOISE)
    assert "가정 위반 진단" in text and "기대 인플레 경로 변화로 해석 가능" in text


def test_no_violation_when_tp_moves():
    a = attribute(d_real=2, d_bei=8, d_path=9, d_acm_tp=6, noise_bp=NOISE)
    assert not a.assumption_violation


def test_threshold_is_parameter():
    """임계값을 바꾸면 결과가 달라진다: D +2는 노이즈 3bp에선 무시, 1bp에선 유의."""
    assert not attribute(5, 2, -3, 14, noise_bp=3).significant["D"]
    assert attribute(5, 2, -3, 14, noise_bp=1).significant["D"]


def test_kim_wright_check():
    assert tp_model_check(14, None)[0].startswith("Kim-Wright 미갱신")
    assert tp_model_check(14, 12, gap_warn_bp=5)[1] is False
    assert tp_model_check(14, 5, gap_warn_bp=5)[1] is True


def test_curve_check():
    assert curve_check(d2=1, d10=8, d30=10)[1] is True        # 프리미엄 주도와 일치
    assert curve_check(d2=-1, d10=-6, d30=-9)[1] is True       # 하락 대칭
    assert curve_check(d2=9, d10=8, d30=6)[1] is False         # 단기물도 움직임


def test_summary_mentions_assumption_and_period_mismatch():
    a = attribute(5, 2, -3, 14, noise_bp=NOISE)
    text = summary_text(a, common_end="10/02", panel1_end="10/05", acm_end="10/02", noise_bp=NOISE)
    assert text.startswith("**실질 텀프리미엄 주도**")          # 결론 먼저
    assert "기대 인플레 경로(B) 변화 ≈ 0" in text
    assert "공통 마지막 날짜" in text
    assert "시사" in text


# ── 유동성 왜곡 경고 (TIPS 유동성 프리미엄) ──
def test_liquidity_march_2020_warns_and_flags_d():
    """2020년 3월형: MOVE +95%, HY +500bp, BEI 급락 → 경고 + D 하락을 유동성 요인으로 지목."""
    text, warn = liquidity_check(d_hy_bp=500, hy_level=10.0, move_chg_pct=95, move_level=135,
                                 d_component_d=-60, noise_bp=NOISE)
    assert warn
    assert "유동성 왜곡 가능" in text
    assert "D -60bp 하락" in text


def test_liquidity_calm_no_warning():
    text, warn = liquidity_check(d_hy_bp=-10, hy_level=3.3, move_chg_pct=-5, move_level=95,
                                 d_component_d=-8, noise_bp=NOISE)
    assert not warn
    assert "경색 신호 없음" in text


@pytest.mark.parametrize("kwargs", [
    dict(d_hy_bp=80, hy_level=4.0, move_chg_pct=5, move_level=100),    # HY 확대만
    dict(d_hy_bp=0, hy_level=6.5, move_chg_pct=0, move_level=100),     # HY 수준만
    dict(d_hy_bp=0, hy_level=3.5, move_chg_pct=33, move_level=129),    # MOVE 급등만 (2025-04형)
    dict(d_hy_bp=0, hy_level=3.5, move_chg_pct=10, move_level=170),    # MOVE 수준만 (2023-03형)
])
def test_liquidity_any_single_signal_warns(kwargs):
    _, warn = liquidity_check(**kwargs, d_component_d=0, noise_bp=NOISE)
    assert warn


def test_liquidity_warns_without_blaming_d_when_d_not_falling():
    text, warn = liquidity_check(d_hy_bp=200, hy_level=7.0, move_chg_pct=50, move_level=160,
                                 d_component_d=2, noise_bp=NOISE)
    assert warn and "판정을 그대로 믿기 어렵습니다" not in text


def test_liquidity_missing_data():
    text, warn = liquidity_check(None, None, None, None, d_component_d=-20, noise_bp=NOISE)
    assert not warn and "점검하지 못했습니다" in text
    # HY는 FRED가 최근 3년만 제공 → 기간 시작값이 없으면 수준만으로 판정
    _, warn = liquidity_check(None, 6.2, None, 110, d_component_d=0, noise_bp=NOISE)
    assert warn
