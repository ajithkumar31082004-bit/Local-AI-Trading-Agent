import pandas as pd
from strategy.orb_ema_volume import evaluate_orb
from config.orb_settings import ORBSettings

def cfg():
    return ORBSettings(ema_period=3,volume_period=3,atr_period=3,session_start="09:00",
        orb_minutes=15,max_ema_crosses=3,max_extension_atr=5.0,stop_buffer=0.1,
        rejection_wick_body_ratio=0.5,ret​​est_tolerance=0.5)

def candles():
    idx=pd.date_range("2026-01-02 09:00",periods=40,freq="5min",tz="Asia/Kolkata")
    close=[100,101,100.5,100.8,101,100.9,100.8,101.2,101.1,101.4,101.3,101.2,101.4,101.5,101.3,101.4,101.6,102.2,102.0,101.8,102.3,102.1,102.4,102.2,102.5,102.4,102.7,102.6,102.8,102.9,103,103.1,103.2,103.3,103.4,103.5,103.6,103.7,103.8,103.9]
    return pd.DataFrame({"Open":[x-.1 for x in close],"High":[x+.2 for x in close],"Low":[x-.2 for x in close],"Close":close,"Volume":[100]*40},index=idx)

def test_empty_input_is_no_trade():
    sig=evaluate_orb(pd.DataFrame(),cfg())
    assert sig.direction=="NO TRADE"
    assert "No candle" in sig.reason

def test_result_always_has_checklist_and_no_llm_dependency():
    sig=evaluate_orb(candles(),cfg())
    assert "orb_set" in sig.checklist
    assert sig.direction in {"LONG","SHORT","NO TRADE"}
    if sig.direction=="NO TRADE": assert sig.entry is None
