"""FRED 시계열 및 발표 일정.

FRED는 응답 시간이 들쭉날쭉하고(0.3~12초) 동시 요청이 많으면 타임아웃이 나므로
- 동시 요청 수 제한 + 재시도
- 시계열을 디스크에 저장해 TTL 내 재사용, 실패 시 마지막 저장본 사용
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st

import config

FRED_API = "https://api.stlouisfed.org/fred"
DISK_DIR = config.CACHE_DIR / "fred"
DISK_DIR.mkdir(exist_ok=True)

_limit = threading.BoundedSemaphore(4)


def fred_today() -> date:
    """FRED 서버 기준 오늘(미 중부). 한국 날짜를 쓰면 KST 자정~미국 자정 사이에
    미래 날짜로 요청돼 400 오류가 난다."""
    return pd.Timestamp.now(tz="America/Chicago").date()


def _get(path: str, **params) -> dict:
    params.update(api_key=config.FRED_API_KEY, file_type="json")
    last_err = None
    for attempt in range(4):
        try:
            with _limit:
                r = requests.get(f"{FRED_API}/{path}", params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last_err = e
            limited = getattr(getattr(e, "response", None), "status_code", None) == 429
            time.sleep(5 * (attempt + 1) if limited else 1 + attempt)  # 분당 호출 한도(429)면 길게 기다림
    raise last_err


def _download(series_id: str) -> dict:
    obs = _get("series/observations", series_id=series_id)["observations"]
    s = pd.Series(
        pd.to_numeric([o["value"] for o in obs], errors="coerce"),
        index=pd.to_datetime([o["date"] for o in obs]),
        name=series_id,
    ).dropna()
    return {"series": s}


def _load(series_id: str) -> dict:
    """디스크 캐시가 TTL 이내면 재사용, 아니면 새로 받고 실패 시 오래된 캐시로 대체."""
    path = DISK_DIR / f"{series_id}.pkl"
    if path.exists() and time.time() - path.stat().st_mtime < config.TTL_MACRO:
        return pd.read_pickle(path)
    try:
        data = _download(series_id)
        pd.to_pickle(data, path)
        return data
    except Exception:
        if path.exists():
            return pd.read_pickle(path)
        raise


def fetch_series(series_id: str) -> pd.Series:
    return _load(series_id)["series"]



def prefetch(series_ids: list[str]) -> None:
    """여러 시계열을 병렬로 디스크 캐시에 채워둔다 (실패는 무시)."""
    def safe(sid):
        try:
            _load(sid)
        except Exception:
            pass
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(safe, series_ids))


@st.cache_data(ttl=config.TTL_MACRO, show_spinner=False)
def get_fred_series(series_id: str) -> pd.Series:
    return fetch_series(series_id)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_next_release_date(series_id: str) -> date | None:
    """해당 시계열이 속한 발표(release)의 다음 예정일."""
    release_id = _get("series/release", series_id=series_id)["releases"][0]["id"]
    today = fred_today()
    dates = _get(
        "release/dates",
        release_id=release_id,
        realtime_start=today.isoformat(),
        realtime_end=(today + timedelta(days=120)).isoformat(),
        include_release_dates_with_no_data="true",
        sort_order="asc",
    )["release_dates"]
    upcoming = [d["date"] for d in dates if d["date"] >= today.isoformat()]
    return date.fromisoformat(upcoming[0]) if upcoming else None


@st.cache_data(ttl=config.TTL_MACRO, show_spinner=False)
def fetch_release_dates(series_id: str, obs_date: date) -> list[date]:
    """obs_date 관측값이 발표·수정된 날들, 최신순 (첫 항목 = 현재 값이 정해진 날).

    발표(release) 날짜 목록은 같은 묶음의 다른 자료 갱신일도 섞여 있어 빈티지로 직접 확인한다.
    GDP처럼 속보→잠정→확정으로 수정되는 지표는 여러 날짜가 나온다.
    """
    today = fred_today()
    obs = _get(
        "series/observations",
        series_id=series_id,
        observation_start=obs_date.isoformat(),
        observation_end=obs_date.isoformat(),
        realtime_start=(today - timedelta(days=200)).isoformat(),
        realtime_end=today.isoformat(),
    )["observations"]
    starts = sorted({o["realtime_start"] for o in obs if o["value"] != "."}, reverse=True)
    return [date.fromisoformat(d) for d in starts]


def transform(s: pd.Series, how: str) -> pd.Series:
    """yoy/mom = 전년·전월 대비 %, ann3/ann6 = 최근 3·6개월 연율 %, diff = 직전 기간 대비 증감, level = 원값.

    비교 대상은 '날짜' 기준(12개월 전·1개월 전)으로 찾는다. 2025-10월처럼 발표가 빠진 달이 있으면
    순서 기준(pct_change(12))은 13개월 전과 비교하게 되어 값이 틀어진다 (CPI 3.4% → 3.69%로 표시됐던 원인).
    비교할 달이 없으면 NaN.
    """
    if how == "level":
        return s
    monthly = len(s) > 0 and bool((s.index.day == 1).all())
    if how in ("yoy", "mom") or (how == "diff" and monthly):
        months = 12 if how == "yoy" else 1
        base = s.reindex(s.index - pd.DateOffset(months=months))
        base.index = s.index
        return (s / base - 1) * 100 if how != "diff" else s - base
    if how in ("ann3", "ann6"):  # 최근 3·6개월 변화를 연율로 (연준이 보는 물가 모멘텀). 날짜 기준 비교
        months = 3 if how == "ann3" else 6
        base = s.reindex(s.index - pd.DateOffset(months=months))
        base.index = s.index
        return ((s / base) ** (12 / months) - 1) * 100
    if how == "diff":
        return s.diff()
    raise ValueError(f"알 수 없는 변환: {how}")
