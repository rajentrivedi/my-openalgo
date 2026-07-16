"""
LLM prompt construction for meta-ranking.

Builds a strict-JSON-rank task from LLMInput, with regime-aware direction rules.
Only includes information available at the decision timestamp.
"""
from __future__ import annotations

import json

from .. import config
from .llm_schema import LLMInput

_SYSTEM = (
    "You are a quantitative stock-selection meta-ranker for Indian NSE intraday trading. "
    "You rank candidate stocks according to expected intraday relative performance using ONLY the "
    "structured scanner evidence provided. You do not invent data, browse the web, or claim "
    "certainty. You always return valid JSON matching the requested schema exactly."
)


def _regime_rules(regime: str) -> str:
    rules = {
        config.BULL: (
            "Market regime is BULL. Prefer LONG candidates with strong scanner consensus, "
            "positive relative strength, multi-timeframe alignment, and volume confirmation. "
            "Avoid SHORT candidates."
        ),
        config.BEAR: (
            "Market regime is BEAR. Prefer SHORT candidates with negative relative strength, "
            "bearish multi-timeframe alignment, and high downside momentum. Avoid LONG candidates."
        ),
        config.SIDEWAYS: (
            "Market regime is SIDEWAYS. Favor mean-reversion candidates (oversold/overbought) "
            "and symmetric setups. Avoid chasing extremes."
        ),
        config.TRANSITION: (
            "Market regime is TRANSITION (low confidence). Rank conservatively; prefer NEUTRAL/flat "
            "or the least risky candidates. No aggressive directional calls."
        ),
    }
    return rules.get(regime, "Market regime is SIDEWAYS.")


def build_prompt(llm_input: LLMInput, limit_candidates: int | None = None) -> tuple[str, str]:
    """
    Returns (system_prompt, user_prompt).

    limit_candidates: cap candidate count for token efficiency (e.g. 25).
    """
    cands = llm_input.candidates
    if limit_candidates:
        cands = cands[:limit_candidates]

    payload = LLMInput(
        timestamp=llm_input.timestamp,
        market_regime=llm_input.market_regime,
        candidate_count=len(cands),
        candidates=cands,
    )

    user = f"""{_regime_rules(llm_input.market_regime)}

Rank the following candidate stocks by expected intraday performance.

Input JSON:
{json.dumps(payload.to_dict(), indent=2, default=str)}

Respond with STRICT JSON only, no prose, of exactly this shape:
{{
  "regime": "{llm_input.market_regime}",
  "ranked_stocks": [
    {{
      "symbol": "XXX",
      "rank": 1,
      "score": 0-100,
      "direction": "LONG|SHORT|NEUTRAL",
      "confidence": 0.0-1.0,
      "reason_codes": ["STRONG_CONSENSUS", "HIGH_RELATIVE_STRENGTH", "MULTI_TIMEFRAME_ALIGNMENT", "VOLUME_CONFIRMATION"]
    }}
  ]
}}

Rules:
- rank must be sequential integers 1..{len(cands)}.
- Use every candidate symbol exactly once; never invent symbols; never omit.
- direction must respect the regime rules above.
- score 0-100; higher = stronger expected intraday performance.
- reason_codes must be from: STRONG_CONSENSUS, HIGH_RELATIVE_STRENGTH, MULTI_TIMEFRAME_ALIGNMENT, HIGH_MOMENTUM, VOLUME_CONFIRMATION, LOW_CONFLICT, HIGH_ENSEMBLE_SCORE, REGIME_ALIGNMENT, OVERSOLD_BOUNCE, OVERBOUGHT_REVERSAL.
Return only the JSON object."""
    return _SYSTEM, user
