"""AI 요약 (Claude Code 헤드리스 실행) — 하루 3회 08:30·13:30·18:30, scripts/install_summarizer.sh

1) 텔레그램 리서치 6개 채널 최근 24시간 글 → 채권·매크로 논점 3~5개
2) 뉴스 헤드라인 최근 12시간 → 국내·해외 이슈 각 4~5개 (기사 수로 화제성 표시)
3) 바이사이드 관점 5개 채널 최근 24시간 글 → '무엇이 일어났나'가 아니라 논지·시장 반영과의 차이·확인/반증 신호
4) 아침 인사이트 (08:30 실행에서만, 또는 --insight) → 대시보드 데이터를 엮은 1~2문장. 숫자는 넘겨준 데이터에서만

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
    for handle in config.TELEGRAM_CHANNELS:
        try:
            posts += fetch_since(handle, since)
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
    for handle in config.BUYSIDE_CHANNELS:
        try:
            posts += fetch_since(handle, since)
        except Exception as e:
            log.warning(f"{handle} 글 수집 실패: {e}")
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


# ── 4) 아침 인사이트 ─────────────────────────────────
INSIGHT_SYSTEM = """너는 매크로·채권 운용역의 아침 회의를 준비하는 애널리스트다. 아래 대시보드 데이터만 보고
오늘 아침 가장 중요한 인사이트를 한국어 1~2문장으로 쓴다.

규칙
- 어제 일어난 일을 나열하지 않는다. 여러 데이터를 엮어 '무엇이 달라졌고 그래서 오늘 무엇이 중요한지'를 쓴다.
  예: 금리 변화의 성격(분해 판정) × 수급 상태 × 오늘 일정, 또는 가격과 관점의 어긋남.
- 데이터가 서로 어긋나면(예: 금리는 크게 움직였는데 수급은 조용함) 그 점이 인사이트가 될 수 있다.
- 숫자는 아래 데이터에 있는 값만 그대로 쓴다. 새로 계산하거나 반올림을 바꾼 숫자를 만들지 않는다.
- 단정하지 않는다("가능성", "해석 가능", "주목"). 투자 권유를 하지 않는다.
- lines: 1~2문장, 각 100자 이내. watch: 오늘 확인할 것 하나(30자 이내).
- basis: 근거로 쓴 데이터 항목 이름 2~4개 (예: "미국 10년물", "10년물 분해", "수급·포지션", "일정: 소매판매")."""
INSIGHT_SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "string"}},
                   "watch": {"type": "string"}, "basis": {"type": "array", "items": {"type": "string"}}},
    "required": ["lines", "watch", "basis"], "additionalProperties": False,
}
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _insight_data() -> str:
    """대시보드가 이미 계산하는 값을 텍스트로 모은다. 섹션마다 실패해도 나머지는 진행."""
    from data import bonds as b
    from data import flow_data as fd
    from data import flow_regime as fr
    from data.bond_attribution import LEADER_LABELS, attribute, curve_check
    from data.econ_calendar import get_recent_indicators, get_week_calendar
    from data.market import SUMMARY_ITEMS, get_snapshot
    from data.news import load_news_summary
    from data.rates import NOMINAL, get_treasury_curve
    from data.telegram import load_summary

    out = [f"작성 시각: {datetime.now():%Y-%m-%d %H:%M} KST"]

    def section(title, fn):
        try:
            out.append(f"\n[{title}]\n" + fn())
        except Exception as e:
            log.warning(f"[인사이트] {title} 실패: {e}")

    def market():
        rows = []
        for key in SUMMARY_ITEMS:
            s = get_snapshot(key)
            rate = s["kind"] in ("rate", "spread")
            unit = "bp" if rate else "%"
            chg = s["chg"] * 100 if rate else s["pct"]
            per = " ".join(f"{k} {v:+.1f}{unit}" for k, v in s.get("periods", {}).items())
            rows.append(f"- {s['label']}: {s['last']:,.2f}{'%' if rate else ''} | 전일 대비 {chg:+.1f}{unit} | "
                        f"z {s['z']:+.1f} | {per} | 기준 {s['asof']:%m/%d %H:%M}")
        return "(z = 오늘 변화 / 1년 일간 표준편차)\n" + "\n".join(rows)

    def bonds():
        rb, acm = b.real_bei_10y(), b.acm_10y()
        end = min(rb.index[-1], acm.index[-1])
        zs, _ = fd.stress_series(config.FLOW_Z_WINDOW_DAYS)
        rows = []
        for label, off in (("1주", pd.DateOffset(weeks=1)), ("1개월", pd.DateOffset(months=1))):
            since = end - off
            rc, ac = b.change_bp(rb[rb.index <= end], since), b.change_bp(acm[acm.index <= end], since)
            a = attribute(rc["실질 10년"], rc["기대인플레(BEI)"], ac["기대 단기금리 경로"], ac["텀 프리미엄"],
                          config.ATTR_NOISE_BP)
            curve = get_treasury_curve((since - pd.Timedelta(days=10)).date(), NOMINAL)
            cc = b.change_bp(curve[curve.index <= end][["2 Yr", "10 Yr", "30 Yr"]], since)
            _, premium_like = curve_check(cc["2 Yr"], cc["10 Yr"], cc["30 Yr"], config.ATTR_NOISE_BP)
            peaks = {k: (fr.window_peak(z, since, end) if z is not None else None) for k, z in zs.items()}
            stress = fr.stress_level({k: (p[0] if p else None) for k, p in peaks.items()},
                                     config.FLOW_Z_THRESHOLD, config.FLOW_STRESS_COUNT)
            mismatch = fr.curve_mismatch(a.leader, premium_like, abs(cc["2 Yr"]) >= config.ATTR_NOISE_BP)
            _, combined = fr.combine(a.leader, abs(a.nominal) >= config.ATTR_NOISE_BP, mismatch, stress["level"])
            comps = " ".join(f"{k} {v:+.0f}bp" for k, v in a.components.items())
            rows.append(f"- {label} (~{end:%m/%d}): 10년물 {a.nominal:+.0f}bp, 판정 '{a.verdict}' "
                        f"(A=정책·성장 기대, C=실질 텀프리미엄, D=인플레 리스크 프리미엄: {comps}) | "
                        f"수급 스트레스 {stress['level']} (|z|≥{config.FLOW_Z_THRESHOLD:g}: {', '.join(stress['hits']) or '없음'}) | "
                        f"결합 해석: {combined.replace('**', '')}")
        return "\n".join(rows)

    def calendar():
        cal = get_week_calendar()
        now = pd.Timestamp.now(tz="Asia/Seoul")
        cal = cal[(cal["일시(KST)"] >= now) & (cal["일시(KST)"] <= now + pd.Timedelta(hours=40))
                  & (cal["_impact"].isin(["High", "Medium"]))]
        return "\n".join(f"- {r['일시(KST)']:%m/%d %H:%M} {r['지표']} ({r['중요도']}) 예상 {r['예상'] or '-'} 이전 {r['이전'] or '-'}"
                         for _, r in cal.iterrows()) or "- 중요 일정 없음"

    def recent():
        r = get_recent_indicators().sort_values("발표", ascending=False).head(8)
        return "\n".join(f"- {x['지표']}: {x['최신']} (예상 {x['예상'] or '-'}, {x['서프라이즈'] or '-'}) 발표 {x['발표']:%m/%d}"
                         for _, x in r.iterrows())

    def views():
        rows = []
        for name, data, key in (("리서치", load_summary(), "themes"), ("바이사이드 관점", load_summary(config.BUYSIDE_SUMMARY_FILE), "views"),
                                ("뉴스 이슈", load_news_summary(), "issues")):
            if data:
                items = data.get(key, [])[:5]
                rows.append(f"- {name} ({data['generated_at']}): {data.get('headline', '')} / "
                            + " / ".join(x["title"] for x in items))
        return "\n".join(rows)

    section("시장 (최신값)", market)
    section("미국 10년물 분해·수급", bonds)
    section("향후 40시간 미국 주요 일정 (KST)", calendar)
    section("최근 발표 지표 (예상 대비)", recent)
    section("오늘 아침 요약들 (제목만)", views)
    return "\n".join(out)


def summarize_insight() -> None:
    text = _insight_data()
    data, meta = run_claude(INSIGHT_SYSTEM, INSIGHT_SCHEMA, text, "아래 데이터로 오늘 아침 인사이트를 규칙에 따라 JSON으로만 답하라.")
    lines = [x.strip() for x in data.get("lines", []) if x.strip()][:2]
    # 숫자 검증: 넘겨준 데이터에 없는 숫자는 표시해 둔다 (지어낸 숫자 방지)
    source = {n.replace(",", "") for n in _NUM.findall(text)}  # 숫자 단위로 비교 (13.2 안의 3.2 같은 부분 일치 방지)
    unverified = sorted({n for line in lines + [data.get("watch", "")] for n in _NUM.findall(line)
                         if n.replace(",", "") not in source})
    payload = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "lines": lines,
               "watch": data.get("watch", "").strip(), "basis": [x.strip() for x in data.get("basis", []) if x.strip()],
               "unverified_numbers": unverified, "input": text, "model": MODEL, "meta": meta}
    save(config.INSIGHT_FILE, "morning_insights.jsonl", payload)
    log.info(f"[인사이트] {len(lines)}문장 · 확인 안 된 숫자 {unverified or '없음'} · {meta.get('duration_ms', 0) / 1000:.0f}초")


def main() -> int:
    log.info("=== AI 요약 시작 ===")
    failed = []
    jobs = [summarize_telegram, summarize_news, summarize_buyside]
    if datetime.now().hour < 11 or "--insight" in sys.argv:  # 하루 3회 중 아침 08:30 실행에서만
        jobs.append(summarize_insight)  # 앞의 요약 결과도 재료로 쓰므로 마지막
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
