from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

import config
from data import krx, naver_live
from data.intraday import kr_intraday, session_bounds
from data.market import SUMMARY_ITEMS, fmt_asof, get_snapshot
from data.morning_brief import CLOSING_KINDS, load_closing
from ui import (CONTEXT_CSS, DOWN_COLOR, UP_COLOR, Z_ALERT, index_card, investor_chart, render_brief_cards, returns_bar,
                show_chart, treemap)

st.title("국내 증시")

# ── 마감 시황 (원문 카드, AI 없음): 한화 ★ 채권·주식 마감 시황, 가장 최근 거래일 것 ──
_closing = load_closing(datetime.now().date())
with st.container(border=True):
    if _closing["cards"]:
        today = _closing["day"] == datetime.now().date()
        st.markdown(f"**🔔 마감 시황** · {_closing['day']:%m/%d}" + ("" if today else " :gray[(직전 거래일 · 오늘 것은 보통 15:45\\~17:10 게시)]")
                    + " · 한화투자증권 리서치 (원문)")
        render_brief_cards(_closing["cards"], CLOSING_KINDS)
    else:
        st.markdown("**🔔 마감 시황**")
        st.caption("최근 5일 안에 한화 마감 시황 원문이 없습니다"
                   + (f" · :orange[채널 읽기 실패: {_closing['error'][:80]}]" if _closing.get("error") else ""))

if not config.KRX_API_KEY:
    st.info("KRX_API_KEY가 없어 국내 증시 데이터를 불러올 수 없습니다 (.env 확인).")
    st.stop()

with st.spinner("KRX 데이터 불러오는 중..."):
    day = krx.latest_trading_day()
    stocks = krx.stocks_day(day)
st.caption(f"조회 시각 {datetime.now():%Y-%m-%d %H:%M} · 종목·업종·ETF: KRX 공식 데이터 **{day:%Y-%m-%d} 종가** "
           "(다음 영업일 공개, 그 전까지 1일 값은 네이버 실시간) · 지수 카드: 네이버 실시간(🟢 장중) · 막대 = 1년 최저\\~최고 중 현재 위치 · "
           f"⚡ = 평소 하루 변동의 {Z_ALERT:.0f}배 이상 · 상승 빨강 / 하락 파랑")


def _ret_color(v) -> str:
    if pd.isna(v) or v == 0:
        return ""
    return f"color: {UP_COLOR if v > 0 else DOWN_COLOR}"


# ── 네이버 실시간 사용 여부: KRX에 아직 없는 날(장중·당일 마감 후)의 '1일' 값은 네이버로 ──
try:
    nv_cap = naver_live.stock_lists("marketValue")
    nv_time, nv_open = naver_live.live_status(nv_cap)
    use_live, live_err = nv_time.date() > day, None
except Exception as e:  # 네이버 실패 → KRX 전 거래일 데이터 그대로
    use_live, live_err = False, str(e)
live_tag = (f"🟢 실시간 {nv_time:%H:%M}" if nv_open else f"{nv_time:%m/%d} 마감 (네이버)") if use_live else ""
src = f"**{live_tag}** · 네이버 증권" if use_live else f"KRX **{day:%m/%d} 종가**"
if live_err:
    st.caption(f":orange[네이버 실시간 읽기 실패 → KRX 전 거래일 데이터로 표시: {live_err[:80]}]")

# ── 주요 지수 카드 (오늘의 시황 카드와 같은 모양) + 시장 폭 ──────────
INDEX_CHART_DAYS = {"1일": 1, "1주": 7, "1개월": 30, "3개월": 90, "6개월": 180, "1년": 365}  # 1일·1주는 장중 분봉
KR_INTRADAY_CODE = {"kospi": "KOSPI", "kosdaq": "KOSDAQ", "kospi200": "KPI200"}
i1, i2 = st.columns([3, 2], vertical_alignment="bottom")
i1.subheader("📈 주요 지수")
idx_period = i2.segmented_control("차트 기간", list(INDEX_CHART_DAYS), default="1일", key="kr_idx_period") or "1일"
st.html(CONTEXT_CSS)
c = st.columns(4)
for col, key in zip(c[:3], ["kospi", "kosdaq", "kospi200"]):
    snap = get_snapshot(key)
    with col:
        if snap is None:
            st.metric(SUMMARY_ITEMS[key][0], "N/A", help="데이터 수집 실패", border=True)
            continue
        live = snap["asof_kind"] == "live"
        spark = None
        if idx_period in ("1일", "1주"):
            try:
                spark = kr_intraday(KR_INTRADAY_CODE[key], idx_period)
            except Exception:
                spark = pd.Series(dtype=float)
        index_card(f"kr_{key}", f"{'🟢 ' if live else ''}{snap['label']} · {fmt_asof(snap)}", snap,
                   INDEX_CHART_DAYS[idx_period], spark=spark,
                   session=session_bounds(spark, "kr") if idx_period == "1일" else None)
br = None
if use_live:
    try:
        br = naver_live.breadth()
    except Exception:
        pass
if br:
    value = pd.Series(br["거래대금"]) / 1e12
    up, down, flat = br["상승"], br["하락"], br["보합"]
    when = "실시간" if nv_open else f"{nv_time:%m/%d}"
else:
    value = stocks.groupby("시장")["거래대금"].sum() / 1e12
    up, down = int((stocks["등락률"] > 0).sum()), int((stocks["등락률"] < 0).sum())
    flat, when = len(stocks) - up - down, f"{day:%m/%d}"
c[3].metric("거래대금", f"{value.sum():,.1f}조원", f"코스피 {value.get('KOSPI', 0):.1f} · 코스닥 {value.get('KOSDAQ', 0):.1f}",
            delta_color="off", delta_arrow="off", delta_description=when, border=True)
c[3].metric("상승 종목 비율", f"{up / (up + down + flat) * 100:.0f}%", f"↑{up:,} ↓{down:,}",
            delta_color="off", delta_arrow="off", delta_description=when, border=True,
            help=f"KOSPI·KOSDAQ 전 종목 중 상승 종목 비율 (상승 {up:,} · 하락 {down:,} · 보합 {flat:,}). "
                 "지수와 달리 대형주 영향이 없는 시장 폭(breadth) 지표")

# ── 투자자별 매매동향 (네이버, 순매수 억원) ────────────────────
st.subheader("💰 투자자별 매매동향")


def _eok(v: float) -> str:
    """억원 금액: 1조 이상은 조 단위로."""
    return f"{v / 1e4:+,.1f}조" if abs(v) >= 1e4 else f"{v:+,.0f}억"


for col, mkt in zip(st.columns(2, gap="large"), ("KOSPI", "KOSDAQ")):
    with col:
        try:
            inv = naver_live.investor_history(mkt, 20)
            prog = naver_live.program_today(mkt)
        except Exception as e:
            st.caption(f"**{mkt}** · :orange[네이버 수급 읽기 실패: {str(e)[:80]}]")
            continue
        if inv.empty:
            continue
        last_day, today_row = inv.index[-1], inv.iloc[-1]
        live_inv = use_live and nv_open and last_day.date() == datetime.now().date()
        st.markdown(f"**{mkt}** · {last_day:%m/%d}" + (" 🟢 장중 잠정" if live_inv else ""))
        m = st.columns(4)
        for c_, name in zip(m, ("외국인", "기관", "개인")):
            v = today_row[name]
            c_.metric(name, _eok(v), f"20일 {_eok(inv[name].sum())}", delta_color="off", delta_arrow="off")
        if prog["날짜"].date() == last_day.date():
            m[3].metric("프로그램", _eok(prog["합계"]), f"비차익 {_eok(prog['비차익'])}", delta_color="off",
                        delta_arrow="off", help=f"프로그램 순매수 = 차익 {_eok(prog['차익'])} + 비차익 {_eok(prog['비차익'])}. "
                                                "비차익은 바스켓(여러 종목 묶음) 매매로 주로 외국인·기관의 방향성 매매")
        show_chart(investor_chart(inv, f"최근 {len(inv)}거래일 순매수 (억원)"))
st.caption("네이버 증권 · 순매수 = 매수 − 매도 (억원) · 장중 값은 잠정치로 장 마감 후 확정 · 외국인 순매도가 이어지면 지수 하락 압력이 커지는 경우가 많음")

tabs = st.tabs(["🏭 업종별 등락", "🗺️ 시가총액 상위", "🔥 등락·거래대금 상위", "📦 ETF"])

# ── 업종별 등락 ──────────────────────────────────────
with tabs[0]:
    periods = {"1일": 1, "1주": 7, "1개월": 31, "3개월": 92}
    c1, c2 = st.columns(2)
    period = c2.segmented_control("기간", list(periods), default="1일", key="sec_period") or "1일"
    if period == "1일" and use_live:
        c1.caption(f"{live_tag} · **네이버 업종 분류** (KOSPI·KOSDAQ 합산) · 1주 이상은 KRX 업종 지수")
        try:
            inds, _ = naver_live.industries()
        except Exception as e:
            inds = None
            st.warning(f"네이버 업종 등락을 불러오지 못했습니다: {e}")
        if inds is not None and len(inds):
            show_all = st.toggle(f"전체 {len(inds)}개 업종 보기 (기본: 상위·하위 15개)", key="sec_all")
            ret = inds.set_index("업종")["등락률"].sort_values(ascending=False)
            shown = ret if show_all or len(ret) <= 30 else pd.concat([ret.head(15), ret.tail(15)])
            show_chart(returns_bar(shown, f"업종 등락률 (네이버 업종 분류, {live_tag})", height=max(360, 22 * len(shown))))
            st.caption(f"{len(ret)}개 업종 중 {(ret > 0).sum()}개 상승 · {(ret < 0).sum()}개 하락 · "
                       "네이버 업종 분류는 KRX 업종 지수와 다름 (KRX 1일 값은 다음 영업일에 공개)")
    else:
        market = c1.segmented_control("시장", ["KOSPI", "KOSDAQ"], default="KOSPI", key="sec_mkt") or "KOSPI"
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
    group = st.segmented_control("묶음", ["업종", "시장"], default="업종", key="heat_group") or "업종"
    top = (nv_cap if use_live else stocks).nlargest(n, "시가총액")
    if group == "업종":
        with st.spinner("업종 분류 불러오는 중... (하루 한 번)"):
            imap = naver_live.industry_map()
        top = top.assign(섹터=[naver_live.sector_of(c, imap, "기타") for c in top["코드"]])
    else:
        top = top.assign(섹터=top["시장"])
    show_chart(treemap(top.assign(수익률=top["등락률"]), color_range=5, root=f"국내 시가총액 상위 {n}"))
    st.caption(f"{src} · 크기 = 시가총액, 색 = 등락률 · 묶음: "
               + ("네이버 업종 분류 (우선주는 보통주 업종)" if group == "업종" else "KOSPI/KOSDAQ")
               + " · 묶음 타일 수익률은 시총 가중 평균")

# ── 등락·거래대금 상위 ───────────────────────────────
with tabs[2]:
    c1, c2 = st.columns([1, 2])
    market = c1.segmented_control("시장", ["전체", "KOSPI", "KOSDAQ"], default="전체", key="top_mkt") or "전체"
    min_val = c2.select_slider("최소 거래대금 (등락률 순위에만 적용)", [0, 10, 50, 100, 500], value=50,
                               format_func=lambda v: f"{v}억원", key="top_min")
    mkts = ["KOSPI", "KOSDAQ"] if market == "전체" else [market]
    lists = None
    if use_live:  # 네이버 상승률·하락률·거래대금 순위 (시장별 상위 100개씩)
        try:
            lists = {k: pd.concat([naver_live.stock_list(k, m) for m in mkts], ignore_index=True)
                     for k in ("up", "down", "priceTop")}
        except Exception as e:
            st.caption(f":orange[네이버 순위 읽기 실패 → KRX {day:%m/%d} 종가: {str(e)[:60]}]")
    if lists is None:
        pool = stocks[stocks["시장"].isin(mkts)]
        lists = {"up": pool, "down": pool, "priceTop": pool}
    liquid = {k: v[v["거래대금"] >= min_val * 1e8] for k, v in lists.items()}

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
        _table(liquid["up"].nlargest(15, "등락률"))
    with m:
        st.markdown("**하락률 상위**")
        _table(liquid["down"].nsmallest(15, "등락률"))
    with r:
        st.markdown("**거래대금 상위**")
        _table(lists["priceTop"].nlargest(15, "거래대금"))
    st.caption(f"{src} · 거래가 거의 없는 종목이 등락률 순위를 차지하지 않도록 최소 거래대금 조건을 둡니다"
               + (" (실시간은 상승·하락률 상위 100개 안에서 거름)" if use_live else "") + ".")

# ── ETF ──────────────────────────────────────────────
with tabs[3]:
    etf = krx.etfs_day(day)
    if use_live:
        try:  # 네이버 실시간 시세 + 기초지수는 KRX 것을 이름으로 붙임
            etf = naver_live.etfs().merge(etf[["ETF", "기초지수"]], on="ETF", how="left")
        except Exception as e:
            st.caption(f":orange[네이버 ETF 실시간 읽기 실패 → KRX {day:%m/%d} 종가: {str(e)[:60]}]")
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
    st.caption(f"{src} · 전체 {len(etf):,}개 ETF" + (" · 순자산 자리는 실시간 시가총액(순자산과 거의 같음)" if use_live else "")
               + " · 레버리지·인버스 ETF가 등락률 순위에 자주 오르니 기초지수와 함께 보세요.")
