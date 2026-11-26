"""🧠 발표 해석: 중요 지표 묶음 찾기, 30분 뒤 한 번만 실행, 입력에 없는 숫자 거르기."""
from datetime import date, datetime

import data.release_insight as ri

ROWS = [{"gmt": "8:30", "eventName": "Core PCE Price Index", "actual": "0.3%", "consensus": "0.3%", "previous": "0.3%"},
        {"gmt": "8:30", "eventName": "Initial Jobless Claims", "actual": "189K", "consensus": "201K", "previous": "202K"},
        {"gmt": "10:00", "eventName": "New Home Sales", "actual": "700K", "consensus": "690K", "previous": "680K"},
        {"gmt": "14:00", "eventName": "CPI", "actual": "&nbsp;", "consensus": "0.3%", "previous": "0.2%"}]
DAY = date(2026, 11, 25)


def test_release_slots_only_with_released_key_indicator():
    slots = ri.release_slots(ROWS, DAY)
    assert [ri.slot_key(t) for t, _ in slots] == ["2026-11-25 08:30 ET"]   # 10:00 중요 지표 없음, 14:00 아직 미발표
    assert len(slots[0][1]) == 2                                            # 같은 시각 행은 함께 넘김


def test_unknown_numbers_and_drop():
    facts = "근원 PCE 0.3% 예상 0.3% 10년물 4.850% → 4.836% (-1.4bp)"
    assert ri.unknown_numbers("근원 PCE 0.3%로 부합함. 10년물 4.85%에서 1.4bp 내림.", facts) == set()
    assert ri.unknown_numbers("12월 인하 확률 65%로 내려감.", facts) == {"12", "65"}
    assert ri.drop_sentences("근원 PCE 부합함. 인하 확률 65%로 내려감.", {"65"}) == "근원 PCE 부합함."


def test_check_and_run_waits_30min_and_runs_once(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "STORE", tmp_path / "s.json")
    monkeypatch.setattr(ri, "HISTORY", tmp_path / "h.jsonl")
    import data.consensus as dc
    monkeypatch.setattr(dc, "fetch_day", lambda d: ROWS if d == DAY else [])
    monkeypatch.setattr(ri, "market_reaction", lambda t: [])
    monkeypatch.setattr(ri, "build_facts", lambda t, g, r: "facts")
    calls, sent = [], []
    monkeypatch.setattr(ri, "interpret", lambda f: (calls.append(f) or {"headline": "h", "result": "r", "meaning": "m",
                                                                         "fed": "f", "market": "k"}, {}))
    ny = lambda h, m: datetime(2026, 11, 25, h, m, tzinfo=ri.NY)
    assert ri.check_and_run(ny(8, 50), send=sent.append) == []             # 30분 전
    assert len(ri.check_and_run(ny(9, 5), send=sent.append)) == 1          # 30분 지남 → 실행
    assert ri.check_and_run(ny(9, 15), send=sent.append) == []             # 이미 함
    assert len(calls) == 1 and sent[0].startswith("🧠 11/25 22:30 발표 해석")
    assert ri.load()[0]["slot"] == "2026-11-25 08:30 ET"


def test_close_reaction_added_once_after_us_close(tmp_path, monkeypatch):
    monkeypatch.setattr(ri, "STORE", tmp_path / "s.json")
    monkeypatch.setattr(ri, "HISTORY", tmp_path / "h.jsonl")
    import data.consensus as dc
    monkeypatch.setattr(dc, "fetch_day", lambda d: [])
    ins = {"slot": "2026-11-25 08:30 ET", "when_kst": "11/25 22:30", "headline": "h",
           "facts": "발표 전후 30분 시장 반응 (…):\n- 미 10년물: 4.850% → 4.836% (-1.4bp)\n\n참고: 목표 2%"}
    (tmp_path / "s.json").write_text(__import__("json").dumps([ins]))
    seen = {}
    monkeypatch.setattr(ri, "market_reaction", lambda t, minutes=30, until=None: seen.setdefault("until", until) and
                        ["- 미 10년물: 4.850% → 4.900% (+5.0bp)"])
    monkeypatch.setattr(ri, "interpret_close", lambda f: (seen.setdefault("facts", f), "30분 뒤보다 상승폭이 커짐.")[1])
    sent = []
    ri.check_and_run(datetime(2026, 11, 25, 16, 0, tzinfo=ri.NY), send=sent.append)   # 16:20 전 → 아직
    assert "close" not in ri.load()[0]
    ri.check_and_run(datetime(2026, 11, 25, 16, 25, tzinfo=ri.NY), send=sent.append)
    ri.check_and_run(datetime(2026, 11, 25, 16, 35, tzinfo=ri.NY), send=sent.append)  # 이미 덧붙임 → 다시 안 함
    assert ri.load()[0]["close"]["text"] == "30분 뒤보다 상승폭이 커짐." and len(sent) == 1
    assert seen["until"].hour == 16 and "-1.4bp" in seen["facts"] and "+5.0bp" in seen["facts"]
    assert sent[0].startswith("🔔 11/25 22:30 발표 · 장 마감 반응")


def test_insight_age_and_fresh_rule():
    import pandas as pd
    import ui
    ins = {"slot": "2026-11-25 08:30 ET"}
    assert ui.insight_age(ins, pd.Timestamp("2026-11-25 23:00", tz="Asia/Seoul")) == 0
    assert ui.insight_age(ins, pd.Timestamp("2026-11-26 09:00", tz="Asia/Seoul")) == 1
    assert ui._age_label(8) == "8일 전"
