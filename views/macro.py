from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

from data import macro as m
import config
from data.econ_calendar import get_macro_releases, refresh_bucket
from data.surprise import get_surprises
from ui import (RELEASE_CSS, bar_chart, curve_chart, fedwatch_html, kpi, line_chart, regime_chart, release_board,
                render_release_insights, show_chart)

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


def _rel(group: str):
    """탭 발표 현황 (df, {지표명: 행}). 실패하면 (None, {}) — 카드는 배지 없이, 보드는 안내만."""
    with st.spinner("발표 일정·컨센서스 불러오는 중... (첫 로딩은 10초 정도)"):
        try:
            df = get_macro_releases(group, refresh_bucket())
        except Exception:
            return None, {}
    return df, {r["지표"]: r for r in df.to_dict("records")}


def _releases(group: str, note: str, df) -> None:
    """탭별 발표 현황 보드: 지표마다 전월비·전년비, 예상 대비, 발표 시각, 다음 발표 D-day."""
    st.markdown("**📋 발표 현황** · " + ("최신값 · 반영일 · 다음 발표일" if group == "금융 여건"
                                          else "전월비·전년비 · 컨센서스 대비 · 다음 발표일"))
    if df is None:
        st.warning("발표 현황을 불러오지 못했습니다 (FRED·Nasdaq 응답 없음). 잠시 뒤 새로고침하세요.")
        return
    # 전월비와 전년비의 기준 기간이 다르면 이유를 적는다 (예: 1년 전 값이 셧다운으로 미발표 → 공식 전년비 없음)
    base = df["지표"].str.replace(r" (전월비|전년비)$", "", regex=True)
    lagged = [b for b, g in df.groupby(base, sort=False) if g["기준"].nunique() > 1 and len(g) == 2]
    if lagged:
        note += (f" · {', '.join(lagged)}: 전년비가 전월비보다 기준 기간이 이름 — 1년 전 값이 없으면(예: 2025년 10월 CPI 셧다운 미발표) "
                 "그 달 공식 전년비가 발표되지 않음")
    release_board(df, note=note)


with st.spinner("FRED 지표 불러오는 중... (첫 로딩은 최대 30초)"):
    m.warmup()

def _regime() -> None:
    """맨 위 매크로 국면: 성장(CFNAI 3개월 평균) × 물가(근원 PCE 3개월 연율 − 2%) 사분면과 최근 12개월 궤적."""
    from data.macro_regime import QUADRANTS, quadrant, regime_history
    try:
        h = regime_history(12)
    except Exception as e:
        st.caption(f"국면 판단 데이터를 불러오지 못했습니다: {str(e)[:80]}")
        return
    g, i = h["성장"].iloc[-1], h["물가"].iloc[-1]
    name, desc = quadrant(g, i)
    edge = abs(g) < 0.1  # 성장 축이 0 근처면 한 달 사이에 국면 이름이 쉽게 뒤집힘
    with st.container(border=True):
        l, r = st.columns([3, 2], gap="large")
        with l:
            show_chart(regime_chart(h))
        with r:
            st.markdown(f"**🧭 매크로 국면 · {h.index[-1]:%Y년 %m월}**")
            st.markdown(f"### {name}" + (" (경계 근처)" if edge else ""))
            st.markdown(desc)
            prev = h.iloc[-4] if len(h) >= 4 else h.iloc[0]
            st.markdown(f"- 성장: 시카고 연은 활동지수 3개월 평균 **{g:+.2f}** (3개월 전 {prev['성장']:+.2f}, 0 = 추세 성장)\n"
                        f"- 물가: 근원 PCE 3개월 연율 **{i + 2:.1f}%** (목표 대비 {i:+.1f}%p, 3개월 전 {prev['물가'] + 2:.1f}%)\n"
                        f"- 최근 12개월: " + " · ".join(f"{k} {v}개월" for k, v in h["국면"].value_counts().items()))
            if edge:
                st.caption("성장 축이 0(추세 성장) 바로 옆이라 다음 달 지표에 따라 국면 이름이 바뀌기 쉬움")
            st.caption("규칙 기반 판단 (AI 아님) · 매달 갱신 (CFNAI는 약 3주 늦게, PCE는 다음 달 말 발표)")


st.html(RELEASE_CSS)  # 카드 배지·발표 현황 보드 공용 스타일
@st.fragment(run_every="60s")  # 1분마다 다시 그림: 발표 시간대엔 2분 캐시라 ⚡속보가 바로 반영, 그 밖엔 캐시 그대로라 가벼움
def _body() -> None:
    from data.release_insight import load as load_insights
    render_release_insights(load_insights())
    _regime()
    tabs = st.tabs(["🏛️ 통화정책", "🔥 물가", "👷 고용", "🏭 성장·소비", "🌡️ 금융 여건", "🎯 서프라이즈"])

    # ── 통화정책 (수익률 곡선·장단기 금리차는 채권 화면) ───────────────
    with tabs[0]:
        from data.macro_regime import fomc_meetings, next_fomc
        upper, lower, y2 = m.fred("DFEDTARU"), m.fred("DFEDTARL"), m.yields("2Y")
        mid = (upper + lower) / 2
        y2d = y2.groupby(y2.index.normalize()).last()
        gap = ((y2d - mid.reindex(y2d.index, method="ffill")) * 100).dropna()
        today = datetime.now().date()
        nxt = next_fomc(today)
        c = st.columns(4)
        with c[0]:
            st.metric("기준금리 (목표 범위)", f"{lower.iloc[-1]:.2f}~{upper.iloc[-1]:.2f}%",
                      f"직전 변경 {upper[upper.diff() != 0].index[-1]:%Y-%m-%d}", delta_color="off", delta_arrow="off",
                      border=True, help="연방기금금리 목표 범위 (FRED DFEDTARL·DFEDTARU)")
        from data.fedwatch import fedwatch
        fw, fw_err = [], ""
        try:
            fw = fedwatch(today)
        except Exception as e:
            fw_err = str(e)
        with c[1]:
            if nxt:
                d = (nxt - today).days
                first = fw[0] if fw and fw[0]["날짜"] == nxt else None
                prob = (f" · 인상 {first['인상']:.0%}" if first and first["인상"] >= max(first["동결"], first["인하"])
                        else f" · 인하 {first['인하']:.0%}" if first and first["인하"] > first["동결"]
                        else f" · 동결 {first['동결']:.0%}" if first else "")
                st.metric("다음 FOMC 결정", f"{nxt:%m/%d}({'월화수목금토일'[nxt.weekday()]})",
                          ("오늘" if d == 0 else f"D-{d}") + prob, delta_color="off", delta_arrow="off", border=True,
                          help="연준 공식 일정 · 결정은 회의 마지막 날 미 동부 14:00 (한국 다음 날 새벽 3~4시) · "
                               "확률은 아래 FedWatch 방식 계산")
            else:
                st.metric("다음 FOMC 결정", "-", border=True)
        with c[2]:
            g = gap.iloc[-1]
            cuts = -g / 25
            st.metric("2년물 − 기준금리 중간값 (장기 참고)", f"{g:+.0f}bp",
                      f"인하 약 {cuts:.1f}회 반영" if g < 0 else f"인상 약 {-cuts:.1f}회 반영", delta_color="off",
                      delta_arrow="off", border=True,
                      help="2년물이 기준금리보다 낮으면 시장이 앞으로 2년간 인하를 기대한다는 뜻 (25bp = 1회로 단순 환산, "
                           "기간 프리미엄이 섞인 대리 지표 · 정확한 확률은 CME FedWatch)")
        with c[3]:
            kpi("2년물", y2, unit="%", delta_unit="bp", delta_scale=100, delta_decimals=0)
        st.markdown("**📊 FOMC 회의별 금리 변경 확률** · CME FedWatch 방식 (30일 연방기금금리 선물로 직접 계산)")
        if fw:
            st.html(fedwatch_html(fw, today, upper.iloc[-1]))
            st.caption("시장 금리 = 100 − 월물 선물 가격(Yahoo, 지연) · 회의 전후 금리를 그 달 평균에서 풀고, 변화를 25bp 단위 확률로 "
                       "나눔 · CME 내부 데이터와 소수점 차이가 날 수 있고, 먼 월물은 거래가 적어 덜 정확 · 누적 = 이번 달 시장 금리 대비")
        else:
            st.caption(f"연방기금금리 선물을 불러오지 못했습니다: {fw_err[:80] or '데이터 없음'}")
        l, r = st.columns(2)
        with l:
            show_chart(line_chart({"기준금리 상단": clip(upper), "기준금리 하단": clip(lower), "2년물": clip(y2)},
                                  "기준금리와 2년물", unit="%"))
        with r:
            show_chart(line_chart({"2년물 − 기준금리": clip(gap)}, "시장이 반영한 정책 경로 (2년물 − 기준금리 중간값)",
                                  unit="bp", hline=0, hline_label="0 아래 = 인하 기대", decimals=0))
        meetings = [d for d in fomc_meetings() if d >= today][:8]
        if meetings:
            st.caption("**FOMC 일정 (결정일)** · " + " · ".join(f"{d:%y/%m/%d}" for d in meetings)
                       + " · 출처: federalreserve.gov · 수익률 곡선·장단기 금리차는 채권 화면")

    # ── 물가 ─────────────────────────────────────────────
    with tabs[1]:
        rel_df, rel = _rel("물가")
        cpi, core, pce = m.fred("CPIAUCNS", "yoy"), m.fred("CPILFENS", "yoy"), m.fred("PCEPILFE", "yoy")
        bei, fwd = m.breakeven_10y(), m.fred("T5YIFR")
        pct_kw = dict(unit="%", delta_unit="%p", freq="M")
        c = st.columns(4)
        with c[0]:
            kpi("CPI (전년비)", cpi, **pct_kw, release=rel.get("CPI 전년비"))
        with c[1]:
            kpi("근원 CPI (전년비)", core, **pct_kw, release=rel.get("근원 CPI 전년비"))
        with c[2]:
            kpi("근원 PCE (전년비)", pce, **pct_kw, help="연준이 목표로 삼는 물가 지표", release=rel.get("근원 PCE 전년비"))
        with c[3]:
            kpi("10년 기대인플레(BEI)", bei, unit="%", delta_unit="bp", delta_scale=100, delta_decimals=0,
                help="명목 10년물 − 물가연동채(TIPS) 10년물")
        _releases("물가", "전월비는 계절조정 지수, CPI 전년비는 BLS 공식과 같은 원계열 기준", rel_df)

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
        l, r = st.columns(2)
        with l:  # 연준이 실제로 보는 모멘텀: 최근 3·6개월 연율 (전년비는 1년 전 기저효과가 섞여 느림)
            show_chart(line_chart(
                {"근원 PCE 3개월 연율": clip(m.fred("PCEPILFE", "ann3")), "근원 PCE 6개월 연율": clip(m.fred("PCEPILFE", "ann6")),
                 "근원 CPI 3개월 연율": clip(m.fred("CPILFESL", "ann3"))},
                "물가 모멘텀 (최근 3·6개월 연율)", unit="%", hline=2, hline_label="연준 목표 2%"))
        with r:  # 어디서 끈적한가: 근원 상품 · 주거 · 주거 제외 서비스
            show_chart(line_chart(
                {"근원 상품": clip(m.fred("CUSR0000SACL1E", "yoy")), "주거": clip(m.fred("CUSR0000SAH1", "yoy")),
                 "주거 제외 서비스": clip(m.fred("CUSR0000SASL2RS", "yoy"))},
                "근원 CPI 구성 (전년비)", unit="%", hline=0))
        st.caption("3개월 연율 = 최근 3개월 상승률을 1년치로 환산 (연준이 '최근 추세'로 보는 숫자) · 주거 제외 서비스 = "
                   "CPI 서비스 중 주거비(임대료) 제외, 흔히 '슈퍼코어'에 가까운 개념")

    # ── 고용 ─────────────────────────────────────────────
    with tabs[2]:
        rel_df, rel = _rel("고용")
        nfp, ur = m.fred("PAYEMS", "diff"), m.fred("UNRATE")
        claims, claims4 = m.fred("ICSA") / 1000, m.fred("IC4WSA") / 1000
        jolts = m.fred("JTSJOL") / 1000
        c = st.columns(4)
        with c[0]:
            kpi("비농업 고용 증감", nfp, unit="K", decimals=0, freq="M", help="전월 대비 일자리 증감(천 명)",
                release=rel.get("비농업 고용 증감"))
        with c[1]:
            kpi("실업률", ur, unit="%", delta_unit="%p", decimals=1, freq="M", release=rel.get("실업률"))
        with c[2]:
            kpi("신규 실업수당 청구", claims, unit="K", decimals=0, help="주간, 천 건", release=rel.get("신규 실업수당 청구"))
        with c[3]:
            kpi("JOLTS 구인건수", jolts, unit="M", decimals=2, freq="M", release=rel.get("JOLTS 구인건수"))
        _releases("고용", "신규·계속 실업수당 청구는 주간(기준 = 해당 주 마지막 날) · 시간당 임금은 민간 전체", rel_df)

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
        l, r = st.columns(2)
        with l:
            sahm = m.fred("SAHMREALTIME")
            show_chart(line_chart({"삼 법칙 지표": clip(sahm)}, f"삼 법칙 (경기침체 신호, 최근 {sahm.iloc[-1]:.2f})",
                                  unit="%p", hline=0.5, hline_label="0.5 이상 = 침체 신호", decimals=2))
        with r:
            show_chart(line_chart({"자발적 이직률": clip(m.fred("JTSQUR"))}, "JOLTS 자발적 이직률 (노동시장 열기)",
                                  unit="%", decimals=1))
        st.caption("삼 법칙 = 실업률 3개월 평균이 직전 12개월 최저보다 몇 %p 올랐나 (0.5 이상이면 과거 침체와 거의 겹침) · "
                   "자발적 이직률이 높을수록 일자리를 쉽게 옮긴다는 뜻 = 임금 상승 압력")

    # ── 성장·소비 ────────────────────────────────────────
    with tabs[3]:
        rel_df, rel = _rel("성장·소비")
        gdp = m.fred("A191RL1Q225SBEA")
        retail_mom, retail_yoy = m.fred("RSAFS", "mom"), m.fred("RSAFS", "yoy")
        ip = m.fred("INDPRO", "yoy")
        houst = m.fred("HOUST") / 1000
        c = st.columns(4)
        with c[0]:
            kpi("실질 GDP (전기비 연율)", gdp, unit="%", delta_unit="%p", decimals=1, freq="Q",
                release=rel.get("실질 GDP (전기비 연율)"))
        with c[1]:
            kpi("소매판매 (전월비)", retail_mom, unit="%", delta_unit="%p", freq="M", release=rel.get("소매판매 전월비"))
        with c[2]:
            kpi("산업생산 (전년비)", ip, unit="%", delta_unit="%p", freq="M", release=rel.get("산업생산 전년비"))
        with c[3]:
            kpi("주택착공 (연율)", houst, unit="M", decimals=2, freq="M", help="계절조정 연율, 백만 호",
                release=rel.get("주택착공 (연율)"))
        _releases("성장·소비", "GDP는 속보→잠정→확정으로 수정되며 발표는 가장 최근 수정일 · 주택착공·건축허가는 계절조정 연율", rel_df)

        l, r = st.columns(2)
        with l:
            show_chart(bar_chart(clip(gdp), "실질 GDP 성장률 (전기비 연율)", unit="%", decimals=1))
        with r:
            show_chart(line_chart(
                {"소매판매": clip(retail_yoy), "산업생산": clip(ip)},
                "소매판매·산업생산 (전년비)", unit="%", hline=0,
            ))
        l, r = st.columns(2)
        with l:
            now_q = m.fred("GDPNOW")
            q = now_q.index[-1]
            st.metric(f"GDPNow · {q.year}년 {q.quarter}분기 성장률 추정", f"{now_q.iloc[-1]:.1f}%",
                      f"직전 분기 확정치 {gdp.iloc[-1]:.1f}%", delta_color="off", delta_arrow="off", border=True,
                      help="애틀랜타 연은 GDPNow: 분기 GDP 발표 전, 나온 지표로 이번 분기 성장률(전기비 연율)을 실시간 추정 · FRED")
            show_chart(line_chart({"주택착공": clip(houst)}, "주택착공 (연율, 백만 호)", unit="M", height=220))
        with r:
            show_chart(line_chart({"CFNAI 3개월 평균": clip(m.fred("CFNAIMA3"))}, "시카고 연은 활동지수 (0 = 추세 성장)",
                                  hline=0, hline_label="−0.7 아래 = 침체 위험", decimals=2, height=330))

    # ── 금융 여건 ────────────────────────────────────────
    with tabs[4]:
        rel_df, rel = _rel("금융 여건")
        hy, ig, nfci = m.fred("BAMLH0A0HYM2"), m.fred("BAMLC0A0CM"), m.fred("NFCI")
        vix, move, dxy = m.yahoo("^VIX"), m.yahoo("^MOVE"), m.yahoo("DX-Y.NYB")
        spread_kw = dict(unit="%p", delta_unit="bp", delta_scale=100, delta_decimals=0)
        c = st.columns(6)
        with c[0]:
            kpi("하이일드 스프레드", hy, **spread_kw, release=rel.get("하이일드 스프레드"))
        with c[1]:
            kpi("투자등급 스프레드", ig, **spread_kw, release=rel.get("투자등급 스프레드"))
        with c[2]:
            kpi("VIX", vix)
        with c[3]:
            kpi("MOVE", move, help="미 국채 변동성 지수")
        with c[4]:
            kpi("달러 인덱스", dxy)
        with c[5]:
            kpi("금융여건지수(NFCI)", nfci, decimals=3, help="0보다 크면 평균보다 긴축적", release=rel.get("금융여건지수 NFCI"))
        _releases("금융 여건", "시장·주간 지표라 컨센서스가 없음 · 기준 = 데이터 날짜, 발표 = FRED 반영일, 이전 = 직전 관측값", rel_df)

        l, r = st.columns(2)
        with l:
            show_chart(line_chart({"하이일드": clip(hy), "투자등급": clip(ig)}, "회사채 신용 스프레드", unit="%p"))
        with r:
            show_chart(line_chart({"VIX": clip(vix)}, "VIX (주식 변동성)"))
        st.caption("신용 스프레드·NFCI는 무료 실시간 소스가 없어 FRED(1일\\~1주 지연) 사용")

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
            l, r = st.columns(2)
            with l:
                show_chart(bar_chart(weekly, "주간 서프라이즈 균형 (상회 − 하회 건수)", height=240))
            with r:  # 성장·물가 따로 누적: '성장은 예상보다 강하고 물가도 예상보다 높다' 같은 흐름
                from data.macro_regime import surprise_index
                idx = surprise_index(sup)
                if not idx.empty:
                    show_chart(line_chart({c: idx[c] for c in ("성장", "물가") if c in idx},
                                          "누적 서프라이즈 (상회 +1 · 하회 −1)", hline=0, decimals=0, height=240))
            st.caption("누적 서프라이즈: 성장 = 고용·주택·성장·소비·심리·제조업 지표, 물가 = 물가 지표 · 선이 오르면 "
                       "예상보다 높게 나오는 발표가 이어진다는 뜻 (씨티 서프라이즈 지수의 단순판)")

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


_body()
