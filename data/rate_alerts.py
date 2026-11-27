"""미 국채 2년·10년물 큰 변동 텔레그램 알림 (alerts.py가 10분마다 호출).

기준 (최근 1년 분포로 정함, 상세는 README)
1. 하루 누적: 전일 종가(CNBC, 뉴욕 17시) 대비 ±8bp → 같은 날 12·16bp까지 더 벌어지면 단계마다 한 번 더
2. 30분 급변: 30분 안에 ±4bp → 같은 종목·방향은 1시간에 한 번
3. 커브: 10Y−2Y 하루 ±6bp → 하루 한 번 (불/베어 스티프닝·플래트닝 표시)
메시지엔 수준·전일 대비·30분 변화·다른 만기·커브, 그 시각 발표 지표, 달러·S&P 선물을 붙인다.
금리가 멈춰 있는 시간(주말·휴장)엔 CNBC 마지막 값이 20분 넘게 묵으면 점검하지 않는다.
"""
import json
from datetime import datetime, timedelta

import pandas as pd

import config

STATE = config.CACHE_DIR / "rate_alert_state.json"
SYMBOLS = {"US2Y": "미 2년물", "US10Y": "미 10년물"}
DAILY_LEVELS = (8, 12, 16)   # bp
FAST_BP, FAST_MIN = 4, 30    # 30분 안 4bp
FAST_COOLDOWN = timedelta(hours=1)
CURVE_BP = 6
STALE = timedelta(minutes=20)
NY = "America/New_York"


def regime(d2: float, d10: float) -> str:
    """2년·10년 변화(bp)로 곡선 국면: 금리 하락 = 불, 상승 = 베어 / 10Y−2Y 확대 = 스티프닝, 축소 = 플래트닝."""
    direction = "불" if (d2 + d10) < 0 else "베어"
    shape = "스티프닝" if d10 - d2 > 0 else "플래트닝"
    return f"{direction} {shape}"


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"sent": [], "fast": {}}


def evaluate(series: dict[str, pd.Series], prev: dict[str, float], now: pd.Timestamp, state: dict) -> list[dict]:
    """알릴 이벤트 목록. series: {US2Y·US10Y: 미 동부 시각 인덱스 1분봉}, prev: 전일 종가(%). state는 갱신된다."""
    events, sent = [], set(state.get("sent", []))
    fast = state.setdefault("fast", {})
    snap = {}
    for sym, s in series.items():
        s = s.dropna()
        if s.empty or sym not in prev or now - s.index[-1] > STALE:
            continue
        last = float(s.iloc[-1])
        ago = s[s.index <= s.index[-1] - timedelta(minutes=FAST_MIN)]
        snap[sym] = {"last": last, "day": (last - prev[sym]) * 100,
                     "fast": (last - float(ago.iloc[-1])) * 100 if len(ago) else 0.0, "time": s.index[-1]}
    if not snap:
        return []
    session = (now + timedelta(hours=6)).date().isoformat()  # 국채 거래일: 미 동부 18시에 다음 날 세션 시작
    for sym, v in snap.items():
        sign = "+" if v["day"] > 0 else "-"
        hit = [lv for lv in DAILY_LEVELS if abs(v["day"]) >= lv and f"{session}:{sym}:{sign}{lv}" not in sent]
        if hit:
            for lv in DAILY_LEVELS:
                if abs(v["day"]) >= lv:
                    sent.add(f"{session}:{sym}:{sign}{lv}")
            events.append({"kind": "day", "sym": sym, "level": max(hit), **v})
        fsign = "+" if v["fast"] > 0 else "-"
        last_fast = fast.get(f"{sym}{fsign}")
        if abs(v["fast"]) >= FAST_BP and (not last_fast or now - pd.Timestamp(last_fast) >= FAST_COOLDOWN):
            fast[f"{sym}{fsign}"] = now.isoformat()
            events.append({"kind": "fast", "sym": sym, **v})
    if "US2Y" in snap and "US10Y" in snap:
        curve = snap["US10Y"]["day"] - snap["US2Y"]["day"]
        csign = "+" if curve > 0 else "-"
        if abs(curve) >= CURVE_BP and f"{session}:curve:{csign}" not in sent:
            sent.add(f"{session}:curve:{csign}")
            events.append({"kind": "curve", "curve": curve, "d2": snap["US2Y"]["day"], "d10": snap["US10Y"]["day"]})
    state["sent"] = sorted(sent)[-300:]
    state["snap"] = {k: {**v, "time": str(v["time"])} for k, v in snap.items()}
    return events


def _releases_near(now: pd.Timestamp) -> list[str]:
    """지금부터 40분 전까지 발표된 미국 지표 (Nasdaq 일정)."""
    from data.consensus import fetch_day
    from data.release_insight import TRIGGERS
    out = []
    try:
        rows = fetch_day(now.date())
    except Exception:
        return out
    for r in rows:
        t, actual = (r.get("gmt") or "").strip(), (r.get("actual") or "").replace("&nbsp;", "").strip()
        if not actual or ":" not in t:
            continue
        h, m = map(int, t.split(":"))
        at = now.normalize() + timedelta(hours=h, minutes=m)
        if now - timedelta(minutes=40) <= at <= now:
            cons = (r.get("consensus") or "").replace("&nbsp;", "").strip()
            kst = at.tz_convert("Asia/Seoul")
            line = f"{kst:%H:%M} {r['eventName'].strip()} {actual}" + (f" (예상 {cons})" if cons else "")
            out.append((0 if r["eventName"].strip().lower() in TRIGGERS else 1, line))
    return [line for _, line in sorted(out)[:3]]


def _markets() -> str:
    from data.market import get_snapshot
    parts = []
    for key, name in (("dxy", "달러"), ("es", "S&P 선물")):
        try:
            s = get_snapshot(key)
            parts.append(f"{name} {s['pct']:+.2f}%")
        except Exception:
            continue
    return " · ".join(parts)


def message(events: list[dict], snap: dict, now: pd.Timestamp, url: str = "") -> str:
    lines = []
    for e in events:
        if e["kind"] == "curve":
            word = "확대" if e["curve"] > 0 else "축소"
            lines.append(f"📐 10Y−2Y 하루 {e['curve']:+.1f}bp {word} ({regime(e['d2'], e['d10'])}) · "
                         f"2년 {e['d2']:+.1f}bp · 10년 {e['d10']:+.1f}bp")
            continue
        name, up = SYMBOLS[e["sym"]], e["day"] > 0 if e["kind"] == "day" else e["fast"] > 0
        icon = "📈" if up else "📉"
        if e["kind"] == "day":
            lines.append(f"{icon} {name} 전일 대비 {e['day']:+.1f}bp → {e['last']:.3f}% "
                         f"({e['level']}bp 넘음{', 더 벌어짐' if e['level'] > DAILY_LEVELS[0] else ''})")
        else:
            lines.append(f"⚡ {name} 30분 새 {e['fast']:+.1f}bp 급{'등' if up else '락'} → {e['last']:.3f}% "
                         f"(전일 대비 {e['day']:+.1f}bp)")
    ctx = []
    if "US2Y" in snap and "US10Y" in snap:
        d2, d10 = snap["US2Y"]["day"], snap["US10Y"]["day"]
        ctx.append(f"2년 {d2:+.1f}bp · 10년 {d10:+.1f}bp · 10Y−2Y {d10 - d2:+.1f}bp ({regime(d2, d10)})")
    rel = _releases_near(now)
    if rel:
        ctx.append("그 시각 발표: " + " / ".join(rel))
    mk = _markets()
    if mk:
        ctx.append(mk)
    kst = now.tz_convert("Asia/Seoul")
    return ("\n".join(lines) + "\n\n" + "\n".join(ctx) + f"\n({kst:%m/%d %H:%M} KST, CNBC)"
            + (f"\n\n📊 {url}" if url else ""))


def check_and_send(send, url: str = "", log=None, now: pd.Timestamp | None = None) -> list[dict]:
    from data.rates import get_cnbc_prev_close
    from data.release_insight import _cnbc_intraday
    now = now or pd.Timestamp.now(tz=NY)
    series = {}
    for sym in SYMBOLS:
        try:
            series[sym] = _cnbc_intraday(sym)
        except Exception:
            continue
    try:
        prev = get_cnbc_prev_close(tuple(SYMBOLS))
    except Exception:
        return []
    state = _load_state()
    events = evaluate(series, prev, now, state)
    if events:
        snap = {k: v for k, v in state.get("snap", {}).items()}
        send(message(events, snap, now, url))
        if log:
            log.info(f"금리 알림 {len(events)}건: " + ", ".join(e["kind"] + ":" + e.get("sym", "curve") for e in events))
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    return events
