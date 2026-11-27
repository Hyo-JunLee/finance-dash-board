"""선물·옵션 지표: 옵션 만기일 규칙과 VIX 기간구조 해석."""
from datetime import date

from data.derivatives import expiries, term_state


def test_expiries_rules():
    ev = expiries(date(2026, 11, 27))
    assert ev[0] == {"날짜": date(2026, 12, 10), "시장": "국내", "이름": "선물·옵션 동시만기"}   # 12월 둘째 목요일
    assert ev[1] == {"날짜": date(2026, 12, 18), "시장": "미국", "이름": "쿼드러플 위칭"}       # 12월 셋째 금요일
    assert {"날짜": date(2027, 1, 14), "시장": "국내", "이름": "옵션 만기"} in ev
    assert expiries(date(2026, 12, 10))[0]["날짜"] == date(2026, 12, 10)                       # 당일 포함


def test_term_state():
    assert term_state(1.05).startswith("백워데이션") and term_state(0.95).startswith("평평")
    assert term_state(0.8).startswith("콘탱고")
