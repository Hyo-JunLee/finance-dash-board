from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from data import bonds as b
from data.bond_attribution import attribute, curve_check, summary_text, tp_model_check
from data.fred import get_fred_series
from data.global_markets import get_closes
from data.krx import REAL_10Y, kts_yields
from data.macro import yahoo
from ui import curve_chart, kpi
from data.rates import NOMINAL, get_treasury_curve
from ui import SERIES_COLORS, _base_layout, line_chart, returns_bar, show_chart

st.title("채권")
st.caption(f"조회 시각 {datetime.now():%Y-%m-%d %H:%M} · 미국: 재무부·뉴욕 연은·FRED·FiscalData · 한국: KRX "
           "· 금리 변화 단위 bp(0.01%p)")

CHANGE_WINDOWS = {"1주": pd.DateOffset(weeks=1), "1개월": pd.DateOffset(months=1), "3개월": pd.DateOffset(months=3),
                  "YTD": "YTD", "1년": pd.DateOffset(years=1)}
CHART_YEARS = {"1년": 1, "3년": 3, "5년": 5, "10년": 10}


def _since(last: pd.Timestamp, window) -> pd.Timestamp:
    return pd.Timestamp(year=last.year, month=1, day=1) - pd.Timedelta(days=1) if window == "YTD" else last - window


def _clip(df, years: int):
    return df[df.index >= df.index[-1] - pd.DateOffset(years=years)]


tabs = st.tabs(["🧩 곡선 분해", "↕️ 곡선 움직임", "🏦 국채 입찰", "💧 연준 유동성", "🇰🇷 한국 국채"])

# ── 1. 곡선 분해 ─────────────────────────────────────
with tabs[0]:
    with st.spinner("수익률 곡선·ACM 모델 불러오는 중... (ACM 엑셀은 12시간마다 갱신)"):
        rb = b.real_bei_10y()
        try:
            acm = b.acm_10y()
        except Exception as e:
            acm = pd.DataFrame()
            st.warning(f"ACM 텀 프리미엄을 불러오지 못했습니다: {e}")

    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    c1.markdown("##### 10년물 금리는 왜 움직였나")
    window = c2.segmented_control("변화 기간", list(CHANGE_WINDOWS), default="1개월", key="dec_win") or "1개월"

    rb_chg = b.change_bp(rb, _since(rb.index[-1], CHANGE_WINDOWS[window]))
    left, right = st.columns(2, gap="large")
    with left:
        show_chart(returns_bar(rb_chg.rename({"명목 10년": "명목 10년 (=실질+BEI)"}),
                               f"① 실질금리 vs 기대인플레 ({window}, ~{rb.index[-1]:%m/%d})",
                               height=220, unit="bp", sort=False, decimals=0))
        st.caption(b.explain_change(rb_chg["명목 10년"],
                                    {"실질금리": rb_chg["실질 10년"], "기대인플레": rb_chg["기대인플레(BEI)"]}))
    with right:
        if not acm.empty:
            acm_chg = b.change_bp(acm, _since(acm.index[-1], CHANGE_WINDOWS[window]))
            show_chart(returns_bar(acm_chg.rename({"ACM 10년": "ACM 10년 (=경로+TP)"}),
                                   f"② 기대 금리경로 vs 텀 프리미엄 ({window}, ~{acm.index[-1]:%m/%d})",
                                   height=220, unit="bp", sort=False, decimals=0))
            st.caption(b.explain_change(acm_chg["ACM 10년"],
                                        {"기대 금리경로": acm_chg["기대 단기금리 경로"], "텀 프리미엄": acm_chg["텀 프리미엄"]})
                       + " (ACM은 모델 추정치로 뉴욕 연은이 며칠 늦게 공개)")

    # ── 종합 판정: 패널 ①×② 교차 (공통 마지막 날짜 기준) ──
    st.markdown("##### 종합 판정 (패널 ①×② 교차 검증)")
    if acm.empty:
        st.info("ACM 데이터가 없어 종합 판정을 계산할 수 없습니다.")
    else:
        with st.expander("판정 설정"):
            s1, s2 = st.columns(2)
            noise_bp = s1.number_input("노이즈 임계값 (bp)", 0.0, 20.0, config.ATTR_NOISE_BP, 0.5,
                                       help="성분 변화의 절댓값이 이보다 작으면 '유의미한 변화 아님'으로 보고 판정에서 제외")
            gap_bp = s2.number_input("모형 괴리 경고 (bp)", 0.0, 50.0, config.ATTR_TP_MODEL_GAP_BP, 0.5,
                                     help="ACM과 Kim-Wright 텀프리미엄 변화 차이가 이보다 크면 해석 신뢰도 경고")

        common_end = min(rb.index[-1], acm.index[-1])
        since_c = _since(common_end, CHANGE_WINDOWS[window])
        rb_c = b.change_bp(rb[rb.index <= common_end], since_c)
        acm_c = b.change_bp(acm[acm.index <= common_end], since_c)
        attr = attribute(rb_c["실질 10년"], rb_c["기대인플레(BEI)"], acm_c["기대 단기금리 경로"], acm_c["텀 프리미엄"],
                         noise_bp=noise_bp)

        # Kim-Wright: 공통 기간 끝까지 데이터가 있을 때만 비교
        try:
            kw = get_fred_series("THREEFYTP10")
            kw_end = kw.index[-1]
            d_kw = (kw[kw.index <= common_end].iloc[-1] - kw[kw.index <= since_c].iloc[-1]) * 100 \
                if kw_end >= common_end else None
        except Exception:
            kw_end, d_kw = None, None

        # 모형 없는 보조 검증: 같은 공통 기간의 2·10·30년 변화
        curve = get_treasury_curve((since_c - pd.Timedelta(days=10)).date(), NOMINAL)
        curve_c = b.change_bp(curve[curve.index <= common_end][["2 Yr", "10 Yr", "30 Yr"]], since_c)

        left_c, right_c = st.columns([3, 2], gap="large")
        with left_c, st.container(border=True):
            st.markdown(summary_text(attr, f"{common_end:%m/%d}", f"{rb.index[-1]:%m/%d}", f"{acm.index[-1]:%m/%d}",
                                     noise_bp))
        with right_c:
            text, _ = curve_check(curve_c["2 Yr"], curve_c["10 Yr"], curve_c["30 Yr"], noise_bp)
            st.markdown(f"**보조 검증 (모형 없음)**  \n{text}")
            kw_text, _ = tp_model_check(acm_c["텀 프리미엄"], d_kw, gap_bp)
            kw_note = f" (Kim-Wright 데이터 ~{kw_end:%m/%d})" if kw_end is not None else ""
            st.markdown(f"**텀프리미엄 모형 비교**  \n{kw_text}{kw_note}")
            st.metric("데이터 정합성 체크", f"{attr.consistency_gap:+.1f}bp", border=True,
                      help="ACM 텀프리미엄 변화 − 내재 텀프리미엄(C+D) 변화. 수식상 'ACM 10년 변화 − 재무부 10년 변화'와 같아, "
                           "가정 검증이 아니라 ACM 무이표채(zero-coupon) 금리와 재무부 이표채(par) 금리의 측정 차이를 보여줍니다.")

    years = CHART_YEARS[st.segmented_control("차트 기간", list(CHART_YEARS), default="3년", key="dec_years") or "3년"]
    l, r = st.columns(2, gap="large")
    with l:
        d = _clip(rb, years)
        show_chart(line_chart({c: d[c] for c in d.columns}, "명목 = 실질 + 기대인플레 (10년)", unit="%"))
    with r:
        if not acm.empty:
            d = _clip(acm, years)
            show_chart(line_chart({c: d[c] for c in d.columns}, "ACM 분해 (10년)", unit="%", hline=0))
    with st.expander("개념 정리"):
        st.markdown(
            "- **실질금리(TIPS)**: 물가연동국채 금리. 인플레를 뺀 '진짜' 자금 비용으로, 성장 기대와 통화정책에 민감\n"
            "- **기대인플레(BEI)**: 명목 − 실질. 채권시장이 가격에 반영한 향후 10년 평균 인플레\n"
            "- **기대 단기금리 경로**: 앞으로 10년간 기준금리(단기금리)의 평균 예상치. 연준 정책 경로 기대\n"
            "- **텀 프리미엄**: 단기채를 굴리는 대신 장기채를 들고 있는 위험(금리·인플레 불확실성, 수급)에 대한 추가 보상"
        )

# ── 2. 곡선 움직임 ───────────────────────────────────
with tabs[1]:
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    c1.markdown("##### 2년·10년 금리 변화로 본 곡선 국면")
    freq_label = c2.segmented_control("단위", ["일간", "주간", "월간"], default="일간", key="mv_freq") or "일간"
    freq = {"일간": "D", "주간": "W", "월간": "M"}[freq_label]
    moves = b.curve_moves(freq)

    cards = st.columns(3)
    for col, (label, f) in zip(cards, [("전 거래일 대비", "D"), ("이번 주 (지난주 금요일 대비)", "W"), ("이번 달 (전월 말 대비)", "M")]):
        last = b.curve_moves(f).iloc[-1]
        col.metric(label, last["국면"],
                   f"2년 {last['2년 변화(bp)']:+.0f}bp · 10년 {last['10년 변화(bp)']:+.0f}bp",
                   delta_color="off", delta_arrow="off", border=True,
                   help=b.REGIMES[last["국면"]])

    lookback = {"D": 120, "W": 104, "M": 36}[freq]
    recent = moves.tail(lookback)
    left, right = st.columns([3, 2], gap="large")
    with left:
        fig = go.Figure()
        # 색 = 방향(불/베어), 모양 = 기울기(스티프닝/플래트닝) → 4개 국면을 색 2개로 구분
        for regime in ["불 스티프닝", "불 플래트닝", "베어 스티프닝", "베어 플래트닝", "변화 미미"]:
            pts = recent[recent["국면"] == regime]
            if pts.empty:
                continue
            color = SERIES_COLORS[0] if regime.startswith("불") else SERIES_COLORS[1] if regime.startswith("베어") else "#888"
            symbol = "triangle-up" if "스티프닝" in regime else "circle" if "플래트닝" in regime else "x"
            fig.add_scatter(x=pts["2년 변화(bp)"], y=pts["10년 변화(bp)"], mode="markers", name=regime,
                            marker=dict(color=color, symbol=symbol, size=9, line=dict(width=1, color="#0e1117")),
                            text=pts.index.strftime("%Y-%m-%d"),
                            hovertemplate="%{text}<br>2년 %{x:+.0f}bp · 10년 %{y:+.0f}bp<extra>" + regime + "</extra>")
        last = recent.iloc[-1]
        fig.add_scatter(x=[last["2년 변화(bp)"]], y=[last["10년 변화(bp)"]], mode="markers", name="최근",
                        marker=dict(size=18, color="rgba(0,0,0,0)", line=dict(width=2, color="white")),
                        hoverinfo="skip")
        lim = max(recent[["2년 변화(bp)", "10년 변화(bp)"]].abs().max().max() * 1.1, 5)
        fig.add_shape(type="line", x0=-lim, y0=-lim, x1=lim, y1=lim, line=dict(color="rgba(255,255,255,0.35)", dash="dash"))
        fig.add_annotation(x=lim * 0.75, y=lim * 0.95, text="대각선 위 = 스티프닝", showarrow=False,
                           font=dict(size=11, color="rgba(255,255,255,0.55)"))
        fig = _base_layout(fig, f"{freq_label} 변화 분포 (최근 {len(recent)}개, 흰 원 = 최근)", "", 420, legend=True)
        fig.update_xaxes(title="2년 변화 (bp)", range=[-lim, lim], zeroline=True, zerolinecolor="rgba(255,255,255,0.3)",
                         showgrid=True, gridcolor="rgba(255,255,255,0.08)")
        fig.update_yaxes(title="10년 변화 (bp)", range=[-lim, lim], zeroline=True, zerolinecolor="rgba(255,255,255,0.3)")
        fig.update_layout(hovermode="closest")
        show_chart(fig)
    with right:
        stats = recent.groupby("국면").agg(횟수=("국면", "size"), 평균_2년=("2년 변화(bp)", "mean"),
                                         평균_10년=("10년 변화(bp)", "mean"))
        stats["비중"] = stats["횟수"] / stats["횟수"].sum() * 100
        stats = stats.reindex([r for r in b.REGIMES if r in stats.index])
        st.dataframe(stats.style.format({"평균_2년": "{:+.1f}bp", "평균_10년": "{:+.1f}bp", "비중": "{:.0f}%"}),
                     width="stretch")
        for k, v in b.REGIMES.items():
            st.caption(f"**{k}**: {v}")
    table = recent.iloc[::-1].head(15)
    table.index = table.index.strftime("%Y-%m-%d")
    st.dataframe(table.rename_axis("날짜").style.format({c: "{:+.0f}" for c in recent.columns[:3]}), width="stretch")

# ── 3. 국채 입찰 ─────────────────────────────────────
with tabs[2]:
    with st.spinner("입찰 결과 불러오는 중..."):
        auc = b.auctions()
    upcoming = auc[auc["평가"] == "예정"].sort_values("입찰일")
    done = auc[auc["평가"] != "예정"]

    if len(upcoming):
        st.markdown("##### 예정된 입찰")
        st.dataframe(upcoming[["입찰일", "만기", "종목", "재개행", "발행액($bn)"]].style.format(
            {"입찰일": "{:%m/%d (%a)}", "발행액($bn)": "{:,.0f}"}), hide_index=True, width="stretch")

    st.markdown("##### 최근 입찰 결과 (명목 고정금리 Note·Bond)")
    show = done.head(20)[["입찰일", "만기", "재개행", "발행액($bn)", "낙찰금리(%)", "응찰률", "응찰률 6회평균",
                          "해외(%)", "딜러(%)", "딜러(%) 6회평균", "근사 테일(bp)", "평가"]]

    def _grade_style(df: pd.DataFrame) -> pd.DataFrame:
        styles = pd.DataFrame("", index=df.index, columns=df.columns)
        styles.loc[df["평가"].str.contains("강함"), "평가"] = "color: #199e70; font-weight: 600"
        styles.loc[df["평가"].str.contains("약함"), "평가"] = "color: #d95926; font-weight: 600"
        return styles

    st.dataframe(
        show.style.apply(_grade_style, axis=None).format({
            "입찰일": "{:%Y-%m-%d}", "발행액($bn)": "{:,.0f}", "낙찰금리(%)": "{:.3f}", "응찰률": "{:.2f}",
            "응찰률 6회평균": "{:.2f}", "해외(%)": "{:.1f}", "딜러(%)": "{:.1f}", "딜러(%) 6회평균": "{:.1f}",
            "근사 테일(bp)": "{:+.1f}",
        }, na_rep="-"),
        hide_index=True, width="stretch",
    )
    st.caption(
        "평가: 같은 만기 직전 6회 평균 대비 응찰률↑·딜러 비중↓ = 💪 강함, 응찰률↓·딜러 비중↑ = ⚠️ 약함 (딜러는 남은 물량을 떠안는 역할이라 "
        "비중이 높을수록 실수요가 약했다는 뜻). 해외(%) = 해외 중앙은행·기관 등 간접 응찰. "
        "근사 테일 = 낙찰금리 − 입찰 전 영업일 재무부 종가. 실제 테일은 입찰 직전 WI 금리(유료) 기준이라 "
        "근사치는 그사이 시장 변동이 섞여 참고용입니다."
    )

    terms = [t for t in ["2-Year", "3-Year", "5-Year", "7-Year", "10-Year", "20-Year", "30-Year"] if t in set(done["만기"])]
    term = st.segmented_control("만기별 추이", terms, default="10-Year" if "10-Year" in terms else terms[0], key="auc_term")
    hist = done[done["만기"] == term].sort_values("입찰일").set_index("입찰일")
    l, r = st.columns(2, gap="large")
    with l:
        show_chart(line_chart({"응찰률": hist["응찰률"], "6회 평균": hist["응찰률 6회평균"]},
                              f"{term} 응찰률 (bid-to-cover)", decimals=2))
    with r:
        show_chart(line_chart({"해외(%)": hist["해외(%)"], "딜러(%)": hist["딜러(%)"]},
                              f"{term} 낙찰 비중", unit="%", decimals=1))

# ── 4. 연준 유동성 ───────────────────────────────────
with tabs[3]:
    liq = b.fed_liquidity()
    last, prev = liq.iloc[-1], liq.iloc[-2]
    cols = st.columns(5)
    for col, name, hint in zip(cols, ["순유동성", "연준 자산", "재무부 계좌(TGA)", "역레포(RRP)", "지급준비금"],
                               ["연준 자산 − TGA − 역레포", "QT(양적긴축) 중이면 감소", "늘면 시중 유동성 흡수",
                                "늘면 시중 유동성 흡수", "은행 시스템 유동성"]):
        col.metric(name, f"${last[name]:,.0f}bn", f"{last[name] - prev[name]:+,.0f}bn (전주비)",
                   delta_color="off", border=True, help=hint)
    st.caption(f"기준: {liq.index[-1]:%Y-%m-%d} (수요일, 연준 H.4.1 주간 발표) · 단위 십억 달러")

    years = CHART_YEARS[st.segmented_control("차트 기간", list(CHART_YEARS)[:3], default="3년", key="liq_years") or "3년"]
    d = _clip(liq, years)
    l, r = st.columns(2, gap="large")
    with l:
        show_chart(line_chart({"순유동성": d["순유동성"], "지급준비금": d["지급준비금"]}, "순유동성과 지급준비금 ($bn)",
                              decimals=0))
    with r:
        spx = get_closes(("^GSPC",), "5y")["^GSPC"]
        spx_w = spx.reindex(spx.index.union(d.index)).ffill().reindex(d.index)
        show_chart(line_chart({"순유동성": d["순유동성"] / d["순유동성"].iloc[0] * 100,
                               "S&P 500": spx_w / spx_w.iloc[0] * 100},
                              "순유동성 vs S&P 500 (시작 = 100)", hline=100, decimals=1))
    c = st.columns(3)
    for col, name in zip(c, ["연준 자산", "재무부 계좌(TGA)", "역레포(RRP)"]):
        with col:
            show_chart(line_chart({name: d[name]}, f"{name} ($bn)", decimals=0, height=220))
    st.caption("순유동성은 시장에서 널리 쓰는 근사 지표입니다. 위험자산과의 동행은 기간에 따라 달라 인과관계로 해석하면 안 됩니다.")

# ── 5. 한국 국채 (KRX 국채전문유통시장) ─────────────────
with tabs[4]:
    import config as _cfg

    if not _cfg.KRX_API_KEY:
        st.info("KRX_API_KEY가 없어 한국 국채 데이터를 불러올 수 없습니다 (.env 확인).")
        st.stop()
    kr_years = {"1년": 1, "3년": 3}[st.segmented_control("기간", ["1년", "3년"], default="1년", key="kr_years") or "1년"]
    with st.spinner("국고채 금리 불러오는 중... (처음엔 1년치 약 10초, 3년치 약 30초. 이후엔 새 날짜만 받음)"):
        kr = kts_yields(kr_years)
    if kr.empty:
        st.warning("국고채 데이터를 불러오지 못했습니다.")
        st.stop()

    kr["BEI 10Y"] = kr["10Y"] - kr[REAL_10Y]
    us = get_treasury_curve(kr.index[0].date(), NOMINAL)[["2 Yr", "10 Yr"]]
    both = kr[["2Y", "10Y"]].join(us, how="inner")  # 같은 날짜끼리 (한국 15:30 종가 vs 미국 그날 종가)
    spread = pd.DataFrame({"2년 (미−한)": (both["2 Yr"] - both["2Y"]) * 100,
                           "10년 (미−한)": (both["10 Yr"] - both["10Y"]) * 100})

    rate_kw = dict(unit="%", delta_unit="bp", delta_scale=100, delta_decimals=0)
    c = st.columns(5)
    with c[0]:
        kpi("국고 3년", kr["3Y"], **rate_kw)
    with c[1]:
        kpi("국고 10년", kr["10Y"], **rate_kw)
    with c[2]:
        kpi("10년 기대인플레(BEI)", kr["BEI 10Y"].dropna(), **rate_kw,
            help="국고 10년 − 물가연동국채 10년. 물가채는 거래가 적어 거래 없는 날은 계산하지 않음")
    with c[3]:
        kpi("한미 금리차 2년", spread["2년 (미−한)"], unit="bp", decimals=0, help="미국 − 한국. 양수면 미국 금리가 높음")
    with c[4]:
        kpi("한미 금리차 10년", spread["10년 (미−한)"], unit="bp", decimals=0, help="미국 − 한국")
    st.caption(f"출처: KRX 국채전문유통시장 지표물 종가 (다음 영업일 공개, 현재 ~{kr.index[-1]:%Y-%m-%d}) · "
               "미국은 재무부 같은 날짜 종가")

    # 수익률 곡선: 최근 / 1개월 전 / 1년 전 (1년 전은 데이터가 있을 때만)
    last = kr.index[-1]
    picks = {"최근": last, "1개월 전": last - pd.DateOffset(months=1), "1년 전": last - pd.DateOffset(years=1)}
    curves = {}
    for label, target in picks.items():
        rows = kr[kr.index <= target]
        if len(rows) and (target - rows.index[-1]).days <= 7:
            curves[f"{label} ({rows.index[-1]:%y/%m/%d})"] = rows.iloc[-1][["2Y", "3Y", "5Y", "10Y", "20Y", "30Y"]].values
    curve_df = pd.DataFrame(curves, index=["2Y", "3Y", "5Y", "10Y", "20Y", "30Y"])

    l, r = st.columns(2, gap="large")
    with l:
        show_chart(curve_chart(curve_df, "국고채 수익률 곡선"))
    with r:
        show_chart(line_chart({"국고 3년": kr["3Y"], "국고 10년": kr["10Y"], "국고 30년": kr["30Y"]},
                              "국고채 금리", unit="%"))
    l, r = st.columns(2, gap="large")
    with l:
        show_chart(line_chart({"명목 10년": kr["10Y"], "물가채 10년": kr[REAL_10Y].dropna(), "BEI 10년": kr["BEI 10Y"].dropna()},
                              "명목 = 실질(물가채) + 기대인플레 (10년)", unit="%"))
        st.caption(f"물가채는 유동성이 낮아 거래 없는 날이 있고({kr[REAL_10Y].isna().sum()}일), "
                   "BEI에 유동성 프리미엄이 섞여 있어 미국 BEI보다 노이즈가 큽니다.")
    with r:
        show_chart(line_chart(spread.to_dict("series"), "한미 금리차 (미국 − 한국, bp)", unit="bp", hline=0,
                              decimals=0))
    usdkrw = yahoo("KRW=X", years=kr_years + 1)
    usdkrw = usdkrw[usdkrw.index >= kr.index[0]]
    show_chart(line_chart({"원/달러": usdkrw}, "원/달러 환율 (금리차와 함께 보기)", height=240, decimals=1))
    st.caption("한미 금리차가 벌어지면(미국 금리가 더 높아지면) 달러 수요가 늘어 원화 약세 압력이 커지는 경향이 있지만, "
               "무역수지·위험선호 등 다른 요인도 커서 항상 같이 움직이지는 않습니다.")
