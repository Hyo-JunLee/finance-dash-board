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


def load_summary() -> dict | None:
    """summarize_telegram.py가 하루 3회 만드는 최신 요약. 없으면 None."""
    import json
    try:
        return json.loads(config.SUMMARY_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return None


def recent_posts(hours: int = 24) -> list[dict]:
    """요약 대상 채널의 최근 글 (원문 확인용 목록, 최신순)."""
    since = pd.Timestamp.now() - pd.Timedelta(hours=hours)

    def safe(h):
        try:
            return fetch_channel(h)
        except Exception:
            return []
    with ThreadPoolExecutor(max_workers=6) as ex:
        posts = [p for chunk in ex.map(safe, list(config.TELEGRAM_CHANNELS)) for p in chunk if p["time"] >= since]
    return sorted(posts, key=lambda p: p["time"], reverse=True)
