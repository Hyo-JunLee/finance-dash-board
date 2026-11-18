"""내 포트폴리오: 거래·배당 기록(SQLite) → 보유 현황·손익·성과 계산 (USD 기준).

- 평균단가: 이동평균법 (키움 해외주식과 동일). 매수 수수료는 원가에 포함, 매도 수수료는 실현손익에서 차감
- 주식 분할: Yahoo 분할 이력으로 분할 이전 거래의 수량·단가 보정
- 가격: 분할만 반영된 종가(배당 미반영). 배당은 기록한 금액을 따로 더한다
- 성과: 시간가중수익률(TWR). 매수·매도 금액은 외부 입출금으로 보고 제외
"""
import os
import sqlite3
from contextlib import closing
from datetime import date, timedelta

import pandas as pd
import streamlit as st
import yfinance as yf

import config

DB_DIR = config.BASE_DIR / "portfolio"
DB_DIR.mkdir(exist_ok=True)
DB_PATH = os.getenv("PORTFOLIO_DB") or DB_DIR / "portfolio.db"  # 테스트 시 다른 DB 지정 가능

TRADE_COLS = ["id", "date", "ticker", "side", "qty", "price", "fee", "memo"]
DIV_COLS = ["id", "date", "ticker", "gross", "tax", "memo"]
BENCHMARK = "SPY"


# ── 저장소 ───────────────────────────────────────────
def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, ticker TEXT NOT NULL,
        side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
        qty REAL NOT NULL CHECK (qty > 0), price REAL NOT NULL CHECK (price > 0),
        fee REAL NOT NULL DEFAULT 0, memo TEXT DEFAULT '')""")
    con.execute("""CREATE TABLE IF NOT EXISTS dividends (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL, ticker TEXT NOT NULL,
        gross REAL NOT NULL CHECK (gross > 0), tax REAL NOT NULL DEFAULT 0, memo TEXT DEFAULT '')""")
    return con


def load_trades() -> pd.DataFrame:
    with closing(_conn()) as con:
        df = pd.read_sql("SELECT * FROM trades ORDER BY date, id", con)
    df["date"] = pd.to_datetime(df["date"])
    return df


def load_dividends() -> pd.DataFrame:
    with closing(_conn()) as con:
        df = pd.read_sql("SELECT * FROM dividends ORDER BY date, id", con)
    df["date"] = pd.to_datetime(df["date"])
    df["net"] = df["gross"] - df["tax"]
    return df


def add_trade(d: date, ticker: str, side: str, qty: float, price: float, fee: float, memo: str) -> None:
    with closing(_conn()) as con, con:
        con.execute("INSERT INTO trades (date, ticker, side, qty, price, fee, memo) VALUES (?,?,?,?,?,?,?)",
                    (d.isoformat(), ticker.upper().strip(), side, qty, price, fee, memo))


def add_dividend(d: date, ticker: str, gross: float, tax: float, memo: str) -> None:
    with closing(_conn()) as con, con:
        con.execute("INSERT INTO dividends (date, ticker, gross, tax, memo) VALUES (?,?,?,?,?)",
                    (d.isoformat(), ticker.upper().strip(), gross, tax, memo))


def replace_table(table: str, df: pd.DataFrame) -> None:
    """표 편집 결과로 테이블 전체를 교체 (한 트랜잭션)."""
    cols = TRADE_COLS[1:] if table == "trades" else DIV_COLS[1:]
    rows = df[cols].copy()
    rows["date"] = pd.to_datetime(rows["date"]).dt.strftime("%Y-%m-%d")
    rows["ticker"] = rows["ticker"].str.upper().str.strip()
    rows["memo"] = rows["memo"].fillna("")
    with closing(_conn()) as con, con:
        con.execute(f"DELETE FROM {table}")
        con.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        rows.itertuples(index=False, name=None))


# ── 시세·분할 ────────────────────────────────────────
@st.cache_data(ttl=config.TTL_PRICE, show_spinner=False)
def get_prices(tickers: tuple[str, ...], start: date) -> pd.DataFrame:
    """분할만 반영된 종가 (배당 미반영). 열=티커."""
    df = yf.download(list(tickers), start=start, auto_adjust=False, progress=False, threads=True)["Close"]
    return df if isinstance(df, pd.DataFrame) else df.to_frame(tickers[0])


@st.cache_data(ttl=60 * 60 * 12, show_spinner=False)
def get_splits(ticker: str) -> pd.Series:
    s = yf.Ticker(ticker).splits
    if s.empty:
        return s
    s.index = s.index.tz_localize(None).normalize()
    return s


@st.cache_data(ttl=60 * 60, show_spinner=False)
def ticker_exists(ticker: str) -> bool:
    try:
        return not yf.Ticker(ticker).history(period="5d").empty
    except Exception:
        return False


def split_adjust(trades: pd.DataFrame) -> pd.DataFrame:
    """분할 이전 거래의 수량×비율, 단가÷비율 (분할 당일 이전 거래만)."""
    df = trades.copy()
    for t in df["ticker"].unique():
        try:
            splits = get_splits(t)
        except Exception:
            continue
        for d, ratio in splits.items():
            mask = (df["ticker"] == t) & (df["date"] < d)
            df.loc[mask, "qty"] *= ratio
            df.loc[mask, "price"] /= ratio
    return df


# ── 계산 ─────────────────────────────────────────────
def compute_positions(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """이동평균법으로 종목별 보유 수량·원가와 매도별 실현손익을 계산.

    반환: (보유: ticker, qty, cost / 실현: date, ticker, qty, proceeds, cost, pnl)
    """
    pos: dict[str, list[float]] = {}  # ticker -> [qty, cost]
    realized = []
    for r in trades.sort_values(["date", "id"]).itertuples():
        qty, cost = pos.get(r.ticker, [0.0, 0.0])
        if r.side == "BUY":
            pos[r.ticker] = [qty + r.qty, cost + r.qty * r.price + r.fee]
        else:
            sell_qty = min(r.qty, qty)  # 보유보다 많이 팔린 기록은 보유분까지만
            avg = cost / qty if qty else 0.0
            proceeds = sell_qty * r.price - r.fee
            realized.append({"date": r.date, "ticker": r.ticker, "qty": sell_qty, "proceeds": proceeds,
                             "cost": avg * sell_qty, "pnl": proceeds - avg * sell_qty})
            pos[r.ticker] = [qty - sell_qty, cost - avg * sell_qty]
    holdings = pd.DataFrame([(t, q, c) for t, (q, c) in pos.items() if q > 1e-9],
                            columns=["ticker", "qty", "cost"])
    return holdings, pd.DataFrame(realized, columns=["date", "ticker", "qty", "proceeds", "cost", "pnl"])


def holdings_table(holdings: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for h in holdings.itertuples():
        s = prices[h.ticker].dropna() if h.ticker in prices else pd.Series(dtype=float)
        last = s.iloc[-1] if len(s) else float("nan")
        prev = s.iloc[-2] if len(s) > 1 else float("nan")
        value = h.qty * last
        rows.append({
            "종목": h.ticker, "수량": h.qty, "평균단가": h.cost / h.qty, "현재가": last,
            "일간": (last / prev - 1) * 100, "평가금액": value, "원가": h.cost,
            "평가손익": value - h.cost, "수익률": (value / h.cost - 1) * 100,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["비중"] = df["평가금액"] / df["평가금액"].sum() * 100
        df = df.sort_values("평가금액", ascending=False).reset_index(drop=True)
    return df


def daily_performance(trades: pd.DataFrame, divs: pd.DataFrame, prices: pd.DataFrame,
                      benchmark: pd.Series | None = None) -> pd.DataFrame:
    """일별 평가금액과 시간가중수익률(TWR) 누적, 벤치마크 누적수익률.

    benchmark는 배당 재투자 총수익 지수(수정주가)를 넘긴다 — 포트폴리오도 배당을 포함하므로.

    매수·매도 금액은 그날 장 마감 시점의 입출금으로 본다 (체결가 대비 종가 손익까지 반영):
    r_t = (V_t + 배당_t − 순매수_t − V_{t−1}) / V_{t−1}
    전날 평가금액이 0인 날(첫 매수·전량 매도 후 재매수)은 분모로 순매수 금액을 쓴다.
    """
    tickers = [t for t in trades["ticker"].unique() if t in prices]
    px = prices[tickers].ffill()
    px = px[px.index >= trades["date"].min()]
    if px.empty:
        return pd.DataFrame()
    days = px.index

    def on_trading_day(dates: pd.Series) -> pd.DatetimeIndex:
        # 휴장일 기록은 다음 거래일로
        return days[days.searchsorted(dates).clip(max=len(days) - 1)]

    buy = trades["side"] == "BUY"
    signed_qty = trades["qty"].where(buy, -trades["qty"])
    qty = (signed_qty.groupby([on_trading_day(trades["date"]), trades["ticker"]]).sum()
           .unstack(fill_value=0).reindex(index=days, columns=tickers, fill_value=0).cumsum())
    value = (qty * px[tickers]).sum(axis=1)

    gross = trades["qty"] * trades["price"]
    cash_flow = (gross + trades["fee"]).where(buy, -(gross - trades["fee"]))
    cf = cash_flow.groupby(on_trading_day(trades["date"])).sum().reindex(days, fill_value=0)
    dv = (divs["net"].groupby(on_trading_day(divs["date"])).sum().reindex(days, fill_value=0)
          if len(divs) else pd.Series(0.0, index=days))

    prev = value.shift(1, fill_value=0)
    base = prev.where(prev > 1e-9, cf)
    r = ((value + dv - cf - prev) / base).where(base > 1e-9, 0.0)
    out = pd.DataFrame({"평가금액": value, "순매수": cf, "배당": dv,
                        "포트폴리오": ((1 + r).cumprod() - 1) * 100})
    if benchmark is not None and len(benchmark):
        b = benchmark.reindex(days).ffill()
        out[BENCHMARK] = (b / b.iloc[0] - 1) * 100
    return out
