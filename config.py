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
TTL_TELEGRAM = 60 * 10
SUMMARY_FILE = CACHE_DIR / "telegram_summary.json"  # 화면용 최신 요약 (summarize_telegram.py가 하루 3회 갱신)
