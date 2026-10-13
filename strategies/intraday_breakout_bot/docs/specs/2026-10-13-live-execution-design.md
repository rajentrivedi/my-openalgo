# Live Execution Design - Intraday Breakout Bot

Date: 2026-10-13
Status: proposed (awaiting approval before implementation planning)

## Problem

The deployed bot plans and places entries but never manages them. `executor.on_entry_filled`,
`on_bar`, `on_t1_touched` and `on_exit_filled` are called only from `replay.py` (the
backtest) and never from `bot.main()`. Live, the only thing that ever touches an open
position is the 15:15 `square_off_all`. So a filled entry sits with no protective stop, no
target, and no exit booking.

On top of that, the live order calls in `executor.py` do not match the installed OpenAlgo
SDK (openalgo 2.0.5). Every one of them raises `TypeError` before a request is sent:

| Call site | Current code | SDK requirement (verified) | Failure |
| --- | --- | --- | --- |
| `place_entry` | `placesmartorder(symbol, exchange, product, price_type, quantity, price, trigger_price)` | also needs `action`, `position_size` | missing required arg `action` |
| `on_entry_filled` | `placesmartorder(..., stoploss=, target=)` | no `stoploss`/`target` on smart orders; needs `action`, `position_size` | missing `action`; bracket fields unsupported |
| `on_t1_touched` | `modifyorder(orderid=..., ...)` | `order_id` (not `orderid`), plus `action`, `price`, `disclosed_quantity` | missing / misnamed `order_id` |
| `cancel_stale_entry` | `cancelorder(id, symbol=...)` positional | `order_id` keyword-only | too many positional args |
| `square_off` | `closeposition(symbol=..., ...)` | `closeposition()` is global; takes no symbol | closes ALL positions, not one |

Two of these are worse than a crash because they would silently misbehave if the signature
ever changed to absorb them: `closeposition` takes `**kwargs`, so a per-trade square-off
flattens the whole account; and the `stoploss`/`target` "bracket" on a smart order is not an
OpenAlgo feature (those fields are GTT-only), so the protective stop would never exist.

Nothing has exercised this path: `DRY_RUN` defaults on, and the sandbox only simulates
orders when the account is in `analyze` mode.

## Goal

Make the deployed bot execute and manage trades end to end, faithful to the backtest's
entry / stop / T1-trail / T2 logic, validated in paper (OpenAlgo `analyze` sandbox).

## Non-goals

- Changing strategy logic, levels, sizing, or the `planner`.
- A market-data websocket feed. Fill/exit detection is poll-based, reusing the existing
  30 s `run_once` cadence.
- Replacing the 15:15 square-off.
- Multi-account or multi-broker support.

## Constraints and invariants

- `config.assert_paper_only()` already runs first in `main()` and fails closed unless the
  account mode is exactly `analyze`. The bot therefore cannot send a real order through
  `main()` even with `DRY_RUN=0`. Any new order path MUST only be reachable from
  `main()`/`Executor`, never constructed ad hoc.
- `DRY_RUN` suppresses writes only. Under `DRY_RUN`, order ids are synthetic (`DRY-n`) and
  no broker order exists, so the live monitor MUST be a no-op.
- `executor` stays the only module allowed to place/modify/cancel/close an order. The
  monitor reads broker state and calls `Executor` transitions; it never sends an order.
- One poll per symbol per pass; no new thread (eventlet/gunicorn single-worker safe).

## Design

### 1. Order-call correctness (`executor.py`)

Replace every live order call with the SDK-correct form. Exit side is `SELL` for a `LONG`
and `BUY` for a `SHORT` (call it `exit_action`); the entry side is `action`.

- **`place_entry`** - `placesmartorder(action=plan.side_action, position_size=+qty/-qty,
  symbol, exchange, price_type="SL", trigger_price=round_tick(entry), price=round_tick(limit),
  product)`. `position_size` is the desired net: `+qty` for `LONG`, `-qty` for `SHORT`.
- **`on_entry_filled`** - place **two** orders (a real broker bracket is not available):
  - protective stop: `placeorder(action=exit_action, price_type="SL-M",
    trigger_price=round_tick(stop), quantity=qty, symbol, exchange, product)`
  - target: `placeorder(action=exit_action, price_type="LIMIT",
    price=round_tick(target2), quantity=qty, symbol, exchange, product)`
  - store `stop_order_id` and `target_order_id` separately (the `Trade` model already has
    both fields). Keep `_bracket_levels` validation: if no protective stop is derivable,
    `_reject_bracket` flattens the naked position.
  - Duplicate-safe: if exactly one leg places, keep the placed leg, journal the other as a
    fault, and treat the position as protected only if the stop leg exists. A position with
    no stop leg is `naked` (see reconcile).
- **`on_t1_touched`** - `modifyorder(order_id=trade.stop_order_id, action=exit_action,
  symbol, exchange, price_type="SL-M", product, quantity=qty, price=round_tick(target1),
  trigger_price=round_tick(target1), disclosed_quantity="0")`. Only the stop leg moves; the
  target leg is unchanged. No partial exit (unchanged).
- **`cancel_stale_entry`** - `cancelorder(order_id=trade.entry_order_id)`.
- **per-symbol flatten** - keep the name `Executor.square_off(trade, *, price=None)` but
  change its body to a counter `placeorder(action=exit_action, price_type="MARKET",
  quantity=qty, symbol, exchange, product)` for that ONE symbol. Used by `_reject_bracket`
  and by the square-off of a single trade. It must NOT use `closeposition`.
- **global square-off** - new `Executor.square_off_all()` uses `closeposition()` (correctly
  global) once, at 15:15, then marks every open trade exited. `Bot._square_off_all` calls
  this once instead of looping per trade.

`_order_id` parsing is unchanged (handles `data.orderid` and bare `data`).

### 2. Live monitor (`monitor.py`) - new module

A single function:

```python
def poll(api, executor, trade, *, now) -> None
```

Reads broker state and drives `Executor` transitions. Called from `Bot._manage_open` once
per open trade per pass. Ordered so a fill is never cancelled:

1. **entry_pending** - `orderstatus(order_id=trade.entry_order_id)`:
   - `order_status == "complete"` -> `executor.on_entry_filled(trade, average_price)`.
   - `order_status in {"rejected", "cancelled"}` -> drop the trade (journal `order_rejected`).
   - still open and `is_expired(trade.expires_at, now=now)` -> `executor.cancel_stale_entry`.
2. **entry_filled / t1_hit, not exited** - `quotes(symbol, exchange)` -> `ltp`:
   - not `t1_hit` and the LTP has crossed `target1` (LONG: `ltp >= target1`,
     SHORT: `ltp <= target1`) -> `executor.on_t1_touched(trade)`.
3. **exit detection** - `orderstatus` on `stop_order_id` and `target_order_id`:
   - target complete -> `executor.on_exit_filled(trade, "T2", average_price)`, then cancel
     the stop leg.
   - stop complete -> reason `"STOP"` if not `t1_hit` else `"T1_TRAIL"`,
     `executor.on_exit_filled(...)`, then cancel the target leg.

Read helpers (`_order_status`, `_ltp`) tolerate any malformed/unexpected response by
returning None and logging - a broker hiccup must not abort the pass or, worse, be read as
a fill.

### 3. Wiring (`bot.py`)

- `_manage_open` replaces its direct `cancel_stale_entry` call with a `monitor.poll(...)`
  per open trade (the monitor handles stale-cancel inside the pending branch).
- `_square_off_all` calls `executor.square_off_all()`.
- Monitor is skipped entirely when `config.DRY_RUN` is true.
- Update the `_manage_open` docstring: fill and exit resolution are now driven by this
  wall-clock pass (there is no separate market-data feed), not by a feed.

### 4. Reconcile / repairs

`reconcile_detail` stays detection-only. Its `naked` bucket (`entry_price` set,
`stop_order_id is None`) now also covers "entry filled but the stop leg failed to place".
Repair (re-place the stop leg vs. flatten) stays a caller decision; the monitor re-places a
missing stop leg on the next pass only if `_bracket_levels` still yields a valid stop,
otherwise it flattens.

## Failure handling

- Broker read returns non-success or an unknown shape -> treat as "no information",
  log, retry next pass. Never interpret as a fill.
- Entry order rejected/cancelled at the broker -> drop the trade, journal `order_rejected`.
- Stop leg fails to place while target leg succeeds -> journal a fault; if no valid stop can
  be placed, flatten the position (unprotected exposure is the one unacceptable state).
- Both legs report complete in the same poll -> open item (below); default to booking the
  stop (conservative for R), then reconcile the residual at the next pass / square-off.
- Every user-facing message names the cause and next action, no codes or exception classes.

## Testing

- **Signature guard (new, primary).** A test that reflects the installed `openalgo` client's
  real signatures (`inspect.signature(...).bind`) and asserts every payload `executor` builds
  for `placeorder`/`placesmartorder`/`modifyorder`/`cancelorder`/`orderstatus`/`quotes` binds
  without error. This is the test that would have caught every bug above. It runs without a
  broker.
- **Scenario fake API** (extend `test/test_executor.py`'s `FakeApi`): order lifecycle with
  explicit `orderstatus`/`quotes` control, to drive monitor transitions:
  entry fill -> bracket placed (two ids); T1 touch -> stop leg modified to T1; target fill ->
  exit booked as T2 with target leg cancelled; stop fill pre-T1 -> STOP; post-T1 -> T1_TRAIL;
  rejected entry; stop-leg-fails -> flatten.
- **`test_bot.py`**: `_manage_open` calls the monitor; monitor skipped under `DRY_RUN`;
  `_square_off_all` calls the global flatten once.
- Keep `test_replay.py` green (backtest parity is unchanged).
- Run `python3 -m pytest test/` and `ruff check`.

## Rollout

1. Land with `DRY_RUN` on (default). No behavioural change live.
2. Turn the OpenAlgo sandbox (`analyze`) on, set `OPENALGO_DRY_RUN=0`, run during market
   hours, and confirm in the journal: entry fill, bracket stop+target ids, T1 modify, exit
   reason and R. `assert_paper_only()` guarantees `analyze` mode.
3. Only after the sandbox run matches the backtest's buckets is any further live use
   considered (out of scope here).

## Open items

- Same-poll double-complete of both exit legs: book stop vs book T2. Needs a decision;
  spec default is stop (conservative), with reconcile handling the residual.
- LTP-based T1 detection can miss a T1 that a 5-minute bar high would have tagged (poll
  every 30 s). Accepted for paper; documented as an anticipated divergence from
  `simulate_trade`.
- Whether `placeorder`/`placesmartorder` return `orderid` at `data.orderid` or bare `data` in
  the installed SDK version - handled defensively today, confirm during the sandbox run.
