"""대시보드와 별개로 주기 실행하는 데이터 수집기. 결과는 collected/*.csv

실행: python3 collect.py   (launchd로 3시간마다 실행, scripts/install_collector.sh 참고)

- econ_calendar.csv  ForexFactory 미국 일정·예상치 (누적, 다시 받을 수 없음)
- surprises.csv      서프라이즈 표 (실제=FRED 첫 발표치)
- news.csv           뉴스 헤드라인 (누적, 링크 기준 중복 제거)
- rates_live.csv     CNBC 실시간 금리 스냅샷 (누적)
- market_daily.csv   지수·환율·원자재·변동성·국채 종가 (날짜 기준 갱신 누적)
- fred_series.csv    매크로 FRED 시계열 전체 (매번 최신본으로 교체, long 형식)
"""
import logging
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")

import pandas as pd
import streamlit.runtime.caching.cache_data_api  # noqa: F401

# Streamlit 밖에서 캐시 함수를 정의·호출할 때 나는 "No runtime found" 경고 숨김
# (data 모듈 import 시점에 발생하므로 그 전에, level은 Streamlit이 재설정하므로 로거 자체를 끈다)
logging.getLogger("streamlit.runtime.caching.cache_data_api").disabled = True

sys.path.insert(0, str(Path(__file__).parent))
import config
from data import econ_calendar, macro, news, rates, surprise
from data.fred import fetch_series, prefetch
from data.market import SUMMARY_ITEMS, get_price_series

OUT = config.BASE_DIR / "collected"
OUT.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("collect")


def upsert(path: Path, new: pd.DataFrame, keys: list[str], sort: list[str] | None = None) -> int:
    """기존 CSV와 합치고 keys 기준으로 최신 행만 남긴다. 추가/갱신 후 총 행 수 반환."""
    new = new.astype(object).where(new.notna(), "").astype(str)  # NaN → 빈칸
    if path.exists():
        old = pd.read_csv(path, dtype=str, keep_default_na=False)
        new = pd.concat([old, new], ignore_index=True)
    df = new.drop_duplicates(keys, keep="last")
    if sort:
        df = df.sort_values(sort)
    df.to_csv(path, index=False, encoding="utf-8-sig")  # 엑셀에서 한글 깨짐 방지
    return len(df)


def collect_calendar() -> None:
    events = econ_calendar._load_ff_events()  # 1시간 파일 캐시 + 주차별 보관 포함
    df = pd.DataFrame([e for e in events if e.get("country") == "USD"])
    if df.empty:
        return
    df["date_kst"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert("Asia/Seoul").dt.strftime("%Y-%m-%d %H:%M")
    df["title_ko"] = df["title"].map(econ_calendar.title_ko)
    df = df[["date_kst", "title", "title_ko", "impact", "forecast", "previous"]]
    n = upsert(OUT / "econ_calendar.csv", df, ["date_kst", "title"], ["date_kst"])
    log.info(f"경제지표 일정: 이번 주 {len(df)}건, 누적 {n}건")


def collect_surprises() -> None:
    df = surprise.get_surprises(weeks=12)  # Nasdaq 캘린더 최근 12주 (누적은 CSV에서)
    if df.empty:
        log.info("서프라이즈: 발표된 대상 지표 없음")
        return
    df = df.drop(columns=["_unit", "_dec"]).rename(columns={"_diff": "diff"})
    df["발표(KST)"] = df["발표(KST)"].dt.strftime("%Y-%m-%d %H:%M")
    n = upsert(OUT / "surprises.csv", df, ["발표(KST)", "지표"], ["발표(KST)"])
    log.info(f"서프라이즈: 확인 {df['diff'].notna().sum()}건, 누적 {n}건")


def collect_news() -> None:
    df = news.get_news()
    if df.empty:
        return
    df = df.assign(time=df["time"].dt.strftime("%Y-%m-%d %H:%M"))
    n = upsert(OUT / "news.csv", df, ["link"], ["time"])
    log.info(f"뉴스: 이번 수집 {len(df)}건, 누적 {n}건")


def collect_rates_live() -> None:
    q = rates.get_cnbc_quotes()
    df = pd.DataFrame(
        [(ts.strftime("%Y-%m-%d %H:%M:%S"), sym, val) for sym, (val, ts) in q.items()],
        columns=["time_kst", "symbol", "value"],
    )
    n = upsert(OUT / "rates_live.csv", df, ["time_kst", "symbol"], ["time_kst"])
    log.info(f"실시간 금리: {len(df)}개 심볼, 누적 {n}행")


def collect_market_daily() -> None:
    """최근 30일 종가를 받아 날짜 기준으로 덮어쓴다 (첫 실행은 2년치)."""
    path = OUT / "market_daily.csv"
    days = 30 if path.exists() else 730
    start = date.today() - timedelta(days=days)
    cols = {}
    for key, (label, source, symbol, _) in SUMMARY_ITEMS.items():
        if source == "yahoo":
            cols[label] = macro.yahoo(symbol, years=3)
        elif source == "naver":
            cols[label] = get_price_series(source, symbol, days=800)
    for label, symbol in [("MOVE", "^MOVE")]:  # VIX·달러인덱스는 SUMMARY_ITEMS에 포함
        cols[label] = macro.yahoo(symbol, years=3)
    curve = rates.get_treasury_curve(start)
    for tenor, (col, _, _) in rates.TENORS.items():
        cols[f"UST {tenor}"] = curve[col]
    df = pd.DataFrame({k: v[v.index >= pd.Timestamp(start)] for k, v in cols.items()})
    df = df[df.index.normalize() == df.index]  # 장중 값 제외, 종가만
    df.index = df.index.strftime("%Y-%m-%d")
    df = df.rename_axis("date").reset_index().round(4)
    n = upsert(path, df, ["date"], ["date"])
    log.info(f"시세 종가: {len(df)}일 갱신, 누적 {n}일")


def collect_fred() -> None:
    prefetch(macro.FRED_IDS)
    frames = []
    for sid in macro.FRED_IDS:
        try:
            s = fetch_series(sid)
            frames.append(pd.DataFrame({"series_id": sid, "date": s.index.strftime("%Y-%m-%d"), "value": s.values}))
        except Exception as e:
            log.warning(f"FRED {sid} 실패: {e}")
    df = pd.concat(frames, ignore_index=True)
    df.to_csv(OUT / "fred_series.csv", index=False, encoding="utf-8-sig")
    log.info(f"FRED: {len(frames)}/{len(macro.FRED_IDS)}개 시계열, {len(df):,}행")


def main() -> int:
    log.info("=== 수집 시작 ===")
    steps = [collect_calendar, collect_surprises, collect_news, collect_rates_live,
             collect_market_daily, collect_fred]
    failed = []
    for step in steps:
        try:
            step()
        except Exception as e:
            failed.append(step.__name__)
            log.exception(f"{step.__name__} 실패: {e}")
    log.info(f"=== 수집 종료 (실패 {len(failed)}개{': ' + ', '.join(failed) if failed else ''}) ===")
    check_health()
    return 1 if failed else 0


def check_health() -> None:
    """데이터 소스 상태 점검 → 새로 나빠지거나 회복된 소스가 있으면 맥 알림 (data/health.py)."""
    try:
        from data.health import OK, alert_changes, check_all
        df = check_all()
        bad = df[df["상태"] != OK]
        log.info(f"상태 점검: {len(df) - len(bad)}/{len(df)}개 정상"
                 + (" · " + ", ".join(bad["소스"]) if len(bad) else ""))
        for msg in alert_changes(df):
            log.warning(f"알림: {msg}")
    except Exception as e:
        log.exception(f"상태 점검 실패: {e}")


if __name__ == "__main__":
    sys.exit(main())
