"""FedWatch 방식 FOMC 확률: 25bp 단위 분할과 회의 전후 금리 계산."""
from datetime import date

from data.fedwatch import meeting_probs, split


def test_split():
    assert {k: round(v, 2) for k, v in split(16).items()} == {0: 0.36, 25: 0.64}
    assert {k: round(v, 2) for k, v in split(-40).items()} == {-25: 0.4, -50: 0.6}
    assert split(0) == {0: 1.0}


def test_meeting_probs_chain():
    # 11월(회의 없음) 3.88 → 12/9 회의, 1월은 회의 없음(가정) → 12월 회의 후 금리 = 1월 평균 4.13
    rates = {(2026, 11): 3.88, (2026, 12): 4.055, (2027, 1): 4.13}
    out = meeting_probs(rates, [date(2026, 12, 9)], date(2026, 11, 28))
    assert len(out) == 1
    r = out[0]
    assert abs(r["전"] - 3.88) < 1e-9 and abs(r["후"] - 4.13) < 1e-9
    assert round(r["변화bp"]) == 25 and round(r["인상"], 2) == 1.0 and round(r["동결"], 2) == 0.0


def test_meeting_probs_solves_post_rate_when_next_month_has_meeting():
    # 12/9 회의, 1월에도 회의 → 12월 평균에서 회의 후 금리를 풀어냄: (4.055*31 - 3.88*9) / 22
    rates = {(2026, 11): 3.88, (2026, 12): 4.055, (2027, 1): 4.2}
    out = meeting_probs(rates, [date(2026, 12, 9), date(2027, 1, 27)], date(2026, 11, 28))
    assert abs(out[0]["후"] - (4.055 * 31 - 3.88 * 9) / 22) < 1e-9
