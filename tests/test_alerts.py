"""휴대폰 알림: 시간대 제한 없이 보내고, 같은 항목은 하루 한 번."""
from datetime import datetime

import pandas as pd

import alerts


def _snap(z=-2.5):
    return {"label": "나스닥", "kind": "index", "last": 23000.0, "chg": -500.0, "pct": -2.1, "z": z,
            "asof": pd.Timestamp("2026-11-27 06:00")}


def test_check_dedupes_and_respects_quiet_hours(tmp_path, monkeypatch):
    import data.market as mk
    monkeypatch.setattr(alerts, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(mk, "SUMMARY_ITEMS", {"ndx": None, "spx": None})
    monkeypatch.setattr(mk, "get_snapshot", lambda k: _snap() if k == "ndx" else _snap(z=0.3))
    sent = []
    monkeypatch.setattr(alerts, "send", sent.append)

    alerts.check(datetime(2026, 11, 27, 3, 0))   # 새벽에도 바로 보냄
    assert len(sent) == 1 and "나스닥 이례적 하락 -2.10%" in sent[0] and "2.5배" in sent[0]
    alerts.check(datetime(2026, 11, 27, 3, 10))  # 같은 날 같은 항목: 다시 안 보냄
    assert len(sent) == 1
