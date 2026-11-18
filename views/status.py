import re
from datetime import datetime

import pandas as pd
import streamlit as st

import config
from data.health import FAIL, ICON, OK, WARN, check_all

st.title("데이터 상태")
st.caption("소스별로 '받아지는지'뿐 아니라 '마지막 데이터가 얼마나 오래됐는지'까지 점검합니다. "
           "수집기가 3시간마다 같은 점검을 돌리고, 새로 문제가 생기거나 회복되면 맥 알림을 보냅니다.")


@st.cache_data(ttl=60 * 5, show_spinner=False)
def _run() -> tuple[pd.DataFrame, datetime]:
    return check_all(), datetime.now()


c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
if c2.button("🔄 다시 점검", width="stretch"):
    _run.clear()
with st.spinner("14개 소스 점검 중... (10초 정도)"):
    df, checked_at = _run()
c1.caption(f"점검 시각 {checked_at:%Y-%m-%d %H:%M:%S} · 5분 캐시")

counts = df["상태"].value_counts()
m = st.columns(3)
m[0].metric("정상", f"{counts.get(OK, 0)}개", border=True)
m[1].metric("주의 (늦어짐)", f"{counts.get(WARN, 0)}개", border=True)
m[2].metric("실패", f"{counts.get(FAIL, 0)}개", border=True)

order = {FAIL: 0, WARN: 1, OK: 2}
view = df.assign(_o=df["상태"].map(order)).sort_values("_o", kind="stable").drop(columns="_o")
view["상태"] = view["상태"].map(ICON)
st.dataframe(view[["상태", "소스", "마지막 데이터", "설명", "쓰는 곳"]], hide_index=True, width="stretch",
             height=38 + 35 * len(view),
             column_config={"상태": st.column_config.TextColumn(width=50),
                            "설명": st.column_config.TextColumn(width="large")})

# ── 수집기 최근 실행 ─────────────────────────────────
st.markdown("##### 수집기 최근 실행")
log = config.BASE_DIR / "collected" / "collect.log"
if log.exists():
    text = log.read_text()
    runs = re.findall(r"^(\S+ \S+),\d+ \[INFO\] === 수집 종료 \(실패 (\d+)개(?:: ([^)]*))?\)", text, re.M)
    alerts = re.findall(r"^(\S+ \S+),\d+ \[WARNING\] 알림: (.*)$", text, re.M)
    if runs:
        hist = pd.DataFrame(runs[-10:][::-1], columns=["완료 시각", "실패 단계 수", "실패 단계"])
        st.dataframe(hist, hide_index=True, width="stretch")
    if alerts:
        st.markdown("##### 최근 알림")
        st.dataframe(pd.DataFrame(alerts[-10:][::-1], columns=["시각", "내용"]), hide_index=True, width="stretch")
    st.caption(f"전체 기록: {log}")
else:
    st.info("수집기 실행 기록이 없습니다.")
