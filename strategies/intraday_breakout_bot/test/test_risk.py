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


# --- Ruling: the margin gate fails closed on undeterminable cash -------------
#
# `check_margin` is the only mechanism bounding capital deployed beyond the
# budget, so an unknown available-cash figure must block rather than pass. An
# API response-shape change, a transient `funds()` failure, or a missing key
# must not silently turn the safety gate into a no-op.


class NoCashApi:
    """Margin succeeds, but nothing anywhere reports available cash."""

    def margin(self, *, positions, **kw):
        return {"status": "success",
                "data": {"total_margin_required": 25012,
                         "span_margin": 25012, "exposure_margin": 0}}


def test_margin_blocks_when_cash_undeterminable():
    ok, detail = risk.check_margin({"symbol": "RELIANCE", "quantity": 100}, [], NoCashApi())
    assert ok is False
    assert "cash" in detail.lower()


def test_margin_blocks_when_cash_undeterminable_even_if_cheap():
    """A trivially affordable-looking figure is still unverifiable -> block."""
    ok, _ = risk.check_margin({"symbol": "A", "quantity": 1}, [], NoCashApi())
    assert ok is False


def test_evaluate_blocks_with_margin_blocked_when_cash_unknown():
    d = risk.evaluate(plan(), 0, 0.0, [], NoCashApi())
    assert d.approved is False
    assert d.reason == "margin_blocked"
    assert d.quantity == 0


def test_funds_failure_does_not_block_a_known_cash_figure():
    """A funds() that raises must not matter when cash is supplied explicitly."""

    class FundsBoom:
        def funds(self):
            raise RuntimeError("funds endpoint down")

        def margin(self, *, positions, **kw):
            return {"status": "success",
                    "data": {"total_margin_required": 25012,
                             "span_margin": 25012, "exposure_margin": 0}}

    ok, _ = risk.check_margin({"symbol": "A", "quantity": 100}, [], FundsBoom(),
                              available_cash=100000.0)
    assert ok is True


def test_availablecash_in_margin_response_is_used():
    class DataCashApi:
        def margin(self, *, positions, **kw):
            return {"status": "success",
                    "data": {"total_margin_required": 400000, "availablecash": 100000.0}}

        def funds(self):
            raise AssertionError("must not need a second round trip")

    ok, detail = risk.check_margin({"symbol": "A", "quantity": 100}, [], DataCashApi())
    assert ok is False
    assert "margin" in detail.lower()


def test_cash_derivable_from_funds_still_approves():
    class FundsApi:
        def funds(self):
            return {"status": "success", "data": {"availablecash": 100000.0}}

        def margin(self, *, positions, **kw):
            return {"status": "success",
                    "data": {"total_margin_required": 25012,
                             "span_margin": 25012, "exposure_margin": 0}}

    d = risk.evaluate(plan(), 0, 0.0, [], FundsApi())
    assert d.approved is True
    assert d.quantity == 50
    assert d.reason is None


def test_margin_api_error_still_blocks():
    class BoomApi:
        def margin(self, *, positions, **kw):
            raise RuntimeError("broker down")

    ok, detail = risk.check_margin({"symbol": "A", "quantity": 100}, [], BoomApi())
    assert ok is False
    assert "margin" in detail.lower()


def test_margin_bad_status_still_blocks():
    class BadStatusApi:
        def margin(self, *, positions, **kw):
            return {"status": "error", "message": "nope"}

    ok, detail = risk.check_margin({"symbol": "A", "quantity": 100}, [], BadStatusApi())
    assert ok is False
    assert "margin" in detail.lower()

