from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

import config
from data import krx
from data.market import fmt_asof, get_snapshot
from ui import DOWN_COLOR, UP_COLOR, returns_bar, show_chart, treemap

st.title("국내 증시")

if not config.KRX_API_KEY:
    st.info("KRX_API_KEY가 없어 국내 증시 데이터를 불러올 수 없습니다 (.env 확인).")
    st.stop()

with st.spinner("KRX 데이터 불러오는 중..."):
    day = krx.latest_trading_day()
    stocks = krx.stocks_day(day)
st.caption(f"조회 시각 {datetime.now():%Y-%m-%d %H:%M} · 종목·업종·ETF: KRX 공식 데이터 **{day:%Y-%m-%d} 종가** "
           "(다음 영업일 공개) · KOSPI·KOSDAQ 카드: 네이버 실시간 · 상승 빨강 / 하락 파랑")


def _ret_color(v) -> str:
    if pd.isna(v) or v == 0:
        return ""
    return f"color: {UP_COLOR if v > 0 else DOWN_COLOR}"


def _color(v: float) -> str:
    return "red" if v > 0 else "blue" if v < 0 else "gray"


# ── 상단 카드 ────────────────────────────────────────
c = st.columns(5)
for col, key in zip(c[:2], ["kospi", "kosdaq"]):
    snap = get_snapshot(key)
    with col:
        if snap is None:
            st.metric(key.upper(), "N/A", border=True)
        else:
            st.metric(f"{snap['label']} · {fmt_asof(snap)}", f"{snap['last']:,.2f}",
                      f"{snap['chg']:+,.2f} ({snap['pct']:+.2f}%)", delta_color=_color(snap["chg"]), border=True)
idx_today = {x["IDX_NM"]: x for x in krx.fetch_day("idx/kospi_dd_trd", day)}
k200 = idx_today.get("코스피 200")
if k200:
    chg = float(k200["CMPPREVDD_IDX"])
    c[2].metric("코스피 200", f"{float(k200['CLSPRC_IDX']):,.2f}", f"{chg:+,.2f} ({float(k200['FLUC_RT']):+.2f}%)",
                delta_color=_color(chg), delta_description=f"{day:%m/%d}", border=True)
value = stocks.groupby("시장")["거래대금"].sum() / 1e12
c[3].metric("거래대금", f"{value.sum():,.1f}조원", f"코스피 {value.get('KOSPI', 0):.1f} · 코스닥 {value.get('KOSDAQ', 0):.1f}",
            delta_color="off", delta_arrow="off", delta_description=f"{day:%m/%d}", border=True)
up, down = (stocks["등락률"] > 0).sum(), (stocks["등락률"] < 0).sum()
c[4].metric("상승 종목 비율", f"{up / len(stocks) * 100:.0f}%",
            f"↑{up:,} ↓{down:,}",
            delta_color="off", delta_arrow="off", delta_description=f"{day:%m/%d}", border=True,
            help=f"KOSPI·KOSDAQ 전 종목(우선주 포함) 중 상승 종목 비율 (상승 {up:,} · 하락 {down:,} · "
                 f"보합 {len(stocks) - up - down:,}). 지수와 달리 대형주 영향이 없는 시장 폭(breadth) 지표")

tabs = st.tabs(["🏭 업종별 등락", "🗺️ 시가총액 상위", "🔥 등락·거래대금 상위", "📦 ETF"])

# ── 업종별 등락 ──────────────────────────────────────
with tabs[0]:
    periods = {"1일": 1, "1주": 7, "1개월": 31, "3개월": 92}
    c1, c2 = st.columns(2)
    market = c1.segmented_control("시장", ["KOSPI", "KOSDAQ"], default="KOSPI", key="sec_mkt") or "KOSPI"
    period = c2.segmented_control("기간", list(periods), default="1일", key="sec_period") or "1일"
    with st.spinner("업종 지수 불러오는 중... (처음 3개월치는 몇 초 걸림)"):
        hist = krx.index_history(market, day - timedelta(days=periods[period] + 10), day)
    cols = [s for s in krx.SECTOR_INDICES[market] if s in hist]
    base = hist.iloc[-2] if period == "1일" else hist[hist.index <= pd.Timestamp(day - timedelta(days=periods[period]))].iloc[-1]
    ret = (hist.iloc[-1][cols] / base[cols] - 1) * 100
    bench = "코스피" if market == "KOSPI" else "코스닥"
    bench_ret = (hist.iloc[-1][bench] / base[bench] - 1) * 100
    show_chart(returns_bar(ret, f"{market} 업종 지수 수익률 ({period}, ~{day:%m/%d}) · {bench} {bench_ret:+.2f}%",
                           height=max(360, 22 * len(ret))))
    beat = (ret > bench_ret).sum()
    st.caption(f"{len(ret)}개 업종 중 {beat}개가 {bench} 지수보다 강했습니다. KRX 업종 분류 기준 (코스피200 섹터 지수와는 다름).")

# ── 시가총액 상위 히트맵 ─────────────────────────────
with tabs[1]:
    n = st.segmented_control("종목 수", [30, 50, 100], default=50, key="heat_n") or 50
    top = stocks.nlargest(n, "시가총액").rename(columns={"시장": "섹터"})
    show_chart(treemap(top.assign(수익률=top["등락률"]), color_range=5, root=f"국내 시가총액 상위 {n}"))
    st.caption(f"크기 = 시가총액, 색 = {day:%m/%d} 등락률 · KRX 데이터에 종목별 업종 분류가 없어 시장(KOSPI/KOSDAQ)으로 묶음 · "
               "시장 타일 수익률은 시총 가중 평균")

# ── 등락·거래대금 상위 ───────────────────────────────
with tabs[2]:
    c1, c2 = st.columns([1, 2])
    market = c1.segmented_control("시장", ["전체", "KOSPI", "KOSDAQ"], default="전체", key="top_mkt") or "전체"
    min_val = c2.select_slider("최소 거래대금 (등락률 순위에만 적용)", [0, 10, 50, 100, 500], value=50,
                               format_func=lambda v: f"{v}억원", key="top_min")
    pool = stocks if market == "전체" else stocks[stocks["시장"] == market]
    liquid = pool[pool["거래대금"] >= min_val * 1e8]

    def _table(df: pd.DataFrame) -> None:
        # 세 표를 나란히 두므로 핵심 열만 (시장은 위 선택으로 거를 수 있음)
        view = df.assign(거래대금=df["거래대금"] / 1e8)[["종목", "종가", "등락률", "거래대금"]].rename(
            columns={"거래대금": "거래대금(억)"})
        st.dataframe(view.style.map(_ret_color, subset=["등락률"]).format(
            {"종가": "{:,.0f}", "등락률": "{:+.2f}%", "거래대금(억)": "{:,.0f}"}),
            hide_index=True, width="stretch", height=38 + 35 * len(view))

    l, m, r = st.columns(3, gap="medium")
    with l:
        st.markdown("**상승률 상위**")
        _table(liquid.nlargest(15, "등락률"))
    with m:
        st.markdown("**하락률 상위**")
        _table(liquid.nsmallest(15, "등락률"))
    with r:
        st.markdown("**거래대금 상위**")
        _table(pool.nlargest(15, "거래대금"))
    st.caption("거래가 거의 없는 종목이 등락률 순위를 차지하지 않도록 최소 거래대금 조건을 둡니다.")

# ── ETF ──────────────────────────────────────────────
with tabs[3]:
    etf = krx.etfs_day(day)
    etf_view = etf.assign(거래대금=etf["거래대금"] / 1e8, 순자산=etf["순자산"] / 1e8)
    fmt = {"종가": "{:,.0f}", "등락률": "{:+.2f}%", "거래대금": "{:,.0f}", "순자산": "{:,.0f}"}
    cols = ["ETF", "종가", "등락률", "거래대금", "순자산", "기초지수"]
    st.markdown("**거래대금 상위** (금액 단위: 억원)")
    st.dataframe(etf_view.nlargest(20, "거래대금")[cols].style.map(_ret_color, subset=["등락률"]).format(fmt),
                 hide_index=True, width="stretch")
    big = etf_view[etf_view["순자산"] >= 1000]  # 순자산 1,000억 이상만
    l, r = st.columns(2, gap="large")
    with l:
        st.markdown("**상승률 상위** (순자산 1,000억 이상)")
        st.dataframe(big.nlargest(10, "등락률")[cols].style.map(_ret_color, subset=["등락률"]).format(fmt),
                     hide_index=True, width="stretch")
    with r:
        st.markdown("**하락률 상위** (순자산 1,000억 이상)")
        st.dataframe(big.nsmallest(10, "등락률")[cols].style.map(_ret_color, subset=["등락률"]).format(fmt),
                     hide_index=True, width="stretch")
    st.caption(f"전체 {len(etf):,}개 ETF · 레버리지·인버스 ETF가 등락률 순위에 자주 오르니 기초지수와 함께 보세요.")
