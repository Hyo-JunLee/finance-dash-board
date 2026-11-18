"""시황 대시보드 진입점. 실행: streamlit run app.py"""
import streamlit as st

import config

st.set_page_config(page_title="시황 대시보드", page_icon="📈", layout="wide")
# 카드 숫자가 좁은 화면에서 '5,064…'처럼 잘리지 않게 화면 폭에 맞춰 줄어들게
st.html("<style>[data-testid='stMetricValue'] { font-size: clamp(1.35rem, 2.1vw, 2.25rem); }</style>")

pages = [
    st.Page("views/overview.py", title="오늘의 시황", icon="🏠", default=True),
    st.Page("views/portfolio.py", title="내 포트폴리오", icon="💼"),
    st.Page("views/korea.py", title="국내 증시", icon="🇰🇷"),
    st.Page("views/global_markets.py", title="해외 증시", icon="🌎"),
    st.Page("views/macro.py", title="매크로", icon="🏦"),
    st.Page("views/bonds.py", title="채권", icon="📐"),
    st.Page("views/news.py", title="뉴스", icon="📰"),
    st.Page("views/status.py", title="데이터 상태", icon="🩺"),
]
if config.PUBLIC_MODE:  # 공개 배포: 개인 포트폴리오·맥 전용 상태 화면 제외
    pages = [p for p in pages if p.title not in ("내 포트폴리오", "데이터 상태")]

with st.sidebar:
    if st.button("🔄 데이터 새로고침", width="stretch"):
        st.cache_data.clear()
        st.rerun()

st.navigation(pages).run()
