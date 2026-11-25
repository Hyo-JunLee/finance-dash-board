"""AI 요약 따라잡기: 회차 계산과 미완료 작업 판단."""
import os
from datetime import datetime

import summarize as sm


def test_due_slot():
    assert sm.due_slot(datetime(2026, 11, 25, 20, 11)) == datetime(2026, 11, 25, 16, 0)
    assert sm.due_slot(datetime(2026, 11, 25, 9, 0)) == datetime(2026, 11, 25, 8, 30)
    assert sm.due_slot(datetime(2026, 11, 25, 7, 0)) == datetime(2026, 11, 24, 16, 0)


def test_bond_view_only_in_morning_slot():
    assert sm.summarize_bond_view in sm.slot_jobs(datetime(2026, 11, 25, 8, 30))
    assert sm.summarize_bond_view not in sm.slot_jobs(datetime(2026, 11, 25, 16, 0))


def test_pending_jobs_by_file_time(tmp_path, monkeypatch):
    slot = datetime(2026, 11, 25, 16, 0)
    files = {}
    for name, when in (("summarize_telegram", datetime(2026, 11, 25, 20, 15)),   # 회차 후 갱신 → 완료
                       ("summarize_news", datetime(2026, 11, 25, 8, 41))):      # 아침 것 그대로 → 미완료
        f = tmp_path / f"{name}.json"
        f.write_text("{}")
        os.utime(f, (when.timestamp(), when.timestamp()))
        files[name] = f
    files["summarize_buyside"] = tmp_path / "missing.json"                       # 파일 없음 → 미완료
    monkeypatch.setattr(sm, "JOB_FILES", files)
    jobs = [sm.summarize_telegram, sm.summarize_news, sm.summarize_buyside]
    assert sm.pending_jobs(slot, jobs) == [sm.summarize_news, sm.summarize_buyside]
