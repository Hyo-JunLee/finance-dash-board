"""뉴스 주제 태그와 같은 사건 묶기."""
import pandas as pd

from data.news_topics import annotate, tag_topics


def test_tag_topics_korean_english_and_noise():
    assert "금리·채권" in tag_topics("국고채 금리 급등…3년물 3.6%대")
    assert {"중앙은행", "금리·채권"} <= set(tag_topics("Fed's Powell signals pause as Treasury yields climb"))
    assert "반도체·AI" in tag_topics("Nvidia leads AI rebound") and "반도체·AI" not in tag_topics("Said the chair")
    assert "무역·관세" not in tag_topics("Japan stocks higher at close of trade") and "무역·관세" in tag_topics("US-China trade talks resume")
    assert tag_topics("[인사] 동국제강그룹") == []                     # 시장 주제 없음 → 기본 숨김
    assert tag_topics("Press release: board meeting", category="정책·기관") == ["중앙은행"]


def test_cluster_groups_same_story_only():
    t = pd.Timestamp("2026-11-27 15:00")
    df = pd.DataFrame({
        "time": [t, t - pd.Timedelta(minutes=5), t - pd.Timedelta(minutes=9), t - pd.Timedelta(hours=1)],
        "source": ["연합뉴스", "이데일리", "한국경제", "CNBC"],
        "category": ["국내", "국내", "국내", "해외"],
        "title": ["코스닥 상승세 강해져 5%대 급등…매수 사이드카 발동", "[속보]코스닥 5%대 급등에 매수 사이드카…올해 23번째",
                  "[속보] 코스닥 5%대 급등에 매수 사이드카 발동", "Oil prices fall as OPEC weighs output"],
        "summary": ["", "", "", ""], "link": ["a", "b", "c", "d"]})
    a = annotate(df)
    assert a["group"].iloc[0] == a["group"].iloc[1] == a["group"].iloc[2] != a["group"].iloc[3]
    assert a["group_size"].iloc[0] == 3 and a["group_sources"].iloc[0] == ["연합뉴스", "이데일리", "한국경제"]
