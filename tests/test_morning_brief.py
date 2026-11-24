"""아침 인사이트: 한화 아침 시황 글 고르기와 요일별 분기."""
from datetime import datetime

import pandas as pd
import pytest

import summarize as s


class FakeNow(datetime):
    fixed = datetime(2026, 11, 24, 8, 40)  # 화요일

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


def _post(text, n):
    return {"text": text, "time": pd.Timestamp("2026-11-24 08:11"), "link": f"https://t.me/hanwhastrategy/{n}"}


@pytest.fixture
def fake_now(monkeypatch):
    monkeypatch.setattr(s, "datetime", FakeNow)
    return FakeNow


def test_picks_today_three_kinds(monkeypatch, fake_now):
    fake_now.fixed = datetime(2026, 11, 24, 8, 40)
    posts = [_post("★ 채권 아침 시황 (11/24) - 미 국채", 1), _post("★ 주식 아침 시황 (11/24) - 다우", 2),
             _post("★ 환율 아침 시황 (11/24) - 원/달러", 3), _post("★ 채권 마감 시황 (11/24) - 국고채", 4),
             _post("", 5)]
    monkeypatch.setattr(s, "fetch_since", lambda *a, **k: posts)
    found = s._morning_brief_posts()
    assert {k: v["link"][-1] for k, v in found.items()} == {"채권": "1", "주식": "2", "환율": "3"}


def test_missing_kind_or_other_date_returns_none(monkeypatch, fake_now):
    fake_now.fixed = datetime(2026, 11, 24, 8, 40)
    posts = [_post("★ 채권 아침 시황 (11/24)", 1), _post("★ 주식 아침 시황 (11/24)", 2),
             _post("★ 환율 아침 시황 (11/23)", 3)]  # 환율은 어제 글
    monkeypatch.setattr(s, "fetch_since", lambda *a, **k: posts)
    assert s._morning_brief_posts() is None


@pytest.mark.parametrize("day, brief, expected", [
    (datetime(2026, 11, 24, 8, 40), {"주식": {}}, "brief"),  # 평일, 글 있음
    (datetime(2026, 11, 24, 8, 40), None, "self"),          # 평일, 마감까지 없음(공휴일)
    (datetime(2026, 11, 28, 8, 40), None, "self"),          # 토요일
    (datetime(2026, 11, 29, 8, 40), None, None),            # 일요일: 아무것도 안 만듦
])
def test_weekday_branch(monkeypatch, fake_now, day, brief, expected):
    fake_now.fixed = day
    called = []
    monkeypatch.setattr(s, "_wait_morning_brief", lambda: brief)
    monkeypatch.setattr(s, "summarize_morning_brief", lambda p: called.append("brief"))
    monkeypatch.setattr(s, "summarize_insight", lambda: called.append("self"))
    s.morning_insight()
    assert called == ([expected] if expected else [])


def test_verify_numbers_flags_new_numbers():
    assert s._verify_numbers("나스닥 6.51% 10년물 4.944%", ["나스닥 6.51%", "10년물 4.94%"]) == ["4.94"]
