"""analyst.py - LLM-powered root-cause narratives via Claude API."""

from __future__ import annotations

import os
from dataclasses import dataclass

from triage.scorer import ScoredPattern

_SYSTEM_PROMPT = """\
You are an expert multi-agent systems debugger. You analyze failure patterns
extracted from agent trace files and write concise, actionable root-cause narratives.

Guidelines:
- Be specific: cite exact turn numbers and divergence fields from the data provided
- Be concise: 2-3 sentences maximum
- Be actionable: end with one concrete fix
- Never use hedging language like "it seems" or "might be"
- Output plain text only, no markdown formatting
"""


@dataclass
class AnalysisResult:
    pattern_id: str
    narrative: str
    model: str
    cached: bool


def analyze_pattern(sp: ScoredPattern, *, api_key: str | None = None) -> AnalysisResult:
    """Send a scored pattern to Claude and return a root-cause narrative.

    Requires the ``anthropic`` package (``pip install 'triage[ai]'``) and
    an ``ANTHROPIC_API_KEY`` environment variable (or pass ``api_key``).
    Uses ``claude-haiku-4-5`` with prompt caching on the system prompt to
    keep per-call cost low.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "anthropic package required for AI analysis. "
            "Install with: pip install 'triage[ai]'"
        ) from exc

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable not set. "
            "Set it or pass api_key= to analyze_pattern()."
        )

    client = anthropic.Anthropic(api_key=key)

    p = sp.pattern
    sample_events = p.events[:5]
    event_lines = "\n".join(
        f"  run={e.run_id} turn={e.turn} tool={e.action_taken.tool_name} "
        f"divergence={e.divergence_fields}"
        for e in sample_events
    )

    user_content = (
        f"Pattern: {p.display_name()}\n"
        f"Failure classification: {p.failure_classification}\n"
        f"Occurrences: {p.frequency} across {len(p.run_ids)} run(s)\n"
        f"Recovery rate: {sp.recovery_rate:.0%}\n"
        f"Divergence fields: {sorted(p.divergence_fields) if p.divergence_fields else 'none'}\n"
        f"\nSample events (up to 5):\n{event_lines}\n"
        "\nWrite a root-cause narrative."
    )

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
    )

    first = message.content[0]
    raw_text = getattr(first, "text", None)
    if not isinstance(raw_text, str):
        raise RuntimeError(f"Unexpected response block type: {type(first).__name__}")
    narrative: str = raw_text.strip()
    cached: bool = getattr(message.usage, "cache_read_input_tokens", 0) > 0

    return AnalysisResult(
        pattern_id=p.pattern_id,
        narrative=narrative,
        model=message.model,
        cached=cached,
    )


def analyze_patterns(
    scored: list[ScoredPattern],
    top_n: int = 3,
    *,
    api_key: str | None = None,
) -> dict[str, AnalysisResult]:
    """Analyze up to ``top_n`` patterns and return results keyed by pattern_id."""
    results: dict[str, AnalysisResult] = {}
    for sp in scored[:top_n]:
        results[sp.pattern.pattern_id] = analyze_pattern(sp, api_key=api_key)
    return results
