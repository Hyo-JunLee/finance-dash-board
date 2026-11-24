from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

import config
from data.consensus import parse_value
from data.econ_calendar import ff_fetched_at, get_recent_indicators, get_week_actuals, get_week_calendar
from data.market import SUMMARY_ITEMS, fmt_asof, get_snapshot
from data.news import get_news, load_news_summary
from data.telegram import load_summary, recent_posts
from ui import (DOWN_COLOR, UP_COLOR, mini_chart, render_brief_cards, render_issues, render_news, render_post_list,
                render_summary, render_views, show_chart)

PERIODS = {"1개월": 30, "3개월": 90, "6개월": 180, "1년": 365}
Z_ALERT = 2.0  # 오늘 변화가 1년 일간 변동(표준편차)의 이 배수를 넘으면 '이례적'


def _fmt_value(snap: dict, key: str) -> str:
    v = snap["last"]
    match snap["kind"]:
        case "fx":
            return f"{v:,.1f}원"
        case "rate":
            return f"{v:.2f}%"
        case "spread":
            return f"{v:+.2f}%p"
        case "usd":
            return f"${v:,.0f}" if key == "btc" else f"${v:,.2f}"
        case _:
            return f"{v:,.2f}"


def _fmt_delta(snap: dict) -> str:
    if snap["kind"] in ("rate", "spread"):
        bp = round(snap["chg"] * 100, 1) + 0.0  # 금리는 0.1bp 단위로 본다
        return f"{bp:+.1f}bp" if bp else "0.0bp"
    return f"{snap['chg']:+,.2f} ({snap['pct']:+.2f}%)"



def _color(chg: float) -> str:
    # 국내 관례: 상승 빨강, 하락 파랑
    return "red" if chg > 0 else "blue" if chg < 0 else "gray"


def _is_flat(snap: dict) -> bool:
    return snap["kind"] in ("rate", "spread") and round(snap["chg"] * 100, 1) == 0


# ── 헤더 ─────────────────────────────────────────────
head_l, head_r = st.columns([3, 1], vertical_alignment="bottom")
head_l.title("오늘의 시황")
head_l.caption(f"조회 시각 {datetime.now():%Y-%m-%d %H:%M} (KST) · 카드 시각은 KST (시각만 = 실시간, '종가' = 공식 마감) · "
               f"막대 = 1년 최저\\~최고 중 현재 위치 · 차트 색 = 선택 기간 방향 · ⚡ = 평소 하루 변동의 {Z_ALERT:.0f}배 이상")
period = head_r.segmented_control("차트 기간", list(PERIODS), default="3개월") or "3개월"

# ── 아침 시황 (원문 카드, AI 없음): 월~금 한화 · 토 @ehdwl FICC · 일 토요일 것 ──────
def _esc(text: str) -> str:
    return text.replace("$", "\\$").replace("~", "\\~")  # 달러·물결이 수식·취소선으로 바뀌지 않게


def _morning_brief() -> None:
    from data.morning_brief import HANWHA_KINDS, load_brief, numbers_line, plan
    kind, day = plan(datetime.now())
    brief = load_brief(kind, day)
    with st.container(border=True):
        if kind == "hanwha":
            cards = brief.get("cards") or {}
            st.markdown(f"**☀️ 아침 시황** · {day:%m/%d} · 한화투자증권 리서치 (원문)")
            if not cards:
                st.markdown(_esc(numbers_line()))
                st.caption("한화 아침 시황 원문 대기 중 (보통 08:05\\~08:50 게시, 공휴일엔 없음) · 위 숫자는 대시보드 데이터"
                           + (f" · :orange[채널 읽기 실패: {brief['error'][:80]}]" if brief.get("error") else ""))
                return
            render_brief_cards(cards, HANWHA_KINDS)
        else:
            f = brief.get("ficc")
            st.markdown(f"**☀️ 주말 시황** · 금요일 미국장 마감  \n{_esc(numbers_line())}")
            if not f:
                st.caption("@ehdwl FICC 원문 대기 중 (보통 토요일 06:30경 게시) · 위 숫자는 대시보드 데이터"
                           + (f" · :orange[채널 읽기 실패: {brief['error'][:80]}]" if brief.get("error") else ""))
                return
            st.markdown(f"**FICC: {_esc(f['title'])}**")
            if not config.PUBLIC_MODE:
                for para in f["first"]:
                    st.markdown(f"<small>{_esc(para)}</small>", unsafe_allow_html=True)
                if f["rest"]:
                    with st.expander("유가·금·원자재·농산물 더 보기"):
                        for para in f["rest"]:
                            st.markdown(f"<small>{_esc(para)}</small>", unsafe_allow_html=True)
            st.caption(f"[원문 · {f['time']:%m/%d %H:%M}]({f['link']}) · 숫자 줄은 대시보드 데이터")


_morning_brief()
cutoff = pd.Timestamp(datetime.now() - timedelta(days=PERIODS[period]))

# ── 요약 카드 (자산군별) ─────────────────────────────
CARD_ROWS = [
    ("주식", ["kospi", "kosdaq", "spx", "ndx"]),
    ("금리", ["us2y", "us10y", "us2s10s", "kr3y"]),
    ("환율·리스크", ["usdkrw", "dxy", "vix"]),
    ("원자재·크립토", ["wti", "gold", "btc"]),
]

with st.spinner("시세 불러오는 중..."):
    snaps = {k: get_snapshot(k) for _, keys in CARD_ROWS for k in keys}


def _short(v: float, kind: str) -> str:
    if kind in ("rate", "spread"):
        return f"{v:.2f}"
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.2f}"


def _context_html(snap: dict) -> str:
    lo, hi, pos = snap["range"]
    unit = "bp" if snap["kind"] in ("rate", "spread") else "%"
    def item(name, v):
        color = UP_COLOR if v > 0 else DOWN_COLOR if v < 0 else "inherit"
        shown = f"{v:+.0f}{unit}" if unit == "bp" else f"{v:+.1f}{unit}"
        return f'<span>{name} <b style="color:{color}">{shown}</b></span>'
    per = "".join(item(name, v) for name, v in snap["periods"].items() if v == v)
    z = snap["z"]
    badge = ""
    if z == z and abs(z) >= Z_ALERT:
        badge = (f'<div class="cx-alert">⚡ 이례적 {"상승" if z > 0 else "하락"} · 평소 하루 변동의 {abs(z):.1f}배</div>')
    return (
        f'<div class="cx"><div class="cx-row"><span>1년 저 {_short(lo, snap["kind"])}</span>'
        f'<span class="cx-pos">{pos:.0f}%</span><span>고 {_short(hi, snap["kind"])}</span></div>'
        f'<div class="cx-track"><div class="cx-dot" style="left:calc({pos:.1f}% - 4px)"></div></div>'
        f'<div class="cx-per">{per}</div>{badge}</div>'
    )


st.html("""<style>
.cx { font-size:.74rem; line-height:1.35; margin-top:-.35rem; }
.cx-row { display:flex; justify-content:space-between; opacity:.65; }
.cx-pos { font-weight:700; opacity:1; }
.cx-track { position:relative; height:4px; border-radius:2px; margin:.2rem 0 .35rem;
            background:linear-gradient(90deg, rgba(57,135,229,.55), rgba(255,255,255,.15), rgba(230,103,103,.55)); }
.cx-dot { position:absolute; top:-3px; width:8px; height:10px; border-radius:2px; background:#fff; }
.cx-per { opacity:.85; display:flex; justify-content:space-between; gap:.3rem; white-space:nowrap; font-size:.7rem; }
.cx-alert { margin-top:.3rem; color:#f0a35a; font-weight:700; }
</style>""")
alert_css = []
for group, keys in CARD_ROWS:
    st.caption(f"**{group}**")
    for col, key in zip(st.columns(len(keys)), keys):  # 3장짜리 줄도 빈칸 없이 꽉 채움
        snap = snaps[key]
        with col, st.container(border=True, key=f"card_{key}"):
            if snap is None:
                st.metric(SUMMARY_ITEMS[key][0], "N/A", help="데이터 수집 실패")
                continue
            st.metric(
                # 기준 시각은 제목 줄에 (변화량 줄에 두면 좁은 화면에서 '11/…'처럼 잘림)
                f"{snap['label']} · {fmt_asof(snap)}",
                _fmt_value(snap, key),
                _fmt_delta(snap),
                delta_color="gray" if _is_flat(snap) else _color(snap["chg"]),
                delta_arrow="off" if _is_flat(snap) else "auto",
                help="1년 범위 막대: 최근 1년 최저\\~최고 중 현재 위치 · 기간 변화는 금리면 bp, 나머지는 % · "
                     "미니 차트 색은 선택한 차트 기간 동안의 방향",
            )
            st.html(_context_html(snap))
            spark = snap["series"][snap["series"].index >= cutoff]
            if len(spark) > 1:
                show_chart(mini_chart(spark, spark.iloc[-1] >= spark.iloc[0], PERIODS[period]))
            if snap["z"] == snap["z"] and abs(snap["z"]) >= Z_ALERT:
                alert_css.append(f".st-key-card_{key} {{ border-color:#f0a35a !important; }}")
if alert_css:
    st.html(f"<style>{' '.join(alert_css)}</style>")

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
        view.insert(0, "", view["일시(KST)"].map(lambda t: "✅" if t <= now else ""))
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
            height=38 + 35 * min(len(view), 14),  # 줄 수에 맞춤 (14줄 넘으면 스크롤)
            column_config={
                "": st.column_config.TextColumn(width=28, help="✅ 발표 완료"),
                "일시(KST)": st.column_config.DatetimeColumn(format="MM/DD(ddd) HH:mm", width=108),
                "지표": st.column_config.TextColumn(width=210),
                "중요도": st.column_config.TextColumn("", width=30, help="🔴 높음 · 🟠 중간 · 🟡 낮음 · ⚪ 휴일"),
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

# ── 오늘의 이슈 (뉴스 헤드라인 → AI 이슈 묶음, 하루 2회) ──────
st.subheader("🗞️ 오늘의 이슈")
news_sum = load_news_summary()
if news_sum and news_sum.get("issues"):
    made = pd.Timestamp(news_sum["generated_at"])
    st.caption(f"{made:%m/%d %H:%M} 기준 · 최근 {news_sum['window_hours']}시간 기사 {news_sum['article_count']}건을 "
               "이슈별로 묶음 (막대 = 관련 기사 수) · 하루 2회 08:30·16:00 갱신 · AI 요약이라 원문 링크로 확인")
    if (pd.Timestamp.now() - made).total_seconds() > 15 * 3600:
        st.warning("이슈 요약이 오래됐습니다. 맥이 꺼져 있었거나 요약 실행이 실패했을 수 있습니다 (데이터 상태 화면 참고).")
    render_issues(news_sum)
else:  # 요약이 아직 없으면 최신 헤드라인 목록으로 대신
    news = get_news()
    kr_col, gl_col = st.columns(2, gap="large")
    for col, cat, label in ((kr_col, "국내", "🇰🇷 국내"), (gl_col, "해외", "🌎 해외")):
        with col:
            st.markdown(f"**{label}**")
            render_news(news[news["category"] == cat].head(8) if not news.empty else news, show_summary=False)
st.page_link("views/news.py", label="뉴스 전체 보기 →")

# ── 리서치 코멘트 요약 (채권·매크로 텔레그램 6개 채널 → AI 요약, 하루 2회) ──────
with st.expander("📮 리서치 코멘트 요약", expanded=True):
    summary = load_summary()
    if summary is None:
        st.info("아직 요약이 없습니다. 하루 2회(08:30·16:00) 자동으로 만들어집니다.")
    else:
        made = pd.Timestamp(summary["generated_at"])
        age_h = (pd.Timestamp.now() - made).total_seconds() / 3600
        channels = " · ".join(f"{k} {v}" for k, v in summary.get("channels", {}).items())
        st.caption(f"{made:%m/%d %H:%M} 기준 · 최근 {summary.get('window_hours', 24)}시간 글 "
                   f"{summary.get('post_count', 0)}개 ({channels}) · 하루 2회 08:30·16:00 갱신")
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

# ── 바이사이드 관점 (개인 공부용 채널 → '무엇이 반영됐고 무엇을 볼 것인가', 로컬 전용) ──────
if not config.PUBLIC_MODE:
    with st.expander("🧭 바이사이드 관점", expanded=True):
        views = load_summary(config.BUYSIDE_SUMMARY_FILE)
        if views is None:
            st.info("아직 정리된 관점이 없습니다. 하루 2회(08:30·16:00) 자동으로 만들어집니다.")
        else:
            made = pd.Timestamp(views["generated_at"])
            channels = " · ".join(f"{k} {v}" for k, v in views.get("channels", {}).items())
            st.caption(f"{made:%m/%d %H:%M} 기준 · 최근 {views.get('window_hours', 24)}시간 글 "
                       f"{views.get('post_count', 0)}개 ({channels}) · 사후 설명·단순 전달은 빼고 "
                       "논지·시장 반영과의 차이·확인/반증 신호만 정리")
            if (pd.Timestamp.now() - made).total_seconds() > 15 * 3600:
                st.warning("관점 정리가 오래됐습니다. 맥이 꺼져 있었거나 요약 실행이 실패했을 수 있습니다.")
            render_views(views)
            st.caption("AI(Claude)가 정리한 것으로 원문과 다를 수 있습니다. 'AI 추론' 표시가 붙은 신호는 글이 직접 말한 것이 "
                       "아니라 논지에서 끌어낸 것입니다. 의견은 각 채널 작성자의 것이며 투자 권유가 아닙니다.")
        with st.popover("원문 최근 글 보기"):
            with st.spinner("불러오는 중..."):
                bs_posts = recent_posts(24, config.BUYSIDE_CHANNELS)
            if bs_posts:
                render_post_list(bs_posts)
            else:
                st.caption("최근 24시간 동안 글이 없거나 불러오지 못했습니다.")
