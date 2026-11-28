"""매크로 국면 사분면, FOMC 일정 파싱, 서프라이즈 누적."""
from datetime import date

import pandas as pd

from data.fred import transform
from data.macro_regime import parse_fomc, quadrant, surprise_index


def test_quadrants():
    assert quadrant(0.2, -0.3)[0] == "골디락스" and quadrant(0.2, 1.0)[0] == "리플레이션·과열"
    assert quadrant(-0.2, 1.0)[0] == "스태그플레이션" and quadrant(-0.2, -0.5)[0] == "디플레이션·둔화"


def test_annualized_transform():
    s = pd.Series([100.0, 100.5, 101.0, 101.5], index=pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"]))
    a = transform(s, "ann3")
    assert abs(a.iloc[-1] - ((101.5 / 100) ** 4 - 1) * 100) < 1e-9 and a.iloc[:3].isna().all()


def test_parse_fomc():
    html = ('<h4>2026 FOMC Meetings</h4><div class="fomc-meeting__month"><strong>October/November</strong></div>'
            '<div class="fomc-meeting__date">31-1</div><div class="fomc-meeting__month"><strong>December</strong></div>'
            '<div class="fomc-meeting__date">8-9*</div><h4>2027 FOMC Meetings</h4>'
            '<div class="fomc-meeting__month"><strong>January</strong></div><div class="fomc-meeting__date">26-27</div>')
    assert parse_fomc(html) == [date(2026, 11, 1), date(2026, 12, 9), date(2027, 1, 27)]  # 두 달에 걸친 회의 = 다음 달 날짜


def test_surprise_index():
    t = pd.Timestamp("2026-11-20 22:30")
    sup = pd.DataFrame({"발표(KST)": [t, t, t + pd.Timedelta(days=1)], "분야": ["물가", "고용", "물가"],
                        "_diff": [0.1, -5.0, 0.2]})
    idx = surprise_index(sup)
    assert list(idx["물가"]) == [1, 2] and list(idx["성장"]) == [-1, -1]
