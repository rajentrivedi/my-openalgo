import os
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = "db/historify.duckdb"

SESSION_TZ_NAME = "Asia/Kolkata"
SESSION_TZ = ZoneInfo(SESSION_TZ_NAME)

COST_PER_SIDE = 0.001
SESSION_START = "09:15"
SESSION_END = "15:30"
HARD_EXIT = "15:15"

LIQUIDITY_GATE_CR = 10.0

KILL_PF = 1.3
KILL_MIN_OOS_TRADES = 500
KILL_MIN_OOS_EXPECTANCY = 0.0

BAD_DAYS = {"2025-10-21"}

PINE_DIR = os.path.join(BASE_DIR, "pine")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
STUDY_DIR = os.path.join(BASE_DIR, "study")
RESULTS_DIR = os.path.join(BASE_DIR, "results")

RESEARCH_START = 1723779900
RESEARCH_END = 1786701540

STUDY_WINDOW_END_EPOCH = 1755216000
STUDY_N_TOP_LIQUID = 60

SIGNAL_PARAMS = {
    "rsi_len": 2,
    "bb_len": 20,
    "bb_std": 2.0,
    "roll_len": 60,
    "or_len": 15,
    "rvol_len": 20,
    "atr_len": 14,
    "atr_tf_min": 5,
}

STRATEGY_PARAMS = {
    "proftrigger_r": 1.0,
    "trail_r": 0.5,
    "stop_r": 1.0,
    "hard_exit": "15:15",
    "max_hold_bars": 60,
}
