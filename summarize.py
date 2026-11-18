"""AI 요약 (Claude Code 헤드리스 실행) — 하루 3회 08:30·13:30·18:30, scripts/install_summarizer.sh

1) 텔레그램 리서치 6개 채널 최근 24시간 글 → 채권·매크로 논점 3~5개
2) 뉴스 헤드라인 최근 12시간 → 국내·해외 이슈 각 4~5개 (기사 수로 화제성 표시)

- API 키 대신 이 맥에 로그인된 Claude Code(`claude -p`)를 써서 별도 결제 없이 구독 사용 한도에서 처리
- 도구는 모두 끄고(--tools ""), MCP도 불러오지 않으며(--strict-mcp-config), 정해진 JSON 형식으로만 받는다
- 결과마다 근거 글·기사 id를 달게 하고, 실제로 넘겨준 목록에 없는 id는 버린다 (출처 지어내기 방지)
- 한쪽이 실패해도 다른 쪽은 진행하고, 실패한 쪽은 직전 요약 파일을 그대로 둔다
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
from data.news import get_news
from data.telegram import fetch_channel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("summarize")

MODEL = "sonnet"
COLLECTED = config.BASE_DIR / "collected"


def run_claude(system: str, schema: dict, text: str, instruction: str) -> tuple[dict, dict]:
    """claude -p 헤드리스 실행. 반환: (JSON 결과, 실행 메타)."""
    exe = shutil.which("claude") or "/opt/homebrew/bin/claude"
    cmd = [exe, "-p", instruction, "--model", MODEL, "--tools", "", "--strict-mcp-config",
           "--no-session-persistence", "--system-prompt", system,
           "--json-schema", json.dumps(schema, ensure_ascii=False), "--output-format", "json"]
    r = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"claude 실행 실패 (exit {r.returncode}): {(r.stderr or r.stdout)[-500:]}")
    out = json.loads(r.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude 오류: {str(out.get('result'))[:500]}")
    data = out.get("structured_output")
    if data is None:  # 구조화 출력 필드가 없으면 본문 텍스트에서 JSON을 읽는다
        data = json.loads(out["result"])
    return data, {k: out.get(k) for k in ("duration_ms", "num_turns", "total_cost_usd")}


def save(path: Path, history_name: str, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    COLLECTED.mkdir(exist_ok=True)
    with (COLLECTED / history_name).open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


# ── 1) 텔레그램 리서치 ───────────────────────────────
TG_WINDOW_HOURS = 24
TG_MAX_POST_CHARS = 1500  # 글 하나가 요약을 독차지하지 않도록
TG_SYSTEM = """너는 채권·매크로 리서치 에디터다. 증권사·리서치 텔레그램 채널의 최근 24시간 글을 읽고,
채권·매크로 투자자가 훑어볼 '논점 요약'을 한국어로 만든다.

규칙
- 비슷한 내용은 하나의 논점으로 묶어 3~5개 논점으로 정리한다. 중요한 순서대로.
- 각 논점 요약은 2~3문장. 수치(금리, bp, 성장률, 날짜)는 원문 그대로 보존한다.
- 의견은 작성 채널의 견해로 전달한다("~로 본다", "~를 지적"). 원인이나 전망을 단정하지 않는다.
- 글에 없는 사실을 덧붙이지 않는다. 광고·행사 안내·인사말은 무시한다.
- source_ids에는 그 논점의 근거가 된 글의 id(예: P3)만 넣는다. 없는 id를 만들지 않는다.
- rates_view는 그 논점이 금리에 주는 방향: 금리 상승 요인 / 금리 하락 요인 / 혼재 / 해당 없음.
- headline은 오늘 논점 전체를 한 문장으로.
- watch에는 글에서 언급된 앞으로의 일정·이벤트(지표 발표, 회의 등)를 짧게. 없으면 빈 목록."""
TG_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "themes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"}, "summary": {"type": "string"},
                "rates_view": {"type": "string", "enum": ["금리 상승 요인", "금리 하락 요인", "혼재", "해당 없음"]},
                "source_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "summary", "rates_view", "source_ids"], "additionalProperties": False}},
        "watch": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "themes", "watch"], "additionalProperties": False,
}


def summarize_telegram() -> None:
    since = pd.Timestamp.now() - pd.Timedelta(hours=TG_WINDOW_HOURS)
    posts = []
    for handle in config.TELEGRAM_CHANNELS:
        try:
            posts += [p for p in fetch_channel(handle) if p["time"] >= since]
        except Exception as e:
            log.warning(f"{handle} 글 수집 실패: {e}")
    posts.sort(key=lambda p: p["time"])
    for i, p in enumerate(posts, 1):
        p["id"] = f"P{i}"
    log.info(f"[텔레그램] 최근 {TG_WINDOW_HOURS}시간 글 {len(posts)}개 ({len(config.TELEGRAM_CHANNELS)}개 채널)")

    meta, data = {}, {"headline": "최근 24시간 동안 새 글이 없습니다.", "themes": [], "watch": []}
    if posts:
        text = "\n\n---\n\n".join(
            f"[{p['id']}] {p['channel']}" + (f" (전달: {p['forwarded']})" if p.get("forwarded") else "")
            + f" | {p['time']:%m/%d %H:%M} KST\n" + (p["text"][:TG_MAX_POST_CHARS] if p["text"] else "[이미지만 있는 글]")
            for p in posts)
        data, meta = run_claude(TG_SYSTEM, TG_SCHEMA, text, "아래 텔레그램 글들을 규칙에 따라 요약해 JSON으로만 답하라.")

    by_id = {p["id"]: p for p in posts}
    themes = []
    for t in data.get("themes", []):
        sources = [by_id[i] for i in dict.fromkeys(t.get("source_ids", [])) if i in by_id]  # 없는 id는 버림
        if sources:
            themes.append({"title": t["title"].strip(), "summary": t["summary"].strip(), "rates_view": t["rates_view"],
                           "sources": [{"channel": s["channel"], "time": s["time"].strftime("%Y-%m-%d %H:%M"),
                                        "link": s["link"]} for s in sources]})
    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "window_hours": TG_WINDOW_HOURS,
        "post_count": len(posts),
        "channels": pd.Series([p["channel"] for p in posts]).value_counts().to_dict() if posts else {},
        "headline": data.get("headline", "").strip(), "themes": themes,
        "watch": [w.strip() for w in data.get("watch", []) if w.strip()], "model": MODEL, "meta": meta,
    }
    save(config.SUMMARY_FILE, "telegram_summaries.jsonl", summary)
    log.info(f"[텔레그램] 논점 {len(themes)}개 · {meta.get('duration_ms', 0) / 1000:.0f}초")


# ── 2) 뉴스 이슈 ─────────────────────────────────────
NEWS_WINDOW_HOURS = 12
NEWS_CATEGORIES = ["금리·연준", "경제지표", "증시", "기업·산업", "정책·정치", "환율·원자재", "기타"]
NEWS_SYSTEM = f"""너는 금융 뉴스 데스크 에디터다. 최근 {NEWS_WINDOW_HOURS}시간 국내·해외 금융 뉴스 헤드라인을 읽고,
투자자가 '지금 무슨 이슈가 있는지' 한눈에 알 수 있게 이슈별로 묶는다. 한국어로 쓴다.

규칙
- 같은 사건·주제를 다룬 기사들을 하나의 이슈로 묶는다.
- 국내 이슈 4~5개, 해외 이슈 4~5개를 고른다. 기사가 많이 몰린 이슈와 시장 영향이 큰 이슈를 우선한다.
  국내 기사가 훨씬 많더라도 해외 이슈를 따로 고른다. 지역은 기사의 출처가 아니라 사건이 일어난 곳 기준
  (예: 한국 언론이 다룬 엔비디아 실적·앤트로픽 나스닥 상장·미 연준은 해외, 국내 증시 반응은 국내).
- title은 15자 안팎의 짧은 이슈명. summary는 한 문장(60자 안팎), 기사에 있는 수치는 보존.
- 기사에 없는 사실이나 전망을 덧붙이지 않는다. 연예·스포츠·사건사고 등 금융과 무관한 기사는 무시한다.
- article_ids에는 그 이슈에 해당하는 기사의 id(예: N12)를 빠짐없이 모두 넣는다(기사 수가 화제성으로 쓰인다).
  없는 id를 만들지 않는다.
- category는 {", ".join(NEWS_CATEGORIES)} 중 하나."""
NEWS_SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "region": {"type": "string", "enum": ["국내", "해외"]},
            "title": {"type": "string"}, "summary": {"type": "string"},
            "category": {"type": "string", "enum": NEWS_CATEGORIES},
            "article_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["region", "title", "summary", "category", "article_ids"], "additionalProperties": False}}},
    "required": ["issues"], "additionalProperties": False,
}


def summarize_news() -> None:
    news = get_news()
    news = news[news["time"] >= pd.Timestamp.now() - pd.Timedelta(hours=NEWS_WINDOW_HOURS)].sort_values("time")
    news = news.reset_index(drop=True)
    news["id"] = [f"N{i}" for i in range(1, len(news) + 1)]
    log.info(f"[뉴스] 최근 {NEWS_WINDOW_HOURS}시간 기사 {len(news)}건")
    if news.empty:
        return
    text = "\n".join(f"[{r.id}] ({r.source}·{r.category}) {r.title}" + (f" — {r.summary[:80]}" if r.summary else "")
                     for r in news.itertuples())
    data, meta = run_claude(NEWS_SYSTEM, NEWS_SCHEMA, text, "아래 헤드라인을 규칙에 따라 이슈별로 묶어 JSON으로만 답하라.")

    by_id = news.set_index("id")
    issues = []
    for it in data.get("issues", []):
        ids = [i for i in dict.fromkeys(it.get("article_ids", [])) if i in by_id.index]  # 없는 id는 버림
        if not ids:
            continue
        arts = by_id.loc[ids].sort_values("time", ascending=False)
        issues.append({
            "region": it["region"], "title": it["title"].strip(), "summary": it["summary"].strip(),
            "category": it["category"], "count": len(ids),
            "articles": [{"title": a.title, "source": a.source, "time": a.time.strftime("%Y-%m-%d %H:%M"),
                          "link": a.link} for a in arts.head(3).itertuples()],
        })
    issues.sort(key=lambda x: x["count"], reverse=True)
    payload = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "window_hours": NEWS_WINDOW_HOURS,
               "article_count": len(news), "issues": issues, "model": MODEL, "meta": meta}
    save(config.NEWS_SUMMARY_FILE, "news_summaries.jsonl", payload)
    log.info(f"[뉴스] 이슈 {len(issues)}개 (국내 {sum(i['region'] == '국내' for i in issues)}, "
             f"해외 {sum(i['region'] == '해외' for i in issues)}) · {meta.get('duration_ms', 0) / 1000:.0f}초")


def main() -> int:
    log.info("=== AI 요약 시작 ===")
    failed = []
    for job in (summarize_telegram, summarize_news):
        try:
            job()
        except Exception as e:  # 실패한 쪽은 직전 요약을 그대로 두어 화면에 남게 한다
            failed.append(job.__name__)
            log.exception(f"{job.__name__} 실패: {e}")
    log.info(f"=== AI 요약 종료 (실패 {len(failed)}개{': ' + ', '.join(failed) if failed else ''}) ===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
