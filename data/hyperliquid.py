"""주말·휴장 때 보는 24시간 시세: 하이퍼리퀴드 XYZ 시장(주식·지수·원자재 무기한 선물, 공개 조회 API).

- 하이퍼리퀴드의 주식·지수 시장 중 실제로 거래되는 곳은 XYZ 뿐 (다른 거래소는 거래 0, 가격도 실제와 동떨어짐 → 제외)
- 핵심 숫자 = '직전 정규장 마감 대비': 미국 자산 16:00 ET, 한국·일본 자산 15:30 KST, 원자재 17:00 ET (평일 기준,
  휴장일은 반영하지 않음). 마감 시각의 15분봉 종가를 기준 가격으로 쓴다 → 월요일 갭을 미리 가늠
- 실제 지수·주식이 아니라 암호화폐 담보 무기한 선물 가격이라 펀딩비 등으로 실제와 차이가 날 수 있음
- 24시간 거래대금이 작으면 '거래 적음' 표시
"""
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
import streamlit as st

API = "https://api.hyperliquid.xyz/info"
DEX = "xyz"
KST, NY = "Asia/Seoul", "America/New_York"
THIN_USD = 2e6  # 24시간 거래대금이 이보다 작으면 '거래 적음'

# (코인, 표시 이름, 묶음, 기준 마감)  기준: us = 16:00 ET · kr = 15:30 KST · fut = 17:00 ET
ASSETS = [
    ("SP500", "S&P 500", "지수", "us"), ("XYZ100", "나스닥100", "지수", "us"), ("KR200", "코스피 200", "지수", "kr"),
    ("JP225", "닛케이 225", "지수", "kr"), ("EWY", "EWY (한국 ETF)", "지수", "us"),
    ("NVDA", "엔비디아", "종목", "us"), ("SKHX", "SK하이닉스", "종목", "kr"), ("SMSN", "삼성전자", "종목", "kr"),
    ("MU", "마이크론", "종목", "us"), ("TSLA", "테슬라", "종목", "us"), ("AAPL", "애플", "종목", "us"),
    ("GOLD", "금", "원자재", "fut"), ("CL", "WTI", "원자재", "fut"), ("BRENTOIL", "브렌트유", "원자재", "fut"),
]
REF_LABEL = {"us": "미국장 마감", "kr": "국장 마감", "fut": "원자재 마감"}


def _post(body: dict):
    r = requests.post(API, json=body, timeout=15)
    r.raise_for_status()
    return r.json()


def last_close(kind: str, now: pd.Timestamp) -> pd.Timestamp:
    """now(시간대 있음) 이전 가장 최근 평일 마감 시각 (한국 시각, 시간대 없음). 휴장일은 반영하지 않음."""
    tz, hh, mm = {"us": (NY, 16, 0), "kr": (KST, 15, 30), "fut": (NY, 17, 0)}[kind]
    local = now.tz_convert(tz)
    t = local.normalize() + pd.Timedelta(hours=hh, minutes=mm)
    while t > local or t.weekday() >= 5:
        t -= pd.Timedelta(days=1)
        t = t.normalize() + pd.Timedelta(hours=hh, minutes=mm)
    return t.tz_convert(KST).tz_localize(None)


def is_weekend(now: pd.Timestamp | None = None) -> bool:
    """주말 모드: 토 06:00 ~ 월 09:00 (한국 시각) — 미국장 금요일 마감 뒤부터 국장 월요일 개장 전까지."""
    now = (now or pd.Timestamp.now(tz=KST)).tz_convert(KST)
    wd, hm = now.weekday(), f"{now:%H:%M}"
    return (wd == 5 and hm >= "06:00") or wd == 6 or (wd == 0 and hm < "09:00")


def _candles(coin: str, start_ms: int, end_ms: int) -> pd.Series:
    rows = _post({"type": "candleSnapshot",
                  "req": {"coin": f"{DEX}:{coin}", "interval": "15m", "startTime": start_ms, "endTime": end_ms}})
    s = pd.Series([float(r["c"]) for r in rows],
                  index=pd.to_datetime([r["T"] for r in rows], unit="ms", utc=True).tz_convert(KST).tz_localize(None))
    return s.sort_index()


@st.cache_data(ttl=120, show_spinner=False)
def snapshot() -> pd.DataFrame:
    """종목별 현재가·기준 마감 가격·등락률·24시간 거래대금·기준 이후 15분봉. 행 = ASSETS 순서."""
    meta, ctx = _post({"type": "metaAndAssetCtxs", "dex": DEX})
    live = {a["name"].split(":")[1]: c for a, c in zip(meta["universe"], ctx)}
    now = pd.Timestamp.now(tz=KST)
    end_ms = int(time.time() * 1000)

    def one(asset):
        coin, name, group, kind = asset
        c = live.get(coin)
        if not c or not c.get("markPx"):
            return None
        ref_t = last_close(kind, now)
        start = ref_t - pd.Timedelta(hours=2)
        try:
            s = _candles(coin, int(start.tz_localize(KST).timestamp() * 1000), end_ms)
        except Exception:
            s = pd.Series(dtype=float)
        before = s[s.index <= ref_t]
        ref_px = float(before.iloc[-1]) if len(before) else None
        mark = float(c["markPx"])
        return {"coin": coin, "이름": name, "묶음": group, "기준": kind, "기준 시각": ref_t, "기준가": ref_px,
                "현재가": mark, "등락률": (mark / ref_px - 1) * 100 if ref_px else None,
                "거래대금": float(c.get("dayNtlVlm") or 0), "차트": s[s.index >= ref_t]}
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = [r for r in ex.map(one, ASSETS) if r]
    if not rows:
        raise RuntimeError("하이퍼리퀴드 응답 없음")
    df = pd.DataFrame(rows)
    return df[df["거래대금"] > 0].reset_index(drop=True)  # 거래가 아예 없는 시장은 제외
