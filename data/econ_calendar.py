"""미국 경제지표: 이번 주 발표 일정(ForexFactory) + 최근 발표치(FRED)."""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import pandas as pd
import requests
import streamlit as st

import config
from data.consensus import find_event, match_actual, parse_value, release_time_kst
from data.fred import fetch_next_release_date, fetch_release_dates, fetch_series, fred_today, transform

FF_WEEK_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_CACHE = config.CACHE_DIR / "ff_calendar_thisweek.json"
FF_MAX_AGE = 60 * 60  # FF는 잦은 호출 시 429로 차단하므로 1시간에 한 번만 받음

IMPACT_KO = {"High": "🔴 높음", "Medium": "🟠 중간", "Low": "🟡 낮음", "Holiday": "⚪ 휴일"}

# ForexFactory 지표명 → 한글 (없으면 원문 그대로)
TITLE_KO = {
    "CPI m/m": "CPI (전월비)",
    "CPI y/y": "CPI (전년비)",
    "Core CPI m/m": "근원 CPI (전월비)",
    "PPI m/m": "PPI (전월비)",
    "Core PPI m/m": "근원 PPI (전월비)",
    "Core PCE Price Index m/m": "근원 PCE (전월비)",
    "Non-Farm Employment Change": "비농업 고용",
    "Unemployment Rate": "실업률",
    "Average Hourly Earnings m/m": "평균 시간당 임금 (전월비)",
    "Unemployment Claims": "신규 실업수당 청구",
    "JOLTS Job Openings": "JOLTS 구인건수",
    "ADP Non-Farm Employment Change": "ADP 민간고용",
    "ISM Manufacturing PMI": "ISM 제조업 PMI",
    "ISM Services PMI": "ISM 서비스업 PMI",
    "Retail Sales m/m": "소매판매 (전월비)",
    "Core Retail Sales m/m": "근원 소매판매 (전월비)",
    "Advance GDP q/q": "GDP 속보치 (전기비 연율)",
    "Prelim GDP q/q": "GDP 잠정치 (전기비 연율)",
    "Final GDP q/q": "GDP 확정치 (전기비 연율)",
    "Federal Funds Rate": "기준금리 (FOMC)",
    "FOMC Meeting Minutes": "FOMC 의사록",
    "Prelim UoM Consumer Sentiment": "미시간대 소비자심리 (예비)",
    "CB Consumer Confidence": "컨퍼런스보드 소비자신뢰",
    "Building Permits": "건축허가",
    "Housing Starts": "주택착공",
    "Existing Home Sales": "기존주택 판매",
    "New Home Sales": "신규주택 판매",
    "Durable Goods Orders m/m": "내구재 주문 (전월비)",
    "Trade Balance": "무역수지",
    "Core CPI y/y": "근원 CPI (전년비)",
    "PCE Price Index m/m": "PCE (전월비)",
    "Industrial Production m/m": "산업생산 (전월비)",
    "Core PCE Price Index y/y": "근원 PCE (전년비)",
    "Flash Manufacturing PMI": "S&P 제조업 PMI (예비)",
    "Flash Services PMI": "S&P 서비스업 PMI (예비)",
    "Final Manufacturing PMI": "S&P 제조업 PMI (확정)",
    "Final Services PMI": "S&P 서비스업 PMI (확정)",
    "ISM Manufacturing Prices": "ISM 제조업 가격지수",
    "ISM Services Prices": "ISM 서비스업 가격지수",
    "ADP Weekly Employment Change": "ADP 주간 고용",
    "HPI m/m": "FHFA 주택가격 (전월비)",
    "S&P/CS Composite-20 HPI y/y": "케이스-실러 주택가격 (전년비)",
    "Richmond Manufacturing Index": "리치먼드 연은 제조업지수",
    "Empire State Manufacturing Index": "뉴욕 연은 제조업지수",
    "Philly Fed Manufacturing Index": "필라델피아 연은 제조업지수",
    "Chicago PMI": "시카고 PMI",
    "API Weekly Statistical Bulletin": "API 원유 재고",
    "Crude Oil Inventories": "EIA 원유 재고",
    "Natural Gas Storage": "천연가스 재고",
    "Personal Spending m/m": "개인소비지출 (전월비)",
    "Personal Income m/m": "개인소득 (전월비)",
    "Core Durable Goods Orders m/m": "근원 내구재 주문 (전월비)",
    "Prelim GDP Price Index q/q": "GDP 물가지수 잠정치 (전기비)",
    "Advance GDP Price Index q/q": "GDP 물가지수 속보치 (전기비)",
    "Final GDP Price Index q/q": "GDP 물가지수 확정치 (전기비)",
    "Beige Book": "베이지북",
    "Bank Holiday": "휴장 (공휴일)",
    "Goods Trade Balance": "상품수지",
    "Prelim Wholesale Inventories m/m": "도매재고 예비치 (전월비)",
    "Business Inventories m/m": "기업재고 (전월비)",
    "Revised UoM Consumer Sentiment": "미시간대 소비자심리 (확정)",
    "Prelim UoM Inflation Expectations": "미시간대 기대인플레 (예비)",
    "Revised UoM Inflation Expectations": "미시간대 기대인플레 (확정)",
    "Pending Home Sales m/m": "잠정주택 판매 (전월비)",
    "NAHB Housing Market Index": "NAHB 주택시장지수",
    "Import Prices m/m": "수입물가 (전월비)",
    "Employment Cost Index q/q": "고용비용지수 (전기비)",
    "Capacity Utilization Rate": "설비가동률",
    "Federal Budget Balance": "재정수지",
    "Challenger Job Cuts y/y": "챌린저 감원 (전년비)",
    "Prelim Nonfarm Productivity q/q": "비농업 생산성 잠정치 (전기비)",
    "Prelim Unit Labor Costs q/q": "단위노동비용 잠정치 (전기비)",
    "Current Account": "경상수지",
    "TIC Long-Term Purchases": "TIC 장기증권 순매입",
    "Average Hourly Earnings y/y": "평균 시간당 임금 (전년비)",
    "FOMC Statement": "FOMC 성명",
    "FOMC Press Conference": "FOMC 기자회견",
    "FOMC Economic Projections": "FOMC 경제전망 (점도표)",
}
_SPEECH = [(re.compile(r"^Fed Chair (\w+) Speaks$"), "연준 의장 {} 발언"),
           (re.compile(r"^FOMC Member (\w+) Speaks$"), "연준 {} 발언"),
           (re.compile(r"^Treasury Sec (\w+) Speaks$"), "재무장관 {} 발언"),
           (re.compile(r"^President (\w+) Speaks$"), "{} 대통령 발언")]
_AUCTION = re.compile(r"^(\d+)-y (?:Bond|Note) Auction$")


def title_ko(title: str) -> str:
    """ForexFactory 지표명 → 한글. 사전에 없으면 발언·입찰 패턴, 그래도 없으면 원문."""
    if title in TITLE_KO:
        return TITLE_KO[title]
    for pattern, fmt in _SPEECH:
        m = pattern.match(title)
        if m:
            return fmt.format(m.group(1))
    m = _AUCTION.match(title)
    return f"{m.group(1)}년물 국채 입찰" if m else title


def _load_ff_events() -> list[dict]:
    """파일 캐시가 1시간 이내면 재사용, 아니면 새로 받고 실패 시 마지막 캐시로 대체."""
    if FF_CACHE.exists() and time.time() - FF_CACHE.stat().st_mtime < FF_MAX_AGE:
        return json.loads(FF_CACHE.read_text())
    try:
        r = requests.get(FF_WEEK_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        FF_CACHE.write_text(r.text)
        return r.json()
    except Exception:
        if FF_CACHE.exists():
            return json.loads(FF_CACHE.read_text())
        raise


def ff_fetched_at() -> datetime | None:
    return datetime.fromtimestamp(FF_CACHE.stat().st_mtime) if FF_CACHE.exists() else None


@st.cache_data(ttl=FF_MAX_AGE, show_spinner=False)
def get_week_calendar() -> pd.DataFrame:
    events = _load_ff_events()
    df = pd.DataFrame([e for e in events if e.get("country") == "USD"])
    if df.empty:
        return df
    dt = pd.to_datetime(df["date"], utc=True).dt.tz_convert("Asia/Seoul")
    return pd.DataFrame({
        "일시(KST)": dt,
        "지표": df["title"].map(title_ko),
        "중요도": df["impact"].map(lambda i: IMPACT_KO.get(i, i)),
        "예상": df["forecast"].replace("", None),
        "이전": df["previous"].replace("", None),
        "_impact": df["impact"],
        "_key": df["title"] + "|" + df["date"],
    }).sort_values("일시(KST)").reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False)
def get_week_actuals() -> dict[str, str]:
    """이번 주 일정 중 발표가 끝난 것의 실제치 {title|date: '54.9'} (Nasdaq 캘린더에서 매칭)."""
    now = pd.Timestamp.now(tz="America/New_York")
    out = {}
    for e in _load_ff_events():
        if e.get("country") != "USD":
            continue
        t = pd.Timestamp(e["date"]).tz_convert("America/New_York")
        if t > now:
            continue
        try:
            actual = match_actual(t.to_pydatetime(), e["title"], e.get("forecast"), e.get("previous"))
        except Exception:
            actual = None
        if actual:
            out[f"{e['title']}|{e['date']}"] = actual
    return out


# (표시명, FRED ID, 변환, 단위, 주기, Nasdaq 지표명, FRED→Nasdaq 단위 배율)
# 변환: yoy=전년비%, mom=전월비%, diff=전기 대비 증감, level=원값
# CPI 전년비는 BLS 공식 발표와 같은 원계열(NSA) 지수로 계산
RECENT_INDICATORS = [
    ("CPI (전년비)",          "CPIAUCNS",        "yoy",   "%",   "M", "CPI",                        1),
    ("근원 CPI (전년비)",     "CPILFENS",        "yoy",   "%",   "M", "Core CPI",                   1),
    ("PCE (전년비)",          "PCEPI",           "yoy",   "%",   "M", "PCE Price Index",            1),
    ("근원 PCE (전년비)",     "PCEPILFE",        "yoy",   "%",   "M", "Core PCE Price Index",       1),
    ("PPI (전년비)",          "PPIFIS",          "yoy",   "%",   "M", "PPI",                        1),
    ("근원 PPI (전년비)",     "PPIFES",          "yoy",   "%",   "M", "Core PPI",                   1),
    ("비농업 고용 증감",       "PAYEMS",          "diff",  "천명", "M", "Nonfarm Payrolls",           1),
    ("실업률",                "UNRATE",          "level", "%",   "M", "Unemployment Rate",          1),
    ("신규 실업수당 청구",     "ICSA",            "level", "건",  "W", "Initial Jobless Claims",     1e-3),
    ("소매판매 (전월비)",      "RSAFS",           "mom",   "%",   "M", "Retail Sales",               1),
    ("실질 GDP (연율)",        "A191RL1Q225SBEA", "level", "%",   "Q", "GDP",                        1),
]


def _period_label(ts: pd.Timestamp, freq: str) -> str:
    # 표 폭이 좁아 짧게: 26.08 / 26 Q2 / 09/26(주간)
    if freq == "M":
        return ts.strftime("%y.%m")
    if freq == "Q":
        return f"{ts:%y} Q{ts.quarter}"
    return ts.strftime("%m/%d")


def _fmt(v: float, unit: str) -> str:
    if unit == "건":
        return f"{v / 1000:,.0f}K"
    if unit == "천명":
        return f"{v:+,.0f}K"
    if unit == "M":  # FRED 천 단위 → 백만 (JOLTS 구인, 주택착공·허가)
        return f"{v / 1000:,.2f}M"
    return f"{v:.2f}%" if unit == "%" else f"{v:,.2f}"


def _cons_unit(unit: str) -> str:
    """표시 단위 → 컨센서스 비교 단위 (Nasdaq 표기)."""
    return {"%": "%", "M": "M"}.get(unit, "K")


def _consensus(days: list[date], nasdaq_name: str, fred_value: float, unit: str) -> dict:
    """마지막 발표일의 Nasdaq 컨센서스로 서프라이즈 계산.

    판정은 같은 출처·같은 반올림인 Nasdaq 실제치 vs 컨센서스로 하고,
    FRED 값과 Nasdaq 실제치가 반올림 이상으로 다르면 경고 표시.
    """
    # 발표 일시: 컨센서스를 찾은 날(+Nasdaq 발표 시각), 못 찾으면 현재 값이 FRED에 올라온 날
    fallback_day = pd.Timestamp(days[0]) if days else None
    empty = {"발표": fallback_day, "예상": "-", "서프라이즈": "-", "_surprise": 0.0, "_warn": "", "_notime": True}
    try:
        ev, timed = None, None
        # 현재 값이 정해진 날부터 거슬러 올라가며 Nasdaq에 해당 지표가 있는 첫 날 사용
        for day in days[:3]:
            ev = find_event(day, nasdaq_name, fred_value)
            if ev and timed is None and release_time_kst(day, ev.get("time")):
                timed = release_time_kst(day, ev.get("time"))  # 컨센서스가 없어도 발표 시각은 쓴다
            if ev and ev["consensus"] is not None:
                break
    except Exception:
        return empty
    if not ev or ev["consensus"] is None:
        return {**empty, "발표": pd.Timestamp(timed) if timed else fallback_day, "_notime": not timed}
    timed = release_time_kst(day, ev.get("time"))
    released = timed or pd.Timestamp(day)
    actual = ev["actual"] if ev["actual"] is not None else fred_value
    diff = actual - ev["consensus"]
    pct = unit == "%"
    dec = {"%": 1, "M": 2}.get(unit, 0)
    suffix = {"%": "%", "M": "M"}.get(unit, "K")
    tol = 0.15 if pct else abs(fred_value) * 0.03 + 0.01 if unit == "M" else max(2.0, abs(fred_value) * 0.03)
    warn = (
        f"Nasdaq 실제치 {ev['actual']:g}{suffix}가 FRED 값과 다름"
        if ev["actual"] is not None and abs(ev["actual"] - fred_value) > tol else ""
    )
    if abs(diff) < 10 ** -dec / 2:
        label = "= 부합"
    else:
        label = f"{'▲ 상회' if diff > 0 else '▼ 하회'} {diff:+.{dec}f}{'%p' if pct else suffix}"
    return {
        "발표": pd.Timestamp(released),
        "예상": f"{ev['consensus']:,.{dec}f}{suffix}",
        "서프라이즈": label + (" ⚠" if warn else ""),
        "_surprise": diff,
        "_warn": warn,
        "_notime": timed is None,
    }


_STEP = {"M": pd.DateOffset(months=1), "Q": pd.DateOffset(months=3), "W": pd.DateOffset(weeks=1)}


def _fmt_nasdaq(v: float, unit: str) -> str:
    """Nasdaq 실제치(발표 반올림 그대로) 표시."""
    if unit == "천명":
        return f"{v:+,.0f}K"
    if unit == "건":
        return f"{v:,.0f}K"
    if unit == "M":
        return f"{v:,.2f}M"
    return f"{v:.1f}%" if unit == "%" else f"{v:,.2f}"


def _flash(item, s: pd.Series, nxt, days: list) -> dict | None:
    """오늘(미국) 발표됐는데 FRED에 아직 안 올라왔으면 Nasdaq 실제치로 새 기간 '속보' 행.
    FRED에 올라오면(현재 값이 정해진 날 = 오늘) 자동으로 FRED 값으로 돌아간다."""
    name, sid, how, unit, freq, nasdaq_name, scale = item
    today = fred_today()
    if not nasdaq_name or freq not in _STEP or nxt != today or (days and days[0] >= today):
        return None
    last = s.iloc[-1]
    ev = find_event(today, nasdaq_name, last * scale)  # 직전 값과 가까운 행 = 같은 종류(전월비/전년비)
    if not ev or ev["actual"] is None:
        return None
    cons = _consensus([today], nasdaq_name, ev["actual"], _cons_unit(unit))
    return {
        "지표": name, "발표": cons.pop("발표"), "최신": _fmt_nasdaq(ev["actual"], unit), **cons,
        "이전": _fmt(last, unit), "기준": _period_label(s.index[-1] + _STEP[freq], freq),
        "다음 발표": "-", "_delta": ev["actual"] / scale - last, "_next": None, "_flash": True,
    }


def _one(item) -> dict:
    name, sid, how, unit, freq, nasdaq_name, nasdaq_scale = item
    s = transform(fetch_series(sid), how).dropna()
    try:
        nxt = fetch_next_release_date(sid)
    except Exception:
        nxt = None
    try:
        days = fetch_release_dates(sid, s.index[-1].date())
    except Exception:
        days = []
    try:
        flash = _flash(item, s, nxt, days)
    except Exception:
        flash = None
    if flash:
        return flash
    last, prev = s.iloc[-1], s.iloc[-2]
    if nasdaq_name:
        cons = _consensus(days, nasdaq_name, last * nasdaq_scale, _cons_unit(unit))
    else:  # 시장 지표 등 컨센서스가 없는 계열: 발표일만
        cons = {"발표": pd.Timestamp(days[0]) if days else None, "예상": "-", "서프라이즈": "-", "_surprise": 0.0,
                "_warn": "", "_notime": True}
    return {
        "지표": name,
        "발표": cons.pop("발표"),
        "최신": _fmt(last, unit),
        **cons,
        "이전": _fmt(prev, unit),
        "기준": _period_label(s.index[-1], freq),
        "다음 발표": nxt.strftime("%m/%d") if nxt else "-",
        "_delta": last - prev,
        "_next": nxt,
        "_flash": False,
    }


def _table(items: list) -> pd.DataFrame:
    def safe(item):
        try:
            return _one(item)
        except Exception:
            return {"지표": item[0], "발표": None, "최신": "-", "예상": "-", "서프라이즈": "-", "이전": "-", "기준": "-",
                    "다음 발표": "-", "_delta": 0, "_surprise": 0.0, "_warn": "", "_notime": True, "_next": None,
                    "_flash": False}
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(safe, items))
    # 실패한 줄(대개 FRED 분당 호출 한도)은 한 번 더, 차례로
    rows = [safe(item) if row["발표"] is None and row["최신"] == "-" else row for item, row in zip(items, rows)]
    if all(r["최신"] == "-" for r in rows):  # 전부 실패면 예외 → 빈 표가 캐시에 남지 않게
        raise RuntimeError("FRED 지표를 하나도 받지 못함")
    return pd.DataFrame(rows)


def release_hours(now: pd.Timestamp | None = None) -> bool:
    """미국 지표 발표·FRED 반영이 몰리는 시간대(미 동부 평일 08:25~12:30)인지. 이때는 2분 캐시·1분 자동 새로고침."""
    now = now or pd.Timestamp.now(tz="America/New_York")
    now = now.tz_convert("America/New_York") if now.tzinfo else now
    return now.weekday() < 5 and "08:25" <= f"{now:%H:%M}" < "12:30"


def refresh_bucket(now: pd.Timestamp | None = None) -> int:
    """캐시 구분 키: 발표 시간대엔 2분마다, 그 밖엔 15분마다 바뀐다."""
    now = now or pd.Timestamp.now(tz="America/New_York")
    step = 120 if release_hours(now) else 900
    return int(now.timestamp() // step)


@st.cache_data(ttl=config.TTL_MACRO, show_spinner=False, max_entries=20)
def get_recent_indicators(bucket: int = 0) -> pd.DataFrame:
    """bucket: refresh_bucket() — 발표 시간대에 캐시를 2분으로 줄이는 용도."""
    return _table(RECENT_INDICATORS)


# 매크로 화면 탭별 발표 현황 (형식은 RECENT_INDICATORS와 같음, Nasdaq 지표명 None = 컨센서스 없음)
# 전월비는 계절조정(SA) 계열, CPI 전년비는 BLS 공식과 같은 원계열(NSA)
MACRO_RELEASES = {
    "물가": [
        ("CPI 전월비",           "CPIAUCSL",      "mom",  "%",   "M", "CPI",                     1),
        ("CPI 전년비",           "CPIAUCNS",      "yoy",  "%",   "M", "CPI",                     1),
        ("근원 CPI 전월비",      "CPILFESL",      "mom",  "%",   "M", "Core CPI",                1),
        ("근원 CPI 전년비",      "CPILFENS",      "yoy",  "%",   "M", "Core CPI",                1),
        ("PCE 전월비",           "PCEPI",         "mom",  "%",   "M", "PCE Price Index",         1),
        ("PCE 전년비",           "PCEPI",         "yoy",  "%",   "M", "PCE Price Index",         1),
        ("근원 PCE 전월비",      "PCEPILFE",      "mom",  "%",   "M", "Core PCE Price Index",    1),
        ("근원 PCE 전년비",      "PCEPILFE",      "yoy",  "%",   "M", "Core PCE Price Index",    1),
        ("PPI 전월비",           "PPIFIS",        "mom",  "%",   "M", "PPI",                     1),
        ("PPI 전년비",           "PPIFIS",        "yoy",  "%",   "M", "PPI",                     1),
        ("근원 PPI 전월비",      "PPIFES",        "mom",  "%",   "M", "Core PPI",                1),
        ("근원 PPI 전년비",      "PPIFES",        "yoy",  "%",   "M", "Core PPI",                1),
    ],
    "고용": [
        ("비농업 고용 증감",     "PAYEMS",        "diff", "천명", "M", "Nonfarm Payrolls",        1),
        ("민간 고용 증감",       "USPRIV",        "diff", "천명", "M", "Private Nonfarm Payrolls", 1),
        ("실업률",               "UNRATE",        "level", "%",  "M", "Unemployment Rate",       1),
        ("경제활동참가율",       "CIVPART",       "level", "%",  "M", "Participation Rate",      1),
        ("시간당 임금 전월비",   "CES0500000003", "mom",  "%",   "M", "Average Hourly Earnings", 1),
        ("시간당 임금 전년비",   "CES0500000003", "yoy",  "%",   "M", "Average Hourly Earnings (YoY)", 1),
        ("신규 실업수당 청구",   "ICSA",          "level", "건", "W", "Initial Jobless Claims",  1e-3),
        ("계속 실업수당 청구",   "CCSA",          "level", "건", "W", "Continuing Jobless Claims", 1e-3),
        ("JOLTS 구인건수",       "JTSJOL",        "level", "M",  "M", "JOLTS Job Openings",      1e-3),
    ],
    "성장·소비": [
        ("실질 GDP (전기비 연율)", "A191RL1Q225SBEA", "level", "%", "Q", "GDP",                  1),
        ("소매판매 전월비",      "RSAFS",         "mom",  "%",   "M", "Retail Sales",            1),
        ("소매판매 전년비",      "RSAFS",         "yoy",  "%",   "M", "Retail Sales",            1),
        ("개인소비지출 전월비",  "PCE",           "mom",  "%",   "M", "Personal Spending",       1),
        ("개인소득 전월비",      "PI",            "mom",  "%",   "M", "Personal Income",         1),
        ("산업생산 전월비",      "INDPRO",        "mom",  "%",   "M", "Industrial Production",   1),
        ("산업생산 전년비",      "INDPRO",        "yoy",  "%",   "M", "Industrial Production",   1),
        ("내구재 주문 전월비",   "DGORDER",       "mom",  "%",   "M", "Durable Goods Orders",    1),
        ("주택착공 (연율)",      "HOUST",         "level", "M",  "M", "Housing Starts",          1e-3),
        ("건축허가 (연율)",      "PERMIT",        "level", "M",  "M", "Building Permits",        1e-3),
    ],
    "금융 여건": [
        ("하이일드 스프레드",    "BAMLH0A0HYM2",  "level", "%",  "D", None,                      1),
        ("투자등급 스프레드",    "BAMLC0A0CM",    "level", "%",  "D", None,                      1),
        ("금융여건지수 NFCI",    "NFCI",          "level", "",   "W", None,                      1),
        ("금융스트레스지수",     "STLFSI4",       "level", "",   "W", None,                      1),
        ("30년 모기지 금리",     "MORTGAGE30US",  "level", "%",  "W", None,                      1),
    ],
}


@st.cache_data(ttl=config.TTL_MACRO, show_spinner=False, max_entries=80)
def get_macro_releases(group: str, bucket: int = 0) -> pd.DataFrame:
    """매크로 탭 발표 현황: 지표·기준 기간·최신·이전·예상·서프라이즈·발표(KST)·다음 발표."""
    return _table(MACRO_RELEASES[group])
