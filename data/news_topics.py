"""뉴스 주제 태그와 같은 사건 묶기 (규칙 기반, AI 없음).

- 주제: 제목·요약의 키워드(한·영)로 0개 이상. 주제가 없는 기사(인사·행사·생활)는 화면에서 기본으로 숨김
- 묶기: 같은 언어 제목의 핵심 단어가 많이 겹치면 같은 사건으로 보고 한 줄로 (대표 = 가장 최근 기사)
"""
import re

import pandas as pd

# (주제, 한글 키워드, 영문 정규식) — 영문 약어는 대소문자를 구분해 엉뚱한 단어 속 일치를 막는다
TOPICS = [
    ("금리·채권", ["금리", "국채", "국고채", "채권", "회사채", "수익률", "크레딧"],
     r"(?i:\btreasur|\bbonds?\b|\byields?\b|\brates?\b|\brate (cut|hike)|\bgilts?\b|\bcredit\b)"),
    ("중앙은행", ["연준", "파월", "한은", "한국은행", "금통위", "기준금리", "일본은행", "라가르드", "이창용", "통화정책"],
     r"\bFed\b|\bFOMC\b|\bECB\b|\bBOJ\b|\bBOE\b|(?i:\bpowell|central bank|\blagarde|monetary policy)"),
    ("물가·지표", ["물가", "인플레", "고용", "실업", "성장률", "소매판매", "소비자심리", "수출", "수입", "무역수지", "경기"],
     r"\bCPI\b|\bPCE\b|\bPPI\b|\bGDP\b|\bISM\b|\bPMI\b|\bJOLTS\b|(?i:inflation|payroll|\bjobs?\b|jobless|"
     r"unemployment|retail sales|recession|economy|economic)"),
    ("환율", ["환율", "원/달러", "원·달러", "달러", "엔화", "위안", "외환", "외국환", "원화"],
     r"(?i:\bdollar|\byen\b|\byuan\b|\bcurrenc|\bforex\b|\beuro\b)"),
    ("증시", ["코스피", "코스닥", "증시", "주가", "지수", "외국인", "순매수", "순매도", "특징주", "상장", "공모",
             "매매동향", "실적", "영업익", "영업이익", "순이익", "목표주가", "시가총액"],
     r"S&P|\bDow\b|(?i:nasdaq|\bstocks?\b|\bshares\b|equit|wall street|\bIPO\b|rally|sell-?off|earnings)"),
    ("반도체·AI", ["반도체", "삼성전자", "하이닉스", "엔비디아", "메모리", "HBM", "인공지능"],
     r"\bAI\b|\bHBM\b|\bTSMC\b|\bAMD\b|(?i:nvidia|\bchips?\b|semiconductor|openai)"),
    ("원자재", ["유가", "원유", "브렌트", "금값", "금 가격", "구리", "원자재", "천연가스", "OPEC"],
     r"\bWTI\b|\bOPEC\b|(?i:\boil\b|\bcrude|\bgold\b|\bcopper|commodit|natural gas)"),
    # 영어 trade는 '거래'(close of trade, AI trade)로도 쓰여 무역 맥락 표현만
    ("무역·관세", ["관세", "무역", "수출통제", "통상"],
     r"(?i:tariff|trade (war|deal|talks?|deficit|surplus|tensions?|polic|truce|agreement|barrier)|export control)"),
    ("지정학", ["전쟁", "이스라엘", "이란", "우크라이나", "러시아", "중동", "북한", "제재", "휴전"],
     r"(?i:\bwar\b|israel|\biran\b|ukrain|russia|middle east|north korea|sanction|ceasefire|geopolit)"),
]
TOPIC_NAMES = [t[0] for t in TOPICS]
_COMPILED = [(name, kws, re.compile(en)) for name, kws, en in TOPICS]


def tag_topics(title: str, summary: str = "", category: str = "") -> list[str]:
    text = f"{title} {summary or ''}"
    out = [name for name, kws, en in _COMPILED if any(k in text for k in kws) or en.search(text)]
    if category == "정책·기관" and "중앙은행" not in out:  # Fed·ECB·한국은행 보도자료
        out.insert(0, "중앙은행")
    return out


# ── 같은 사건 묶기 ─────────────────────────────────────
_EN_STOP = set("""a an the and or of to in on for at by with from as is are was were be been it its this that
after before over under into amid as says say said new more most than up down out off vs about after will would could
may might its their his her they we you he she not no yes how why what when where who amid""".split())
_BRACKET = re.compile(r"\[[^\]]*\]|\([^)]*\)|<[^>]*>|【[^】]*】")


def title_tokens(title: str) -> set[str]:
    t = _BRACKET.sub(" ", title or "")
    if re.search(r"[가-힣]", t):
        return {w for w in re.findall(r"[가-힣A-Za-z0-9]+", t) if len(w) >= 2}
    return {w for w in re.findall(r"[a-z0-9&]+", t.lower()) if len(w) >= 3 and w not in _EN_STOP}


def _similar(a: set[str], b: set[str]) -> bool:
    """핵심 단어가 3개 이상 겹치고, 짧은 쪽 제목 단어의 60% 이상이 겹치면 같은 사건."""
    if not a or not b:
        return False
    inter = len(a & b)
    return inter >= 3 and inter / min(len(a), len(b)) >= 0.6


def cluster(df: pd.DataFrame, window: pd.Timedelta = pd.Timedelta(hours=36)) -> pd.Series:
    """같은 사건 묶음 번호 (최신순 df 기준, 대표 = 묶음에서 가장 앞 = 가장 최근). 시각이 window 넘게 떨어지면 다른 사건."""
    toks = [title_tokens(t) for t in df["title"]]
    times = list(df["time"])
    parent = list(range(len(df)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(df)):
        for j in range(i):
            if (times[i] is not None and times[j] is not None and pd.notna(times[i]) and pd.notna(times[j])
                    and abs(times[i] - times[j]) > window):
                continue
            if _similar(toks[i], toks[j]):
                parent[find(i)] = find(j)
    return pd.Series([find(i) for i in range(len(df))], index=df.index)


def annotate(df: pd.DataFrame) -> pd.DataFrame:
    """topics(list), group(묶음 번호), group_size, group_sources(대표 외 출처) 열 추가. 입력은 최신순."""
    df = df.reset_index(drop=True).copy()
    df["topics"] = [tag_topics(t, s, c) for t, s, c in zip(df["title"], df["summary"], df["category"])]
    df["group"] = cluster(df)
    sizes = df.groupby("group")["title"].transform("size")
    df["group_size"] = sizes
    srcs = df.groupby("group")["source"].apply(lambda x: list(dict.fromkeys(x)))
    df["group_sources"] = df["group"].map(srcs)
    # 묶음의 주제 = 묶인 기사들 주제의 합 (대표 기사 제목이 짧아 주제가 빠지는 것 방지)
    tops = df.groupby("group")["topics"].apply(lambda x: list(dict.fromkeys(t for lst in x for t in lst)))
    df["group_topics"] = df["group"].map(tops)
    return df
