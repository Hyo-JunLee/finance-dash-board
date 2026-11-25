"""Yahoo 일봉 종가 누락(거래량만 있고 종가 NaN) → 같은 날 1분봉 마지막 값으로 채움."""
import pandas as pd

from data.global_markets import fill_missing_last


def _df():
    idx = pd.to_datetime(["2026-11-23", "2026-11-24"])
    return pd.DataFrame({"AAPL": [349.08, None], "^GSPC": [7237.17, 7201.09], "TSM": [340.0, None]}, index=idx)


def test_fills_only_same_day_minute_bar():
    live = {"AAPL": (354.19, pd.Timestamp("2026-11-24 20:59", tz="UTC")),   # 뉴욕 11/24 15:59 → 채움
            "TSM": (350.0, pd.Timestamp("2026-11-23 20:59", tz="UTC")),     # 다른 날 → 그대로 NaN
            "^GSPC": (1.0, pd.Timestamp("2026-11-24 20:59", tz="UTC"))}     # 이미 값 있음 → 덮어쓰지 않음
    out = fill_missing_last(_df(), live)
    assert out.at[pd.Timestamp("2026-11-24"), "AAPL"] == 354.19
    assert pd.isna(out.at[pd.Timestamp("2026-11-24"), "TSM"])
    assert out.at[pd.Timestamp("2026-11-24"), "^GSPC"] == 7201.09
