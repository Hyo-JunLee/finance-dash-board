"""AI 요약 (Claude Code 헤드리스 실행) — 하루 2회 08:30·16:00, scripts/install_summarizer.sh

1) 텔레그램 리서치 6개 채널 최근 24시간 글 → 채권·매크로 논점 3~5개
2) 뉴스 헤드라인 최근 12시간 → 국내·해외 이슈 각 4~5개 (기사 수로 화제성 표시)
3) 바이사이드 관점 5개 채널 최근 24시간 글 → '무엇이 일어났나'가 아니라 논지·시장 반영과의 차이·확인/반증 신호
4) 10년물 판단 (08:30 실행에서만, 또는 --insight) → 규칙 기반 분해를 근거로 받아 맥락까지 보고 쉬운 말로 판단 + 규칙과 일치 여부 기록

- API 키 대신 이 맥에 로그인된 Claude Code(`claude -p`)를 써서 별도 결제 없이 구독 사용 한도에서 처리
- 도구는 모두 끄고(--tools ""), MCP도 불러오지 않으며(--strict-mcp-config), 정해진 JSON 형식으로만 받는다
- 결과마다 근거 글·기사 id를 달게 하고, 실제로 넘겨준 목록에 없는 id는 버린다 (출처 지어내기 방지)
- 한쪽이 실패해도 다른 쪽은 진행하고, 실패한 쪽은 직전 요약 파일을 그대로 둔다
"""
import json
import logging
import re
import shutil
import subprocess
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")

import pandas as pd
import streamlit.runtime.caching.cache_data_api  # noqa: F401

# Streamlit 밖에서 캐시 함수를 쓸 때 나는 "No runtime found" 경고 숨김
logging.getLogger("streamlit.runtime.caching.cache_data_api").disabled = True

sys.path.insert(0, str(Path(__file__).parent))
import config
from data.news import get_news
from data.telegram import fetch_since

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
    errors = 0
    for handle in config.TELEGRAM_CHANNELS:
        try:
            posts += fetch_since(handle, since)
        except Exception as e:
            errors += 1
            log.warning(f"{handle} 글 수집 실패: {e}")
    if errors == len(config.TELEGRAM_CHANNELS):  # 네트워크 문제를 '새 글 없음'으로 덮어쓰지 않게
        raise RuntimeError("모든 채널 수집 실패 — 직전 요약을 그대로 둡니다")
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
        raise RuntimeError("최근 기사 0건 (네트워크·피드 문제로 보임) — 직전 요약을 그대로 둡니다")
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


# ── 3) 바이사이드 관점 ───────────────────────────────
BS_WINDOW_HOURS = 24
BS_HORIZONS = ["단기 (이벤트·수주)", "중기 (수개월)", "장기 (구조적·1년+)", "불명"]
BS_SYSTEM = """너는 바이사이드 운용역의 리서치 어시스턴트다. 매크로·시장 텔레그램 채널의 최근 24시간 글에서
'시장이 왜 움직였나'라는 사후 설명이 아니라, 앞으로의 판단에 쓸 수 있는 '관점(view)'만 골라 한국어로 정리한다.

관점으로 볼 글
- 시장 가격(금리 선물·OIS·밸류에이션·컨센서스)에 무엇이 반영돼 있고, 작성자가 어디가 다르다고 보는지
- 여러 뉴스 중 무엇이 더 중요한지 우선순위를 매기는 판단 (예: "A보다 B가 더 의미 있다")
- 자금 흐름·포지셔닝·수급이 말해 주는 것
- 국면(레짐) 가설과 그 근거
단순 뉴스 전달, 실시간 중계, 인사·광고, 판단 없는 지정학 소식은 관점으로 만들지 않는다.
같은 논지를 다룬 글은 하나로 묶고, 중요하고 구체적인 순서로 3~6개.

필드
- title: 관점을 한 구절로 (예: "AI 수요가 대중 수출 공백을 상쇄").
- thesis: 작성자의 논지 1~2문장. 작성자 견해로 전달("~로 본다"). 수치는 원문 그대로.
- vs_market: 시장에 이미 반영된 것과 이 관점이 다른 지점. 글에 언급이 없으면 빈 문자열.
- evidence: 글에 나온 근거 데이터·사실 1~3개. 글에 없는 것은 넣지 않는다.
- confirm / refute: 앞으로 이 관점이 맞는지 확인할 신호와 틀렸음을 보여줄 신호 각 1~2개.
  관찰 가능한 지표·이벤트로 구체적으로 쓴다(예: "12월 FOMC 점도표 상향", "외국인 반도체 순매수 전환").
- signals_inferred: confirm/refute를 글이 직접 말하지 않아 네가 논지에서 끌어냈으면 true.
- horizon: 관점의 시간축.
- assets: 영향받는 자산 1~4개를 짧게 (예: "미 2년물", "KOSPI 반도체", "엔화").
- source_ids: 근거 글 id(예: P3)만. 없는 id를 만들지 않는다.
- headline: 오늘 관점들을 꿰는 한 문장. 관점이 없으면 그렇다고 쓴다."""
BS_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "views": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"}, "thesis": {"type": "string"}, "vs_market": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "confirm": {"type": "array", "items": {"type": "string"}},
                "refute": {"type": "array", "items": {"type": "string"}},
                "signals_inferred": {"type": "boolean"},
                "horizon": {"type": "string", "enum": BS_HORIZONS},
                "assets": {"type": "array", "items": {"type": "string"}},
                "source_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "thesis", "vs_market", "evidence", "confirm", "refute", "signals_inferred",
                         "horizon", "assets", "source_ids"], "additionalProperties": False}},
    },
    "required": ["headline", "views"], "additionalProperties": False,
}


def summarize_buyside() -> None:
    since = pd.Timestamp.now() - pd.Timedelta(hours=BS_WINDOW_HOURS)
    posts = []
    errors = 0
    for handle in config.BUYSIDE_CHANNELS:
        try:
            posts += fetch_since(handle, since)
        except Exception as e:
            errors += 1
            log.warning(f"{handle} 글 수집 실패: {e}")
    if errors == len(config.BUYSIDE_CHANNELS):  # 네트워크 문제를 '새 글 없음'으로 덮어쓰지 않게
        raise RuntimeError("모든 채널 수집 실패 — 직전 요약을 그대로 둡니다")
    posts = [p for p in posts if p["text"]]  # 이미지만 있는 글은 논지를 읽을 수 없음
    posts.sort(key=lambda p: p["time"])
    for i, p in enumerate(posts, 1):
        p["id"] = f"P{i}"
    log.info(f"[바이사이드] 최근 {BS_WINDOW_HOURS}시간 글 {len(posts)}개 ({len(config.BUYSIDE_CHANNELS)}개 채널)")

    meta, data = {}, {"headline": "최근 24시간 동안 새 글이 없습니다.", "views": []}
    if posts:
        text = "\n\n---\n\n".join(
            f"[{p['id']}] {p['channel']}" + (f" (전달: {p['forwarded']})" if p.get("forwarded") else "")
            + f" | {p['time']:%m/%d %H:%M} KST\n" + p["text"][:TG_MAX_POST_CHARS] for p in posts)
        data, meta = run_claude(BS_SYSTEM, BS_SCHEMA, text, "아래 텔레그램 글에서 관점을 규칙에 따라 정리해 JSON으로만 답하라.")

    by_id = {p["id"]: p for p in posts}
    clean = lambda xs: [x.strip() for x in xs if x.strip()]
    views = []
    for v in data.get("views", []):
        sources = [by_id[i] for i in dict.fromkeys(v.get("source_ids", [])) if i in by_id]  # 없는 id는 버림
        if not sources:
            continue
        views.append({
            "title": v["title"].strip(), "thesis": v["thesis"].strip(), "vs_market": v["vs_market"].strip(),
            "evidence": clean(v["evidence"]), "confirm": clean(v["confirm"]), "refute": clean(v["refute"]),
            "signals_inferred": bool(v["signals_inferred"]), "horizon": v["horizon"], "assets": clean(v["assets"]),
            "sources": [{"channel": s["channel"], "time": s["time"].strftime("%Y-%m-%d %H:%M"), "link": s["link"]}
                        for s in sources],
        })
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "window_hours": BS_WINDOW_HOURS,
        "post_count": len(posts),
        "channels": pd.Series([p["channel"] for p in posts]).value_counts().to_dict() if posts else {},
        "headline": data.get("headline", "").strip(), "views": views, "model": MODEL, "meta": meta,
    }
    save(config.BUYSIDE_SUMMARY_FILE, "buyside_summaries.jsonl", payload)
    log.info(f"[바이사이드] 관점 {len(views)}개 · {meta.get('duration_ms', 0) / 1000:.0f}초")


# ── 공용: 10년물 판단에 넣는 일정·지표 텍스트 ─────────────
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _calendar_text(hours: int = 40) -> str:
    from data.econ_calendar import get_week_calendar
    cal = get_week_calendar()
    now = pd.Timestamp.now(tz="Asia/Seoul")
    cal = cal[(cal["일시(KST)"] >= now) & (cal["일시(KST)"] <= now + pd.Timedelta(hours=hours))
              & (cal["_impact"].isin(["High", "Medium"]))]
    return "\n".join(f"- {r['일시(KST)']:%m/%d %H:%M} {r['지표']} ({r['중요도']}) 예상 {r['예상'] or '-'} 이전 {r['이전'] or '-'}"
                     for _, r in cal.iterrows()) or "- 중요 일정 없음"


def _recent_text(n: int = 8) -> str:
    from data.econ_calendar import get_recent_indicators
    r = get_recent_indicators().sort_values("발표", ascending=False).head(n)
    return "\n".join(f"- {x['지표']}: {x['최신']} (예상 {x['예상'] or '-'}, {x['서프라이즈'] or '-'}) 발표 {x['발표']:%m/%d}"
                     for _, x in r.iterrows())


# ── 4) 10년물 판단 (채권 › 곡선 분해 맨 위) ──────────────
BOND_SYSTEM = """너는 미국 국채 애널리스트이자 설명을 잘하는 선생님이다. 독자는 금융을 공부하는 사람이다.
'미국 10년물 금리가 왜 움직였나'를 기간별(1주, 1개월)로 판단해 쉬운 한국어로 쓴다.

판단 방법
- '규칙 기반 근거'는 정해진 공식으로 계산한 숫자와 판정이다. 참고하되 그대로 따르지 않는다.
  커브 모양, 실질금리와 기대인플레의 움직임, 수급, 입찰, 최근 지표, 뉴스·리서치 맥락까지 보고 스스로 판단한다.
- 공통 기간 분해(ACM 모형)는 며칠 늦게 공개된다. '최신 기준' 숫자와 크게 다르면 그 차이를 판단에 반영하고 짚어 준다.
- 규칙 판정과 같은지 다른지(vs_rule)를 정하고, 다르면 왜 다르게 보는지 vs_rule_reason에 쓴다.
- 맥락 자료(뉴스·리서치·관점)는 최근 하루치다. 기간 초반의 원인을 이것으로 지어내지 않는다.
  원인으로 쓸 때는 context_ids에 그 id(예: N2, R1, B3)를 넣고, 데이터로 확인되지 않으면 '가능성'으로 쓴다.
- 수급 스트레스가 '주의' 이상이거나 유동성 경고가 있으면 반드시 언급한다.

쓰는 방법 (가장 중요)
- 문체: 한국어 개조식. 모든 문장(headline·explanation·evidence·이유·change_mind)은 "~음.", "~함.", "~임.", "~됨."처럼
  명사형으로 끝낸다. 경어체(~습니다, ~요)와 평서체(~했다, ~이다) 금지.
  예: "10년물은 1개월간 15bp 내림." "물가 걱정이 줄어든 영향이 큼." "BEI가 다시 오르면 판단을 바꿀 것임."
- headline: 결론 한 문장(55자 이내). 전문용어 없이, 무엇이 금리를 움직였는지가 바로 보이게. 명사형으로 끝낸다.
  좋은 예: "이번 주 금리 하락은 경기 걱정보다 '물가가 덜 오를 것'이라는 기대가 이끎"
- explanation: 2~4문장. ① 무엇이 움직였나 → ② 왜 그렇게 보나 → ③ 그래서 무슨 뜻인가.
  A·B·C·D 같은 기호는 쓰지 않는다. 텀프리미엄, BEI 같은 용어는 처음 쓸 때 괄호로 풀어 쓴다
  (예: 텀프리미엄(장기채를 오래 들고 있는 대가로 더 받는 금리)).
- evidence: 판단을 받치는 근거 2~3개. 각각 짧은 한 문장에 숫자 하나 이상. (예: "BEI가 1개월간 22bp 내림.")
- confidence: 높음/중간/낮음과 그 이유 한 문장 (예: 모형 데이터가 며칠 늦음, 움직임이 작음, 근거끼리 어긋남).
- change_mind: 이게 나오면 판단을 바꾸겠다는 관찰 가능한 신호 1~2개. (예: "BEI가 10bp 넘게 반등하면 물가 기대 약화 해석을 바꿈.")
- 숫자는 아래 데이터에 있는 값만 그대로 쓴다. 새로 계산하지 않는다. 단정하지 않고 투자 권유를 하지 않는다."""
_BOND_WINDOW_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"}, "explanation": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "vs_rule": {"type": "string", "enum": ["일치", "부분 일치", "다름"]}, "vs_rule_reason": {"type": "string"},
        "confidence": {"type": "string", "enum": ["높음", "중간", "낮음"]}, "confidence_reason": {"type": "string"},
        "change_mind": {"type": "array", "items": {"type": "string"}},
        "context_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "explanation", "evidence", "vs_rule", "vs_rule_reason", "confidence", "confidence_reason",
                 "change_mind", "context_ids"], "additionalProperties": False,
}


def _context_items() -> tuple[str, dict]:
    """뉴스 이슈(N)·리서치 논점(R)·바이사이드 관점(B)을 id와 함께. 반환: (텍스트, {id: 출처 정보})."""
    from data.news import load_news_summary
    from data.telegram import load_summary
    items, rows = {}, []
    news = load_news_summary()
    for i, it in enumerate((news or {}).get("issues", []), 1):
        items[f"N{i}"] = {"kind": "뉴스", "title": it["title"], "link": it["articles"][0]["link"] if it["articles"] else ""}
        rows.append(f"[N{i}] 뉴스 이슈({it['region']}, 기사 {it['count']}건): {it['title']} — {it['summary']}")
    res = load_summary()
    for i, t in enumerate((res or {}).get("themes", []), 1):
        items[f"R{i}"] = {"kind": "리서치", "title": t["title"], "link": t["sources"][0]["link"] if t["sources"] else ""}
        rows.append(f"[R{i}] 리서치 논점({t['rates_view']}): {t['title']} — {t['summary']}")
    bs = load_summary(config.BUYSIDE_SUMMARY_FILE)
    for i, v in enumerate((bs or {}).get("views", []), 1):
        items[f"B{i}"] = {"kind": "바이사이드", "title": v["title"], "link": v["sources"][0]["link"] if v["sources"] else ""}
        rows.append(f"[B{i}] 바이사이드 관점: {v['title']} — {v['thesis']}" + (f" (시장 반영과 차이: {v['vs_market']})" if v["vs_market"] else ""))
    return "\n".join(rows) or "- 맥락 자료 없음", items


def summarize_bond_view() -> None:
    from data.bond_story import WINDOWS, rule_snapshot, snapshot_text
    snaps = {w: rule_snapshot(w) for w in WINDOWS}
    context, items = _context_items()
    text = (f"작성 시각: {datetime.now():%Y-%m-%d %H:%M} KST\n\n"
            + "\n\n".join(f"[규칙 기반 근거 · {w}]\n{snapshot_text(s)}" for w, s in snaps.items())
            + f"\n\n[최근 발표 지표 (예상 대비)]\n{_recent_text()}\n\n[향후 40시간 미국 주요 일정 (KST)]\n{_calendar_text()}"
            + f"\n\n[맥락: 최근 하루 뉴스·리서치·관점 (id)]\n{context}")
    keys = {"1주": "week", "1개월": "month"}  # 스키마 키는 영문만 허용
    schema = {"type": "object", "properties": {keys[w]: _BOND_WINDOW_SCHEMA for w in WINDOWS},
              "required": [keys[w] for w in WINDOWS], "additionalProperties": False}
    data, meta = run_claude(BOND_SYSTEM, schema, text,
                            "아래 데이터로 기간별 10년물 판단을 규칙에 따라 JSON으로만 답하라. week = 1주, month = 1개월.")

    source_nums = {n.replace(",", "") for n in _NUM.findall(text)}
    windows = {}
    for w, s in snaps.items():
        v = data.get(keys[w]) or {}
        if not v.get("headline"):
            continue
        written = [v["headline"], v["explanation"], *v["evidence"], v["vs_rule_reason"], v["confidence_reason"], *v["change_mind"]]
        windows[w] = {
            "headline": v["headline"].strip(), "explanation": v["explanation"].strip(),
            "evidence": [x.strip() for x in v["evidence"] if x.strip()], "vs_rule": v["vs_rule"],
            "vs_rule_reason": v["vs_rule_reason"].strip(), "confidence": v["confidence"],
            "confidence_reason": v["confidence_reason"].strip(),
            "change_mind": [x.strip() for x in v["change_mind"] if x.strip()],
            "sources": [{"id": i, **items[i]} for i in dict.fromkeys(v.get("context_ids", [])) if i in items],  # 없는 id는 버림
            "unverified_numbers": sorted({n for line in written for n in _NUM.findall(line) if n.replace(",", "") not in source_nums}),
            # 나중에 AI·규칙 비교와 사후 채점에 쓰는 기록
            "rule_verdict": s["attr"].verdict, "rule_leader": s["attr"].leader, "nominal_bp": round(s["attr"].nominal, 1),
            "fresh_nominal_bp": round(float(s["fresh"]["명목 10년"]), 1), "end": f"{s['end']:%Y-%m-%d}",
            "panel1_end": f"{s['panel1_end']:%Y-%m-%d}", "stress": s["stress"]["level"],
        }
    payload = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "windows": windows, "input": text,
               "model": MODEL, "meta": meta}
    save(config.BOND_VIEW_FILE, "bond_views.jsonl", payload)
    log.info(f"[10년물 판단] " + " · ".join(f"{w}: {v['vs_rule']}" for w, v in windows.items())
             + f" · {meta.get('duration_ms', 0) / 1000:.0f}초")


def wait_for_network(host: str = "t.me", timeout: int = 300) -> bool:
    """잠자기에서 깨어난 직후 실행되면 아직 인터넷이 안 붙어 있다 → 이름 풀이가 될 때까지 최대 5분 기다린다."""
    import socket
    import time
    start = time.time()
    while True:
        try:
            socket.getaddrinfo(host, 443)
            if time.time() - start > 1:
                log.info(f"네트워크 연결 확인 ({time.time() - start:.0f}초 대기)")
            return True
        except OSError:
            if time.time() - start > timeout:
                log.warning(f"{timeout}초 기다려도 네트워크 없음 — 그대로 진행")
                return False
            time.sleep(10)


SLOTS = ((8, 30), (16, 0))  # 정해진 실행 시각 (launchd StartCalendarInterval과 같게)
CATCHUP_MAX_ATTEMPTS = 6    # 한 회차당 따라잡기 재시도 상한 (30분 간격 → 약 3시간)
CATCHUP_STATE = config.CACHE_DIR / "summarize_catchup.json"


def due_slot(now: datetime) -> datetime:
    """지금 기준 가장 최근에 지나간 실행 회차 시각 (08:30 이전이면 전날 16:00)."""
    today = [now.replace(hour=h, minute=m, second=0, microsecond=0) for h, m in SLOTS]
    past = [t for t in today if t <= now]
    if past:
        return past[-1]
    return today[-1] - timedelta(days=1)


def slot_jobs(slot: datetime) -> list:
    jobs = [summarize_telegram, summarize_news, summarize_buyside]
    if slot.hour < 11:  # 하루 2회 중 아침 08:30 회차에서만
        jobs += [summarize_bond_view]  # 앞의 요약 결과도 재료로 쓰므로 마지막
    return jobs


JOB_FILES = {"summarize_telegram": config.SUMMARY_FILE, "summarize_news": config.NEWS_SUMMARY_FILE,
             "summarize_buyside": config.BUYSIDE_SUMMARY_FILE, "summarize_bond_view": config.BOND_VIEW_FILE}


def pending_jobs(slot: datetime, jobs: list) -> list:
    """그 회차 시각 이후 결과 파일이 갱신되지 않은 작업 (성공하면 항상 파일을 쓰고, 실패하면 직전 것을 둔다)."""
    def done(job):
        f = JOB_FILES[job.__name__]
        return f.exists() and datetime.fromtimestamp(f.stat().st_mtime) >= slot
    return [j for j in jobs if not done(j)]


def _catchup_attempts(slot: datetime, add: int = 0) -> int:
    key = slot.isoformat(timespec="minutes")
    try:
        state = json.loads(CATCHUP_STATE.read_text())
    except Exception:
        state = {}
    n = (state.get("attempts", 0) if state.get("slot") == key else 0) + add
    if add:
        CATCHUP_STATE.write_text(json.dumps({"slot": key, "attempts": n}))
    return n


def main() -> int:
    """기본: 가장 최근 회차의 작업을 모두 실행.
    --catchup: 그 회차에 아직 성공하지 못한 작업만 실행 (launchd가 정시 + 30분마다 호출 — 맥이 꺼져 있거나
    인터넷 없이 깨어나 정시 실행이 실패해도 연결되면 따라잡는다). 할 일이 없으면 조용히 끝낸다."""
    slot = due_slot(datetime.now())
    jobs = slot_jobs(slot)
    if "--insight" in sys.argv and summarize_bond_view not in jobs:
        jobs += [summarize_bond_view]
    if "--catchup" in sys.argv:
        jobs = pending_jobs(slot, jobs)
        if not jobs or _catchup_attempts(slot) >= CATCHUP_MAX_ATTEMPTS:
            return 0
        if not wait_for_network(timeout=60):  # 인터넷 없는 잠깐 깨어남: 시도 횟수를 쓰지 않고 다음 점검으로
            return 0
        _catchup_attempts(slot, add=1)
        log.info(f"=== AI 요약 ({slot:%m/%d %H:%M} 회차, {len(jobs)}개: {', '.join(j.__name__ for j in jobs)}) ===")
    else:
        log.info("=== AI 요약 시작 ===")
        wait_for_network()
    failed = []
    # 아침 인사이트는 AI 없이 화면에서 원문 카드로 보여준다 (data/morning_brief.py)
    for job in jobs:
        try:
            job()
        except Exception as e:  # 실패한 쪽은 직전 요약을 그대로 두어 화면에 남게 한다
            failed.append(job.__name__)
            log.exception(f"{job.__name__} 실패: {e}")
    log.info(f"=== AI 요약 종료 (실패 {len(failed)}개{': ' + ', '.join(failed) if failed else ''}) ===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
