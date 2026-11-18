from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from data.consensus import parse_value
from data.econ_calendar import ff_fetched_at, get_recent_indicators, get_week_actuals, get_week_calendar
from data.market import SUMMARY_ITEMS, fmt_asof, get_snapshot
from data.news import get_news
from ui import render_news

PERIODS = {"1개월": 30, "3개월": 90, "6개월": 180, "1년": 365}


def _fmt_value(snap: dict, key: str) -> str:
    v = snap["last"]
    match snap["kind"]:
        case "fx":
            return f"{v:,.1f}원"
        case "rate":
            return f"{v:.2f}%"
        case "usd":
            return f"${v:,.0f}" if key == "btc" else f"${v:,.2f}"
        case _:
            return f"{v:,.2f}"


def _fmt_delta(snap: dict) -> str:
    if snap["kind"] == "rate":
        return f"{snap['chg'] * 100:+.0f}bp"
    return f"{snap['chg']:+,.2f} ({snap['pct']:+.2f}%)"





def _color(chg: float) -> str:
    # 국내 관례: 상승 빨강, 하락 파랑
    return "red" if chg > 0 else "blue" if chg < 0 else "gray"


# ── 헤더 ─────────────────────────────────────────────
head_l, head_r = st.columns([3, 1], vertical_alignment="bottom")
head_l.title("오늘의 시황")
head_l.caption(f"조회 시각 {datetime.now():%Y-%m-%d %H:%M} (KST) · 카드 시각은 모두 KST · 시각만 있으면 실시간, '종가'는 거래소 공식 마감 시각, '마지막 체결'은 24시간·선물 상품의 최근 거래 시각")
period = head_r.segmented_control("차트 기간", list(PERIODS), default="3개월") or "3개월"
cutoff = pd.Timestamp(datetime.now() - timedelta(days=PERIODS[period]))

# ── 요약 카드 (5 x 2) ────────────────────────────────
with st.spinner("시세 불러오는 중..."):
    snaps = {k: get_snapshot(k) for k in SUMMARY_ITEMS}

keys = list(SUMMARY_ITEMS)
for row in (keys[:5], keys[5:]):
    for col, key in zip(st.columns(5), row):
        snap = snaps[key]
        with col:
            if snap is None:
                st.metric(SUMMARY_ITEMS[key][0], "N/A", border=True, help="데이터 수집 실패")
                continue
            spark = snap["series"][snap["series"].index >= cutoff]
            st.metric(
                # 기준 시각은 제목 줄에 (변화량 줄에 두면 좁은 화면에서 '11/…'처럼 잘림)
                f"{snap['label']} · {fmt_asof(snap)}",
                _fmt_value(snap, key),
                _fmt_delta(snap),
                delta_color=_color(snap["chg"]),
                chart_data=spark.round(4).tolist(),
                chart_type="area",
                border=True,
            )

st.divider()

# ── 미국 경제지표 ────────────────────────────────────
cal_col, recent_col = st.columns([5, 6], gap="large")

with cal_col:
    st.subheader("🗓️ 이번 주 미국 경제지표 일정")
    impacts = st.pills(
        "중요도", ["High", "Medium", "Low"], default=["High", "Medium"],
        selection_mode="multi", format_func={"High": "🔴 높음", "Medium": "🟠 중간", "Low": "🟡 낮음"}.get,
        label_visibility="collapsed",
    )
    try:
        cal = get_week_calendar()
    except Exception as e:
        st.warning(f"일정을 불러오지 못했습니다: {e}")
        cal = pd.DataFrame()
    if cal.empty:
        st.info("표시할 일정이 없습니다.")
    else:
        now = pd.Timestamp.now(tz="Asia/Seoul")
        view = cal[cal["_impact"].isin(impacts + ["Holiday"])].copy()
        view.insert(0, "", view["일시(KST)"].map(lambda t: "✅" if t <= now else "⏳"))
        try:
            actuals = get_week_actuals()
        except Exception:
            actuals = {}
        view.insert(view.columns.get_loc("예상"), "실제", view["_key"].map(actuals))
        view[["실제", "예상", "이전"]] = view[["실제", "예상", "이전"]].fillna("-")
        view["중요도"] = view["중요도"].str.split().str[0]  # 아이콘만 표시
        view["일시(KST)"] = view["일시(KST)"].dt.tz_localize(None)
        view = view.drop(columns=["_impact", "_key"]).reset_index(drop=True)

        def _actual_style(df: pd.DataFrame) -> pd.DataFrame:
            # 실제가 예상보다 높으면 빨강, 낮으면 파랑 (좋고 나쁨 판단 아님)
            styles = pd.DataFrame("", index=df.index, columns=df.columns)
            for i in df.index:
                a, f = parse_value(df.at[i, "실제"]), parse_value(df.at[i, "예상"])
                if a is not None:
                    color = "#e66767" if f is not None and a > f else "#3987e5" if f is not None and a < f else "inherit"
                    styles.at[i, "실제"] = f"color: {color}; font-weight: 600"
            return styles

        st.dataframe(
            view.style.apply(_actual_style, axis=None),
            hide_index=True,
            width="stretch",
            height=420,
            column_config={
                "": st.column_config.TextColumn(width=30, help="✅ 발표 완료 · ⏳ 예정"),
                "일시(KST)": st.column_config.DatetimeColumn(format="MM/DD (ddd) HH:mm", width=118),
                "지표": st.column_config.TextColumn(width=130),
                "중요도": st.column_config.TextColumn(width=50),
                "실제": st.column_config.TextColumn(width=58, help="발표 결과 (Nasdaq 캘린더). 예상보다 높으면 빨강, 낮으면 파랑"),
                "예상": st.column_config.TextColumn(width=52),
                "이전": st.column_config.TextColumn(width=52),
            },
        )
        fetched = ff_fetched_at()
        st.caption(
            f"일정·예상·이전: ForexFactory (수신 {fetched:%m/%d %H:%M}) · 실제: Nasdaq 캘린더 (발표 후 5분 내 반영)"
            if fetched else "출처: ForexFactory"
        )

with recent_col:
    st.subheader("📊 최근 발표된 주요 지표")
    with st.spinner("FRED 지표 불러오는 중..."):
        recent = get_recent_indicators()
    recent = recent.sort_values("발표", ascending=False, na_position="last").reset_index(drop=True)  # 최근 발표순
    def _surprise_style(df: pd.DataFrame) -> pd.DataFrame:
        # 예상 대비 상회 빨강 / 하회 파랑 (가격 화면과 같은 방향 색). 좋고 나쁨은 지표마다 달라 판단하지 않음
        styles = pd.DataFrame("", index=df.index, columns=df.columns)
        for i, v in recent["_surprise"].items():
            if recent.at[i, "예상"] == "-" or abs(v) < 1e-9:
                continue
            color, bg = ("#e66767", "rgba(230,103,103,0.14)") if v > 0 else ("#3987e5", "rgba(57,135,229,0.14)")
            styles.at[i, "서프라이즈"] = f"color: {color}; background-color: {bg}; font-weight: 600"
        return styles

    table = recent.drop(columns=["_delta", "_surprise", "_warn"])
    st.dataframe(
        table.style.apply(_surprise_style, axis=None),
        hide_index=True,
        width="stretch",
        height=38 + 35 * len(table),  # 줄 수에 맞춰 스크롤 없이
        column_config={
            "지표": st.column_config.TextColumn(width=112),
            "발표": st.column_config.DatetimeColumn("발표 (KST)", format="MM/DD HH:mm", width=90,
                                                   help="해당 수치가 발표된 한국 시간"),
            "최신": st.column_config.TextColumn(width=58),
            "예상": st.column_config.TextColumn(width=48),
            "서프라이즈": st.column_config.TextColumn(width=100, help="Nasdaq 실제치 − 컨센서스"),
            "이전": st.column_config.TextColumn(width=58),
            "기준": st.column_config.TextColumn(width=50, help="지표의 대상 기간 (26.08 = 2026년 8월, 주간 지표는 주 마지막 날)"),
            "다음 발표": st.column_config.TextColumn("다음", width=48, help="다음 발표 예정일"),
        },
    )
    warns = recent.loc[recent["_warn"] != "", ["지표", "_warn"]]
    st.caption(
        "실제: FRED · 예상(컨센서스): Nasdaq 경제 캘린더 · 서프라이즈는 같은 출처(Nasdaq)의 실제치와 예상치로 판정"
        + "".join(f"  \n⚠ {name}: {msg}" for name, msg in zip(warns["지표"], warns["_warn"]))
    )

st.divider()

# ── 최신 헤드라인 ────────────────────────────────────
st.subheader("📰 최신 헤드라인")
news = get_news()
kr_col, gl_col = st.columns(2, gap="large")
for col, cat, label in ((kr_col, "국내", "🇰🇷 국내"), (gl_col, "해외", "🌎 해외")):
    with col:
        st.markdown(f"**{label}**")
        render_news(news[news["category"] == cat].head(8) if not news.empty else news, show_summary=False)
st.page_link("views/news.py", label="뉴스 전체 보기 →")
