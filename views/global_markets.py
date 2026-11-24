from datetime import datetime

import pandas as pd
import streamlit as st

from data import global_markets as gm
from ui import UP_COLOR, DOWN_COLOR, line_chart, returns_bar, show_chart, treemap

RET_COLS = list(gm.PERIODS)
COLOR_RANGE = {"1일": 3, "1주": 6, "1개월": 10, "YTD": 30, "1년": 40}  # 히트맵 색 포화 기준(±%)
CHART_PERIODS = {"3개월": pd.DateOffset(months=3), "6개월": pd.DateOffset(months=6),
                 "YTD": "YTD", "1년": pd.DateOffset(years=1), "2년": pd.DateOffset(years=2)}

st.title("미국 증시")
st.caption(f"조회 시각 {datetime.now():%Y-%m-%d %H:%M} · Yahoo Finance (수정주가, 5분 캐시) · 상승 빨강 / 하락 파랑")


def _ret_color(v) -> str:
    if pd.isna(v) or v == 0:
        return ""
    return f"color: {UP_COLOR if v > 0 else DOWN_COLOR}"


def _since(df: pd.DataFrame, period) -> pd.DataFrame:
    last = df.index[-1]
    start = pd.Timestamp(year=last.year, month=1, day=1) if period == "YTD" else last - period
    return df[df.index >= start]


with st.spinner("시세 불러오는 중..."):
    idx_closes = gm.get_closes(tuple(gm.INDICES))

# ── 주요 지수 (4개, 한눈에) ───────────────────────────
live_closes, status = gm.apply_market_status(idx_closes)
table = gm.returns_table(live_closes).reindex([t for t in gm.INDICES if t in live_closes])
table = table.drop(columns="기준일").join(status)
table.insert(0, "지수", [gm.INDICES[t][0] for t in table.index])
st.dataframe(
    table.style.map(_ret_color, subset=RET_COLS)
    .format({"현재": "{:,.2f}", "기준 시각": "{:%m/%d %H:%M}", **{c: "{:+.2f}%" for c in RET_COLS}}, na_rep="-"),
    hide_index=True, width="stretch", height=38 + 35 * len(table),
)
st.caption("기준 시각은 한국 시간 · 장중은 최신 체결 시각(Yahoo, 최대 15분 지연), 마감은 뉴욕 정규장 마감 시각")

# ── S&P 500 히트맵 ───────────────────────────────────
h1, h2 = st.columns([3, 2], vertical_alignment="bottom")
h1.subheader("🗺️ S&P 500 대형주 히트맵")
period = h2.segmented_control("수익률 기간", RET_COLS, default="1일", key="heat_period") or "1일"

with st.spinner("히트맵 불러오는 중... (첫 로딩 시 시가총액 계산에 10초 정도)"):
    heat = gm.heatmap_data(period)
if heat.empty:
    st.warning("히트맵 데이터를 불러오지 못했습니다.")
else:
    st.plotly_chart(treemap(heat, COLOR_RANGE[period]), width="stretch", config={"displayModeBar": False})
    st.caption(f"{len(heat)}개 종목 · 크기 = 시가총액(발행주식수 × 현재가) · 섹터 타일 수익률은 시총 가중 평균")

# ── 주요 종목 시세표 (investing.com 스타일) ──────────────
SEMIS = ["NVDA", "AVGO", "AMD", "QCOM", "TXN", "AMAT", "MU", "TSM", "SOXL", "SOXS"]


@st.fragment(run_every="60s")  # 이 표만 1분마다 다시 그림 (페이지 전체는 그대로)
def _quote_section() -> None:
    q1, q2 = st.columns([3, 2], vertical_alignment="bottom")
    q1.subheader("📋 주요 종목 시세")
    search = q2.text_input("종목 검색", placeholder="이름 또는 티커 (예: 엔비디아, NVDA)", label_visibility="collapsed")
    group = st.pills("구분", ["전체", "반도체", *gm.HEATMAP_STOCKS], default="전체", key="quote_group",
                     label_visibility="collapsed") or "전체"
    with st.spinner("시세 불러오는 중..."):
        quotes = gm.quote_table()
    if quotes.empty:
        st.warning("종목 시세를 불러오지 못했습니다.")
    else:
        q = quotes
        if group == "반도체":
            q = q[q["티커"].isin(SEMIS)]
        elif group != "전체":
            q = q[q["섹터"] == group]
        if search:
            q = q[q["종목"].str.contains(search, case=False) | q["티커"].str.contains(search, case=False)]
        q = q.sort_values("시가총액($bn)", ascending=False, na_position="last")
        st.dataframe(
            q.style.map(_ret_color, subset=["전일 대비", "등락률", "시간외 등락률"])
            .format({"현재가": "${:,.2f}", "전일 대비": "{:+,.2f}", "등락률": "{:+.2f}%", "시가총액($bn)": "{:,.0f}",
                     "시간외 가격": "${:,.2f}", "시간외 등락률": "{:+.2f}%",
                     "기준 시각": "{:%m/%d %H:%M}", "시간외 시각": "{:%m/%d %H:%M}"}, na_rep="-"),
            hide_index=True, width="stretch", height=min(38 + 35 * len(q), 38 + 35 * 15),
        )
        live = (quotes["상태"] != "마감").sum()
        st.caption(f"{len(q)}개 종목 · 전 거래일 정규장 종가 대비 · 기준 시각은 한국 시간 "
                   f"(장중 {live}개는 Yahoo 1분봉 최신값, 최대 15분 지연 / 마감은 뉴욕 16:00 종가) · "
                   "열 제목을 누르면 정렬 · TSMC는 뉴욕 상장 ADR(TSM) · 1분마다 자동 갱신")
        st.caption(r"시간외: 애프터마켓(뉴욕 16:00\~20:00, 한국 06:00\~10:00 · 서머타임 05:00\~09:00)은 그날 정규장 종가 대비, "
                   r"프리마켓(뉴욕 04:00\~09:30)은 전 거래일 정규장 종가 대비 · Yahoo 5분봉 마지막 값 · "
                   "시간외에는 거래가 뜸한 종목이 많아 마지막 체결이 수십 분 전일 수 있음(시간외 시각 확인) · "
                   "가격이 튈 수 있음 · 정규장 중에는 비어 있음")


_quote_section()

st.divider()

# ── 섹터·스타일·지수 비교 ────────────────────────────
s1, s2 = st.columns([3, 2], vertical_alignment="bottom")
s1.subheader("🧭 섹터·스타일")
chart_period = s2.segmented_control("차트 기간", list(CHART_PERIODS), default="1년", key="idx_period") or "1년"

left, right = st.columns(2, gap="large")
with left:
    sec_closes = gm.get_closes(tuple(gm.SECTOR_ETFS))
    sec_ret = gm.returns_table(sec_closes)[period]
    sec_ret.index = [f"{gm.SECTOR_ETFS[t]} ({t})" for t in sec_ret.index]
    show_chart(returns_bar(sec_ret, f"섹터 ETF 수익률 ({period}, 히트맵 기간을 따름)", height=400))

with right:
    style_tickers = tuple(sorted({t for pair in gm.STYLE_PAIRS.values() for t in pair}))
    style = _since(gm.get_closes(style_tickers).ffill(), CHART_PERIODS[chart_period]).dropna()
    ratios = {}
    for label, (num, den) in gm.STYLE_PAIRS.items():
        r = style[num] / style[den]
        ratios[label] = r / r.iloc[0] * 100
    show_chart(line_chart(ratios, f"스타일 상대강도 (시작 = 100, {chart_period})", hline=100, decimals=1,
                          height=400))
    st.caption("100보다 위로 오르면 앞쪽(분자)이 상대적으로 강세")

names = {v[0]: k for k, v in gm.INDICES.items()}
picked = st.multiselect("비교할 지수", list(names), default=list(names))
if picked:
    sub = _since(idx_closes[[names[n] for n in picked]].ffill(), CHART_PERIODS[chart_period]).dropna()
    norm = sub / sub.iloc[0] * 100
    show_chart(line_chart({n: norm[names[n]] for n in picked}, f"지수 비교 (시작 = 100, {chart_period})",
                          hline=100, decimals=1))
