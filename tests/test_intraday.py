"""1일 차트 가로축: 장 시작·마감 (한국 시각, 서머타임 반영)."""
import pandas as pd

from data.intraday import session_bounds


def _s(*ts):
    return pd.Series(range(len(ts)), index=pd.to_datetime(list(ts)), dtype=float)


def test_kr_session():
    assert session_bounds(_s("2026-11-27 14:30"), "kr") == (pd.Timestamp("2026-11-27 09:00"), pd.Timestamp("2026-11-27 15:30"))


def test_us_session_winter_and_summer():
    # 11월(서머타임 해제): 09:30~16:00 ET = 23:30~다음 날 06:00 KST
    assert session_bounds(_s("2026-11-26 05:55"), "us") == (pd.Timestamp("2026-11-25 23:30"), pd.Timestamp("2026-11-26 06:00"))
    # 7월(서머타임): 22:30~05:00 KST
    assert session_bounds(_s("2026-07-15 04:00"), "us") == (pd.Timestamp("2026-07-14 22:30"), pd.Timestamp("2026-07-15 05:00"))


def test_futures_session():
    # 전날 18:00 ET ~ 당일 17:00 ET = 08:00 ~ 다음 날 07:00 KST (11월)
    assert session_bounds(_s("2026-11-26 13:55"), "fut") == (pd.Timestamp("2026-11-26 08:00"), pd.Timestamp("2026-11-27 07:00"))


def test_vix_and_24h_sessions():
    # VIX: 03:15~16:15 ET = 17:15 ~ 다음 날 06:15 KST (11월)
    assert session_bounds(_s("2026-11-27 01:30"), "vix") == (pd.Timestamp("2026-11-26 17:15"), pd.Timestamp("2026-11-27 06:15"))
    # 비트코인: 최근 24시간
    assert session_bounds(_s("2026-11-27 14:55"), "24h") == (pd.Timestamp("2026-11-26 14:55"), pd.Timestamp("2026-11-27 14:55"))
