# test/test_risk.py
import risk
from planner import Plan


def plan(**kw):
    base = dict(  # noqa: C408 - verbatim from the task brief
        symbol="TCS", side="LONG", entry=3500.0, stop=3480.0,
                target1=3520.0, target2=3543.0, risk_per_share=20.0,
                stop_atr=1.0, expires_at=None, status="WATCH_BREAKOUT", error=None)
    base.update(kw)
    return Plan(**base)


def test_risk_amount_is_one_percent_of_budget():
    assert risk.risk_amount() == 1000.0


def test_size_floors_to_whole_shares():
    # 1000 / 20 = 50 exactly
    assert risk.size_for(plan()) == 50
    # 1000 / 30 = 33.33 -> 33
    assert risk.size_for(plan(risk_per_share=30.0)) == 33


def test_size_is_zero_when_stop_too_wide():
    assert risk.size_for(plan(risk_per_share=1500.0)) == 0


def test_daily_loss_halt_at_minus_3000():
    assert risk.check_daily_loss(-2999.0) is False
    assert risk.check_daily_loss(-3000.0) is True
    assert risk.check_daily_loss(-5000.0) is True


def test_concurrency_cap_at_four():
    assert risk.check_concurrency(3) is False
    assert risk.check_concurrency(4) is True


class FakeApi:
    def __init__(self, total, cash):
        self.total, self.cash, self.seen = total, cash, None

    def margin(self, *, positions, **kw):
        self.seen = positions
        return {"status": "success",
                "data": {"total_margin_required": self.total,
                         "span_margin": self.total, "exposure_margin": 0}}


def test_margin_check_passes_when_affordable():
    api = FakeApi(total=25012, cash=100000)
    ok, _ = risk.check_margin({"symbol": "RELIANCE", "quantity": 100}, [], api)
    assert ok is True
    assert api.seen[0]["quantity"] == "100"      # string, per SDK quirk
    assert api.seen[0]["pricetype"] == "MARKET"


def test_margin_check_blocks_when_expensive():
    api = FakeApi(total=400000, cash=100000)
    ok, detail = risk.check_margin({"symbol": "RELIANCE", "quantity": 100}, [], api)
    assert ok is False
    assert "margin" in detail.lower()


def test_evaluate_blocks_on_watch_volume():
    d = risk.evaluate(plan(status="WATCH_VOLUME"), 0, 0.0, [], FakeApi(1, 1e9))
    assert d.approved is False
    assert d.reason == "WATCH_VOLUME"


def test_evaluate_blocks_on_stop_too_wide():
    p = plan(stop_atr=3.5)
    d = risk.evaluate(p, 0, 0.0, [], FakeApi(1, 1e9))
    assert d.approved is False
    assert d.reason == "stop_too_wide"


def test_evaluate_blocks_on_daily_loss_before_sizing():
    d = risk.evaluate(plan(), 0, -3000.0, [], FakeApi(1, 1e9))
    assert d.approved is False
    assert d.reason == "daily_loss_halt"


def test_evaluate_blocks_on_concurrency():
    d = risk.evaluate(plan(), 4, 0.0, [], FakeApi(1, 1e9))
    assert d.approved is False
    assert d.reason == "concurrency_cap"


def test_evaluate_blocks_when_quantity_is_zero():
    d = risk.evaluate(plan(risk_per_share=1500.0), 0, 0.0, [], FakeApi(1, 1e9))
    assert d.approved is False
    assert d.reason == "stop_too_wide"
    assert d.quantity == 0


def test_evaluate_approves_good_trade():
    d = risk.evaluate(plan(), 0, 0.0, [], FakeApi(25012, 100000))
    assert d.approved is True
    assert d.quantity == 50
    assert d.reason is None
