"""네이버 실시간 목록 파싱: KRX와 같은 열·단위(원), ETF 제외."""
import pandas as pd

from data.naver_live import _stocks, live_status


def _row(name, end="stock", status="OPEN"):
    return {"itemCode": "005930", "stockName": name, "stockEndType": end, "closePrice": "172,000",
            "closePriceRaw": "172000", "compareToPreviousClosePrice": "1,300", "compareToPreviousClosePriceRaw": "1300",
            "fluctuationsRatio": "0.76", "accumulatedTradingValueRaw": "2037363000000",
            "marketValueRaw": "1005559920576000", "localTradedAt": "2026-11-26T15:15:17+09:00", "marketStatus": status}


def test_stocks_units_and_etf_filter():
    df = _stocks([_row("삼성전자"), _row("KODEX 200", end="etf")], "KOSPI")
    assert list(df["종목"]) == ["삼성전자"]
    r = df.iloc[0]
    assert (r["종가"], r["등락률"], r["거래대금"], r["시가총액"]) == (172000, 0.76, 2.037363e12, 1005559920576000)
    t, live = live_status(df, now=pd.Timestamp("2026-11-26 15:15"))
    assert str(t) == "2026-11-26 15:15:17" and live


def test_regular_session_ignores_after_hours():
    import pandas as pd
    from data.naver_live import regular_session
    assert regular_session(pd.Timestamp("2026-11-26 15:10"))
    assert not regular_session(pd.Timestamp("2026-11-26 15:58"))   # 시간외: 네이버는 OPEN이어도 장중 아님
    assert not regular_session(pd.Timestamp("2026-11-28 10:00"))   # 토요일
