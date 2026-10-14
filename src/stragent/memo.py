"""Analysis -> LLM-written memo whose every number is checked. Shared by the CLI and the API.

    facts -> generate -> validate -> (problems?) regenerate once with them -> validate
          -> Memo(verified=True)  or  Memo(verified=False, problems=[...])

A memo that still fails is returned with its problems, never hidden or silently fixed. The
provider is passed in: this module never knows which vendor ran.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from stragent.config import LLMConfig, Settings
from stragent.llm.cache import MemoCache, cache_key
from stragent.llm.facts import build_facts, facts_hash
from stragent.llm.prompts import load_prompt, retry_message, user_message
from stragent.llm.provider import LLMError, Provider
from stragent.llm.validate import validate_memo
from stragent.models import Analysis, LLMUsage, Memo, MemoSections

log = logging.getLogger("stragent.memo")

SECTION_TITLES = {
    "verdict": "Verdict",
    "pricing": "Pricing",
    "occupancy_revenue": "Occupancy and revenue",
    "competition": "Competition",
    "risks": "Risks",
    "checks_before_buying": "Checks before buying",
}
TOKENS_PER_PRICE_UNIT = 1_000_000


def render_markdown(sections: MemoSections) -> str:
    return "\n\n".join(
        f"## {title}\n\n{getattr(sections, name).strip()}" for name, title in SECTION_TITLES.items()
    )


def _add(a: LLMUsage, b: LLMUsage) -> LLMUsage:
    def plus(x: int | None, y: int | None) -> int | None:
        return None if x is None or y is None else x + y

    return LLMUsage(
        input_tokens=plus(a.input_tokens, b.input_tokens),
        output_tokens=plus(a.output_tokens, b.output_tokens),
    )


def estimate_cost(cfg: LLMConfig, model: str, usage: LLMUsage) -> float | None:
    """USD from the [llm.prices] table; None when the model or a token count is unknown."""
    price = cfg.prices.get(model)
    if price is None or usage.input_tokens is None or usage.output_tokens is None:
        return None
    cost = usage.input_tokens * price.input + usage.output_tokens * price.output
    return round(cost / TOKENS_PER_PRICE_UNIT, 6)


def _log(memo: Memo, request_id: str | None, spent: LLMUsage, cost: float | None) -> None:
    log.info(
        "memo request=%s provider=%s model=%s prompt=%s cached=%s attempts=%d verified=%s "
        "problems=%d input_tokens=%s output_tokens=%s latency_ms=%d cost_usd=%s",
        request_id or "-",
        memo.provider,
        memo.model,
        memo.prompt_version,
        memo.cached,
        memo.attempts,
        memo.verified,
        len(memo.problems),
        spent.input_tokens,
        spent.output_tokens,
        0 if memo.cached else memo.latency_ms,
        "null" if cost is None else f"{cost:.6f}",
    )


def write_memo(
    analysis: Analysis,
    settings: Settings,
    provider: Provider,
    *,
    request_id: str | None = None,
    use_cache: bool = True,
    clock: Callable[[], float] = time.monotonic,
) -> Memo:
    """Write and check a memo. Raises LLMError / LLMUnavailableError if the first call fails."""
    cfg = settings.llm
    prompt = load_prompt(cfg.prompt_version)
    facts = build_facts(analysis)
    fhash = facts_hash(facts)
    cache = MemoCache(settings.resolve(settings.paths.cache_dir) / "memo") if use_cache else None
    key = cache_key(fhash, prompt, cfg, provider.name, provider.model)

    if cache and (hit := cache.get(key)) is not None:
        # Re-check: the validator may have become stricter since the memo was cached.
        if hit.facts_hash == fhash and validate_memo(hit.sections, facts).verified:
            memo = hit.model_copy(update={"cached": True})
            _log(memo, request_id, LLMUsage(input_tokens=0, output_tokens=0), 0.0)
            return memo
        log.info(
            "memo request=%s cache entry %s no longer validates; regenerating", request_id, key
        )

    start = clock()
    deadline = start + cfg.deadline_seconds
    notes: list[str] = []
    sections, usage = provider.generate(
        prompt.system, user_message(prompt, analysis, facts), MemoSections, deadline - clock()
    )
    attempts = 1
    check = validate_memo(sections, facts)
    if not check.verified:
        time_left = deadline - clock()
        if time_left < cfg.min_attempt_seconds:
            notes.append("Not regenerated: the time budget for this memo ran out.")
        else:
            retry = retry_message(prompt, analysis, facts, sections, check.problems)
            try:
                second, more = provider.generate(prompt.system, retry, MemoSections, time_left)
            except LLMError as exc:
                notes.append(f"Regeneration failed ({exc}); showing the first draft.")
            else:
                attempts = 2
                usage = _add(usage, more)
                sections, check = second, validate_memo(second, facts)
    latency_ms = round((clock() - start) * 1000)
    cost = estimate_cost(cfg, provider.model, usage)
    memo = Memo(
        sections=sections,
        markdown=render_markdown(sections),
        verified=check.verified,
        problems=check.problems,
        facts=facts,
        facts_hash=fhash,
        snapshot_date=analysis.snapshot_date,
        attempts=attempts,
        provider=provider.name,
        model=provider.model,
        prompt_version=prompt.version,
        usage=usage,
        latency_ms=latency_ms,
        estimated_cost_usd=cost,
        notes=notes,
    )
    if cache and memo.verified:
        cache.put(key, memo)
    _log(memo, request_id, usage, cost)
    return memo
