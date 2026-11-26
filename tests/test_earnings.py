"""실적 일정 파싱: 감시 종목·초대형주만, 발표 시점·EPS 숫자 변환."""
from datetime import date

from data.earnings import parse_rows


def test_parse_rows_filters_and_converts():
    rows = [
        {"symbol": "CRM", "name": "Salesforce, Inc.", "marketCap": "$159,052,972,000", "time": "time-after-hours",
         "epsForecast": "$2.41", "fiscalQuarterEnding": "Oct/2026"},
        {"symbol": "XYZ", "name": "Small Co", "marketCap": "$2,000,000,000", "time": "time-pre-market",
         "epsForecast": "$0.10"},                                                          # 감시 밖 소형주 → 제외
        {"symbol": "RY", "name": "Royal Bank Of Canada", "marketCap": "$261,535,613,000", "time": "time-pre-market",
         "epsForecast": "$2.93", "eps": "$3.10", "surprise": "5.8"},                       # 초대형주 → 포함
        {"symbol": "LOSS", "name": "Loss Co", "marketCap": "$120,000,000,000", "time": "time-not-supplied",
         "epsForecast": "($0.20)"},                                                        # 음수 EPS 괄호 표기
    ]
    df = parse_rows(rows, date(2026, 12, 2))
    assert list(df["티커"]) == ["CRM", "RY", "LOSS"]
    assert list(df["시점"]) == ["장 후", "장 전", "미정"]
    assert df.loc[0, "종목"] == "세일즈포스" and df.loc[0, "예상 EPS"] == 2.41
    assert df.loc[1, "서프라이즈(%)"] == 5.8 and df.loc[2, "예상 EPS"] == -0.2
