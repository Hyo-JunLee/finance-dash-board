from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from data import bonds as b
from data.bond_attribution import attribute, curve_check, liquidity_check, summary_text, tp_model_check
from data import flow_data as fd
from data import flow_regime as fr
from data.fred import get_fred_series
from data.global_markets import get_closes
from data.krx import REAL_10Y, kts_yields
from data.macro import yahoo
from data.market import card_context, fmt_asof, get_snapshot
from data.rates import NOMINAL, get_treasury_curve
from ui import CONTEXT_CSS, SERIES_COLORS, render_bond_briefs, _base_layout, curve_chart, kpi, line_chart, rate_card, returns_bar, show_chart

st.title("채권")
st.caption(f"조회 시각 {datetime.now():%Y-%m-%d %H:%M} · 미국: 재무부·뉴욕 연은·FRED·FiscalData · 한국: KRX "
           "· 금리 변화 단위 bp(0.01%p)")
render_bond_briefs()  # 맨 위 원문 박스: 미 국채(아침) | 국고채(마감)

CHANGE_WINDOWS = {"1주": pd.DateOffset(weeks=1), "1개월": pd.DateOffset(months=1), "3개월": pd.DateOffset(months=3),
                  "YTD": "YTD", "1년": pd.DateOffset(years=1)}
CHART_YEARS = {"1년": 1, "3년": 3, "5년": 5, "10년": 10}


def _since(last: pd.Timestamp, window) -> pd.Timestamp:
    return pd.Timestamp(year=last.year, month=1, day=1) - pd.Timedelta(days=1) if window == "YTD" else last - window


def _clip(df, years: int):
    return df[df.index >= df.index[-1] - pd.DateOffset(years=years)]


VS_RULE_COLOR = {"일치": "green", "부분 일치": "orange", "다름": "red"}
CONF_COLOR = {"높음": "green", "중간": "blue", "낮음": "orange"}


def _md(text: str) -> str:
    return text.replace("$", "\\$").replace("~", "\\~")  # 달러·물결이 수식·취소선으로 바뀌지 않게


def _ai_bond_card(window: str, attr, liq_text: str | None, liq_warn: bool) -> None:
    """맨 위 AI 판단: 결론 → 설명 → 근거 | 규칙과 비교·확신도·판단을 바꿀 신호 → 참고 출처."""
    from data.telegram import load_summary
    view = load_summary(config.BOND_VIEW_FILE)
    v = (view or {}).get("windows", {}).get(window)
    with st.container(border=True):
        if liq_warn:
            st.warning(liq_text)
        if v is None:
            st.markdown(f"**🧭 {window} 판정 (규칙 기반)**")
            if attr is not None:
                st.markdown(f"10년물 {attr.nominal:+.0f}bp · **{attr.verdict}** — 자세한 근거는 아래 '근거 ①'을 펼쳐 보세요.")
            st.caption("AI 판단은 매일 08:30에 1주·1개월 기간으로 만듭니다. 이 기간은 규칙 판정만 있습니다.")
            return
        made = pd.Timestamp(view["generated_at"])
        stale = made.date() != datetime.now().date()
        st.caption(f"🧭 AI 판단 · {window} · {made:%m/%d %H:%M} 작성 (데이터 \\~{pd.Timestamp(v['panel1_end']):%m/%d})"
                   + (" · :orange[오늘 것 아님]" if stale else ""))
        st.markdown(f"#### {_md(v['headline'])}")
        st.markdown(_md(v["explanation"]))
        left, right = st.columns([1, 1], gap="large")
        with left:
            st.markdown("**근거**  \n" + "  \n".join(f"· {_md(e)}" for e in v["evidence"]))
        with right:
            color = VS_RULE_COLOR.get(v["vs_rule"], "gray")
            st.markdown(f"**규칙 판정과 비교** :{color}-badge[{v['vs_rule']}]  \n"
                        f"규칙: {v['rule_verdict']}  \n{_md(v['vs_rule_reason'])}")
            st.markdown(f"**확신도** :{CONF_COLOR.get(v['confidence'], 'gray')}-badge[{v['confidence']}]  \n"
                        f"{_md(v['confidence_reason'])}")
            if v["change_mind"]:
                st.markdown("**판단을 바꿀 신호**  \n" + "  \n".join(f"· {_md(x)}" for x in v["change_mind"]))
        note = "AI(Claude)가 아래 규칙 기반 근거와 최근 뉴스·리서치를 함께 읽고 쓴 판단입니다. 투자 권유가 아닙니다."
        if v["sources"]:
            note = "참고: " + " · ".join(f"[{x['kind']} · {x['title']}]({x['link']})" if x.get("link") else f"{x['kind']} · {x['title']}"
                                       for x in v["sources"]) + " · " + note
        if v.get("unverified_numbers"):
            note += f" · :orange[데이터에서 확인되지 않은 숫자: {', '.join(v['unverified_numbers'])}]"
        st.caption(note)


def _ai_history(rb: pd.DataFrame) -> None:
    """매일 아침 AI 판단과 규칙 판정을 나란히 쌓고, 판단 이후 1주간 10년물이 실제로 어떻게 움직였는지 붙인다."""
    import json
    path = config.BASE_DIR / "collected" / "bond_views.jsonl"
    try:
        logs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except FileNotFoundError:
        st.caption("아직 기록이 없습니다. 매일 08:30 판단이 쌓입니다.")
        return
    nominal = rb["명목 10년"]
    rows = []
    for rec in logs:
        for w, v in rec.get("windows", {}).items():
            base = pd.Timestamp(v["panel1_end"])
            after = nominal[(nominal.index > base) & (nominal.index <= base + pd.Timedelta(days=7))]
            rows.append({
                "작성": rec["generated_at"], "기간": w, "10년물(bp)": v["fresh_nominal_bp"],
                "규칙 판정": v["rule_verdict"], "AI 결론": v["headline"], "비교": v["vs_rule"], "확신도": v["confidence"],
                "이후 1주 10년물(bp)": round((after.iloc[-1] - nominal[nominal.index <= base].iloc[-1]) * 100, 0)
                if len(after) else None,
            })
    if not rows:
        st.caption("아직 기록이 없습니다.")
        return
    df = pd.DataFrame(rows).sort_values("작성", ascending=False).drop_duplicates(["작성", "기간"])
    agree = (df["비교"] == "일치").mean() * 100
    st.caption(f"기록 {len(df)}건 · 규칙과 일치 {agree:.0f}% · '이후 1주 10년물'은 판단 데이터 기준일 다음 1주간의 실제 변화"
               " (판단이 방향을 맞혔는지가 아니라, 어떤 판단 뒤에 금리가 어떻게 움직였는지 쌓아 보는 용도)")
    st.dataframe(df, hide_index=True, width="stretch")


FLOW_JUDGE_WINDOWS = ("1주", "1개월")
SRF_WARN_BN = 1.0  # 상설 레포기구 이용액이 최근 20일 중 이 이상(십억 달러)이면 경고  # 이보다 긴 기간은 한 번만 튀어도 '스트레스'로 기울어 결합 판정 생략


def _z_text(z: pd.Series | None) -> str:
    z = z.dropna() if z is not None else None
    return f"z {z.iloc[-1]:+.1f}" if z is not None and len(z) else "z -"


def _flow_card(col, label: str, value: str, delta: str | None, z: str, asof: str, help: str, lagged: bool = False):
    with col:
        st.metric(label, value, delta, delta_color="off", delta_arrow="off", border=True, help=help)
        st.caption(f"{z} · " + (f":orange[**{asof}**]" if lagged else asof))


def _flow_section(window, attr, noise_bp, since_c, common_end, premium_like, d2, z_thr, n_thr, z_win, corr_win):
    """수급·포지션: 기존 종합 판정의 움직임이 수급 스트레스를 동반했는지 (판정은 data/flow_regime.py)."""
    st.markdown("##### 수급·포지션 (정보에 의한 재가격인가, 포지션 정리인가)")
    with st.spinner("CFTC·FRED·입찰 데이터 불러오는 중..."):
        zs, errors = fd.stress_series(z_win)
    try:
        vol_s, vol_name, vol_sub = fd.rate_vol()
    except Exception:
        vol_s, vol_name, vol_sub = None, "금리 변동성", False

    # ── 결론 한 줄 ──
    judged = window in FLOW_JUDGE_WINDOWS
    if judged:  # 종합 판정과 같은 기간(공통 마지막 날짜 기준)의 최대 |z|
        peaks = {k: (fr.window_peak(z, since_c, common_end) if z is not None else None) for k, z in zs.items()}
        stress = fr.stress_level({k: (p[0] if p else None) for k, p in peaks.items()}, z_thr, n_thr)
        mismatch = fr.curve_mismatch(attr.leader, premium_like, abs(d2) >= noise_bp)
        _, text = fr.combine(attr.leader, abs(attr.nominal) >= noise_bp, mismatch, stress["level"])
        basis = f"기준: 종합 판정과 같은 {window} 기간(\\~{common_end:%m/%d})의 지표별 최대 |z|"
    else:
        now = pd.Timestamp.now()
        peaks = {k: (fr.window_peak(z, now, now) if z is not None else None) for k, z in zs.items()}
        stress = fr.stress_level({k: (p[0] if p else None) for k, p in peaks.items()}, z_thr, n_thr)
        text = f"{window}처럼 긴 기간은 한 번만 튀어도 스트레스로 기울어 정보·수급 결합 판정은 생략하고, 현재 수급 상태만 표시합니다."
        basis = "기준: 지표별 최신값"
    if vol_sub:
        basis += " · 금리 변동성은 MOVE를 받지 못해 10년물 실현변동성으로 대체"
    with st.container(border=True):
        st.markdown(fr.flow_summary(text, stress, z_thr, basis))
        hit_dates = [f"{k} {p[1]:%m/%d} z {p[0]:+.1f}" for k, p in peaks.items() if p]
        st.caption("지표별 z (날짜): " + " · ".join(hit_dates) + f" · 1년 롤링 z ({z_win}영업일, 주간 데이터는 {max(round(z_win * 52 / 252), 8)}주)")

    # ── 지표 카드 ──
    row1, row2 = st.columns(4), st.columns([1, 3])
    try:
        lev = fd.lev_10y_equiv().dropna()
        rep_day = lev.index[-1]
        _flow_card(row1[0], "레버리지 펀드 순포지션 (10년물 환산)", f"{lev.iloc[-1] / 1e4:,.0f}만 계약",
                   f"주간 {lev.diff().iloc[-1] / 1e4:+,.1f}만", _z_text(zs[fd.LEV]),
                   f"{rep_day:%m/%d}(화) 기준 · {rep_day + pd.Timedelta(days=3):%m/%d}(금) 발표 · 주간",
                   "CFTC TFF 레버리지 펀드의 국채 선물 순포지션(롱−숏)을 계약별 DV01 근사치로 10년물 선물 환산. "
                   "순숏 대부분은 현물·선물 베이시스 트레이드라 방향성 베팅이 아닙니다. 수준보다 **주간 변화의 급변**"
                   "(베이시스 트레이드 청산 가능성)이 스트레스 신호이고, z도 주간 변화 기준입니다. "
                   "DV01(달러/bp): " + ", ".join(f"{n} {d}" for n, d in config.TSY_FUTURES.values()), lagged=True)
    except Exception:
        _flow_card(row1[0], "레버리지 펀드 순포지션", "데이터 없음", None, "z -", "CFTC 조회 실패", "CFTC 공개 API 호출 실패")
    try:
        repo = fd.sofr_iorb()
        _flow_card(row1[1], "SOFR − IORB", f"{repo.iloc[-1]:+.0f}bp", f"전일 {repo.diff().iloc[-1]:+.0f}bp",
                   _z_text(zs[fd.REPO]), f"{repo.index[-1]:%m/%d} 기준 (SOFR는 다음 날 공개)",
                   "담보부 익일물 금리(SOFR)가 연준이 지급준비금에 주는 금리(IORB)보다 높아질수록 레포 자금이 빠듯하다는 뜻. "
                   "월말·분기말·국채 결제일에 일시적으로 튀는 경향이 있습니다.")
    except Exception:
        _flow_card(row1[1], "SOFR − IORB", "데이터 없음", None, "z -", "FRED 조회 실패", "FRED SOFR·IORB 호출 실패")
    # 상설 레포기구(SRF): 평소 0에 가까워 카드 대신, 최근 20일 최대가 SRF_WARN_BN 이상일 때만 경고
    srf_note = ""
    try:
        srf = fd.fred_daily("RPONTSYD")
        peak = srf.tail(20).max()
        if peak >= SRF_WARN_BN:
            srf_note = (f":orange[⚠ 연준 상설 레포기구(SRF) 이용액 최근 20일 최대 ${peak:,.1f}bn "
                        f"({srf.idxmax():%m/%d}) — 시장에서 레포 자금을 구하기 어려웠다는 신호]")
    except Exception:
        pass

    try:
        a = fd.auction_table().iloc[-1]
        _flow_card(row1[2], f"최근 입찰 · {a['만기']}", f"응찰률 {a['응찰률']:.2f}",
                   f"평균 {a['응찰률 6회평균']:.2f} · 딜러 {a['딜러(%)']:.0f}%(평균 {a['딜러(%) 6회평균']:.0f}%)",
                   _z_text(zs[fd.AUCTION]).replace("z", "약세 z"),
                   f"{a['입찰일']:%m/%d} 입찰 · 해외 {a['해외(%)']:.0f}%(평균 {a['해외(%) 6회평균']:.0f}%)",
                   "2·5·7·10·20·30년물 입찰마다 응찰률 하락과 프라이머리 딜러 인수 비율 상승을 같은 만기 직전 6회 평균과 비교해 "
                   "약세 z로 만든 값(양수일수록 약함). 해외는 간접 응찰 비율. 소스: TreasuryDirect (FiscalData는 2026-10 말 갱신 중단). "
                   "입찰 테일(낙찰금리 − WI 금리)은 WI 금리가 유료라 생략했습니다(입찰 탭의 근사 테일은 참고용).")
    except Exception:
        _flow_card(row1[2], "최근 입찰", "데이터 없음", None, "z -", "TreasuryDirect 조회 실패", "입찰 데이터 호출 실패")
    if vol_s is not None and len(vol_s):
        unit = "" if not vol_sub else "bp"
        _flow_card(row1[3], vol_name, f"{vol_s.iloc[-1]:.1f}{unit}", f"1개월 전 대비 {vol_s.iloc[-1] - vol_s.iloc[-22]:+.1f}",
                   _z_text(zs[fd.VOL]), f"{vol_s.index[-1]:%m/%d} 기준" + (" · 대체 지표" if vol_sub else ""),
                   "MOVE: 미 국채 옵션 내재변동성 지수(Yahoo ^MOVE). 받지 못하면 FRED DGS10 일간 변화의 20영업일 표준편차(bp)로 대체하고 "
                   "카드 이름에 표시합니다.", lagged=vol_sub)
    else:
        _flow_card(row1[3], "금리 변동성", "데이터 없음", None, "z -", "조회 실패", "MOVE·DGS10 모두 실패")
    try:
        corr = fd.stock_bond_corr(corr_win)
        c = corr.iloc[-1]
        _flow_card(row2[0], f"주식·채권 수익률 {corr_win}일 상관", f"{c:+.2f}",
                   ("채권 헤지 작동" if c < 0 else "주식·채권 동반 움직임") + (" (약함)" if abs(c) < 0.2 else ""), "판정 미사용", f"{corr.index[-1]:%m/%d} 기준",
                   "S&P 500 일간 수익률과 10년물 국채 일간 수익률(= −금리 변화, FRED SP500·DGS10)의 롤링 상관 (업계 관례). "
                   "음수 = 주가가 빠지는 날 채권값이 오름 → 채권이 주식 헤지로 작동. "
                   "양수 = 주가가 빠지는 날 채권값도 빠짐 → 주식·채권 동반 움직임 (2022년처럼 인플레·재정 우려 국면에 흔함).")
    except Exception:
        _flow_card(row2[0], "주식·채권 상관", "데이터 없음", None, "판정 미사용", "FRED 조회 실패", "FRED SP500·DGS10 호출 실패")
    with row2[1]:
        if srf_note:
            st.markdown(srf_note)
        st.caption("판정에 쓰는 스트레스 지표(윗줄): 레버리지 펀드 주간 변화, SOFR−IORB, 입찰 약세, 금리 변동성. "
                   "주식·채권 상관은 참고용입니다. :orange[주황색 기준일]은 주간 발표이거나 대체 지표라는 뜻입니다. "
                   f"연준 상설 레포기구(SRF) 이용액은 평소 0에 가까워 ${SRF_WARN_BN:.0f}bn 이상일 때만 경고합니다. "
                   "스왑스프레드는 무료 SOFR 스왑금리 소스가 없어 다루지 않습니다.")
        if errors:
            st.caption("불러오지 못한 지표: " + " · ".join(f"{k} ({v})" for k, v in errors.items()))

    # ── 차트 (고정 기간) ──
    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        try:
            lev = fd.lev_10y_equiv().dropna() / 1e4
            show_chart(line_chart({"10년물 환산 순포지션": _clip(lev, 1)}, "레버리지 펀드 순포지션 (1년, 만 계약)",
                                  height=240, decimals=0))
        except Exception:
            st.caption("레버리지 펀드 차트: 데이터 없음")
    with c2:
        try:
            show_chart(line_chart({"SOFR−IORB": _clip(fd.sofr_iorb(), 1)}, "SOFR − IORB (1년)", unit="bp",
                                  height=240, hline=0, decimals=0))
        except Exception:
            st.caption("SOFR−IORB 차트: 데이터 없음")
    with c3:
        try:
            show_chart(line_chart({"상관": _clip(fd.stock_bond_corr(corr_win), 3)},
                                  f"주식·채권 수익률 {corr_win}일 상관 (3년, 음수 = 헤지 작동)", height=240, hline=0))
        except Exception:
            st.caption("주식·채권 상관 차트: 데이터 없음")


# ── 맨 위 요약: 금리 수준 (오늘의 시황 카드 형식) ────────────────
_WEEK = "월화수목금토일"


def _rb_snap(rb: pd.DataFrame, col: str, label: str) -> dict:
    """재무부 종가 기반 10년 실질금리·BEI 카드용 값."""
    s = rb[col].dropna()
    chg = float(s.iloc[-1] - s.iloc[-2])
    return {"label": label, "kind": "rate", "last": float(s.iloc[-1]), "chg": chg, "series": s,
            **card_context(s, "rate", chg)}


def _bond_events() -> str:
    """다가오는 채권 이벤트: 국채 입찰 예정 + 다음 CPI·고용·PCE 발표 + 다음 금통위 (2주 안)."""
    from datetime import date as _date
    today = _date.today()
    ev = []
    try:
        auc = b.auctions()
        for _, r in auc[auc["평가"] == "예정"].sort_values("입찰일").iterrows():
            d = pd.Timestamp(r["입찰일"]).date()
            ev.append((d, f"{r['만기'].replace('-Year', '년')}물 입찰 ${r['발행액($bn)']:,.0f}bn"))
    except Exception:
        pass
    from data.fred import fetch_next_release_date
    for name, sid in (("CPI", "CPIAUCSL"), ("고용보고서", "PAYEMS"), ("PCE", "PCEPI"), ("GDP", "A191RL1Q225SBEA")):
        try:
            d = fetch_next_release_date(sid)
            if d:
                ev.append((d, f"미 {name}"))
        except Exception:
            pass
    try:
        from data.kr_calendar import bok_meetings
        nxt = [d for d in bok_meetings(today.year) if d >= today]
        if nxt:
            ev.append((nxt[0], "한은 금통위"))
    except Exception:
        pass
    ev = sorted(e for e in ev if 0 <= (e[0] - today).days <= 14)
    return " · ".join(f"{d:%m/%d}({_WEEK[d.weekday()]}) {t} " + ("**오늘**" if d == today else f"D-{(d - today).days}")
                      for d, t in ev)


st.html(CONTEXT_CSS)
_rb_top = b.real_bei_10y()
_snaps = {k: get_snapshot(k) for k in ("us2y", "us10y", "us30y", "us2s10s")}
for row in ((("us2y", "미 2년물"), ("us10y", "미 10년물"), ("us30y", "미 30년물")),
            (("us2s10s", "10Y−2Y"), ("real", "10년 실질금리(TIPS)"), ("bei", "10년 기대인플레(BEI)"))):
    for col, (key, name) in zip(st.columns(3), row):
        with col:
            try:
                if key == "real":
                    snap, asof = _rb_snap(_rb_top, "실질 10년", name), f"{_rb_top.index[-1]:%m/%d} 종가"
                elif key == "bei":
                    snap, asof = _rb_snap(_rb_top, "기대인플레(BEI)", name), f"{_rb_top.index[-1]:%m/%d} 종가"
                else:
                    snap = _snaps[key]
                    asof = fmt_asof(snap)
                rate_card(f"bd_{key}", f"{name} · {asof}", snap)
            except Exception:
                st.metric(name, "N/A", help="데이터 수집 실패", border=True)
_events = _bond_events()
if _events:
    st.markdown(f"**📅 2주 안 채권 이벤트** · {_events}",
                help="국채 입찰: TreasuryDirect 예정 공고 · 미국 지표: FRED 발표 일정 · 금통위: 한국은행")
st.caption("금리 카드: 재무부 종가 + CNBC 실시간 (시각만 = 실시간) · 실질금리·BEI는 재무부 종가 · "
           "막대 = 1년 최저\\~최고 중 현재 위치 · 기간 변화는 bp")

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
    c1.caption("맨 위는 AI 판단(매일 08:30), 아래 '근거'는 정해진 공식으로 계산한 규칙 기반 분해입니다.")
    window = c2.segmented_control("변화 기간", list(FLOW_JUDGE_WINDOWS), default="1개월", key="dec_win") or "1개월"
    ai_slot = st.container()  # AI 판단 카드: 아래 규칙 계산이 끝난 뒤 채운다

    with st.expander("🔍 근거 ① 규칙 기반 분해 — 실질금리·기대인플레 / 정책 기대·텀프리미엄", expanded=False):
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
        st.markdown("**종합 판정 (패널 ①×② 교차 검증)**")
        if acm.empty:
            st.info("ACM 데이터가 없어 종합 판정을 계산할 수 없습니다.")
        else:
            # 판정 임계값은 config.py 값 고정 (노이즈 bp, 모형 괴리 bp, 수급 z·지표 수·롤링 창)
            noise_bp, gap_bp = config.ATTR_NOISE_BP, config.ATTR_TP_MODEL_GAP_BP
            flow_z, flow_n, flow_win = config.FLOW_Z_THRESHOLD, config.FLOW_STRESS_COUNT, config.FLOW_Z_WINDOW_DAYS

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

            # 유동성 왜곡 점검: 같은 공통 기간의 HY 스프레드(FRED, 최근 3년만 제공)·MOVE 변화
            def _window(s: pd.Series):
                s = s[s.index <= common_end]
                if s.empty:
                    return None, None
                start = s[s.index <= since_c]
                return (start.iloc[-1] if len(start) else None), s.iloc[-1]
            try:
                hy0, hy1 = _window(get_fred_series("BAMLH0A0HYM2"))
            except Exception:
                hy0, hy1 = None, None
            try:
                mv0, mv1 = _window(yahoo("^MOVE", years=3))
            except Exception:
                mv0, mv1 = None, None
            liq_text, liq_warn = liquidity_check(
                (hy1 - hy0) * 100 if hy0 is not None and hy1 is not None else None, hy1,
                (mv1 / mv0 - 1) * 100 if mv0 is not None and mv1 is not None else None, mv1,
                attr.components["D"], noise_bp, config.ATTR_HY_WIDEN_BP, config.ATTR_HY_LEVEL,
                config.ATTR_MOVE_RISE_PCT, config.ATTR_MOVE_LEVEL)

            left_c, right_c = st.columns([3, 2], gap="large")
            with left_c, st.container(border=True):
                if liq_warn:
                    st.warning(liq_text)
                st.markdown(summary_text(attr, f"{common_end:%m/%d}", f"{rb.index[-1]:%m/%d}", f"{acm.index[-1]:%m/%d}",
                                         noise_bp))
            with right_c:
                text, _ = curve_check(curve_c["2 Yr"], curve_c["10 Yr"], curve_c["30 Yr"], noise_bp)
                st.markdown(f"**보조 검증 (모형 없음)**  \n{text}")
                kw_text, _ = tp_model_check(acm_c["텀 프리미엄"], d_kw, gap_bp)
                kw_note = f" (Kim-Wright 데이터 \\~{kw_end:%m/%d})" if kw_end is not None else ""
                st.markdown(f"**텀프리미엄 모형 비교**  \n{kw_text}{kw_note}")
                if not liq_warn:
                    st.markdown(f"**유동성 왜곡 점검**  \n{liq_text}",
                                help="시장 경색 때 TIPS 유동성 프리미엄이 커지면 BEI 하락이 인플레 리스크 프리미엄(D) 하락으로 "
                                     "잘못 읽힙니다. HY 스프레드나 MOVE가 급등하면 판정 위에 경고를 띄웁니다.")

    if not acm.empty:
        _, premium_like = curve_check(curve_c["2 Yr"], curve_c["10 Yr"], curve_c["30 Yr"], noise_bp)
        with st.expander("🔍 근거 ② 수급·포지션 — 정보에 의한 재가격인가, 포지션 정리인가", expanded=False):
            _flow_section(window, attr, noise_bp, since_c, common_end, premium_like, curve_c["2 Yr"],
                          flow_z, flow_n, flow_win, config.FLOW_CORR_WINDOW)
    with st.expander("📒 AI 판단 vs 규칙 판정 기록", expanded=False):
        _ai_history(rb)
    with ai_slot:
        _ai_bond_card(window, attr if not acm.empty else None, liq_text if not acm.empty else None,
                      liq_warn if not acm.empty else False)

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
        st.caption("국면 뜻은 위 카드의 ? 도움말 · 불 = 금리 하락, 베어 = 금리 상승 · "
                   "스티프닝 = 장단기 금리차 확대, 플래트닝 = 축소",
                   help="\n\n".join(f"**{k}**: {v}" for k, v in b.REGIMES.items()))

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
    top = done.head(20)
    show = pd.DataFrame({
        "입찰일": top["입찰일"], "만기": top["만기"].str.replace("-Year", "년"), "발행액($bn)": top["발행액($bn)"],
        "낙찰금리(%)": top["낙찰금리(%)"],
        "응찰률 (6회 평균)": [f"{x:.2f} ({a:.2f})" if a == a else f"{x:.2f}" for x, a in zip(top["응찰률"], top["응찰률 6회평균"])],
        "해외(%)": top["해외(%)"],
        "딜러(%) (6회 평균)": [f"{x:.1f} ({a:.1f})" if a == a else f"{x:.1f}" for x, a in zip(top["딜러(%)"], top["딜러(%) 6회평균"])],
        "평가": top["평가"],
    })

    def _grade_style(df: pd.DataFrame) -> pd.DataFrame:
        styles = pd.DataFrame("", index=df.index, columns=df.columns)
        styles.loc[df["평가"].str.contains("강함"), "평가"] = "color: #199e70; font-weight: 600"
        styles.loc[df["평가"].str.contains("약함"), "평가"] = "color: #d95926; font-weight: 600"
        return styles

    st.dataframe(
        show.style.apply(_grade_style, axis=None).format({
            "입찰일": "{:%Y-%m-%d}", "발행액($bn)": "{:,.0f}", "낙찰금리(%)": "{:.3f}", "해외(%)": "{:.1f}",
        }, na_rep="-"),
        hide_index=True, width="stretch",
    )
    st.caption(
        "평가: 같은 만기 직전 6회 평균 대비 응찰률↑·딜러 비중↓ = 💪 강함, 응찰률↓·딜러 비중↑ = ⚠️ 약함 (딜러는 남은 물량을 떠안는 역할이라 "
        "비중이 높을수록 실수요가 약했다는 뜻). 해외(%) = 해외 중앙은행·기관 등 간접 응찰. "
        "괄호 = 같은 만기 직전 6회 평균."
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
    st.caption(f"출처: KRX 국채전문유통시장 지표물 종가 (다음 영업일 공개, 현재 \\~{kr.index[-1]:%Y-%m-%d}) · "
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
