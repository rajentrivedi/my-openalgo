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
