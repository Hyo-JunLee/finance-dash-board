"""시황 대시보드 진입점. 실행: streamlit run app.py"""
import streamlit as st

import config

st.set_page_config(page_title="시황 대시보드", page_icon="📈", layout="wide")
# 카드 숫자가 좁은 화면에서 '5,064…'처럼 잘리지 않게 화면 폭에 맞춰 줄어들게
# 상단 메뉴 아래 기본 여백(약 6rem)을 줄여 첫 화면에 더 많이 보이게
st.html("<style>[data-testid='stMetricValue'] { font-size: clamp(1.35rem, 2.1vw, 2.25rem); }"
        "[data-testid='stMainBlockContainer'] { padding-top: 3.8rem; }</style>")

pages = [
    st.Page("views/overview.py", title="오늘의 시황", icon="🏠", default=True),
    st.Page("views/portfolio.py", title="내 포트폴리오", icon="💼"),
    st.Page("views/korea.py", title="국내 증시", icon="🇰🇷"),
    st.Page("views/global_markets.py", title="미국 증시", icon="🇺🇸"),
    st.Page("views/macro.py", title="매크로", icon="🏦"),
    st.Page("views/bonds.py", title="채권", icon="📐"),
    st.Page("views/news.py", title="뉴스", icon="📰"),
    st.Page("views/status.py", title="데이터 상태", icon="🩺"),
]
if config.PUBLIC_MODE:  # 공개 배포: 개인 포트폴리오·맥 전용 상태 화면 제외
    pages = [p for p in pages if p.title not in ("내 포트폴리오", "데이터 상태")]

# 메뉴는 상단 가로 바로 (사이드바를 없애 본문 폭 확보). 새로고침 버튼은 상단 바 오른쪽에 고정
page = st.navigation(pages, position="top")
st.html("<style>.st-key-refresh { position: fixed; top: .55rem; right: 3.6rem; z-index: 999991; width: auto; }"
        ".st-key-refresh button { padding: .1rem .6rem; min-height: 2rem; }</style>")
with st.container(key="refresh"):
    if st.button("🔄 새로고침", help="캐시를 비우고 모든 데이터를 다시 불러옵니다"):
        st.cache_data.clear()
        st.rerun()
page.run()
