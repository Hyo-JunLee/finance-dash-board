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


# ── 리서치 코멘트 카드 (텔레그램) ─────────────────────
_POST_CSS = """
<style>
.tg-card { display:flex; flex-direction:column; gap:.5rem; height:100%; min-height:300px; padding:.9rem 1rem;
           border:1px solid rgba(255,255,255,.12); border-radius:.6rem; background:rgba(255,255,255,.03); }
.tg-head { display:flex; justify-content:space-between; align-items:center; gap:.5rem; font-size:.78rem; }
.tg-ch { padding:.1rem .5rem; border-radius:.3rem; background:rgba(57,135,229,.18); color:#8fbaf0; font-weight:600;
         white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.tg-time { opacity:.6; white-space:nowrap; }
.tg-fwd { font-size:.74rem; opacity:.55; }
.tg-img { width:100%; max-height:140px; object-fit:cover; object-position:top; border-radius:.4rem; display:block; }
.tg-body { font-size:.86rem; line-height:1.5; white-space:pre-line; opacity:.9;
           display:-webkit-box; -webkit-line-clamp:7; -webkit-box-orient:vertical; overflow:hidden; }
.tg-card details { font-size:.86rem; order:5; }  /* 미리보기 글 아래로 */
.tg-card details summary { cursor:pointer; font-size:.78rem; opacity:.7; }
.tg-card details[open] summary { font-size:0; }                       /* 펼치면 '접기'로 */
.tg-card details[open] summary::after { content:"접기"; font-size:.78rem; }
.tg-card details[open] + .tg-body-clamped { display:none; }
.tg-full { white-space:pre-line; line-height:1.5; margin-top:.4rem; opacity:.9; }
.tg-foot { margin-top:auto; font-size:.8rem; order:6; }
.tg-foot a { text-decoration:none; }
</style>
"""


def _ago(ts: pd.Timestamp, now: pd.Timestamp) -> str:
    sec = (now - ts).total_seconds()
    if sec < 3600:
        return f"{max(int(sec // 60), 0)}분 전"
    if sec < 86400:
        return f"{int(sec // 3600)}시간 전"
    return f"{int(sec // 86400)}일 전"


def render_post_cards(posts: list[dict], full_text: bool = True) -> None:
    """텔레그램 글 카드를 가로로. 긴 글은 앞부분만 보이고 '전체 보기'로 펼친다.

    st.markdown은 본문의 '- '·'#' 등을 마크다운으로 바꿔버려 원문이 깨지므로 st.html로 그린다.
    """
    now = pd.Timestamp.now()
    cols = st.columns(max(len(posts), 1), gap="small")
    for col, p in zip(cols, posts):
        raw = p["text"] if full_text else "\n".join(p["text"].splitlines()[:4])[:200]  # 공개 배포: 앞부분만
        text = html.escape(raw) if raw else "<i>(이미지만 있는 글)</i>"
        long = full_text and (len(raw) > 220 or raw.count("\n") > 6)
        body = (f'<details><summary>전체 보기</summary><div class="tg-full">{text}</div></details>'
                f'<div class="tg-body tg-body-clamped">{text}</div>') if long else f'<div class="tg-body">{text}</div>'
        img = (f'<a href="{html.escape(p["link"], quote=True)}" target="_blank">'
               f'<img class="tg-img" src="{html.escape(p["photo"], quote=True)}" loading="lazy"></a>') if p["photo"] else ""
        fwd = f'<div class="tg-fwd">↪ {html.escape(p["forwarded"])}에서 전달</div>' if p.get("forwarded") else ""
        with col:
            st.html(
            _POST_CSS +
            f'<div class="tg-card"><div class="tg-head"><span class="tg-ch">{html.escape(p["channel"])}</span>'
            f'<span class="tg-time">{p["time"]:%m/%d %H:%M} · {_ago(p["time"], now)}</span></div>'
            f'{fwd}{img}{body}'
            f'<div class="tg-foot"><a href="{html.escape(p["link"], quote=True)}" target="_blank">원문 보기 ↗</a></div></div>'
            )
