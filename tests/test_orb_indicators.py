import pandas as pd
import pytest
from indicators.orb_indicators import ema, prior_volume_average, ema_cross_count

def test_ema20_matches_expected_trend():
    s=pd.Series([float(x) for x in range(1,30)])
    out=ema(s,20)
    assert out.iloc[:19].isna().all()
    assert out.iloc[-1] > out.iloc[-2]

def test_volume_average_excludes_current_bar():
    v=pd.Series([10.0]*20+[1000.0])
    out=prior_volume_average(v,20)
    assert out.iloc[19] != out.iloc[19]  # not enough prior bars
    assert out.iloc[20] == pytest.approx(10.0)

def test_ema_cross_count():
    close=pd.Series([1,2,3,2,1,2,3])
    ema_line=pd.Series([2]*7)
    assert ema_cross_count(close,ema_line,10)==3
