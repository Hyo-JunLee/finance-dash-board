"""텔레그램 공개 채널 최근 글 (웹 미리보기 t.me/s/<채널>, 로그인·API 키 불필요).

텔레그램이 웹 미리보기 HTML 구조를 바꾸면 파싱이 깨질 수 있다 → 호출부에서 예외 처리 후 원문 링크 안내.
"""
import html
import re
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests
import streamlit as st

import config

_HEADERS = {"User-Agent": "Mozilla/5.0"}
_BLOCK = 'class="tgme_widget_message_wrap'
_TEXT = re.compile(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_PHOTO = re.compile(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)")
_FWD = re.compile(r'tgme_widget_message_forwarded_from_name"[^>]*>(?:<span[^>]*>)?([^<]+)')


def _clean(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


@st.cache_data(ttl=config.TTL_TELEGRAM, show_spinner=False)
def fetch_channel(handle: str) -> list[dict]:
    """최근 글 목록 (최신순). 글자·사진이 모두 없는 서비스 메시지는 제외."""
    r = requests.get(f"https://t.me/s/{handle}", headers=_HEADERS, timeout=15)
    r.raise_for_status()
    posts = []
    for block in r.text.split(_BLOCK)[1:]:
        pid = re.search(r'data-post="([^"]+)"', block)
        when = re.search(r'<time datetime="([^"]+)"', block)
        if not pid or not when:
            continue
        text = _TEXT.search(block)
        photo = _PHOTO.search(block)
        fwd = _FWD.search(block)
        body = _clean(text.group(1)) if text else ""
        if not body and not photo:
            continue
        posts.append({
            "handle": handle,
            "channel": config.TELEGRAM_CHANNELS.get(handle, handle),
            "time": pd.Timestamp(when.group(1)).tz_convert("Asia/Seoul").tz_localize(None),
            "text": body,
            "photo": photo.group(1) if photo else None,
            "forwarded": html.unescape(fwd.group(1)).strip() if fwd else None,
            "link": f"https://t.me/{pid.group(1)}",
        })
    return sorted(posts, key=lambda p: p["time"], reverse=True)


def latest_posts(handles: list[str], n: int = 3) -> tuple[list[dict], list[str]]:
    """최근 글 n개. 반환: (글 목록·최신순, 불러오지 못한 채널).

    채널 하나면 그 채널의 최신 n개. 여러 개면 글을 자주 올리는 채널이 칸을 독차지하지 않도록
    최신 글이 새로운 채널 순서대로 한 개씩 번갈아 고른다.
    """
    def safe(h):
        try:
            return h, fetch_channel(h)
        except Exception:
            return h, None
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(safe, handles))
    failed = [h for h, posts in results if posts is None]
    queues = sorted((list(posts) for _, posts in results if posts), key=lambda q: q[0]["time"], reverse=True)
    picked = []
    while len(picked) < n and any(queues):
        for q in queues:
            if q and len(picked) < n:
                picked.append(q.pop(0))
    return sorted(picked, key=lambda p: p["time"], reverse=True), failed
