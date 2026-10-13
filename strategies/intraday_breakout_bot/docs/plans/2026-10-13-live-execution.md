# Live Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deployed intraday breakout bot execute and manage trades end to end (entry fill -> two-leg bracket -> T1 trail -> exit), faithful to the backtest and validated in the OpenAlgo `analyze` sandbox.

**Architecture:** Fix the executor's live order calls to the real openalgo SDK, replace the unsupported single smart-order "bracket" with two `placeorder` legs (an SL-M stop and a LIMIT target), add a poll-based `monitor.py` that reads broker order/quote state and drives the executor's existing transitions, and wire it into the bot's 30 s `_manage_open` pass. No new threads, no new dependencies.

**Tech Stack:** Python 3, openalgo SDK (2.0.5), pytest, ruff.

**Spec:** `strategies/intraday_breakout_bot/docs/specs/2026-10-13-live-execution-design.md`

## Global Constraints

- **Paper-only by construction.** `config.assert_paper_only()` runs first in `main()` and fails closed unless the account mode is exactly `"analyze"`. No new order path may be reachable outside `main()`/`Executor`.
- **`DRY_RUN` suppresses writes only.** Under `config.DRY_RUN`, order ids are synthetic (`DRY-n`) and no broker order exists; the monitor MUST be a no-op.
- **`executor` is the only module allowed to place/modify/cancel/close an order.** `monitor` reads broker state and calls `Executor` methods; it never sends an order directly.
- **No new thread.** Polling runs inside the existing `run_once` -> `_manage_open` pass (`POLL_SECONDS = 30`, eventlet/gunicorn single-worker safe).
- **Trader-facing copy.** Every user-facing message names the cause and the next action; no status codes, exception classes, protocol terms, or endpoints. `logger.exception()` keeps technical detail in `log/errors.jsonl`.
- **No icons or emojis** in source, comments, logs, tests, or docs.
- **Style:** ruff, line-length 100, Google-style docstrings. Run `python3 -m pytest test/ -q` and `python3 -m ruff check` (or `ruff check`) from `strategies/intraday_breakout_bot/`; both must be clean.
- **Reasons are a closed vocabulary.** `journal.REASONS` already contains `order_rejected`, `STOP`, `T1_TRAIL`, `T2`, `EXPIRY`, `SQUAREOFF`; do not add tokens.

## Review Focus

The five inputs/conditions the spec implies but no task's happy-path test exercises. Each is pinned by a test added in the owning task.

1. **A malformed or non-success `orderstatus` / `quotes` response** must be read as "no information" and retried, never as a fill or a touch. (Task 2)
2. **An entry order the broker rejects or cancels** must drop the trade and journal it, not leave it pending forever. (Task 2)
3. **The stop leg failing to place after a fill** must flatten the position, never leave it unprotected. (Task 1)
4. **Both exit legs completing** must book exactly one exit and cancel the sibling, never double-book. (Task 2)
5. **T1 at the boundary** (LTP exactly equal to `target1`) counts as touched, and T1 is never re-fired once `t1_hit`. (Task 2)

---

### Task 1: SDK-correct order calls in the executor

**Files:**
- Modify: `strategies/intraday_breakout_bot/executor.py`
- Modify: `strategies/intraday_breakout_bot/replay.py` (the `FakeApi` double only)
- Test: `strategies/intraday_breakout_bot/test/test_executor.py`

**Interfaces:**
- Consumes: the installed `openalgo.api` client methods (confirmed signatures):
  - `placeorder(*, strategy="Python", symbol, action, exchange, price_type="MARKET", product="MIS", quantity=1, **kwargs)`
  - `placesmartorder(*, strategy="Python", symbol, action, exchange, price_type="MARKET", product="MIS", quantity=1, position_size, **kwargs)`
  - `modifyorder(*, order_id, strategy="Python", symbol, action, exchange, price_type="LIMIT", product, quantity, price, disclosed_quantity="0", trigger_price="0", **kwargs)`
  - `cancelorder(*, order_id, strategy="Python", **kwargs)`
  - `closeposition(*, strategy="Python", **kwargs)`
- Produces (used by Task 2):
  - `executor.exit_action(side: str) -> str` returns `"SELL"` for `"LONG"`, `"BUY"` for `"SHORT"`.
  - `Executor.on_entry_filled(trade, fill_price)` places two legs and sets `trade.stop_order_id`, `trade.target_order_id`.
  - `Executor.cancel_resting(order_id: str | None) -> None` - cancels a resting leg through the executor (honours `DRY_RUN`).
  - `Executor.square_off_all() -> None` - one global `closeposition()`, then every open trade marked exited and journalled `SQUAREOFF`.
  - `Executor.square_off(trade, *, price=None)` now flattens ONE symbol with a counter MARKET `placeorder`.

- [ ] **Step 1: Make the `FakeApi` double mirror the real SDK signatures**

In `test/test_executor.py`, rewrite `FakeApi` so a wrong call raises `TypeError` in the test itself, and record placement calls. Add a `placed` list for `placeorder`.

```python
class FakeApi:
    def __init__(self):
        self.orders, self.placed = [], []
        self.modified, self.cancelled, self.closed = [], [], []
        self.margin_calls = []

    def placeorder(self, *, strategy="Python", symbol, action, exchange,
                   price_type="MARKET", product="MIS", quantity=1, **kw):
        self.placed.append(dict(symbol=symbol, action=action, exchange=exchange,
                                price_type=price_type, product=product,
                                quantity=quantity, **kw))
        return {"status": "success", "data": {"orderid": f"P{len(self.placed)}"}}

    def placesmartorder(self, *, strategy="Python", symbol, action, exchange,
                        price_type="MARKET", product="MIS", quantity=1,
                        position_size, **kw):
        self.orders.append(dict(symbol=symbol, action=action, exchange=exchange,
                                price_type=price_type, product=product,
                                quantity=quantity, position_size=position_size, **kw))
        return {"status": "success", "data": {"orderid": f"O{len(self.orders)}"}}

    def modifyorder(self, *, order_id, strategy="Python", symbol, action, exchange,
                    price_type="LIMIT", product, quantity, price,
                    disclosed_quantity="0", trigger_price="0", **kw):
        self.modified.append(dict(order_id=order_id, symbol=symbol, action=action,
                                  price_type=price_type, quantity=quantity,
                                  price=price, trigger_price=trigger_price))
        return {"status": "success"}

    def cancelorder(self, *, order_id, strategy="Python", **kw):
        self.cancelled.append(order_id)
        return {"status": "success"}

    def closeposition(self, *, strategy="Python", **kw):
        self.closed.append(kw)
        return {"status": "success"}

    def margin(self, positions=None, pricetype=None):
        self.margin_calls.append(positions or [])
        return {"status": "success",
                "data": {"total_margin_required": 1000.0, "availablecash": 100000.0}}
```

- [ ] **Step 2: Run the executor tests to verify they now fail**

Run: `python3 -m pytest test/test_executor.py -q`
Expected: FAIL — `TypeError` from missing `action`/`position_size` (entry), missing `order_id` (T1), and unexpected positional `order_id` (cancel); the bracket test reads `api.orders[1]` which no longer exists.

- [ ] **Step 3: Add task-level tests pinning the new contract**

Replace `test_on_entry_filled_places_bracket_legs` and update the two neighbours:

```python
def test_place_entry_uses_stop_limit():
    api = FakeApi(); ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    o = api.orders[0]
    assert o["price_type"] == "SL" and o["trigger_price"] == 3500.0
    assert o["quantity"] == 50 and o["action"] == "BUY" and o["position_size"] == 50
    assert trade.state == "entry_pending"

def test_on_entry_filled_places_stop_and_target_legs():
    api = FakeApi(); ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    stop, target = api.placed[0], api.placed[1]
    assert stop["price_type"] == "SL-M" and stop["trigger_price"] == 3480.0
    assert stop["action"] == "SELL" and stop["quantity"] == 50
    assert target["price_type"] == "LIMIT" and target["price"] == 3543.0
    assert target["action"] == "SELL"
    assert trade.stop_order_id and trade.target_order_id
    assert trade.stop_order_id != trade.target_order_id

def test_on_entry_filled_flattens_when_stop_leg_will_not_place():
    api = FakeApi(); ex = Executor(api)
    trade = ex.place_entry(plan(stop=3500.0), Decision(50))  # stop == fill: invalid
    ex.on_entry_filled(trade, 3500.0)
    assert api.placed and api.placed[0]["price_type"] == "MARKET"  # flat, not a stop
    assert trade.state == "exited"

def test_on_t1_touched_moves_only_the_stop_leg():
    api = FakeApi(); ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.on_t1_touched(trade)
    m = api.modified[0]
    assert m["order_id"] == trade.stop_order_id
    assert m["action"] == "SELL" and m["trigger_price"] == 3520.0
    assert trade.t1_hit is True and trade.qty == 50   # no partial exit

def test_cancel_stale_entry_cancels_by_keyword_order_id():
    api = FakeApi(); ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50))
    trade.expires_at = dt.datetime(2020, 1, 1, 10, 0, tzinfo=executor.TZ)
    ex.cancel_stale_entry(trade)
    assert trade.entry_order_id in api.cancelled

def test_square_off_all_calls_closeposition_once():
    api = FakeApi(); ex = Executor(api)
    t1 = ex.place_entry(plan(symbol="TCS"), Decision(50)); ex.on_entry_filled(t1, 3500.0)
    t2 = ex.place_entry(plan(symbol="INFY"), Decision(10)); ex.on_entry_filled(t2, 3500.0)
    ex.square_off_all()
    assert len(api.closed) == 1 and api.closed[0] == {}
    assert ex.open_positions == {}

def test_square_off_one_symbol_uses_a_counter_market_order():
    api = FakeApi(); ex = Executor(api)
    trade = ex.place_entry(plan(), Decision(50)); ex.on_entry_filled(trade, 3500.0)
    ex.square_off(trade)
    assert api.closed == []
    assert api.placed[-1]["price_type"] == "MARKET"
    assert api.placed[-1]["action"] == "SELL" and api.placed[-1]["quantity"] == 50
```

Also add the reflection guard that pins the payloads against the installed SDK (catches a future SDK rename that `**kwargs` would otherwise absorb):

```python
import inspect

def _binds(method, payload):
    inspect.signature(method).bind(None, **payload)

def test_executor_order_payloads_bind_against_the_real_sdk():
    from openalgo import api
    api_client = FakeApi(); ex = Executor(api_client)
    trade = ex.place_entry(plan(), Decision(50))
    ex.on_entry_filled(trade, 3500.0)
    ex.on_t1_touched(trade)
    for kw in api_client.orders: _binds(api.placesmartorder, kw)
    for kw in api_client.placed: _binds(api.placeorder, kw)
    for kw in api_client.modified: _binds(api.modifyorder, kw)
    for oid in api_client.cancelled: _binds(api.cancelorder, {"order_id": oid})
    _binds(api.closeposition, {})
    # The bracket legs must carry ONLY supported keys - no stoploss/target.
    for leg in api_client.placed:
        assert set(leg) <= {"symbol", "action", "exchange", "price_type",
                            "product", "quantity", "price", "trigger_price"}
```

- [ ] **Step 4: Run the new tests to verify they fail for the right reason**

Run: `python3 -m pytest test/test_executor.py -q -k "bracket or stop_limit or square_off or binds"`
Expected: FAIL — `api.placed` is empty (executor still calls `placesmartorder` with `stoploss`/`target`); `square_off_all` does not exist.

- [ ] **Step 5: Fix the executor order calls**

In `executor.py`:

- Add a module-level helper:
  ```python
  def exit_action(side: str) -> str:
      """The order action that closes a position on `side`."""
      return "SELL" if side == "LONG" else "BUY"
  ```
- `place_entry` payload: add `"action"` = `"BUY" if plan.side == "LONG" else "SELL"` and `"position_size"` = `qty if plan.side == "LONG" else -qty`.
- Rewrite `on_entry_filled` to place two `placeorder` legs (no `placesmartorder`, no `stoploss`/`target`):
  ```python
  stop, target = self._bracket_levels(trade, fill_price)
  if stop is None:
      self._reject_bracket(trade, fill_price); return
  side = exit_action(trade.side)
  stop_payload = dict(symbol=trade.symbol, action=side, exchange=config.EXCHANGE,
                      product=config.PRODUCT, price_type="SL-M",
                      quantity=trade.qty, trigger_price=stop)
  target_payload = dict(symbol=trade.symbol, action=side, exchange=config.EXCHANGE,
                        product=config.PRODUCT, price_type="LIMIT",
                        quantity=trade.qty, price=target)
  if config.DRY_RUN:
      trade.stop_order_id = self._next_dry_order_id()
      trade.target_order_id = self._next_dry_order_id()
  else:
      trade.stop_order_id = self._order_id(self.api.placeorder(**stop_payload)) or None
      trade.target_order_id = self._order_id(self.api.placeorder(**target_payload)) or None
      if trade.stop_order_id is None:
          # No protective stop: journal the fault and flatten the naked position.
          self._reject_bracket(trade, fill_price); return
  ```
  (Keep the existing `_record`/`_notify` calls; adjust the note to name both legs.)
- `on_t1_touched` payload -> `modifyorder(order_id=trade.stop_order_id, symbol=..., action=exit_action(trade.side), exchange=config.EXCHANGE, product=config.PRODUCT, price_type="SL-M", quantity=trade.qty, price=trigger, disclosed_quantity="0", trigger_price=trigger)`.
- `cancel_stale_entry` -> `self.api.cancelorder(order_id=trade.entry_order_id)`.
- Replace `square_off`'s body: `placeorder(action=exit_action(trade.side), symbol, exchange=config.EXCHANGE, product=config.PRODUCT, price_type="MARKET", quantity=trade.qty)`; keep the rest (mark exited, `_settle`, journal `SQUAREOFF`, notify).
- Add `cancel_resting`:
  ```python
  def cancel_resting(self, order_id: str | None) -> None:
      """Cancel one resting leg through the executor. Honours DRY_RUN."""
      if not order_id:
          return
      if config.DRY_RUN:
          logger.info("DRY_RUN cancel resting leg (not sent): %s", order_id)
          return
      self.api.cancelorder(order_id=order_id)
  ```
- Add `square_off_all`:
  ```python
  def square_off_all(self) -> None:
      """Flatten every open position at the 15:15 deadline with one global close."""
      trades = list(self.open_positions.values())
      if not trades:
          return
      if config.DRY_RUN:
          logger.info("DRY_RUN global square-off (not sent)")
      else:
          self.api.closeposition()
      for trade in trades:
          self.open_positions.pop(trade.symbol, None)
          trade.state = "exited"
          trade.exit_reason = "SQUAREOFF"
          self._record(trade, stage="squareoff", decision="exited", reason="SQUAREOFF",
                       note="[DRY_RUN]" if config.DRY_RUN else "")
          self._notify("SQUAREOFF", symbol=trade.symbol, side=trade.side,
                       qty=trade.qty, exit_price=trade.exit_price, reason="SQUAREOFF")
  ```
- Add `exit_action` to `__all__`.

- [ ] **Step 6: Update the replay `FakeApi` double**

In `replay.py`, add `placeorder` (keyword-only, mirroring the SDK) and a `placed` list, and mirror the same keyword-only signatures used in Step 1 (`cancelorder(*, order_id, ...)`, etc.) so replay exercises the same call shapes the live path does.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python3 -m pytest test/test_executor.py test/test_replay.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add strategies/intraday_breakout_bot/executor.py \
        strategies/intraday_breakout_bot/replay.py \
        strategies/intraday_breakout_bot/test/test_executor.py
git commit -m "fix: make executor order calls match the openalgo SDK"
```

---

### Task 2: Live monitor

**Files:**
- Create: `strategies/intraday_breakout_bot/monitor.py`
- Test: `strategies/intraday_breakout_bot/test/test_monitor.py`

**Interfaces:**
- Consumes (from Task 1): `Executor.on_entry_filled`, `Executor.on_t1_touched`, `Executor.on_exit_filled(trade, price, reason)`, `Executor.cancel_stale_entry(trade, *, now)`, `Executor.cancel_resting(order_id)`, `executor.exit_action`, `executor.is_expired`, `Trade` fields `entry_order_id`/`stop_order_id`/`target_order_id`/`state`/`t1_hit`/`target1`/`side`/`expires_at`.
- Produces (used by Task 3): `monitor.poll(api, executor, trade, *, now) -> None`; and (added here) `Executor.drop_unfilled(trade, *, reason="order_rejected") -> None` which removes a dead entry order's trade from `open_positions` and journals it.
- [ ] **Step 1: Write the failing monitor tests**

Create `test/test_monitor.py` with a controllable double and the scenarios below. `MonitorApi` records calls and returns scripted `orderstatus`/`quotes` data; it mirrors the real SDK signatures for the write methods (import them from `test_executor` or duplicate the shape).

```python
def test_poll_fills_entry_when_orderstatus_complete(monkeypatch, tmp_path): ...
def test_poll_cancels_stale_pending_entry(monkeypatch, tmp_path): ...
def test_poll_drops_a_rejected_entry(monkeypatch, tmp_path): ...
def test_poll_trails_stop_when_ltp_touches_t1(monkeypatch, tmp_path): ...
def test_poll_t1_fires_once_at_the_boundary(monkeypatch, tmp_path): ...
def test_poll_books_t2_and_cancels_the_stop_when_target_fills(monkeypatch, tmp_path): ...
def test_poll_books_stop_and_cancels_the_target_when_stop_fills(monkeypatch, tmp_path): ...
def test_poll_labels_post_t1_stop_as_t1_trail(monkeypatch, tmp_path): ...
def test_poll_is_a_noop_under_dry_run(monkeypatch, tmp_path): ...
def test_poll_ignores_a_malformed_orderstatus(monkeypatch, tmp_path): ...
def test_poll_ignores_a_malformed_quote(monkeypatch, tmp_path): ...
def test_poll_books_one_exit_when_both_legs_are_complete(monkeypatch, tmp_path): ...
```

Key assertions (values from the spec):
- complete entry `{"order_status": "complete", "average_price": 3500.5}` -> `trade.state == "entry_filled"`, `trade.entry_price == 3500.5`, and two `placeorder` legs exist.
- `order_status in ("rejected", "cancelled")` -> `trade.symbol not in executor.open_positions`.
- LTP `>= target1` (LONG) -> `trade.t1_hit is True` and the stop leg modified to `trigger_price == 3520.0`; a second poll with the same LTP does not modify again.
- target leg complete -> `trade.exit_reason == "T2"`, exactly one `on_exit_filled` (one `exited` journal row), and `cancel_resting(stop_order_id)` was called (recorded cancel of the stop id).
- stop leg complete before T1 -> `"STOP"`; after T1 -> `"T1_TRAIL"`.
- `monkeypatch.setattr(config, "DRY_RUN", True)` -> no calls recorded at all.
- `orderstatus` returning `{"status": "error"}`, `None`, or a non-dict -> no state change, no exception.
- Both legs complete in one poll -> exactly one exit booked and the sibling cancelled.

- [ ] **Step 2: Run the monitor tests to verify they fail**

Run: `python3 -m pytest test/test_monitor.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'monitor'`.

- [ ] **Step 3: Implement `monitor.py`**

```python
"""Poll-based live monitor: reads broker state, drives Executor transitions.

No new thread: `Bot._manage_open` calls `poll` once per open trade per pass.
Reads only - every order action goes through `Executor` (see the module
invariant in `executor.py`).
"""

def poll(api, executor, trade, *, now) -> None:
    """Advance one open `trade` by one poll. Never raises."""
```

Ordered logic:
1. If `config.DRY_RUN`: return immediately.
2. If `trade.state == "entry_pending"`:
   - `data = _order_status(api, trade.entry_order_id)`
   - if `data` and `data.get("order_status") == "complete"`: `executor.on_entry_filled(trade, float(data.get("average_price") or trade.entry))`; return.
   - if `data` and `data.get("order_status") in ("rejected", "cancelled")`: drop the trade (see Step 3 note) and return.
   - if `is_expired(trade.expires_at, now=now)`: `executor.cancel_stale_entry(trade, now=now)`; return.
3. If `trade.state in ("entry_filled", "t1_hit")`:
   - if not `trade.t1_hit`: `ltp = _ltp(api, trade.symbol)`; if crossed (`>= target1` for LONG, `<=` for SHORT): `executor.on_t1_touched(trade)`.
   - target leg: `data = _order_status(api, trade.target_order_id)`; if complete -> `executor.on_exit_filled(trade, _fill_price(data, trade), "T2")`, then `executor.cancel_resting(trade.stop_order_id)`; return.
   - stop leg: `data = _order_status(api, trade.stop_order_id)`; if complete -> reason = `"T1_TRAIL" if trade.t1_hit else "STOP"`, `executor.on_exit_filled(trade, _fill_price(data, trade), reason)`, then `executor.cancel_resting(trade.target_order_id)`; return.

Helpers:

```python
def _order_status(api, order_id) -> dict | None:
    """The order's data dict, or None when there is no usable status."""
    if not order_id:
        return None
    try:
        response = api.orderstatus(order_id=order_id)
    except Exception:
        logger.exception("could not read order status for %s", order_id)
        return None
    if isinstance(response, dict) and response.get("status") == "success":
        data = response.get("data")
        if isinstance(data, dict):
            return data
    return None

def _ltp(api, symbol) -> float | None:
    """Last traded price, or None when there is no usable quote."""
    try:
        response = api.quotes(symbol=symbol, exchange=config.EXCHANGE)
    except Exception:
        logger.exception("could not read a quote for %s", symbol)
        return None
    if isinstance(response, dict) and response.get("status") == "success":
        data = response.get("data")
        if isinstance(data, dict):
            try:
                return float(data["ltp"])
            except (KeyError, TypeError, ValueError):
                return None
    return None

def _fill_price(data: dict, trade) -> float:
    """The fill price for a completed order, falling back to the plan level."""
    try:
        return float(data["average_price"])
    except (KeyError, TypeError, ValueError):
        return float(trade.entry or 0.0)
```

For the rejected/cancelled drop, add a small `Executor` transition `drop_unfilled(trade, *, reason="order_rejected")` (in `executor.py`) that removes the trade from `open_positions`, marks it `cancelled`, and journals `order_rejected`; the monitor calls it. (Do not pop `open_positions` from the monitor - the same "executor owns state" invariant.)

- [ ] **Step 4: Run the monitor tests to verify they pass**

Run: `python3 -m pytest test/test_monitor.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add strategies/intraday_breakout_bot/monitor.py \
        strategies/intraday_breakout_bot/executor.py \
        strategies/intraday_breakout_bot/test/test_monitor.py
git commit -m "feat: add poll-based live monitor for fills, T1 and exits"
```

---

### Task 3: Wire the monitor into the bot

**Files:**
- Modify: `strategies/intraday_breakout_bot/bot.py` (`_manage_open`, `_square_off_all`, docstrings)
- Test: `strategies/intraday_breakout_bot/test/test_bot.py`

**Interfaces:**
- Consumes: `monitor.poll(api, executor, trade, *, now)` (Task 2); `Executor.square_off_all()` (Task 1).
- Produces: a `Bot` whose manage pass resolves fills/T1/exits and whose square-off is one global flatten.

- [ ] **Step 1: Write the failing bot tests**

Add to `test/test_bot.py`:

```python
def test_manage_open_polls_the_monitor(monkeypatch, ...):
    # seed executor.open_positions with one trade; spy on monitor.poll; assert called once
def test_manage_open_skips_the_monitor_when_dry_run(...):
    # with config.DRY_RUN True, poll records no api writes
def test_square_off_all_flattens_everything_once(...):
    # two open trades; assert one closeposition call and open_positions empty
def test_manage_open_survives_one_bad_trade(...):
    # monitor.poll raises for one symbol; the pass still handles the next
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test/test_bot.py -q`
Expected: FAIL — `_manage_open` never calls `monitor.poll`; `_square_off_all` calls `square_off` per trade.

- [ ] **Step 3: Wire it**

- `import monitor` at the top of `bot.py`.
- `_manage_open`: replace the `cancel_stale_entry` call with:
  ```python
  for trade in list(self.executor.open_positions.values()):
      try:
          monitor.poll(self.api, self.executor, trade, now=now)
      except Exception as exc:  # noqa: BLE001 - one bad trade must not abort the pass
          logger.warning("managing %s failed: %s", trade.symbol, exc)
  ```
- `_square_off_all`: replace the loop with `self.executor.square_off_all()`.
- Update the `_manage_open` docstring: fill and exit resolution are now driven by this wall-clock pass (there is no separate market-data feed).

- [ ] **Step 2: Run the bot tests to verify they pass**

Run: `python3 -m pytest test/test_bot.py -q`
Expected: PASS.

- [ ] **Step 3: Run the whole suite and the linter**

Run: `python3 -m pytest test/ -q && python3 -m ruff check strategies/intraday_breakout_bot/`
Expected: all pass; ruff clean. Add any new no-credentials test file to the CI-safe list in `.github/workflows/ci.yml` if that job enumerates files.

- [ ] **Step 4: Commit**

```bash
git add strategies/intraday_breakout_bot/bot.py \
        strategies/intraday_breakout_bot/test/test_bot.py
git commit -m "feat: drive live fill and exit resolution from the manage pass"
```

---

### Task 4: Document the live/paper path

**Files:**
- Modify: `strategies/intraday_breakout_bot/README.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update the README**

Add/adjust a "Live (paper) execution" section stating, in trader-facing language:
- The bot only runs in OpenAlgo `analyze` (sandbox) mode; `main()` refuses to start otherwise.
- `DRY_RUN` (default on) journls intents and sends nothing.
- To rehearse for real: turn the sandbox on, set `OPENALGO_DRY_RUN=0`, run during market hours; the bot places the entry, a protective stop plus a target once filled, moves the stop to the first target when it is reached, and books the exit.
- Order lifecycle: entry stop-limit -> fill -> stop leg + target leg -> T1 trail -> exit (or the 15:15 flatten).

- [ ] **Step 2: Commit**

```bash
git add strategies/intraday_breakout_bot/README.md
git commit -m "docs: describe the live paper-execution lifecycle"
```

---

## Self-Review

**1. Spec coverage:** order-call correctness -> Task 1; two-leg bracket -> Task 1; `monitor.py` poll loop -> Task 2; per-symbol vs global square-off -> Task 1 (`square_off`/`square_off_all`) + Task 3 (wiring); safety gating -> Global Constraints + Task 3 (DRY_RUN no-op, Rejected); failure handling -> Task 1 (stop-leg flatten), Task 2 (rejected entry, malformed reads, double-complete); signature guard test -> Task 1 Step 3; scenario tests -> Tasks 1-3; rollout/doc -> Task 4. Covered.

**2. Step scan:** every step names an exact file, signature, test name, and assertions; no "handle edge cases" steps. Test bodies are given as assertions; implementation bodies are given only where the spec fixes them (payload key sets, `_order_status`/`_ltp`/`_fill_price`).

**3. Type consistency:** `exit_action(side)`, `cancel_resting(order_id)`, `square_off_all()`, `drop_unfilled(trade, *, reason)`, `monitor.poll(api, executor, trade, *, now)` are used with the same names throughout. `on_exit_filled(trade, price, reason)` matches the existing signature.

**4. Review Focus:** malformed reads (Task 2), rejected entry (Task 2), stop-leg failure (Task 1), both-legs-complete (Task 2), T1 boundary/once (Task 2) — all have named tests.

**5. Proportion:** the plan is longer than the spec because it pins exact SDK payloads and test assertions, but it is not a code transcript — bodies appear only where an algorithm or exact key set is a decision.

## Open Items carried from the spec

- Both-legs-complete resolution defaults to booking the STOP (conservative); the test pins "exactly one exit, sibling cancelled". Revisit after the sandbox run if it proves too pessimistic.
- LTP-based T1 detection can miss a bar-tagged T1 (30 s poll); accepted for paper.
- Confirm the installed SDK returns the order id at `data.orderid` vs bare `data` during the sandbox run; `_order_id` already handles both.
