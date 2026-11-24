"""아침 시황 원문 카드: 한화 글·FICC 글 파싱과 요일별 원천 선택."""
from datetime import date, datetime

import pandas as pd
import pytest

from data.morning_brief import parse_ficc, parse_hanwha, pick_ficc, pick_hanwha, plan

BOND = """★ 채권 아침 시황 (11/24)
- 미 국채 2년물 4.694% +4.7bp
  미 국채 10년물 4.944% -0.0bp
- 미 국채 시장은 증시와 국제유가를 살피며 혼조 마감. 이에 단기 금리 반등."""
STOCK = """★ 주식 아침 시황 (11/24)
- 다우지수 49,595.31pt +3.01%   S&P500지수 7,237.17pt +4.73%   나스닥지수 24,089.56pt +6.51%
- 뉴욕 증시는 급등 마감."""


def _post(text, t="2026-11-24 08:11", n=1):
    return {"text": text, "time": pd.Timestamp(t), "link": f"https://t.me/x/{n}"}


def test_parse_hanwha_numbers_and_comment():
    p = parse_hanwha(BOND)
    assert (p["kind"], p["month"], p["day"]) == ("채권", 11, 24)
    assert p["numbers"] == ["미 국채 2년물 4.694% +4.7bp", "미 국채 10년물 4.944% -0.0bp"]
    assert p["comment"].startswith("미 국채 시장은") and p["comment"].endswith("단기 금리 반등.")


def test_parse_hanwha_splits_numbers_on_one_line():
    assert parse_hanwha(STOCK)["numbers"] == ["다우지수 49,595.31pt +3.01%", "S&P500지수 7,237.17pt +4.73%",
                                              "나스닥지수 24,089.56pt +6.51%"]


def test_pick_hanwha_today_only_and_skips_closing_posts():
    posts = [_post(BOND, n=1), _post(STOCK.replace("11/24", "11/23"), n=2),
             _post("★ 채권 마감 시황 (11/24)\n- 국고채 3년물", n=3), _post("", n=4)]
    assert list(pick_hanwha(posts, date(2026, 11, 24))) == ["채권"]


def test_parse_ficc_puts_rates_and_dollar_first():
    text = ("한국 증시는 …\n\n*FICC: 금, 은 급락, 국채 금리 큰 폭 하락\n\n국제유가는 상승.\n\n"
            "달러화는 약세.\n\n국채금리는 큰 폭으로 하락. 특히 단기물의 하락이 컸음\n\n금은 급락.")
    f = parse_ficc(text)
    assert f["title"] == "금, 은 급락, 국채 금리 큰 폭 하락"
    assert [x[:4] for x in f["first"]] == ["국채금리", "달러화는"]
    assert [x[:2] for x in f["rest"]] == ["국제", "금은"]
    assert parse_ficc("FICC 없음") is None


def test_pick_ficc_uses_that_day_post():
    posts = [_post("*FICC: 금요일 것\n\n국채금리는 하락", "2026-11-21 06:33", 1),
             _post("*FICC: 목요일 것\n\n국채금리는 상승", "2026-11-20 06:33", 2)]
    assert pick_ficc(posts, date(2026, 11, 21))["title"] == "금요일 것"
    assert pick_ficc(posts, date(2026, 11, 22)) is None


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 11, 23, 9), ("hanwha", date(2026, 11, 23))),  # 월
    (datetime(2026, 11, 27, 9), ("hanwha", date(2026, 11, 27))),  # 금
    (datetime(2026, 11, 28, 9), ("ficc", date(2026, 11, 28))),    # 토: 당일 FICC
    (datetime(2026, 11, 29, 9), ("ficc", date(2026, 11, 28))),    # 일: 토요일 FICC
])
def test_plan_by_weekday(now, expected):
    assert plan(now) == expected


@pytest.mark.parametrize("now, brief, status", [
    (datetime(2026, 11, 28, 7), {"ficc": None}, "ok"),                       # 토 07시: 게시 전
    (datetime(2026, 11, 28, 10), {"ficc": None}, "warn"),                    # 토 10시: 없음
    (datetime(2026, 11, 29, 10), {"ficc": {"title": "t", "first": ["국채금리는"], "rest": [],
                                           "time": pd.Timestamp("2026-11-28 06:30")}}, "warn"),  # 문단 하나만
    (datetime(2026, 11, 28, 10), {"ficc": {"title": "t", "first": ["국채금리는", "달러화는"], "rest": [],
                                           "time": pd.Timestamp("2026-11-28 06:30")}}, "ok"),
    (datetime(2026, 11, 28, 10), {"error": "timeout"}, "fail"),
    (datetime(2026, 11, 25, 9), {"cards": {}}, "ok"),                        # 평일 09시: 게시 전
    (datetime(2026, 11, 25, 10), {"cards": {"채권": {}}}, "warn"),           # 평일 10시: 일부만
])
def test_health_check_morning_brief(monkeypatch, now, brief, status):
    import data.morning_brief as mb
    from data.health import check_morning_brief
    monkeypatch.setattr(mb, "load_brief", lambda kind, day: brief)
    assert check_morning_brief(now)["상태"] == status


def test_closing_picks_latest_day_and_ignores_morning():
    from data.morning_brief import pick_latest_closing
    posts = [_post("★ 채권 마감 시황 (11/23)\n- 국고채 3년물 3.624% -1.3bp\n- 강세 마감", "2026-11-23 15:45", 1),
             _post("★ 주식 마감 시황 (11/23)\n- KOSPI 4,708.54pt -3.31%\n- 하락 마감", "2026-11-23 15:46", 2),
             _post("★ 채권 아침 시황 (11/24)\n- 미 국채 2년물 4.694% +4.7bp\n- 혼조", "2026-11-24 08:11", 3)]
    day, cards = pick_latest_closing(posts)  # 11/24 마감은 아직 → 11/23 것
    assert day == date(2026, 11, 23) and list(cards) == ["채권", "주식"]
    assert cards["주식"]["numbers"] == ["KOSPI 4,708.54pt -3.31%"]
