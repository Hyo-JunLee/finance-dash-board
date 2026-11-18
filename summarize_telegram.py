"""텔레그램 리서치 채널 최근 24시간 글 → 논점 요약 (Claude Code 헤드리스 실행).

실행: python3 summarize_telegram.py   (launchd로 하루 3회 08:30·13:30·18:30, scripts/install_summarizer.sh)

- API 키 대신 이 맥에 로그인된 Claude Code(`claude -p`)를 써서 별도 결제 없이 구독 사용 한도에서 처리
- 도구는 모두 끄고(--tools ""), MCP도 불러오지 않으며(--strict-mcp-config), 정해진 JSON 형식으로만 받는다
- 요약 논점마다 출처 글 id를 달게 하고, 실제로 넘겨준 글에 없는 id는 버린다 (출처 지어내기 방지)
- 결과: .cache/telegram_summary.json (화면용 최신본) + collected/telegram_summaries.jsonl (누적 기록)
"""
import json
import logging
import shutil
import subprocess
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

import pandas as pd
import streamlit.runtime.caching.cache_data_api  # noqa: F401

# Streamlit 밖에서 캐시 함수를 쓸 때 나는 "No runtime found" 경고 숨김
logging.getLogger("streamlit.runtime.caching.cache_data_api").disabled = True

sys.path.insert(0, str(Path(__file__).parent))
import config
from data.telegram import fetch_channel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("summarize")

WINDOW_HOURS = 24
MODEL = "sonnet"
MAX_POST_CHARS = 1500  # 글 하나가 요약을 독차지하지 않도록
HISTORY = config.BASE_DIR / "collected" / "telegram_summaries.jsonl"

SYSTEM = """너는 채권·매크로 리서치 에디터다. 증권사·리서치 텔레그램 채널의 최근 24시간 글을 읽고,
채권·매크로 투자자가 아침에 훑어볼 '논점 요약'을 한국어로 만든다.

규칙
- 비슷한 내용은 하나의 논점으로 묶어 3~5개 논점으로 정리한다. 중요한 순서대로.
- 각 논점 요약은 2~3문장. 수치(금리, bp, 성장률, 날짜)는 원문 그대로 보존한다.
- 의견은 작성 채널의 견해로 전달한다("~로 본다", "~를 지적"). 원인이나 전망을 단정하지 않는다.
- 글에 없는 사실을 덧붙이지 않는다. 광고·행사 안내·인사말은 무시한다.
- source_ids에는 그 논점의 근거가 된 글의 id(예: P3)만 넣는다. 없는 id를 만들지 않는다.
- rates_view는 그 논점이 금리에 주는 방향: 금리 상승 요인 / 금리 하락 요인 / 혼재 / 해당 없음.
- headline은 오늘 논점 전체를 한 문장으로.
- watch에는 글에서 언급된 앞으로의 일정·이벤트(지표 발표, 회의 등)를 짧게. 없으면 빈 목록."""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "rates_view": {"type": "string", "enum": ["금리 상승 요인", "금리 하락 요인", "혼재", "해당 없음"]},
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "summary", "rates_view", "source_ids"],
                "additionalProperties": False,
            },
        },
        "watch": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "themes", "watch"],
    "additionalProperties": False,
}


def collect_posts() -> list[dict]:
    since = pd.Timestamp.now() - pd.Timedelta(hours=WINDOW_HOURS)
    posts = []
    for handle in config.TELEGRAM_CHANNELS:
        try:
            posts += [p for p in fetch_channel(handle) if p["time"] >= since]
        except Exception as e:
            log.warning(f"{handle} 글 수집 실패: {e}")
    posts.sort(key=lambda p: p["time"])
    for i, p in enumerate(posts, 1):
        p["id"] = f"P{i}"
    return posts


def render_input(posts: list[dict]) -> str:
    blocks = []
    for p in posts:
        body = p["text"][:MAX_POST_CHARS] if p["text"] else "[이미지만 있는 글]"
        fwd = f" (전달: {p['forwarded']})" if p.get("forwarded") else ""
        blocks.append(f"[{p['id']}] {p['channel']}{fwd} | {p['time']:%m/%d %H:%M} KST\n{body}")
    return "\n\n---\n\n".join(blocks)


def run_claude(text: str) -> tuple[dict, dict]:
    """claude -p 헤드리스 실행. 반환: (요약 JSON, 실행 메타)."""
    exe = shutil.which("claude") or "/opt/homebrew/bin/claude"
    cmd = [exe, "-p", "아래 텔레그램 글들을 규칙에 따라 요약해 JSON으로만 답하라.",
           "--model", MODEL, "--tools", "", "--strict-mcp-config", "--no-session-persistence",
           "--system-prompt", SYSTEM, "--json-schema", json.dumps(SCHEMA, ensure_ascii=False),
           "--output-format", "json"]
    r = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"claude 실행 실패 (exit {r.returncode}): {(r.stderr or r.stdout)[-500:]}")
    out = json.loads(r.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude 오류: {str(out.get('result'))[:500]}")
    data = out.get("structured_output")
    if data is None:  # 구조화 출력 필드가 없으면 본문 텍스트에서 JSON을 읽는다
        data = json.loads(out["result"])
    meta = {k: out.get(k) for k in ("duration_ms", "num_turns", "total_cost_usd", "usage")}
    return data, meta


def build_summary(posts: list[dict], data: dict, meta: dict) -> dict:
    by_id = {p["id"]: p for p in posts}
    themes = []
    for t in data.get("themes", []):
        sources = [by_id[i] for i in dict.fromkeys(t.get("source_ids", [])) if i in by_id]  # 없는 id는 버림
        if not sources:
            continue
        themes.append({
            "title": t["title"].strip(), "summary": t["summary"].strip(), "rates_view": t["rates_view"],
            "sources": [{"channel": s["channel"], "time": s["time"].strftime("%Y-%m-%d %H:%M"), "link": s["link"]}
                        for s in sources],
        })
    counts = pd.Series([p["channel"] for p in posts]).value_counts().to_dict() if posts else {}
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "window_hours": WINDOW_HOURS, "post_count": len(posts), "channels": counts,
        "headline": data.get("headline", "").strip(), "themes": themes,
        "watch": [w.strip() for w in data.get("watch", []) if w.strip()],
        "model": MODEL, "meta": meta,
    }


def main() -> int:
    log.info("=== 텔레그램 요약 시작 ===")
    posts = collect_posts()
    log.info(f"최근 {WINDOW_HOURS}시간 글 {len(posts)}개 ({len(config.TELEGRAM_CHANNELS)}개 채널)")
    if not posts:
        summary = build_summary([], {"headline": "최근 24시간 동안 새 글이 없습니다.", "themes": [], "watch": []}, {})
    else:
        try:
            data, meta = run_claude(render_input(posts))
        except Exception as e:
            log.exception(f"요약 실패: {e}")  # 기존 요약 파일은 그대로 두어 화면에 직전 요약이 남게 한다
            return 1
        summary = build_summary(posts, data, meta)
        dropped = len(data.get("themes", [])) - len(summary["themes"])
        log.info(f"논점 {len(summary['themes'])}개" + (f" (출처 없는 논점 {dropped}개 제외)" if dropped else "")
                 + f" · {meta.get('duration_ms', 0) / 1000:.0f}초")
    config.SUMMARY_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    HISTORY.parent.mkdir(exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    log.info("=== 텔레그램 요약 종료 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
