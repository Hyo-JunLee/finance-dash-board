"""매크로 발표 현황: 백만 단위(JOLTS·주택착공) 표시와 컨센서스 판정, 탭 지표 목록 형식."""
from datetime import date

import data.econ_calendar as ec


def test_million_unit_format_and_surprise(monkeypatch):
    assert ec._fmt(7523.0, "M") == "7.52M"  # FRED 천 단위 → 백만
    monkeypatch.setattr(ec, "find_event", lambda d, n, v: {"actual": 7.523, "consensus": 7.15, "previous": 7.19,
                                                           "time": "10:00"})
    c = ec._consensus([date(2026, 11, 3)], "JOLTS Job Openings", 7.523, "M")
    assert c["예상"] == "7.15M" and c["서프라이즈"] == "▲ 상회 +0.37M" and c["_warn"] == ""
    assert str(c["발표"]) == "2026-11-04 00:00:00" and not c["_notime"]  # 미 동부 10:00 → 한국 00:00 (시각 있음)


def test_release_without_consensus_keeps_time(monkeypatch):
    monkeypatch.setattr(ec, "find_event", lambda d, n, v: {"actual": 6.82, "consensus": None, "previous": 6.57,
                                                           "time": "8:30"})
    c = ec._consensus([date(2026, 11, 17)], "Retail Sales", 6.82, "%")
    assert c["예상"] == "-" and str(c["발표"]) == "2026-11-17 22:30:00"


def test_macro_release_groups_shape():
    for group, items in ec.MACRO_RELEASES.items():
        assert items and all(len(i) == 7 for i in items), group
