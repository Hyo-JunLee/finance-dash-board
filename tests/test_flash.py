"""⚡속보: 오늘 발표됐는데 FRED에 아직 없으면 Nasdaq 실제치로 새 기간 행, FRED에 올라오면 FRED 값."""
from datetime import date

import pandas as pd

import data.econ_calendar as ec

ITEM = ("CPI 전월비", "CPIAUCSL", "mom", "%", "M", "CPI", 1)
S = pd.Series([0.65, 0.35], index=pd.to_datetime(["2026-09-01", "2026-10-01"]))
TODAY = date(2026, 12, 10)


def _patch(monkeypatch):
    monkeypatch.setattr(ec, "fred_today", lambda: TODAY)
    monkeypatch.setattr(ec, "find_event", lambda d, n, v: {"actual": 0.4, "consensus": 0.3, "previous": 0.4,
                                                           "time": "8:30"})


def test_flash_row_before_fred_update(monkeypatch):
    _patch(monkeypatch)
    row = ec._flash(ITEM, S, TODAY, [date(2026, 11, 10)])  # FRED 현재 값은 11/10에 정해짐 → 아직 반영 전
    assert row["_flash"] and row["최신"] == "0.4%" and row["이전"] == "0.35%" and row["기준"] == "26.11"
    assert row["서프라이즈"].startswith("▲ 상회") and str(row["발표"]) == "2026-12-10 22:30:00"


def test_no_flash_after_fred_update_or_other_day(monkeypatch):
    _patch(monkeypatch)
    assert ec._flash(ITEM, S, TODAY, [TODAY]) is None                       # FRED에 이미 반영
    assert ec._flash(ITEM, S, date(2026, 12, 11), [date(2026, 11, 10)]) is None  # 오늘이 발표일 아님


def test_release_hours():
    ny = lambda t: pd.Timestamp(t, tz="America/New_York")
    assert ec.release_hours(ny("2026-12-10 08:30")) and ec.release_hours(ny("2026-12-10 12:00"))
    assert not ec.release_hours(ny("2026-12-10 13:00")) and not ec.release_hours(ny("2026-12-12 09:00"))  # 오후·토요일
    assert ec.refresh_bucket(ny("2026-12-10 08:30")) != ec.refresh_bucket(ny("2026-12-10 08:32"))         # 2분 단위
    assert ec.refresh_bucket(ny("2026-12-10 14:00")) == ec.refresh_bucket(ny("2026-12-10 14:02"))         # 15분 단위
