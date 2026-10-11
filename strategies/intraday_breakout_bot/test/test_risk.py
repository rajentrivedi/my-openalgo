# test/test_risk.py
import pytest
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



# --- Fix round 1 -------------------------------------------------------------
#
# 1. CRITICAL: a non-finite (NaN/inf) cash figure passed the `isinstance` guard,
#    then `required > nan` evaluated False, so the gate returned True. NaN is
#    the canonical *undeterminable* figure and must fail closed like any other.
# 2. IMPORTANT: the documented gate order was unpinned -- every test above
#    exercises exactly one gate, so reordering the middle gates stayed green.
# 3. IMPORTANT: only `api.seen[0]` (the proposed row) was asserted for quantity
#    coercion; a regression coercing just row 0 would have shipped green.


class CountingApi(FakeApi):
    """FakeApi that records how many times `margin()` was actually called."""

    def __init__(self, total=25012, cash=100000):
        super().__init__(total, cash)
        self.calls = 0

    def margin(self, *, positions, **kw):
        self.calls += 1
        return super().margin(positions=positions, **kw)


NAN = float("nan")


class NanCashApi:
    """Margin succeeds, but available cash is NaN -- i.e. undeterminable."""

    def __init__(self, cash=NAN):
        self.cash = cash

    def margin(self, *, positions, **kw):
        return {"status": "success",
                "data": {"total_margin_required": 400000,
                         "span_margin": 400000, "exposure_margin": 0}}


def test_nan_cash_in_margin_response_blocks():
    ok, detail = risk.check_margin({"symbol": "A", "quantity": 100}, [], NanCashApi())
    assert ok is False
    assert "cash" in detail.lower()


def test_nan_cash_from_funds_blocks():
    class NanFunds(NanCashApi):
        def funds(self):
            return {"status": "success", "data": {"availablecash": NAN}}

    ok, _ = risk.check_margin({"symbol": "A", "quantity": 100}, [], NanFunds())
    assert ok is False


def test_infinite_cash_blocks():
    ok, _ = risk.check_margin({"symbol": "A", "quantity": 100}, [],
                              NanCashApi(cash=float("inf")))
    assert ok is False


def test_evaluate_does_not_approve_on_nan_cash():
    d = risk.evaluate(plan(), 0, 0.0, [], NanCashApi())
    assert d.approved is False
    assert d.reason == "margin_blocked"


def test_bool_cash_is_not_a_cash_figure():
    """`isinstance(True, int)` is True, so a bool must be rejected explicitly.

    Required margin is 0.5: if the bool were accepted as 1.00 this would
    approve, so the test genuinely discriminates rather than blocking for an
    unrelated reason.
    """
    class BoolCash:
        def margin(self, *, positions, **kw):
            return {"status": "success",
                    "data": {"total_margin_required": 0.5, "availablecash": True}}

        @property
        def cash(self):
            return True

    ok, _ = risk.check_margin({"symbol": "A", "quantity": 100}, [], BoolCash())
    assert ok is False


def test_negative_total_margin_required_is_rejected():
    class NegativeRequired:
        cash = 1e9

        def margin(self, *, positions, **kw):
            return {"status": "success",
                    "data": {"total_margin_required": -500}}

    ok, _ = risk.check_margin({"symbol": "A", "quantity": 1}, [], NegativeRequired())
    assert ok is False


def test_size_for_non_numeric_risk_per_share_returns_zero():
    """The guardrail path must return a value, never raise."""
    assert risk.size_for(plan(risk_per_share="20")) == 0
    assert risk.size_for(plan(risk_per_share=object())) == 0
    assert risk.size_for(plan(risk_per_share=float("nan"))) == 0


def test_every_payload_row_has_string_quantity():
    """A regression coercing only the proposed row must not ship green."""
    api = CountingApi()
    open_positions = [
        {"symbol": "TCS", "quantity": 30},
        {"symbol": "INFY", "quantity": -50},
        {"symbol": "SBIN", "quantity": "75"},
        {"symbol": "BHARTIARTL", "quantity": 10.0},
    ]
    ok, _ = risk.check_margin({"symbol": "RELIANCE", "quantity": 100},
                              open_positions, api)
    assert ok is True
    assert len(api.seen) == 5                      # proposed + 4 open
    assert all(isinstance(row["quantity"], str) for row in api.seen)
    assert all(row["pricetype"] == "MARKET" for row in api.seen)
    assert [row["quantity"] for row in api.seen] == ["100", "30", "-50", "75", "10.0"]


def test_malformed_open_position_without_quantity_is_dropped():
    """Sending a fabricated "0" row would inflate broker-side position count."""
    api = CountingApi()
    ok, _ = risk.check_margin({"symbol": "RELIANCE", "quantity": 100},
                              [{"symbol": "TCS"}, {"symbol": "INFY", "quantity": 30}],
                              api)
    assert ok is True
    assert [row["symbol"] for row in api.seen] == ["RELIANCE", "INFY"]


# --- Gate order --------------------------------------------------------------
#
# Peels one gate at a time off an all-gates-violating plan. The reported reason
# must be the earliest still-violated gate in the documented order:
#   error -> WATCH_VOLUME -> stop_atr -> daily loss -> concurrency -> size 0 -> margin

#: Documented gate order, in evaluation sequence.
GATE_ORDER = ["error", "watch_volume", "stop_atr", "daily_loss",
              "concurrency", "size_zero", "margin"]


def _evaluate_violating(violated, api=None):
    """Build a plan/session violating exactly `violated`, then evaluate it."""
    api = api if api is not None else CountingApi()
    p = plan()
    open_count, day_pnl = 0, 0.0

    if "error" in violated:
        p.error = "history_error: boom"
    if "watch_volume" in violated:
        p.status = "WATCH_VOLUME"
    if "stop_atr" in violated:
        p.stop_atr = 3.5
    if "size_zero" in violated:
        p.risk_per_share = 1500.0
    if "daily_loss" in violated:
        day_pnl = -3000.0
    if "concurrency" in violated:
        open_count = 4
    if "margin" in violated:
        api = CountingApi(total=400000, cash=100000)

    return risk.evaluate(p, open_count, day_pnl, [], api), api


@pytest.mark.parametrize(
    "violated,expected_reason",
    [
        (GATE_ORDER, "history_error: boom"),          # error wins over all
        (GATE_ORDER[1:], "WATCH_VOLUME"),
        (GATE_ORDER[2:], "stop_too_wide"),
        (GATE_ORDER[3:], "daily_loss_halt"),
        (GATE_ORDER[4:], "concurrency_cap"),
        (GATE_ORDER[5:], "stop_too_wide"),           # zero size, margin still clean
        (GATE_ORDER[6:], "margin_blocked"),
        ([], None),
    ],
)
def test_gate_order_reports_first_violation(violated, expected_reason):
    violated = set(violated)
    decision, api = _evaluate_violating(violated)
    assert decision.reason == expected_reason
    assert decision.approved is (expected_reason is None)
    # Every gate except the last is cheaper than an API call, so an early
    # block must never spend one: margin() runs only when nothing before it
    # is violated.
    should_call = violated <= {"margin"}
    assert api.calls == (1 if should_call else 0), (
        f"gate {expected_reason!r} called margin() {api.calls} time(s)"
    )


def test_earliest_gate_wins_over_later_ones_pairwise():
    """Every earlier gate must outrank every later one, not just in sequence."""
    for i, earlier in enumerate(GATE_ORDER[:-1]):
        for later in GATE_ORDER[i + 1:]:
            decision, _ = _evaluate_violating({earlier, later})
            expected = {"error": "history_error: boom", "watch_volume": "WATCH_VOLUME",
                        "stop_atr": "stop_too_wide", "daily_loss": "daily_loss_halt",
                        "concurrency": "concurrency_cap", "size_zero": "stop_too_wide",
                        "margin": "margin_blocked"}[earlier]
            assert decision.reason == expected, f"{earlier} lost to {later}"


def test_margin_is_the_only_gate_that_calls_the_api():
    decision, api = _evaluate_violating({"margin"})
    assert decision.reason == "margin_blocked"
    assert api.calls == 1



# --- Fix round 2 -------------------------------------------------------------
#
# The `available_cash` override parameter was consumed raw, bypassing the
# `_finite_number` guard applied to the other three cash sources. A caller
# passing `available_cash=float('nan')` got the same fail-open that was closed
# for the internal sources one round earlier. Same class of bug, one line away
# through the public signature, so it has to be routed through the same guard.


class ExpensiveApi:
    def margin(self, *, positions, **kw):
        return {"status": "success",
                "data": {"total_margin_required": 400000,
                         "span_margin": 400000, "exposure_margin": 0}}


class FallbackApi:
    """No cash in the margin response, but `funds()` can answer 100,000."""

    def funds(self):
        return {"status": "success", "data": {"availablecash": 100000.0}}

    def margin(self, *, positions, **kw):
        return {"status": "success", "data": {"total_margin_required": 400000}}


class FallbackAffordableApi(FallbackApi):
    """Same cash source, but a requirement the fallback cash actually covers."""

    def margin(self, *, positions, **kw):
        return {"status": "success", "data": {"total_margin_required": 25012}}


def test_nan_cash_override_blocks():
    ok, detail = risk.check_margin({"symbol": "A", "quantity": 100}, [],
                                   ExpensiveApi(), available_cash=float("nan"))
    assert ok is False
    assert "cash" in detail.lower()


def test_infinite_cash_override_blocks():
    ok, detail = risk.check_margin({"symbol": "A", "quantity": 100}, [],
                                   ExpensiveApi(), available_cash=float("inf"))
    assert ok is False
    assert "cash" in detail.lower()


def test_negative_cash_override_is_unusable_not_merely_blocking():
    """A negative balance is not a cash figure, so it falls through.

    Discriminating: `FallbackAffordableApi` could approve on 100,000 cash, and
    the override of -1.0 would have blocked via comparison. Approving proves
    the override was discarded as unusable rather than merely compared.
    """
    ok, _ = risk.check_margin({"symbol": "A", "quantity": 100}, [], FallbackAffordableApi(),
                              available_cash=-1.0)
    assert ok is True


def test_bool_cash_override_is_not_a_cash_figure():
    """`isinstance(True, int)` is True, so a bool must be rejected explicitly."""
    ok, _ = risk.check_margin({"symbol": "A", "quantity": 100}, [], FallbackAffordableApi(),
                              available_cash=True)
    assert ok is True


def test_nan_override_falls_back_and_uses_broker_cash():
    """An unusable override must not block when the broker can still answer.

    Discriminating: `FallbackApi` needs 400,000 against 100,000 available, so
    blocking proves the NaN override was discarded and the broker figure
    actually consulted. With the raw override, `required > nan` is False and
    this would have approved.
    """
    ok, _ = risk.check_margin({"symbol": "A", "quantity": 100}, [], FallbackApi(),
                              available_cash=float("nan"))
    assert ok is False


def test_nan_override_falls_back_successfully_rather_than_blocking():
    """...and when the fallback cash does cover it, the trade still proceeds."""
    ok, _ = risk.check_margin({"symbol": "A", "quantity": 100}, [], FallbackAffordableApi(),
                              available_cash=float("nan"))
    assert ok is True


def test_finite_cash_override_still_short_circuits():
    """The override's whole purpose: it must win and skip the round trip."""
    class NoFundsAllowed:
        def funds(self):
            raise AssertionError("override should have short-circuited funds()")

        def margin(self, *, positions, **kw):
            return {"status": "success",
                    "data": {"total_margin_required": 25012, "availablecash": 1.0}}

    ok, _ = risk.check_margin({"symbol": "A", "quantity": 100}, [], NoFundsAllowed(),
                              available_cash=1e9)
    assert ok is True


def test_zero_cash_override_blocks_even_though_fallback_could_approve():
    """0.0 is finite and is a real figure -- it must win, not be discarded.

    Discriminating: `fallback` could answer 100,000 and approve, so blocking
    proves the explicit zero was actually used.
    """
    ok, _ = risk.check_margin({"symbol": "A", "quantity": 100}, [], FallbackApi(),
                              available_cash=0.0)
    assert ok is False


def test_evaluate_is_unaffected_by_the_override():
    """`evaluate` never passes the override; this pins that it stays that way."""
    d = risk.evaluate(plan(), 0, 0.0, [], ExpensiveApi())
    assert d.approved is False
    assert d.reason == "margin_blocked"


def test_proposed_position_without_quantity_is_rejected():
    """A proposed row with no quantity cannot be sent as a real position."""
    ok, detail = risk.check_margin({"symbol": "RELIANCE"}, [], CountingApi())
    assert ok is False
    assert "quantity" in detail.lower()


def test_proposed_explicit_zero_quantity_is_still_sent():
    """0 is an explicit, meaningful quantity -- distinct from absent."""
    api = CountingApi()
    ok, _ = risk.check_margin({"symbol": "RELIANCE", "quantity": 0}, [], api)
    assert ok is True
    assert api.seen[0]["quantity"] == "0"

