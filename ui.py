"""여러 화면에서 공유하는 UI 조각."""
import html
import re

import pandas as pd
import streamlit as st

_NEWS_CSS = """
<style>
.news-item { padding: 0.55rem 0; border-bottom: 1px solid rgba(128,128,128,0.18); }
.news-item a { text-decoration: none; font-weight: 600; line-height: 1.4; }
.news-item a:hover { text-decoration: underline; }
.news-meta { font-size: 0.78rem; opacity: 0.65; margin-top: 0.15rem; }
.news-sum { font-size: 0.85rem; opacity: 0.8; margin-top: 0.2rem; }
.news-tag { display: inline-block; padding: 0 0.4rem; margin-right: 0.35rem; border-radius: 0.3rem;
            background: rgba(76,154,255,0.18); font-size: 0.72rem; }
.news-item mark { background: rgba(255,200,0,0.35); color: inherit; padding: 0 1px; }
</style>
"""


def relative_time(ts: pd.Timestamp | None, now: pd.Timestamp) -> str:
    if ts is None or pd.isna(ts):
        return ""
    sec = (now - ts).total_seconds()
    if sec < 60:
        return "방금"
    if sec < 3600:
        return f"{int(sec // 60)}분 전"
    if sec < 86400:
        return f"{int(sec // 3600)}시간 전"
    return f"{ts:%m/%d %H:%M}"


def _highlight(text: str, query: str) -> str:
    text = html.escape(text)
    if not query:
        return text
    pattern = re.compile(re.escape(html.escape(query)), re.IGNORECASE)
    return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", text)


def render_news(df: pd.DataFrame, query: str = "", show_summary: bool = True) -> None:
    """뉴스 목록을 제목(링크)·출처·시각·요약 형태로 출력."""
    if df.empty:
        st.caption("표시할 뉴스가 없습니다.")
        return
    now = pd.Timestamp.now()
    items = []
    for row in df.itertuples():
        summary = (
            f'<div class="news-sum">{_highlight(row.summary, query)}</div>'
            if show_summary and row.summary else ""
        )
        items.append(
            f'<div class="news-item">'
            f'<a href="{html.escape(row.link, quote=True)}" target="_blank">{_highlight(row.title, query)}</a>'
            f'<div class="news-meta"><span class="news-tag">{html.escape(row.source)}</span>'
            f'{relative_time(row.time, now)}</div>{summary}</div>'
        )
    st.markdown(_NEWS_CSS + "".join(items), unsafe_allow_html=True)


# ── 차트 ─────────────────────────────────────────────
# 다크 테마 기준 범주형 팔레트(고정 순서). 차트당 최대 3개 시리즈
SERIES_COLORS = ["#3987e5", "#d95926", "#199e70"]
POS_COLOR, NEG_COLOR = "#3987e5", "#e66767"
_GRID = "rgba(255,255,255,0.08)"
_MUTED = "rgba(255,255,255,0.55)"


def _base_layout(fig, title: str | None, unit: str, height: int, legend: bool):
    # 제목은 맨 위, 범례는 그 아래 한 줄 (긴 범례가 제목과 겹치지 않게)
    top = (32 if title else 8) + (24 if legend else 0)
    fig.update_layout(
        title=dict(text=title, font=dict(size=14), x=0, xanchor="left", y=1, yanchor="top",
                   yref="container", pad=dict(t=8)) if title else None,
        height=height,
        margin=dict(l=8, r=8, t=top, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=_MUTED, size=12),
        hovermode="x unified",
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=11)),
    )
    fig.update_xaxes(showgrid=False, linecolor=_GRID, ticks="")
    fig.update_yaxes(gridcolor=_GRID, zeroline=False, ticksuffix=unit, ticks="")
    return fig


def line_chart(series: dict, title: str | None = None, unit: str = "", height: int = 280,
               hline: float | None = None, hline_label: str = "", decimals: int = 2):
    """여러 시리즈(동일 단위)를 한 축에 그린다. series: {이름: pd.Series}."""
    import plotly.graph_objects as go

    fig = go.Figure()
    for (name, s), color in zip(series.items(), SERIES_COLORS):
        fig.add_scatter(
            x=s.index, y=s.values, name=name, mode="lines",
            line=dict(color=color, width=2),
            hovertemplate=f"%{{y:,.{decimals}f}}{unit}",
        )
    if hline is not None:
        fig.add_hline(y=hline, line=dict(color=_MUTED, width=1, dash="dash"),
                      annotation_text=hline_label, annotation_position="top left",
                      annotation_font=dict(size=11, color=_MUTED))
    return _base_layout(fig, title, unit, height, legend=len(series) > 1)


def bar_chart(s, title: str | None = None, unit: str = "", height: int = 280, decimals: int = 0):
    """증감 막대: 양수 파랑, 음수 빨강."""
    import plotly.graph_objects as go

    fig = go.Figure(go.Bar(
        x=s.index, y=s.values,
        marker=dict(color=[POS_COLOR if v >= 0 else NEG_COLOR for v in s.values], line=dict(width=0)),
        hovertemplate=f"%{{y:,.{decimals}f}}{unit}<extra></extra>",
    ))
    fig.update_layout(bargap=0.25)
    return _base_layout(fig, title, unit, height, legend=False)


def curve_chart(df, title: str | None = None, height: int = 300):
    """수익률 곡선: 행=만기(범주), 열=시점."""
    import plotly.graph_objects as go

    fig = go.Figure()
    for col, color, dash in zip(df.columns, SERIES_COLORS, ["solid", "dot", "dash"]):
        fig.add_scatter(
            x=df.index, y=df[col], name=col, mode="lines+markers",
            line=dict(color=color, width=2, dash=dash), marker=dict(size=8),
            hovertemplate="%{y:.2f}%",
        )
    return _base_layout(fig, title, "%", height, legend=True)


def show_chart(fig) -> None:
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def kpi(label: str, s, unit: str = "", decimals: int = 2, delta_unit: str | None = None,
        delta_scale: float = 1, delta_decimals: int | None = None, freq: str = "D",
        help: str | None = None) -> None:
    """최신값 + 직전 대비 변화 + 기준 시점. 매크로 지표는 좋고 나쁨이 방향마다 달라 색은 중립."""
    if s is None or len(s) < 2:
        st.metric(label, "N/A", border=True)
        return
    last, prev, ts = s.iloc[-1], s.iloc[-2], s.index[-1]
    dd = decimals if delta_decimals is None else delta_decimals
    d = round((last - prev) * delta_scale, dd) + 0.0  # -0 표시 방지
    du = unit if delta_unit is None else delta_unit
    if (ts.hour, ts.minute) != (0, 0):
        asof = f"{ts:%m/%d %H:%M} 실시간"
    elif freq == "M":
        asof = f"{ts:%Y-%m}"
    elif freq == "Q":
        asof = f"{ts.year} Q{ts.quarter}"
    else:
        asof = f"{ts:%Y-%m-%d}"
    st.metric(
        label, f"{last:,.{decimals}f}{unit}",
        f"{d:+,.{dd}f}{du}",
        delta_color="off", delta_arrow="off" if d == 0 else "auto",
        delta_description=asof, border=True, help=help,
    )


# 가격 수익률은 국내 관례(상승 빨강, 하락 파랑). 발산형: 파랑 ↔ 중립 회색 ↔ 빨강
UP_COLOR, DOWN_COLOR = "#e66767", "#3987e5"
DIVERGING_SCALE = [[0, "#256abf"], [0.5, "#383835"], [1, "#d03b3b"]]


def returns_bar(s, title: str | None = None, height: int = 360, unit: str = "%", sort: bool = True,
                decimals: int = 2):
    """수익률(%)·변화(bp) 가로 막대. sort=True면 큰 값이 위로. 값 라벨은 막대 끝에."""
    import plotly.graph_objects as go

    s = s.dropna()
    s = s.sort_values() if sort else s.iloc[::-1]  # 가로 막대는 아래부터 그려지므로 순서 유지 시 뒤집음
    fig = go.Figure(go.Bar(
        x=s.values, y=s.index, orientation="h",
        marker=dict(color=[UP_COLOR if v >= 0 else DOWN_COLOR for v in s.values], line=dict(width=0)),
        text=[f"{v:+.{decimals}f}{unit}" for v in s.values], textposition="outside", cliponaxis=False,
        textfont=dict(size=11, color="rgba(255,255,255,0.8)"),
        hovertemplate=f"%{{y}}: %{{x:+.{decimals}f}}{unit}<extra></extra>",
    ))
    fig.update_layout(bargap=0.3, hovermode="closest")
    fig = _base_layout(fig, title, unit, height, legend=False)
    lo, hi = min(0, s.min()), max(0, s.max())
    pad = (hi - lo) * 0.22 or 0.5  # 막대 끝 라벨이 축 글자·가장자리와 겹치지 않도록
    fig.update_xaxes(showgrid=True, gridcolor=_GRID, zeroline=True, zerolinecolor=_MUTED, ticksuffix=unit,
                     range=[lo - (pad if lo < 0 else 0), hi + pad])
    fig.update_yaxes(showgrid=False, ticksuffix="")
    fig.update_layout(hovermode="closest", margin=dict(r=48))
    return fig


def treemap(df, color_range: float, height: int = 620, root: str = "S&P 500 대형주"):
    """섹터 > 종목 트리맵. 크기=시가총액, 색=수익률(%). 섹터·전체 수익률은 시총 가중 평균."""
    import plotly.graph_objects as go

    sectors = df.groupby("섹터").apply(
        lambda g: pd.Series({"시가총액": g["시가총액"].sum(),
                             "수익률": (g["수익률"] * g["시가총액"]).sum() / g["시가총액"].sum()}),
        include_groups=False,
    )
    total_ret = (df["수익률"] * df["시가총액"]).sum() / df["시가총액"].sum()
    ids = [root] + list(sectors.index) + list(df["종목"])
    parents = [""] + [root] * len(sectors) + list(df["섹터"])
    values = [df["시가총액"].sum()] + list(sectors["시가총액"]) + list(df["시가총액"])
    rets = [total_ret] + list(sectors["수익률"]) + list(df["수익률"])

    fig = go.Figure(go.Treemap(
        ids=ids, labels=ids, parents=parents, values=values, branchvalues="total",
        customdata=rets,
        marker=dict(colors=rets, colorscale=DIVERGING_SCALE, cmid=0, cmin=-color_range, cmax=color_range,
                    line=dict(width=2, color="#0e1117"),  # 타일 사이 2px 간격
                    colorbar=dict(ticksuffix="%", thickness=10, len=0.6)),
        texttemplate="<b>%{label}</b><br>%{customdata:+.2f}%",
        hovertemplate="<b>%{label}</b><br>수익률 %{customdata:+.2f}%<br>시총 $%{value:,.3s}<extra></extra>",
        textfont=dict(color="white"), pathbar=dict(visible=False),
    ))
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=8, b=0), paper_bgcolor="rgba(0,0,0,0)",
                      font=dict(color=_MUTED))
    return fig


# ── 리서치 코멘트 요약 (텔레그램) ─────────────────────
_SUMMARY_CSS = """
<style>
.sm-head { font-size:1.02rem; font-weight:600; line-height:1.55; padding:.7rem .9rem; margin-bottom:.6rem;
           border-left:3px solid #3987e5; background:rgba(57,135,229,.08); border-radius:.3rem; }
.sm-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(340px, 1fr)); gap:.7rem; }
.sm-card { display:flex; flex-direction:column; gap:.45rem; padding:.85rem 1rem; border-radius:.6rem;
           border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.03); }
.sm-top { display:flex; gap:.5rem; align-items:center; }
.sm-view { font-size:.72rem; font-weight:600; padding:.08rem .45rem; border-radius:.3rem; white-space:nowrap; }
.sm-up { background:rgba(230,103,103,.16); color:#ef8b8b; }
.sm-down { background:rgba(57,135,229,.18); color:#8fbaf0; }
.sm-mixed { background:rgba(255,255,255,.08); color:rgba(255,255,255,.7); }
.sm-title { font-weight:700; font-size:.95rem; line-height:1.35; }
.sm-body { font-size:.86rem; line-height:1.6; opacity:.9; }
.sm-src { display:flex; flex-wrap:wrap; gap:.3rem; margin-top:auto; }
.sm-src a { font-size:.74rem; text-decoration:none; padding:.08rem .45rem; border-radius:.3rem;
            border:1px solid rgba(255,255,255,.15); color:rgba(255,255,255,.75); white-space:nowrap; }
.sm-src a:hover { border-color:#3987e5; color:#8fbaf0; }
.sm-watch { font-size:.86rem; line-height:1.6; margin-top:.7rem; }
.sm-watch li { margin-left:1rem; }
</style>
"""
_VIEW_CLASS = {"금리 상승 요인": "sm-up", "금리 하락 요인": "sm-down"}


def render_summary(summary: dict) -> None:
    """한 줄 요약 → 논점 카드(금리 방향·출처 링크) → 앞으로 볼 일정. st.html이라 본문이 마크다운으로 바뀌지 않음."""
    e = html.escape
    cards = []
    for t in summary.get("themes", []):
        view = t.get("rates_view", "해당 없음")
        # 같은 채널이 같은 분에 올린 글이 여러 개면 칩은 하나만 (첫 글로 연결)
        chips = {}
        for x in t["sources"]:
            chips.setdefault(f'{x["channel"]} {x["time"][11:16]}', x["link"])
        srcs = "".join(f'<a href="{e(link, quote=True)}" target="_blank">{e(label)} ↗</a>'
                       for label, link in chips.items())
        cards.append(
            f'<div class="sm-card"><div class="sm-top"><span class="sm-view {_VIEW_CLASS.get(view, "sm-mixed")}">'
            f'{e(view)}</span><span class="sm-title">{e(t["title"])}</span></div>'
            f'<div class="sm-body">{e(t["summary"])}</div><div class="sm-src">{srcs}</div></div>')
    watch = "".join(f"<li>{e(w)}</li>" for w in summary.get("watch", []))
    st.html(
        _SUMMARY_CSS
        + (f'<div class="sm-head">{e(summary["headline"])}</div>' if summary.get("headline") else "")
        + f'<div class="sm-grid">{"".join(cards)}</div>'
        + (f'<div class="sm-watch"><b>📅 앞으로 볼 것</b><ul>{watch}</ul></div>' if watch else "")
    )


_VIEWS_CSS = """
<style>
.bv-card { display:flex; flex-direction:column; gap:.5rem; padding:.9rem 1rem; border-radius:.6rem;
           border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.03); }
.bv-tags { display:flex; flex-wrap:wrap; gap:.3rem; }
.bv-tag { font-size:.7rem; padding:.05rem .4rem; border-radius:.3rem; background:rgba(255,255,255,.08);
          color:rgba(255,255,255,.7); white-space:nowrap; }
.bv-hz { background:rgba(57,135,229,.18); color:#8fbaf0; font-weight:600; }
.bv-sec { font-size:.84rem; line-height:1.55; }
.bv-sec b { font-size:.74rem; letter-spacing:.02em; opacity:.6; display:block; margin-bottom:.1rem; }
.bv-sec ul { margin:0; padding-left:1rem; }
.bv-vs { border-left:3px solid #d95926; padding:.15rem 0 .15rem .55rem; background:rgba(217,89,38,.07); }
.bv-sig { display:grid; grid-template-columns:1fr 1fr; gap:.6rem; }
.bv-ok b { color:#4cc79a; opacity:1; } .bv-no b { color:#ef8b8b; opacity:1; }
.bv-inf { font-size:.68rem; opacity:.55; font-weight:400; }
</style>
"""


def render_views(payload: dict) -> None:
    """바이사이드 관점 카드: 논지 → 시장 반영과의 차이 → 근거 → 확인/반증 신호 → 출처."""
    e = html.escape
    ul = lambda xs: "<ul>" + "".join(f"<li>{e(x)}</li>" for x in xs) + "</ul>"
    cards = []
    for v in payload.get("views", []):
        tags = f'<span class="bv-tag bv-hz">{e(v["horizon"])}</span>' + "".join(
            f'<span class="bv-tag">{e(a)}</span>' for a in v.get("assets", []))
        vs = (f'<div class="bv-sec bv-vs"><b>시장 반영 vs 이 관점</b>{e(v["vs_market"])}</div>'
              if v.get("vs_market") else "")
        ev = f'<div class="bv-sec"><b>근거</b>{ul(v["evidence"])}</div>' if v.get("evidence") else ""
        inf = '<span class="bv-inf"> · AI 추론</span>' if v.get("signals_inferred") else ""
        sig = (f'<div class="bv-sig"><div class="bv-sec bv-ok"><b>✓ 맞다면 보일 신호{inf}</b>{ul(v["confirm"])}</div>'
               f'<div class="bv-sec bv-no"><b>✗ 틀렸다면 보일 신호{inf}</b>{ul(v["refute"])}</div></div>')
        chips = {}
        for x in v["sources"]:
            chips.setdefault(f'{x["channel"]} {x["time"][5:10].replace("-", "/")} {x["time"][11:16]}', x["link"])
        srcs = "".join(f'<a href="{e(link, quote=True)}" target="_blank">{e(label)} ↗</a>'
                       for label, link in chips.items())
        cards.append(f'<div class="bv-card"><div class="bv-tags">{tags}</div>'
                     f'<div class="sm-title">{e(v["title"])}</div><div class="sm-body">{e(v["thesis"])}</div>'
                     f'{vs}{ev}{sig}<div class="sm-src">{srcs}</div></div>')
    st.html(_SUMMARY_CSS + _VIEWS_CSS
            + (f'<div class="sm-head">{e(payload["headline"])}</div>' if payload.get("headline") else "")
            + f'<div class="sm-grid">{"".join(cards)}</div>')


def _ago(ts: pd.Timestamp, now: pd.Timestamp) -> str:
    sec = (now - ts).total_seconds()
    if sec < 3600:
        return f"{max(int(sec // 60), 0)}분 전"
    if sec < 86400:
        return f"{int(sec // 3600)}시간 전"
    return f"{int(sec // 86400)}일 전"


def render_post_list(posts: list[dict]) -> None:
    """원문 확인용: 채널·시각·첫 줄·링크만 한 줄씩."""
    now = pd.Timestamp.now()
    rows = []
    for p in posts:
        first = (p["text"].strip().splitlines() or ["(이미지만 있는 글)"])[0][:90]
        rows.append(f'<div class="news-item"><a href="{html.escape(p["link"], quote=True)}" target="_blank">'
                    f'{html.escape(first)}</a><div class="news-meta"><span class="news-tag">'
                    f'{html.escape(p["channel"])}</span>{p["time"]:%m/%d %H:%M} · {_ago(p["time"], now)}</div></div>')
    st.html(_NEWS_CSS + "".join(rows))



# ── 오늘의 이슈 (뉴스 헤드라인 AI 묶음) ──────────────
_ISSUE_CSS = """
<style>
.is-cols { display:grid; grid-template-columns:repeat(auto-fit, minmax(380px, 1fr)); gap:1.2rem; }
.is-col h4 { margin:0 0 .4rem 0; font-size:.95rem; }
.is-item { padding:.6rem 0; border-bottom:1px solid rgba(255,255,255,.08); }
.is-top { display:flex; align-items:center; gap:.45rem; }
.is-title { font-weight:700; font-size:.93rem; }
.is-tag { font-size:.7rem; padding:.05rem .4rem; border-radius:.3rem; background:rgba(255,255,255,.08);
          color:rgba(255,255,255,.7); white-space:nowrap; }
.is-bar { display:flex; align-items:center; gap:.4rem; margin:.3rem 0 .2rem; font-size:.74rem; opacity:.75; }
.is-bar span.b { height:6px; border-radius:3px; background:#3987e5; display:inline-block; }
.is-sum { font-size:.85rem; line-height:1.5; opacity:.9; }
.is-arts { margin-top:.25rem; font-size:.76rem; line-height:1.6; }
.is-arts a { text-decoration:none; color:rgba(255,255,255,.65); display:block; white-space:nowrap;
             overflow:hidden; text-overflow:ellipsis; }
.is-arts a:hover { color:#8fbaf0; }
</style>
"""


def render_issues(payload: dict, per_region: int = 5) -> None:
    """해외·국내 두 열. 이슈마다 분야 태그, 기사 수 막대(화제성), 한 줄 요약, 대표 기사 링크."""
    e = html.escape
    issues = payload.get("issues", [])
    top = max((i["count"] for i in issues), default=1)
    cols = []
    for region, flag in (("해외", "🌎"), ("국내", "🇰🇷")):
        rows = []
        for it in [i for i in issues if i["region"] == region][:per_region]:
            arts = "".join(f'<a href="{e(a["link"], quote=True)}" target="_blank">↗ {e(a["source"])} · {e(a["title"])}</a>'
                           for a in it["articles"])
            rows.append(
                f'<div class="is-item"><div class="is-top"><span class="is-title">{e(it["title"])}</span>'
                f'<span class="is-tag">{e(it["category"])}</span></div>'
                f'<div class="is-bar"><span class="b" style="width:{max(it["count"] / top * 160, 6):.0f}px"></span>'
                f'기사 {it["count"]}건</div><div class="is-sum">{e(it["summary"])}</div>'
                f'<div class="is-arts">{arts}</div></div>')
        cols.append(f'<div class="is-col"><h4>{flag} {region}</h4>{"".join(rows) or "<i>해당 이슈 없음</i>"}</div>')
    st.html(_ISSUE_CSS + f'<div class="is-cols">{"".join(cols)}</div>')


# ── 오늘의 시황 카드용 미니 차트 (x축 날짜 표시) ───────
def mini_chart(s, up: bool, days: int, height: int = 96):
    """카드 아래 작은 영역 차트. y축은 숨기고 x축에 날짜 눈금 3~4개, 마우스를 올리면 날짜·값."""
    import plotly.graph_objects as go

    color = UP_COLOR if up else DOWN_COLOR
    fill = "rgba(230,103,103,0.15)" if up else "rgba(57,135,229,0.15)"
    fig = go.Figure(go.Scatter(
        x=s.index, y=s.values, mode="lines", line=dict(color=color, width=1.6),
        fill="tozeroy", fillcolor=fill, hovertemplate="%{x|%y/%m/%d %H:%M}<br>%{y:,.2f}<extra></extra>",
    ))
    lo, hi = float(s.min()), float(s.max())
    pad = (hi - lo) * 0.08 or abs(hi) * 0.01 or 1
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=2, b=0), showlegend=False, hovermode="x",
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    fig.update_yaxes(visible=False, range=[lo - pad, hi + pad])
    fig.update_xaxes(showgrid=False, ticks="", nticks=4, tickfont=dict(size=10, color=_MUTED),
                     tickformat="%-m/%-d" if days <= 95 else "%y.%-m", linecolor=_GRID)
    return fig
