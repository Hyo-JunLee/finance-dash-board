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
.news-topic { display: inline-block; padding: 0 0.4rem; margin-left: 0.35rem; border-radius: 999px;
              border: 1px solid rgba(255,255,255,0.18); font-size: 0.68rem; opacity: 0.8; }
.news-more { margin-top: 0.25rem; font-size: 0.8rem; }
.news-more summary { cursor: pointer; opacity: 0.7; }
.news-more ul { margin: 0.25rem 0 0 1rem; padding: 0; }
.news-more li { margin: 0.15rem 0; }
.news-more a { font-weight: 400 !important; }
.news-meta-i { font-size: 0.72rem; opacity: 0.55; }
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


def render_news(df: pd.DataFrame, query: str = "", show_summary: bool = True, others: dict | None = None) -> None:
    """뉴스 목록: 제목(링크)·출처·시각·주제 칩·요약. others={묶음 번호: 같은 사건의 다른 기사 df}면 '외 N건' 펼치기."""
    if df.empty:
        st.caption("표시할 뉴스가 없습니다.")
        return
    now = pd.Timestamp.now()
    e = html.escape
    items = []
    for row in df.itertuples():
        summary = (
            f'<div class="news-sum">{_highlight(row.summary, query)}</div>'
            if show_summary and row.summary else ""
        )
        topics = getattr(row, "group_topics", None) or getattr(row, "topics", None) or []
        chips = "".join(f'<span class="news-topic">{e(t)}</span>' for t in topics[:3])
        more = ""
        rest = (others or {}).get(getattr(row, "group", None))
        if rest is not None and len(rest):
            srcs = list(dict.fromkeys(rest["source"]))
            links = "".join(f'<li><a href="{e(r.link, quote=True)}" target="_blank">{e(r.source)} · {_highlight(r.title, query)}</a>'
                            f' <span class="news-meta-i">{relative_time(r.time, now)}</span></li>' for r in rest.itertuples())
            more = (f'<details class="news-more"><summary>같은 사건 외 {len(rest)}건 · {e(", ".join(srcs[:4]))}'
                    f'{" 등" if len(srcs) > 4 else ""}</summary><ul>{links}</ul></details>')
        items.append(
            f'<div class="news-item">'
            f'<a href="{e(row.link, quote=True)}" target="_blank">{_highlight(row.title, query)}</a>'
            f'<div class="news-meta"><span class="news-tag">{e(row.source)}</span>'
            f'{relative_time(row.time, now)}{chips}</div>{summary}{more}</div>'
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
        help: str | None = None, release: dict | None = None) -> None:
    """최신값 + 직전 대비 변화 + 기준 시점. 매크로 지표는 좋고 나쁨이 방향마다 달라 색은 중립.
    release(발표 현황 한 줄)를 주면 카드 안에 발표 시각·다음 발표 D-day·예상 대비 배지를 붙이고,
    예상보다 높으면 빨강 / 낮으면 파랑 테두리."""
    if release is not None and s is not None and len(s) >= 2:
        key = "kpi_" + re.sub(r"\W", "_", label)
        with st.container(border=True, key=key):
            _kpi_metric(label, s, unit, decimals, delta_unit, delta_scale, delta_decimals, freq, help, border=False)
            st.html(release_badge_html(release))
        tone = release_tone(release)
        if tone:
            st.html(f"<style>.st-key-{key} {{ border-color:{UP_COLOR if tone == 'up' else DOWN_COLOR} !important; }}</style>")
        return
    _kpi_metric(label, s, unit, decimals, delta_unit, delta_scale, delta_decimals, freq, help, border=True)


def _kpi_metric(label, s, unit, decimals, delta_unit, delta_scale, delta_decimals, freq, help, border) -> None:
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
        delta_description=asof, border=border, help=help,
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
def session_chart(s, up: bool, start, end, base: float | None = None, height: int = 96):
    """1일 장중 차트: 가로축 = 장 시작~마감 고정, 지금까지만 선, 현재 위치 세로 점선, 전일 종가 가로 점선."""
    import plotly.graph_objects as go

    color = UP_COLOR if up else DOWN_COLOR
    fill = "rgba(230,103,103,0.15)" if up else "rgba(57,135,229,0.15)"
    vals = list(s.values) + ([base] if base is not None else [])
    lo, hi = float(min(vals)), float(max(vals))
    pad = (hi - lo) * 0.1 or abs(hi) * 0.01 or 1
    fig = go.Figure(go.Scatter(
        x=s.index, y=s.values, mode="lines", line=dict(color=color, width=1.6), fill="tozeroy", fillcolor=fill,
        hovertemplate="%{x|%H:%M}<br>%{y:,.2f}<extra></extra>"))
    if base is not None:
        fig.add_hline(y=base, line=dict(color=_MUTED, width=1, dash="dot"))
    now = pd.Timestamp.now(tz="Asia/Seoul").tz_localize(None)
    last = s.index[-1]
    live = start <= now <= end and now - last <= pd.Timedelta(minutes=30)
    if live:  # 진행 중: 현재 위치 표시 + 남은 시간 비율
        fig.add_vline(x=last, line=dict(color="rgba(255,255,255,0.55)", width=1, dash="dot"))
        done = (last - start) / (end - start) * 100
        fig.add_annotation(x=last, y=1, yref="paper", yanchor="bottom", showarrow=False,
                           text=f"지금 {last:%H:%M} · {done:.0f}%", font=dict(size=9, color="rgba(255,255,255,0.75)"),
                           xanchor="right" if done > 70 else "left")
    mid = start + (end - start) / 2
    # 양 끝 눈금은 그림 영역 밖으로 나가 잘리므로, 시각 라벨을 안쪽 정렬 주석으로 직접 단다 (시작 · 가운데 · 마감)
    for x, anchor, text in ((0, "left", f"{start:%H:%M} 시작"), (0.5, "center", f"{mid:%H:%M}"),
                            (1, "right", f"{end:%H:%M} 마감")):
        fig.add_annotation(x=x, xref="paper", y=0, yref="paper", yanchor="top", xanchor=anchor, showarrow=False,
                           text=text, font=dict(size=10, color=_MUTED), yshift=-2)
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=12 if live else 2, b=16), showlegend=False,
                      hovermode="x", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    fig.update_yaxes(visible=False, range=[lo - pad, hi + pad])
    fig.update_xaxes(showgrid=False, ticks="", showticklabels=False, linecolor=_GRID, range=[start, end])
    return fig


def mini_chart(s, up: bool, days: int, height: int = 96):
    """카드 아래 작은 영역 차트. y축은 숨기고 x축에 날짜 눈금 3~4개, 마우스를 올리면 날짜·값."""
    import plotly.graph_objects as go

    color = UP_COLOR if up else DOWN_COLOR
    fill = "rgba(230,103,103,0.15)" if up else "rgba(57,135,229,0.15)"
    intraday = days <= 7
    # 장중(1일·1주): 밤·주말처럼 거래 없는 시간이 긴 직선으로 이어지지 않게 순번 축에 그리고 눈금만 시각/날짜로
    x = list(range(len(s))) if intraday else s.index
    fig = go.Figure(go.Scatter(
        x=x, y=s.values, mode="lines", line=dict(color=color, width=1.6), fill="tozeroy", fillcolor=fill,
        customdata=[f"{t:%m/%d %H:%M}" for t in s.index],
        hovertemplate="%{customdata}<br>%{y:,.2f}<extra></extra>",
    ))
    lo, hi = float(s.min()), float(s.max())
    pad = (hi - lo) * 0.08 or abs(hi) * 0.01 or 1
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=2, b=0), showlegend=False, hovermode="x",
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    fig.update_yaxes(visible=False, range=[lo - pad, hi + pad])
    axis = dict(showgrid=False, ticks="", tickfont=dict(size=10, color=_MUTED), linecolor=_GRID)
    if intraday:
        if days <= 1:  # 시각 눈금 4개
            pos = [round(i * (len(s) - 1) / 4) for i in range(1, 4)]
            labels = [f"{s.index[i]:%H:%M}" for i in pos]
        else:  # 거래일(45분 넘게 끊긴 곳에서 새 세션)마다 첫 지점에 날짜 — 미국장은 한국 시간으로 자정을 넘겨도 한 세션
            gaps = pd.Series(s.index).diff() > pd.Timedelta(minutes=45)
            pos = [i for i in range(len(s)) if i == 0 or gaps.iloc[i]]
            labels = [f"{s.index[i]:%-m/%-d}" for i in pos]
            if len(pos) > 1:
                pos, labels = pos[1:], labels[1:]  # 맨 왼쪽 끝 눈금은 잘려서 생략
        fig.update_xaxes(**axis, tickmode="array", tickvals=pos, ticktext=labels, tickangle=0, range=[0, len(s) - 1])
    else:
        fig.update_xaxes(**axis, nticks=4, tickformat="%-m/%-d" if days <= 95 else "%y.%-m")
    return fig


def render_brief_cards(cards: dict, kinds: tuple) -> None:
    """한화 ★ 시황 원문 카드 (종류별 한 칸): 숫자 줄 → 해설 → 원문 링크. 공개 배포에선 해설을 싣지 않는다 (저작권)."""
    import config

    def esc(text: str) -> str:
        return text.replace("$", "\\$").replace("~", "\\~")  # 달러·물결이 수식·취소선으로 바뀌지 않게

    for col, k in zip(st.columns(len(kinds), gap="small"), kinds):
        with col:
            c = cards.get(k)
            if c is None:
                st.markdown(f"**{k}**")
                st.caption("아직 게시 전")
                continue
            st.markdown(f"**{k}**  \n" + "  \n".join(f"`{esc(n)}`" for n in c["numbers"]))
            if not config.PUBLIC_MODE:
                st.markdown(f"<small>{esc(c['comment'])}</small>", unsafe_allow_html=True)
            st.caption(f"[원문 · {c['time']:%H:%M}]({c['link']})")



# ── 시세 카드 맥락 (1년 범위 막대·기간 변화·이례적 변동) — 오늘의 시황·미국 증시 공용 ──
Z_ALERT = 2.0  # 오늘 변화가 1년 일간 변동(표준편차)의 이 배수를 넘으면 '이례적'
CONTEXT_CSS = """<style>
.cx { font-size:.74rem; line-height:1.35; margin-top:-.35rem; }
.cx-row { display:flex; justify-content:space-between; opacity:.65; }
.cx-pos { font-weight:700; opacity:1; }
.cx-track { position:relative; height:4px; border-radius:2px; margin:.2rem 0 .35rem;
            background:linear-gradient(90deg, rgba(57,135,229,.55), rgba(255,255,255,.15), rgba(230,103,103,.55)); }
.cx-dot { position:absolute; top:-3px; width:8px; height:10px; border-radius:2px; background:#fff; }
.cx-per { opacity:.85; display:flex; justify-content:space-between; gap:.3rem; white-space:nowrap; font-size:.7rem; }
.cx-alert { margin-top:.3rem; color:#f0a35a; font-weight:700; }
</style>"""


def short_value(v: float, kind: str) -> str:
    if kind in ("rate", "spread"):
        return f"{v:.2f}"
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.2f}"


def context_html(snap: dict) -> str:
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
        f'<div class="cx"><div class="cx-row"><span>1년 저 {short_value(lo, snap["kind"])}</span>'
        f'<span class="cx-pos">{pos:.0f}%</span><span>고 {short_value(hi, snap["kind"])}</span></div>'
        f'<div class="cx-track"><div class="cx-dot" style="left:calc({pos:.1f}% - 4px)"></div></div>'
        f'<div class="cx-per">{per}</div>{badge}</div>'
    )


def index_card(card_key: str, title: str, snap: dict, days: int, spark: pd.Series | None = None,
               session: tuple | None = None) -> None:
    """지수 카드 (국내·미국 증시 공용): 현재값·전일 대비, 1년 범위 막대, 1주·1개월·YTD·1년 변화, 미니 차트.
    snap: last·chg·pct·series + card_context 결과(range·z·periods). 오늘 변화가 이례적이면 테두리를 주황색으로.
    spark: 차트 기간 1일·1주일 때 장중 시계열 (없으면 일봉에서 days일). 1일 차트 색은 전일 종가 대비 방향.
    session: 1일일 때 (장 시작, 장 마감) 한국 시각 → 가로축 고정·현재 위치 표시."""
    s = snap["series"]
    base = s[s.index <= s.index[-1] - pd.Timedelta(days=365)]
    periods = dict(snap["periods"])
    if len(base):
        periods["1년"] = (float(s.iloc[-1]) / float(base.iloc[-1]) - 1) * 100
    chg = snap["chg"]
    with st.container(border=True, key=card_key):
        st.metric(title, f"{snap['last']:,.2f}", f"{chg:+,.2f} ({snap['pct']:+.2f}%)",
                  delta_color="red" if chg > 0 else "blue" if chg < 0 else "gray",
                  help="1년 범위 막대: 최근 1년 최저\\~최고 중 현재 위치 · 기간 변화는 % · 미니 차트 색은 선택한 기간 동안의 방향")
        st.html(context_html({**snap, "periods": periods}))
        intraday = spark is not None
        if not intraday:
            spark = s[s.index >= pd.Timestamp.now() - pd.Timedelta(days=days)]
        if len(spark) > 1:
            up = chg >= 0 if intraday and days <= 1 else spark.iloc[-1] >= spark.iloc[0]
            if intraday and days <= 1 and session:  # 1일: 장 시작~마감 고정 축 + 현재 위치 + 전일 종가 선
                show_chart(session_chart(spark, up, *session, base=snap["last"] - chg))
            else:
                show_chart(mini_chart(spark, up, days))
        elif intraday:
            st.caption("장중 데이터 없음")
    if snap["z"] == snap["z"] and abs(snap["z"]) >= Z_ALERT:
        st.html(f"<style>.st-key-{card_key} {{ border-color:#f0a35a !important; }}</style>")


INVESTOR_COLORS = {"외국인": "#d95926", "기관": "#199e70", "개인": "rgba(160,160,160,0.75)"}


def investor_chart(df, title: str | None = None, height: int = 260):
    """투자자별 일별 순매수(억원) 묶음 막대. df: 행=날짜, 열=외국인·기관·개인."""
    import plotly.graph_objects as go

    fig = go.Figure()
    x = [f"{d:%m/%d}" for d in df.index]  # 휴장일 빈칸 없이
    for name in ("외국인", "기관", "개인"):
        if name in df:
            fig.add_bar(x=x, y=df[name], name=name, marker=dict(color=INVESTOR_COLORS[name], line=dict(width=0)),
                        hovertemplate=f"{name} %{{y:+,.0f}}억<extra></extra>")
    fig.update_layout(barmode="group", bargap=0.25, bargroupgap=0.05)
    fig = _base_layout(fig, title, "", height, legend=True)
    fig.update_xaxes(nticks=8)
    fig.update_yaxes(zeroline=True, zerolinecolor=_MUTED, tickformat=",.0f")
    return fig


# ── 경제지표 발표 현황 보드 (표 대신 카드형 행) ─────────────────────────
_WEEK = "월화수목금토일"
RELEASE_CSS = """<style>
.rb { container-type:inline-size; font-size:.84rem; }
.rb-head, .rb-row { display:grid; grid-template-columns: 1.15fr 2.6fr 1fr 1fr; gap:.4rem 1rem; align-items:center; }
.rb-head { font-size:.72rem; opacity:.55; padding:0 .8rem .35rem; }
.rb-row { padding:.6rem .8rem; margin-bottom:.4rem; border-radius:.55rem; background:rgba(255,255,255,.035);
          border:1px solid rgba(255,255,255,.07); border-left:3px solid rgba(255,255,255,.18); }
.rb-row.up { border-left-color:#e66767; } .rb-row.down { border-left-color:#3987e5; }
.rb-name { font-weight:700; font-size:.92rem; line-height:1.25; }
.rb-sub { font-size:.72rem; opacity:.55; margin-top:.1rem; }
.rb-ms { display:flex; flex-wrap:wrap; gap:.35rem 1.4rem; }
.rb-m { display:flex; align-items:baseline; flex-wrap:wrap; gap:.15rem .45rem; }
.rb-lab { font-size:.7rem; opacity:.6; min-width:2.6rem; }
.rb-val { font-weight:700; font-size:1.05rem; font-variant-numeric:tabular-nums; }
.rb-ctx { font-size:.72rem; opacity:.6; font-variant-numeric:tabular-nums; }
.rb-pill { font-size:.7rem; font-weight:700; padding:.05rem .45rem; border-radius:999px; white-space:nowrap;
           background:rgba(255,255,255,.08); color:rgba(255,255,255,.75); }
.rb-pill.up { background:rgba(230,103,103,.16); color:#ef8a8a; } .rb-pill.down { background:rgba(57,135,229,.18); color:#7fb1f0; }
.rb-pill.soon { background:rgba(240,163,90,.18); color:#f0a35a; } .rb-pill.new { background:rgba(25,158,112,.2); color:#4fd1a1; }
.rb-when { font-variant-numeric:tabular-nums; line-height:1.35; }
.rb-when .rb-sub { margin-top:0; }
.rb.dense .rb-head, .rb.dense .rb-row { grid-template-columns: 1.05fr 1.9fr 1.05fr; gap:.3rem .8rem; }
.rb.dense .rb-row { padding:.45rem .7rem; margin-bottom:.3rem; }
.rb.dense .rb-name { font-size:.86rem; } .rb.dense .rb-val { font-size:.98rem; }
.rb.dense .rb-when { font-size:.78rem; }
@container (max-width: 720px) {
  .rb:not(.dense) .rb-head { display:none; }
  .rb:not(.dense) .rb-row { grid-template-columns: 1fr 1fr; }
  .rb:not(.dense) .rb-row .rb-name-cell, .rb:not(.dense) .rb-row .rb-ms { grid-column: 1 / -1; }
}
@container (max-width: 430px) {
  .rb.dense .rb-head { display:none; }
  .rb.dense .rb-row { grid-template-columns: 1fr 1fr; }
  .rb.dense .rb-row .rb-ms { grid-column: 1 / -1; grid-row: 2; }
}
</style>"""


FLASH_PILL = ('<span class="rb-pill soon" title="FRED 반영 전 Nasdaq 발표치 (FRED에 올라오면 자동으로 바뀜)">'
              '⚡속보</span>')


def _esc(x) -> str:
    return html.escape(str(x))


def _dday(d, today) -> tuple[str, str]:
    if d is None or pd.isna(d):
        return "-", ""
    d = pd.Timestamp(d).date()
    n = (d - today).days
    tag = "오늘" if n == 0 else f"D-{n}" if n > 0 else f"D+{-n}"
    return f"{d:%m/%d}({_WEEK[d.weekday()]})", f'<span class="rb-pill{" soon" if 0 <= n <= 3 else ""}">{tag}</span>'


def _released(t, notime: bool, today) -> tuple[str, str]:
    if t is None or pd.isna(t):
        return "-", ""
    t = pd.Timestamp(t)
    when = f"{t:%m/%d}({_WEEK[t.weekday()]})" + ("" if notime else f" {t:%H:%M}")
    new = '<span class="rb-pill new">🆕 최근</span>' if 0 <= (today - t.date()).days <= 3 else ""
    return when, new


def _tone(v: float, has_cons: bool) -> str:
    return "" if not has_cons or abs(v) < 1e-9 else "up" if v > 0 else "down"


def release_badge_html(row: dict, today=None) -> str:
    """카드용 한 줄: 발표 시각 · 다음 발표 D-day · 예상 대비 배지."""
    today = today or pd.Timestamp.now().date()
    when, new = _released(row.get("발표"), row.get("_notime", True), today)
    nxt, dd = _dday(row.get("_next"), today)
    has = row.get("예상", "-") != "-"
    tone = _tone(row.get("_surprise", 0.0), has)
    cons = (f'<span class="rb-ctx">예상 {_esc(row["예상"])}</span> '
            f'<span class="rb-pill {tone}">{_esc(row["서프라이즈"])}</span>') if has else ""
    if row.get("_flash"):
        cons = (f'{FLASH_PILL}<span class="rb-ctx">속보 {_esc(row["최신"])} ({_esc(row["기준"])}분)</span> ' + cons)
    return (f'<div class="rb" style="margin-top:-.4rem"><div class="rb-ms" style="gap:.25rem .6rem">'
            f'{cons}</div><div class="rb-sub" style="margin-top:.3rem">📅 {when} 발표 {new} · 다음 {nxt} {dd}</div></div>')


def release_tone(row: dict | None) -> str:
    if not row:
        return ""
    return _tone(row.get("_surprise", 0.0), row.get("예상", "-") != "-")


def release_board(df, note: str = "", today=None, dense: bool = False) -> None:
    """발표 현황 보드: 지표 한 줄에 전월비·전년비(같은 지표)를 함께, 값·예상·이전·상회/하회 배지, 발표 시각·다음 발표 D-day.
    df: data.econ_calendar._table 결과 (지표명 끝이 ' 전월비'/' 전년비'면 한 줄로 묶음)."""
    today = today or pd.Timestamp.now().date()
    df = df.reset_index(drop=True)
    base = df["지표"].str.replace(r" (전월비|전년비)$", "", regex=True)
    rows = []
    for name in dict.fromkeys(base):  # 원래 순서 유지
        g = df[base == name]
        first = g.iloc[0]
        measures, tones = [], []
        for _, r in g.iterrows():
            lab = r["지표"][len(name):].strip()
            has = r["예상"] != "-"
            tone = _tone(r["_surprise"], has)
            tones.append(tone)
            cons = (f'<span class="rb-ctx">예상 {_esc(r["예상"])}</span>'
                    f'<span class="rb-pill {tone}">{_esc(r["서프라이즈"])}</span>') if has else ""
            measures.append(
                f'<div class="rb-m">{f"<span class=rb-lab>{_esc(lab)}</span>" if lab else ""}<span class="rb-val">{_esc(r["최신"])}</span>'
                f'{FLASH_PILL if r.get("_flash") else ""}'
                f'{cons}<span class="rb-ctx">이전 {_esc(r["이전"])}</span></div>')
        row_tone = next((t for t in tones if t), "")
        # 발표 시각: 묶음 중 가장 최근
        latest = g.loc[pd.to_datetime(g["발표"]).fillna(pd.Timestamp(0)).idxmax()]
        when, new = _released(latest["발표"], bool(latest.get("_notime", True)), today)
        nxt, dd = _dday(first.get("_next"), today)
        period = " · ".join(dict.fromkeys(g["기준"]))
        name_cell = (f'<div class="rb-name-cell"><div class="rb-name">{_esc(name)}</div>'
                     f'<div class="rb-sub">{_esc(period)} 기준</div></div>')
        if dense:  # 좁은 칸: 발표·다음 발표를 한 칸에 위아래로
            when_cells = (f'<div class="rb-when">{when} {new}<div class="rb-sub">다음 {nxt} {dd}</div></div>')
        else:
            when_cells = (f'<div class="rb-when">{when} {new}<div class="rb-sub">발표 (KST)</div></div>'
                          f'<div class="rb-when">{nxt} {dd}<div class="rb-sub">다음 발표</div></div>')
        rows.append(f'<div class="rb-row {row_tone}">{name_cell}<div class="rb-ms">{"".join(measures)}</div>{when_cells}</div>')
    head = ('<div class="rb-head"><div>지표</div><div>결과 · 예상 대비</div>'
            + ('<div>발표 (KST) · 다음</div>' if dense else '<div>발표</div><div>다음</div>') + '</div>')
    st.html(RELEASE_CSS + f'<div class="rb{" dense" if dense else ""}">{head}{"".join(rows)}</div>')
    warns = df.loc[df["_warn"] != "", ["지표", "_warn"]]
    st.caption(
        "최신: FRED · 예상(컨센서스): Nasdaq 경제 캘린더 · 상회/하회는 같은 출처(Nasdaq)의 실제치와 예상치로 판정 "
        "(빨강 = 예상보다 높음, 파랑 = 낮음, 좋고 나쁨 판단 아님) · 🆕 = 최근 3일 안 발표 · 주황 D-day = 3일 안 발표 예정 · "
        "⚡속보 = FRED 반영 전 Nasdaq 발표치(보통 발표 후 5분 안, FRED에 올라오면 자동 교체)"
        + (f" · {note}" if note else "")
        + "".join(f"  \n⚠ {n}: {m}" for n, m in zip(warns["지표"], warns["_warn"]))
    )


# ── 🧠 최근 발표 해석 (data/release_insight.py 결과) ───────────────────
INSIGHT_CSS = """<style>
.ri { border:1px solid rgba(255,255,255,.1); border-left:3px solid #9b7be0; border-radius:.6rem; padding:.75rem .9rem;
      background:rgba(155,123,224,.06); font-size:.86rem; line-height:1.5; }
.ri-top { font-size:.74rem; opacity:.65; margin-bottom:.15rem; }
.ri-head { font-weight:700; font-size:1rem; margin-bottom:.45rem; }
.ri-row { display:grid; grid-template-columns: 5.6rem 1fr; gap:.6rem; padding:.18rem 0; }
.ri-lab { font-size:.72rem; font-weight:700; opacity:.8; padding-top:.12rem; }
.ri-close { margin-top:.3rem; padding-top:.45rem; border-top:1px dashed rgba(255,255,255,.12); }
.ri-age { font-size:.68rem; font-weight:700; padding:.02rem .4rem; border-radius:999px; background:rgba(155,123,224,.2);
          color:#c2a9f5; margin-left:.2rem; }
@media (max-width: 640px) { .ri-row { grid-template-columns: 1fr; gap:.05rem; } }
</style>"""
_EVENT_KO = {"cpi": "CPI", "core cpi": "근원 CPI", "pce price index": "PCE", "core pce price index": "근원 PCE",
             "nonfarm payrolls": "고용보고서", "unemployment rate": "실업률", "gdp": "GDP", "retail sales": "소매판매",
             "ppi": "PPI", "core ppi": "근원 PPI", "jolts job openings": "JOLTS", "ism manufacturing pmi": "ISM 제조업",
             "ism non-manufacturing pmi": "ISM 서비스업"}


def insight_age(ins: dict, now=None) -> int:
    """발표 후 지난 날 수 (한국 날짜 기준)."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    t = _dt.strptime(ins["slot"], "%Y-%m-%d %H:%M ET").replace(tzinfo=ZoneInfo("America/New_York"))
    now = now or pd.Timestamp.now(tz="Asia/Seoul")
    return (now.date() - t.astimezone(ZoneInfo("Asia/Seoul")).date()).days


def _age_label(n: int) -> str:
    return "오늘" if n <= 0 else "어제" if n == 1 else f"{n}일 전"


def _insight_html(ins: dict, top: bool = True) -> str:
    from data.release_insight import SECTIONS
    keys = list(dict.fromkeys(_EVENT_KO[e.lower()] for e in ins.get("events", []) if e.lower() in _EVENT_KO))
    rows = "".join(f'<div class="ri-row"><div class="ri-lab">{lab}</div><div>{_esc(ins.get(k, ""))}</div></div>'
                   for k, lab in SECTIONS if ins.get(k))
    if ins.get("close"):
        rows += (f'<div class="ri-row ri-close"><div class="ri-lab">🔔 장 마감 반응</div>'
                 f'<div>{_esc(ins["close"]["text"])}</div></div>')
    age = f'<span class="ri-age">{_age_label(insight_age(ins))}</span>'
    head = (f'<div class="ri-top">🧠 최근 발표 해석 · {_esc(ins["when_kst"])} (KST) 발표 {age} · {_esc(" · ".join(keys))}</div>'
            if top else f'<div class="ri-top">{_esc(ins["when_kst"])} (KST) {age} · {_esc(" · ".join(keys))}</div>')
    return f'<div class="ri">{head}<div class="ri-head">{_esc(ins.get("headline", ""))}</div>{rows}</div>'


INSIGHT_FRESH_DAYS = 7  # 이보다 오래된 해석은 맨 위 카드로 보이지 않고 '지난 발표 해석'에만


def render_release_insights(items: list[dict], previous: int = 5) -> None:
    """가장 최근 해석 카드(7일 이내) + 지난 해석 접어 두기. items: 최신순."""
    if not items:
        return
    fresh = insight_age(items[0]) < INSIGHT_FRESH_DAYS
    if fresh:
        st.html(INSIGHT_CSS + _insight_html(items[0]))
    rest = items[1:1 + previous] if fresh else items[:previous]
    if rest:
        with st.expander(f"지난 발표 해석 {len(rest)}개" + ("" if fresh else " (최근 7일 안 중요 지표 발표 없음)")):
            st.html(INSIGHT_CSS + "".join(_insight_html(x, top=False) + "<div style='height:.5rem'></div>" for x in rest))
    st.caption("AI(Claude) 해석 — 숫자는 코드가 계산해 넘긴 값만 쓰도록 대조 검증 · 발표 30분 뒤 반응으로 해석, "
               "장 마감(한국 다음 날 아침) 반응을 한 줄 덧붙임 · 투자 판단의 근거가 아닌 참고용")


def rate_card(card_key: str, title: str, snap: dict, days: int = 90) -> None:
    """금리 카드 (채권 화면 요약 줄): 수준·전일 대비 bp, 1년 범위 막대·기간 변화(bp), 미니 차트.
    snap: last·chg·series·kind(rate|spread) + card_context 결과."""
    s, chg = snap["series"], snap["chg"]
    bp = round(chg * 100, 1) + 0.0
    value = f"{snap['last']:+.2f}%p" if snap["kind"] == "spread" else f"{snap['last']:.2f}%"
    with st.container(border=True, key=card_key):
        st.metric(title, value, f"{bp:+.1f}bp" if bp else "0.0bp",
                  delta_color="red" if bp > 0 else "blue" if bp < 0 else "gray", delta_arrow="auto" if bp else "off",
                  help="1년 범위 막대: 최근 1년 최저~최고 중 현재 위치 · 기간 변화는 bp · 미니 차트는 최근 3개월")
        st.html(context_html(snap))
        spark = s[s.index >= pd.Timestamp.now() - pd.Timedelta(days=days)]
        if len(spark) > 1:
            show_chart(mini_chart(spark, spark.iloc[-1] >= spark.iloc[0], days, height=80))
    if snap["z"] == snap["z"] and abs(snap["z"]) >= Z_ALERT:
        st.html(f"<style>.st-key-{card_key} {{ border-color:#f0a35a !important; }}</style>")


# ── 화면 맨 위 원문 박스: 미국 증시(주식) · 채권(미 국채·국고채) ─────────────
def _brief_esc(text: str) -> str:
    return text.replace("$", "\\$").replace("~", "\\~")


def _stale_note(day, today, when: str) -> str:
    return "" if day == today else f" :gray[(직전 거래일 · 오늘 것은 보통 {when} 게시)]"


def render_us_brief() -> None:
    """미국 증시 맨 위: 평일 한화 ★ 주식 아침 시황(전날 미국장), 토·일 @ehdwl '한국 증시 관련 수치'(금요일 미국장)."""
    import config
    from datetime import datetime
    from data.morning_brief import load_brief, load_latest_morning, plan
    now = datetime.now()
    kind, day = plan(now)
    with st.container(border=True):
        if kind == "hanwha":
            m = load_latest_morning(now.date())
            card = m["cards"].get("주식")
            if not card:
                st.markdown("**🌙 미국장 마감 시황**")
                st.caption("최근 5일 안에 한화 주식 아침 시황 원문이 없습니다"
                           + (f" · :orange[채널 읽기 실패: {m['error'][:80]}]" if m.get("error") else ""))
                return
            st.markdown(f"**🌙 미국장 마감 시황** · {m['day']:%m/%d} 아침"
                        + _stale_note(m["day"], now.date(), "08:05\\~08:50") + " · 한화투자증권 리서치 (원문)")
            render_brief_cards({"주식": card}, ("주식",))
            return
        f = load_brief("ficc", day).get("ficc")
        eq = (f or {}).get("equity")
        if not eq:
            st.markdown("**🌙 주말 미국장 정리**")
            st.caption("@ehdwl 토요일 글 대기 중 (보통 토요일 06:30경 게시) · 아래 지수 카드 참고")
            return
        st.markdown(f"**🌙 주말 미국장 정리** · {day:%m/%d}(토) · 금요일 미국장 · @ehdwl (원문)")
        st.markdown(f"**{_brief_esc(eq['title'])}**")
        if not config.PUBLIC_MODE:
            st.markdown(f"<small>{_brief_esc(eq['numbers'])}</small>", unsafe_allow_html=True)
            if eq["rest"]:
                with st.expander("월요일 한국 증시 전망 더 보기"):
                    for para in eq["rest"]:
                        st.markdown(f"<small>{_brief_esc(para)}</small>", unsafe_allow_html=True)
        st.caption(f"[원문 · {f['time']:%m/%d %H:%M}]({f['link']})")


def render_bond_briefs() -> None:
    """채권 맨 위: 미 국채(한화 ★ 채권 아침 시황, 토·일은 @ehdwl FICC '국채금리' 문단) | 국고채(한화 ★ 채권 마감 시황)."""
    import config
    from datetime import datetime
    from data.morning_brief import load_brief, load_closing, load_latest_morning, plan
    now = datetime.now()
    kind, day = plan(now)
    with st.container(border=True):
        left, right = st.columns(2, gap="large")
        with left:
            if kind == "hanwha":
                m = load_latest_morning(now.date())
                card = m["cards"].get("채권")
                if card:
                    st.markdown(f"**🌙 미 국채 · 아침 시황** · {m['day']:%m/%d}"
                                + _stale_note(m["day"], now.date(), "08:05\\~08:50") + " · 한화 (원문)")
                    render_brief_cards({"채권": card}, ("채권",))
                else:
                    st.markdown("**🌙 미 국채 · 아침 시황**")
                    st.caption("최근 5일 안에 한화 채권 아침 시황 원문이 없습니다")
            else:
                f = load_brief("ficc", day).get("ficc")
                rates = [p for p in (f or {}).get("first", []) if p.startswith("국채금리")]
                st.markdown(f"**🌙 미 국채 · 주말 정리** · {day:%m/%d}(토) · 금요일 미국장 · @ehdwl (원문)")
                if rates:
                    if not config.PUBLIC_MODE:
                        st.markdown(f"<small>{_brief_esc(rates[0])}</small>", unsafe_allow_html=True)
                    st.caption(f"[원문 · {f['time']:%m/%d %H:%M}]({f['link']})")
                else:
                    st.caption("@ehdwl 토요일 글 대기 중 (보통 토요일 06:30경 게시)")
        with right:
            c = load_closing(now.date())
            card = c["cards"].get("채권")
            if card:
                st.markdown(f"**🔔 국고채 · 마감 시황** · {c['day']:%m/%d}"
                            + _stale_note(c["day"], now.date(), "15:45\\~17:10") + " · 한화 (원문)")
                render_brief_cards({"채권": card}, ("채권",))
            else:
                st.markdown("**🔔 국고채 · 마감 시황**")
                st.caption("최근 5일 안에 한화 채권 마감 시황 원문이 없습니다")


# ── 🎲 선물·옵션 (국내·미국 증시) ─────────────────────────
def term_structure_chart(curves: dict, height: int = 220):
    """VIX 기간구조: {라벨: Series(index=만기 이름, 값)} 여러 개 (오늘·1주 전·1개월 전)."""
    import plotly.graph_objects as go
    fig = go.Figure()
    styles = [dict(width=2.4), dict(width=1.4, dash="dot"), dict(width=1.2, dash="dash")]
    for (label, s), color, style in zip(curves.items(), ["#f0a35a", SERIES_COLORS[0], "rgba(255,255,255,0.45)"], styles):
        fig.add_scatter(x=list(s.index), y=list(s.values), name=label, mode="lines+markers",
                        line=dict(color=color, **style), marker=dict(size=6), hovertemplate="%{x} %{y:.2f}<extra>" + label + "</extra>")
    fig = _base_layout(fig, "VIX 기간구조 (9일 → 6개월)", "", height, legend=True)
    fig.update_layout(hovermode="closest")
    return fig


def expiry_line(today=None, markets=("국내", "미국"), n: int = 4) -> str:
    """다가오는 옵션 만기일 한 줄 (D-day, 7일 안이면 강조)."""
    from datetime import date as _date
    from data.derivatives import expiries
    today = today or _date.today()
    parts = []
    for e in [x for x in expiries(today) if x["시장"] in markets][:n]:
        d = (e["날짜"] - today).days
        tag = "**오늘**" if d == 0 else f"**D-{d}**" if d <= 7 else f"D-{d}"
        parts.append(f"{e['날짜']:%m/%d}({'월화수목금토일'[e['날짜'].weekday()]}) {e['시장']} {e['이름']} {tag}")
    return " · ".join(parts)


# ── 🌐 24시간 시세 (하이퍼리퀴드 XYZ) ─────────────────────────
def render_hl_section(expanded: bool) -> None:
    """주말·휴장 때 보는 24시간 시세. expanded면 펼친 박스(주말), 아니면 접힌 expander(평일)."""
    from data.hyperliquid import REF_LABEL, THIN_USD, snapshot
    title = "🌐 24시간 시세 · 하이퍼리퀴드 — 직전 정규장 마감 대비 (월요일 갭 가늠)"
    box = st.container(border=True) if expanded else st.expander(title + " · 평일엔 미국장 밖·휴장 때 참고")
    with box:
        if expanded:
            st.markdown(f"**{title}**")
        try:
            df = snapshot()
        except Exception as e:
            st.caption(f"하이퍼리퀴드 시세를 불러오지 못했습니다: {str(e)[:80]}")
            return
        for group in ("지수", "종목", "원자재"):
            sub = df[df["묶음"] == group]
            if sub.empty:
                continue
            st.caption(f"**{group}**")
            for col, r in zip(st.columns(max(len(sub), 4) if group != "종목" else 6), sub.to_dict("records")):
                with col, st.container(border=True):
                    pct = r["등락률"]
                    ref = f"{r['기준가']:,.2f}" if r["기준가"] else "-"
                    st.metric(r["이름"], f"{r['현재가']:,.2f}", f"{pct:+.2f}%" if pct is not None else None,
                              delta_color="red" if (pct or 0) > 0 else "blue" if (pct or 0) < 0 else "gray",
                              help=f"기준: {REF_LABEL[r['기준']]} {r['기준 시각']:%m/%d %H:%M} (KST) 가격 {ref} · "
                                   f"하이퍼리퀴드 XYZ:{r['coin']} 무기한 선물")
                    s = r["차트"]
                    if len(s) > 1:
                        span1d = s.index[-1] - s.index[0] <= pd.Timedelta(hours=26)
                        show_chart(mini_chart(s, s.iloc[-1] >= s.iloc[0], 1 if span1d else 7, height=70))
                    thin = r["거래대금"] < THIN_USD
                    st.caption(f"{r['기준 시각']:%m/%d %H:%M} 대비 · 24h ${r['거래대금'] / 1e6:,.0f}M"
                               + (" · :orange[거래 적음]" if thin else ""))
        st.caption("실제 지수·주식이 아니라 하이퍼리퀴드 XYZ의 암호화폐 담보 무기한 선물 가격(펀딩비 등으로 실제와 차이 가능) · "
                   "기준 = 직전 평일 정규장 마감(미국 16:00 ET · 한국·일본 15:30 · 원자재 17:00 ET, 휴장일 미반영) · "
                   "차트 = 기준 시각 이후 15분봉 · 거래 적은 종목은 가격이 튈 수 있음")
