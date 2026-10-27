from datetime import datetime, timezone, timedelta
from risk.position_sizing import size_position, DailyRiskState
from config.orb_settings import ORBSettings

def test_minimum_lot_exceeding_risk_budget_is_rejected():
    cfg=ORBSettings(risk_pct=0.001,contract_size_xaut=0.001,fee_pct_per_side=0.01,
        spread_pct_round_trip=0.02,slippage_pct_round_trip=0.02)
    result=size_position(3000,2990,100,cfg)
    assert not result.approved
    assert result.lots==0

def test_daily_trade_limit_persists(tmp_path):
    cfg=ORBSettings(max_trades_per_day=1,daily_loss_limit_pct=1.5,cooldown_after_loss_minutes=60)
    state=DailyRiskState(tmp_path/"state.json",cfg)
    now=datetime(2026,10,27,5,0,tzinfo=timezone.utc)
    ok,_,_=state.can_trade(now); assert ok
    state.record_trade(5,1000,now)
    ok,reason,_=state.can_trade(now+timedelta(minutes=1))
    assert not ok and "Maximum trades" in reason
