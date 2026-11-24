"""아침 시황 원문 카드 (오늘의 시황 맨 위, AI 없이 원문 그대로).

- 월~금: 한화 리서치 전략 채널(@hanwhastrategy) ★ 채권·주식·환율 아침 시황 (보통 08:05~08:50 게시)
- 토: @ehdwl 아침 글의 '*FICC:' 부분 (보통 06:30경 게시, 금요일 미국장 마감 반영)
- 일: 토요일 FICC를 그대로
- 원문이 아직 없으면(공휴일·게시 전) 대시보드 데이터로 만든 숫자 줄만 보여준다

화면을 열 때 채널을 직접 읽고 10분간 캐시한다 → 정해진 실행 시각이나 맥 잠자기와 무관.
"""
import re
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from data.telegram import fetch_since

HANWHA = "hanwhastrategy"
HANWHA_KINDS = ("채권", "주식", "환율")
FICC_CHANNEL = "ehdwl"
FICC_FIRST = ("국채금리", "달러화")  # 펼쳐 둘 문단 (나머지는 '더 보기')
_HANWHA_HEAD = re.compile(r"\s*★\s*(\S+)\s*아침\s*시황\s*\((\d{1,2})/(\d{1,2})\)")


def parse_hanwha(text: str) -> dict | None:
    """'★ 채권 아침 시황 (11/24)\\n- 숫자줄…\\n- 해설…' → {kind, month, day, numbers: [..], comment}."""
    m = _HANWHA_HEAD.match(text or "")
    if not m:
        return None
    body = text[m.end():]
    items = [x.strip() for x in re.split(r"(?:^|\n)\s*-\s+", body) if x.strip()]
    if not items:
        return None
    # 첫 항목 = 숫자 줄 (한 줄에 여러 개가 공백 2칸 이상으로 이어져 있음), 나머지 = 해설
    numbers = [x.strip() for line in items[0].splitlines() for x in re.split(r"\s{2,}", line) if x.strip()]
    return {"kind": m.group(1), "month": int(m.group(2)), "day": int(m.group(3)),
            "numbers": numbers, "comment": "\n".join(items[1:])}


def pick_hanwha(posts: list[dict], day: date) -> dict[str, dict]:
    """그날 날짜가 적힌 ★ 채권·주식·환율 아침 시황 {종류: {numbers, comment, time, link}}."""
    out = {}
    for p in sorted(posts, key=lambda p: p["time"]):
        parsed = parse_hanwha(p.get("text") or "")
        if parsed and parsed["kind"] in HANWHA_KINDS and (parsed["month"], parsed["day"]) == (day.month, day.day):
            out.setdefault(parsed["kind"], {**parsed, "time": p["time"], "link": p["link"]})
    return out


def parse_ficc(text: str) -> dict | None:
    """'*FICC: 제목\\n\\n국제유가는…\\n\\n달러화는…' → {title, first: [문단], rest: [문단]}."""
    i = (text or "").find("*FICC:")
    if i < 0:
        return None
    section = text[i + len("*FICC:"):].strip()
    title, _, rest = section.partition("\n")
    paras = [x.strip() for x in re.split(r"\n\s*\n", rest) if x.strip()]
    first = [p for key in FICC_FIRST for p in paras if p.startswith(key)][:len(FICC_FIRST)]
    return {"title": title.strip(), "first": first, "rest": [p for p in paras if p not in first]}


def pick_ficc(posts: list[dict], day: date) -> dict | None:
    for p in sorted(posts, key=lambda p: p["time"], reverse=True):
        if p["time"].date() == day:
            parsed = parse_ficc(p.get("text") or "")
            if parsed:
                return {**parsed, "time": p["time"], "link": p["link"]}
    return None


def plan(now: datetime) -> tuple[str, date]:
    """오늘 보여줄 원문 종류와 그 글이 올라오는 날짜. 월~금 한화, 토 FICC(당일), 일 FICC(토요일)."""
    wd = now.weekday()
    if wd < 5:
        return "hanwha", now.date()
    return "ficc", now.date() - timedelta(days=wd - 5)


@st.cache_data(ttl=600, show_spinner=False)
def load_brief(kind: str, day: date) -> dict:
    """{kind, day, cards | ficc, error}. 실패해도 예외 대신 error를 담아 돌려준다."""
    since = pd.Timestamp(day)
    try:
        if kind == "hanwha":
            return {"kind": kind, "day": day, "cards": pick_hanwha(fetch_since(HANWHA, since), day)}
        return {"kind": kind, "day": day, "ficc": pick_ficc(fetch_since(FICC_CHANNEL, since, max_pages=2), day)}
    except Exception as e:
        return {"kind": kind, "day": day, "error": str(e)}


def numbers_line() -> str:
    """원문이 없을 때(토요일 위쪽·게시 전) 보여줄 숫자 줄: 대시보드 데이터 그대로."""
    from data.market import get_snapshot
    parts = []
    for key, name in (("spx", "S&P 500"), ("ndx", "나스닥"), ("us2y", "미 2년물"), ("us10y", "미 10년물"), ("dxy", "달러인덱스")):
        try:
            s = get_snapshot(key)
        except Exception:
            continue
        if s["kind"] == "rate":
            parts.append(f"{name} {s['last']:.3f}% ({s['chg'] * 100:+.1f}bp)")
        else:
            parts.append(f"{name} {s['last']:,.2f} ({s['pct']:+.2f}%)")
    return " · ".join(parts)
