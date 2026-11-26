"""발표 현황 보드: 같은 지표의 전월비·전년비를 한 줄로 묶고, 상회/하회 색·D-day를 붙인다."""
from datetime import date

import pandas as pd

import ui


def _row(name, latest, cons, label, surprise, released="2026-11-10 22:30", nxt=date(2026, 12, 10)):
    return {"지표": name, "발표": pd.Timestamp(released), "최신": latest, "예상": cons, "서프라이즈": label,
            "이전": "0.6%", "기준": "26.10", "다음 발표": f"{nxt:%m/%d}", "_delta": 0.0, "_surprise": surprise,
            "_warn": "", "_notime": False, "_next": nxt}


def test_board_groups_measures_and_marks_tone(monkeypatch):
    out = []
    monkeypatch.setattr(ui.st, "html", out.append)
    monkeypatch.setattr(ui.st, "caption", lambda *a, **k: None)
    df = pd.DataFrame([_row("CPI 전월비", "0.35%", "0.3%", "▲ 상회 +0.1%p", 0.1),
                       _row("CPI 전년비", "3.74%", "3.6%", "▲ 상회 +0.1%p", 0.1),
                       _row("PPI 전월비", "0.35%", "0.4%", "▼ 하회 -0.1%p", -0.1, nxt=date(2026, 11, 27)),
                       _row("실업률", "4.10%", "-", "-", 0.0, released="2026-11-24 22:30")])
    ui.release_board(df, today=date(2026, 11, 26))
    page = out[-1]
    assert page.count('class="rb-row') == 3                     # CPI 두 줄 → 한 줄
    assert '<div class="rb-row up">' in page and '<div class="rb-row down">' in page
    assert "D-14" in page and 'rb-pill soon">D-1' in page       # 3일 안이면 주황
    assert page.count("🆕 최근") == 1                             # 11/24 발표만 최근 3일


def test_board_shows_flash_badge(monkeypatch):
    out = []
    monkeypatch.setattr(ui.st, "html", out.append)
    monkeypatch.setattr(ui.st, "caption", lambda *a, **k: None)
    r = {**_row("CPI 전월비", "0.4%", "0.3%", "▲ 상회 +0.1%p", 0.1), "_flash": True, "_next": None}
    ui.release_board(pd.DataFrame([r]), today=date(2026, 12, 10))
    assert "⚡속보" in out[-1] and '<div class="rb-row up">' in out[-1]
    assert "⚡속보" in ui.release_badge_html(r, today=date(2026, 12, 10))
