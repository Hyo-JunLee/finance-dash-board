from datetime import datetime

import pandas as pd
import streamlit as st

from data.news import get_news_annotated, load_news_summary
from data.news_topics import TOPIC_NAMES
from ui import render_issues, render_news

WINDOWS = {"6시간": 6, "24시간": 24, "3일": 72, "전체": None}
PAGE_SIZE = 40

st.title("뉴스")

with st.spinner("뉴스 불러오는 중..."):
    news = get_news_annotated()

if news.empty:
    st.error("뉴스를 불러오지 못했습니다. 네트워크 상태를 확인해주세요.")
    st.stop()

st.caption(f"조회 시각 {datetime.now():%Y-%m-%d %H:%M} · 10분마다 갱신 · 총 {len(news)}건 (중복 제거) · "
           "주제는 제목·요약 키워드로 자동 분류")

# ── 오늘의 이슈 (AI 요약, 오늘의 시황과 같은 것 · 추가 AI 호출 없음) ──────────
summary = load_news_summary()
if summary and summary.get("issues"):
    made = pd.Timestamp(summary["generated_at"])
    with st.expander(f"🗞️ 오늘의 이슈 · {made:%m/%d %H:%M} AI 요약 (하루 2회 08:30·16:00) — 펼쳐서 이슈별 원문 기사 보기"):
        render_issues(summary, per_region=6)

# ── 필터 ─────────────────────────────────────────────
c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
query = c1.text_input("키워드 검색", placeholder="예: 금리, 반도체, Fed, Nvidia").strip()
window = c2.segmented_control("기간", list(WINDOWS), default="24시간") or "전체"
topics = st.pills("주제", TOPIC_NAMES, selection_mode="multi", key="news_topics",
                  help="여러 개 고르면 그중 하나라도 해당하는 기사. 아무것도 안 고르면 전체")
t1, t2, t3 = st.columns(3)
group = t1.toggle("같은 사건 묶기", value=True, help="제목 핵심 단어가 많이 겹치는 기사를 한 줄로 (대표 = 가장 최근 기사)")
show_other = t2.toggle("주제 없는 기사도 보기", value=False, help="인사·행사·생활 등 시장 주제 키워드가 없는 기사")
show_summary = t3.toggle("요약 보기", value=True)

view = news
if WINDOWS[window]:
    view = view[view["time"] >= pd.Timestamp.now() - pd.Timedelta(hours=WINDOWS[window])]
if query:
    hit = view["title"].str.contains(query, case=False, regex=False) | view["summary"].str.contains(
        query, case=False, regex=False)
    view = view[hit]
untagged = view["group_topics"].str.len() == 0
hidden = 0
if not show_other and not query:  # 검색할 땐 주제 없는 기사도 보여 줌
    hidden = int(untagged.sum())
    view = view[~untagged]
if topics:
    view = view[view["group_topics"].apply(lambda ts: any(t in ts for t in topics))]

others = {}
if group:  # 묶음마다 가장 최근 기사 1건만, 나머지는 '외 N건'으로
    first = ~view["group"].duplicated()
    for g, sub in view[~first].groupby("group"):
        others[g] = sub
    view = view[first]

note = [f"{len(view)}건 표시"]
if group and others:
    note.append(f"같은 사건 {sum(len(x) for x in others.values())}건 묶음")
if hidden:
    note.append(f"주제 없는 기사 {hidden}건 숨김")
st.caption(" · ".join(note))

# ── 탭별 목록 ────────────────────────────────────────
categories = ["전체", "국내", "해외", "정책·기관"]
tabs = st.tabs([
    f"{c} ({len(view) if c == '전체' else (view['category'] == c).sum()})" for c in categories
])
for cat, tab in zip(categories, tabs):
    with tab:
        subset = view if cat == "전체" else view[view["category"] == cat]
        key = f"news_shown_{cat}"
        shown = st.session_state.get(key, PAGE_SIZE)
        render_news(subset.head(shown), query, show_summary, others=others)
        if len(subset) > shown:
            if st.button(f"더 보기 ({len(subset) - shown}건 남음)", key=f"more_{cat}"):
                st.session_state[key] = shown + PAGE_SIZE
                st.rerun()
