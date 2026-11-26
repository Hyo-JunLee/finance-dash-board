"""미국 주요 지표 발표 직후 AI 해석 (🧠 최근 발표 해석).

흐름 (alerts.py가 10분마다 호출):
1. 오늘(미 동부) Nasdaq 일정에서 중요 지표(CPI·PCE·고용·GDP·소매판매·PPI·JOLTS·ISM)가 발표된 시각 묶음을 찾는다
2. 발표 30분 뒤가 지나면 한 번만 실행: 숫자(실제·예상·이전, FRED 최근 추이)와
   발표 전후 30분 시장 반응(2년물·10년물 금리·달러·S&P/나스닥 선물)을 코드가 계산해 넘긴다
3. claude -p 가 4줄(결과·의미·연준/금리 함의·시장 반응/다음 확인)로 해석 ("~음/~함" 체)
4. 응답 속 숫자가 입력에 없는 숫자면 한 번 다시 요청, 그래도 있으면 그 문장을 뺀다
5. .cache/release_insights.json 에 저장(화면), collected/release_insights.jsonl 에 누적, 텔레그램 전송
6. 그날 미 동부 16:20 이후 장 마감까지 반응을 계산해 한 줄 덧붙임 (🔔 장 마감 반응)
"""
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

import config

NY = ZoneInfo("America/New_York")
STORE = config.CACHE_DIR / "release_insights.json"
HISTORY = config.BASE_DIR / "collected" / "release_insights.jsonl"
WAIT = timedelta(minutes=30)       # 발표 후 시장 반응을 볼 시간
GIVE_UP = timedelta(hours=10)      # 이보다 늦으면(맥이 오래 꺼져 있었음) 건너뜀
# 이 지표가 들어 있는 발표 묶음만 해석 (주간 실업수당 청구만 있는 날 등은 건너뜀)
TRIGGERS = {"cpi", "core cpi", "pce price index", "core pce price index", "nonfarm payrolls", "unemployment rate",
            "gdp", "retail sales", "ppi", "core ppi", "jolts job openings", "ism manufacturing pmi",
            "ism non-manufacturing pmi"}
# 시장 반응: 금리는 CNBC 장중 차트(1분봉, 미 동부 시각, 최근 5거래일), 나머지는 Yahoo 5분봉
# (2년물 국채 선물 ZT=F 가격 환산은 만기 교체 때 방향까지 틀려서 쓰지 않음)
YIELDS = [("미 2년물", "US2Y"), ("미 10년물", "US10Y")]
REACTION = [("달러인덱스", "DX-Y.NYB"), ("S&P 500 선물", "ES=F"), ("나스닥100 선물", "NQ=F")]
CNBC_CHART = "https://ts-api.cnbc.com/harmony/app/charts/5D.json"


def _cnbc_intraday(symbol: str) -> pd.Series:
    import requests
    bars = requests.get(CNBC_CHART, params={"symbol": symbol}, headers={"User-Agent": "Mozilla/5.0"},
                        timeout=15).json()["barData"]["priceBars"]
    s = pd.Series([float(b["close"]) for b in bars],
                  index=pd.to_datetime([b["tradeTime"] for b in bars], format="%Y%m%d%H%M%S"))
    return s.tz_localize(NY)

SYSTEM = """너는 미국 매크로·채권 이코노미스트다. 방금 발표된 미국 경제지표를 투자자에게 짧게 해석해 준다.

규칙
- 한국어 개조식 문체. 모든 문장은 "~음.", "~함.", "~임.", "~됨." 처럼 명사형으로 끝낸다. 경어체(~습니다, ~요) 금지.
  예: "근원 물가가 예상보다 높게 나옴." "연준의 12월 인하 기대를 낮추는 재료임." "2년물 금리가 5bp 올라 반응함."
- 숫자는 입력에 있는 숫자만 쓴다. 새 숫자를 계산하거나 지어내지 않는다(반올림 표기는 입력 그대로).
- 좋고 나쁨이 아니라 '물가·고용·성장 추세에 어떤 신호인지'와 '연준 정책·금리에 어떤 방향인지'를 말한다.
- 시장 반응이 숫자의 직관과 반대면(예: 예상보다 높은 물가에 금리 하락) 그 점을 짚는다. 단정하지 말고 "~로 해석됨" 정도로.
- 각 항목은 1~2문장, 군더더기 없이.

항목
- headline: 한 줄 요약 (예: "근원 PCE 예상 상회, 금리 소폭 상승 반응")
- result: 결과 — 핵심 숫자와 예상 대비
- meaning: 의미 — 추세(최근 몇 달 흐름, 근원 vs 헤드라인)에서 어떤 신호인지
- fed: 연준·금리 함의 — 인하/동결 기대와 2년물·10년물 방향
- market: 시장 반응·다음 확인 — 발표 후 30분 금리·달러·선물 움직임과 다음에 볼 지표/일정"""
SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "string"} for k in ("headline", "result", "meaning", "fed", "market")},
    "required": ["headline", "result", "meaning", "fed", "market"], "additionalProperties": False,
}
SECTIONS = (("result", "결과"), ("meaning", "의미"), ("fed", "연준·금리"), ("market", "시장 반응·다음"))


# ── 1) 발표 묶음 찾기 ──────────────────────────────────
def release_slots(rows: list[dict], day) -> list[tuple[datetime, list[dict]]]:
    """Nasdaq 하루 목록 → [(발표 시각 ET, 그 시각 행들)] 중 중요 지표가 있고 실제치가 나온 묶음."""
    by_time: dict[str, list[dict]] = {}
    for r in rows:
        t = (r.get("gmt") or "").strip()
        if re.fullmatch(r"\d{1,2}:\d{2}", t):
            by_time.setdefault(t, []).append(r)
    out = []
    for t, group in by_time.items():
        hit = [r for r in group if r["eventName"].strip().lower() in TRIGGERS and (r.get("actual") or "").strip()
               not in ("", "&nbsp;")]
        if hit:
            h, m = map(int, t.split(":"))
            out.append((datetime(day.year, day.month, day.day, h, m, tzinfo=NY), group))
    return sorted(out, key=lambda x: x[0])


def slot_key(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M ET")


# ── 2) 입력 숫자 만들기 ────────────────────────────────
def _clean(v) -> str:
    return (v or "").replace("&nbsp;", "").strip() or "-"


def _fred_context(rows: list[dict]) -> list[str]:
    """발표된 지표와 같은 FRED 계열의 최근 6개 값."""
    from data.econ_calendar import MACRO_RELEASES, _fmt, _period_label
    from data.fred import fetch_series, transform
    names = {r["eventName"].strip().lower() for r in rows}
    lines, seen = [], set()
    for items in MACRO_RELEASES.values():
        for name, sid, how, unit, freq, nasdaq_name, _ in items:
            if not nasdaq_name or nasdaq_name.lower() not in names or (sid, how) in seen:
                continue
            seen.add((sid, how))
            try:
                s = transform(fetch_series(sid), how).dropna().tail(6)
            except Exception:
                continue
            lines.append(f"- {name}: " + ", ".join(f"{_period_label(t, freq)} {_fmt(v, unit)}" for t, v in s.items()))
    return lines


def market_reaction(t: datetime, minutes: int = 30, until: datetime | None = None) -> list[str]:
    """발표 직전(5분 전)과 minutes분 뒤(또는 until 시각) 값 비교."""
    import yfinance as yf
    before, after = t - timedelta(minutes=5), until or t + timedelta(minutes=minutes)
    lines = []
    for label, sym in YIELDS:
        try:
            s = _cnbc_intraday(sym)
            if s.index[0] > pd.Timestamp(before):  # 5거래일 밖이면 값 없음
                continue
            a, b = float(s.asof(pd.Timestamp(before))), float(s.asof(pd.Timestamp(after)))
            lines.append(f"- {label}: {a:.3f}% → {b:.3f}% ({(b - a) * 100:+.1f}bp)")
        except Exception:
            continue
    for label, sym in REACTION:
        try:
            d = yf.download(sym, start=(t - timedelta(days=1)).date(), end=(t + timedelta(days=1)).date(),
                            interval="5m", progress=False, auto_adjust=False)["Close"].squeeze().dropna()
            d.index = d.index.tz_convert(NY)
            a, b = float(d.asof(pd.Timestamp(before))), float(d.asof(pd.Timestamp(after)))
        except Exception:
            continue
        if a != a or b != b:
            continue
        lines.append(f"- {label}: {a:,.2f} → {b:,.2f} ({(b / a - 1) * 100:+.2f}%)")
    return lines


def build_facts(t: datetime, rows: list[dict], reaction: list[str]) -> str:
    kst = t.astimezone(ZoneInfo("Asia/Seoul"))
    lines = [f"발표 시각: 미 동부 {t:%Y-%m-%d %H:%M} (한국 {kst:%m/%d %H:%M})", "", "이번 발표 (지표 | 실제 | 예상 | 이전):"]
    lines += [f"- {r['eventName'].strip()} | {_clean(r.get('actual'))} | {_clean(r.get('consensus'))} | "
              f"{_clean(r.get('previous'))}" for r in rows]
    ctx = _fred_context(rows)
    if ctx:
        lines += ["", "FRED 최근 추이 (최근 6개, 이번 발표분이 이미 반영됐을 수 있음):"] + ctx
    lines += ["", "발표 전후 30분 시장 반응 (발표 5분 전 → 30분 뒤):"] + (reaction or ["- (자료 없음)"])
    try:  # 정책금리 맥락
        from data.fred import fetch_series
        upper = fetch_series("DFEDTARU").dropna().iloc[-1]
        lines += ["", f"참고: 연준 기준금리 상단 {upper:.2f}%, 연준 물가 목표 2%"]
    except Exception:
        lines += ["", "참고: 연준 물가 목표 2%"]
    return "\n".join(lines)


# ── 3) 숫자 검증 ───────────────────────────────────────
_NUMS = re.compile(r"\d+(?:[.,]\d+)*")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMS.findall(text)}


def unknown_numbers(answer: str, facts: str) -> set[str]:
    """응답 속 숫자 중 입력에 없는 것. 소수 끝 0 차이(4.30 vs 4.3)는 같은 숫자로 본다."""
    def norm(x: str) -> str:
        return x.rstrip("0").rstrip(".") if "." in x else x
    allowed = {norm(n) for n in _numbers(facts)} | {"2", "30", "10", "5"}  # 물가 목표·반응 창·만기 같은 상수
    return {n for n in _numbers(answer) if norm(n) not in allowed}


def drop_sentences(text: str, bad: set[str]) -> str:
    keep = [s for s in re.split(r"(?<=[.。])\s+", text) if not (_numbers(s) & bad)]
    return " ".join(keep).strip()


# ── 4) 실행 ───────────────────────────────────────────
def interpret(facts: str) -> tuple[dict, dict]:
    from summarize import run_claude
    data, meta = run_claude(SYSTEM, SCHEMA, facts, "아래 발표 숫자와 시장 반응을 규칙에 따라 해석해 JSON으로만 답하라.")
    bad = set().union(*(unknown_numbers(v, facts) for v in data.values()))
    if bad:
        data, meta = run_claude(SYSTEM, SCHEMA, facts,
                                f"입력에 없는 숫자({', '.join(sorted(bad))})를 쓰지 말고 다시 해석해 JSON으로만 답하라.")
        bad = set().union(*(unknown_numbers(v, facts) for v in data.values()))
        if bad:
            data = {k: drop_sentences(v, bad) for k, v in data.items()}
    return data, {**(meta or {}), "dropped_numbers": sorted(bad)}


CLOSE_AT = (16, 20)              # 미 동부 16:20 이후 (정규장 16:00 마감 + 데이터 여유) 장 마감 반응 덧붙임
CLOSE_GIVE_UP = timedelta(hours=30)
CLOSE_SYSTEM = """너는 미국 매크로·채권 이코노미스트다. 오늘 발표된 지표에 대한 시장 반응을
'발표 후 30분'과 '장 마감까지'로 비교해 한두 문장으로 정리한다.

규칙
- 한국어. 모든 문장은 "~음.", "~함.", "~임.", "~됨." 처럼 명사형으로 끝낸다. 경어체 금지.
- 숫자는 입력에 있는 숫자만 쓴다.
- 30분 반응보다 커졌는지(확대), 되돌렸는지(반납), 방향이 바뀌었는지를 금리(2년물·10년물) 중심으로 말하고,
  지표 외 다른 재료가 섞였을 수 있으면 단정하지 않는다("~로 보임")."""
CLOSE_SCHEMA = {"type": "object", "properties": {"close": {"type": "string"}}, "required": ["close"],
                "additionalProperties": False}


def close_time(slot: str) -> datetime:
    day = datetime.strptime(slot[:10], "%Y-%m-%d")
    return datetime(day.year, day.month, day.day, *CLOSE_AT, tzinfo=NY)


def interpret_close(facts: str) -> str:
    from summarize import run_claude
    data, _ = run_claude(CLOSE_SYSTEM, CLOSE_SCHEMA, facts, "아래 반응을 규칙에 따라 정리해 JSON으로만 답하라.")
    text = data["close"]
    bad = unknown_numbers(text, facts)
    return drop_sentences(text, bad) if bad else text


def add_close(ins: dict, send=None, log=None) -> dict:
    """해석에 장 마감 반응 한 줄을 덧붙여 저장하고 텔레그램으로 짧게 보낸다."""
    t = datetime.strptime(ins["slot"], "%Y-%m-%d %H:%M ET").replace(tzinfo=NY)
    close = datetime(t.year, t.month, t.day, 16, 0, tzinfo=NY)
    lines = market_reaction(t, until=close)
    if not lines:
        return ins
    thirty = ins["facts"].split("발표 전후 30분 시장 반응")[-1].split("참고:")[0].strip()
    facts = (f"발표: {ins['when_kst']} (KST) · {ins['headline']}\n\n발표 후 30분 반응 {thirty}\n\n"
             "발표 5분 전 → 장 마감(미 동부 16:00) 반응:\n" + "\n".join(lines))
    text = interpret_close(facts)
    ins = {**ins, "close": {"text": text, "lines": lines, "facts": facts}}
    STORE.write_text(json.dumps([ins if x["slot"] == ins["slot"] else x for x in load()], ensure_ascii=False, indent=1))
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"slot": ins["slot"], "close": ins["close"]}, ensure_ascii=False) + "\n")
    if send:
        try:
            send(close_text(ins))
        except Exception as e:
            if log:
                log.warning(f"장 마감 반응 텔레그램 전송 실패: {e}")
    if log:
        log.info(f"장 마감 반응 덧붙임: {ins['slot']}")
    return ins


def close_text(ins: dict) -> str:
    return (f"🔔 {ins['when_kst']} 발표 · 장 마감 반응\n{ins['headline']}\n\n" + "\n".join(ins["close"]["lines"])
            + f"\n\n{ins['close']['text']}")


def load() -> list[dict]:
    try:
        return json.loads(STORE.read_text())
    except Exception:
        return []


def telegram_text(ins: dict) -> str:
    body = "\n".join(f"• {lab}: {ins[k]}" for k, lab in SECTIONS if ins.get(k))
    return f"🧠 {ins['when_kst']} 발표 해석 (KST)\n{ins['headline']}\n\n{body}"


def check_and_run(now: datetime | None = None, send=None, log=None) -> list[dict]:
    """오늘 발표 묶음 중 30분이 지났고 아직 해석하지 않은 것을 해석. 새로 만든 해석 목록을 돌려준다."""
    from data.consensus import fetch_day
    now = (now or datetime.now(NY)).astimezone(NY)
    done = {x["slot"] for x in load()}
    new = []
    for day in (now.date() - timedelta(days=1), now.date()):  # 자정 넘어 따라잡기용으로 전날도
        try:
            rows = fetch_day(day)
        except Exception:
            continue
        for t, group in release_slots(rows, day):
            key = slot_key(t)
            if key in done or not (t + WAIT <= now <= t + GIVE_UP):
                continue
            facts = build_facts(t, group, market_reaction(t))
            data, meta = interpret(facts)
            kst = t.astimezone(ZoneInfo("Asia/Seoul"))
            ins = {"slot": key, "when_kst": f"{kst:%m/%d %H:%M}", "created": datetime.now(NY).isoformat(timespec="seconds"),
                   "events": [r["eventName"].strip() for r in group], **data, "facts": facts, "meta": meta}
            store = [ins] + [x for x in load() if x["slot"] != key]
            STORE.write_text(json.dumps(store[:60], ensure_ascii=False, indent=1))
            HISTORY.parent.mkdir(exist_ok=True)
            with HISTORY.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ins, ensure_ascii=False) + "\n")
            if send:
                try:
                    send(telegram_text(ins))
                except Exception as e:
                    if log:
                        log.warning(f"해석 텔레그램 전송 실패: {e}")
            if log:
                log.info(f"발표 해석 저장: {key} ({', '.join(ins['events'][:4])}…)")
            new.append(ins)
            done.add(key)
    # 장 마감 반응: 해석은 있는데 아직 마감 반응이 없고, 그날 미 동부 16:20이 지난 것
    for ins in load():
        if "close" not in ins and close_time(ins["slot"]) <= now <= close_time(ins["slot"]) + CLOSE_GIVE_UP:
            try:
                add_close(ins, send=send, log=log)
            except Exception as e:
                if log:
                    log.warning(f"장 마감 반응 실패 ({ins['slot']}): {e}")
    return new
