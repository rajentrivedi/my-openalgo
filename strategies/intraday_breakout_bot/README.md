# Intraday Breakout Bot — Operator Runbook

An OpenAlgo **paper-trading** bot for an intraday breakout strategy on a screened
NIFTY-500 universe. It wakes with the market, plans and places bracket entries in
the mid-morning window, manages open positions off the 5-minute bar feed, and
hard-flattens everything at 15:15 IST.

> **Paper-only by design.** The bot refuses to start unless the connected
> OpenAlgo account reports `analyze` (paper) mode, and every order is additionally
> gated by `OPENALGO_DRY_RUN`. See [Known limitations](#8-known-limitations) before
> you even think about real money — the edge is **in-sample and unvalidated**.

---

## 1. Install and configure

Prerequisites: Python 3.12, a running OpenAlgo server in **analyze mode**, and the
API key for that account.

```bash
cd /home/pcadmin/Documents/nse/openalgo/strategies/intraday_breakout_bot

# 1. Dependencies (the repo's own venv/toolchain; openalgo SDK, pandas, etc.)
python3 -m pytest test/ -q          # sanity: the whole suite must be green

# 2. The environment file the systemd units read. NOT committed (gitignored).
printf 'OPENALGO_API_KEY=<paste key here>\nOPENALGO_DRY_RUN=1\n' > .env
chmod 600 .env
```

Confirm the key never gets staged:

```bash
cd /home/pcadmin/Documents/nse/openalgo && git status   # .env must NOT appear
```

Relevant configuration (defaults in `config.py`, overridable by env var):

| Setting | Env var | Default |
|---|---|---|
| Paper/dry-run guard | `OPENALGO_DRY_RUN` | `1` (dry) |
| API key | `OPENALGO_API_KEY` | *(required)* |
| API host | `OPENALGO_API_HOST` | `http://127.0.0.1:5000` |
| Capital budget | — | ₹100,000 |
| Risk per trade | — | 1.0% |
| Max concurrent positions | — | 4 |
| Daily loss limit | — | ₹3,000 |

Install the services (as root):

```bash
cd /home/pcadmin/Documents/nse/openalgo
cp systemd/intraday-breakout-bot.service      /etc/systemd/system/
cp systemd/intraday-breakout-universe.service /etc/systemd/system/
cp systemd/intraday-breakout-universe.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now intraday-breakout-bot.service
systemctl enable --now intraday-breakout-universe.timer
```

> The systemd units assume IST on the host (`09:15`/`15:15` wall clock). If the
> server runs another timezone, set the host timezone or adjust the stage times.

---

## 2. Validate on paper first (do this before anything else)

**Run at least one full session in dry mode and read the journal.** Dry mode
(`OPENALGO_DRY_RUN=1`) exercises the entire pipeline — planning, risk gating,
journaling, notifications — but suppresses every broker write, so no order is
ever sent.

```bash
cd /home/pcadmin/Documents/nse/openalgo/strategies/intraday_breakout_bot
set -a; . ./.env; set +a            # loads OPENALGO_API_KEY, OPENALGO_DRY_RUN=1
python3 -m bot                      # runs the live loop against your paper account
```

After the session (or the next day), confirm two things:

1. The journal has `stage` and decision rows (see [§5](#5-inspect-the-journal)).
2. The broker shows **zero** orders for the day (`orderbook()` empty) — in dry mode
   nothing should have been placed.

The bot exits `0` at square-off, `1` if the account is not in paper mode, and `2`
if startup reconciliation finds a pre-existing position it cannot account for.

> **Deferred check — verify the paper guard's *accept* path once, by hand.** The
> test suite proves `assert_paper_only()` *refuses* (exit 1) a non-paper account,
> but its *accept* path has never run against a real OpenAlgo server in `analyze`
> mode (none was available at build time). Before your first dry-run session,
> confirm `python3 -m bot` gets **past** the paper guard and into the session loop
> against your own analyze-mode server — if it aborts with exit 1, your account is
> not actually in paper mode, regardless of `OPENALGO_DRY_RUN`.

---

## 3. Flip to live paper execution

Once you trust the dry-run journal, let the bot actually place orders **on the
paper account** by turning dry-run off:

```bash
cd /home/pcadmin/Documents/nse/openalgo/strategies/intraday_breakout_bot
sed -i 's/^OPENALGO_DRY_RUN=1/OPENALGO_DRY_RUN=0/' .env
sudo systemctl restart intraday-breakout-bot.service
```

`OPENALGO_DRY_RUN=0` only removes the dry-run suppression; the account must still
be in `analyze` mode or `assert_paper_only()` aborts the start. This project does
**not** support real-money trading — keep the account on paper.

---

## 4. The daily timeline (IST)

| Time | Stage | What happens |
|---|---|---|
| 09:15 | wake | Session opens; startup reconciliation runs; idle until the plan window. |
| 10:15–11:15 | plan | Plans generated for the universe; risk-approved breakout entries placed once. |
| 11:15–15:15 | manage | Open positions managed off the bar feed; stale working entries cancelled. |
| 15:15 | squareoff | **Every** open position is flattened; the bot exits. |

Entries are stop-triggered bracket orders (entry + stop + targets). The `plan`
window is a window, not an instant, so a poll that lands a few minutes late still
runs the plan stage instead of skipping the day. The plan is pinned to the 10:10
five-minute bar (which closes at 10:15), so the stage must never run earlier than
10:15 — planning at 10:10 makes the planner see a bar that does not exist yet and
skip the entire universe for the day.

---

## 5. Inspect the journal

One CSV per trading day under `data/journal/YYYY-MM-DD.csv`, one row per decision.
Columns: `ts, symbol, side, stage, decision, reason, plan_entry, plan_stop,
plan_t1, plan_t2, qty, order_id, entry_price, exit_price, r_multiple, pnl, note`.

```bash
cd /home/pcadmin/Documents/nse/openalgo/strategies/intraday_breakout_bot

ls -1 data/journal/                                   # all session files
column -s, -t < data/journal/$(date +%F).csv | less   # today, human-readable

# Programmatic read:
python3 -c "import journal, datetime; \
import pprint; pprint.pp(journal.read_day(datetime.date.today()))"
```

Service logs (startup reconciliation, aborts, per-stage warnings) go to journald:

```bash
journalctl -u intraday-breakout-bot.service -f
```

---

## 6. Run the universe jobs manually

**Weekly re-screen** (`universe_weekly`) — drops names that no longer clear the
thresholds and promotes the best candidates back to the target size:

```bash
cd /home/pcadmin/Documents/nse/openalgo/strategies/intraday_breakout_bot
set -a; . ./.env; set +a

python3 -m universe_weekly --dry-run     # preview: prints kept/dropped/promoted, writes NOTHING
python3 -m universe_weekly               # commits: rewrites universe.json + data/changes_<date>.csv
```

The scheduled run happens automatically via the timer (Sundays 18:00). Check it:

```bash
systemctl list-timers intraday-breakout-universe.timer
journalctl -u intraday-breakout-universe.service -n 50
```

**Monthly re-backtest** (`universe_monthly`) — re-runs the batch backtest for the
full universe and rebuilds `data/candidates.csv` from the fresh report (only on a
clean run; it never touches `universe.json`). This is heavy and not scheduled by
default; run it by hand:

```bash
python3 -m universe_monthly             # returns the batch exit code
```

---

## 7. Stop and square off

```bash
# Graceful: the bot flattens everything on its own at 15:15 and exits.

# Manual stop (does NOT auto-flatten open positions):
sudo systemctl stop intraday-breakout-bot.service
```

> `systemctl stop` kills the process; it does **not** close open positions. If you
> stop the bot mid-session with live paper positions open, flatten them yourself in
> the OpenAlgo UI (or let the broker's own MIS auto-square-off handle it). To
> prevent the next day's session, also `systemctl disable intraday-breakout-bot.service`.

---

## 8. Known limitations

Carried from the design spec (§12) — read these before trusting any number:

1. **Stop fills assume no gap slippage.** The backtest exits at the stop price even
   when a bar gapped through it (losing trades average −1.32R MAE). Paper MAE will
   be worse; expect live PF **below** the backtest 1.169.
2. **The 64-symbol screen is in-sample.** Thresholds were chosen after seeing the
   full 2-year results for all 494 symbols. The screen is fitted, not validated —
   paper trading is how it gets tested.
3. **Survivorship bias.** The universe is the *current* NIFTY-500 membership;
   delisted names are absent from the backtest entirely.
4. **No out-of-sample split** exists in any figure quoted so far.
5. **Stop-gap realism.** `MAX_STOP_ATR=3.0` rejects wide stops at plan time, but a
   gap at entry can still exceed planned risk. Sizing assumes the stop fills.
6. **EXPIRY exits are assumed fillable at market** at plan expiry; thin names may
   slip.
7. **Service model is one-session-per-process.** The bot exits `0` at square-off
   and `Restart=always` brings it back; between 15:15 and midnight each restart
   re-exits immediately (it is past the square-off deadline), so expect periodic
   benign restarts and startup API calls in that window until the next trading
   day. The two safety aborts (exit 1 paper-guard, exit 2 orphan reconciliation)
   are exempted via `RestartPreventExitStatus=1 2` and are therefore terminal —
   a refusal shows up as a `failed` unit, not a silent retry loop.

**Scope:** paper trading only. Real-money/live trading, options/F&O, multi-leg
strategies, and any UI are explicitly out of scope.
