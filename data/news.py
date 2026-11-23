"""RSS 기반 금융 뉴스 수집."""
import html
import re
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

import feedparser
import pandas as pd
import requests
import streamlit as st
from dateutil import parser as dtparser

import config

# (출처명, 분류, URL, 시간대 표기가 없을 때 가정할 시간대)
# 매경(403 차단), 머니투데이(2025년 이후 갱신 중단)는 제외
# 제목에 금융 키워드가 있는 기사만 (본문 검색이면 연예·생활 기사가 섞임)
_GNEWS_WORDS = ["stocks", "stock", "bonds", "treasuries", "treasury", "yields", "fed", "inflation", "dollar",
                "oil", "economy", "rates", "markets", "shares", "currency", "yen", "yuan", "gold", "ECB", "BOJ",
                "tariffs", "GDP"]


def _gnews(site: str) -> str:
    q = f"when:1d site:{site} " + " OR ".join(f"intitle:{w}" for w in _GNEWS_WORDS)
    return "https://news.google.com/rss/search?q=" + quote(q) + "&hl=en-US&gl=US&ceid=US:en"


FEEDS = [
    ("한국경제",      "국내",     "https://www.hankyung.com/feed/finance",              "Asia/Seoul"),
    ("한국경제",      "국내",     "https://www.hankyung.com/feed/economy",              "Asia/Seoul"),
    ("연합뉴스",      "국내",     "https://www.yna.co.kr/rss/market.xml",               "Asia/Seoul"),
    ("연합인포맥스",  "국내",     "https://news.einfomax.co.kr/rss/allArticle.xml",     "Asia/Seoul"),
    ("이데일리",      "국내",     "http://rss.edaily.co.kr/stock_news.xml",             "Asia/Seoul"),
    ("CNBC",          "해외",     "https://www.cnbc.com/id/100003114/device/rss/rss.html", "UTC"),
    ("CNBC",          "해외",     "https://www.cnbc.com/id/15839069/device/rss/rss.html",  "UTC"),
    ("CNBC",          "해외",     "https://www.cnbc.com/id/20910258/device/rss/rss.html",  "UTC"),
    ("MarketWatch",   "해외",     "https://feeds.content.dowjones.io/public/rss/mw_topstories", "UTC"),
    ("WSJ",           "해외",     "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", "UTC"),
    ("Investing.com", "해외",     "https://www.investing.com/rss/news_25.rss",          "UTC"),
    ("Nasdaq",        "해외",     "https://www.nasdaq.com/feed/rssoutbound?category=Markets", "UTC"),
    ("FT",            "해외",     "https://www.ft.com/markets?format=rss",              "UTC"),
    ("FT",            "해외",     "https://www.ft.com/global-economy?format=rss",       "UTC"),
    # 로이터·블룸버그는 공개 RSS가 없어 구글 뉴스 검색 RSS로 제목만 받는다 (링크는 구글 뉴스 경유, 요약 없음)
    ("Reuters",       "해외",     _gnews("reuters.com"),                               "UTC"),
    ("Bloomberg",     "해외",     _gnews("bloomberg.com"),                             "UTC"),
    ("Fed",           "정책·기관", "https://www.federalreserve.gov/feeds/press_all.xml",  "UTC"),
    ("ECB",           "정책·기관", "https://www.ecb.europa.eu/rss/press.html",           "UTC"),
    ("한국은행",      "정책·기관", "https://www.bok.or.kr/portal/bbs/B0000501/news.rss?menuNo=201264", "Asia/Seoul"),
]

_TAG = re.compile(r"<[^>]+>")
_GNEWS_SUFFIX = re.compile(r"\s+-\s+(Reuters|Bloomberg(\.com)?|Bloomberg Law)$")
MAX_PER_FEED = 40  # 구글 뉴스 검색 피드는 100개씩 와서 국내·해외 비중을 흐리므로 피드당 최신 N개
_WS = re.compile(r"\s+")


def _clean(text: str, limit: int = 200) -> str:
    # 태그가 &lt;p&gt;처럼 인코딩된 피드(한국은행)도 있어 복원 → 태그 제거 → 한 번 더 복원
    text = html.unescape(_TAG.sub(" ", html.unescape(text or "")))
    text = _WS.sub(" ", text).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _parse_time(entry, naive_tz: str) -> pd.Timestamp | None:
    raw = entry.get("published") or entry.get("updated")
    if not raw:
        return None
    try:
        ts = pd.Timestamp(dtparser.parse(raw))
    except (ValueError, OverflowError):
        return None
    # feedparser는 시간대 없는 날짜를 UTC로 간주하므로 피드별 시간대를 직접 적용
    ts = ts.tz_localize(naive_tz) if ts.tzinfo is None else ts
    return ts.tz_convert("Asia/Seoul").tz_localize(None)


def _fetch_feed(feed) -> list[dict]:
    source, category, url, naive_tz = feed
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
    except requests.RequestException:
        return []
    rows = []
    google = "news.google.com" in url
    for e in feedparser.parse(r.content).entries:
        title = _clean(e.get("title", ""), 300)
        if google:
            title = _GNEWS_SUFFIX.sub("", title)
            if " " not in title or "Stock Price" in title or "Latest Updates" in title:  # 종목 시세·토픽 페이지
                continue
        if not title:
            continue
        rows.append({
            "time": _parse_time(e, naive_tz),
            "source": source,
            "category": category,
            "title": title,
            "summary": "" if google else _clean(e.get("summary", "")),  # 구글 뉴스 요약은 제목 링크 반복뿐
            "link": e.get("link", ""),
        })
    rows = [x for x in rows if x["time"] is not None] + [x for x in rows if x["time"] is None]
    return sorted(rows, key=lambda x: x["time"] or pd.Timestamp.min, reverse=True)[:MAX_PER_FEED]


@st.cache_data(ttl=config.TTL_NEWS, show_spinner=False)
def get_news() -> pd.DataFrame:
    """모든 피드를 병렬 수집해 최신순 정렬, 제목 기준 중복 제거."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = [r for feed_rows in ex.map(_fetch_feed, FEEDS) for r in feed_rows]
    df = pd.DataFrame(rows, columns=["time", "source", "category", "title", "summary", "link"])
    if df.empty:
        return df
    df["_key"] = df["title"].str.replace(r"\W", "", regex=True).str.lower()
    df = (
        df.sort_values("time", ascending=False, na_position="last")
        .drop_duplicates("_key")
        .drop(columns="_key")
        .reset_index(drop=True)
    )
    return df


def load_news_summary() -> dict | None:
    """summarize.py가 하루 3회 만드는 뉴스 이슈 요약. 없으면 None."""
    import json
    try:
        return json.loads(config.NEWS_SUMMARY_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return None
