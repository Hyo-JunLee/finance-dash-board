from datetime import date, datetime

import pandas as pd
import streamlit as st

from data import portfolio as pf
from data.global_markets import get_closes
from ui import DOWN_COLOR, UP_COLOR, bar_chart, line_chart, returns_bar, show_chart

st.title("내 포트폴리오")
st.caption("미국 주식·ETF · USD 기준 · 평균단가 이동평균법 · 기록은 이 맥의 portfolio/portfolio.db에만 저장")

SIDE_KO = {"BUY": "매수", "SELL": "매도"}


def _pnl_color(v) -> str:
    if pd.isna(v) or abs(v) < 1e-9:
        return ""
    return f"color: {UP_COLOR if v > 0 else DOWN_COLOR}"


def _usd(v: float, sign: bool = False) -> str:
    """$1,234.56 / 부호 표시 시 +$1,234.56, −$1,234.56"""
    if pd.isna(v):
        return "-"
    s = f"${abs(v):,.2f}"
    return (("+" if v > 0 else "-" if v < 0 else "") + s) if sign else ("-" + s if v < 0 else s)


def _refresh() -> None:
    st.cache_data.clear()
    st.rerun()


trades_raw, divs = pf.load_trades(), pf.load_dividends()
tabs = st.tabs(["📊 현황", "✍️ 거래 입력", "💵 배당", "📈 성과"])

# ── 거래 입력 ────────────────────────────────────────
with tabs[1]:
    st.markdown("##### 새 거래")
    with st.form("trade_form", clear_on_submit=True):
        c = st.columns([1.2, 1, 1, 1, 1, 1])
        t_date = c[0].date_input("체결일", value=date.today(), max_value=date.today())
        t_ticker = c[1].text_input("티커", placeholder="예: NVDA").upper().strip()
        t_side = c[2].segmented_control("구분", ["BUY", "SELL"], default="BUY", format_func=SIDE_KO.get)
        t_qty = c[3].number_input("수량", min_value=0.0, step=1.0, format="%.4f")
        t_price = c[4].number_input("체결가 ($)", min_value=0.0, step=0.01, format="%.4f")
        t_fee = c[5].number_input("수수료 ($)", min_value=0.0, step=0.01, format="%.2f")
        t_memo = st.text_input("메모 (선택)")
        if st.form_submit_button("기록하기", type="primary"):
            held = pf.compute_positions(pf.split_adjust(trades_raw))[0].set_index("ticker")["qty"]
            errors = []
            if not t_ticker:
                errors.append("티커를 입력하세요.")
            elif not pf.ticker_exists(t_ticker):
                errors.append(f"'{t_ticker}' 시세를 찾을 수 없습니다. 티커를 확인하세요.")
            if t_qty <= 0 or t_price <= 0:
                errors.append("수량과 체결가는 0보다 커야 합니다.")
            if t_side == "SELL" and t_qty > held.get(t_ticker, 0) + 1e-9:
                errors.append(f"보유 수량({held.get(t_ticker, 0):g}주)보다 많이 매도할 수 없습니다.")
            if errors:
                for e in errors:
                    st.error(e)
            else:
                pf.add_trade(t_date, t_ticker, t_side, t_qty, t_price, t_fee, t_memo)
                st.toast(f"{SIDE_KO[t_side]} 기록: {t_ticker} {t_qty:g}주 @ ${t_price:,.2f}", icon="✅")
                _refresh()

    st.markdown("##### 거래 기록 (직접 수정·삭제 가능)")
    st.caption("칸을 고치거나 행을 선택해 삭제한 뒤 '변경 저장'을 누르세요. 저장하면 모든 손익이 다시 계산됩니다.")
    trade_view = trades_raw.drop(columns="id").sort_values("date", ascending=False).reset_index(drop=True)
    edited = st.data_editor(
        trade_view,
        num_rows="dynamic", hide_index=True, width="stretch", key="trade_editor",
        column_config={
            "date": st.column_config.DateColumn("체결일", required=True),
            "ticker": st.column_config.TextColumn("티커", required=True),
            "side": st.column_config.SelectboxColumn("구분", options=["BUY", "SELL"], required=True),
            "qty": st.column_config.NumberColumn("수량", min_value=0.0001, format="%.4f", required=True),
            "price": st.column_config.NumberColumn("체결가", min_value=0.0001, format="$%.4f", required=True),
            "fee": st.column_config.NumberColumn("수수료", min_value=0.0, format="$%.2f", default=0.0),
            "memo": st.column_config.TextColumn("메모"),
        },
    )
    c1, c2 = st.columns([1, 5])
    if c1.button("변경 저장", disabled=edited.equals(trade_view)):
        clean = edited.dropna(subset=["date", "ticker", "side", "qty", "price"]).fillna({"fee": 0.0, "memo": ""})
        pf.replace_table("trades", clean)
        st.toast("거래 기록을 저장했습니다.", icon="💾")
        _refresh()
    if len(trades_raw):
        c2.download_button("CSV 백업", trades_raw.to_csv(index=False).encode("utf-8-sig"),
                           f"trades_{date.today():%Y%m%d}.csv", "text/csv")

# ── 배당 ─────────────────────────────────────────────
with tabs[2]:
    st.markdown("##### 배당 기록")
    with st.form("div_form", clear_on_submit=True):
        c = st.columns([1.2, 1, 1, 1, 2])
        d_date = c[0].date_input("입금일", value=date.today(), max_value=date.today())
        d_ticker = c[1].text_input("티커", placeholder="예: SCHD").upper().strip()
        d_gross = c[2].number_input("세전 배당금 ($)", min_value=0.0, step=0.01, format="%.2f")
        d_tax = c[3].number_input("원천징수세 ($)", min_value=0.0, step=0.01, format="%.2f",
                                  help="미국 주식 배당은 보통 15% 원천징수")
        d_memo = c[4].text_input("메모 (선택)")
        if st.form_submit_button("기록하기", type="primary"):
            if not d_ticker or d_gross <= 0:
                st.error("티커와 세전 배당금을 입력하세요.")
            elif d_tax > d_gross:
                st.error("세금이 배당금보다 클 수 없습니다.")
            else:
                pf.add_dividend(d_date, d_ticker, d_gross, d_tax, d_memo)
                st.toast(f"배당 기록: {d_ticker} ${d_gross - d_tax:,.2f} (세후)", icon="💵")
                _refresh()

    if divs.empty:
        st.info("아직 배당 기록이 없습니다.")
    else:
        c = st.columns(3)
        c[0].metric("누적 배당 (세후)", f"${divs['net'].sum():,.2f}", border=True)
        this_year = divs[divs["date"].dt.year == date.today().year]
        c[1].metric(f"{date.today().year}년 배당 (세후)", f"${this_year['net'].sum():,.2f}", border=True)
        c[2].metric("누적 원천징수세", f"${divs['tax'].sum():,.2f}", border=True)

        monthly = divs.set_index("date")["net"].resample("MS").sum()
        monthly = monthly[monthly.index >= monthly.index.max() - pd.DateOffset(months=23)]
        show_chart(bar_chart(monthly, "월별 배당 (세후, 최근 24개월)", unit="$", decimals=2, height=260))

        by_ticker = divs.groupby("ticker")["net"].sum().sort_values(ascending=False)
        st.dataframe(by_ticker.rename("누적 세후 배당").to_frame().style.format("${:,.2f}"), width="stretch")

        st.markdown("##### 배당 기록 (직접 수정·삭제 가능)")
        div_edit = st.data_editor(
            divs.drop(columns=["id", "net"]).sort_values("date", ascending=False).reset_index(drop=True),
            num_rows="dynamic", hide_index=True, width="stretch", key="div_editor",
            column_config={
                "date": st.column_config.DateColumn("입금일", required=True),
                "ticker": st.column_config.TextColumn("티커", required=True),
                "gross": st.column_config.NumberColumn("세전", min_value=0.01, format="$%.2f", required=True),
                "tax": st.column_config.NumberColumn("세금", min_value=0.0, format="$%.2f", default=0.0),
                "memo": st.column_config.TextColumn("메모"),
            },
        )
        if st.button("배당 변경 저장"):
            pf.replace_table("dividends", div_edit.dropna(subset=["date", "ticker", "gross"]).fillna({"tax": 0.0}))
            st.toast("배당 기록을 저장했습니다.", icon="💾")
            _refresh()

# ── 현황 · 성과 (거래가 있어야 계산) ───────────────────
if trades_raw.empty:
    for tab in (tabs[0], tabs[3]):
        with tab:
            st.info("아직 거래 기록이 없습니다. '✍️ 거래 입력' 탭에서 첫 거래를 기록하세요.")
    st.stop()

trades = pf.split_adjust(trades_raw)
holdings, realized = pf.compute_positions(trades)
tickers = tuple(sorted(set(trades["ticker"])))
with st.spinner("시세 불러오는 중..."):
    prices = pf.get_prices(tickers, trades["date"].min().date())
table = pf.holdings_table(holdings, prices)

with tabs[0]:
    value = table["평가금액"].sum() if len(table) else 0.0
    cost = table["원가"].sum() if len(table) else 0.0
    unreal = value - cost
    real_pnl = realized["pnl"].sum()
    div_total = divs["net"].sum()
    day_pnl = (table["평가금액"] * table["일간"] / (100 + table["일간"])).sum() if len(table) else 0.0

    c = st.columns(5)
    c[0].metric("평가금액", f"${value:,.2f}", f"{_usd(day_pnl, sign=True)} 오늘", delta_color="off", border=True)
    c[1].metric("평가손익", _usd(unreal, sign=True), f"{(unreal / cost * 100 if cost else 0):+.2f}%",
                delta_color="red" if unreal > 0 else "blue" if unreal < 0 else "gray", border=True)
    c[2].metric("실현손익 (누적)", _usd(real_pnl, sign=True), border=True)
    c[3].metric("배당 (누적, 세후)", f"${div_total:,.2f}", border=True)
    total = unreal + real_pnl + div_total
    c[4].metric("총 손익", _usd(total, sign=True), help="평가손익 + 실현손익 + 배당", border=True)

    if table.empty:
        st.info("현재 보유 종목이 없습니다 (모두 매도).")
    else:
        pct_cols = ["일간", "수익률"]
        st.dataframe(
            table.style.map(_pnl_color, subset=pct_cols + ["평가손익"]).format({
                "수량": "{:,.4g}", "평균단가": "${:,.2f}", "현재가": "${:,.2f}", "일간": "{:+.2f}%",
                "평가금액": "${:,.2f}", "원가": "${:,.2f}", "평가손익": lambda v: _usd(v, sign=True), "수익률": "{:+.2f}%",
                "비중": "{:.1f}%",
            }, na_rep="-"),
            hide_index=True, width="stretch",
        )
        l, r = st.columns(2)
        with l:
            show_chart(returns_bar(table.set_index("종목")["수익률"], "종목별 수익률", height=max(240, 40 * len(table))))
        with r:
            w = table.set_index("종목")["비중"].sort_values()
            fig = returns_bar(w, "비중", height=max(240, 40 * len(table)))
            fig.update_traces(marker_color="#3987e5", text=[f"{v:.1f}%" for v in w.values],
                              hovertemplate="%{y}: %{x:.1f}%<extra></extra>")
            show_chart(fig)

    if len(realized):
        with st.expander(f"실현손익 내역 ({len(realized)}건)"):
            st.dataframe(
                realized.rename(columns={"date": "매도일", "ticker": "종목", "qty": "수량", "proceeds": "매도대금",
                                         "cost": "매도원가", "pnl": "실현손익"})
                .style.map(_pnl_color, subset=["실현손익"])
                .format({"매도일": "{:%Y-%m-%d}", "수량": "{:,.4g}", "매도대금": "${:,.2f}",
                         "매도원가": "${:,.2f}", "실현손익": lambda v: _usd(v, sign=True)}),
                hide_index=True, width="stretch",
            )

with tabs[3]:
    bench = get_closes((pf.BENCHMARK,), "5y")[pf.BENCHMARK]  # 배당 재투자 총수익
    perf = pf.daily_performance(trades, divs, prices, bench)
    if perf.empty or len(perf) < 2:
        st.info("성과를 계산하려면 거래일이 이틀 이상 필요합니다.")
    else:
        start = perf.index[0]
        c = st.columns(3)
        twr, spy = perf["포트폴리오"].iloc[-1], perf[pf.BENCHMARK].iloc[-1]
        c[0].metric("내 수익률 (TWR)", f"{twr:+.2f}%", help="시간가중수익률: 입출금 시점·규모의 영향을 제거한 운용 성과",
                    border=True)
        c[1].metric(f"{pf.BENCHMARK} (배당 재투자)", f"{spy:+.2f}%", border=True)
        c[2].metric("초과수익", f"{twr - spy:+.2f}%p",
                    delta_color="off", border=True)
        st.caption(f"기간: {start:%Y-%m-%d} (첫 거래일) ~ {perf.index[-1]:%Y-%m-%d}")
        show_chart(line_chart({"내 포트폴리오": perf["포트폴리오"], f"{pf.BENCHMARK}": perf[pf.BENCHMARK]},
                              "누적 수익률", unit="%", hline=0, height=340))
        show_chart(line_chart({"평가금액": perf["평가금액"]}, "평가금액 ($)", height=260))
