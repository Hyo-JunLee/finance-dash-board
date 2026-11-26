"""미국 시장 폭: 이동평균 위 종목 비율."""
import numpy as np
import pandas as pd

from data.global_markets import breadth_history


def test_breadth_counts_stocks_above_ma():
    idx = pd.bdate_range("2026-01-01", periods=60)
    up = pd.Series(np.arange(60, dtype=float) + 100, index=idx)      # 꾸준히 상승 → 이평 위
    down = pd.Series(200 - np.arange(60, dtype=float), index=idx)    # 꾸준히 하락 → 이평 아래
    b = breadth_history(pd.DataFrame({"A": up, "B": down, "C": up}), windows=(20,))
    assert len(b) == 60 - 19
    assert abs(b["20일선 위"].iloc[-1] - 200 / 3) < 1e-9
