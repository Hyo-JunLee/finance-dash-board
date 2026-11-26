"""국내 일정: 한국은행 금통위 표 파싱과 다가오는 일정."""
from datetime import date

from data.kr_calendar import parse_meetings, upcoming

HTML = '<tr><th scope="row">10월 22일(목)</th></tr><tr><th scope="row">11월 26일(목)</th></tr>'


def test_parse_and_upcoming():
    m = parse_meetings(HTML, 2026)
    assert m == [date(2026, 10, 22), date(2026, 11, 26)]
    ev = upcoming(date(2026, 11, 26), m, days=20)
    assert [e["날짜"] for e in ev] == [date(2026, 11, 26), date(2026, 12, 1)]
    assert ev[0]["일정"].startswith("한은 금통위") and ev[1]["일정"].startswith("수출입동향")
