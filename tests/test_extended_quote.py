import pandas as pd
import pytest

from data.global_markets import extended_quote

NY = "America/New_York"
DAILY = pd.Series([100.0, 110.0], index=pd.to_datetime(["2026-11-19", "2026-11-20"]))


def _bars(*pairs):
    return pd.Series([p for _, p in pairs], index=pd.DatetimeIndex([pd.Timestamp(t, tz=NY) for t, _ in pairs]))


def test_after_hours_vs_same_day_close():
    bars = _bars(("2026-11-20 15:55", 110.0), ("2026-11-20 19:55", 113.3))
    q = extended_quote(bars, DAILY, pd.Timestamp("2026-11-20 20:05", tz=NY))
    assert q["session"] == "애프터마켓" and q["pct"] == pytest.approx(3.0) and q["live"]


def test_pre_market_vs_previous_close():
    bars = _bars(("2026-11-20 19:55", 113.3), ("2026-11-23 08:00", 104.5))
    q = extended_quote(bars, DAILY, pd.Timestamp("2026-11-23 08:03", tz=NY))
    assert q["session"] == "프리마켓" and q["pct"] == pytest.approx(-5.0)


def test_weekend_keeps_last_after_hours_but_not_live():
    bars = _bars(("2026-11-20 19:55", 113.3))
    q = extended_quote(bars, DAILY, pd.Timestamp("2026-11-22 12:00", tz=NY))
    assert q["session"] == "애프터마켓" and not q["live"]


def test_regular_session_bar_returns_none():
    bars = _bars(("2026-11-20 19:55", 113.3), ("2026-11-23 10:30", 111.0))
    assert extended_quote(bars, DAILY, pd.Timestamp("2026-11-23 10:31", tz=NY)) is None


@pytest.mark.parametrize("ts, session", [
    ("2026-11-23 08:50", "프리마켓"), ("2026-11-23 10:00", "정규장"), ("2026-11-23 17:00", "애프터마켓"),
    ("2026-11-23 21:00", "휴장"), ("2026-11-22 08:50", "휴장"),  # 일요일
])
def test_market_session_by_clock(ts, session):
    from data.global_markets import market_session
    assert market_session(pd.Timestamp(ts, tz=NY)) == session
