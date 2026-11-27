"""미 국채 2년·10년 큰 변동 알림: 하루 8/12/16bp 단계, 30분 4bp(1시간 쿨다운), 커브 6bp, 멈춘 데이터 무시."""
import pandas as pd

from data.rate_alerts import evaluate, regime

NY = "America/New_York"
PREV = {"US2Y": 4.60, "US10Y": 4.80}


def _s(values, end="2026-12-10 09:00"):
    idx = pd.date_range(end=pd.Timestamp(end, tz=NY), periods=len(values), freq="10min")
    return pd.Series(values, index=idx)


def _now(t="2026-12-10 09:00"):
    return pd.Timestamp(t, tz=NY)


def test_daily_levels_step_up_once_each():
    state = {}
    ev = evaluate({"US2Y": _s([4.60, 4.65, 4.69])}, PREV, _now(), state)       # +9bp → 8bp 단계
    assert [(e["kind"], e["level"]) for e in ev if e["kind"] == "day"] == [("day", 8)]
    assert evaluate({"US2Y": _s([4.65, 4.69, 4.695])}, PREV, _now(), state) == []  # 같은 단계 → 다시 안 보냄
    ev = evaluate({"US2Y": _s([4.69, 4.70, 4.735])}, PREV, _now(), state)       # +13.5bp → 12bp 단계
    assert [e["level"] for e in ev if e["kind"] == "day"] == [12]


def test_fast_move_with_cooldown():
    state = {}
    ev = evaluate({"US10Y": _s([4.80, 4.80, 4.80, 4.845])}, PREV, _now(), state)   # 30분 새 +4.5bp
    assert any(e["kind"] == "fast" for e in ev)
    ev = evaluate({"US10Y": _s([4.80, 4.80, 4.80, 4.85], end="2026-12-10 09:20")}, PREV, _now("2026-12-10 09:20"), state)
    assert not any(e["kind"] == "fast" for e in ev)                               # 1시간 안 → 안 보냄


def test_curve_and_regime():
    state = {}
    ev = evaluate({"US2Y": _s([4.60, 4.62]), "US10Y": _s([4.80, 4.87])}, PREV, _now(), state)  # 2년 +2, 10년 +7 → 커브 +5
    assert not any(e["kind"] == "curve" for e in ev)
    ev = evaluate({"US2Y": _s([4.60, 4.61]), "US10Y": _s([4.80, 4.875])}, PREV, _now(), state)  # 커브 +6.5
    assert any(e["kind"] == "curve" for e in ev)
    assert regime(1, 7.5) == "베어 스티프닝" and regime(-9, -3) == "불 스티프닝" and regime(9, 3) == "베어 플래트닝"


def test_stale_data_ignored():
    assert evaluate({"US2Y": _s([4.60, 4.75], end="2026-12-10 08:00")}, PREV, _now("2026-12-10 09:00"), {}) == []
