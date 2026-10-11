# test/test_notify.py
import notify


def test_daily_digest_mentions_taken_and_skipped():
    from planner import Plan
    p1 = Plan(symbol="TCS", side="LONG", entry=3500, stop=3480, target1=3520,
              target2=3543, risk_per_share=20, stop_atr=1.0,
              expires_at=None, status="WATCH_BREAKOUT", error=None)
    p2 = Plan(symbol="AXISBANK", status="WATCH_VOLUME", error=None)
    msg = notify.daily_digest({"TCS": p1, "AXISBANK": p2}, ["TCS"], ["AXISBANK"])
    assert "TCS" in msg and "AXISBANK" in msg
    assert "1" in msg  # taken count appears


def test_event_line_includes_symbol_and_kind():
    line = notify.event("ENTRY", symbol="TCS", side="LONG", qty=50, price=3500.0)
    assert "ENTRY" in line and "TCS" in line and "50" in line


def test_send_swallows_exceptions(monkeypatch):
    class Boom:
        def whatsapp(self, **kw):
            raise RuntimeError("not paired")
    monkeypatch.setattr(notify, "client", lambda: Boom())
    assert notify.send("hello") is False


def test_send_reports_failure_when_sdk_returns_error_dict(monkeypatch):
    """The SDK reports failure by *returning* {'status': 'error'}, not raising.

    `openalgo/whatsapp.py` catches Timeout/Connect/HTTP errors and returns an
    error dict, so every failure mode the docstring names (unpaired bridge,
    server down, timeout) arrives here rather than as an exception. A `send`
    that only catches exceptions reports success for all of them.
    """
    class ErrorDict:
        def whatsapp(self, **kw):
            return {
                "status": "error",
                "message": "WhatsApp is not paired",
                "code": 400,
                "error_type": "api_error",
            }
    monkeypatch.setattr(notify, "client", lambda: ErrorDict())
    assert notify.send("hello") is False


def test_send_reports_failure_when_sdk_returns_success_dict(monkeypatch):
    class Ok:
        def whatsapp(self, **kw):
            return {"status": "success", "message": "sent"}
    monkeypatch.setattr(notify, "client", lambda: Ok())
    assert notify.send("hello") is True


def test_send_reports_failure_for_non_dict_response(monkeypatch):
    """A None or non-dict response is not a delivery we can vouch for."""
    class NoneReturn:
        def whatsapp(self, **kw):
            return None
    monkeypatch.setattr(notify, "client", lambda: NoneReturn())
    assert notify.send("hello") is False


def test_send_accepts_plain_string_response(monkeypatch):
    """A non-dict, non-None truthy response is not an error report."""
    class Text:
        def whatsapp(self, **kw):
            return "OK"
    monkeypatch.setattr(notify, "client", lambda: Text())
    assert notify.send("hello") is True


def test_send_does_not_swallow_base_exception(monkeypatch):
    """Ctrl-C must still stop a live session."""
    class Interrupts:
        def whatsapp(self, **kw):
            raise KeyboardInterrupt
    monkeypatch.setattr(notify, "client", lambda: Interrupts())
    try:
        notify.send("hello")
    except KeyboardInterrupt:
        return
    raise AssertionError("KeyboardInterrupt must propagate")


def test_event_field_order_is_deterministic():
    """Two call sites passing the same fields in different orders agree."""
    a = notify.event("ENTRY", symbol="TCS", side="LONG", qty=50)
    b = notify.event("ENTRY", qty=50, side="LONG", symbol="TCS")
    assert a == b
    # and the order is the documented canonical one, not merely *some* order
    assert a == "ENTRY symbol=TCS side=LONG qty=50"


def test_digest_header_counts_match_the_lines_below(tmp_path):
    """Header totals must agree with the per-symbol lines under them."""
    from planner import Plan
    plans = {
        "TCS": Plan(symbol="TCS", side="LONG", entry=3500, stop=3480,
                    status="WATCH_BREAKOUT", error=None),
        "INFY": Plan(symbol="INFY", side="SHORT", entry=600, stop=610,
                     status="WATCH_BREAKOUT", error=None),
    }
    # 'TCS' listed twice: dedup must apply to the counts AND the lines
    msg = notify.daily_digest(plans, ["TCS", "TCS"], ["INFY"])
    assert "taken: 1" in msg and "skipped: 1" in msg
    assert msg.count("TCS") == 1
