"""
LLM Meta-Ranker — calls OpenAI-compatible API for structured ranking.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import numpy as np

from .. import config
from ..scanners.scanner_signal import RankedCandidate
from .llm_prompt import build_prompt
from .llm_schema import LLMInput, LLMInputCandidate, LLMOutput, LLMOutputStock


def _call_llm(system: str, user: str) -> str | None:
    """Call OpenAI-compatible chat completions API. Returns raw response text or None."""
    api_key = config.LLM_API_KEY or os.getenv("OPENAI_API_KEY")
    api_base = config.LLM_API_BASE or os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    if not api_key:
        return None

    import httpx

    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": config.LLM_TEMPERATURE,
        "max_tokens": config.LLM_MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }

    for attempt in range(max(1, config.LLM_RETRY_COUNT + 1)):
        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=config.LLM_TIMEOUT_SECONDS)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return content
        except Exception:
            if attempt < config.LLM_RETRY_COUNT:
                time.sleep(1.0)
            else:
                return None
    return None


def parse_llm_response(
    raw: str | None,
    candidate_symbols: set[str],
    expected_count: int,
) -> LLMOutput | None:
    """Parse and validate LLM JSON response."""
    if raw is None:
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown code blocks
        import re
        m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
        else:
            return None

    if not isinstance(data, dict):
        return None

    regime = data.get("regime", "SIDEWAYS")
    stocks_raw = data.get("ranked_stocks", [])

    stocks = []
    for s in stocks_raw:
        stocks.append(LLMOutputStock(
            symbol=str(s.get("symbol", "")),
            rank=int(s.get("rank", 0)),
            score=float(s.get("score", 0)),
            direction=str(s.get("direction", "NEUTRAL")),
            confidence=float(s.get("confidence", 0)),
            reason_codes=list(s.get("reason_codes", [])),
        ))

    output = LLMOutput(regime=regime, ranked_stocks=stocks)
    errors = output.validate(candidate_symbols)
    if errors:
        # If errors exist, try to fix by filling missing symbols, fixing ranks, etc.
        fixed = _fix_llm_output(output, candidate_symbols, expected_count)
        if fixed:
            return fixed
        return None

    return output


def _fix_llm_output(
    output: LLMOutput,
    candidate_symbols: set[str],
    expected_count: int,
) -> LLMOutput | None:
    """Attempt minimal fixes for common LLM output issues."""
    ranked = output.ranked_stocks
    valid_stocks = [s for s in ranked if s.symbol in candidate_symbols]

    # If no valid stocks, fail
    if not valid_stocks:
        return None

    # Deduplicate by keeping first occurrence
    seen = set()
    deduped = []
    for s in valid_stocks:
        if s.symbol not in seen:
            seen.add(s.symbol)
            deduped.append(s)

    # Add any missing symbols
    missing = [s for s in candidate_symbols if s not in seen]
    for sym in missing:
        deduped.append(LLMOutputStock(
            symbol=sym,
            rank=0,
            score=0,
            direction="NEUTRAL",
            confidence=0.0,
            reason_codes=[],
        ))

    # Reassign sequential ranks
    for i, s in enumerate(deduped, 1):
        s.rank = i

    output.ranked_stocks = deduped
    return output


def llm_rank_candidates(
    candidates: list[RankedCandidate],
    regime: str,
    timestamp: str,
) -> list[RankedCandidate] | None:
    """
    Rank candidates using LLM meta-ranker.
    Returns re-ranked candidates or None on failure.
    """
    if not candidates or not config.LLM_ENABLED:
        return None

    # Build LLM input
    inputs = []
    for c in candidates:
        kwargs = {
            "symbol": c.symbol,
            "ensemble_score": c.ensemble_score,
            "scanner_count": c.scanner_count,
            "bullish_scanners": c.bullish_scanners,
            "bearish_scanners": c.bearish_scanners,
            "scanner_rank_mean": c.scanner_rank_mean,
            "scanner_rank_median": c.scanner_rank_median,
        }
        inputs.append(LLMInputCandidate(**kwargs))

    llm_input = LLMInput(
        timestamp=timestamp,
        market_regime=regime,
        candidate_count=len(inputs),
        candidates=inputs,
    )

    system, user = build_prompt(llm_input, limit_candidates=config.MAX_CANDIDATES)
    raw = _call_llm(system, user)
    output = parse_llm_response(raw, {c.symbol for c in candidates}, len(candidates))

    if output is None:
        return None

    # Map LLM output back to RankedCandidates
    {s.symbol: s for s in output.ranked_stocks}
    candidate_map = {c.symbol: c for c in candidates}

    result = []
    for s in output.ranked_stocks:
        if s.symbol in candidate_map:
            c = candidate_map[s.symbol]
            c.llm_rank = s.rank
            c.llm_score = s.score
            c.llm_selected = s.rank <= 5
            c.llm_confidence = s.confidence
            c.llm_reason_codes = s.reason_codes
            c.selection_method = config.SELECT_LLM_TOP
            result.append(c)

    # Sort by LLM rank
    result.sort(key=lambda c: c.llm_rank)
    return result


def deterministic_fallback(
    candidates: list[RankedCandidate],
    top_n: int = 5,
) -> list[RankedCandidate]:
    """Fallback: select by ensemble score."""
    selected = candidates[:top_n]
    for i, c in enumerate(selected):
        c.llm_rank = i + 1
        c.llm_score = c.ensemble_score * 100
        c.llm_selected = True
        c.llm_confidence = 0.5
        c.selection_method = config.SELECT_FALLBACK_DETERMINISTIC
    return selected
