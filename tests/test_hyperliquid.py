"""24시간 시세(하이퍼리퀴드): 직전 마감 시각과 주말 모드."""
import pandas as pd

from data.hyperliquid import is_weekend, last_close


def _kst(t):
    return pd.Timestamp(t, tz="Asia/Seoul")


def test_last_close_on_saturday():
    now = _kst("2026-11-28 10:00")                                  # 토요일 오전
    assert last_close("us", now) == pd.Timestamp("2026-11-28 06:00")   # 금 16:00 ET
    assert last_close("kr", now) == pd.Timestamp("2026-11-27 15:30")   # 금 15:30 KST
    assert last_close("fut", now) == pd.Timestamp("2026-11-28 07:00")  # 금 17:00 ET


def test_last_close_weekday_before_close_uses_previous_day():
    assert last_close("kr", _kst("2026-11-30 10:00")) == pd.Timestamp("2026-11-27 15:30")  # 월 오전 → 금요일


def test_weekend_mode():
    assert is_weekend(_kst("2026-11-28 07:00")) and is_weekend(_kst("2026-11-29 23:00"))
    assert is_weekend(_kst("2026-11-30 08:59")) and not is_weekend(_kst("2026-11-30 09:00"))
    assert not is_weekend(_kst("2026-11-28 05:59")) and not is_weekend(_kst("2026-11-27 16:00"))
