from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

from data import macro as m
import config
from data.surprise import get_surprises
from data.telegram import load_summary, recent_posts
from ui import bar_chart, curve_chart, kpi, line_chart, render_post_list, render_summary, show_chart

PERIODS = {"1년": 1, "3년": 3, "5년": 5, "10년": 10}

head_l, head_r = st.columns([3, 1], vertical_alignment="bottom")
head_l.title("매크로 (미국)")
head_l.caption(
    f"조회 시각 {datetime.now():%Y-%m-%d %H:%M} · 경제지표는 FRED(발표 당일 반영, 15분 캐시), "
    "금리·기대인플레는 재무부 종가+CNBC 실시간, 변동성·달러는 Yahoo"
)
years = PERIODS[head_r.segmented_control("차트 기간", list(PERIODS), default="3년") or "3년"]
cutoff = pd.Timestamp.now() - pd.DateOffset(years=years)


def clip(s: pd.Series) -> pd.Series:
    return s[s.index >= cutoff]


# ── 리서치 코멘트 요약 (텔레그램 6개 채널 → Claude 요약, 하루 3회) ──────
with st.expander("📮 리서치 코멘트 요약", expanded=True):
    summary = load_summary()
    if summary is None:
        st.info("아직 요약이 없습니다. 하루 3회(08:30·13:30·18:30) 자동으로 만들어집니다.")
    else:
        made = pd.Timestamp(summary["generated_at"])
        age_h = (pd.Timestamp.now() - made).total_seconds() / 3600
        channels = " · ".join(f"{k} {v}" for k, v in summary.get("channels", {}).items())
        st.caption(f"{made:%m/%d %H:%M} 기준 · 최근 {summary.get('window_hours', 24)}시간 글 "
                   f"{summary.get('post_count', 0)}개 ({channels}) · 하루 3회 08:30·13:30·18:30 갱신")
        if age_h > 15:
            st.warning(f"요약이 {age_h:.0f}시간 전 것입니다. 맥이 꺼져 있었거나 요약 실행이 실패했을 수 있습니다 (데이터 상태 화면 참고).")
        render_summary(summary)
        st.caption("AI(Claude)가 각 채널 글을 묶어 요약한 것으로 틀리거나 빠진 내용이 있을 수 있습니다. "
                   "숫자·판단은 출처 링크의 원문으로 확인하세요. 의견은 각 채널 작성자의 것이며 투자 권유가 아닙니다.")
    with st.popover("원문 최근 글 보기"):
        with st.spinner("불러오는 중..."):
            posts = recent_posts(24)
        if posts:
            render_post_list(posts)
        else:
            st.caption("최근 24시간 동안 글이 없거나 불러오지 못했습니다.")


with st.spinner("FRED 지표 불러오는 중... (첫 로딩은 최대 30초)"):
    m.warmup()

tabs = st.tabs(["💵 금리", "🔥 물가", "👷 고용", "🏭 성장·소비", "🌡️ 금융 여건", "🎯 서프라이즈"])

# ── 금리 ─────────────────────────────────────────────
with tabs[0]:
    fed, y2, y10 = m.fred("DFEDTARU"), m.yields("2Y"), m.yields("10Y")
    s2_10, s3m_10 = m.curve_spread("10Y", "2Y"), m.curve_spread("10Y", "3M")
    rate_kw = dict(unit="%", delta_unit="bp", delta_scale=100, delta_decimals=0)
    c = st.columns(4)
    with c[0]:
        kpi("기준금리 상단", fed[fed.diff().fillna(1) != 0], **rate_kw, help="직전 변경 대비")
    with c[1]:
        kpi("2년물", y2, **rate_kw)
    with c[2]:
        kpi("10년물", y10, **rate_kw)
    with c[3]:
        kpi("10Y−2Y", s2_10, unit="%p", delta_unit="bp", delta_scale=100, delta_decimals=0)

    l, r = st.columns(2)
    with l:
        show_chart(curve_chart(m.yield_curves(), "수익률 곡선 (재무부 종가)"))
    with r:
        show_chart(line_chart(
            {"기준금리 상단": clip(fed), "2년물": clip(y2), "10년물": clip(y10)},
            "기준금리와 국채 금리", unit="%",
        ))
    show_chart(line_chart(
        {"10Y−2Y": clip(s2_10), "10Y−3M": clip(s3m_10)},
        "장단기 금리차", unit="%p", hline=0, hline_label="0 아래 = 역전", height=260,
    ))

# ── 물가 ─────────────────────────────────────────────
with tabs[1]:
    cpi, core, pce = m.fred("CPIAUCNS", "yoy"), m.fred("CPILFENS", "yoy"), m.fred("PCEPILFE", "yoy")
    bei, fwd = m.breakeven_10y(), m.fred("T5YIFR")
    pct_kw = dict(unit="%", delta_unit="%p", freq="M")
    c = st.columns(4)
    with c[0]:
        kpi("CPI (전년비)", cpi, **pct_kw)
    with c[1]:
        kpi("근원 CPI (전년비)", core, **pct_kw)
    with c[2]:
        kpi("근원 PCE (전년비)", pce, **pct_kw, help="연준이 목표로 삼는 물가 지표")
    with c[3]:
        kpi("10년 기대인플레(BEI)", bei, unit="%", delta_unit="bp", delta_scale=100, delta_decimals=0,
            help="명목 10년물 − 물가연동채(TIPS) 10년물")

    l, r = st.columns(2)
    with l:
        show_chart(line_chart(
            {"CPI": clip(cpi), "근원 CPI": clip(core), "근원 PCE": clip(pce)},
            "물가 상승률 (전년비)", unit="%", hline=2, hline_label="연준 목표 2%",
        ))
    with r:
        show_chart(line_chart(
            {"10년 BEI": clip(bei), "5년 후 5년 기대인플레": clip(fwd)},
            "시장 기대인플레이션", unit="%", hline=2, hline_label="2%",
        ))

# ── 고용 ─────────────────────────────────────────────
with tabs[2]:
    nfp, ur = m.fred("PAYEMS", "diff"), m.fred("UNRATE")
    claims, claims4 = m.fred("ICSA") / 1000, m.fred("IC4WSA") / 1000
    jolts = m.fred("JTSJOL") / 1000
    c = st.columns(4)
    with c[0]:
        kpi("비농업 고용 증감", nfp, unit="K", decimals=0, freq="M", help="전월 대비 일자리 증감(천 명)")
    with c[1]:
        kpi("실업률", ur, unit="%", delta_unit="%p", decimals=1, freq="M")
    with c[2]:
        kpi("신규 실업수당 청구", claims, unit="K", decimals=0, help="주간, 천 건")
    with c[3]:
        kpi("JOLTS 구인건수", jolts, unit="M", decimals=2, freq="M")

    l, r = st.columns(2)
    with l:
        show_chart(bar_chart(clip(nfp), "비농업 고용 증감 (천 명)", unit="K"))
    with r:
        show_chart(line_chart({"실업률": clip(ur)}, "실업률", unit="%", decimals=1))
    l, r = st.columns(2)
    with l:
        show_chart(line_chart(
            {"신규 청구": clip(claims), "4주 평균": clip(claims4)},
            "신규 실업수당 청구 (천 건)", unit="K", decimals=0,
        ))
    with r:
        show_chart(line_chart({"구인건수": clip(jolts)}, "JOLTS 구인건수 (백만 건)", unit="M"))

# ── 성장·소비 ────────────────────────────────────────
with tabs[3]:
    gdp = m.fred("A191RL1Q225SBEA")
    retail_mom, retail_yoy = m.fred("RSAFS", "mom"), m.fred("RSAFS", "yoy")
    ip = m.fred("INDPRO", "yoy")
    houst = m.fred("HOUST") / 1000
    c = st.columns(4)
    with c[0]:
        kpi("실질 GDP (전기비 연율)", gdp, unit="%", delta_unit="%p", decimals=1, freq="Q")
    with c[1]:
        kpi("소매판매 (전월비)", retail_mom, unit="%", delta_unit="%p", freq="M")
    with c[2]:
        kpi("산업생산 (전년비)", ip, unit="%", delta_unit="%p", freq="M")
    with c[3]:
        kpi("주택착공 (연율)", houst, unit="M", decimals=2, freq="M", help="계절조정 연율, 백만 호")

    l, r = st.columns(2)
    with l:
        show_chart(bar_chart(clip(gdp), "실질 GDP 성장률 (전기비 연율)", unit="%", decimals=1))
    with r:
        show_chart(line_chart(
            {"소매판매": clip(retail_yoy), "산업생산": clip(ip)},
            "소매판매·산업생산 (전년비)", unit="%", hline=0,
        ))
    show_chart(line_chart({"주택착공": clip(houst)}, "주택착공 (연율, 백만 호)", unit="M", height=240))

# ── 금융 여건 ────────────────────────────────────────
with tabs[4]:
    hy, ig, nfci = m.fred("BAMLH0A0HYM2"), m.fred("BAMLC0A0CM"), m.fred("NFCI")
    vix, move, dxy = m.yahoo("^VIX"), m.yahoo("^MOVE"), m.yahoo("DX-Y.NYB")
    spread_kw = dict(unit="%p", delta_unit="bp", delta_scale=100, delta_decimals=0)
    c = st.columns(6)
    with c[0]:
        kpi("하이일드 스프레드", hy, **spread_kw)
    with c[1]:
        kpi("투자등급 스프레드", ig, **spread_kw)
    with c[2]:
        kpi("VIX", vix)
    with c[3]:
        kpi("MOVE", move, help="미 국채 변동성 지수")
    with c[4]:
        kpi("달러 인덱스", dxy)
    with c[5]:
        kpi("금융여건지수(NFCI)", nfci, decimals=3, help="0보다 크면 평균보다 긴축적")

    l, r = st.columns(2)
    with l:
        show_chart(line_chart({"하이일드": clip(hy), "투자등급": clip(ig)}, "회사채 신용 스프레드", unit="%p"))
    with r:
        show_chart(line_chart({"VIX": clip(vix)}, "VIX (주식 변동성)"))
    l, mid, r = st.columns(3)
    with l:
        show_chart(line_chart({"MOVE": clip(move)}, "MOVE (국채 변동성)", height=240))
    with mid:
        show_chart(line_chart({"DXY": clip(dxy)}, "달러 인덱스", height=240))
    with r:
        show_chart(line_chart({"NFCI": clip(nfci)}, "금융여건지수", hline=0, hline_label="평균",
                              decimals=3, height=240))
    st.caption("신용 스프레드·NFCI는 무료 실시간 소스가 없어 FRED(1일~1주 지연) 사용")

# ── 서프라이즈 ───────────────────────────────────────
with tabs[5]:
    c1, c2 = st.columns([1, 3], vertical_alignment="bottom")
    weeks = {"4주": 4, "8주": 8, "12주": 12}[c1.segmented_control("기간", ["4주", "8주", "12주"], default="8주",
                                                                key="sup_weeks") or "8주"]
    with st.spinner("발표치 불러오는 중... (처음엔 12주치 약 30초, 이후엔 새 날짜만)"):
        sup_all = get_surprises(12)
    cats = ["고용", "물가", "성장·소비", "심리·제조업", "주택"]
    picked = c2.pills("분야", cats, default=cats, selection_mode="multi", key="sup_cats") or cats
    if sup_all.empty:
        st.info("서프라이즈 데이터를 불러오지 못했습니다.")
    else:
        since = pd.Timestamp.now() - pd.DateOffset(weeks=weeks)
        sup = sup_all[(sup_all["발표(KST)"] >= since) & sup_all["분야"].isin(picked)]
        d = sup["_diff"]
        beat, miss, inline = (d > 0).sum(), (d < 0).sum(), (d == 0).sum()
        c = st.columns(4)
        c[0].metric("발표", f"{len(sup)}건", border=True)
        c[1].metric("예상 상회", f"{beat}건", f"{beat / max(len(sup), 1) * 100:.0f}%", delta_color="off",
                    delta_arrow="off", border=True)
        c[2].metric("예상 하회", f"{miss}건", f"{miss / max(len(sup), 1) * 100:.0f}%", delta_color="off",
                    delta_arrow="off", border=True)
        c[3].metric("부합", f"{inline}건", border=True)

        # 주간 서프라이즈 균형: (상회 − 하회) 건수. 지표 단위가 달라 크기 대신 방향만 센다
        weekly = (sup.assign(w=sup["발표(KST)"].dt.to_period("W-FRI").dt.start_time,
                             s=np.sign(sup["_diff"])).groupby("w")["s"].sum())
        show_chart(bar_chart(weekly, "주간 서프라이즈 균형 (상회 − 하회 건수)", height=220))

        def _sup_style(df: pd.DataFrame) -> pd.DataFrame:
            styles = pd.DataFrame("", index=df.index, columns=df.columns)
            for i, v in sup["_diff"].items():
                if v:
                    color = "#e66767" if v > 0 else "#3987e5"
                    styles.at[i, "서프라이즈"] = f"color: {color}; font-weight: 600"
            return styles

        table = sup[["발표(KST)", "분야", "지표", "실제", "예상", "이전", "서프라이즈"]]
        st.dataframe(table.style.apply(_sup_style, axis=None), hide_index=True, width="stretch",
                     height=min(38 + 35 * len(table), 600),
                     column_config={"발표(KST)": st.column_config.DatetimeColumn(format="MM/DD (ddd) HH:mm")})
    st.caption(
        "출처: Nasdaq 경제 캘린더 (실제·예상·이전 모두 같은 출처) · 상회 빨강 / 하회 파랑은 방향만 뜻하며 "
        "좋고 나쁨을 판단하지 않음 (예: 실업률·물가 상회는 보통 부정적) · 주간 균형은 지표 단위가 달라 건수로만 계산"
    )
