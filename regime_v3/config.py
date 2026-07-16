"""
Regime V3 + 10-Scanner Ensemble + LLM Meta-Ranker — Configuration.

All tuning parameters in one place. No magic numbers in modules.
"""

import os
from dataclasses import dataclass, field
from typing import Any, ClassVar

# ---------------------------------------------------------------------------
# Core constants
# ---------------------------------------------------------------------------

BULL = "BULL"
BEAR = "BEAR"
SIDEWAYS = "SIDEWAYS"
TRANSITION = "TRANSITION"
ALL_REGIMES = (BULL, BEAR, SIDEWAYS, TRANSITION)
NO_TRADE = "NO_TRADE"

# Scanner names
SCANNER_NAMES: ClassVar[list[str]] = [
    "swing_mtf",
    "hilega_milega_mtf",
    "range_breakout_v3",
    "momentum_v6",
    "volume_spike",
    "mean_reversion",
    "nifty_regime",
    "relative_strength",
    "multi_tf_trend",
    "volatility_contraction",
]

# Strategy names
STRATEGY_BULL = "BULL_MOMENTUM"
STRATEGY_BEAR = "BEAR_MOMENTUM"
STRATEGY_SIDEWAYS = "SIDEWAYS_MEAN_REVERSION"
STRATEGY_NONE = "NO_TRADE"


# ---------------------------------------------------------------------------
# Selection methods
# ---------------------------------------------------------------------------

SELECT_RANDOM = "RANDOM"
SELECT_SCANNER_TOP = "SCANNER_TOP"
SELECT_CONSENSUS_TOP = "CONSENSUS_TOP"
SELECT_ENSEMBLE_TOP = "ENSEMBLE_TOP"
SELECT_LLM_TOP = "LLM_TOP"
SELECT_LLM_CONSTRAINED = "LLM_CONSTRAINED"
SELECT_FALLBACK_DETERMINISTIC = "FALLBACK_DETERMINISTIC"


# ---------------------------------------------------------------------------
# Regime engine
# ---------------------------------------------------------------------------

REGIME_CONFIRM_BARS = 1
REGIME_USE_ATR_SEPARATION = True
REGIME_USE_20BAR_RETURN = True


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------

MAX_CANDIDATES = 50
MIN_SCANNERS_FOR_VOTE = 3
MIN_CONSENSUS = 0.3


# ---------------------------------------------------------------------------
# LLM meta-ranker
# ---------------------------------------------------------------------------

LLM_ENABLED = True
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_TEMPERATURE = 0.0
LLM_MAX_TOKENS = 4096
LLM_TIMEOUT_SECONDS = 30
LLM_API_KEY = os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY", ""))
LLM_API_BASE = os.getenv("LLM_API_BASE", os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1"))
LLM_PROMPT_VERSION = "v3.0.0"
LLM_INPUT_SCHEMA_VERSION = "v1.0.0"
LLM_RETRY_COUNT = 2


# ---------------------------------------------------------------------------
# Portfolio / risk
# ---------------------------------------------------------------------------

INITIAL_CAPITAL = 100_000.0
RISK_PER_TRADE_PCT = 0.25
MAX_OPEN_POSITIONS = 5
MAX_TRADES_PER_DAY = 5
MAX_PORTFOLIO_OPEN_RISK_PCT = 1.0
MAX_DAILY_LOSS_PCT = 2.0
USE_DAILY_LOSS_LIMIT = True
SQUARE_OFF_ON_DAILY_LOSS = True
POSITION_VALUE_PCT = 20.0
FORCE_NO_TRADE = False


# ---------------------------------------------------------------------------
# Costs / execution
# ---------------------------------------------------------------------------

ESTIMATED_COST_PCT = 0.03
SLIPPAGE_PCT = 0.02
COST_MODEL = "SIMPLE"  # SIMPLE | DETAILED


# ---------------------------------------------------------------------------
# Data / timeframes
# ---------------------------------------------------------------------------

TIMEFRAME = "5m"
NIFTY_EXCHANGE = "NSE_INDEX"
STOCK_EXCHANGE = "NSE"
LOOKBACK_DAYS = 120
NIFTY_SYMBOL = "NIFTY"
MAX_SYMBOLS = 100
SYMBOLS_FILE = "data/qtyfreeze.csv"

SESSION1_START = "09:20"
SESSION1_END = "11:00"
SESSION2_START = "13:30"
SESSION2_END = "15:00"
ENTRY_CUTOFF = "15:00"
SQUARE_OFF_TIME = "15:15"


# ---------------------------------------------------------------------------
# Strategy defaults
# ---------------------------------------------------------------------------

ATR_LENGTH = 14
VOLUME_LOOKBACK = 5
RELATIVE_VOLUME_MULTIPLIER = 1.5
BULL_MOMENTUM_MIN_RVOL = 1.5
BEAR_MOMENTUM_MIN_RVOL = 1.5
SIDEWAYS_MIN_RVOL = 1.0

VWAP_DEVIATION_ENTRY = 1.0
EMA_DEVIATION_ENTRY = 0.8
TARGET_RR = 1.5
TRAIL_ATR_MULTIPLIER = 0.75
STOP_ATR_MULTIPLIER = 0.5
SIDEWAYS_STOP_ATR = 0.3

RANGE_BARS = 8
MAX_RANGE_WIDTH_PCT = 1.0
MIN_BREAKOUT_ATR = 0.10
EMA9_LEN = 9
EMA20_LEN = 20
EMA50_LEN = 50
EMA_SLOPE_BARS = 3


# ---------------------------------------------------------------------------
# Output directories
# ---------------------------------------------------------------------------

OUT_DIR = "diagnostics/regime_v3_ensemble"


# ---------------------------------------------------------------------------
# Comprehensive parameter container for sweep/research
# ---------------------------------------------------------------------------


@dataclass
class RegimeV3Params:
    # Regime
    regime_confirm_bars: int = REGIME_CONFIRM_BARS
    regime_use_atr_separation: bool = REGIME_USE_ATR_SEPARATION
    regime_use_20bar_return: bool = REGIME_USE_20BAR_RETURN

    # Candidate generation
    max_candidates: int = MAX_CANDIDATES
    min_scanners_for_vote: int = MIN_SCANNERS_FOR_VOTE
    min_consensus: float = MIN_CONSENSUS

    # LLM
    llm_enabled: bool = LLM_ENABLED
    llm_temperature: float = LLM_TEMPERATURE
    llm_timeout: int = LLM_TIMEOUT_SECONDS
    llm_prompt_version: str = LLM_PROMPT_VERSION

    # Selection
    selection_method: str = SELECT_LLM_TOP
    top_n: int = 5

    # Strategy
    bull_min_rvol: float = BULL_MOMENTUM_MIN_RVOL
    bear_min_rvol: float = BEAR_MOMENTUM_MIN_RVOL
    sideways_min_rvol: float = SIDEWAYS_MIN_RVOL
    target_rr: float = TARGET_RR
    trail_atr_multiplier: float = TRAIL_ATR_MULTIPLIER
    stop_atr_multiplier: float = STOP_ATR_MULTIPLIER
    sideways_stop_atr: float = SIDEWAYS_STOP_ATR

    # Portfolio
    initial_capital: float = INITIAL_CAPITAL
    risk_per_trade_pct: float = RISK_PER_TRADE_PCT
    max_open_positions: int = MAX_OPEN_POSITIONS
    max_trades_per_day: int = MAX_TRADES_PER_DAY
    max_portfolio_open_risk_pct: float = MAX_PORTFOLIO_OPEN_RISK_PCT
    max_daily_loss_pct: float = MAX_DAILY_LOSS_PCT
    use_daily_loss_limit: bool = USE_DAILY_LOSS_LIMIT
    square_off_on_daily_loss: bool = SQUARE_OFF_ON_DAILY_LOSS
    position_value_pct: float = POSITION_VALUE_PCT
    force_no_trade: bool = FORCE_NO_TRADE

    # Costs
    estimated_cost_pct: float = ESTIMATED_COST_PCT
    slippage_pct: float = SLIPPAGE_PCT
    cost_model: str = COST_MODEL

    # Time
    entry_cutoff: str = ENTRY_CUTOFF
    square_off_time: str = SQUARE_OFF_TIME

    def clone_with(self, **kwargs) -> "RegimeV3Params":
        d = self.__dict__.copy()
        d.update(kwargs)
        return RegimeV3Params(**d)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_out_dir() -> str:
    import os
    os.makedirs(OUT_DIR, exist_ok=True)
    return OUT_DIR
