"""경제지표 서프라이즈 (실제 − 예상) — Nasdaq 경제 캘린더 기반.

Nasdaq은 과거 날짜의 실제치·예상치를 함께 주므로 기간을 소급해서 채울 수 있다
(이전 방식인 ForexFactory 주간 보관은 2026-10월부터만 쌓였음).
같은 출처의 실제치·예상치를 비교하므로 반올림 기준도 같다.
"""
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st

import config
from data.consensus import fetch_day, parse_value, release_time_kst

# Nasdaq 지표명(소문자) → (표시명, 분야)
INDICATORS = {
    "nonfarm payrolls": ("비농업 고용", "고용"),
    "unemployment rate": ("실업률", "고용"),
    "average hourly earnings": ("평균 시간당 임금 (전월비)", "고용"),
    "average hourly earnings (yoy)": ("평균 시간당 임금 (전년비)", "고용"),
    "initial jobless claims": ("신규 실업수당 청구", "고용"),
    "jolts job openings": ("JOLTS 구인건수", "고용"),
    "adp nonfarm employment change": ("ADP 민간고용", "고용"),
    "cpi": ("CPI", "물가"),
    "core cpi": ("근원 CPI", "물가"),
    "ppi": ("PPI", "물가"),
    "core ppi": ("근원 PPI", "물가"),
    "pce price index": ("PCE", "물가"),
    "core pce price index": ("근원 PCE", "물가"),
    "michigan 1-year inflation expectations": ("미시간대 1년 기대인플레", "물가"),
    "michigan 5-year inflation expectations": ("미시간대 5년 기대인플레", "물가"),
    "gdp": ("GDP (전기비 연율)", "성장·소비"),
    "retail sales": ("소매판매", "성장·소비"),
    "core retail sales": ("근원 소매판매", "성장·소비"),
    "durable goods orders": ("내구재 주문", "성장·소비"),
    "trade balance": ("무역수지", "성장·소비"),
    "ism manufacturing pmi": ("ISM 제조업 PMI", "심리·제조업"),
    "ism non-manufacturing pmi": ("ISM 서비스업 PMI", "심리·제조업"),
    "michigan consumer sentiment": ("미시간대 소비자심리", "심리·제조업"),
    "ny empire state manufacturing index": ("뉴욕 연은 제조업지수", "심리·제조업"),
    "building permits": ("건축허가", "주택"),
    "housing starts": ("주택착공", "주택"),
    "existing home sales": ("기존주택 판매", "주택"),
    "new home sales": ("신규주택 판매", "주택"),
}
# 같은 이름으로 전월비·전년비 두 줄이 오는 지표
PAIRED = {"cpi", "core cpi", "ppi", "core ppi", "pce price index", "core pce price index", "retail sales"}


def _unit(text: str) -> str:
    t = (text or "").strip()
    return t[-1] if t and t[-1] in "%KMBT" else ""


def _decimals(text: str) -> int:
    t = (text or "").replace(",", "").strip().rstrip("%KMBT")
    return len(t.split(".")[1]) if "." in t else 0


def _day_rows(day) -> list[dict]:
    """그날 발표된 대상 지표 중 실제치·예상치가 모두 있는 것."""
    try:
        rows = fetch_day(day)
    except Exception:
        return []
    picked = [x for x in rows if x["eventName"].strip().lower() in INDICATORS
              and parse_value(x["actual"]) is not None and parse_value(x["consensus"]) is not None]
    out = []
    for key in sorted({x["eventName"].strip().lower() for x in picked}):
        group = [x for x in picked if x["eventName"].strip().lower() == key]
        name, category = INDICATORS[key]
        if key in PAIRED and len(group) >= 2:
            # 이전치 절댓값이 큰 쪽이 전년비 (예: CPI 3.4% vs 0.4%)
            group = sorted(group, key=lambda x: abs(parse_value(x["previous"]) or parse_value(x["actual"])))
            labeled = [(group[0], f"{name} (전월비)"), (group[-1], f"{name} (전년비)")]
        elif key in PAIRED:
            # 한 줄만 예상치가 있을 때: 소매판매는 전년비에 예상치가 없어 전월비,
            # 물가는 전월비가 보통 ±1.5% 이내라 크기로 구분
            x = group[0]
            mom = key == "retail sales" or abs(parse_value(x["actual"])) < 1.5
            labeled = [(x, f"{name} ({'전월비' if mom else '전년비'})")]
        elif len(group) >= 2:
            # 같은 이름에 수준(3.75M)과 변화율(-5.5%)이 함께 오는 지표 (기존주택 판매 등)
            labeled = [(x, f"{name} (전월비)" if _unit(x["actual"]) == "%" and name.find("(") < 0 else name)
                       for x in group]
        else:
            labeled = [(x, name) for x in group]
        for x, label in labeled:
            actual, cons = parse_value(x["actual"]), parse_value(x["consensus"])
            dec = max(_decimals(x["consensus"]), _decimals(x["actual"]))
            out.append({
                "발표(KST)": pd.Timestamp(release_time_kst(day, x.get("gmt")) or pd.Timestamp(day)),
                "분야": category, "지표": label,
                "실제": x["actual"].strip(), "예상": x["consensus"].strip(),
                "이전": (x["previous"] or "").replace("&nbsp;", "").strip() or "-",
                "_diff": round(actual - cons, dec + 2),
                "_unit": _unit(x["consensus"]) or _unit(x["actual"]), "_dec": dec,
            })
    return out


def _label(diff: float, unit: str, dec: int) -> str:
    if abs(diff) < 10 ** -dec / 2:
        return "= 부합"
    shown = "%p" if unit == "%" else unit
    return f"{'▲ 상회' if diff > 0 else '▼ 하회'} {diff:+.{dec}f}{shown}"


@st.cache_data(ttl=config.TTL_MACRO, show_spinner=False)
def get_surprises(weeks: int = 12) -> pd.DataFrame:
    """최근 weeks주 핵심 지표의 실제·예상·서프라이즈 (최신순). 지난 날짜는 디스크 캐시라 처음만 느림."""
    end = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    days = [d.date() for d in pd.bdate_range(end - pd.DateOffset(weeks=weeks), end)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = [r for day_rows in ex.map(_day_rows, days) for r in day_rows]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["서프라이즈"] = [_label(d, u, k) for d, u, k in zip(df["_diff"], df["_unit"], df["_dec"])]
    return df.sort_values("발표(KST)", ascending=False).reset_index(drop=True)
