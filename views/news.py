from datetime import datetime

import pandas as pd
import streamlit as st

from data.news import get_news
from ui import render_news

WINDOWS = {"6시간": 6, "24시간": 24, "3일": 72, "전체": None}
PAGE_SIZE = 40

st.title("뉴스")

with st.spinner("뉴스 불러오는 중..."):
    news = get_news()

if news.empty:
    st.error("뉴스를 불러오지 못했습니다. 네트워크 상태를 확인해주세요.")
    st.stop()

st.caption(f"조회 시각 {datetime.now():%Y-%m-%d %H:%M} · 10분마다 갱신 · 총 {len(news)}건 (중복 제거)")

# ── 필터 ─────────────────────────────────────────────
c1, c2, c3 = st.columns([2, 1.2, 2], vertical_alignment="bottom")
query = c1.text_input("키워드 검색", placeholder="예: 금리, 반도체, Fed, Nvidia").strip()
window = c2.segmented_control("기간", list(WINDOWS), default="24시간") or "전체"
sources = c3.multiselect("출처", sorted(news["source"].unique()), placeholder="전체 출처")
show_summary = st.toggle("요약 보기", value=True)

view = news
if WINDOWS[window]:
    view = view[view["time"] >= pd.Timestamp.now() - pd.Timedelta(hours=WINDOWS[window])]
if sources:
    view = view[view["source"].isin(sources)]
if query:
    hit = view["title"].str.contains(query, case=False, regex=False) | view["summary"].str.contains(
        query, case=False, regex=False
    )
    view = view[hit]

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
        render_news(subset.head(shown), query, show_summary)
        if len(subset) > shown:
            if st.button(f"더 보기 ({len(subset) - shown}건 남음)", key=f"more_{cat}"):
                st.session_state[key] = shown + PAGE_SIZE
                st.rerun()
