import pandas as pd
from backtest.orb_backtest import run_backtest

def test_backtest_empty_rejected():
    try: run_backtest(pd.DataFrame())
    except ValueError as e: assert "No candles" in str(e)
    else: assert False

def test_skipped_day_counted_as_zero_trade_day():
    idx=pd.date_range("2026-01-01 09:00",periods=40,freq="5min",tz="Asia/Kolkata")
    frame=pd.DataFrame({"Open":[100.]*40,"High":[100.2]*40,"Low":[99.8]*40,"Close":[100.]*40,"Volume":[100.]*40},index=idx)
    result=run_backtest(frame,10000)
    assert result.days==1
    assert result.skipped_days==1
    assert result.trades==0
