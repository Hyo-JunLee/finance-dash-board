"""대시보드 전역 설정: API 키, 캐시 TTL, 추적 지표 목록."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

load_dotenv(BASE_DIR / ".env")


def _setting(name: str, default: str = "") -> str:
    """로컬은 .env, 웹 배포(Streamlit Community Cloud)는 Secrets에서 읽는다."""
    if os.getenv(name):
        return os.environ[name]
    try:
        import streamlit as st
        return str(st.secrets.get(name, default))
    except Exception:  # secrets.toml이 없으면 예외
        return default


FRED_API_KEY = _setting("FRED_API_KEY")
KRX_API_KEY = _setting("KRX_API_KEY")
# 공개 배포용: 개인 화면(내 포트폴리오)과 맥 전용 화면(데이터 상태)을 숨기고, 텔레그램 원문 전문 대신 앞부분만 보여준다
PUBLIC_MODE = _setting("PUBLIC_MODE", "0") == "1"

# 캐시 유지 시간(초)
# 매크로: FRED는 공식 발표 당일 반영되므로 발표 직후 빠르게 보이도록 15분
TTL_PRICE = 60 * 5
TTL_MACRO = 60 * 15
TTL_NEWS = 60 * 10


# 10년물 교차 판정(채권 화면) 기본 임계값 (bp). 화면의 '판정 설정'에서 바꿀 수 있음
ATTR_NOISE_BP = 3.0          # 이보다 작은 성분 변화는 '유의미한 변화 아님'
ATTR_TP_MODEL_GAP_BP = 5.0   # ACM vs Kim-Wright 텀프리미엄 변화 차이가 이보다 크면 해석 신뢰도 경고
# 유동성 왜곡 경고 (TIPS 유동성 프리미엄): 선택 기간 중 하나라도 넘으면 경고
# 과거 사례: 2020-03 MOVE +95%·HY +500bp 이상, 2023-03 MOVE +37%·170, 2025-04 MOVE +33%·HY +90bp
ATTR_HY_WIDEN_BP = 75.0      # HY OAS 확대폭
ATTR_HY_LEVEL = 6.0          # HY OAS 수준 (%)
ATTR_MOVE_RISE_PCT = 30.0    # MOVE 상승률 (%)
ATTR_MOVE_LEVEL = 150.0      # MOVE 수준

# 수급·포지션 (채권 › 곡선 분해): 스트레스 지표 |z| ≥ 임계값이 '높음 개수' 이상이면 높음, 1개 이상이면 주의
FLOW_Z_THRESHOLD = 2.0
FLOW_STRESS_COUNT = 2
FLOW_Z_WINDOW_DAYS = 252     # 롤링 z 창 (영업일, 주간 데이터는 /5 → 52주)
FLOW_CORR_WINDOW = 60        # 주식·채권 롤링 상관 (영업일)
# CFTC 국채 선물 계약 코드 → (이름, 계약당 DV01 근사 $/bp). 10년물 환산에 쓴다.
# CTD(인도 최저가 채권)별 DV01은 무료로 구할 수 없어 금리 4~5% 기준 고정 근사치
TSY_FUTURES = {
    "042601": ("2년", 38), "044601": ("5년", 43), "043602": ("10년", 63),
    "043607": ("울트라10년", 88), "020601": ("본드", 120), "020604": ("울트라본드", 195),
}

# 리서치 코멘트 요약 대상 (텔레그램 공개 채널, 웹 미리보기 t.me/s/<채널>) — {채널 username: 표시 이름}
# 2026-11-18 선별: 한두 줄 단신(삼성 매크로), 전달 글만 있는 채널(하나 매크로), 영어·이미지 위주(Macro Trader),
# 하루 30개 넘는 실시간 단신(카이에)은 요약을 흐려서 제외
TELEGRAM_CHANNELS = {
    "yieldnspread": "YIELD & SPREAD",
    "miraebond": "미래에셋 채권",
    "deandatbond": "하나 해외채권",
    "lssec_macro": "LS 매크로",
    "kisthemacro": "한투 매크로",
    "lim_econ": "한화 경제",
}
# 바이사이드 관점 요약 대상 — 사건 설명보다 '무엇이 반영됐고 무엇이 중요한가'를 다루는 채널 (로컬 전용)
BUYSIDE_CHANNELS = {
    "harveyspecterMike": "Harvey's Macro Story",
    "cahier_de_market": "카이에 de market",
    "kkkontemp": "KK Kontemporaries",
    "MacroAllocation": "Macro Trader",
    "The_MariTimes": "Polaristimes",
}
TTL_TELEGRAM = 60 * 10
# 화면용 최신 AI 요약 (summarize.py가 하루 3회 갱신)
SUMMARY_FILE = CACHE_DIR / "telegram_summary.json"
NEWS_SUMMARY_FILE = CACHE_DIR / "news_summary.json"
BUYSIDE_SUMMARY_FILE = CACHE_DIR / "buyside_summary.json"
INSIGHT_FILE = CACHE_DIR / "morning_insight.json"  # 아침 08:30 실행에서만 갱신
