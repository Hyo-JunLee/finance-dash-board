import numpy as np
import pandas as pd
import pytest

from data.flow_regime import (auction_weakness, combine, curve_mismatch, flow_summary, rolling_z, stock_bond_corr,
                              stress_level, ten_year_equiv, window_peak)


# ── z-score ──
def test_rolling_z_matches_manual():
    s = pd.Series([1.0, 2, 3, 4, 10])
    z = rolling_z(s, window=5, min_periods=5)
    assert z.iloc[-1] == pytest.approx((10 - s.mean()) / s.std())
    assert z.iloc[:4].isna().all()


def test_rolling_z_flat_series_is_nan():
    z = rolling_z(pd.Series([3.0] * 30), window=20)
    assert z.dropna().empty


def test_window_peak_uses_largest_abs_in_window_and_falls_back_to_last():
    idx = pd.date_range("2026-01-01", periods=6, freq="D")
    z = pd.Series([5.0, 0.5, -2.5, 1.0, 0.2, 0.1], index=idx)
    v, at = window_peak(z, idx[1], idx[5])          # 5.0은 창 밖
    assert (v, at) == (-2.5, idx[2])
    v, at = window_peak(z, idx[5], idx[5])          # 창 안 관측 없음 → 마지막 값
    assert (v, at) == (0.1, idx[5])
    assert window_peak(z, idx[0] - pd.Timedelta(days=9), idx[0] - pd.Timedelta(days=1)) is None


# ── 스트레스 개수 판정 ──
@pytest.mark.parametrize("zs, level", [
    ({"a": 2.1, "b": -2.0, "c": 0.3}, "높음"),
    ({"a": 2.1, "b": 1.9, "c": 0.3}, "주의"),
    ({"a": 1.0, "b": -1.5, "c": 0.3}, "조용함"),
])
def test_stress_level_counts(zs, level):
    assert stress_level(zs, z_threshold=2.0, count_high=2)["level"] == level


def test_stress_thresholds_are_parameters():
    zs = {"a": 1.6, "b": 0.2}
    assert stress_level(zs, z_threshold=1.5, count_high=1)["level"] == "높음"


# ── 결합 판정 3분기 ──
def test_combine_information_repricing():
    kind, text = combine(leader="A", nominal_significant=True, mismatch=False, level="조용함")
    assert kind == "정보" and "정보에 의한 재가격으로 해석 가능" in text


@pytest.mark.parametrize("leader, mismatch", [(None, False), ("C", True)])
def test_combine_forced_selling(leader, mismatch):
    kind, text = combine(leader=leader, nominal_significant=True, mismatch=mismatch, level="높음")
    assert kind == "수급" and "포지션 정리·강제 매매 가능성" in text and "되돌림" in text


@pytest.mark.parametrize("leader, mismatch, level", [
    ("A", False, "주의"),     # 단일 요인이지만 수급 주의
    ("A", False, "높음"),     # 단일 요인 + 스트레스 높음
    (None, False, "조용함"),  # 복합인데 수급 조용
    (None, False, "주의"),
])
def test_combine_mixed(leader, mismatch, level):
    kind, text = combine(leader=leader, nominal_significant=True, mismatch=mismatch, level=level)
    assert kind == "혼합" and "함께 작용" in text


def test_combine_skips_insignificant_move():
    kind, _ = combine(leader=None, nominal_significant=False, mismatch=False, level="높음")
    assert kind == "생략"


def test_curve_mismatch():
    assert curve_mismatch("C", premium_like=False, d2_significant=True)
    assert curve_mismatch("A", premium_like=True, d2_significant=False)
    assert not curve_mismatch("D", premium_like=True, d2_significant=False)
    assert not curve_mismatch(None, premium_like=True, d2_significant=True)


# ── 데이터 누락 ──
def test_missing_indicator_excluded_and_named():
    stress = stress_level({"레버리지 펀드": 2.5, "SOFR−IORB": None, "MOVE": float("nan"), "입찰 약세": 2.2})
    assert stress["excluded"] == ["SOFR−IORB", "MOVE"]
    assert stress["used"] == ["레버리지 펀드", "입찰 약세"]
    assert stress["level"] == "높음"
    text = flow_summary("결론", stress, 2.0, "기준")
    assert "판정에서 제외: SOFR−IORB, MOVE" in text


def test_all_missing_is_quiet_but_listed():
    stress = stress_level({"a": None, "b": None})
    assert stress["level"] == "조용함" and stress["excluded"] == ["a", "b"]


# ── 주식·채권 상관 부호 ──
def test_stock_bond_corr_negative_when_bonds_hedge():
    """금리 하락일(채권 상승)에 주가 하락 → 채권이 헤지 → 음수 (업계 관례)."""
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2025-01-01", periods=120)
    stock_ret = rng.normal(0, 0.01, len(idx))
    spx = pd.Series(100 * np.cumprod(1 + stock_ret), index=idx)
    y10 = pd.Series(4 + np.cumsum(stock_ret * 5 + rng.normal(0, 0.005, len(idx))), index=idx)  # 주가와 같은 방향
    assert stock_bond_corr(spx, y10, 60).iloc[-1] < -0.5


def test_stock_bond_corr_positive_when_both_fall():
    """금리 상승일(채권 하락)에 주가 하락 → 동반 움직임 → 양수."""
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2025-01-01", periods=120)
    stock_ret = rng.normal(0, 0.01, len(idx))
    spx = pd.Series(100 * np.cumprod(1 + stock_ret), index=idx)
    y10 = pd.Series(4 - np.cumsum(stock_ret * 5 + rng.normal(0, 0.005, len(idx))), index=idx)
    assert stock_bond_corr(spx, y10, 60).iloc[-1] > 0.5


# ── 10년물 환산·입찰 약세 ──
def test_ten_year_equiv():
    net = pd.DataFrame({"2년": [-100.0], "10년": [-50.0], "본드": [10.0]})
    dv01 = {"2년": 40, "10년": 60, "본드": 120}
    assert ten_year_equiv(net, dv01, "10년").iloc[0] == pytest.approx((-4000 - 3000 + 1200) / 60)


def test_auction_weakness_flags_weak_auction():
    days = pd.date_range("2025-12-01", periods=12, freq="7D")
    rng = np.random.default_rng(2)
    btc = 2.5 + rng.normal(0, 0.03, 12)
    dealer = 15 + rng.normal(0, 0.5, 12)
    btc[-1], dealer[-1] = 2.2, 25  # 마지막 입찰: 응찰률 급락 + 딜러 인수 급증
    df = pd.DataFrame({"입찰일": days, "응찰률": btc, "응찰률 6회평균": 2.5, "딜러(%)": dealer, "딜러(%) 6회평균": 15.0})
    z = auction_weakness(df, min_obs=8)
    assert z.iloc[-1] > 2 and z.iloc[:7].isna().all()
